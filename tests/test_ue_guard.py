"""Unreal writes stay in the project's own folder, and renders stay off busy GPUs.

- A project copied to another storage root (a scratch root) must not fall through to the user
  config's Unreal projects and backup folders: ue.check_target refuses until the project sets its
  own (project.json unreal.project_dir / backup_dir) or the environment does.
- An Unreal folder another pipeline project claimed (OWNER_FILE) is refused, `ap ue build` included.
- ue.pick_gpu never picks ComfyUI's GPU while ComfyUI has a job running or queued, nor a GPU with a
  TRELLIS or vision job running; neither free: it waits, then refuses with the reasons.
"""
import httpx
import pytest

from asset_pipeline import config, gpu_policy, ue, ue_build, vlm
from asset_pipeline.comfy.client import ComfyClient
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import UnrealSettings


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """The user config's storage root and Unreal folders, and a scratch root the shell points at."""
    real, scratch = tmp_path / "real", tmp_path / "scratch"
    config.save_user({"paths": {"ap_root": str(real)},
                      "unreal": {"projects_dir": str(tmp_path / "Unreal"), "backup_dir": str(tmp_path / "backups")}})
    monkeypatch.setenv("AP_ROOT", str(scratch))
    config.backends.cache_clear()
    return tmp_path


def own_folders(store, d):
    p = store.load()
    p.unreal = (p.unreal or UnrealSettings()).model_copy(update={"project_dir": str(d / "ue"), "backup_dir": str(d / "bak")})
    store.save(p)


def test_a_scratch_copy_never_resolves_to_the_real_unreal_project(roots, monkeypatch):
    store = ProjectStore.create("my-project")
    assert ue.project_dir(store) == roots / "Unreal" / "my-project"          # what the user config alone gives
    with pytest.raises(RuntimeError, match="refusing to write.*Unreal project and backup"):
        ue.check_target(store)
    with pytest.raises(RuntimeError, match="refusing to write"):
        ue.init(store)                                                       # every headless run and render
    assert not (roots / "Unreal").exists()
    own_folders(store, roots / "scratch-ue")                                 # the project's own config wins
    assert ue.project_dir(store) == roots / "scratch-ue" / "ue" and ue.project_backups_dir(store) == roots / "scratch-ue" / "bak"
    assert ue.init(store).parent == roots / "scratch-ue" / "ue"
    monkeypatch.setenv("AP_UE_PROJECTS", str(roots / "env-ue"))               # so does the environment, both folders
    p = store.load()
    p.unreal = UnrealSettings()
    store.save(p)
    with pytest.raises(RuntimeError, match="backup folder"):
        ue.check_target(store)
    monkeypatch.setenv("AP_UE_BACKUPS", str(roots / "env-bak"))
    assert ue.check_target(store) == roots / "env-ue" / "my-project"


def test_an_unreal_folder_belongs_to_the_project_that_made_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("AP_UE_PROJECTS", str(tmp_path / "Unreal"))
    monkeypatch.setenv("AP_UE_BACKUPS", str(tmp_path / "bak"))
    mine = ProjectStore.create("my-project")
    up = ue.init(mine)
    assert (up.parent / ue.OWNER_FILE).is_file() and ue.check_target(mine) == up.parent
    other = ProjectStore.create("other-project")
    own = other.load()
    own.unreal = UnrealSettings(project_dir=str(up.parent))                   # pointed at my-project's folder
    other.save(own)
    with pytest.raises(RuntimeError, match="belongs to the pipeline project .*my-project"):
        ue.init(other)
    calls = []
    monkeypatch.setattr(ue, "backup", lambda st: calls.append("backup"))
    with pytest.raises(RuntimeError, match="refusing to write"):
        ue_build.build(other, log=lambda *a: None)
    assert calls == []                                                       # refused before the backup


# --- render GPU ------------------------------------------------------------------------------------

def test_pick_gpu_skips_busy_lanes_then_waits_or_refuses():
    busy = {0: ["TRELLIS is running on GPU 0"], 1: []}
    assert ue.pick_gpu(0, busy_fn=busy.get, vram_fn=lambda: {0: 1.0, 1: 1.0}, log=lambda *a: None) == 1
    busy = {0: [], 1: ["ComfyUI has 1 job(s) running and 0 queued on GPU 1"]}
    assert ue.pick_gpu(0, busy_fn=busy.get, vram_fn=lambda: {0: 2.7, 1: 1.0}, log=lambda *a: None) == 0
    # GPU 0 over the VRAM limit and ComfyUI busy on GPU 1: no fall-over, a plain refusal
    with pytest.raises(RuntimeError, match="no GPU free.*7.4 GB.*ComfyUI has 1 job"):
        ue.pick_gpu(0, busy_fn=busy.get, vram_fn=lambda: {0: 7.4, 1: 3.4}, wait_s=0, log=lambda *a: None)
    seen = iter([{0: 7.4, 1: 3.4}, {0: 2.0, 1: 3.4}])                        # waits until GPU 0 frees up
    assert ue.pick_gpu(0, busy_fn=busy.get, vram_fn=lambda: next(seen), wait_s=60, poll_s=0, log=lambda *a: None) == 0


def test_gpu_busy_reads_comfyui_trellis_and_the_vision_model(monkeypatch):
    monkeypatch.setattr(gpu_policy, "settings", lambda: {"gpus": {"gpu0": 0, "comfy": 1}})
    monkeypatch.setattr(vlm, "trellis_jobs", lambda g: [123] if g == 0 else [])
    monkeypatch.setattr(vlm, "state", lambda: {"gpu": 1, "url": "x", "backend": "llamacpp"})
    monkeypatch.setattr(vlm, "busy", lambda st: True)
    monkeypatch.setattr(ue, "comfy_queue", lambda: {"running": 0, "pending": 2})
    assert ue.gpu_busy(0) == ["TRELLIS is running on GPU 0"]
    assert ue.gpu_busy(1) == ["the vision model is answering a request on GPU 1",
                              "ComfyUI has 0 job(s) running and 2 queued on GPU 1"]
    monkeypatch.setattr(vlm, "busy", lambda st: False)
    monkeypatch.setattr(ue, "comfy_queue", lambda: None)                      # ComfyUI down: its GPU is free
    assert ue.gpu_busy(1) == []
    monkeypatch.setattr(ue, "comfy_queue", lambda: {"unknown": "timeout"})    # up but unreadable: busy
    assert "can't be read" in ue.gpu_busy(1)[0]


def test_comfy_queue_state_counts_running_and_pending():
    def handler(req):
        assert req.url.path == "/queue"
        return httpx.Response(200, json={"queue_running": [[1, "a"]], "queue_pending": [[2, "b"], [3, "c"]]})
    c = ComfyClient("http://comfy", transport=httpx.MockTransport(handler))
    assert c.queue_state() == {"running": 1, "pending": 2}

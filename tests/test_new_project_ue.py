"""A first-time project from New project to Unreal:

- images the build reads are written atomically, a truncated one is read again a few times, and the
  terrain heightmap left truncated is made again from the layout with a warning, not an error;
- a new project starts with light world dressing ([defaults.dressing]); one from before keeps its own;
- a new UE project's DefaultEngine.ini has the streaming / Lumen settings and the city level as its
  startup map, and Open in Unreal names the city level.
"""
import threading
import time

import numpy as np
import pytest
from PIL import Image

from asset_pipeline import project, ue
from asset_pipeline.project import ProjectStore, TruncatedImage, collecting_warnings, load_image, save_image, warn
from asset_pipeline.schema import DressingOptions, UnrealSettings
from asset_pipeline.stages import sw_site


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("AP_UE_PROJECTS", str(tmp_path / "Unreal"))
    monkeypatch.setenv("AP_UE_BACKUPS", str(tmp_path / "bak"))
    return tmp_path


def _png(path, n=64):
    save_image(Image.fromarray((np.arange(n * n) % 251).astype(np.uint8).reshape(n, n)), path)
    return path.read_bytes()


def test_save_image_is_atomic_and_skips_unchanged(tmp_path):
    p = tmp_path / "a.png"
    data = _png(p)
    assert Image.open(p).size == (64, 64) and not list(tmp_path.glob(".a.png.*"))   # no temp file left
    mtime = p.stat().st_mtime_ns
    time.sleep(0.01)
    assert not save_image(Image.open(p), p, unchanged_ok=True) and p.stat().st_mtime_ns == mtime
    assert p.read_bytes() == data


def test_load_image_retries_a_file_still_being_written(tmp_path):
    p = tmp_path / "frame.png"
    data = _png(p)
    p.write_bytes(data[: len(data) // 2])                 # a non-atomic writer half way through
    threading.Timer(0.3, lambda: p.write_bytes(data)).start()
    assert load_image(p, retries=5, delay_s=0.2).size == (64, 64)
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(TruncatedImage, match="frame.png"):
        load_image(p, retries=1, delay_s=0.01)
    with pytest.raises(FileNotFoundError):
        load_image(tmp_path / "missing.png")


def test_a_truncated_terrain_png_is_made_again_with_a_warning(root, monkeypatch):
    monkeypatch.setattr(project.time, "sleep", lambda s: None)
    store = ProjectStore.create("my-project")
    layout = sw_site.starter_layout()
    layout.terrain = layout.terrain.model_copy(update={"resolution": 65})
    sw_site.save_layout(store, layout)
    h = sw_site.terrain(store, layout)
    png = sw_site.site_dir(store) / "terrain.png"
    full = png.read_bytes()
    png.write_bytes(full[: len(full) // 3])
    with pytest.raises(TruncatedImage):                   # what the build used to fail on
        load_image(png)
    from asset_pipeline.ground import Ground
    with collecting_warnings() as w:
        g = Ground.from_project(store)
    assert np.allclose(g.sample.h, h, atol=0.01) and png.read_bytes() == full
    assert len(w) == 1 and "terrain.png unreadable" in w[0] and "made it again" in w[0]
    with collecting_warnings() as w2:
        warn("x")
        warn("x")
    assert w2 == ["x"] and w == [w[0]]                    # collectors are per run, without repeats


def test_new_projects_start_with_light_dressing(root):
    o = ProjectStore.create("my-project").load().unreal.dressing
    assert (o.ground, o.rocks, o.vegetation, o.water, o.megascans) == (True, True, True, "simple", False)
    assert o.rock_density == o.vegetation_density == 1.0
    old = ProjectStore.create("old-project")                # from before: no saved "unreal" at all
    p = old.load()
    p.unreal = None
    old.save(p)
    from asset_pipeline import dressing
    assert dressing.options(old) == DressingOptions() and not dressing.options(old).any()
    kept = ProjectStore.create("kept-project")              # a saved choice stays as it is
    p = kept.load()
    p.unreal = UnrealSettings(dressing=DressingOptions(rocks=True))
    kept.save(p)
    assert dressing.options(kept) == DressingOptions(rocks=True)


def test_new_ue_project_ini_and_open_on_the_city_map(root, monkeypatch):
    store = ProjectStore.create("my-project")
    up = ue.init(store)
    ini = (up.parent / "Config" / "DefaultEngine.ini").read_text()
    for line in ("[SystemSettings]", "r.Streaming.PoolSize=8000", "r.LumenScene.SurfaceCache.AtlasSize=8192",
                 "r.LumenScene.SurfaceCache.CardTexelDensityScale=50",
                 "EditorStartupMap=/Game/AP/Maps/MyProject.MyProject", "GameDefaultMap=/Game/AP/Maps/MyProject.MyProject"):
        assert line in ini.splitlines()
    started = []
    monkeypatch.setattr(ue, "editor_cmd", lambda: root / "UE" / "UnrealEditor-Cmd")
    monkeypatch.setattr(ue.subprocess, "Popen", lambda cmd, **kw: started.append(cmd) or type("P", (), {"pid": 1})())
    monkeypatch.setattr(ue, "editor_running", lambda up: False)
    ue.open_editor(store)                                   # no level saved yet: the startup map
    assert started[-1][1:] == [str(up)]
    ue.map_file(store).parent.mkdir(parents=True)
    ue.map_file(store).write_bytes(b"")
    ue.open_editor(store)
    assert started[-1][1:] == [str(up), "/Game/AP/Maps/MyProject"]

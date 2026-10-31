"""Project management (File > Manage projects…, `ap project`): list facts, protect, rename, duplicate
and Delete to the system trash. Scratch projects only; the trash is a recording fake, so nothing
(real or scratch) goes to a real trash, and the Windows path runs through mocks."""
import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import cli, project_manager as pm
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore
from asset_pipeline.ui import app as ui_app


@pytest.fixture
def trashed(monkeypatch, tmp_path_factory):
    """Replace send2trash: records the paths, moves them to a scratch trash folder."""
    trash = tmp_path_factory.mktemp("trash")
    moved = []
    mod = types.ModuleType("send2trash")

    def send2trash(path):
        assert isinstance(path, str)
        moved.append(path)
        os.rename(path, str(trash / f"{len(moved)}-{os.path.basename(path)}"))
    mod.send2trash = send2trash
    monkeypatch.setitem(sys.modules, "send2trash", mod)
    return moved


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "ap"))
    monkeypatch.setenv("AP_UE_PROJECTS", str(tmp_path / "unreal"))
    monkeypatch.setenv("AP_UE_BACKUPS", str(tmp_path / "backups"))
    return tmp_path


def _fill(path, n=3000):
    path.mkdir(parents=True, exist_ok=True)
    (path / "f.bin").write_bytes(os.urandom(n))


def _with_unreal(slug):
    store = ProjectStore.create(slug, name=slug.title())
    from asset_pipeline import ue
    _fill(ue.project_dir(store), 9000)
    _fill(ue.project_backups_dir(store), 5000)
    return store


def _approve(store):
    d = store.root / "site"
    d.mkdir(exist_ok=True)
    (d / "library_review.json").write_text(json.dumps({"_reviews": {"gate": {"status": "approved"}}}))


@pytest.fixture
def client(env, trashed):
    return TestClient(ui_app.create_app(JobQueue()))


def test_listing_facts(env):
    a = _with_unreal("alpha")
    ProjectStore.create("beta")
    _approve(a)
    rows = {r["slug"]: r for r in pm.listing()}
    assert rows["alpha"]["approved"] and rows["alpha"]["protected"]            # approved: protected by default
    assert not rows["beta"]["approved"] and not rows["beta"]["protected"]
    b = rows["alpha"]["bytes"]
    assert b["project"] > 0 and b["unreal"] >= 9000 and b["backups"] >= 5000   # shown separately
    assert rows["beta"]["bytes"]["unreal"] == 0
    assert rows["alpha"]["opened"] is None
    pm.touch_opened("alpha", now=1000.0)
    assert pm.info("alpha", sizes=False)["opened"] == 1000.0


def test_protect_overrides_default(env):
    s = ProjectStore.create("p")
    _approve(s)
    assert pm.is_protected(s)
    pm.set_protected("p", False)
    assert not pm.is_protected(s)
    pm.set_protected("p", True)
    assert pm.is_protected(s)


def test_rename_keeps_folder(env):
    ProjectStore.create("p", name="Old")
    assert pm.rename("p", " New name ") == "New name"
    assert ProjectStore.open("p").load().name == "New name" and ProjectStore.open("p").root.name == "p"
    with pytest.raises(ValueError):
        pm.rename("p", "  ")


def test_duplicate_has_own_unreal_folders(env):
    s = ProjectStore.create("p", name="Orig")
    pr = s.load()
    from asset_pipeline.schema import UnrealSettings
    pr.unreal = pr.unreal or UnrealSettings()
    pr.unreal.project_dir, pr.unreal.backup_dir = str(env / "own-ue"), str(env / "own-bak")
    s.save(pr)
    pm.set_protected("p", True)
    (s.root / "refs").mkdir()
    (s.root / "refs" / "a.png").write_bytes(b"x" * 100)
    os.link(s.root / "refs" / "a.png", s.root / "refs" / "b.png")
    new = pm.duplicate("p")
    d = ProjectStore.open(new)
    assert new != "p" and d.load().slug == new and d.load().name == "Orig copy"
    assert d.load().unreal.project_dir == "" and d.load().unreal.backup_dir == ""
    assert d.load().protected is None                                          # the copy isn't marked protected
    assert (d.root / "refs" / "a.png").stat().st_ino == (d.root / "refs" / "b.png").stat().st_ino   # links kept
    assert ProjectStore.open("p").load().unreal.project_dir == str(env / "own-ue")                  # original untouched


def test_delete_with_unreal_and_backups(env, trashed):
    s = _with_unreal("scratch")
    from asset_pipeline import ue
    ue_dir, bak = ue.project_dir(s), ue.project_backups_dir(s)
    plan = pm.plan_delete("scratch")
    assert [i["key"] for i in plan["items"]] == ["project", "unreal", "backups"] and not plan["blocked"]
    r = pm.delete("scratch", unreal=True, backups=True)
    assert r["removed"] == ["Unreal project", "Unreal backups", "Project folder"] and r["freed"] >= 14000
    assert len(trashed) == 3 and not s.root.exists() and not ue_dir.exists() and not bak.exists()
    assert "scratch" not in [x["slug"] for x in pm.listing()]


def test_delete_project_only_leaves_unreal(env, trashed):
    s = _with_unreal("scratch")
    from asset_pipeline import ue
    ue_dir, bak = ue.project_dir(s), ue.project_backups_dir(s)
    pm.delete("scratch")
    assert len(trashed) == 1 and ue_dir.is_dir() and bak.is_dir()


def test_blocked_cases(env, trashed):
    s = ProjectStore.create("open-one")
    ProjectStore.create("guarded")
    pm.set_protected("guarded", True)
    with pytest.raises(pm.Blocked, match="open"):
        pm.delete("open-one", current="open-one")
    with pytest.raises(pm.Blocked, match="protected"):
        pm.delete("guarded")
    with pytest.raises(pm.Blocked, match="Jobs"):
        pm.delete("open-one", busy=True)
    approved = ProjectStore.create("approved-one")
    _approve(approved)
    with pytest.raises(pm.Blocked, match="protected"):                         # protected by default
        pm.delete("approved-one")
    assert not trashed and s.root.exists()


def test_foreign_unreal_folder_is_not_deleted(env, trashed):
    s = _with_unreal("mine")
    from asset_pipeline import ue
    (ue.project_dir(s) / ue.OWNER_FILE).write_text(json.dumps({"project": "/elsewhere/other"}))
    with pytest.raises(pm.Blocked, match="belongs to"):
        pm.delete("mine", unreal=True)
    assert not trashed and s.root.exists()


def test_scratch_root_never_takes_unreal_folders_from_user_config(env, trashed, monkeypatch):
    """A scratch project in another storage root, with Unreal folders from the user's config, must not
    offer them (they would be the real project's)."""
    from asset_pipeline import config
    monkeypatch.delenv("AP_UE_PROJECTS")
    monkeypatch.delenv("AP_UE_BACKUPS")
    monkeypatch.setattr(config, "user", lambda: {"paths": {"ap_root": str(env / "real-root")}})
    s = ProjectStore.create("scratch")
    from asset_pipeline import ue
    _fill(ue.project_dir(s))
    with pytest.raises(pm.Blocked, match="user config"):
        pm.delete("scratch", unreal=True)
    assert ue.project_dir(s).is_dir()


def test_trash_failure_removes_nothing(env, monkeypatch):
    s = _with_unreal("scratch")
    mod = types.ModuleType("send2trash")

    def boom(path):
        raise OSError("no trash here")
    mod.send2trash = boom
    monkeypatch.setitem(sys.modules, "send2trash", mod)
    with pytest.raises(RuntimeError, match="nothing"):
        pm.delete("scratch", unreal=True, backups=True)
    assert s.root.is_dir()                                                     # never a hard delete fallback


def test_windows_paths_via_mocks(env, monkeypatch):
    """On native Windows send2trash gets absolute str paths (backslashes, drive letters, a Unreal folder
    on another drive); the call is the Recycle Bin one and nothing else removes files."""
    s = _with_unreal("scratch")
    seen = []
    mod = types.ModuleType("send2trash")
    mod.send2trash = lambda p: seen.append(p)
    monkeypatch.setitem(sys.modules, "send2trash", mod)
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    removed = []
    monkeypatch.setattr("shutil.rmtree", lambda *a, **k: removed.append(a))
    monkeypatch.setattr(pm.Path, "resolve", lambda self, strict=False: self)   # as Windows paths do not resolve here
    pm.delete("scratch", unreal=True, backups=True)
    assert len(seen) == 3 and all(isinstance(p, str) and os.path.isabs(p) for p in seen)
    assert not removed
    assert s.root.name in os.path.basename(seen[-1])


def test_missing_send2trash_says_so(env, monkeypatch):
    ProjectStore.create("p")
    monkeypatch.setitem(sys.modules, "send2trash", None)
    with pytest.raises(RuntimeError, match="send2trash"):
        pm.delete("p")
    assert ProjectStore.open("p").root.is_dir()


def test_api_flow(client, env, trashed):
    s = _with_unreal("alpha")
    ProjectStore.create("beta")
    _approve(s)
    rows = {r["slug"]: r for r in client.get("/api/manage/projects").json()}
    assert rows["alpha"]["protected"] and not rows["beta"]["protected"]
    assert client.get("/api/manage/projects/beta/delete-plan?current=beta").json()["blocked"]
    assert client.post("/api/manage/projects/beta/delete", json={"current": "beta"}).status_code == 409
    assert client.post("/api/manage/projects/alpha/delete", json={"current": "beta"}).status_code == 409   # protected
    assert client.post("/api/manage/projects/alpha/protect", json={"protected": False}).json() == {"protected": False}
    assert client.post("/api/manage/projects/beta/rename", json={"name": "Beta two"}).json() == {"name": "Beta two"}
    dup = client.post("/api/manage/projects/beta/duplicate", json={}).json()["slug"]
    r = client.post("/api/manage/projects/alpha/delete", json={"unreal": True, "backups": True, "current": "beta"})
    assert r.status_code == 200 and len(trashed) == 3
    assert sorted(client.get("/api/projects").json()) == sorted(["beta", dup])    # the Project dropdown's list
    assert client.post("/api/manage/projects/nope/delete", json={}).status_code == 404


def test_opened_is_recorded_when_a_project_loads(client, env):
    ProjectStore.create("p")
    assert pm.info("p", sizes=False)["opened"] is None
    assert client.get("/api/projects/p").status_code == 200
    assert pm.info("p", sizes=False)["opened"]


def test_cli(env, trashed, capsys, monkeypatch):
    ProjectStore.create("p", name="Pee")
    ProjectStore.create("q")
    cli.main(["project", "list"])
    out = capsys.readouterr().out
    assert "Pee" in out and "p " in out
    cli.main(["project", "protect", "q"])
    with pytest.raises(SystemExit, match="protected"):
        cli.main(["project", "delete", "q", "--yes"])
    cli.main(["project", "protect", "q", "--off"])
    monkeypatch.setattr("builtins.input", lambda *_: "wrong")
    with pytest.raises(SystemExit, match="not confirmed"):
        cli.main(["project", "delete", "q"])
    assert ProjectStore.open("q").root.is_dir()
    monkeypatch.setattr("builtins.input", lambda *_: "q")
    cli.main(["project", "delete", "q"])
    assert not (env / "ap" / "projects" / "q").exists() and len(trashed) == 1
    cli.main(["project", "delete", "p", "--yes"])
    assert len(trashed) == 2


def test_sizes_count_every_file_when_the_walk_has_no_inodes(env, monkeypatch, tmp_path):
    """Windows: os.scandir's stat has st_ino 0; every file must still count once (not just the first)."""
    from asset_pipeline import disk_usage
    d = tmp_path / "tree"
    for i in range(3):
        _fill(d / str(i), 4000)
    real = disk_usage._walk

    def no_inodes(base):
        for p, st in real(base):
            yield p, os.stat_result((st.st_mode, 0, 0, st.st_nlink, st.st_uid, st.st_gid, st.st_size,
                                     int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))
    monkeypatch.setattr(disk_usage, "_walk", no_inodes)
    assert pm._tree_bytes(d, set()) >= 12000

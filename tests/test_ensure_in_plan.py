"""Decided, non-excluded assets with sheets stay in the library plan, and the one "ensure in plan"
step (library_entries.ensure_in_plan) brings back any that were dropped before star, Extract
reference views, Brief & build, Make 3D and Generate missing act on them. `ap library repair-plan`.
Synthetic assets."""
import time

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import areas, kit_proc, library_entries, library_review, review
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import s1_plan, s2_refs, s3_views, s5_3d, sw_library
from asset_pipeline.ui import app as ui_app
from test_sheets_needed import BRIEF, ent

REF = {"decision": "reference", "frame": "frames/r.png"}
KIT = {"decision": "kit", "route": "procedural", "pieces": {"bay": {"size": [4, 4, 6], "description": "A bay."},
                                                            "corner": {"size": [4, 4, 6], "description": "A corner."}}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("demo")
    ents = [ent("cube"), ent("tower", route="trellis"), ent("kit-x"), ent("orig"), ent("gone-src"),
            ent("new-src"), ent("excl"), ent("undec"), ent("tree", kind="vegetation"), ent("fresh")]
    write_json(sw_library.library_path(s), {"min_share": 0.0005, "sources": {}, "mood": {}, "candidates": [], "checked": True,
                                            "entries": [], "unseen": ents, "surfaces": {}})
    sw_library.save_custom(s, {"copy-src": ent("copy-src", custom=True, duplicate_of="orig", variant=True)})
    write_json(sw_library.decisions_path(s), {
        "cube": REF, "tower": REF, "kit-x": KIT, "orig": REF, "fresh": {"decision": "description"},
        "copy-src": {"decision": "source"},            # a copy made before 2026-10-25: no frame of its own
        "gone-src": {"decision": "source"},            # its frame is gone, no crop on disk
        "new-src": {"decision": "source"},             # never planned: nothing shows it
        "excl": {"decision": "exclude"}, "tree": {"decision": "description"}})
    for a in ("cube", "tower", "kit-x-bay", "kit-x-corner", "orig", "copy-src", "gone-src", "excl"):
        d = s.root / "refs" / "library" / a
        d.mkdir(parents=True)
        (d / "sheet_000.png").write_bytes(b"x")
    write_json(kit_proc.spec_path(s, "cube"), BRIEF)
    monkeypatch.setattr(library_review, "entry_of", lambda store, asset: None)
    # the plan as it was while these were included (new-src can't be planned, so it never was)
    sw_library.asset_plan(s, only=["cube", "tower", "kit-x", "orig", "copy-src"], keep_included=False)
    return s


def included(s):
    return sw_library.included_assets(s)


def drop(s, *assets):
    """What an older plan rewrite did: the assets stay listed, no longer included."""
    plan = s1_plan.load(s, sw_library.PLAN_NAME)
    for a in plan.assets:
        if a.id in assets:
            a.include = False
    s1_plan.save(s, sw_library.PLAN_NAME, plan)
    assert not set(assets) & included(s)


def test_the_plan_rewrite_that_dropped_them_no_longer_does(store):
    """asset_plan(only=...) (ap library sheets --only / --area) used to include just those entries."""
    assert {"cube", "tower", "kit-x-bay", "kit-x-corner", "orig", "copy-src"} <= included(store)
    sw_library.asset_plan(store, only=["fresh"])
    assert {"cube", "tower", "kit-x-bay", "kit-x-corner", "orig", "copy-src", "fresh"} <= included(store)
    sw_library.asset_plan(store, only=["fresh"], keep_included=False)            # the old narrowing, still there on request
    assert included(store) == {"fresh"}


def test_sync_plan_keeps_a_good_source_copy_and_an_asset_whose_frame_is_gone(store):
    """sync_plan dropped a good-source copy (no frame of its own) that asset_plan would plan."""
    crop = sw_library.crop_path(store, "gone-src")                          # it was planned from its crop; the frame went since
    crop.parent.mkdir(parents=True, exist_ok=True)
    crop.write_bytes(b"x")
    sw_library.asset_plan(store, only=["copy-src", "gone-src"], keep_included=False)
    crop.unlink()
    before = included(store)
    assert {"copy-src", "gone-src"} <= before
    library_entries.sync_plan(store)
    assert included(store) == before                                       # none dropped
    plan = {a.id: a for a in s1_plan.load(store, sw_library.PLAN_NAME).assets}
    assert plan["gone-src"].refs == [] and plan["copy-src"].refs          # kept without a look reference / by its copied crop


def test_excluding_or_undeciding_is_what_takes_an_asset_out(store):
    sw_library.decide(store, "tower", "exclude")
    library_entries.sync_plan(store)
    assert "tower" not in included(store) and "cube" in included(store)
    sw_library.decide(store, "cube", None)
    library_entries.sync_plan(store)
    assert "cube" not in included(store)


def test_ensure_in_plan_brings_back_what_is_decided_and_says_why_not_for_the_rest(store):
    drop(store, "cube", "kit-x-bay", "kit-x-corner")
    assert library_entries.ensure_in_plan(store, ["cube", "kit-x-bay", "tower"]) == ["cube", "kit-x-bay"]
    assert {"cube", "kit-x-bay", "kit-x-corner"} <= included(store)        # the whole kit decision comes back
    plan_file = store.root / "plans" / "library.json"
    mtime = plan_file.stat().st_mtime_ns if plan_file.exists() else None
    assert library_entries.ensure_in_plan(store, ["cube", "tower"]) == []    # all in: a no-op, nothing written
    assert (plan_file.stat().st_mtime_ns if plan_file.exists() else None) == mtime
    for asset, why in (("undec", "not decided"), ("excl", "excluded"), ("new-src", "no shot frame"), ("tree", "foliage"),
                       ("nope", "not a library asset"), ("kit-x-wing", "not a library asset")):
        with pytest.raises(ValueError, match=why):
            library_entries.ensure_in_plan(store, [asset])
        assert library_entries.ensure_in_plan(store, [asset], strict=False) == []       # skipped, not fatal
    assert "excl" not in included(store) and "undec" not in included(store)


def test_starring_a_sheet_of_a_dropped_asset_brings_it_back(store):
    drop(store, "tower")
    c = TestClient(ui_app.create_app(JobQueue()))
    r = c.post("/api/projects/demo/star", json={"path": "refs/library/tower/sheet_000.png", "starred": True})
    assert r.status_code == 200 and "tower" in included(store)
    drop(store, "tower")
    c.post("/api/projects/demo/star", json={"path": "refs/library/tower/sheet_000.png", "starred": False})
    assert "tower" not in included(store)                                  # unstarring changes nothing
    assert c.post("/api/projects/demo/star", json={"path": "refs/library/excl/sheet_000.png", "starred": True}).status_code == 200
    assert "excl" not in included(store)                                   # an excluded asset's sheet can still be starred


def test_extract_reference_views_finds_the_asset_the_plan_dropped(store, monkeypatch):
    drop(store, "tower", "cube")
    key = "refs/library/tower/sheet_000.png"
    u = s3_views._unit_for_sheet(store, key, ensure=False)                  # a listing: found as a pending unit, nothing written
    assert u.key == "tower" and u.pending and "tower" not in included(store)
    cut = []
    monkeypatch.setattr(s3_views, "cut", lambda st, sheet, **k: cut.append(sheet) or [])
    review.set_star(store, key)
    review.set_star(store, "refs/library/cube/sheet_000.png")
    assert s3_views.cut_starred(store, log=lambda m: None) == 0
    assert set(cut) == {key, "refs/library/cube/sheet_000.png"} and {"tower", "cube"} <= included(store)
    drop(store, "excl")
    with pytest.raises(ValueError, match="excluded"):
        s3_views._unit_for_sheet(store, "refs/library/excl/sheet_000.png")


def test_brief_and_build_and_make_3d_bring_it_back_first(store, monkeypatch):
    drop(store, "cube", "tower")
    calls = []
    monkeypatch.setattr(library_review, "rebuild", lambda st, a, log=None: calls.append(a) or {"images": []})
    c = TestClient(ui_app.create_app(JobQueue()))
    r = c.post("/api/projects/demo/library/asset/cube/brief-build", json={}).json()        # it has a brief: build only
    for _ in range(200):
        if c.get(f"/api/jobs/{r['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert calls == ["cube"] and "cube" in included(store)
    with pytest.raises(ValueError, match="choose a view first"):                              # Make 3D: in the plan, then no view yet
        s5_3d.run(store, "library", "tower")
    assert "tower" in included(store)
    with pytest.raises(ValueError, match="choose a view first"):              # an undecided one just runs as before: no plan change
        s5_3d.run(store, "library", "undec")
    assert "undec" not in included(store)
    assert c.post("/api/projects/demo/library/asset/excl/brief-build", json={}).status_code == 400


def test_generate_missing_includes_the_assets_it_makes_sheets_for(store, monkeypatch):
    made = []
    monkeypatch.setattr(s2_refs, "generate", lambda st, u, n=2, **k: made.append(u.key) or [])
    s2_refs.generate_missing(store, log=lambda *a: None)
    assert made == ["fresh"] and "fresh" in included(store) and {"cube", "tower"} <= included(store)


def test_repair_plan_lists_then_adds_every_decided_asset_with_a_sheet(store, capsys):
    from asset_pipeline import cli
    drop(store, "cube", "tower", "kit-x-bay", "kit-x-corner", "orig")
    sw_library.decide(store, "excl", "exclude")
    cli.main(["library", "repair-plan", "demo", "--dry-run"])
    out = capsys.readouterr().out
    for a in ("cube", "tower", "kit-x-bay", "kit-x-corner", "orig"):
        assert f"would add {a}:" in out
    assert "excl" not in out and "fresh" not in out and "undec" not in out           # excluded / no sheet / undecided
    assert "would add 5 asset(s)" in out and not ({"cube", "tower", "orig"} & included(store))   # a dry run writes nothing
    cli.main(["library", "repair-plan", "demo"])
    out = capsys.readouterr().out
    assert "added cube: reference, 1 sheet(s)" in out and "added 5 asset(s)" in out
    assert {"cube", "tower", "kit-x-bay", "kit-x-corner", "orig"} <= included(store)
    cli.main(["library", "repair-plan", "demo"])                                       # nothing left: idempotent
    assert "added 0 asset(s)" in capsys.readouterr().out
    assert "excl" not in included(store)


def test_cli_sheets_for_an_entry_keeps_the_others_in_the_plan(store, capsys):
    from asset_pipeline import cli
    cli.main(["library", "sheets", "demo", "--only", "fresh", "--plan-only"])
    out = capsys.readouterr().out
    assert "fresh" in out and "cube" not in out                                         # it reports the entries asked for
    assert {"cube", "tower", "kit-x-bay", "orig", "fresh"} <= included(store)           # the others stay included


def test_the_sheets_verdict_agrees_with_what_the_plan_can_hold(store):
    states = areas.sheet_states(store)
    assert states["new-src"] == "no frame"                                              # nothing shows it: not counted
    assert states["gone-src"] == "sheets" and states["copy-src"] == "sheets"            # decided, have sheets, plannable

"""next_build: what the next city build places per library asset, and why. The explanation and the
build share one source (city_library takes its gate and models from next_build), so these tests
check the explanation against what city_library actually places. Synthetic projects only."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from asset_pipeline import city_library as cl, kit_proc, library_review, next_build, versions
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import write_json
from asset_pipeline.stages import s6_cleanup, sw_library, sw_site
from asset_pipeline.ui import app as ui_app

from tests.test_assembly import glb_box
from tests.test_entry_variants import _trellis, market  # noqa: F401  (fixture)
from tests.test_library_entries import _entry, proj  # noqa: F401  (fixture)


def _rows(store, use_active=False):
    return {r["entry"]: r for r in next_build.explain(store, use_active)["assets"]}


def test_props_explanation_matches_what_the_build_places(market, monkeypatch):    # noqa: F811
    monkeypatch.setattr(s6_cleanup, "_asset", lambda *a: SimpleNamespace(usage="game"))
    r = _rows(market)
    assert r["canopy-table"]["role"] == "prop" and not r["canopy-table"]["placed"]
    assert r["canopy-table"]["stand_in"] == "no 3D model yet"
    assert r["canopy-table"]["line"] == "Next city build: greybox stand-in (no 3D model yet)"
    _trellis(market, "canopy-table")
    _trellis(market, "canopy-table-02")
    r = _rows(market)                                    # built, not approved: approved-only keeps the greybox
    assert not r["canopy-table"]["placed"] and r["canopy-table"]["stand_in"].startswith("not approved")
    assert r["canopy-table"]["version"] == 1 and r["canopy-table-03"]["stand_in"] == "no 3D model yet"
    for use_active in (False, True):
        gb = sw_site.load_greybox(market)
        from asset_pipeline import variants
        props, _, missing = cl.prop_placements(market, gb, variants.assignments(market, gb))
        ok = cl.ok_entries(market, use_active)
        placed = {p["entry"]: p["glb"] for p in props if ok(p["entry"])}       # what plan() keeps
        rows = _rows(market, use_active)
        assert {e for e, x in rows.items() if x["placed"]} == set(placed)
        for e, glb in placed.items():
            assert glb == next_build.placed_glb(market, e).relative_to(market.root).as_posix()
            assert rows[e]["version"] == versions.current(market, e) and rows[e]["model"] == "raw"
    library_review.set_status(market, "canopy-table", "approved", "")
    r = _rows(market)
    assert r["canopy-table"]["placed"] and r["canopy-table"]["approved"] and r["canopy-table"]["approved_version"] == 1
    assert r["canopy-table"]["line"] == "Next city build uses: v1 · raw model (props don't use Clean up) · approved"
    assert not r["canopy-table-02"]["placed"]
    r = _rows(market, True)
    assert r["canopy-table-02"]["line"] == "Next city build uses: v1 · raw model (props don't use Clean up) · not approved (Use Active versions)"
    assert next_build.explain(market, True)["counts"] == {"approved": 1, "active_not_approved": 1, "stand_ins": 1, "need_cleanup": 0}


def test_a_trellis_building_explains_the_model_use_active_models_places(proj, monkeypatch):     # noqa: F811
    monkeypatch.setattr(s6_cleanup, "_asset", lambda *a: SimpleNamespace(usage="game"))
    write_json(sw_site.site_dir(proj) / "greybox.json", {"slots": [], "shots": []})
    sw_site._greybox_memo.d = None
    write_json(sw_library.library_path(proj), {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [], "checked": True,
                                               "entries": [_entry("hall", kind="building", slot_ids=["s1"]), _entry("shed", slot_ids=[])],
                                               "unseen": [], "surfaces": {}})
    d = kit_proc.out_dir(proj, "hall")
    glb_box(d / "procedural.glb", [-6, -4, 0], [6, 4, 10])
    write_json(d / "procedural" / "job.json", {})
    r = _rows(proj, True)
    assert r["hall"]["role"] == "building" and r["hall"]["model"] == "procedural" and r["hall"]["version"] is None
    assert r["hall"]["line"] == "Next city build uses: the procedural build on disk · procedural build · not approved (Use Active versions)"
    assert r["shed"]["role"] == "none" and r["shed"]["stand_in"].startswith("not in the city layout")
    _trellis(proj, "hall")                               # a TRELLIS Active version takes the building's places
    m = cl.active_model(proj, "hall")
    assert m == next_build.building_model(proj, "hall") and m["cleaned"] is None
    pl = [{"key": "hall_s1", "entry": "hall", "glb": "3d/library/hall/procedural.glb", "x": 0.0, "y": 0.0, "z": 0.0,
           "yaw": 0.0, "w": 12.0, "d": 8.0, "slot": "s1", "typed": False}]
    bounds = {"3d/library/hall/procedural.glb": [[-6, -4, 0], [6, 4, 10]], m["glb"]: [[-2, -1.5, 0], [2, 1.5, 2.7]]}
    cl.use_active_models(proj, {"slots": []}, None, pl, bounds)
    r = _rows(proj, True)["hall"]
    assert pl[0]["glb"] == m["raw"] and pl[0]["active"] == r["version"] == 1
    assert r["model"] == "raw" and r["cleanup_used"] and not r["cleaned"]
    assert r["line"] == "Next city build uses: v1 · raw model, not cleaned for game · not approved (Use Active versions)"
    assert next_build.counts([r])["need_cleanup"] == 1
    out = proj.root / "cleanup/library/hall/a_1024_cascade_s1_game.glb"     # Clean up for its tier: the build takes it
    glb_box(out, [-2, -1.5, 0], [2, 1.5, 2.7])
    write_json(out.with_suffix(".json"), {"glb": "cleanup/library/hall/a_1024_cascade_s1_game.glb", "source": m["raw"],
                                          "usage": "game", "output_faces": 900})
    pl[0]["glb"] = "3d/library/hall/procedural.glb"
    bounds[out.relative_to(proj.root).as_posix()] = [[-2, -1.5, 0], [2, 1.5, 2.7]]
    cl.use_active_models(proj, {"slots": []}, None, pl, bounds)
    r = _rows(proj, True)["hall"]
    assert pl[0]["glb"] == "cleanup/library/hall/a_1024_cascade_s1_game.glb" and r["model"] == "cleaned"
    assert r["line"] == "Next city build uses: v1 · cleaned for game · not approved (Use Active versions)"
    assert _rows(proj)["hall"]["stand_in"] == "not approved; only approved assets are placed"


def test_no_greybox_and_excluded_entries_and_the_api(proj):    # noqa: F811
    write_json(sw_library.library_path(proj), {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [], "checked": True,
                                               "entries": [_entry("bench")], "unseen": [], "surfaces": {}})
    assert _rows(proj)["bench"]["stand_in"] == "no greybox yet"
    write_json(sw_site.site_dir(proj) / "greybox.json", {"slots": [], "shots": []})
    sw_site._greybox_memo.d = None
    sw_library.decide(proj, "bench", "exclude")
    assert _rows(proj)["bench"]["stand_in"] == "excluded"
    c = TestClient(ui_app.create_app(JobQueue()))
    d = c.get("/api/projects/p/next-build", params={"use_active": True}).json()
    assert d["use_active"] and d["assets"][0]["entry"] == "bench" and d["assets"][0]["placement"] == {}
    assert not d["placement_checked"] and d["counts"]["placement_issues"] == 0
    from asset_pipeline import ue_build                    # the last build's check: issues per asset
    write_json(ue_build.report_path(proj), {"placement": {"ok": False, "per_entry": {"bench": {"floating": 2}}}, "finished": 1.0})
    d = c.get("/api/projects/p/next-build").json()
    assert d["placement_checked"] and d["assets"][0]["placement"] == {"floating": 2} and d["counts"]["placement_issues"] == 1
    assert not d["use_active"]


def test_the_build_takes_its_choices_from_next_build(proj, monkeypatch):    # noqa: F811
    """One source: city_library's gate, building model and the props' / pieces' model are next_build's."""
    from asset_pipeline import assembly
    monkeypatch.setattr(next_build, "gate", lambda store, use_active: (lambda e: e == "only-me"))
    ok = cl.ok_entries(proj, False)
    assert ok("only-me") and not ok("other")
    monkeypatch.setattr(next_build, "building_model", lambda store, e: {"n": 9, "kind": "trellis", "glb": "x.glb"})
    assert cl.active_model(proj, "any")["n"] == 9
    glb = proj.root / "m.glb"
    glb_box(glb, [-1, -1, 0], [1, 1, 2])
    monkeypatch.setattr(next_build, "placed_glb", lambda store, e: glb)
    assert assembly.model(proj, "any")["glb"] == glb

"""Upper tiers never wider than the tier under them (kit_assembly + the "overhang" check), rows
never sliced into houses narrower than min_house_w, and an entry's non-procedural Active version
standing at all its placements. Synthetic slots and stand-in builds only."""
import numpy as np
import pytest

from asset_pipeline import building_gen, city_library as cl, kit_assembly as ka
from asset_pipeline.building_gen import features, row_houses

from test_vertical import Fake, Flat, _check, _kit_plan, _slot, proj  # noqa: F401  (proj: fixture)


# --- 1. overhangs ----------------------------------------------------------------------------

def test_a_setback_reaching_past_its_lower_mass_is_clamped_to_it():
    # a 20 x 20 lower mass and a 14 x 14 top shifted 6 m east: 3 m of it hangs past the edge
    blocks = ka.outline(ka.slot_boxes(*_slot("s", [(0, 0, 0, 20, 20, 12.0), (6, 0, 12.0, 14, 14, 9.0)])))
    top = next(b for b in blocks if b["tier"] == 1)
    xs = [x for x, _ in top["pts"]]
    assert max(xs) == pytest.approx(10.0, abs=0.01) and min(xs) == pytest.approx(-1.0, abs=0.01)


def test_an_upper_mass_wider_than_its_base_is_one_building_on_one_outline():
    # a big block on a narrower lower mass (about as large: no setback): one tier, no stacking
    blocks = ka.outline(ka.slot_boxes(*_slot("s", [(0, 0, 0, 30, 20, 6.0), (0, 0, 6.0, 34, 24, 20.0)])))
    assert [(b["tier"], b["role"]) for b in blocks] == [(0, "single")]


def test_the_check_flags_an_upper_tier_wider_than_the_one_under_it(proj):
    pl, reps, bounds, fake, s = _kit_plan(proj, [(20, 12, 0, 40, 24, 12.0), (20, 12, 12.0, 24, 16, 20.0)])
    assert not _check(pl, bounds, {"blk-1": s})["issues"].get("overhang")
    for p in pl:                                       # the upper tier's ring pushed 12 m out east
        if p["tier"] == 1:
            p["matrix"] = (np.asarray(p["matrix"]) + np.array([[0, 0, 0, 12.0]] + [[0] * 4] * 3)).tolist()
    res = _check(pl, bounds, {"blk-1": s})
    assert res["issues"].get("overhang") and "overhang" in " ".join(cl_lines(res))


def cl_lines(res):
    from asset_pipeline.placement_check import lines
    return lines(res)


def test_a_top_too_small_for_the_modules_is_built_into_the_tier_under_it(proj):
    # 16 m corner modules can't ring a 10 x 10 top: it joins the lower block, whose modules under it rise
    masses = [(20, 12, 0, 40, 24, 12.0), (6, 4, 12.0, 10, 10, 9.0)]
    pl, reps, bounds, fake, s = _kit_plan(proj, masses)
    assert {p["tier"] for p in pl} == {0} and reps[0]["folded"] == 1
    assert any("too small for the modules" in line for line in ka.report_lines(reps))
    res = _check(pl, bounds, {"blk-1": s})
    assert not {"overhang", "roof_under", "ground_floor_up", "floating"} & set(res["issues"]), res["issues"]
    tops = sorted({round(bounds[p["glb"]][1][2]) for p in pl})
    assert len(tops) == 2 and tops[-1] >= 19             # those under the top reach it, the rest stay at 12 m


def test_a_wall_thin_block_gets_no_ring():
    blocks = ka.outline(ka.slot_boxes(*_slot("s", [(0, 0, 0, 30, 20, 10.0), (0, 30, 0, 25, 1.2, 10.0)])))
    assert len(blocks) == 1


# --- 2. row houses ---------------------------------------------------------------------------

@pytest.mark.parametrize("w,k", [(24.0, 4), (16.0, 3), (10.0, 2), (7.0, 1), (3.0, 1)])
def test_a_row_never_has_houses_narrower_than_the_minimum(w, k):
    f = features("row", {})
    assert f["min_house_w"] == 5.0 and row_houses(w, f) == k
    assert row_houses(7.0, features("row", {"min_house_w": 0})) == 4      # off: the brief's count
    parts = building_gen.row([w, 12.0, 9.6], {}, f)
    walls = [p for p in parts if p.get("mat") == "wall" and p["kind"] == "box" and p["x1"] - p["x0"] < 0.31]
    assert len(walls) == 2 * k                          # two party walls per house


# --- 3. the Active version ---------------------------------------------------------------------

def test_an_entry_with_a_trellis_active_version_stands_with_it_everywhere(proj, monkeypatch):
    bounds = {"3d/library/hall/instances/a.glb": [[-6, -4, 0], [6, 4, 10]],
              "3d/library/hall/instances/b.glb": [[-10, -5, 0], [10, 5, 14]],
              "3d/library/hall/model.glb": [[-1, -0.5, -0.2], [1, 0.5, 0.8]],
              "3d/library/other/procedural.glb": [[-5, -5, 0], [5, 5, 9]]}
    monkeypatch.setattr(cl, "active_model", lambda store, e: {"n": 6, "kind": "trellis", "glb": "3d/library/hall/model.glb"}
                        if e == "hall" else None)
    pl = [{"key": "hall_a", "entry": "hall", "glb": "3d/library/hall/instances/a.glb", "x": 50.0, "y": 0.0, "z": 3.0,
           "yaw": 30.0, "w": 12.0, "d": 8.0, "typology": "hall", "typed": True, "slot": "t1"},
          {"key": "hall_b", "entry": "hall", "glb": "3d/library/hall/instances/b.glb", "x": -50.0, "y": 0.0, "z": 0.0,
           "yaw": 0.0, "w": 20.0, "d": 10.0, "typology": "hall", "typed": True, "slot": "t2"},
          {"key": "other_s", "entry": "other", "glb": "3d/library/other/procedural.glb", "x": 0.0, "y": 0.0, "z": 0.0,
           "yaw": 0.0, "w": 10.0, "d": 10.0, "slot": "s1", "typed": False}]
    act = cl.use_active_models(proj, {"slots": []}, None, pl, bounds)
    assert act == {"hall": {"n": 6, "kind": "trellis", "model": "raw GLB (no Clean up output for its tier)", "placements": 2}}
    assert cl.active_lines(act) == ["hall: 2 placements use Active v6 TRELLIS, raw GLB (no Clean up output for its tier)"]
    a, b, other = pl
    assert a["glb"] == b["glb"] == "3d/library/hall/model.glb" and a["mesh"] == b["mesh"] == "active_hall_v6"
    assert other["glb"] == "3d/library/other/procedural.glb" and "active" not in other
    cl.ground_all(proj, pl, Flat(), "min", bounds)
    for p, (w, d, h) in ((a, (12, 8, 10)), (b, (20, 10, 14))):
        lo, hi = bounds[p["glb"]]
        f = cl.footprint_of(cl.world_matrix(p), lo, hi)
        assert (f["w"], f["d"], f["base"]) == pytest.approx((w, d, 0.0), abs=1e-6)        # its footprint, on the ground
        assert (f["x"], f["y"], f["yaw"]) == pytest.approx((p["x"], p["y"], p["yaw"]), abs=1e-6)
        assert np.linalg.norm(np.asarray(p["matrix"])[:3, 2]) * (hi[2] - lo[2]) == pytest.approx(h)


def test_an_active_trellis_model_places_its_clean_up_output_for_its_tier(proj, monkeypatch):
    """Clean up of the Active version for the asset's own tier wins; another tier's output, or one of
    another version, doesn't; without one the raw model stays."""
    from types import SimpleNamespace
    from asset_pipeline import versions
    from asset_pipeline.stages import s6_cleanup
    raw = "3d/library/hall/model_s1.glb"
    monkeypatch.setattr(versions, "current", lambda store, e, *a: 4)
    monkeypatch.setattr(versions, "get", lambda store, e, n, *a: {"kind": "trellis"})
    monkeypatch.setattr(versions, "current_glb", lambda store, e, *a: proj.root / raw)
    monkeypatch.setattr(s6_cleanup, "_asset", lambda store, plan, a: SimpleNamespace(usage="game"))
    reps = []
    monkeypatch.setattr(s6_cleanup, "results", lambda store, plan, a: reps)
    m = cl.active_model(proj, "hall")
    assert m["glb"] == raw and m["cleaned"] is None
    reps += [{"glb": "cleanup/library/hall/model_s1_cine.glb", "source": raw, "usage": "cine", "output_faces": 900000},
             {"glb": "cleanup/library/hall/other_game.glb", "source": "3d/library/hall/other.glb", "usage": "game"},
             {"glb": "cleanup/library/hall/model_s1_game.glb", "source": raw, "usage": "game", "output_faces": 30000,
              "retopo_method": "decimate"}]
    m = cl.active_model(proj, "hall")
    assert m["glb"] == "cleanup/library/hall/model_s1_game.glb" and m["raw"] == raw
    assert cl.model_label(m) == "Cleaned (game, decimate) 30,000 faces"
    bounds = {"3d/library/hall/instances/a.glb": [[-6, -4, 0], [6, 4, 10]], m["glb"]: [[-1, -1, 0], [1, 1, 2]]}
    pl = [{"key": "hall_a", "entry": "hall", "glb": "3d/library/hall/instances/a.glb", "x": 0.0, "y": 0.0, "z": 0.0,
           "yaw": 0.0, "w": 12.0, "d": 8.0, "typology": "hall", "typed": True, "slot": "t1"}]
    act = cl.use_active_models(proj, {"slots": []}, None, pl, bounds)
    assert pl[0]["glb"] == m["glb"] and pl[0]["mesh"] == "active_hall_v4_game"
    assert cl.active_lines(act) == ["hall: 1 placements use Active v4 TRELLIS, Cleaned (game, decimate) 30,000 faces"]


def test_the_build_report_lists_entries_placed_with_their_active_model():
    from asset_pipeline import ue_build
    r = {"ok": True, "placed": 3, "props": 0, "attached": 0, "slots_replaced": 2, "greybox_slots": 1, "held": 0,
         "imported": 1, "skipped": 0, "reset": 0, "pruned": 0, "terrain_outer": False, "shots": None, "seconds": 60,
         "steps": {}, "warnings": [], "placement": None, "active_models": ["my-hall: 14 placements use Active v6 TRELLIS"]}
    assert "my-hall: 14 placements use Active v6 TRELLIS" in ue_build.report_lines(r)

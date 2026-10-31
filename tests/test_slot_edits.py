"""Hand edits to the greybox in Blender (Re-read .blend): every slot against the greybox as built,
and the city plan's typed buildings following their slot. Pure: no project, no Blender."""
import math

import numpy as np

from asset_pipeline import city_library as cl
from asset_pipeline.stages import sw_site


def _m(x=0.0, y=0.0, yaw=0.0, k=(1, 1, 1)):
    return np.asarray(sw_site._pose([x, y, 0.0], yaw, k), float)


def _slot(sid, M, typology="insula", half=20.0, pieces=None):
    c = M[:3, 3]
    return {"id": sid, "typology": typology, "matrix": M.tolist(),
            "bbox": [[c[0] - half, c[1] - half, 0.0], [c[0] + half, c[1] + half, 10.0]],
            "pieces": pieces if pieces is not None else [{"name": f"{sid}.mass.000", "matrix_local": np.eye(4).tolist()}]}


def _built(*slots):
    return {s["id"]: sw_site._as_built(s) for s in slots}


def _bld(key, slot, x, y, yaw=0.0):
    return {"key": key, "entry": "insula", "slot": slot, "typed": True, "x": x, "y": y, "z": 0.0, "yaw": yaw,
            "w": 10.0, "d": 8.0, "glb": f"3d/{key}.glb"}


def _moves(built, now, added=None):
    out = {sid: [(sid, sw_site.slot_delta(built[sid], s))] if (s := now.get(sid)) else [] for sid in built}
    for sid, src in (added or {}).items():
        out[src].append((sid, sw_site.slot_delta(built[src], now[sid])))
    return out


def test_an_untouched_greybox_has_no_edits_and_buildings_stay():
    a = _slot("a", _m(100, 0))
    built = _built(a)
    assert sw_site.slot_edits(built, [a], {}) == {"moved": [], "resized": [], "removed": [], "added": {}}
    p = _bld("k", "a", 104, 2, 30)
    assert cl.follow_slots([p], _moves(built, {"a": a})) == [p]
    assert sw_site.edits_line(sw_site.slot_edits(built, [a], {})) == "0 moved, 0 resized, 0 removed, 0 added"


def test_a_moved_slot_takes_its_buildings_along():
    a0 = _slot("a", _m(100, 0))
    a1 = _slot("a", _m(130, -20))
    built = _built(a0)
    assert sw_site.slot_edits(built, [a1], {})["moved"] == ["a"]
    [q] = cl.follow_slots([_bld("k", "a", 104, 2, 30)], _moves(built, {"a": a1}))
    assert (round(q["x"], 6), round(q["y"], 6), round(q["yaw"], 6)) == (134, -18, 30)
    assert q["key"] == "k" and q["slot"] == "a" and "scale" not in q


def test_a_turned_slot_turns_its_buildings_about_the_slot():
    a0, a1 = _slot("a", _m(100, 0)), _slot("a", _m(100, 0, 90))
    built = _built(a0)
    assert sw_site.slot_edits(built, [a1], {})["moved"] == ["a"]
    [q] = cl.follow_slots([_bld("k", "a", 110, 0, 0)], _moves(built, {"a": a1}))
    assert np.allclose([q["x"], q["y"], q["yaw"]], [100, 10, 90])


def test_a_resized_slot_spreads_and_scales_its_buildings():
    a0, a1 = _slot("a", _m(100, 0)), _slot("a", _m(100, 0, 0, (2, 1, 1.5)))
    built = _built(a0)
    e = sw_site.slot_edits(built, [a1], {})
    assert e["resized"] == ["a"] and e["moved"] == []
    [q] = cl.follow_slots([_bld("k", "a", 110, 4, 90)], _moves(built, {"a": a1}))
    assert np.allclose([q["x"], q["y"]], [120, 4])
    assert np.allclose(q["scale"], [1, 2, 1.5])          # turned 90: the building's own x lies along the slot's y
    W = cl.world_matrix(q)
    assert np.allclose(np.linalg.norm(W[:3, :3], axis=0), [1, 2, 1.5])
    assert math.isclose(cl.footprint_of(W, [-5, -4, 0], [5, 4, 10])["d"], 16)


def test_child_meshes_moved_together_move_the_slot():
    """In Blender a click selects the block's meshes, not its empty: they moved, the empty didn't."""
    piece = {"name": "a.mass.000", "matrix_local": np.eye(4).tolist()}
    a0 = _slot("a", _m(100, 0), pieces=[piece, piece | {"name": "a.mass.001"}])
    shifted = _m(5, 0).tolist()
    a1 = _slot("a", _m(100, 0), pieces=[{"name": "a.mass.000", "matrix_local": shifted},
                                        {"name": "a.mass.001", "matrix_local": shifted}])
    built = _built(a0)
    assert sw_site.slot_edits(built, [a1], {})["moved"] == ["a"]
    [q] = cl.follow_slots([_bld("k", "a", 100, 0)], _moves(built, {"a": a1}))
    assert np.isclose(q["x"], 105)
    one = _slot("a", _m(100, 0), pieces=[{"name": "a.mass.000", "matrix_local": shifted}, piece | {"name": "a.mass.001"}])
    assert sw_site.slot_edits(built, [one], {})["moved"] == []       # only one of them: the slot stays


def test_a_deleted_slot_holds_its_buildings():
    a, b = _slot("a", _m(100, 0)), _slot("b", _m(200, 0))
    built = _built(a, b)
    assert sw_site.slot_edits(built, [b], {})["removed"] == ["a"]
    [q] = cl.follow_slots([_bld("k", "a", 100, 0)], _moves(built, {"b": b}))
    assert q["slot"] is None and q["removed_slot"] == "a"
    placed, _, held = cl.choose([q], {}, lambda e: True)
    assert placed == [] and held[0]["why"] == "its greybox slot was deleted"


def test_a_duplicated_slot_gets_copies_of_its_buildings():
    a, a2 = _slot("a", _m(100, 0)), _slot("a-2", _m(100, 60, 0))
    built = _built(a)
    e = sw_site.slot_edits(built, [a, a2], {"a-2": "a"})
    assert e["added"] == {"a-2": "a"} and e["moved"] == []
    out = cl.follow_slots([_bld("ins_104_2", "a", 104, 2)], _moves(built, {"a": a, "a-2": a2}, e["added"]))
    assert [(q["key"], q["slot"]) for q in out] == [("ins_104_2", "a"), ("ins_104_2_am2", "a-2")]
    assert np.allclose([out[1]["x"], out[1]["y"]], [104, 62]) and out[1]["glb"] == out[0]["glb"]   # the same build, no rebuild
    placed, replaced, _ = cl.choose(out, {"a": 1, "a-2": 1}, lambda e: True)
    assert len(placed) == 2 and replaced == {"a": "footprints", "a-2": "footprints"}


def test_a_duplicate_finds_its_source_without_a_tag():
    built = {"a": {}, "b-2": {}}
    assert sw_site.origin("a-3", {}, built) == "a"
    assert sw_site.origin("a-3", {"a-3": "a-2", "a-2": "a"}, built) == "a"   # a copy of a copy
    assert sw_site.origin("b-2-2", {}, built) == "b-2"
    assert sw_site.origin("new-block", {}, built) is None


def test_slot_moves_without_built_slots_file_reads_the_spec(tmp_path):
    """A greybox built before built_slots.json: the spec Blender built it from is the as-built pose."""
    from asset_pipeline.project import ProjectStore, write_json
    store = ProjectStore(tmp_path)
    write_json(tmp_path / "site/build/spec.json", {"slots": [
        {"id": "a", "typology": "insula", "loc": [100, 0, 0], "rot_z": 0,
         "pieces": [{"name": "a.mass.000", "piece": "mass", "prim": {"kind": "box"}, "loc": [0, 0, 0], "rot_z": 0},
                    {"name": "x", "piece": "street", "prim": {"kind": "ribbon"}, "loc": [0, 0, 0], "rot_z": 0}]}]})
    now = _slot("a", _m(110, 0), pieces=[{"name": "a.mass.000", "matrix_local": np.eye(4).tolist()},
                                         {"name": "a.street", "matrix_local": np.eye(4).tolist()}])
    slots, to = sw_site.slot_moves(store, {"slots": [now]})
    assert np.allclose(slots[0]["bbox"], [[80, -20, 0], [120, 20, 10]])      # the bbox now, moved back
    assert np.isclose(to["a"][0][1][0, 3], 10)
    assert sw_site.slot_moves(ProjectStore(tmp_path / "none"), {"slots": [now]}) == ([now], {})

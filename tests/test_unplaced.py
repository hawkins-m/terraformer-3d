"""Use unplaced assets (unplaced.py): built assets with no slot in the city plan take a share of the
places that fit them at build time. The selection rules on synthetic places, then city_library.plan on
a synthetic plan: off is the plan exactly, on changes only the places it takes, off again restores
them. Synthetic projects only."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from asset_pipeline import city_library as cl, kit_assembly, unplaced as up
from asset_pipeline.project import ProjectStore, write_json

from tests.test_assembly import glb_box


def _place(key, entry, slot, district, w=20.0, d=15.0, group="housing", typology=None, **kw):
    return {"key": key, "entry": entry, "slot": slot, "district": district, "w": w, "d": d, "group": group,
            "typology": typology, "waterfront": False, "edge": False} | kw


def _asset(entry, group="housing", w=20.0, d=15.0, one_off=False, max_count=None, **kw):
    return {"entry": entry, "group": group, "w": w, "d": d, "one_off": one_off, "max_count": max_count} | kw


def test_a_footprint_fits_within_a_quarter_after_uniform_scaling_and_may_turn():
    assert up.fit(20, 10, 40, 20)["k"] == pytest.approx(2.0) and up.fit(20, 10, 40, 20)["err"] == 0
    f = up.fit(10, 20, 20, 10)                            # the same footprint turned a quarter
    assert f["turn"] and f["err"] == 0 and f["k"] == pytest.approx(1.0)
    assert up.fit(20, 10, 22, 13)["err"] <= up.FIT_TOL      # close enough
    assert up.fit(80, 12, 30, 30) is None                   # a long bar never fits a square
    assert up.fit(100, 100, 10, 10) is None                 # past the scale range
    assert up.fit(0, 10, 10, 10) is None


def test_one_offs_by_name_or_a_small_max_count():
    assert up.one_off("temple", "temple", None) and up.one_off("my-lighthouse", None, None)
    assert up.one_off("hall-x", "hall_x", 2) and not up.one_off("insula-copper", "insula", None)
    assert not up.one_off("library-cube", "library_cube", 4)


def test_families_take_only_their_own_kind_of_place():
    places = [_place("c1", "hall", "s2", "a", group="civic"), _place("ec", "hall", "s5", "b", group="civic", edge=True),
              _place("c3", "hall", "s6", "c", group="civic"), _place("c4", "hall", "s7", "c", group="civic"),
              _place("e1", "row", "s3", "b", edge=True), _place("w1", "row", "s4", "b", waterfront=True)]
    places += [_place(f"h{i}", "row", f"s{i}", "c") for i in range(2, 12)]
    r = up.select([_asset("house"), _asset("museum", group="civic"), _asset("pump", group="infrastructure"),
                   _asset("garden", group="public_realm")], places, [])
    took = {e: {p["key"] for p in ps} for e, ps in r["picks"].items()}
    assert took["museum"] and took["museum"] <= {"c1", "ec", "c3", "c4"}
    assert took["pump"] and took["pump"] <= {"e1", "w1"}     # infrastructure: waterfront or edge, never civic
    assert took["house"] and not took["house"] & {"c1", "ec", "c3", "c4"}
    assert "garden" not in r["picks"] and r["skipped"]["garden"] == "no free park or plaza"


def test_repeated_types_take_about_a_tenth_and_the_biggest_donor_gives_first():
    places = [_place(f"big{i}", "row-a", f"s{i % 5}", "abcde"[i % 5]) for i in range(80)]
    places += [_place(f"small{i}", "row-b", f"t{i}", "f") for i in range(20)]
    r = up.select([_asset("row-c")], places, [], seed=3)
    ps = r["picks"]["row-c"]
    assert len(ps) == 10                                    # 10% of the 100 places that fit
    assert {p["from"] for p in ps} == {"row-a"}             # the build with most placements gives first
    assert len({p["district"] for p in ps}) == 5            # spread across districts


def test_no_build_gives_more_than_a_quarter_and_assets_take_turns():
    places = [_place(f"p{i}", "row-a", f"s{i}", "abcd"[i % 4]) for i in range(40)]
    r = up.select([_asset(f"new-{k}") for k in range(5)], places, [], seed=1)
    n = sum(len(v) for v in r["picks"].values())
    assert n == int(up.DONOR_SHARE * 40) == 10
    assert sorted(len(v) for v in r["picks"].values()) == [2, 2, 2, 2, 2]    # turns, not first come
    keys = [p["key"] for v in r["picks"].values() for p in v]
    assert len(keys) == len(set(keys))                      # a place is taken once


def test_one_off_landmarks_take_one_or_two_open_places_in_different_districts():
    opens = [{"slot": f"park-{d}{i}", "district": d, "waterfront": False, "edge": False} for d in "abc" for i in range(3)]
    tried = []

    def open_fit(a, sid):
        tried.append(sid)
        return {"x": 0.0, "y": 0.0, "yaw": 0.0, "k": 1.0, "turn": False, "pieces": [], "z": 0.0}
    r = up.select([_asset("temple", group="civic", one_off=True, max_count=2), _asset("rotunda", group="civic", one_off=True, max_count=1),
                   _asset("fountain", group="public_realm", one_off=True)], [], opens, open_fit)
    assert len(r["picks"]["temple"]) == 2 and len(r["picks"]["rotunda"]) == 1
    assert len({p["district"] for p in r["picks"]["temple"]}) == 2
    slots = [p["slot"] for v in r["picks"].values() for p in v]
    assert len(slots) == len(set(slots))                    # one asset per park
    assert len(tried) == len(slots)                         # spots are searched only as far as needed


def test_civic_one_offs_fall_back_to_matching_civic_places_and_sea_steps_stay_by_the_water():
    places = [_place("c1", "hall", "s1", "a", group="civic", w=30, d=20)] + \
             [_place(f"c{i}", "hall", f"s{i}", "b", group="civic", w=30, d=20) for i in range(2, 12)]
    r = up.select([_asset("temple", group="civic", w=31, d=21, one_off=True, max_count=2)], places, [])
    assert len(r["picks"]["temple"]) == 2 and all(p["kind"] == "swap" for p in r["picks"]["temple"])
    opens = [{"slot": "inland", "district": "a", "waterfront": False, "edge": False},
             {"slot": "shore", "district": "b", "waterfront": True, "edge": False}]
    fit_any = lambda a, s: {"x": 0.0, "y": 0.0, "yaw": 0.0, "k": 1.0, "turn": False, "pieces": [], "z": 0.0}  # noqa: E731
    r = up.select([_asset("sea-steps", group="infrastructure", one_off=True)], [], opens, fit_any)
    assert [p["slot"] for p in r["picks"]["sea-steps"]] == ["shore"]


def test_a_trellis_material_variant_takes_its_own_typologys_places_at_their_size():
    places = [_place(f"i{i}", "insula-stone", f"s{i}", "ab"[i % 2], w=25, d=30, typology="insula") for i in range(20)]
    r = up.select([_asset("insula-copper", w=65, d=65, typology="insula", stretch=True)], places, [])
    assert len(r["picks"]["insula-copper"]) == 2 and all(p["own"] for p in r["picks"]["insula-copper"])
    r = up.select([_asset("insula-copper", w=65, d=65, typology="insula", stretch=False)], places, [])
    assert "insula-copper" not in r["picks"]                # a procedural build keeps its own size: no fit


def test_the_same_seed_picks_the_same_places():
    places = [_place(f"p{i}", "row-a", f"s{i}", "abcd"[i % 4]) for i in range(200)]
    a = up.select([_asset("row-b")], places, [], seed=7)
    b = up.select([_asset("row-b")], copy.deepcopy(places), [], seed=7)
    c = up.select([_asset("row-b")], places, [], seed=8)
    keys = lambda r: [p["key"] for p in r["picks"]["row-b"]]  # noqa: E731
    assert keys(a) == keys(b) and keys(a) != keys(c)


def test_a_free_spot_stays_on_the_lawn_among_the_trees_and_clear_of_buildings():
    from shapely.geometry import box
    lawn = box(0, 0, 100, 100)
    trees = [(x, y) for x in range(5, 100, 10) for y in range(5, 100, 10) if x < 60]   # planted on the west side only
    building = box(20, 40, 40, 60)
    s = up.free_spot(lawn, trees, building, 20, 10, 0.0, (50, 50))
    assert s is not None
    r = up.rect(s["x"], s["y"], 20 * s["k"], 10 * s["k"], 0.0)
    assert lawn.contains(r) and not r.intersects(building)
    assert r.bounds[2] <= 60 + up.TREE_NEAR                  # not out on the bare east side
    assert up.free_spot(box(0, 0, 10, 10), [], None, 30, 30, 0.0, (5, 5)) is None


def test_the_model_stands_centred_on_its_place_scaled_and_turned():
    B = cl.world_matrix({"x": 100.0, "y": 50.0, "z": 3.0, "yaw": 90.0})
    M = up.placed_matrix(B, [0, 0, 0], [-1, -2, 0], [1, 2, 1], [20, 10, 8], 1.5, turn=False)
    c = M @ np.array([0.0, 0.0, 0.0, 1.0])
    assert c[:3] == pytest.approx([100, 50, 3])
    corner = M @ np.array([1.0, 2.0, 1.0, 1.0])             # model +x (20 m * 1.5) runs along world +y at yaw 90
    assert corner[:3] == pytest.approx([100 - 7.5, 50 + 15, 3 + 12])
    T = up.placed_matrix(np.eye(4), [0, 0, 0], [-1, -1, 0], [1, 1, 1], [20, 10, 5], 1.0, turn=True)
    assert (T @ np.array([1.0, 0, 0, 1]))[:2] == pytest.approx([0, 10])


# --- city_library.plan on a synthetic plan ---------------------------------------------------

def _slot(sid, x, y, typology=None, district="a", stype=None, pieces=()):
    m = np.eye(4)
    m[:3, 3] = [x, y, 0.0]
    return {"id": sid, "type": stype or typology or "block", "typology": typology, "district": district, "category": "building",
            "matrix": m.tolist(), "bbox": [[x - 30, y - 30, 0.0], [x + 30, y + 30, 10.0]], "pieces": list(pieces)}


@pytest.fixture
def city(tmp_path, monkeypatch):
    """Ten row-house slots (two buildings each) built from one entry, a hall slot, a locked shot
    showing one row slot, a kit slot, and one unplaced asset with a procedural build that fits."""
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("u")
    slots = [_slot(f"row-{i}", i * 100.0, 0.0, "row", district="ab"[i % 2]) for i in range(10)]
    slots += [_slot("hall-1", 0.0, 300.0, "hall"), _slot("kit-1", 500.0, 300.0)]
    gb = {"slots": slots, "shots": []}
    glb_box(store.root / "3d/library/row/instances/a.glb", [-10, -7, 0], [10, 7, 9])
    glb_box(store.root / "3d/library/hall/procedural.glb", [-20, -15, 0], [20, 15, 12])
    glb_box(store.root / "3d/library/court/procedural.glb", [-11, -8, 0], [11, 8, 12])
    write_json(store.root / "3d/library/court/procedural/job.json", {})
    base = []
    for i in range(10):
        for j in (0, 1):
            base.append({"key": f"row_{i}_{j}", "entry": "row", "typology": "row", "typed": True, "slot": f"row-{i}",
                         "glb": "3d/library/row/instances/a.glb", "x": i * 100.0 + j * 25, "y": 0.0, "z": 0.0, "yaw": 0.0,
                         "w": 20.0, "d": 14.0})
    base.append({"key": "hall_hall-1", "entry": "hall", "slot": "hall-1", "glb": "3d/library/hall/procedural.glb",
                 "x": 0.0, "y": 300.0, "z": 0.0, "yaw": 0.0, "w": 40.0, "d": 30.0, "typed": False})
    kit = [{"key": "kitmod_0", "entry": "kitmod", "slot": "kit-1", "kit": True, "glb": "3d/library/row/instances/a.glb",
            "x": 500.0, "y": 300.0, "z": 0.0, "yaw": 0.0}]
    planned = {f"row-{i}": [("row", 0, 0, 0, 0), ("row", 25, 0, 0, 0)] for i in range(10)}
    monkeypatch.setattr(cl, "base_placements", lambda st, g: (copy.deepcopy(base), {"row": "j", "hall": "j"}, planned, {}))
    monkeypatch.setattr(kit_assembly, "placements", lambda *a: (copy.deepcopy(kit), []))
    monkeypatch.setattr(cl, "prop_placements", lambda *a: ([], {}, []))
    monkeypatch.setattr(cl, "settings", lambda st: SimpleNamespace(ground_rule="mean"))
    import asset_pipeline.assembly as assembly
    import asset_pipeline.ground as ground
    monkeypatch.setattr(assembly, "child_placements", lambda st, placed: ([], {}, []))
    monkeypatch.setattr(ground.Ground, "from_project", classmethod(lambda c, st: SimpleNamespace(under=lambda *a: 0.0)))
    from asset_pipeline import next_build
    monkeypatch.setattr(next_build, "gate", lambda st, ua: (lambda e: True))
    families = {"row": SimpleNamespace(id="row", family="housing", max_count=None),
                "hall": SimpleNamespace(id="hall", family="civic", max_count=None),
                "court": SimpleNamespace(id="court", family="housing", max_count=None)}
    monkeypatch.setattr(up, "_families", lambda st: families)
    monkeypatch.setattr(up, "locked_slots", lambda st: {"row-3"})
    from asset_pipeline import library_entries as le
    from asset_pipeline.stages import sw_library
    ents = {"row": {"kind": "building", "typology": "row"}, "hall": {"kind": "building", "typology": "hall"},
            "court": {"kind": "building", "typology": "court", "size_m": [22, 16, 12]}}
    monkeypatch.setattr(le, "_all_entries", lambda st: ents)
    monkeypatch.setattr(sw_library, "decisions", lambda st: {})
    monkeypatch.setattr(next_build, "_prop_entries", lambda st, g: set())
    monkeypatch.setattr(next_build, "_building_entries", lambda st: {"row", "hall"})
    from asset_pipeline.stages import sw_site
    monkeypatch.setattr(sw_site, "load_greybox", lambda st: gb)
    fitter = lambda: SimpleNamespace(bounds={}, built=[], build_s=0.0, layers=lambda *a: None)  # noqa: E731
    return SimpleNamespace(store=store, gb=gb, fitter=fitter)


def _plan(city, unplaced=None):
    return cl.plan(city.store, city.gb, True, None, fitter=city.fitter(), log=lambda *a: None, unplaced=unplaced)


def _by_key(pl):
    return {p["key"]: p for p in pl["placed"]}


def test_off_is_the_plan_exactly_and_on_swaps_only_the_places_it_takes(city):
    plain = _plan(city)
    assert plain["unplaced"] is None and {p["entry"] for p in plain["placed"]} == {"row", "hall", "kitmod"}
    on = _plan(city, {"avoid_locked": True, "seed": 0})
    took = [p for p in on["placed"] if p.get("unplaced")]
    assert took and {p["entry"] for p in took} == {"court"} and on["unplaced"]["placed"] == len(took)
    assert len(took) == max(1, round(up.REPEAT_SHARE * 18))         # the 18 row places outside the locked slot fit
    assert all(p["slot"] != "row-3" for p in took)                  # never in a slot a locked frame shows
    assert all(p["unplaced"]["from"] == "row" and p.get("typed") for p in took)
    A, B = _by_key(plain), _by_key(on)
    assert set(A) == set(B)                                         # the same keys: actors stay on their slots
    changed = {k for k in A if A[k]["glb"] != B[k]["glb"]}
    assert changed == {p["key"] for p in took}                      # nothing else moved or changed
    assert on["replaced"] == plain["replaced"] and on["entries"].keys() >= {"court"}
    p = took[0]
    f = p["foot"]                                                   # the court (22 x 16) stands on the row's footprint
    assert f["w"] == pytest.approx(22 * p["unplaced"]["k"]) and abs(f["x"] - A[p["key"]]["x"]) < 1e-6
    off = _plan(city)
    assert _by_key(off) == A                                        # off again: the plan's own builds everywhere


def test_avoid_locked_off_may_use_the_locked_slot_and_rebuilds_are_stable(city):
    a = _plan(city, {"avoid_locked": True, "seed": 0})
    b = _plan(city, {"avoid_locked": True, "seed": 0})
    keys = lambda pl: sorted(p["key"] for p in pl["placed"] if p.get("unplaced"))  # noqa: E731
    assert keys(a) == keys(b)
    seen = set()
    for seed in range(20):
        seen |= {p["slot"] for p in _plan(city, {"avoid_locked": False, "seed": seed})["placed"] if p.get("unplaced")}
    assert "row-3" in seen


def test_a_block_that_stays_greybox_gives_no_places(city, monkeypatch):
    base = cl.base_placements(city.store, city.gb)[0]
    planned = {f"row-{i}": 2 for i in range(10)} | {"row-0": 3}             # row-0 misses a build: it stays greybox
    took = {p["slot"] for p in up.places(city.store, city.gb, base, lambda e: True, True, planned)}
    assert "row-0" not in took and "row-1" in took


def test_kit_modules_halls_and_attached_parents_are_never_swapped(city, monkeypatch):
    from asset_pipeline import next_build
    on = _plan(city, {"avoid_locked": True, "seed": 0})
    assert not any(p.get("unplaced") for p in on["placed"] if p.get("kit") or p["slot"] == "hall-1")
    monkeypatch.setattr(next_build, "_relations", lambda st, dec: {"variant": {}, "attached": {"statue": "row"}})
    assert not any(p.get("unplaced") for p in _plan(city, {"avoid_locked": True, "seed": 0})["placed"])


def test_the_summary_lists_each_asset_its_count_and_the_slots_it_takes(city):
    rep = up.plan(city.store, city.gb, cl.base_placements(city.store, city.gb)[0], lambda e: True, None, True, 0)
    s = up.summary(rep)
    assert s["swapped"] == sum(a["count"] for a in s["assets"]) and s["open"] == 0
    a = s["assets"][0]
    assert a["entry"] == "court" and a["from"] == ["row"] and set(a["slots"]) <= {f"row-{i}" for i in range(10)}
    assert s["lines"][0].startswith(f"court: {a['count']} ({a['count']} from row; slots ")
    assert len(s["lines"]) == 1

"""Height fit and vertical roles (vertical.py) in kit assembly and city placement: synthetic tall
slots and stacked masses for each builder type (perimeter, row, arcade kit, podium_tower). The
builds are the generators' parts (pure Python); a stand-in for Blender writes each GLB's bounds."""
import math

import numpy as np
import pytest

from asset_pipeline import city_library as cl, kit_assembly as ka, placement_check, vertical
from asset_pipeline.building_parts import facade, merged, FACADE, top_of
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.schema import UnrealSettings
from asset_pipeline.stages import sw_library, sw_site

BRIEFS = {
    "test-perimeter": {"form": "perimeter", "dims": [40.0, 40.0, 17.0], "params": {"storey_h": 3.4},
                       "features": {"street": {"ground": {"kind": "shop"}}, "street_garden": {"depth": 3.0, "h": 1.2, "gap": 6.0}}},
    "test-row": {"form": "row", "dims": [24.0, 12.0, 9.6], "params": {"storey_h": 3.2},
                 "features": {"roof_box": {"w": 2.6, "d": 3.0, "h": 2.6}, "front_garden": {"depth": 2.0, "h": 1.0},
                              "house_types": [{}, {"ground": {"kind": "shop"}, "roof": {"pergola": True}}]}},
    "test-tower": {"form": "podium_tower", "dims": [30.0, 24.0, 40.0], "params": {"storey_h": 3.4}, "features": {}},
    "my-block-bay": {"form": "arcade", "dims": [4.5, 16.0, 20.4], "params": {},
                     "features": {"faces": ["front"], "side": {"ground": {"kind": "solid"}},
                                  "attic": {"storey_h": 3.2, "setback": 2.5, "side_setback": 0.0}}},
    "my-block-turn": {"form": "arcade", "dims": [16.0, 16.0, 20.4], "params": {},
                      "features": {"faces": ["front", "left"], "attic": {"storey_h": 3.2, "setback": 2.5, "side_setback": 2.5}}},
}
GROUND_VEG = {"front_garden", "court_bed", "garden_strip"}


def _z(p) -> tuple[float, float]:
    dz = (p.get("at") or [0, 0, 0])[2]
    if p["kind"] in ("box", "zone"):
        return p["z0"] + dz, p["z1"] + dz
    if p["kind"] == "prism":
        zs = [z for _, z in p["xz"]]
        return min(zs) + dz, max(zs) + dz
    return top_of(p), top_of(p)


def _walls(parts):
    return max(top_of(p) for p in parts if p.get("mat") == "roof")


def ground_parts(parts):
    """The ground storey's parts: ground-material walls at the base (doors, shopfronts, arcades,
    the podium) and the gardens in front of it."""
    return [p for p in parts if (p.get("mat") == "base" and _z(p)[0] < 0.3) or p.get("veg") in GROUND_VEG]


def roof_features(parts):
    """What stands above the roof: parapets, cornices, attics, pergolas, crowns, planters."""
    w = _walls(parts)
    return [p for p in parts if math.isfinite(top_of(p)) and top_of(p) > w + 0.05]


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    ents = [{"id": "my-block", "tier": "A", "kind": "building", "typology": "block", "part": "block", "material": None,
             "instances": 1, "districts": {}, "district": "core", "mood": [], "flags": [], "best": None, "share": 0.0,
             "description_auto": "a block", "description": None, "size_m": [40, 24, 10], "route_auto": "procedural",
             "slot_ids": ["blk-1"]}]
    write_json(sw_site.site_dir(store) / "library.json", {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [],
                                                          "checked": True, "entries": ents, "unseen": [], "surfaces": {}})
    sw_library.decide(store, "my-block", "kit", pieces={
        "bay": {"description": "one arched bay", "size": [4.5, 16, 20.4]},
        "turn": {"description": "the arcade turning a corner", "size": [16, 16, 20.4]}})
    for a, b in BRIEFS.items():
        write_json(store.root / "library" / "kits" / f"{a}.json", {"kind": "building", **b})
    return store


class Fake:
    """The Blender build's stand-in: a GLB file and its bounds (from the parts), every build logged."""

    def __init__(self, store, bounds):
        self.store, self.bounds, self.calls = store, bounds, []
        self.fitter = vertical.Fitter(store, build=self, log=lambda *a: None)
        for a in BRIEFS:                                   # the entries' own builds
            d = store.root / "3d" / "library" / a
            (d / "procedural").mkdir(parents=True, exist_ok=True)
            (d / "procedural" / "job.json").write_text("{}")
            self(store, a, d / "procedural.glb", BRIEFS[a]["dims"], "single", None, log=False)

    def __call__(self, store, asset, out, dims, role, maps, log=True):
        parts = self.fitter.parts(asset, role, dims[2])
        zs = [_z(p) for p in parts]
        w, d = dims[:2]
        out.write_bytes(b"glb")
        self.bounds[out.relative_to(store.root).as_posix()] = [[-w / 2, -d / 2, min(z for z, _ in zs)],
                                                               [w / 2, d / 2, max(z for _, z in zs if math.isfinite(z))]]
        if log:
            self.calls.append((asset, role, dims[2]))


class Flat:
    def under(self, x, y, w, d, yaw, rule):
        return 0.0


def _check(placed, bounds, slots):
    """The placement check on actors standing where the plan puts them."""
    rows = cl.actor_rows(placed, slots)
    man = {"slots": [{"id": sid, "library": rs} for sid, rs in rows.items()],
           "library_assets": [{"id": cl.asset_id(p.get("mesh") or p["key"]), "bounds": bounds[p["glb"]]} for p in placed]}
    actors = [{"key": r["key"], **r["world"]} for rs in rows.values() for r in rs]
    return placement_check.check(man, actors, {}, {}, Flat(), UnrealSettings())


def _slot(sid, masses):
    """A slot of boxes (x, y, z, w, d, h) and the mesh index giving their bounds."""
    pieces, index = [], {}
    for i, (x, y, z, w, d, h) in enumerate(masses):
        M = np.eye(4)
        M[:3, 3] = [x, y, z]
        pieces.append({"mesh": f"{sid}#{i}", "piece": "mass", "matrix_local": M.tolist()})
        index[f"{sid}#{i}"] = {"bounds": [[-w / 2, -d / 2, 0.0], [w / 2, d / 2, h]]}
    xs = [(x - w / 2, x + w / 2) for x, _, _, w, _, _ in masses]
    ys = [(y - d / 2, y + d / 2) for _, y, _, _, d, _ in masses]
    return ({"id": sid, "typology": None, "matrix": np.eye(4).tolist(), "pieces": pieces,
             "bbox": [[min(a for a, _ in xs), min(a for a, _ in ys), 0], [max(b for _, b in xs), max(b for _, b in ys), 99]]},
            index)


# --- the builders' roles ---------------------------------------------------------------------

@pytest.mark.parametrize("asset", list(BRIEFS))
def test_each_role_drops_the_ground_floor_and_roof_features_it_should(proj, asset):
    F = vertical.Fitter(proj, build=lambda *a: None)
    h = BRIEFS[asset]["dims"][2]
    single = F.parts(asset, "single", h)
    assert ground_parts(single) and roof_features(single)            # the whole building has both
    for role in ("bottom", "middle", "top"):
        parts = F.parts(asset, role, h)
        ground, roof = vertical.FLAGS[role]
        assert bool(ground_parts(parts)) == ground, role
        assert bool(roof_features(parts)) == roof, role


def test_an_upper_ground_storey_is_like_the_storeys_above_it():
    F = merged(FACADE, {"storeys": 3, "ground_h": 3.4, "ground": {"kind": "upper"}})
    parts = facade(12.0, F)
    plain = facade(12.0, merged(F, {"ground": {"kind": "plain"}}))
    assert not [p for p in parts if p.get("mat") == "base"] and [p for p in plain if p.get("mat") == "base"]
    sills = sorted({round(_z(p)[0], 2) for p in parts if p["kind"] == "box" and p.get("mat") == "trim" and _z(p)[1] - _z(p)[0] < 0.1})
    assert len(sills) == 3                              # a window sill on every storey, the first included


# --- height fit before stacking --------------------------------------------------------------

@pytest.mark.parametrize("asset,H", [("test-perimeter", 30.0), ("test-row", 16.0), ("test-tower", 70.0),
                                     ("my-block-bay", 40.0), ("my-block-turn", 12.0)])
def test_a_taller_or_lower_slot_gets_one_build_with_the_storeys_that_fill_it(proj, asset, H):
    fake = Fake(proj, {})
    plan = fake.fitter.layers(asset, H)
    st = vertical.storey_h(fake.fitter.spec(asset)["form"], fake.fitter.spec(asset)["f"], fake.fitter.spec(asset)["p"])
    assert [ly["role"] for ly in plan] == ["single"]
    assert plan[0]["walls"] - st <= H <= plan[0]["top"] + st          # within a storey (a crown counts)
    g = fake.fitter.glb(asset, plan[0])
    assert g.startswith(f"3d/library/{asset}/heights/single-") and len(fake.calls) == 1
    assert fake.fitter.glb(asset, fake.fitter.layers(asset, H + 0.3)[0]) == g     # cached per storey count
    assert len(fake.calls) == 1
    assert fake.fitter.glb(asset, fake.fitter.layers(asset, BRIEFS[asset]["dims"][2])[0]).endswith("procedural.glb")


@pytest.mark.parametrize("asset,H", [("test-perimeter", 80.0), ("test-row", 40.0), ("test-tower", 260.0),
                                     ("my-block-bay", 110.0)])
def test_beyond_a_builders_storeys_layers_stack_bottom_middle_top(proj, asset, H):
    F = vertical.Fitter(proj, build=lambda *a: None)
    plan = F.layers(asset, H)
    roles = [ly["role"] for ly in plan]
    assert len(plan) >= 2 and roles[0] == "bottom" and roles[-1] == "top" and set(roles[1:-1]) <= {"middle"}
    s = F.spec(asset)
    st = vertical.storey_h(s["form"], s["f"], s["p"])
    assert abs(sum(ly["walls"] for ly in plan) - H) <= st
    for i, ly in enumerate(plan):
        parts = F.parts(asset, ly["role"], ly["h"])
        assert bool(ground_parts(parts)) == (i == 0)                   # no ground floor above level 0
        assert bool(roof_features(parts)) == (i == len(plan) - 1)       # no roof features under a layer


def test_roles_compose_within_a_tier():
    assert vertical.compose("single", 1) == ["single"]
    assert vertical.compose("single", 3) == ["bottom", "middle", "top"]
    assert vertical.compose("bottom", 2) == ["bottom", "middle"] and vertical.compose("top", 2) == ["middle", "top"]
    assert vertical.compose("middle", 2) == ["middle", "middle"]
    assert vertical.stack_lifts([([0, 0, -0.2], [0, 0, 10]), ([0, 0, 0], [0, 0, 6]), ([0, 0, 0], [0, 0, 3])]) == [0.0, 10.0, 16.0]


# --- kit slots: tall, very tall, stacked masses ----------------------------------------------

def _kit_plan(proj, masses):
    bounds = {}
    fake = Fake(proj, bounds)
    s, index = _slot("blk-1", masses)
    pl, reps = ka.placements(proj, {"slots": [s]}, index, bounds, fake.fitter)
    cl.ground_all(proj, pl, Flat(), "min", bounds)
    return pl, reps, bounds, fake, s


def _stands_ok(pl, bounds, s):
    res = _check(pl, bounds, {"blk-1": s})
    assert not {"ground_floor_up", "roof_under", "floating", "sunk"} & set(res["issues"]), res["issues"]


def test_a_tall_kit_slot_takes_height_variants_not_stacked_copies(proj):
    pl, reps, bounds, fake, s = _kit_plan(proj, [(20, 12, 0, 40, 24, 44.0)])
    blk = reps[0]["blocks"][0]
    assert blk["courses"] == 1 and blk["fit"]["stacked"] == 0 and blk["fit"]["fitted"] == blk["modules"]
    assert {p["course"] for p in pl} == {0} and {p["vrole"] for p in pl} == {"single"}
    for p in pl:
        assert abs(_top_walls(fake, p) - 44.0) <= 3.4 and "/heights/" in p["glb"]
    assert len(fake.calls) == 2                       # one variant per module, shared by the whole ring
    _stands_ok(pl, bounds, s)


def _top_walls(fake, p):
    """The roof height of a placed height variant (its index entry)."""
    from asset_pipeline.project import read_json
    d = fake.store.root / p["glb"].rsplit("/", 1)[0]
    return read_json(d / "index.json")[p["glb"].rsplit("/", 1)[1][:-4]]["walls"]


def test_a_very_tall_kit_slot_stacks_layers_with_roles(proj):
    pl, reps, bounds, fake, s = _kit_plan(proj, [(20, 12, 0, 40, 24, 110.0)])
    cols = {}
    for p in pl:
        cols.setdefault(p["key"].rsplit("_", 1)[0], []).append(p)
    for col in cols.values():
        col.sort(key=lambda p: p["course"])
        assert [p["vrole"] for p in col][0] == "bottom" and col[-1]["vrole"] == "top"
        assert {p["vrole"] for p in col[1:-1]} <= {"middle"} and len(col) >= 2
        assert all(p["base"] == col[0]["key"] for p in col[1:])
    _stands_ok(pl, bounds, s)
    assert any("layers" in line for line in ka.report_lines(reps))


def test_stacked_masses_make_tiers_that_read_as_one_building(proj):
    # a block's lower mass with a setback mass on it
    masses = [(20, 12, 0, 40, 24, 12.0), (20, 12, 12.0, 24, 16, 20.0)]
    blocks = ka.outline(ka.slot_boxes(*_slot("blk-1", masses)))
    assert [(b["tier"], b["role"]) for b in blocks] == [(0, "bottom"), (1, "top")]
    assert blocks[1]["z0"] == pytest.approx(12.0) and blocks[1]["height"] == pytest.approx(20.0)
    pl, reps, bounds, fake, s = _kit_plan(proj, masses)
    low = [p for p in pl if p["tier"] == 0]
    high = [p for p in pl if p["tier"] == 1]
    assert low and high
    assert {p["vrole"] for p in low} == {"bottom"} and {p["vrole"] for p in high} == {"top"}
    keys = {p["key"] for p in low}
    assert all(p["base"] in keys for p in high)       # each upper module stands on the column under it
    _stands_ok(pl, bounds, s)
    # the same greybox building split in a ground storey and its upper storeys (same footprint): one tier
    same = ka.outline(ka.slot_boxes(*_slot("blk-1", [(20, 12, 0, 40, 24, 6.0), (20, 12, 6.0, 40, 26, 20.0)])))
    assert [(b["tier"], b["role"]) for b in same] == [(0, "single")] and same[0]["height"] == pytest.approx(26.0)


def test_modules_with_no_building_brief_stack_as_copies_and_the_check_flags_them(proj):
    bounds = {}
    fake = Fake(proj, bounds)
    for a in ("my-block-bay", "my-block-turn"):       # a TRELLIS-like module: no brief to rebuild from
        (proj.root / "library" / "kits" / f"{a}.json").unlink()
    fake.fitter = vertical.Fitter(proj, build=fake)
    s, index = _slot("blk-1", [(20, 12, 0, 40, 24, 44.0)])
    pl, reps = ka.placements(proj, {"slots": [s]}, index, bounds, fake.fitter)
    cl.ground_all(proj, pl, Flat(), "min", bounds)
    assert max(p["course"] for p in pl) >= 1 and {p["vrole"] for p in pl} == {None}
    res = _check(pl, bounds, {"blk-1": s})
    assert res["issues"].get("ground_floor_up") and res["issues"].get("roof_under")
    assert any("copies" in line for line in ka.report_lines(reps))


# --- single-building slots: height fit after the variant split -------------------------------

@pytest.mark.parametrize("asset,H,layers", [("test-perimeter", 30.0, 1), ("test-perimeter", 75.0, 2),
                                            ("test-row", 15.0, 1), ("test-row", 30.0, 2),
                                            ("test-tower", 80.0, 1), ("test-tower", 250.0, 2)])
def test_a_single_building_slot_fits_its_height_or_stacks_with_roles(proj, asset, H, layers):
    bounds = {}
    fake = Fake(proj, bounds)
    w, d, _ = BRIEFS[asset]["dims"]
    s, index = _slot("s-1", [(0, 0, 0, w, d, H)])
    p = {"key": f"{asset}_s_1".replace("-", "m"), "entry": asset, "glb": f"3d/library/{asset}/procedural.glb",
         "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "w": w, "d": d, "slot": "s-1", "typed": False}
    out = cl.fit_heights(proj, {"slots": [s]}, index, [p], fake.fitter, bounds)
    assert len(out) >= layers and out[0]["key"] == p["key"]          # the variant split's key stays
    assert all(q["layer_of"] == p["key"] and q["base"] == p["key"] for q in out[1:])
    roles = [q["vrole"] for q in out]
    assert roles == (["single"] if len(out) == 1 else ["bottom"] + ["middle"] * (len(out) - 2) + ["top"])
    placed, replaced, held = cl.choose(out + [p | {"key": "other", "entry": "x"}], {}, lambda e: e != "x")
    assert [q["key"] for q in placed] == [q["key"] for q in out] and replaced == {"s-1": "whole"}
    cl.ground_all(proj, placed, Flat(), "min", bounds)
    res = _check(placed, bounds, {"s-1": s})
    assert not {"ground_floor_up", "roof_under", "floating"} & set(res["issues"]), res["issues"]


def test_a_variant_placement_fits_with_its_own_brief_and_keeps_its_key(proj):
    write_json(proj.root / "library" / "kits" / "test-row-v.json",
               {"kind": "building", **BRIEFS["test-row"], "params": {"storey_h": 2.8}})
    bounds = {}
    fake = Fake(proj, bounds)
    s, index = _slot("s-1", [(0, 0, 0, 24, 12, 15.0)])
    key = "testmrow_s_1"
    base = {"key": key, "glb": "3d/library/test-row/procedural.glb", "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,
            "w": 24.0, "d": 12.0, "slot": "s-1", "typed": False}
    a = cl.fit_heights(proj, {"slots": [s]}, index, [base | {"entry": "test-row"}], fake.fitter, bounds)
    b = cl.fit_heights(proj, {"slots": [s]}, index, [base | {"entry": "test-row-v", "variant_of": "test-row"}],
                       fake.fitter, bounds)
    assert a[0]["key"] == b[0]["key"] == key                        # the variant split's key: the same
    assert a[0]["glb"].startswith("3d/library/test-row/") and b[0]["glb"].startswith("3d/library/test-row-v/")
    assert bounds[a[0]["glb"]][1][2] != bounds[b[0]["glb"]][1][2]               # built from its own brief


def test_a_dry_fitter_lists_the_variants_a_build_would_make_and_builds_nothing(proj):
    """The build estimate's count (ue_build.queued_work): the same variants, no Blender, no files."""
    s, index = _slot("blk-1", [(20, 12, 0, 40, 24, 44.0)])
    dry = vertical.Fitter(proj, build=lambda *a: pytest.fail("a dry fitter built"), log=lambda *a: None, dry=True)
    Fake(proj, dry.bounds)                               # the entries' own builds exist; no variants yet
    pl, _ = ka.placements(proj, {"slots": [s]}, index, dry.bounds, dry)
    assert len(dry.missing) == 2 and not dry.built and all("/heights/" in g for g in dry.missing)
    assert not any((proj.root / g).exists() for g in dry.missing)
    assert {p["glb"] for p in pl} == set(dry.missing) and all(dry.bounds[g][1][2] > 40 for g in dry.missing)
    bounds: dict = {}
    fake = Fake(proj, bounds)                            # the real build makes those, then none are missing
    pl2, _ = ka.placements(proj, {"slots": [s]}, index, bounds, fake.fitter)
    assert {p["glb"] for p in pl2} == set(dry.missing) and len(fake.fitter.built) == 2 and fake.fitter.build_s >= 0
    again = vertical.Fitter(proj, log=lambda *a: None, dry=True)
    ka.placements(proj, {"slots": [s]}, index, bounds, again)
    assert again.missing == []

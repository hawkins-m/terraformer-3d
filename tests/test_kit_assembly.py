"""Kit assembly (kit_assembly.py): corner and bay modules around synthetic block footprints, roles,
and the kit slots in the city manifest's plan. Pure geometry plus a synthetic project; no Blender."""
import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from asset_pipeline import city_library as cl, kit_assembly as ka, placement_check, ue_coords
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_library, sw_site

BAY = ([-2.0, -1.5, 0.0], [2.0, 1.5, 5.0])          # 4 m wide, 3 m deep, 5 m high
CORNER = ([-1.5, -1.5, 0.0], [1.5, 1.5, 5.0])        # 3 x 3 m
FILLER = ([-0.5, -1.5, 0.0], [0.5, 1.5, 5.0])        # 1 m
END = ([-0.75, -1.5, 0.0], [0.75, 1.5, 5.0])         # 1.5 m
MODS = {"bay": BAY, "corner": CORNER}
RECT = [(0, 0), (40, 0), (40, 24), (0, 24)]
L_SHAPE = [(0, 0), (36, 0), (36, 14), (17, 14), (17, 30), (0, 30)]
OVERLAP_M2 = 0.01


def _feet(placed, mods, course=0):
    return [Polygon(ka.module_outline(p["matrix"], *mods[p["role"]])) for p in placed if p["course"] == course]


def _check_block(pts, mods, placed, rep):
    poly = Polygon(pts)
    feet = _feet(placed, mods)
    # corners at the outer corners, their two facades on the block's outline facing out
    corners = [p for p in placed if p["role"] == "corner" and p["course"] == 0]
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
    convex = [v for i, v in enumerate(pts) if turn(pts[i - 1], v, pts[(i + 1) % len(pts)]) > 0]
    got = sorted(tuple(np.round((p["matrix"] @ np.array([CORNER[0][0], CORNER[0][1], 0, 1]))[:2], 6)) for p in corners)
    assert got == sorted(tuple(map(float, v)) for v in convex)
    # every module inside the block, its front facing out
    for p, f in zip([p for p in placed if p["course"] == 0], feet):
        assert poly.buffer(1e-6).contains(f)
        lo, hi = mods[p["role"]]
        front = p["matrix"] @ np.array([(lo[0] + hi[0]) / 2, lo[1], 0, 1])
        out = p["matrix"] @ np.array([(lo[0] + hi[0]) / 2, lo[1] - 0.5, 0, 1])
        assert poly.exterior.distance(Point(front[:2])) < 1e-6 and not poly.contains(Point(out[:2]))
    # no overlaps beyond tolerance
    for i, a in enumerate(feet):
        for b in feet[i + 1:]:
            assert a.intersection(b).area < OVERLAP_M2
    # edges fully covered (but for reported gaps)
    left = LineString(list(poly.exterior.coords)).difference(unary_union(feet).buffer(1e-4))
    assert left.length <= sum(g["m"] for g in rep["gaps"]) + 1e-3
    return feet


@pytest.mark.parametrize("pts", [RECT, L_SHAPE, L_SHAPE[::-1]], ids=["rectangle", "L", "L-clockwise"])
def test_corners_at_corners_no_overlaps_edges_covered(pts):
    placed, rep = ka.assemble(pts, MODS, height=5.0)
    pts = L_SHAPE if len(pts) == 6 else pts
    _check_block(pts, MODS, placed, rep)
    assert rep["gaps"] == [] and rep["short_edges"] == [] and rep["courses"] == 1
    for p in placed:
        assert abs(p["k"] - 1) <= ka.SCALE_TOL + 1e-9                   # bays scale at most 10%
    assert rep["inner_corners"] == (1 if len(pts) == 6 else 0)


def test_an_l_inner_corner_takes_end_modules_when_the_kit_has_them():
    mods = MODS | {"end": END}
    placed, rep = ka.assemble(L_SHAPE, mods, height=5.0)
    assert sum(p["role"] == "end" for p in placed) == 2                  # one per run meeting there
    _check_block(L_SHAPE, mods, placed, rep)


def test_bays_scale_to_close_an_edge_then_fillers_then_a_reported_gap():
    f = ka.fill(34.0, 4.0)                    # 8.5 bays: 8 at 1.0625 (or 9 at 0.944): within 10%
    assert f["bays"] * 4.0 * f["k"] == pytest.approx(34.0) and abs(f["k"] - 1) <= 0.1 and f["gap"] == 0
    f = ka.fill(6.0, 4.0, 1.0)                # 1.5 bays: 1 bay at 1.1 (4.4 m), the 1.6 m left: 1 filler at 1.1 + gap
    assert (f["bays"], f["fillers"]) == (1, 1) and f["gap"] == pytest.approx(6.0 - 4.4 - 1.1)
    f = ka.fill(6.4, 4.0, 1.0)                # 1 bay at 1.1, then 2 m: 2 fillers, closed
    assert (f["bays"], f["fillers"]) == (1, 2) and f["gap"] == pytest.approx(0.0, abs=1e-9)
    f = ka.fill(6.0, 4.0)                     # no filler: the gap is left and reported
    assert f["bays"] == 1 and f["gap"] == pytest.approx(1.6)
    # a block whose edges need fillers stays covered with them
    pts = [(0, 0), (12.6, 0), (12.6, 12.6), (0, 12.6)]                  # 6.6 m runs between corners
    mods = MODS | {"filler": FILLER}
    placed, rep = ka.assemble(pts, mods, height=5.0)
    assert any(p["role"] == "filler" for p in placed)
    _check_block(pts, mods, placed, rep)
    placed, rep = ka.assemble(pts, MODS, height=5.0)                    # without one: gaps, reported
    assert len(rep["gaps"]) == 4 and all(g["m"] > 0 for g in rep["gaps"])
    _check_block(pts, MODS, placed, rep)
    assert ka.report_lines([{"slot": "s", "kit": "k", "unbuilt": [], "blocks": [rep]}])[0].startswith("kit k in s: gaps")


def test_a_tall_slot_stacks_courses():
    placed, rep = ka.assemble(RECT, MODS, height=14.0, z0=2.0)
    assert rep["courses"] == 3
    z = sorted({round(p["matrix"][2, 3], 6) for p in placed})
    assert z == [2.0, 7.0, 12.0]
    assert len(_feet(placed, MODS, 0)) == len(_feet(placed, MODS, 2))


def test_the_outline_of_a_slots_masses_ignores_low_and_thin_pieces():
    def box(x, y, w, d, h, yaw=0.0):
        a = math.radians(yaw)
        W = np.eye(4)
        W[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
        W[:3, 3] = [x, y, 1.0]
        return W, [-w / 2, -d / 2, 0.0], [w / 2, d / 2, h]
    boxes = [box(18, 7, 36, 14, 12), box(8.5, 22, 17, 16, 12),          # an L made of two masses
             box(20, -2, 1, 1, 6),                                       # a pier
             box(18, 15, 40, 40, 0.2)]                                   # paving
    blocks = ka.outline(boxes)
    assert len(blocks) == 1 and blocks[0]["z0"] == 1.0 and blocks[0]["height"] == 12.0
    assert Polygon(blocks[0]["pts"]).symmetric_difference(Polygon(L_SHAPE)).area < 1e-6
    assert len(blocks[0]["pts"]) == 6
    two = ka.outline([box(0, 0, 20, 20, 10), box(40, 0, 20, 20, 10, 30)])   # two blocks, one turned
    assert len(two) == 2
    placed, rep = ka.assemble(two[1]["pts"], MODS, two[1]["height"])
    _check_block([tuple(p) for p in ka._clean(two[1]["pts"])], MODS, placed, rep)


@pytest.mark.parametrize("asset,piece,brief,role", [
    ("my-block-corner", "corner", None, "corner"), ("my-block-bay", "bay", None, "bay"),
    ("my-block-a", "a", {"form": "arcade", "features": {"faces": ["front", "left"]}}, "corner"),
    ("my-block-b", "b", {"form": "arcade", "features": {"faces": ["front"]}}, "bay"),
    ("my-block-end", None, None, "end"), ("my-block-filler", None, None, "filler"),
    ("my-block", None, {"form": "cube"}, None)])
def test_roles_detected_from_the_id_and_the_brief(asset, piece, brief, role):
    assert ka.detect_role(asset, piece, brief) == role


def test_a_kit_slot_is_placed_whole_only_when_every_module_is_approved():
    kit = [{"key": f"k{i}", "entry": e, "slot": "blk", "kit": "my-block", "typed": False}
           for i, e in enumerate(["my-block-bay", "my-block-corner", "my-block-bay"])]
    other = {"key": "rep", "entry": "my-block", "slot": "blk", "typed": False}
    placed, replaced, held = cl.choose(kit + [other], {}, lambda e: True)
    assert [p["key"] for p in placed] == ["k0", "k1", "k2"] and replaced == {"blk": "whole"}
    assert held[0]["why"] == "a kit assembly stands in this slot"
    placed, replaced, held = cl.choose(kit + [other], {}, lambda e: e != "my-block-corner")
    assert [p["key"] for p in placed] == ["rep"]                      # the slot's other build, as before
    assert {h["why"] for h in held} == {"not approved: my-block-corner"}


class _FlatGround:
    def under(self, x, y, w, d, yaw, rule):
        return 3.0 + 0.01 * x


def test_stacked_modules_stand_on_their_grounded_base():
    W = ka.module_matrix((10, 0), (1, 0), BAY[0], 1.05, 0.0)
    base = {"key": "b0", "glb": "bay.glb", "matrix": W.tolist()}
    up = {"key": "b1", "glb": "bay.glb", "matrix": W.tolist(), "base": "b0", "lift": 5.0}
    cl.ground_all(None, [up, base], _FlatGround(), "min", {"bay.glb": [BAY[0], BAY[1]]})
    zb = base["matrix"][2][3]
    assert zb == pytest.approx(3.0 + 0.01 * base["foot"]["x"])
    assert up["matrix"][2][3] == pytest.approx(zb + 5.0)
    assert base["foot"]["w"] == pytest.approx(4.2) and base["foot"]["d"] == pytest.approx(3.0)   # scaled along x only


def test_the_placement_check_reads_a_bays_scale_along_x():
    W = ka.module_matrix((10, 5), (0, 1), BAY[0], 0.92, 1.0)
    t = ue_coords.to_ue(W)
    B = placement_check.ue_to_blender(t["loc"], t["rot"], t["scale"])
    assert np.allclose(B, W, atol=1e-4)
    assert cl.footprint_of(B, *BAY)["w"] == pytest.approx(4 * 0.92)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    ent = {"id": "my-block", "tier": "A", "kind": "building", "typology": "block", "part": "block", "material": None,
           "instances": 1, "districts": {}, "district": "core", "mood": [], "flags": [], "best": None, "share": 0.0,
           "description_auto": "a block", "description": None, "size_m": [40, 24, 10], "route_auto": "procedural",
           "slot_ids": ["blk-1"]}
    write_json(sw_site.site_dir(store) / "library.json", {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [],
                                                          "checked": True, "entries": [ent], "unseen": [], "surfaces": {}})
    sw_library.decide(store, "my-block", "kit", pieces={
        "bay": {"description": "one arched bay", "size": [4, 3, 5]},
        "turn": {"description": "the arcade turning a corner", "size": [3, 3, 5]}})
    return store


def test_roles_are_stored_and_kit_slots_get_placements(proj):
    mods = {m["asset"]: m for m in ka.modules(proj, "my-block")}
    assert mods["my-block-bay"]["role"] == "bay" and mods["my-block-turn"]["role"] == "corner"   # from its description
    ka.set_roles(proj, "my-block", {"my-block-turn": "none"})
    assert {m["asset"]: m["role"] for m in ka.modules(proj, "my-block")}["my-block-turn"] is None
    ka.set_roles(proj, "my-block", {"my-block-turn": "corner"})
    assert sw_library.decisions(proj)["my-block-turn"]["kit_role"] == "corner"
    with pytest.raises(ValueError):
        ka.set_roles(proj, "my-block", {"my-block-turn": "roof"})
    assert ka.kits(proj) == [{"kit": "my-block", "slots": ["blk-1"],
                              "modules": {"bay": ["my-block-bay"], "corner": ["my-block-turn"]}}]
    S = np.eye(4)
    S[:3, 3] = [100, 50, 0]
    gb = {"slots": [{"id": "blk-1", "typology": None, "matrix": S.tolist(), "bbox": [[80, 38, 0], [120, 62, 10]],
                     "pieces": [{"mesh": "mass#1", "piece": "mass", "matrix_local": np.eye(4).tolist()}]}]}
    index = {"mass#1": {"bounds": [[-20, -12, 0], [20, 12, 10]]}}
    bounds = {}
    pl, reps = ka.placements(proj, gb, index, bounds)
    assert pl == [] and reps[0]["unbuilt"] == ["my-block-bay", "my-block-turn"]   # not built yet: none
    for a, b in (("my-block-bay", BAY), ("my-block-turn", CORNER)):
        d = proj.root / "3d" / "library" / a
        (d / "procedural").mkdir(parents=True)
        (d / "procedural.glb").write_bytes(b"glb")
        (d / "procedural" / "job.json").write_text("{}")
        bounds[f"3d/library/{a}/procedural.glb"] = [b[0], b[1]]
    pl, reps = ka.placements(proj, gb, index, bounds)
    assert reps[0]["blocks"][0]["courses"] == 2 and reps[0]["blocks"][0]["gaps"] == []
    assert len({p["key"] for p in pl}) == len(pl)
    assert {p["mesh"] for p in pl} == {"kit_my-block-bay", "kit_my-block-turn"}
    assert sum(p["role"] == "corner" for p in pl) == 8
    by_key = {p["key"]: p for p in pl}
    for p in pl:
        if p["course"]:
            assert by_key[p["base"]]["course"] == 0 and p["lift"] == 5.0
    rows = cl.actor_rows([p | {"z": 0} for p in pl], {"blk-1": gb["slots"][0]})
    assert len(rows["blk-1"]) == len(pl)
    feet = [Polygon(ka.module_outline(np.asarray(p["matrix"]), *bounds[p["glb"]])) for p in pl if p["course"] == 0]
    block = Polygon([(80, 38), (120, 38), (120, 62), (80, 62)])
    assert all(block.buffer(1e-6).contains(f) for f in feet)
    assert LineString(list(block.exterior.coords)).difference(unary_union(feet).buffer(1e-4)).length < 1e-3


def test_manage_entry_reads_and_sets_kit_roles(proj):
    from fastapi.testclient import TestClient
    from asset_pipeline.jobs import JobQueue
    from asset_pipeline.ui import app as ui_app
    c = TestClient(ui_app.create_app(JobQueue()))
    r = c.get("/api/projects/p/library/entries/my-block/kit").json()
    assert r["roles"] == list(ka.ROLES) and [m["source"] for m in r["modules"]] == ["detected", "detected"]
    r = c.post("/api/projects/p/library/entries/my-block/kit", json={"roles": {"my-block-bay": "filler"}}).json()
    assert {m["asset"]: (m["role"], m["source"]) for m in r["modules"]}["my-block-bay"] == ("filler", "yours")
    assert c.post("/api/projects/p/library/entries/my-block/kit", json={"roles": {"other": "bay"}}).status_code == 404
    assert c.get("/api/projects/p/library/entries/nope/kit").status_code == 404

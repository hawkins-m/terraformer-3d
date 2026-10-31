"""Merge ground surfaces (ground_merge.py): off / auto (metro only) / on per project; the draped street,
road and field surfaces go into ~1 km tiles, one mesh per tile and surface, and nothing else moves."""
import json

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import ground_merge
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import UnrealSettings
from asset_pipeline.stages import s7_export, sw_site
from asset_pipeline.ui import app as ui_app
from test_city import small_city


def _m(x, y, z=0.0):
    return [[1.0, 0, 0, x], [0, 1.0, 0, y], [0, 0, 1.0, z], [0, 0, 0, 1.0]]


def _slot(sid, typ, x, y, pieces):
    return {"id": sid, "type": typ, "category": "terrain", "kit": None, "district": None, "material": None,
            "matrix": _m(x, y), "bbox": [[x - 5, y - 5, 0], [x + 5, y + 5, 1]],
            "pieces": [{"name": f"{sid}.{p}.{i:03d}", "piece": p, "mesh": mesh or f"{sid}.{p}", "matrix_local": _m(0, 0)}
                       for i, (p, mesh) in enumerate(pieces)]}


GB = {"shots": [], "slots": [
    _slot("street-x0y0", "street", 100, 100, [("street", None)]),
    _slot("street-x1y0", "street", 500, 100, [("street", None)]),
    _slot("way-av1", "avenue", 300, 300, [("avenue", None)]),
    _slot("way-hw", "highway", 1500, 200, [("highway", None)]),
    _slot("farmland-x0y0", "farmland", 700, 600, [("field:crop", None), ("field:fallow", None)]),
    _slot("park-x0y0", "park", 200, 200, [("lawn", None), ("tree:pine-crown", "tree:pine-crown#abc")]),
    _slot("stair-1", "stair", 50, 50, [("stair", None), ("stair-tread", "stair-tread#123")]),
    _slot("houses-x0y0", "houses", 300, 100, [("houses:box", "box#1")]),
]}
INDEX = {m: {"id": m.replace(".", "-").replace(":", "-").replace("#", "-"), "glb": f"{m}.glb", "faces": 2,
             "bounds": [[0, 0, 0], [1, 1, 1]]}
         for m in ["street-x0y0.street", "street-x1y0.street", "way-av1.avenue", "way-hw.highway",
                   "farmland-x0y0.field:crop", "farmland-x0y0.field:fallow", "park-x0y0.lawn", "tree:pine-crown#abc",
                   "stair-1.stair", "stair-tread#123", "box#1"]}


def test_streets_roads_and_fields_go_into_tiles_by_surface():
    p = ground_merge.plan(GB)
    t = p["tiles"]
    assert set(t) == {"ground.street.x0y0", "ground.avenue.x0y0", "ground.highway.x1y0", "ground.field.x0y0"}
    assert len(t["ground.street.x0y0"]["parts"]) == 2                     # two street slots, one mesh
    assert len(t["ground.field.x0y0"]["parts"]) == 2                      # crop and fallow, one mesh
    assert t["ground.highway.x1y0"]["origin"] == [1500.0, 500.0, 0.0] and t["ground.avenue.x0y0"]["type"] == "avenue"
    assert set(p["taken"]) == {"street-x0y0", "street-x1y0", "way-av1", "way-hw", "farmland-x0y0"}   # never parks, stairs, buildings


def test_the_manifest_swaps_merged_slots_for_tiles_and_keeps_the_rest():
    merge = ground_merge.plan(GB)
    index = dict(INDEX) | {n: {"id": n.replace(".", "-"), "glb": f"{n}.glb", "faces": 9, "bounds": [[0, 0, 0], [1, 1, 1]]}
                           for n in merge["tiles"]}
    plain = s7_export.manifest(GB, INDEX)
    m = s7_export.manifest(GB, index, skip=merge["taken"])
    r = ground_merge.apply(m, GB, index, merge)
    assert r == {"tiles": 4, "slots_merged": 5, "pieces": 6}
    ids = {s["id"] for s in m["slots"]}
    assert ids == {"park-x0y0", "stair-1", "houses-x0y0", "ground-street-x0y0", "ground-avenue-x0y0",
                   "ground-highway-x1y0", "ground-field-x0y0"}
    kept = {s["id"]: s for s in plain["slots"]}
    for s in m["slots"]:
        if not s["id"].startswith("ground-"):
            assert s == kept[s["id"]]                                     # untouched
    av = next(s for s in m["slots"] if s["id"] == "ground-avenue-x0y0")
    assert av["type"] == "avenue" and av["category"] == "terrain" and "ap:merged=ground" in av["tags"]
    tint = {a["id"]: a["tint"] for a in m["assets"]}
    assert tint["ground-avenue-x0y0"] == s7_export._tint("avenue", "avenue")   # the same look
    assert not {"street-x0y0-street", "farmland-x0y0-field-crop"} & {a["id"] for a in m["assets"]}


def test_auto_merges_a_metro_city_only_and_the_setting_stays_out_of_a_default_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    assert ground_merge.mode(store) == "auto" and not ground_merge.wanted(store)        # no layout
    L = small_city()
    assert not ground_merge.wanted(store, L)
    L.city.size = "metro"
    assert ground_merge.wanted(store, L)
    L.city.size = "large"
    assert not ground_merge.wanted(store, L)
    ground_merge.save_mode(store, "on")
    assert ground_merge.wanted(store, L) and store.load().unreal.merge_ground == "on"
    ground_merge.save_mode(store, "off")
    L.city.size = "metro"
    assert not ground_merge.wanted(store, L)
    assert "merge_ground" not in UnrealSettings().model_dump(mode="json")
    with pytest.raises(ValueError):
        ground_merge.save_mode(store, "sometimes")


def test_the_unreal_tab_reads_and_saves_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    sw_site.save_layout(store, small_city())
    c = TestClient(ui_app.create_app(JobQueue()))
    assert c.get("/api/projects/p/unreal").json()["merge_ground"] == {"mode": "auto", "now": False}
    assert c.post("/api/projects/p/ue/merge-ground", json={"mode": "on"}).json() == {"mode": "on", "now": True}
    assert c.post("/api/projects/p/ue/merge-ground", json={"mode": "x"}).status_code == 422
    assert json.loads((store.root / "project.json").read_text())["unreal"]["merge_ground"] == "on"

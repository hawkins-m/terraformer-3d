"""Extended building types (city_extended.py): behind CitySpec.extended_types, off for existing
projects (left out of layout.json, the plan untouched) and on for new projects and rolls; each type's
placement, greybox slot (shared unit meshes), viewer rows and Building types editor entry."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point

from asset_pipeline import city, city_extended, city_roll, city_view, greybox, layout_presets
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import SiteLayout
from asset_pipeline.stages import sw_site
from asset_pipeline.ui import app as ui_app
from test_city import heights, small_city


def extended(L: SiteLayout, preset: str | None = None) -> SiteLayout:
    L = layout_presets.apply(L, preset) if preset else L.model_copy(deep=True)
    L.city.extended_types = True
    return L


@pytest.fixture(scope="module")
def castles():
    """A walled-castle and a star-fortress small city, extended types on: {preset: (layout, heights, plan)}."""
    out = {}
    for name in ("walled-castle", "star-fortress"):
        L = extended(small_city(), name)
        h = heights(L)
        out[name] = (L, h, city.plan(L, h))
    return out


def test_off_by_default_left_out_of_the_layout_and_the_plan_is_untouched():
    L = small_city()
    assert not L.city.extended_types and "extended_types" not in L.model_dump_json()
    assert city_extended.types(L.city) == [] and city_extended.all_typologies(L.city) == []
    h = heights(L)
    P = city.plan(layout_presets.apply(L, "walled-castle"), h)
    assert P.extended == [] and "extended" not in P.stats
    on = extended(L)
    assert '"extended_types":true' in on.model_dump_json()
    assert [t.id for t in city_extended.types(on.city)] == list(city_extended.DEFAULTS)


@pytest.mark.parametrize("name", ["walled-castle", "star-fortress"])
def test_a_castle_stands_on_the_central_hub_with_its_gate_to_the_main_road(castles, name):
    L, h, P = castles[name]
    cs = [s for s in P.extended if s.type == "castle"]
    assert len(cs) == 1 and P.stats["extended"]["castle"] == 1
    st, nd = cs[0], city_extended.castle_node(P.layout.city)
    assert (st.x, st.y) == tuple(nd.center)
    roles = {p.role for p in st.parts}
    assert {"keep", "curtain", "corner-tower", "corner-tower-roof", "gate-tower", "gate-passage", "great-hall"} <= roles
    assert sum(p.role == "corner-tower" for p in st.parts) == 4
    assert {p.prim for p in st.parts} >= {"box", "cyl", "cone", "gable"}
    keep = next(p for p in st.parts if p.role == "keep")
    assert keep.h >= 25 and all(p.h <= keep.h + 8 for p in st.parts if p.role != "keep-turret")
    # the gatehouse sits in the wall towards `rot`, the keep at the back
    gate = next(p for p in st.parts if p.role == "gate-passage")
    fwd = city._dir(st.rot)
    assert np.dot([gate.x - st.x, gate.y - st.y], fwd) > 30 > np.dot([keep.x - st.x, keep.y - st.y], fwd)
    if name == "walled-castle":       # up the castle road, which ends at the gatehouse
        road = next(w for w in P.ways if w.id == "castle-gate-av")
        end = road.line.coords[-1]
        b = math.degrees(math.atan2(end[1] - st.y, end[0] - st.x))
        assert abs(city._angdiff(b, st.rot)) < 1
        # the trace steps 20 m: it may run on into the gate passage
        assert city_extended.castle_reach(nd) - 20 < math.dist(end, (st.x, st.y)) < city_extended.castle_reach(nd) + 30
    else:                             # square to the grid
        assert round(st.rot) % 90 == round(P.layout.city.preset.grid_deg) % 90
    # nothing else stands in it: no housing, trees or the hub's monument
    assert not any(st.foot.contains(Point(b.x, b.y)) for b in P.buildings)
    assert not any(st.foot.contains(Point(t[0], t[1])) for t in P.trees)
    assert not any(pl.district == nd.id and pl.type in ("rotunda", "temple") for pl in P.layout.plots)
    tight = next(s for s in city.shots(L, h) if s.id == f"tight-{nd.id}")     # the castle has the node's tight
    assert math.dist(tight.look_at[:2], (st.x, st.y)) < 1


def test_no_castle_in_other_layouts():
    L = extended(small_city(), "grid")
    P = city.plan(L, heights(L))
    assert not any(s.type == "castle" for s in P.extended)


def test_the_castle_is_one_greybox_slot_of_shared_unit_meshes(castles):
    L, h, _ = castles["walled-castle"]
    spec = greybox.build_spec(L, h)
    slots = [s for s in spec["slots"] if s["type"] == "castle"]
    assert len(slots) == 1 and slots[0]["typology"] == "castle" and slots[0]["category"] == "building"
    prims = {p["prim"]["kind"] for p in slots[0]["pieces"]}
    assert prims == {"box", "cylinder", "cone", "gable"}
    for p in slots[0]["pieces"]:      # unit meshes, scaled: one mesh per primitive for the whole city
        assert p["prim"] in (city_extended.CONE, *city.UNIT.values()) and len(p["scale"]) == 3
    ids = [s["id"] for s in spec["slots"]]
    assert len(ids) == len(set(ids))


def test_viewer_rows_carry_the_type_and_shape(castles, tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p", start="city")
    L, h, P = castles["walled-castle"]
    sw_site.save_layout(store, L)
    d = city_view.scene(store, P, h)
    ti = d["types"].index("castle")
    rows = [r for r in d["buildings"] if r[7] == ti]
    assert len(rows) == len(P.extended[0].parts)
    assert {d["shapes"][r[9]] for r in rows if len(r) > 9} == {"cyl", "cone", "gable"}
    assert all(len(r) == 9 for r in d["buildings"] if r[7] != ti)


def test_new_projects_and_rolls_turn_it_on_existing_projects_keep_it_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("new", start="city")
    assert sw_site.init(store, force=True, city_mode=True).city.extended_types
    old = ProjectStore.create("old", start="city")
    L = small_city()
    sw_site.save_layout(old, L)
    assert not sw_site.load_layout(old).city.extended_types
    city_roll.roll(old, seed=5, shots=False, log=lambda m: None)
    assert sw_site.load_layout(old).city.extended_types
    city_roll.set_extended(old, False, shots=False, log=lambda m: None)
    assert not sw_site.load_layout(old).city.extended_types
    assert city_roll.summary(old)["size"]["extended_types"] is False


def test_the_building_types_editor_lists_them_and_keeps_edits_apart_from_the_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p", start="city")
    sw_site.save_layout(store, extended(small_city()))
    client = TestClient(ui_app.create_app(JobQueue()))
    rows = client.get("/api/projects/p/catalog").json()["typologies"]
    ext = {r["id"]: r for r in rows if r.get("extended")}
    assert set(ext) == set(city_extended.DEFAULTS)
    for r in ext.values():            # Look words for frames and asset sheets
        assert r["prompt"] and r["desc"] and r["roofs"] and r["facades"]
    rows = [dict(r) for r in rows]
    next(r for r in rows if r["id"] == "castle")["prompt"] = "a ruined castle"
    assert client.put("/api/projects/p/catalog/typologies", json=rows).status_code == 200
    L = sw_site.load_layout(store)
    assert L.city.typologies == []                       # still no catalog: the plain massing stays
    assert [t.id for t in L.city.extended_looks] == ["castle"]
    assert {t.id: t for t in city_extended.all_typologies(L.city)}["castle"].prompt == "a ruined castle"
    assert client.put("/api/projects/p/catalog/typologies", json=client.get("/api/projects/p/catalog").json()["typologies"]
                      ).status_code == 200
    assert [t.id for t in sw_site.load_layout(store).city.extended_looks] == ["castle"]   # the edit stays

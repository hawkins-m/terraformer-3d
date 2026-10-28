"""A preset's whole city shape on a roll (city_shape.py, city_roll.roll): outline, wall and its
trace, districts and landmarks, satellite towns, all on land; "Keep current outline and
districts"; utopia rolls as before; heightmaps keep their coast. Synthetic projects."""
import math

import numpy as np
import pytest

from asset_pipeline import city, city_roll, city_shape, greybox, layout_presets
from asset_pipeline.project import ProjectStore, read_json, write_json
from asset_pipeline.schema import Enclosure, Town
from asset_pipeline.stages import sw_site
from test_layout_presets import slot_overlaps


def walled_starter():
    """The starter city with a round wall, a green belt and a satellite town of its own."""
    L = sw_site.starter_city()
    L.city.enclosure = Enclosure(center=(0, -500), radius=1000, greenbelt_w=150)
    L.city.towns = [Town(id="town", center=(2000, -700), radius=300)]
    return L


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Shape Test", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", walled_starter().model_dump(mode="json"))
    return s


@pytest.fixture
def fast(monkeypatch):
    """No real plan or shots."""
    monkeypatch.setattr(sw_site, "plan_city", lambda store: {"buildings": 100, "warnings": []})
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])


def on_land(store, L) -> list[str]:
    """The districts whose core is not on land (the sea, under the sea level) on the plan's ground."""
    h = city.apply_pads(L, sw_site.plan_ground(store, L))
    f = city.Fields(city.with_preset(L), h)
    bad = []
    for nd in L.city.nodes:
        a = np.linspace(0, 2 * math.pi, 8, endpoint=False)
        xs = np.r_[nd.center[0], nd.center[0] + 0.6 * nd.radius * np.cos(a)]
        ys = np.r_[nd.center[1], nd.center[1] + 0.6 * nd.radius * np.sin(a)]
        if not (np.all(f.land(xs, ys)) and np.all(f.z(xs, ys) > L.sea_level)):
            bad.append(nd.id)
    return bad


@pytest.mark.parametrize("name", [n for n in layout_presets.PRESETS if city_shape.has_recipe(n)])
def test_every_preset_roll_makes_its_own_shape_on_land(store, fast, name):
    city_roll.roll(store, seed=5, preset=name, log=lambda *a: None)
    L = sw_site.load_layout(store)
    c, rec = L.city, city_shape.RECIPES[name]
    assert c.preset.name == name
    assert {n.id for n in c.nodes} <= {d.id for d in rec.districts} and c.nodes[0].id == rec.districts[0].id
    assert len(c.nodes) >= max(1, len(rec.districts) - 1)
    assert on_land(store, L) == []
    assert c.sites == []
    if rec.wall:
        assert c.enclosure is not None and c.enclosure.shape == rec.wall.get("shape", "circle")
    else:
        assert c.enclosure is None                     # the project's own wall is gone
    assert {tw.id for tw in c.towns} <= set(rec.town_names) and "town" not in {tw.id for tw in c.towns}
    x0, y0, x1, y1 = c.bounds
    assert all(x0 <= n.center[0] <= x1 and y0 <= n.center[1] <= y1 for n in c.nodes)


def test_classic_has_no_wall_no_green_belt_no_forum(store):
    city_roll.roll(store, seed=3, preset="classic", log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    c = L.city
    assert c.enclosure is None and not any(n.role == "forum" or n.monument for n in c.nodes)
    assert c.nodes[0].id == "downtown" and c.nodes[0].civic_ring == "none" and c.nodes[0].stoa_share == 0
    P = city.plan(L, sw_site.plan_terrain(store, L))
    kinds = {b.kind for b in P.buildings}
    assert {"tower", "housing", "houses", "industrial"} <= kinds and not P.walls
    assert {w.id.split("-")[0] for w in P.ways if w.kind == "highway"} >= {"hw"} and P.interchanges
    assert slot_overlaps(P) == []


def test_manhattan_is_a_long_narrow_strip_with_water_on_its_long_sides(store):
    city_roll.roll(store, seed=3, preset="manhattan", log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    P = city.plan(L, sw_site.plan_terrain(store, L))
    f = city.Fields(L, sw_site.plan_terrain(store, L))
    pts = np.array([[b.x, b.y] for b in P.buildings])
    across, along = np.ptp(pts @ f.u), np.ptp(pts @ f.v)
    assert along > 3.5 * across                                        # long and narrow
    assert any(w.kind == "river" and w.id.startswith("channel") for w in P.ways)   # a channel inland ...
    C = np.array(city.main_centre(L.city))
    sea_side = C + (L.city.preset.outline_m + 120) * f.u               # ... the sea on the other side
    assert not bool(f.land(*sea_side))
    assert slot_overlaps(P) == []


def test_star_fortress_wall_follows_a_bastioned_star_trace(store):
    city_roll.roll(store, seed=3, preset="star-fortress", log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    e = L.city.enclosure
    P = city.plan(L, sw_site.plan_terrain(store, L))
    bays = [w for w in P.walls if w.piece == "wall:bay"]
    r = [math.dist((w.x, w.y), e.center) for w in bays]
    assert bays and min(r) < e.radius * 1.05 and max(r) > e.radius * (1 + 0.8 * e.point_m)
    corners = [360 * k / e.points for k in range(e.points)]
    assert P.gates and all(any(abs(city._angdiff(g, cn)) < 0.01 for cn in corners) for g in P.gates)
    assert P.moat and all(not q.interiors for q in P.moat)
    assert slot_overlaps(P) == []


def test_island_city_is_offshore_with_the_mainland_kept(store, fast):
    city_roll.roll(store, seed=4, preset="island-city", log=lambda *a: None)
    L = sw_site.load_layout(store)
    h = city.apply_pads(L, sw_site.plan_ground(store, L))
    f = city.Fields(L, h)
    C = np.array(city.main_centre(L.city))
    assert float(f._shore(float(C @ f.v)) - C @ f.u) < 0                  # the centre is out to sea of the coast
    gap = C - (L.city.preset.outline_m + 150) * f.u                       # the strait towards the mainland
    assert float(f.z(*gap)) < L.sea_level
    assert [tw.id for tw in L.city.towns] == ["mainland"] and bool(f.land(*L.city.towns[0].center))


def test_keep_current_outline_and_districts_rolls_only_new_streets(store, fast):
    before = sw_site.load_layout(store)
    city_roll.roll(store, seed=6, preset="grid", keep_shape=True, log=lambda *a: None)
    L = sw_site.load_layout(store)
    assert L.city.preset.streets == "grid" and L.city.preset.outline == before.city.preset.outline
    assert L.city.enclosure == before.city.enclosure and L.city.towns == before.city.towns
    assert [n.id for n in L.city.nodes] == [n.id for n in before.city.nodes]
    assert [n.center for n in L.city.nodes] == [n.center for n in before.city.nodes]
    assert L.city.bounds == before.city.bounds


def test_utopia_rolls_as_before_keeping_the_projects_wall_and_towns(store, fast):
    city_roll.roll(store, seed=6, log=lambda *a: None)                    # the layout's own preset: utopia
    L = sw_site.load_layout(store)
    assert L.city.preset.name == "utopia" and L.city.enclosure is not None and [t.id for t in L.city.towns] == ["town"]
    rolled, _ = city_roll.rolled(walled_starter(), 6)
    assert [n.id for n in L.city.nodes] == [n.id for n in rolled.city.nodes]


def test_previous_roll_restores_the_shape(store, fast):
    city_roll.roll(store, seed=7, preset="walled-castle", log=lambda *a: None)
    castle = sw_site.load_layout(store)
    city_roll.roll(store, seed=8, preset="classic", log=lambda *a: None)
    city_roll.step(store, -1)
    L = sw_site.load_layout(store)
    assert L.city.preset.name == "walled-castle" and L.city.enclosure == castle.city.enclosure
    assert L.city.towns == castle.city.towns and [n.model_dump() for n in L.city.nodes] == [n.model_dump() for n in castle.city.nodes]


def test_a_heightmap_keeps_its_coast_and_no_district_lands_in_the_sea(store, fast):
    from PIL import Image
    L = sw_site.load_layout(store)
    Image.fromarray(greybox.to_png16(greybox.make_terrain(L.terrain), L.terrain.z_range)).save(sw_site.site_dir(store) / "hm.png")
    L.terrain.heightmap = "site/hm.png"
    sw_site.save_layout(store, L)
    for seed, preset in ((9, None), (10, "harbour-port")):
        r = city_roll.roll(store, seed=seed, preset=preset, log=lambda *a: None)
        lay = sw_site.load_layout(store)
        assert "waterfront" in r["keep"] and lay.terrain.shore_m == L.terrain.shore_m
        assert on_land(store, lay) == []


def test_roll_api_and_cli_take_keep_shape(store, fast):
    import time
    from fastapi.testclient import TestClient
    from asset_pipeline import cli
    from asset_pipeline.jobs import JobQueue
    from asset_pipeline.ui import app as ui_app
    before = sw_site.load_layout(store)
    c = TestClient(ui_app.create_app(JobQueue()))
    j = c.post("/api/projects/shape-test/site/roll", json={"seed": 2, "preset": "suburbia", "keep_shape": True}).json()
    for _ in range(200):
        if c.get(f"/api/jobs/{j['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert c.get(f"/api/jobs/{j['id']}").json()["status"] == "done"
    L = sw_site.load_layout(store)
    assert L.city.preset.name == "suburbia" and L.city.enclosure == before.city.enclosure
    cli.main(["site", "roll", "shape-test", "--seed", "3", "--preset", "classic"])
    assert sw_site.load_layout(store).city.enclosure is None
    html = (ui_app.STATIC / "index.html").read_text() if hasattr(ui_app.STATIC, "read_text") else open(f"{ui_app.STATIC}/index.html").read()
    assert 'id="city-roll-keep-shape"' in html and "Keep current outline and districts" in html

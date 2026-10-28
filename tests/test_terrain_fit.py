"""The city plan follows the ground (city.fits_terrain: terrain settings, or any preset but utopia):
nothing built on water or a cliff, flat pads under buildings on slopes, rough ground left green,
highways round steep ground and across rivers, walls and moats on land. Synthetic projects."""
import math

import numpy as np
import pytest

from asset_pipeline import city, city_roll, greybox, layout_presets, terrain_gen
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_site


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Fit Test", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    return s


def on(store, terrain, preset=None, seed=4):
    if preset:
        city_roll.roll(store, seed=seed, preset=preset, log=lambda *a: None, shots=False)
    city_roll.set_terrain(store, terrain_gen.get(terrain, seed=5), log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    h = sw_site.plan_terrain(store, L)
    return L, h, city.plan(L, h), city.Fields(city.with_preset(L), h)


def corners(b):
    u, v = city._dir(b.rot), city._dir(b.rot + 90)
    return np.array([np.array([b.x, b.y]) + sx * b.w / 2 * u + sy * b.d / 2 * v for sx in (-1, 1) for sy in (-1, 1)])


def test_the_switch_leaves_utopia_on_its_own_terrain_alone():
    L = sw_site.starter_city()
    assert not city.fits_terrain(L)
    assert city.fits_terrain(layout_presets.apply(L, "classic"))
    assert city.fits_terrain(L.model_copy(update={"terrain": terrain_gen.with_settings(L.terrain, terrain_gen.get("flat-plain"))}))


@pytest.mark.parametrize("terrain,preset", [("river-valley", "classic"), ("island", "utopia"), ("hill-city", "walled-castle")])
def test_nothing_is_built_on_water_or_a_cliff(store, terrain, preset):
    L, h, P, f = on(store, terrain, preset if preset != "utopia" else None)
    assert P.buildings
    for b in P.buildings:
        cs = corners(b)
        assert np.all(f.land(cs[:, 0], cs[:, 1])), b
        zs = f.z(cs[:, 0], cs[:, 1])
        assert (zs.max() - zs.min()) / max(min(b.w, b.d), 1) <= city.STEEP + 1e-6
    assert all(bool(f.land(b.x, b.y)) for b in P.typed)


def test_buildings_on_slopes_stand_on_flat_pads(store):
    L, h, P, f = on(store, "hill-city")
    assert P.pads
    by = {(round(p[0], 3), round(p[1], 3)): p for p in P.pads}
    padded = [b for b in P.buildings if (round(b.x, 3), round(b.y, 3)) in by]
    assert padded
    for b in padded[:200]:
        x, y, w, d, rot, z0, z1 = by[(round(b.x, 3), round(b.y, 3))]
        zs = f.z(*corners(b).T)
        assert abs(z1 - zs.max()) < 1e-3 and z0 < zs.min() and abs(b.z - (z1 - 0.05)) < 1e-6   # the top level with the uphill side
    spec = greybox.build_spec(L, h)
    pads = [s for s in spec["slots"] if s["type"] == "pad"]
    assert pads and all(p["category"] == "structure" for p in pads)


def test_rough_ground_stays_green(store):
    L, h, P, f = on(store, "plateau")
    rough = [b for b in P.blocks if float(f.slope(*b.centroid.coords[0])) > city.ROUGH]
    built = {(round(b.x), round(b.y)) for b in P.buildings}
    assert rough
    assert all(not any(b.contains(__import__("shapely").geometry.Point(x, y)) for x, y in built) for b in rough[:40])
    assert len(P.parks) >= len(rough)


def test_highways_go_round_steep_ground_and_bridge_rivers(store):
    L, h, P, f = on(store, "river-valley", "classic")
    hw = [w for w in P.ways if w.kind == "highway"]
    assert hw
    for w in hw:
        pts = np.asarray(w.line.segmentize(20).coords)
        assert np.mean(f.slope(pts[:, 0], pts[:, 1]) < city.HIGHWAY_GRADE + 0.05) > 0.95
        assert np.all(f.ground(pts[:, 0], pts[:, 1]))                     # never into the sea
    river = next(w for w in P.ways if w.id.startswith("terrain-river"))
    assert any(w.line.intersects(river.line) for w in P.ways if w.kind in ("avenue", "arterial", "highway"))   # bridges


def test_walls_and_moat_stay_on_land(store):
    L, h, P, f = on(store, "river-valley", "walled-castle")
    assert P.walls
    for w in P.walls:
        if w.piece == "wall:bay":
            assert bool(f.land(w.x, w.y))
    assert all(bool(f.land(*q.representative_point().coords[0])) for q in P.moat)
    assert all(bool(f.land(*nd.center)) for nd in L.city.nodes)


def test_island_city_on_an_island_terrain_takes_the_terrains_island(store):
    L, h, P, f = on(store, "island", "island-city")
    ts = L.terrain.settings
    assert ts.coast == "all"
    o = f.outline
    assert o.terrain is not None and np.allclose(o.c, ts.centre)           # the terrain's own shore, not a second island
    sea_ways = [w for w in P.ways if not np.all(f.ground(*np.asarray(w.line.segmentize(40).coords).T))
                and w.kind not in ("river", "canal")]
    assert not [w for w in sea_ways if w.kind in ("promenade", "street", "avenue", "ring")]
    x0, y0, x1, y1 = L.city.bounds
    assert x1 - x0 >= 2 * ts.island_r_m and y1 - y0 >= 2 * ts.island_r_m      # the bounds take the island in

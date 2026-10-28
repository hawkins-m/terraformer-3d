"""Terrain settings (schema.TerrainSettings, terrain_gen.py): base noise, a mountain ring with passes,
hills, a river lowered into the ground, the coast (side, island, inland) with a beach, a plateau with
terraces; water is not land; rerolling the terrain keeps the city's layout preset and seeds, rerolling
the city keeps the terrain. Synthetic projects."""
import math

import numpy as np
import pytest

from asset_pipeline import city, city_roll, greybox, layout_presets, terrain_gen
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.schema import TerrainSettings, TerrainSpec
from asset_pipeline.stages import sw_site


def spec(**kw) -> TerrainSpec:
    """A small tile (fast) with settings."""
    t = TerrainSpec(extent_m=4000, resolution=401, shore_m=1200, z_range=(-80, 600))
    return t.model_copy(update={"settings": TerrainSettings(**kw)})


def H(t):
    return greybox.Heights(greybox.make_terrain(t), t.extent_m)


def test_no_settings_is_the_legacy_generator():
    t = TerrainSpec(extent_m=4000, resolution=401)
    assert t.settings is None
    assert np.array_equal(greybox.make_terrain(t), greybox.make_terrain(t.model_copy()))
    assert not np.array_equal(greybox.make_terrain(t), greybox.make_terrain(spec()))


def test_its_own_seed_draws_it():
    a, b = greybox.make_terrain(spec(seed=1, base="rugged")), greybox.make_terrain(spec(seed=1, base="rugged"))
    assert np.array_equal(a, b)
    assert not np.array_equal(a, greybox.make_terrain(spec(seed=2, base="rugged")))
    flat, rugged = greybox.make_terrain(spec(base="flat")), greybox.make_terrain(spec(base="rugged"))
    land = greybox.make_terrain(spec()) > 5
    assert np.std(np.diff(rugged, axis=1)[land[:, 1:]]) > 3 * np.std(np.diff(flat, axis=1)[land[:, 1:]])


def test_a_mountain_ring_with_passes_round_the_city():
    t = spec(mountains_m=300, mountains_at_m=1500, mountains_w_m=500, mountain_gaps=2, gap_deg=40, centre=(0, 0))
    h = H(t)
    ring = [h(1500 * math.cos(a), 1500 * math.sin(a)) for a in np.radians([0, 45, 135, 180])]
    assert min(ring) > h(0, 0) + 150                                      # the ring stands over the city
    # the first pass faces the sea (sea_dir -90: south), the second opposite it
    assert h(0, 1500) < max(ring) - 150 and h(0, -1100) < 60


def test_hills_and_a_terraced_plateau():
    t = spec(hills=4, hill_m=60, city_r_m=900, relief_m=0, rise=0)
    assert H(t).h.max() > 40
    tp = spec(plateau_m=80, plateau_r_m=700, terraces=4, relief_m=0, rise=0, coast="none")
    h = H(tp)
    assert h(0, 0) > h(0, 1600) + 70
    prof = np.array([h(x, 0) for x in np.arange(0, 1200, 5)])
    steps = np.diff(prof)
    assert (np.abs(steps) < 0.05).sum() > 0.6 * len(steps)               # mostly level treads ...
    assert (steps < -1).sum() >= 3                                       # ... between risers


def test_the_coast_side_island_and_inland():
    hs = H(spec(beach_m=80))
    assert hs(0, -1320) < 0 and 0 < hs(0, -1250) < 3                      # the sea, then a low beach (the headland: shore at 1280 m)
    isl = spec(coast="all", island_r_m=1000, centre=(0, 0))
    h = H(isl)
    assert h(0, 0) > 0 and h(1600, 0) < 0 and h(0, -1600) < 0 and h(0, 1600) < 0
    inland = H(spec(coast="none"))
    assert inland.h.min() > 5                                            # no sea anywhere


def test_a_river_is_real_water_lowered_into_the_ground_falling_to_the_sea():
    t = spec(river=True, river_w_m=60, river_depth_m=5, river_meander_m=0, rise=0.05)
    raw = greybox.make_terrain(t.model_copy(update={"settings": t.settings.model_copy(update={"river": False})}))
    h = greybox.make_terrain(t)
    hs, hr = greybox.Heights(h, t.extent_m), greybox.Heights(raw, t.extent_m)
    ln = terrain_gen.river_line(t)
    assert ln is not None
    mask = terrain_gen.water_mask(t, terrain_gen.terrain_lines(t))
    assert mask.sum() > 0
    for y in (800.0, 300.0, -300.0):                                     # its bed well below the banks
        assert hs(0, y) < hr(0, y) - 5 and hs(0, y) < hs(100, y) - 4
    beds = [hs(0, y) for y in (1500.0, 900.0, 300.0, -300.0, -900.0)]
    assert all(a >= b - 0.5 for a, b in zip(beds, beds[1:]))               # never rising towards the sea
    canyon = spec(river=True, river_meander_m=0, canyon_m=50, coast="none", rise=0, plateau_m=80, plateau_r_m=5000)
    hc = H(canyon)
    assert hc(220, 0) - hc(0, 0) > 40                                     # steep walls either side


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Terrain Test", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    return s


def test_water_is_not_land_and_roads_bridge_it(store):
    city_roll.set_terrain(store, TerrainSettings(river=True, river_w_m=60, seed=3), log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    h = sw_site.plan_terrain(store, L)
    P = city.plan(L, h)
    f = city.Fields(city.with_preset(L), h)
    river = [w for w in P.ways if w.id.startswith("terrain-river")]
    assert river
    m = river[0].line.interpolate(0.5, normalized=True)
    assert bool(f.ground(m.x, m.y)) and not bool(f.land(m.x, m.y))          # dry ground no, a bridgeable river yes
    bridges = [w for w in P.ways if w.kind in ("avenue", "arterial") and w.line.intersects(river[0].line)]
    assert bridges
    assert all(not bool(f.water(*nd.center)) for nd in L.city.nodes)       # no district in the river


def test_rerolling_the_terrain_keeps_the_layout_and_rerolling_the_city_keeps_the_terrain(store, monkeypatch):
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    city_roll.roll(store, seed=4, preset="classic", layout_seed=6, log=lambda *a: None, shots=False)
    before = sw_site.load_layout(store)
    city_roll.set_terrain(store, TerrainSettings(name="t", seed=9, hills=3), log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    assert (L.city.preset, L.city.layout_seed, L.city.seed) == (before.city.preset, 6, 4)
    assert L.terrain.settings.seed == 9 and L.terrain.settings.hills == 3
    city_roll.roll(store, seed=5, log=lambda *a: None, shots=False)
    after = sw_site.load_layout(store)
    assert after.terrain == L.terrain and after.city.seed == 5                # the terrain stays, coast included
    assert after.city.preset.name == "classic"


def test_a_heightmap_is_replaced_only_when_asked(store):
    L = sw_site.load_layout(store)
    from PIL import Image
    Image.fromarray(greybox.to_png16(greybox.make_terrain(L.terrain), L.terrain.z_range)).save(sw_site.site_dir(store) / "hm.png")
    L.terrain.heightmap = "site/hm.png"
    sw_site.save_layout(store, L)
    with pytest.raises(city_roll.HeightmapInUse):
        city_roll.set_terrain(store, TerrainSettings(), log=lambda *a: None, shots=False)
    city_roll.set_terrain(store, TerrainSettings(), log=lambda *a: None, shots=False, replace_heightmap=True)
    L = sw_site.load_layout(store)
    assert L.terrain.heightmap is None and L.terrain.settings is not None
    lo, hi = L.terrain.z_range
    h = greybox.make_terrain(L.terrain)
    assert lo <= h.min() and h.max() <= hi

"""Described terrains (terrain_compose.py): a TerrainCompose spec built into heights, water and coast, the
same outputs a preset gives (the plan, greybox spec and 3D viewer work unchanged); presets untouched."""
import math

import numpy as np
import pytest

from asset_pipeline import city, city_roll, city_view, greybox, terrain_compose as TC, terrain_gen
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import TerrainCompose
from asset_pipeline.stages import sw_site

SPECS = {
    "volcano": {"base": "island", "features": [{"type": "volcano", "at": "centre", "size": "large", "crater": True}],
                "city": [{"kind": "ring"}]},
    "valley": {"base": "valley", "features": [{"type": "river", "at": "centre"}], "city": [{"kind": "river"}]},
    "cliffs": {"base": "coast", "side": "west", "features": [{"type": "cliffs", "at": "west", "steepness": "steep"},
                                                            {"type": "bay", "at": "coast"}, {"type": "hills", "at": "inland"}],
               "city": [{"kind": "coast"}]},
    "plateau": {"base": "plateau", "features": [{"type": "plateau", "at": "centre", "size": "large", "height_m": 300},
                                                {"type": "lake", "at": "centre"}]},
    "delta": {"base": "coast", "ground": "flat", "features": [{"type": "river", "at": "centre", "count": 5}]},
}


def built(name, seed=5, res=513):
    L = sw_site.starter_city()
    t0 = L.terrain.model_copy(update={"resolution": res})
    s = TC.compose(TerrainCompose.model_validate(SPECS[name]), seed=seed, extent_m=t0.extent_m)
    t = terrain_gen.with_settings(t0, s)
    return t, terrain_gen.make(t)


def at(t, h, x, y):
    n = t.resolution
    j = int(round((x / t.extent_m + 0.5) * (n - 1)))
    i = int(round((0.5 - y / t.extent_m) * (n - 1)))
    return float(h[i, j])


def test_presets_have_no_spec_and_stay_byte_for_byte():
    for name, s in terrain_gen.PRESETS.items():
        assert s.compose is None and "compose" not in s.model_dump_json(), name
    t = sw_site.starter_city().terrain.model_copy(update={"resolution": 257})
    a = terrain_gen.make(terrain_gen.with_settings(t, terrain_gen.get("island", 3)))
    b = terrain_gen.make(terrain_gen.with_settings(t, terrain_gen.get("island", 3)))
    assert a.tobytes() == b.tobytes()


def test_a_volcano_island_with_a_crater():
    t, h = built("volcano")
    assert t.settings.coast == "all" and t.settings.name == "described"
    half = t.extent_m / 2 - 10
    assert all(at(t, h, x, y) < 0 for x, y in ((half, 0), (-half, 0), (0, half), (0, -half)))    # the sea all round
    R = TC.radius(t, t.settings.compose.features[0])
    rim = max(at(t, h, 0.2 * R * math.cos(a), 0.2 * R * math.sin(a)) for a in np.linspace(0, 6.2, 12))
    assert rim > 600 and at(t, h, 0, 0) < rim - 50                                       # a cone, a crater at its top
    assert at(t, h, 0.6 * R, 0) < 0.6 * rim


def test_a_valley_has_ridges_either_side_and_a_river_to_the_sea():
    t, h = built("valley")
    assert t.sea_dir == 270.0
    lines = terrain_gen.terrain_lines(t)
    assert len(lines) == 1 and lines[0].flow == "down"
    off = 0.3 * t.extent_m / 2
    assert at(t, h, -off, 600) > at(t, h, 0, 600) + 150 < at(t, h, off, 600) + 300


def test_cliffs_a_bay_on_the_west_coast_and_hills_inland():
    t, h = built("cliffs")
    assert t.sea_dir == 180.0 and t.bay_m > 0 and t.headland_m == 0 and t.settings.beach_m == 0
    from asset_pipeline.greybox import shore_at
    assert shore_at(t, 0.0) < shore_at(t, 2000.0) - 300                                # the bay pulls the shore in
    assert at(t, h, -t.shore_m - 300, 2500) < 0 < at(t, h, -t.shore_m + 300, 2500)


def test_a_plateau_with_a_lake_and_a_delta_of_channels():
    t, h = built("plateau")
    assert t.settings.coast == "none" and t.settings.plateau_m == 300
    lakes = TC.lakes(t)
    assert len(lakes) == 1 and not terrain_gen.terrain_lines(t)                             # a basin, not a water line
    q, depth = lakes[0]
    assert len(q.exterior.coords) > 60 and q.area / q.convex_hull.area > 0.9                 # organic, not a rectangle
    lr = q.minimum_rotated_rectangle
    assert q.area < 0.92 * lr.area
    assert terrain_gen.water_mask(t, [])[t.resolution // 2, t.resolution // 2]              # in the water mask
    level = TC.lake_level(t, h, q)
    assert at(t, h, 0, 0) < level - 1 < at(t, h, 1600, 1600)                                 # carved, under its shore
    t, h = built("delta")
    lines = terrain_gen.terrain_lines(t)
    assert len(lines) == 6 and np.ptp(h[h > 0]) < 60                                         # a stem, five channels; flat
    ends = [ln.line.coords[-1] for ln in lines[1:]]
    assert max(e[0] for e in ends) - min(e[0] for e in ends) > 1500                         # fanned out along the coast


def test_city_hints_are_weights():
    t, _ = built("volcano")
    R = TC.radius(t, t.settings.compose.features[0])
    w = TC.weight(t, np.array([0.0, 1.1 * R, 2.5 * R]), np.zeros(3))
    assert w[1] > 0.9 and w[0] < 0.1 and w[2] < 0.1
    t, _ = built("delta")
    assert TC.weight(t, np.zeros(1), np.zeros(1)) is None
    assert "city rings the lower slopes" in TC.line(TerrainCompose.model_validate(SPECS["volcano"]))


@pytest.mark.parametrize("name", list(SPECS))
def test_apply_a_described_terrain_plans_builds_and_draws_like_a_preset(tmp_path, monkeypatch, name):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p", start="city")
    from asset_pipeline import layout_presets
    L = layout_presets.apply(sw_site.starter_city(), "classic")      # a new project's (its recipe fits the bounds to an island)
    L.terrain = L.terrain.model_copy(update={"resolution": 513})
    sw_site.save_layout(store, L)
    s = TC.compose(TerrainCompose.model_validate(SPECS[name]), seed=4, extent_m=L.terrain.extent_m)
    st = city_roll.set_terrain(store, s, log=lambda m: None, shots=False)
    assert st["buildings"] > 100
    L2 = sw_site.load_layout(store)
    assert L2.terrain.settings.compose is not None and (sw_site.site_dir(store) / "terrain.png").is_file()
    h = sw_site.plan_terrain(store, L2)
    P = city.plan(L2, h)
    spec = greybox.build_spec(L2, h)
    assert spec["slots"] and spec["terrain"]
    d = city_view.scene(store, P, h)
    assert d["buildings"] and d["terrain"]["n"] > 0
    from shapely.geometry import Point
    f = city.Fields(P.layout, h)
    if any(f_["type"] == "river" for f_ in SPECS[name]["features"]):
        assert any(w.kind == "river" for w in P.ways)                    # the plan carries the water
    assert not any(bool(f.water(b.x, b.y)) for b in P.buildings)          # nothing on a river or a lake
    assert all(float(f.z(b.x, b.y)) > L2.sea_level for b in P.buildings)  # nor in a bay
    for q, level in P.lakes:
        assert not any(w.kind not in ("river", "canal") and w.line.intersects(q) for w in P.ways)
        assert not any(b.intersection(q).area > 1 for b in P.blocks + P.parks)
        assert not any(q.contains(Point(b.x, b.y)) for b in P.buildings)
        lake = next(s_ for s_ in spec["slots"] if s_["type"] == "lake")
        assert lake["pieces"][0]["piece"] == "lake-water" and lake["category"] == "terrain"
        assert {round(v[2], 3) for v in lake["pieces"][0]["prim"]["pts"]} == {0.0}            # flat, at its level
        assert d["lakes"] and abs(d["lakes"][0][1] - level) < 0.02
    if name == "plateau":
        assert P.lakes
    if name == "volcano":                          # the districts ring the cone's lower slopes
        R = TC.radius(L2.terrain, L2.terrain.settings.compose.features[0])
        C = L2.terrain.settings.centre
        ds = [math.dist(n.center, C) for n in L2.city.nodes]
        assert sum(0.6 * R < d_ < 1.7 * R for d_ in ds) >= len(ds) - 1
        f = city.Fields(P.layout, h)
        steep = TC.steep_ground(f, P.layout.city.max_slope)
        assert steep.area > 0.5 * math.pi * (0.6 * R) ** 2                   # the cone is steep ground
        assert not any(b.intersection(steep).area > 50 for b in P.blocks)    # bare rock: no blocks, parks
        assert sum(p.intersection(steep).area for p in P.parks) < 1.0
        assert all(max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1]) <= TC.PARK_TILE_M + 1e-6 for p in P.parks)
        roads = [w for w in P.ways if w.kind not in ("river", "canal") and not w.id.startswith("lookout")]
        assert all(w.line.intersection(steep.buffer(-2)).length < 1 for w in roads)       # streets stop at its foot
        look = [w for w in P.ways if w.id.startswith("lookout")]
        assert look and sum(w.line.length for w in look) > 1000 and not any(w.kind == "river" for w in P.ways)
        top = min(look, key=lambda w: min(math.dist(q, C) for q in w.line.coords))
        assert min(math.dist(q, C) for q in top.line.coords) < 0.5 * R                    # it reaches the top
        a = [math.degrees(math.atan2(b.y - C[1], b.x - C[0])) for b in P.buildings]
        assert (np.histogram(a, bins=12, range=(-180, 180))[0] > 20).sum() >= 9        # built all round the cone


def test_heights_are_clamped_per_landform():
    from asset_pipeline.schema import TerrainFeature
    assert TC.height(TerrainFeature(type="lake", height_m=1000)) == 30.0
    assert TC.height(TerrainFeature(type="plateau", height_m=1500)) == 600.0
    assert TC.height(TerrainFeature(type="peak", height_m=500)) == 500.0

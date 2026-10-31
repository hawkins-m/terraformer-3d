"""The classic preset (a coastal metro, city_metro.py): every other preset plans byte for byte as
before it; classic's downtown sits on the coast, heights fall off with distance from it, no ring
road, nothing built in the river corridor, and an irregular outer edge."""
import hashlib
import json
import math

import numpy as np
import shapely
import shapely.ops
import pytest

from asset_pipeline import city, city_shape, greybox, layout_presets, terrain_gen
from asset_pipeline.city_metro import LAKEFRONT_M
from asset_pipeline.schema import SiteLayout
from asset_pipeline.stages import sw_site
from test_city import heights, small_city
from test_layout_presets import digest


def on_terrain(name: str | None, seed: int) -> SiteLayout:
    """The starter city (a smaller height grid) on a terrain preset (None: its legacy coast)."""
    L = sw_site.starter_city()
    L.terrain = L.terrain.model_copy(update={"resolution": 513})
    if name is not None:
        L.terrain = terrain_gen.with_settings(L.terrain, terrain_gen.get(name, seed))
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def shaped(L: SiteLayout, name: str, seed: int) -> tuple[SiteLayout, np.ndarray]:
    """The layout with the preset, its shape rolled onto the ground as a roll does, and its heights."""
    g = greybox.make_terrain(L.terrain)
    A = layout_presets.apply(L, name)
    if city_shape.has_recipe(name):
        A = city_shape.shape(A, g, name, seed)
    return A, city.apply_pads(A, g)


def spec_digest(L, h) -> str:
    spec = greybox.build_spec(L, h)
    return hashlib.sha256(json.dumps(spec["slots"], sort_keys=True, default=str).encode()).hexdigest()[:16]


OTHERS = [n for n in layout_presets.PRESETS if n != "classic"]
# plan digests made before classic became a metro (commit 76d10c3)
SMALL = {
    'utopia': 'a67b52531d37c91c',
    'grid': '9aea544b7bac4b18',
    'manhattan': 'a626ad231d0b4104',
    'park-grid': 'f28560c337a4056c',
    'suburbia': 'cc9c2640bd7eb495',
    'garden-city': '4f6163575d574417',
    'concentric': '969b26daaef990cf',
    'walled-castle': '335fd45542604526',
    'star-fortress': 'b5cc120ce0c54859',
    'linear': '12b93764ffee8bcb',
    'organic': 'c96b23131cb1f0a0',
    'canal-city': '20dadcad6668d01c',
    'river-town': 'e91822becf91eabc',
    'harbour-port': '331c8ef03ecf6684',
    'island-city': '12818bf1d556eb7f',
    'hill-town': 'b4a15333022cecbe',
    'cliff-town': 'f5ede61af39c8acd',
    'market-town': '15b74d17b0a79ec4',
    'campus': '82f6e5661a33443c',
}
COASTAL = {
    'utopia': '637bf8a3a3e4d8c0',
    'grid': '5517f796f2b45fc8',
    'manhattan': '5bc841c7a6b20a9b',
    'park-grid': '392c5d3975149fda',
    'suburbia': '95d5967c088dc6e9',
    'garden-city': '476f717340de3066',
    'concentric': '0c468f0c5590a997',
    'walled-castle': '7e1e8c498e7c9fd9',
    'star-fortress': '474a44fe23add537',
    'linear': '8ce1ec049d4db8d7',
    'organic': '99671e51f791d65f',
    'canal-city': 'e92bdbd8c184cf69',
    'river-town': '5c69e51c2aa64131',
    'harbour-port': '90577d88fde3f27a',
    'island-city': 'c703de0981272810',
    'hill-town': '6e7a771520e3a784',
    'cliff-town': '4d4e42c3bddcac66',
    'market-town': '139754a71f618cf3',
    'campus': '038aefac576124d5',
}
MORE = {
    'utopia|-|7': 'b694e300abec9f24',
    'utopia|river-valley|5': '9318b9386211a95e',
    'utopia|flat-plain|4': '7a99ecf8f78cb4c7',
    'utopia|island|2': 'cd0205835c64775c',
    'grid|-|7': 'ddb0a088d20d4256',
    'grid|river-valley|5': '278d214029182341',
    'grid|flat-plain|4': '367877e3728f88e0',
    'grid|island|2': '5b4762259018fcf2',
    'river-town|-|7': '4be31f3f143a2946',
    'river-town|river-valley|5': '54f7269fd27c7e90',
    'river-town|flat-plain|4': '22df545049b8e864',
    'river-town|island|2': '79e0f8f428581b36',
    'harbour-port|-|7': 'faec031248fb8291',
    'harbour-port|river-valley|5': 'ae73812072e5fce7',
    'harbour-port|flat-plain|4': 'fe4170110f7e95d9',
    'harbour-port|island|2': '17b7508d363300ac',
    'suburbia|-|7': '17cee66357c67dd3',
    'suburbia|river-valley|5': 'ee591264030d31d6',
    'suburbia|flat-plain|4': 'a22118f998701728',
    'suburbia|island|2': 'eb4109f713319481',
    'island-city|-|7': '1a7de39e00d2c4a3',
    'island-city|river-valley|5': '2d3f38c7afacc5f3',
    'island-city|flat-plain|4': '6fb6981d7dea81b4',
    'island-city|island|2': '066d9ebdcecaed9d',
}
SPECS = {
    'utopia': '170a5e0edc625a24',
    'grid': 'a5fd87699b6183ef',
    'river-town': '276f98a4ef2eaa61',
    'suburbia': '09fa975a54dde537',
    'harbour-port': 'ce35181dfc3ea82c',
}


@pytest.mark.parametrize("name", OTHERS)
def test_every_other_preset_plans_exactly_as_before_on_the_small_city(name):
    L = small_city()
    assert digest(city.plan(layout_presets.apply(L, name), heights(L))) == SMALL[name]


@pytest.mark.parametrize("name", OTHERS)
def test_every_other_preset_rolls_and_plans_exactly_as_before_on_a_coastal_plain(name):
    A, h = shaped(on_terrain("coastal-plain", 3), name, 3)
    assert digest(city.plan(A, h)) == COASTAL[name]


@pytest.mark.parametrize("key", list(MORE))
def test_other_presets_on_other_terrains_plan_exactly_as_before(key):
    name, terrain, seed = key.split("|")
    A, h = shaped(on_terrain(None if terrain == "-" else terrain, int(seed)), name, int(seed))
    assert digest(city.plan(A, h)) == MORE[key]


@pytest.mark.parametrize("name", list(SPECS))
def test_other_presets_greybox_slots_exactly_as_before(name):
    L = small_city()
    lay = layout_presets.apply(L, name)
    assert spec_digest(lay, heights(L)) == SPECS[name]


# --- classic: a coastal metro ------------------------------------------------------------------

def _ranks(a) -> np.ndarray:
    return np.argsort(np.argsort(np.asarray(a, float))).astype(float)


def spearman(a, b) -> float:
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


def edge_irregularity(P, C) -> float:
    """The built edge's spread round downtown: the coefficient of variation of the 95th-percentile
    building distance over 10-degree sectors that have buildings (a round city: near 0)."""
    by = {}
    for b in P.buildings:
        a = int(math.degrees(math.atan2(b.y - C[1], b.x - C[0])) % 360 // 10)
        by.setdefault(a, []).append(math.dist((b.x, b.y), C))
    reach = np.array([np.percentile(v, 95) for v in by.values() if len(v) >= 15])
    return float(reach.std() / reach.mean())


@pytest.fixture(scope="module", params=[("coastal-plain", 3), ("coastal-plain", 11), ("river-valley", 5), ("flat-plain", 4)],
                ids=lambda p: f"{p[0]}-{p[1]}")
def metro(request):
    name, seed = request.param
    A, h = shaped(on_terrain(name, seed), "classic", seed)
    P = city.plan(A, h)
    return name, A, h, P, city.Fields(city.with_preset(A), h)


def test_classic_is_a_metro_with_no_wall_ring_or_towns(metro):
    _, A, _, P, _ = metro
    c = A.city
    assert c.preset.metro and c.enclosure is None and c.towns == [] and not P.walls
    assert c.nodes[0].id == "downtown" and city.main_centre(c) == tuple(c.nodes[0].center)
    assert not [w for w in P.ways if w.id.startswith(("ring", "hw-ring"))]          # no ring road, no ring highway
    rings = [w for w in P.ways if w.kind == "ring"]                                  # only the districts' own squares
    assert all(w.id.endswith(("-outer", "-square")) for w in rings)
    assert {w.id.split("-")[0] for w in P.ways if w.kind == "highway"} == {"hw"}
    assert all(w.id.startswith("hw-spoke") for w in P.ways if w.kind == "highway")
    assert 2 <= len({w.id.split("-")[0] for w in P.ways if w.id.startswith("diag")}) <= 4    # the diagonals
    kinds = {b.kind for b in P.buildings}
    assert {"tower", "housing", "houses"} <= kinds


def test_classic_heights_fall_off_from_downtown(metro):
    _, A, _, P, _ = metro
    C = city.main_centre(P.layout.city)
    d = [math.dist((b.x, b.y), C) for b in P.buildings]
    assert spearman(d, [b.h for b in P.buildings]) < -0.5
    towers = [math.dist((b.x, b.y), C) for b in P.buildings if b.kind == "tower"]
    houses = [math.dist((b.x, b.y), C) for b in P.buildings if b.kind == "houses"]
    housing = [math.dist((b.x, b.y), C) for b in P.buildings if b.kind == "housing"]
    assert np.median(towers) < np.median(housing) < np.median(houses)             # towers, mid-rise, houses


def test_classic_builds_nothing_in_the_river_corridor(metro):
    _, A, _, P, _ = metro
    rivers = [w for w in P.ways if w.kind == "river"]
    assert len(rivers) >= 2 and any("fork" in w.id for w in rivers)               # a river and its forks
    corridor = shapely.ops.unary_union([w.line.buffer(w.width / 2) for w in rivers])
    tree = shapely.STRtree([city._rect(b.x, b.y, b.w, b.d, b.rot) for b in P.buildings])
    assert len(tree.query(corridor, predicate="intersects")) == 0
    banks = [p for p in P.parks if p.distance(corridor) < 1]
    assert banks                                                                 # green along the banks


def test_classic_downtown_is_on_the_coast_with_a_lakefront_park(metro):
    name, A, _, P, f = metro
    if name == "flat-plain":
        assert all(n.role != "harbour" for n in A.city.nodes)                      # inland: no harbour
        return
    nd = A.city.nodes[0]
    assert float(f.inland(*nd.center)) < nd.radius + 400
    towers = [b for b in P.buildings if b.kind == "tower"]
    assert min(float(f.inland(b.x, b.y)) for b in towers) < 300                    # towers down to the lakefront
    assert any(w.id.startswith("lakeshore") and w.kind == "boulevard" for w in P.ways)
    strip = [p for p in P.parks if float(f.inland(*p.representative_point().coords[0])) < LAKEFRONT_M + 20]
    assert sum(p.area for p in strip) > 1.5e5                                    # the lakefront park, 15 ha and more
    hb = next(n for n in A.city.nodes if n.role == "harbour")
    assert math.dist(hb.center, nd.center) < 4 * nd.radius + 300 and P.piers       # the harbour beside downtown


def test_classic_uses_the_terrains_own_river():
    A, h = shaped(on_terrain("river-valley", 5), "classic", 5)
    P = city.plan(A, h)
    ids = {w.id.split("-")[0] + "-" + w.id.split("-")[1] for w in P.ways if w.kind == "river"}
    assert "terrain-river0" in ids and not any(w.id == "river" for w in P.ways)     # its own, plus forks
    own = terrain_gen.river_line(A.terrain)
    f = city.Fields(A, h)
    pts = np.array(own.coords)
    mouth = pts[np.nonzero(f.inland(pts[:, 0], pts[:, 1]) <= 0)[0][0]]
    assert math.dist(mouth, A.city.nodes[0].center) < A.city.nodes[0].radius + 400  # downtown at its mouth


def test_classic_edge_is_irregular_not_round(metro):
    _, A, _, P, _ = metro
    assert edge_irregularity(P, city.main_centre(P.layout.city)) > 0.12


def test_classic_is_deterministic_per_seed():
    a, ha = shaped(on_terrain("coastal-plain", 3), "classic", 3)
    b, hb = shaped(on_terrain("coastal-plain", 3), "classic", 3)
    assert a.model_dump() == b.model_dump() and digest(city.plan(a, ha)) == digest(city.plan(b, hb))
    c, hc = shaped(on_terrain("coastal-plain", 3), "classic", 4)
    assert digest(city.plan(c, hc)) != digest(city.plan(a, ha))


def test_classic_bridges_are_level_decks():
    A, h = shaped(on_terrain("coastal-plain", 3), "classic", 3)
    spec = greybox.build_spec(A, h)
    P = city.plan(A, h)
    rivers = shapely.ops.unary_union([w.line.buffer(w.width / 2) for w in P.ways if w.kind == "river"])
    crossing = [w for w in P.ways if w.kind in ("avenue", "highway", "boulevard") and w.line.intersects(rivers)]
    assert crossing
    slots = {s["id"]: s for s in spec["slots"]}
    zs = []
    for w in crossing:
        sl = slots[f"way-{w.id}"]
        assert sl["type"] in ("avenue", "highway", "boulevard")                    # the usual slot types
        zs.append(len(sl["pieces"]))
    assert all(zs)


# --- classic, tuned: frontage, streets, downtown, edges, the river channel ------------------------

def per_km2(P, f, C, r0, r1) -> float:
    """Buildings per km2 of land between r0 and r1 from downtown."""
    x0, y0, x1, y1 = P.layout.city.bounds
    gx, gy = np.meshgrid(np.arange(x0, x1, 25.0), np.arange(y0, y1, 25.0))
    gx, gy = gx.ravel(), gy.ravel()
    gd = np.hypot(gx - C[0], gy - C[1])
    km2 = float(np.sum(f.land(gx, gy) & (gd >= r0) & (gd < r1))) * 625 / 1e6
    n = sum(1 for b in P.buildings if r0 <= math.dist((b.x, b.y), C) < r1)
    return n / km2


def test_classic_packs_continuous_frontage_in_the_inner_and_middle_rings(metro):
    _, A, _, P, f = metro
    C = city.main_centre(P.layout.city)
    assert per_km2(P, f, C, 500, 1500) > 800              # was 340-530 before the frontage (76d10c3's previews)
    housing = [b for b in P.buildings if b.kind == "housing"]
    assert np.median([b.w for b in housing]) < 12          # narrow lots side by side, not scattered blocks
    houses = [math.dist((b.x, b.y), C) for b in P.buildings if b.kind == "houses"]
    assert np.percentile(houses, 25) > np.median([math.dist((b.x, b.y), C) for b in housing])   # detached: the outer ring


def test_classic_streets_arterials_every_1600_m_local_every_200(metro):
    _, A, _, P, f = metro
    c = P.layout.city
    assert c.preset.grid_m == 1600
    n = city._dir(c.preset.grid_deg + 90)
    C = np.array(city.main_centre(c))
    offs = {round(float((np.array(w.line.interpolate(0.5, normalized=True).coords[0]) - C) @ n)) % 1600
            for w in P.ways if w.id.startswith("grid-a")}
    assert len(offs) == 1                                  # one family of lines, 1.6 km apart
    local = [w for w in P.ways if w.id.startswith("local-a")]
    o = sorted({round(float((np.array(w.line.interpolate(0.5, normalized=True).coords[0]) - C) @ n)) for w in local})
    gaps = np.diff(o)
    assert local and set(np.round(gaps / 100).astype(int)) <= set(range(1, 17)) and 100 in gaps.round(-1)   # 100 m downtown


def test_classic_downtown_scales_tallest_by_the_river_mouth_then_steps_down(metro):
    from asset_pipeline import city_metro
    _, A, _, P, f = metro
    c = P.layout.city
    sm = c.storey_m
    towers = [b for b in P.buildings if b.kind == "tower" and b.h > 12 * sm]
    assert len(towers) >= 150                              # 19-42 before (76d10c3's previews)
    pk = city_metro.peak(c, P.layout.terrain)
    dr = city_metro.downtown_r(c)
    top = sorted(towers, key=lambda b: -b.h)[:10]
    assert np.median([math.dist((b.x, b.y), pk) for b in top]) < 0.6 * dr
    C = city.main_centre(c)
    inner = [b.h for b in towers if math.dist((b.x, b.y), C) < dr]
    ring = [b.h for b in P.buildings if b.kind == "tower" and dr * 1.15 < math.dist((b.x, b.y), C) < dr * 1.75]
    assert ring and np.median(ring) < 0.7 * np.median(inner) and np.median(ring) > 6 * sm   # the mid-rise ring


def test_classic_cuts_dead_end_stubs_back_to_the_buildings(metro):
    from scipy.spatial import cKDTree
    from asset_pipeline import city_metro
    _, A, _, P, f = metro
    kd = cKDTree(np.array([[b.x, b.y] for b in P.buildings]))
    lines = [w.line for w in P.ways]
    for i, w in enumerate(P.ways):
        if not (w.kind == "street" or w.id.startswith("grid-")):
            continue
        for end in (w.line.coords[0], w.line.coords[-1]):
            if any(j != i and lines[j].distance(shapely.Point(end)) < 3 for j in range(len(lines))):
                continue
            assert kd.query(end)[0] < city_metro.STUB_M + 70, w.id
    C = city.main_centre(P.layout.city)
    edge = np.percentile([math.dist((b.x, b.y), C) for b in P.buildings], 90)
    assert any(w.id.startswith("diag") and math.dist(w.line.coords[-1], C) > edge for w in P.ways)   # diagonals run on out


def test_classic_river_is_a_lowered_blue_channel_with_bridges_at_street_level():
    A, h = shaped(on_terrain("coastal-plain", 3), "classic", 3)
    P = city.plan(A, h)
    spec = greybox.build_spec(A, h)
    hs = greybox.Heights(h, A.terrain.extent_m)
    slots = {s["id"]: s for s in spec["slots"]}
    river = next(w for w in P.ways if w.kind == "river" and w.id == "river")
    sl = slots[f"way-{river.id}"]
    p = river.line.interpolate(0.5, normalized=True)
    q = river.line.interpolate(0.51, normalized=True)
    d = np.array([q.x - p.x, q.y - p.y])
    n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
    bank = max(float(hs(*(np.array([p.x, p.y]) + s * (river.width / 2 + 30) * n))) for s in (-1, 1))
    pts = sl["pieces"][0]["prim"]["pts"]
    o = np.add(sl["loc"], sl["pieces"][0]["loc"])
    zs = [o[2] + v[2] for v in pts]
    assert zs and float(np.median(zs)) < bank - 1.5           # the water lies below the street level beside it
    crossing = [w for w in P.ways if w.kind == "avenue" and w.line.intersects(river.line)]
    assert crossing
    w = crossing[0]
    x = w.line.intersection(river.line)
    x = x if x.geom_type == "Point" else x.geoms[0]
    s = slots[f"way-{w.id}"]
    o, pts = np.add(s["loc"], s["pieces"][0]["loc"]), s["pieces"][0]["prim"]["pts"]
    deck = min(pts, key=lambda v: math.dist((o[0] + v[0], o[1] + v[1]), (x.x, x.y)))
    assert o[2] + deck[2] > float(hs(x.x, x.y)) + 2                # the bridge stays up at street level
    src = open(greybox.__file__.replace("asset_pipeline/greybox.py", "scripts/blender_greybox.py")).read()
    assert '"river": (0.07, 0.20, 0.32)' in src                    # previews colour it as water


# --- classic, third pass: downtown variety, back-to-back rows, the outer ring, the tower cap -------

def test_classic_downtown_mixes_block_types_and_heights(metro):
    from asset_pipeline import city_metro
    _, A, _, P, f = metro
    c = P.layout.city
    dr = city_metro.downtown_r(c)
    C = city.main_centre(c)
    tw = [b for b in P.buildings if b.kind == "tower" and math.dist((b.x, b.y), C) < dr]
    assert any(min(b.w, b.d) > 55 for b in tw)                     # full-block towers and podiums
    assert any(max(b.w, b.d) <= 31 and b.h > 20 * c.storey_m for b in tw)    # slim towers
    hs = np.array([b.h for b in tw if b.h > 6 * c.storey_m])
    assert hs.std() / hs.mean() > 0.35                             # heights vary strongly
    hi = c.preset.tower_storeys[0] * c.storey_m
    marks = [b for b in tw if b.h > 1.1 * hi]
    assert 1 <= len(marks) <= 0.1 * len(tw)                        # a few landmarks, most lower
    pk = city_metro.peak(c, P.layout.terrain)
    assert np.median([math.dist((b.x, b.y), pk) for b in marks]) < 0.6 * dr   # by the river mouth


def test_classic_middle_ring_is_two_rows_back_to_back(metro):
    _, A, _, P, f = metro
    C = city.main_centre(P.layout.city)
    mid = [b for b in P.buildings if b.kind == "housing" and city.TERRACE_D * 0.6 < float(f.density(b.x, b.y)) < city.CORE_D]
    deep = [b for b in mid if b.d >= 20]
    assert len(deep) > 0.6 * len(mid)                              # deep rows to the alley, not a thin perimeter


def test_classic_towers_are_capped_and_the_suburbs_spread(metro):
    from asset_pipeline import city_metro
    _, A, _, P, f = metro
    c = P.layout.city
    assert sum(1 for b in P.buildings if b.kind == "tower" and b.h > 12 * c.storey_m) <= city_metro.TOWER_CAP
    C = city.main_centre(c)
    houses = [math.dist((b.x, b.y), C) for b in P.buildings if b.kind == "houses"]
    assert np.percentile(houses, 90) > 1.4 * city_metro.scale(c)   # detached houses spread out before open land

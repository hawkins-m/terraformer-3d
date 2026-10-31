"""Layout presets (schema.LayoutPreset, layout_presets.py, city.py): "utopia" plans exactly as the
generator did before presets, every preset field changes the plan, the layout seed is the shape's
own, and a roll records and restores the preset."""
import hashlib
import math

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from asset_pipeline import catalog, city, city_roll, greybox, layout_presets
from asset_pipeline.project import ProjectStore, read_json, write_json
from asset_pipeline.schema import LayoutPreset, SiteLayout
from asset_pipeline.stages import sw_site
from test_city import forum_city, heights, hill_city, small_city, walled_city
from test_city_types import CATALOG


def digest(P) -> str:
    """Everything the plan places, rounded to the millimetre."""
    h = hashlib.sha256()
    r = lambda v: round(float(v), 3)  # noqa: E731
    for w in P.ways:
        h.update(f"{w.id}{w.kind}{w.width}{[tuple(map(r, p)) for p in w.line.coords]}".encode())
    for b in P.blocks:
        h.update(str([tuple(map(r, p)) for p in b.exterior.coords]).encode())
    for b in P.buildings:
        h.update(str([r(b.x), r(b.y), r(b.w), r(b.d), r(b.rot), r(b.h), b.kind, b.district]).encode())
    for b in P.typed:
        h.update(str([b.typology, r(b.x), r(b.y), b.storeys, b.district, len(b.parts)]).encode())
    for p in P.parks:
        h.update(str(r(p.area)).encode())
    for t in P.trees:
        h.update(str([r(v) if not isinstance(v, str) else v for v in t]).encode())
    for sq, st in P.markets:
        h.update(str([r(sq.area), len(st)]).encode())
    for w in P.walls:
        h.update(str([w.group, r(w.x), r(w.y)]).encode())
    return h.hexdigest()[:16]


def typed_city():
    return catalog.apply(small_city(trees=False), catalog.yaml.safe_load(CATALOG))


# digests of these layouts' plans made by the generator before presets (commit 82db3f2)
BEFORE = {"small": (small_city, "a67b52531d37c91c"), "walled": (walled_city, "8d8f35aef667967d"),
          "forum": (forum_city, "4799e463384935a4"), "hill": (hill_city, "d5916bd4b2246959"),
          "typed": (typed_city, "e53d12076bd9637b")}


@pytest.mark.parametrize("name", BEFORE)
@pytest.mark.golden
def test_utopia_plans_exactly_as_before_presets(name):
    make, before = BEFORE[name]
    L = make()
    assert L.city.preset == LayoutPreset() and L.city.preset.name == "utopia"
    h = heights(L)
    assert digest(city.plan(L, h)) == before
    # an existing project's layout.json (no preset keys) loads as utopia and plans the same
    old = L.model_dump(mode="json")
    del old["city"]["preset"], old["city"]["layout_seed"]
    again = SiteLayout.model_validate(old)
    assert again.city.preset.name == "utopia" and digest(city.plan(again, h)) == before
    # utopia's layout seed draws nothing: the same plan with another one
    other = L.model_copy(deep=True)
    other.city.layout_seed = 99
    assert digest(city.plan(other, h)) == before


def with_fields(L, **fields) -> SiteLayout:
    L = L.model_copy(deep=True)
    L.city.preset = L.city.preset.model_copy(update=fields)
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def plan_with(L, h, **fields):
    return city.plan(with_fields(L, **fields), h)


@pytest.fixture(scope="module")
def base():
    L = small_city()
    h = heights(L)
    return L, h, city.plan(L, h)


def test_every_named_preset_plans_a_city(base):
    L, h, _ = base
    for name in layout_presets.PRESETS:
        P = city.plan(layout_presets.apply(L, name), h)
        assert P.stats["buildings"] > 200, name
    with pytest.raises(ValueError, match="unknown layout preset"):
        layout_presets.get("nope")


def test_street_patterns(base):
    L, h, P0 = base
    radial = {w.id for w in P0.ways if w.kind == "avenue"}
    assert radial and all("-av" in i for i in radial)
    grid = plan_with(L, h, streets="grid")
    ids = {w.id for w in grid.ways}
    assert any(i.startswith("grid-a") for i in ids) and any(i.startswith("grid-b") for i in ids)
    assert not any("-av" in i or i.startswith("art-") for i in ids)       # no radials or arterials
    # grid avenues run on the grid's two axes
    for w in grid.ways:
        if w.id.startswith("grid-"):
            (x0, y0), (x1, y1) = w.line.coords[0], w.line.coords[-1]
            assert min(abs(x1 - x0), abs(y1 - y0)) < 1e-6
    ring = plan_with(L, h, streets="ring")
    assert {"ring0", "ring1", "ring2"} <= {w.id.split("-")[0] for w in ring.ways if w.kind == "ring"}
    linear = plan_with(L, h, streets="linear")
    assert any(w.id.startswith("spine") for w in linear.ways) and any(w.id.startswith("cross") for w in linear.ways)
    organic = plan_with(L, h, streets="organic")
    assert digest(organic) != digest(P0) and any("-av" in w.id for w in organic.ways)


def test_outlines_bound_the_city(base):
    L, h, P0 = base
    C = city.main_centre(L.city)
    R = 450.0
    for kind in ("circle", "blob", "strip"):
        P = plan_with(L, h, outline=kind, outline_m=R)
        assert P.stats["buildings"] > 50, kind
        f = city.Fields(P.layout, h)
        assert all(float(f.outline.sd(b.x, b.y)) > -20 for b in P.buildings), kind
        assert len(P.buildings) < len(P0.buildings)
    circle = plan_with(L, h, outline="circle", outline_m=R)
    assert max(math.dist((b.x, b.y), C) for b in circle.buildings) < R + 20


def test_rings_walls_moat_and_parks(base):
    L, h, P0 = base
    rings = plan_with(L, h, rings=2)
    assert {"ring0", "ring1"} <= {w.id.split("-")[0] for w in rings.ways}
    walled = plan_with(L, h, walls="wall_moat", outline="circle", outline_m=500)
    e = walled.layout.city.enclosure
    assert e is not None and e.radius == 500 and e.moat_w == 30 and walled.walls and walled.moat
    assert all(math.dist((b.x, b.y), e.center) < e.radius for b in walled.buildings)
    assert all(not q.interiors for q in walled.moat)
    assert all(math.dist(q.representative_point().coords[0], e.center) > e.radius for q in walled.moat)
    spec = greybox.build_spec(with_fields(L, walls="wall_moat", outline="circle", outline_m=500), h)
    assert {"wall", "gate", "moat"} <= {s["type"] for s in spec["slots"]}
    wall_only = plan_with(L, h, walls="wall")
    assert wall_only.walls and not wall_only.moat
    parks = plan_with(L, h, block_parks=0.3)
    assert len(parks.parks) > len(P0.parks) + 10 and len(parks.blocks) == len(P0.blocks)


def test_density_falloff_and_height_zoning(base):
    L, h, P0 = base
    nd = L.city.nodes[0]
    x = np.array([nd.center[0] + 500.0])
    y = np.array([nd.center[1] + 300.0])
    dens = {}
    for fo in ("gaussian", "linear", "flat"):
        lay = L.model_copy(deep=True)
        lay.city.preset = LayoutPreset(falloff=fo)
        dens[fo] = float(city.Fields(lay, h).density(x, y)[0])
    assert len({round(v, 4) for v in dens.values()}) == 3
    wide = L.model_copy(deep=True)
    wide.city.preset = LayoutPreset(falloff_scale=1.5)
    assert float(city.Fields(wide, h).density(x, y)[0]) > dens["gaussian"]
    # tallest at the waterfront: near the shore taller than inland
    P = plan_with(L, h, tall_at="waterfront", height_falloff_m=300)
    f = city.Fields(P.layout, h)
    near = [b.h for b in P.buildings if b.kind == "housing" and f.inland(b.x, b.y) < 300]
    far = [b.h for b in P.buildings if b.kind == "housing" and f.inland(b.x, b.y) > 700]
    assert near and far and np.median(near) > np.median(far)
    assert city.Fields(L, h).height_zone(x, y) is None                   # utopia: heights by density
    # the catalog follows the zoning too
    T = typed_city()
    Pt = plan_with(T, heights(T), tall_at="centre", height_falloff_m=250)
    C = city.main_centre(T.city)
    near = [b.storeys for b in Pt.typed if b.storeys and math.dist((b.x, b.y), C) < 350]
    far = [b.storeys for b in Pt.typed if b.storeys and math.dist((b.x, b.y), C) > 700]
    assert near and far and np.mean(near) > np.mean(far)


def test_highways_and_interchanges_build_as_flat_road_strips(base):
    L, h, P0 = base
    P = plan_with(L, h, highways="both", highway_spokes=3)
    hw = [w for w in P.ways if w.kind == "highway"]
    assert any(w.id.startswith("hw-ring") for w in hw) and any(w.id.startswith("hw-spoke") for w in hw)
    assert all(w.width == 36 for w in hw) and P.interchanges
    assert P.stats["highways"] == len(hw) and P.stats["interchanges"] == len(P.interchanges)
    assert not any(P.interchanges[0].contains(Point(b.x, b.y)) for b in P.buildings)
    spec = greybox.build_spec(with_fields(L, highways="both", highway_spokes=3), h)
    types = {s["type"] for s in spec["slots"]}
    assert {"highway", "interchange"} <= types
    for kind, n in (("ring", 1), ("radial", 0)):
        Q = plan_with(L, h, highways=kind)
        assert sum(w.id.startswith("hw-ring") for w in Q.ways) >= n
        assert any(w.id.startswith("hw-spoke") for w in Q.ways) == (kind == "radial")
    assert not plan_with(L, h, highways="both", interchanges=False).interchanges


def test_layout_seed_reshapes_only_the_preset():
    L = layout_presets.apply(small_city(), "organic")
    h = heights(L)
    a = city.plan(L, h)
    b = city.plan(layout_presets.apply(L, "organic", layout_seed=5), h)
    assert digest(a) != digest(b)
    assert digest(city.plan(L, h)) == digest(a)                         # still reproducible


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Preset Test", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    monkeypatch.setattr(sw_site, "plan_city", lambda store: {"buildings": 100, "warnings": []})
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    return s


def test_roll_keeps_records_and_restores_the_preset(store):
    city_roll.roll(store, seed=3)
    assert sw_site.load_layout(store).city.preset.name == "utopia"           # a roll keeps the layout's own
    city_roll.roll(store, seed=4, preset="grid", layout_seed=8)
    lay = sw_site.load_layout(store)
    assert (lay.city.preset.name, lay.city.layout_seed, lay.city.seed) == ("grid", 8, 4)
    assert read_json(city_roll.rolls_path(store))["rolls"][-1]["preset"]["streets"] == "grid"
    city_roll.step(store, -1)
    lay = sw_site.load_layout(store)
    assert (lay.city.preset.name, lay.city.layout_seed, lay.city.seed) == ("utopia", 0, 3)
    city_roll.set_preset(store, "ring-city")                             # a renamed preset still works
    lay = sw_site.load_layout(store)
    assert lay.city.preset.name == "concentric" and lay.city.seed == 3      # the rules and seed stay
    s = city_roll.summary(store)
    assert s["preset"] == "concentric" and {p["name"] for p in s["presets"]} == set(layout_presets.PRESETS)


# --- the twenty presets ---------------------------------------------------------------------

def _rect(x, y, w, d, rot):
    u, v = city._dir(rot) * w / 2, city._dir(rot + 90) * d / 2
    c = np.array([x, y])
    return Polygon([c - u - v, c + u - v, c + u + v, c - u + v])


def slot_overlaps(P, tol=2.0) -> list:
    """Overlapping slots in a plan (checked on its own, apart from city.clear_overlaps): a building
    footprint (housing, tower or shed box, catalog mass, stall, pier, wall bay) on a road, water
    or paving surface, or on another slot's footprint. Slots group as add_slots does (kind or
    typology per tile); gates span their roads by design."""
    T = P.layout.city.tile_m
    tile = lambda x, y: (math.floor(x / T), math.floor(y / T))  # noqa: E731
    fp = [((b.kind, tile(b.x, b.y)), _rect(b.x, b.y, b.w, b.d, b.rot)) for b in P.buildings]
    fp += [((b.typology, tile(b.x, b.y), b.material), _rect(p.x, p.y, p.w, p.d, p.rot))
           for b in P.typed for p in b.parts if p.role in ("mass", "ground", "podium", "base") and isinstance(p.prim, str)]
    fp += [(("market", i), _rect(b.x, b.y, b.w, b.d, b.rot)) for i, (_, st) in enumerate(P.markets) for b in st]
    fp += [(("quay",), _rect(b.x, b.y, b.w, b.d, b.rot)) for b in P.piers]
    fp += [((w.group,), _rect(w.x, w.y, w.prim["w"], w.prim["d"], w.rot)) for w in P.walls
           if w.prim.get("kind") in ("box", "archwall") and not w.piece.startswith("gate:")]
    surf = ([w.line.buffer(w.width / 2 - 0.5, cap_style="flat") for w in P.ways]
            + list(P.interchanges) + list(P.moat) + list(P.culdesacs))
    polys = [g for _, g in fp]
    tree, stree = STRtree(polys), STRtree(surf)
    bad = []
    for i, (k, g) in enumerate(fp):
        bad += [("surface", k) for j in stree.query(g) if g.intersection(surf[j]).area > tol]
        bad += [("slots", k, fp[j][0]) for j in tree.query(g) if j > i and fp[j][0] != k
                and g.intersection(polys[j]).area > tol]
    return bad


def test_twenty_presets_each_with_a_description():
    assert len(layout_presets.PRESETS) == 20
    assert {"utopia", "classic", "walled-castle", "star-fortress", "manhattan", "suburbia", "park-grid", "concentric",
            "canal-city", "hill-town", "harbour-port", "garden-city", "river-town", "island-city", "market-town",
            "cliff-town", "campus"} <= set(layout_presets.PRESETS)
    for p in layout_presets.summary():
        assert p["description"] and "\n" not in p["description"]
    for old, new in layout_presets.ALIASES.items():
        assert layout_presets.get(old).name == new


@pytest.mark.parametrize("name", [n for n in layout_presets.PRESETS if n != "utopia"])
def test_every_preset_plans_and_builds_with_no_overlapping_slots(base, name):
    """utopia is left out: it must plan exactly as before presets (existing projects, locked
    frames), overlaps included; every other preset clears them."""
    L, h, _ = base
    lay = layout_presets.apply(L, name)
    P = city.plan(lay, h)
    assert P.stats["buildings"] > 150, name
    assert slot_overlaps(P) == [], name
    spec = greybox.build_spec(lay, h)
    ids = [s["id"] for s in spec["slots"]]
    assert len(ids) == len(set(ids))


def test_utopia_still_has_its_old_overlaps(base):
    _, _, P0 = base
    assert slot_overlaps(P0)        # why clear_overlaps stays off for it


def test_classic_is_an_ordinary_modern_city():
    """A coastal metro (city_metro; the shape and its tests: test_classic_metro.py): towers round
    downtown, mid-rise past them, houses at the edge; highways in, none round; industry on the river."""
    st = sw_site.starter_city()
    h = city.apply_pads(st, greybox.make_terrain(st.terrain))
    lay = layout_presets.apply(st, "classic")
    pr = lay.city.preset
    assert pr.metro and pr.streets == "grid" and pr.highways == "radial" and pr.rings == 0 and pr.outline == "coastal"
    P = city.plan(lay, h)
    C = city.main_centre(P.layout.city)
    towers = [b for b in P.buildings if b.kind == "tower"]
    assert towers and max(b.h for b in towers) > 30 * P.layout.city.storey_m                # skyscrapers, over 100 m
    dist = lambda b: math.dist((b.x, b.y), C)  # noqa: E731
    houses = [b for b in P.buildings if b.kind == "houses"]
    assert houses and np.median([dist(b) for b in houses]) > np.median([dist(b) for b in towers]) + 500
    assert P.interchanges and not any(w.id.startswith(("hw-ring", "ring")) for w in P.ways)
    assert any(w.id.startswith("hw-spoke") for w in P.ways)
    sheds = [b for b in P.buildings if b.kind == "industrial"]
    river = unary_union([w.line for w in P.ways if w.kind == "river"])
    assert sheds and all(river.distance(Point(b.x, b.y)) < pr.industrial_m + 100 for b in sheds)


def test_the_presets_features(base):
    L, h, _ = base
    plans = {n: city.plan(layout_presets.apply(L, n), h) for n in
             ("star-fortress", "walled-castle", "canal-city", "river-town", "suburbia", "park-grid", "island-city",
              "market-town", "campus", "hill-town")}
    star = plans["star-fortress"]
    e = star.layout.city.enclosure
    assert e.shape == "star" and star.moat
    assert max(math.dist((w.x, w.y), e.center) for w in star.walls if w.piece == "wall:bay") > e.radius * 1.2
    castle = plans["walled-castle"]
    assert any(w.group.startswith("innerwall") for w in castle.walls) and any(w.group.startswith("wall") for w in castle.walls)
    assert castle.moat and {w.id.split("-")[0] for w in castle.ways} >= {"ring0", "ring1"}
    assert sum(w.kind == "canal" for w in plans["canal-city"].ways) >= 3
    assert any(w.kind == "river" for w in plans["river-town"].ways)
    sub = plans["suburbia"]
    assert sub.culdesacs and any(w.id.startswith("cds") for w in sub.ways)
    assert all(b.h <= 4 * L.city.storey_m + 40 for b in sub.buildings)
    assert len(plans["park-grid"].parks) > 30
    isl = plans["island-city"]
    f = city.Fields(isl.layout, city.apply_pads(isl.layout, greybox.make_terrain(isl.layout.terrain)))
    assert f.outline.kind == "island" and all(float(f.outline.sd(b.x, b.y)) > 0 for b in isl.buildings)
    assert {w.id for w in plans["market-town"].ways} >= {"cross0", "cross1"} or \
        any(w.id.startswith("cross0") for w in plans["market-town"].ways)
    assert plans["campus"].parks and max(b.h for b in plans["campus"].buildings) < 30
    st = sw_site.starter_city()                       # it has a harbour node
    port = city.plan(layout_presets.apply(st, "harbour-port"), city.apply_pads(st, greybox.make_terrain(st.terrain)))
    assert len(port.piers) == 7 and any(b.kind == "industrial" for b in port.buildings)
    assert all(n.layout == "organic" for n in plans["hill-town"].layout.city.nodes)


def test_island_terrain_sinks_round_the_island():
    L = layout_presets.apply(small_city(), "island-city")
    h = city.apply_pads(L, greybox.make_terrain(L.terrain))
    f = city.Fields(L, h)
    C = np.array(city.main_centre(L.city))
    o = f.outline
    d = np.array([1.0, 0.0])
    r = float(o.radius_at(0.0))
    strait = C + (r + 150) * d                    # the strait round it is sea ...
    beyond = C + (r + city.ISLAND_STRAIT_M + 400) * d   # ... the mainland beyond keeps its ground
    raw = greybox.Heights(greybox.make_terrain(L.terrain), L.terrain.extent_m)
    assert float(f.z(*strait)) < L.sea_level and float(f.z(*C)) > L.sea_level
    assert abs(float(f.z(*beyond)) - float(raw(*beyond))) < 0.5


def test_new_projects_start_on_classic_existing_keep_theirs(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="New City", start="city")
    assert sw_site.init(s, city_mode=True).city.preset.name == "classic"
    old = tmp_path / "old.json"
    old.write_text(sw_site.starter_city().model_dump_json())
    assert sw_site.init(s, layout_file=old, force=True).city.preset.name == "utopia"


def test_a_roll_can_pick_a_random_preset(store):
    r = city_roll.roll(store, seed=12, preset="random")
    lay = sw_site.load_layout(store)
    assert lay.city.preset.name == layout_presets.random_name(12) == read_json(
        city_roll.rolls_path(store))["rolls"][-1]["preset"]["name"]
    assert r["seed"] == 12
    assert len({layout_presets.random_name(s) for s in range(60)}) > 10
    with pytest.raises(ValueError, match="unknown layout preset"):
        city_roll.set_preset(store, "random")

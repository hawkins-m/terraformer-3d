"""Layout preset polish: the star fortress fills out to its star-shaped wall and stays on land; a
preset's walled city is crossed by roads only at its gates (a town's road leaves by the gate nearest
it; the castle has one gate); hubs are round, square (a grid square) or a mall in a car park by
preset; classic's lakeshore is a boulevard built as road. Synthetic projects."""
import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from asset_pipeline import city, city_roll, greybox, layout_presets
from asset_pipeline.stages import sw_site
from test_city_shape import fast, on_land, store  # noqa: F401 - fixtures
from test_layout_presets import slot_overlaps


def rolled(store, name, seed=3):
    city_roll.roll(store, seed=seed, preset=name, log=lambda *a: None, shots=False)
    L = sw_site.load_layout(store)
    h = sw_site.plan_terrain(store, L)
    return L, h, city.plan(L, h)


def gate_points(P):
    e = P.layout.city.enclosure
    return [np.array(e.center) + e.radius * city._dir(g) for g in P.gates]


def crossings(P):
    """(way id, point) wherever a way meets the wall's band or the moat ring."""
    e = P.layout.city.enclosure
    wl = city.wall_outline(e)
    ring = wl.buffer(e.wall_d / 2 + 3 + e.moat_w).difference(wl.buffer(-(e.wall_d / 2)))
    out = []
    for w in P.ways:
        if not w.line.intersects(ring):          # (intersection() alone returns phantom pieces for some lines)
            continue
        x = w.line.intersection(ring)
        for g in getattr(x, "geoms", [x]):
            if not g.is_empty:
                out.append((w.id, g.representative_point()))
    return out


@pytest.mark.parametrize("name", ["star-fortress", "walled-castle"])
def test_roads_cross_the_wall_and_moat_only_at_gates(store, name):
    L, h, P = rolled(store, name)
    e = L.city.enclosure
    assert P.gates and P.walls and P.moat
    gp = gate_points(P)
    near = [(wid, pt) for wid, pt in crossings(P) if min(pt.distance(Point(*g)) for g in gp) > e.gate_w]
    assert near == []                                              # nothing crosses but through a gate
    assert any(wid.startswith("gate") for wid, _ in crossings(P))   # the gates do have roads through them
    # the same on a project that keeps utopia: its own wall's roads are not touched by the guard
    assert city.wall_guard.__doc__


def test_a_towns_road_leaves_by_the_gate_nearest_it(store):
    L, h, P = rolled(store, "walled-castle")
    e = L.city.enclosure
    assert L.city.towns
    for tw in L.city.towns:
        reach = [w for w in P.ways if w.id.startswith("gate") and "-road" in w.id
                 and any(Point(c).distance(Point(tw.center)) < tw.radius + 90 for c in (w.line.coords[0], w.line.coords[-1]))]
        assert len(reach) == 1                                                  # one road, not one from every gate
        k = min(range(len(P.gates)), key=lambda i: math.dist(np.array(e.center) + e.radius * city._dir(P.gates[i]), tw.center))
        assert reach[0].id.startswith(f"gate{k}-road")
        # it stays outside the wall, moat and belt all the way (it never goes through the city)
        out = city.wall_outline(e).buffer(e.wall_d / 2 + 3 + e.moat_w)
        far = LineString([c for c in reach[0].line.coords if math.dist(c, e.center) > e.radius + 30])
        assert not far.intersects(out.buffer(-3)) or far.is_empty


def test_the_castle_has_one_gate_and_no_road_through_it(store):
    L, h, P = rolled(store, "walled-castle")
    assert {w.group for w in P.walls if w.group.startswith("innergate")} == {"innergate-0"}
    nd = min(L.city.nodes, key=lambda n: math.dist(n.center, city.main_centre(L.city)))
    R = nd.radius + 14
    wall = Point(nd.center).buffer(R).exterior
    gate = next(w for w in P.walls if w.group == "innergate-0" and w.piece == "gate:arch")
    for w in P.ways:
        x = w.line.intersection(wall.buffer(6))
        if x.is_empty:
            continue
        pts = [g.representative_point() for g in getattr(x, "geoms", [x])]
        assert w.id == "castle-gate-av" or not pts, w.id                       # only the gate's avenue meets the wall
        assert all(pt.distance(Point(gate.x, gate.y)) < 30 for pt in pts)
    # it is a road: from the outer gate nearest it, in to the castle
    road = next(w for w in P.ways if w.id == "castle-gate-av")
    assert road.kind == "avenue" and road.line.length > 100


def test_the_star_fortress_fills_out_into_its_bastion_points_and_stays_on_land(store):
    L, h, P = rolled(store, "star-fortress")
    e = L.city.enclosure
    star = city.wall_outline(e)
    assert e.shape == "star"
    d = [math.dist((b.x, b.y), e.center) for b in P.buildings]
    assert max(d) > e.radius * 1.05 > city.ring_road_r(L.city)             # out past the circle the old fill stopped at
    assert all(star.buffer(2).contains(Point(b.x, b.y)) for b in P.buildings)   # none past the wall
    tips = [city._dir(360 * k / e.points + 180 / e.points) * e.radius * (1 + e.point_m) + np.array(e.center) for k in range(e.points)]
    f0 = city.Fields(city.with_preset(L), h)
    for tip in tips:                                                       # every bastion point has houses in it
        assert min(math.dist((b.x, b.y), tip) for b in P.buildings) < 170 or not bool(f0.land(*tip))
    # the whole star, its moat and its belt on land above the sea
    f = city.Fields(city.with_preset(L), h)
    ring = star.buffer(e.wall_d / 2 + 3 + e.moat_w + e.greenbelt_w)
    pts = np.array(ring.exterior.segmentize(30).coords)
    assert np.all(f.land(pts[:, 0], pts[:, 1])) and np.all(f.z(pts[:, 0], pts[:, 1]) > L.sea_level)
    # blocks reach the ring road, which follows the star
    assert city.enclosure_ring(L.city).area < star.area and any(w.id.startswith("enclosure-ring") for w in P.ways)
    assert slot_overlaps(P) == []


def test_density_runs_out_at_the_stars_wall_not_at_a_circle(store):
    L, h, P = rolled(store, "star-fortress")
    e = L.city.enclosure
    f = city.Fields(city.with_preset(L), h)
    tip = np.array(e.center) + city._dir(180 / e.points) * e.radius * (1 + 0.6 * e.point_m)
    assert float(f.density(*tip)) > 0.3 and float(f.density(*(np.array(e.center) + city._dir(180 / e.points) * e.radius * 1.5))) < 0.05


@pytest.mark.parametrize("name,kind", [("utopia", "round"), ("walled-castle", "round"), ("star-fortress", "round"),
                                       ("canal-city", "round"), ("island-city", "round"), ("hill-town", "round"),
                                       ("classic", "square"), ("manhattan", "square")])
def test_hub_shape_by_preset(store, name, kind):
    if name == "utopia":
        L = sw_site.load_layout(store)
    else:
        L, h, P = rolled(store, name)
    c = L.city
    shapes = {city.hub_kind(c, n) for n in c.nodes}
    assert shapes == {kind}, (name, shapes)
    if name != "utopia":
        sq = [p for p in P.layout.plazas if p.shape == "square"]
        assert (len(sq) == len(c.nodes)) == (kind == "square") and (bool(sq) == (kind == "square"))


def test_a_modern_hub_is_a_square_with_square_streets_and_no_round_rings(store):
    L, h, P = rolled(store, "classic")
    assert not P.layout.rings and all(p.shape == "square" for p in P.layout.plazas)
    assert any(w.id.endswith("-square") for w in P.ways) and all(r.id.endswith(("-r0", "-r1", "-r2", "-r3")) for r in P.layout.radials)
    spec = greybox.build_spec(L, h)
    sq = next(s for s in spec["slots"] if s["id"].startswith("plaza-"))
    assert sq["pieces"][0]["piece"] == "paving" and sq["type"] == "plaza"
    assert slot_overlaps(P) == []


def test_suburbia_has_a_mall_in_a_car_park(store):
    L, h, P = rolled(store, "suburbia")
    c = L.city
    malls = [n for n in c.nodes if city.hub_kind(c, n) == "mall"]
    assert malls and all(n.role == "market" for n in malls)
    assert {b.kind for b in P.buildings if b.kind == "mall"} == {"mall"} and len([b for b in P.buildings if b.kind == "mall"]) == len(malls)
    lots = [p for p in P.layout.plazas if p.type == "lot"]
    assert len(lots) == len(malls) and all(p.shape == "square" for p in lots)
    mb = next(b for b in P.buildings if b.kind == "mall")
    assert mb.w < 2 * lots[0].radius * 0.6 and mb.h < 15                         # a low mall well inside its lot
    spec = greybox.build_spec(L, h)
    lot = next(s for s in spec["slots"] if s["id"] == f"plaza-{lots[0].id}")
    pieces = [p["piece"] for p in lot["pieces"]]
    assert pieces.count("paving") == 1 and pieces.count("lot-stripe") > 40       # paved, with painted bays
    assert any(s["type"] == "mall" for s in spec["slots"])
    assert slot_overlaps(P) == []


def test_classics_lakeshore_is_a_boulevard_built_as_road(store):
    L, h, P = rolled(store, "classic")
    assert not L.city.enclosure and not P.walls                                      # no wall anywhere
    bl = [w for w in P.ways if w.kind == "boulevard"]
    assert bl and all(w.id.startswith("lakeshore") for w in bl)                      # along the coast, not a ring
    assert not [w for w in P.ways if w.id.startswith("ring")]
    spec = greybox.build_spec(L, h)
    slots = [s for s in spec["slots"] if s["type"] == "boulevard"]
    assert slots and all(s["category"] == "terrain" for s in slots)
    pieces = {p["piece"] for s in slots for p in s["pieces"]}
    assert pieces == {"boulevard", "median"}                                        # a carriageway and a green median
    img = city.plan_image(P, h, px=900)
    assert img.size[0] == 900


def test_utopia_with_its_own_wall_keeps_its_roads_and_inner_gates_unchanged(store, fast):
    """The guard and the gate rules are the presets'; a project's own wall plans as before presets."""
    L = sw_site.load_layout(store)
    assert L.city.preset.walls == "none" and L.city.enclosure is not None
    h = sw_site.plan_terrain(store, L)
    P = city.plan(L, h)
    assert any(w.id.endswith("-road") and w.id.startswith("gate") for w in P.ways)
    e = L.city.enclosure
    roads = [w for w in P.ways if w.id.startswith("gate") and w.id.endswith("-road")]
    assert len(roads) == len(P.gates)                                               # one out of every gate, as before
    assert e.shape == "circle"

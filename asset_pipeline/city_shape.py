"""A layout preset's whole city shape, applied when it's picked on a roll (city_roll.roll):
the outline and its size, the wall, moat and green belt (or none) and its trace, the number,
names, roles and places of the districts (civic nodes) and their landmarks (monuments), the
satellite towns. The street pattern comes with the preset itself.

Everything is placed on the ground the plan will use (sw_site.plan_ground): every district's
core on land, above the sea and off steep ground, inside the bounds and the outline; the city
centre at the recipe's distance from the coast. The draws come from the roll's seed on their
own stream. "utopia" has no recipe: a roll keeps its old behaviour (rolled civic nodes, the
project's own wall, towns and civic sites).
"""
import math
from dataclasses import dataclass, field

import numpy as np

from . import city
from .schema import CivicNode, Enclosure, LayoutPreset, SiteLayout, Town

STREAM = 0x5A9E          # the shape's random stream: (this, seed)


@dataclass
class D:
    """A district: id, role, where (angle off straight inland, distance in half-bounds), core
    radius (m, at the starter city's scale), pull, landmark, layout, centre, civic ring."""
    id: str
    role: str = "local"
    angle: float = 0.0
    dist: float = 0.0
    r: float = 120.0
    weight: float = 1.0
    monument: str | None = None
    layout: str = "radial"
    centre: str = "auto"
    civic_ring: str = "arcade"
    stoa: float = 1.0
    place: str = "land"          # land: nearest good ground. high: the highest near there. coast: by the water


def plain(id, **kw):
    """A modern district: a small square, no civic ring of arcades, no stoas."""
    return D(id, centre="plaza", civic_ring="none", stoa=0.0, **kw)


@dataclass
class Recipe:
    inland: float                 # the centre's distance from the coast, x half-bounds (< 0: offshore, an island)
    outline_r: float | None       # the outline's radius / half-width, x half-bounds (None: the preset's own)
    districts: list[D]
    wall: dict | None = None      # Enclosure fields, `radius` x half-bounds
    towns: tuple = (0, 1.3, 250.0)   # how many, their distance x half-bounds, radius (m)
    town_names: list[str] = field(default_factory=list)


RECIPES: dict[str, Recipe] = {
    "classic": Recipe(0.55, 0.85, [
        plain("downtown", r=120, weight=1.6), plain("midtown", dist=0.35, r=90),
        plain("eastside", angle=70, dist=0.55, r=90, weight=0.8), plain("westside", angle=-70, dist=0.55, r=90, weight=0.8),
        plain("harbour", role="harbour", angle=180, dist=0.4, r=110, place="coast")],
        towns=(2, 1.25, 260.0), town_names=["eastfield", "westfield"]),
    "grid": Recipe(0.5, None, [
        plain("centre", r=130, weight=1.3), plain("north", dist=0.55), plain("east", angle=90, dist=0.5),
        plain("west", angle=-90, dist=0.5), plain("harbour", role="harbour", angle=180, dist=0.4, place="coast")]),
    "manhattan": Recipe(0.32, 0.28, [
        plain("midtown", r=100, weight=1.3), plain("downtown", angle=95, dist=0.9, r=100, weight=1.2),
        plain("uptown", angle=-95, dist=0.85, r=90)]),
    "park-grid": Recipe(0.5, None, [
        plain("centre", r=120, weight=1.2), plain("north", dist=0.5), plain("east", angle=90, dist=0.5),
        plain("west", angle=-90, dist=0.5)]),
    "suburbia": Recipe(0.6, 0.9, [
        plain("town-centre", role="market", r=100, weight=1.0), plain("mall", angle=80, dist=0.55, r=90, weight=0.6),
        plain("northside", dist=0.6, r=80, weight=0.5), plain("southside", angle=-80, dist=0.5, r=80, weight=0.5)],
        towns=(2, 1.3, 230.0), town_names=["oakridge", "maplewood"]),
    "garden-city": Recipe(0.6, 0.6, [
        D("centre", r=140, weight=1.1, centre="basins"), D("ward-north", dist=0.33, r=90, weight=0.7),
        D("ward-east", angle=120, dist=0.33, r=90, weight=0.7), D("ward-west", angle=-120, dist=0.33, r=90, weight=0.7)],
        towns=(3, 1.2, 220.0), town_names=["garden-north", "garden-east", "garden-west"]),
    "concentric": Recipe(0.75, 0.75, [
        D("heart", role="forum", r=220, weight=1.4, monument="rotunda"), D("north-ring", dist=0.45, r=100),
        D("east-ring", angle=90, dist=0.45, r=100), D("west-ring", angle=-90, dist=0.45, r=100),
        D("south-ring", angle=180, dist=0.4, r=100)]),
    "walled-castle": Recipe(0.6, 0.55, [
        D("castle", role="hill", r=150, weight=1.0, monument="temple", layout="organic", place="high"),
        D("market", role="market", angle=120, dist=0.3, r=110), D("lower-town", angle=-120, dist=0.3, r=100)],
        wall={"kind": "wall", "radius": 0.55, "moat_w": 30.0, "greenbelt_w": 100.0, "gate_count": 4},
        towns=(1, 1.25, 200.0), town_names=["village"]),
    "star-fortress": Recipe(0.6, 0.5, [
        plain("parade", r=130, weight=1.2), plain("north-quarter", angle=45, dist=0.26, r=80),
        plain("east-quarter", angle=135, dist=0.26, r=80), plain("west-quarter", angle=-45, dist=0.26, r=80),
        plain("south-quarter", angle=-135, dist=0.26, r=80)],
        wall={"kind": "wall", "radius": 0.45, "shape": "star", "points": 8, "point_m": 0.3, "moat_w": 35.0,
              "greenbelt_w": 80.0, "gate_count": 4}),
    "linear": Recipe(0.3, 0.3, [
        plain("centre", r=110, weight=1.2), plain("east", angle=92, dist=0.65), plain("west", angle=-92, dist=0.65),
        plain("far-east", angle=92, dist=1.2, r=90, weight=0.8), plain("far-west", angle=-92, dist=1.2, r=90, weight=0.8)]),
    "organic": Recipe(0.55, 0.7, [
        D("centre", role="forum", r=160, weight=1.2, monument="temple"), D("upper", angle=25, dist=0.5, r=110),
        D("east", angle=110, dist=0.5, r=100), D("west", angle=-100, dist=0.45, r=100)]),
    "canal-city": Recipe(0.45, 0.7, [
        D("piazza", role="market", r=130, weight=1.2), D("harbour", role="harbour", angle=180, dist=0.4, r=110, place="coast"),
        D("east", angle=80, dist=0.5, r=100), D("west", angle=-80, dist=0.5, r=100)]),
    "river-town": Recipe(0.55, 0.7, [
        D("centre", role="market", r=130, weight=1.1), D("east-bank", angle=90, dist=0.45, r=100),
        D("west-bank", angle=-90, dist=0.45, r=100)],
        towns=(1, 1.2, 220.0), town_names=["upstream"]),
    "harbour-port": Recipe(0.4, None, [
        plain("centre", r=120, weight=1.1), D("harbour", role="harbour", angle=150, dist=0.35, r=120, place="coast"),
        D("docks", role="harbour", angle=-150, dist=0.4, r=110, place="coast"), plain("uptown", dist=0.5, r=90)]),
    "island-city": Recipe(-0.85, 0.55, [
        D("centre", role="forum", r=170, weight=1.3, monument="temple"),
        D("harbour", role="harbour", angle=180, dist=0.35, r=110, place="coast"),
        D("east", angle=80, dist=0.3, r=90), D("west", angle=-80, dist=0.3, r=90)],
        towns=(1, 1.3, 220.0), town_names=["mainland"]),
    "hill-town": Recipe(0.7, 0.5, [
        D("citadel", role="hill", r=150, monument="temple", layout="organic", place="high"),
        D("upper-town", role="hill", angle=60, dist=0.4, r=120, layout="organic", place="high"),
        D("lower-town", role="hill", angle=-60, dist=0.4, r=120, layout="organic", place="high")],
        towns=(1, 1.2, 180.0), town_names=["valley"]),
    "cliff-town": Recipe(0.22, 0.25, [
        D("clifftop", role="hill", r=130, monument="temple", layout="organic", place="high"),
        D("east-terraces", role="hill", angle=92, dist=0.5, r=110, layout="organic", place="high"),
        D("west-terraces", role="hill", angle=-92, dist=0.5, r=110, layout="organic", place="high")]),
    "market-town": Recipe(0.6, None, [
        D("market", role="market", r=120, weight=1.0)],
        towns=(3, 0.95, 160.0), town_names=["northfold", "eastfold", "westfold"]),
    "campus": Recipe(0.6, 0.45, [
        D("quad", r=150, weight=1.0, monument="temple", centre="quad"), D("north-hall", dist=0.26, r=90, centre="quad"),
        D("east-hall", angle=100, dist=0.26, r=90, centre="quad"), D("west-hall", angle=-100, dist=0.26, r=90, centre="quad")]),
}


def has_recipe(name: str) -> bool:
    return name in RECIPES


def _probe(layout: SiteLayout, nodes: list[CivicNode], preset: LayoutPreset | None = None) -> SiteLayout:
    L = layout.model_copy(deep=True)
    c = L.city
    c.nodes, c.enclosure, c.towns, c.sites = nodes, None, [], []
    if preset is not None:
        c.preset = preset
    return L


def _search(f, target, r_node, ok, rng, reach, high=False):
    """The nearest point to target (or the highest within reach, `high`) where ok(p) holds."""
    cand = [np.array(target, float)]
    for rr in np.arange(30.0, reach, 30.0):
        n = max(8, int(2 * math.pi * rr / 60))
        a0 = rng.uniform(0, 2 * math.pi)
        cand += [np.array(target) + rr * np.array([math.cos(a0 + 2 * math.pi * k / n), math.sin(a0 + 2 * math.pi * k / n)])
                 for k in range(n)]
    good = [p for p in cand if ok(p)]
    if not good:
        return None
    if high:
        near = good[:max(1, len(good) // 3)]
        return max(near, key=lambda p: float(f.z(*p)))
    return good[0]


def shape(layout: SiteLayout, ground: np.ndarray, name: str, seed: int) -> SiteLayout:
    """The layout with the preset `name`'s whole shape, placed on `ground` (the plan's heights
    before the pads). Its preset must already be set. Returns a validated copy."""
    rec = RECIPES[name]
    rng = np.random.default_rng([STREAM, int(seed)])
    L = layout.model_copy(deep=True)
    c, t = L.city, L.terrain
    pr = c.preset
    x0, y0, x1, y1 = c.bounds
    Rb = 0.5 * min(x1 - x0, y1 - y0)
    k = float(np.clip(Rb / 1500.0, 0.5, 1.5))         # node sizes scale with the city
    if rec.outline_r is not None:
        pr.outline_m = round(rec.outline_r * Rb, 1)
    f0 = city.Fields(_probe(L, [], LayoutPreset()), ground)
    u, v = f0.u, f0.v
    sea = L.sea_level
    half = t.extent_m / 2 - 80
    inland_deg = math.degrees(math.atan2(-u[1], -u[0]))
    placed: list[tuple[np.ndarray, float]] = []

    def ok_on(f, outline=None):
        def ok(p, r):
            if not (x0 + r + 40 <= p[0] <= x1 - r - 40 and y0 + r + 40 <= p[1] <= y1 - r - 40):
                return False
            if any(math.dist(p, q) < r + rq + 90 for q, rq in placed):
                return False
            if outline is not None and float(outline.sd(*p)) < 0.7 * r:
                return False
            ring = [p] + [p + 0.7 * r * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
            xs, ys = np.array([q[0] for q in ring]), np.array([q[1] for q in ring])
            return bool(np.all(f.land(xs, ys)) and np.all(f.z(xs, ys) > sea + 3) and float(f.slope(*p)) < 0.3)
        return ok

    wall_fields = (dict(rec.wall) | {"radius": round(rec.wall["radius"] * Rb)}) if rec.wall else None

    def wall_fits(p) -> bool:
        """The whole wall (a star's tips too), its moat and green belt on land, above the sea."""
        if wall_fields is None:
            return True
        e = Enclosure(center=(float(p[0]), float(p[1])), **wall_fields)
        ring = city.wall_outline(e).buffer(e.wall_d / 2 + 3 + e.moat_w + e.greenbelt_w)
        pts = np.array(ring.exterior.segmentize(30).coords)
        return bool(np.all(f0.land(pts[:, 0], pts[:, 1])) and np.all(f0.z(pts[:, 0], pts[:, 1]) > sea + 3))

    # the centre: at the recipe's distance from the coast, near the middle of the bounds
    lat = float(np.array([(x0 + x1) / 2, (y0 + y1) / 2]) @ v) + rng.uniform(-0.12, 0.12) * (x1 - x0)
    along = float(f0._shore(lat)) - rec.inland * Rb
    target = along * u + lat * v
    d0 = rec.districts[0]
    r0 = d0.r * k
    if rec.inland < 0:             # an island: offshore, its own ground made round the centre; the bounds take it in
        R = pr.outline_m or Rb
        C = np.clip(target, -half + R + 100, half - R - 100)
        x0, y0 = max(min(x0, C[0] - R - 150), -half), max(min(y0, C[1] - R - 150), -half)
        x1, y1 = min(max(x1, C[0] + R + 150), half), min(max(y1, C[1] + R + 150), half)
        c.bounds = (round(x0), round(y0), round(x1), round(y1))
    else:
        C = _search(f0, target, r0, lambda p: ok_on(f0)(p, r0) and wall_fits(p), rng, 0.7 * Rb, high=d0.place == "high")
        if C is None:
            raise ValueError(f"no room for a {name} city on this terrain: its centre needs {r0:.0f} m of level land")
    # the preset's own ground (an island, a strip's water) round that centre, then the districts on it
    def node(d: D, p) -> CivicNode:
        return CivicNode(id=d.id, center=(round(float(p[0]), 1), round(float(p[1]), 1)), radius=round(d.r * k, 1),
                         role=d.role, monument=d.monument, weight=d.weight, layout=d.layout, centre=d.centre,
                         civic_ring=d.civic_ring, stoa_share=d.stoa, radials=int(rng.integers(5, 9)),
                         rot=round(float(rng.uniform(0, 45)), 1), spiral_deg_per_100m=round(float(rng.uniform(0, 2.5)), 1),
                         avenue_m=round(0.8 * Rb))
    nodes = [node(d0, C)]
    probe = _probe(L, list(nodes), pr)
    g2, _ = city.preset_terrain(probe, ground)
    f2 = city.Fields(probe, g2)
    ok2 = ok_on(f2, f2.outline)
    placed.append((C, r0))
    for d in rec.districts[1:]:
        r = d.r * k
        a = inland_deg + d.angle + rng.uniform(-10, 10)
        tgt = C + d.dist * Rb * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
        if d.place == "coast":     # pulled to the water's edge, the core still on land
            def okc(p, r=r):
                return ok2(p, r) and float(f2.inland(*p)) < r + 260
            p = _search(f2, tgt, r, okc, rng, 0.5 * Rb)
        else:
            p = _search(f2, tgt, r, lambda p, r=r: ok2(p, r), rng, 0.45 * Rb, high=d.place == "high")
        if p is None:
            continue                # no room: the city does without it
        nodes.append(node(d, p))
        placed.append((p, r))
    c.nodes = nodes
    # the wall, moat and green belt, or none
    if rec.wall:
        c.enclosure = Enclosure(center=(nodes[0].center[0], nodes[0].center[1]), **wall_fields)
        reach = city._wall_reach(c.enclosure) + 60      # the bounds take the whole wall, moat and belt in
        c.bounds = tuple(round(float(np.clip(b, -half, half))) for b in (
            min(c.bounds[0], c.enclosure.center[0] - reach), min(c.bounds[1], c.enclosure.center[1] - reach),
            max(c.bounds[2], c.enclosure.center[0] + reach), max(c.bounds[3], c.enclosure.center[1] + reach)))
        x0, y0, x1, y1 = c.bounds
    else:
        c.enclosure = None
    # satellite towns: out past the city, on land, inside the terrain
    reach = (c.enclosure.radius * (1 + (c.enclosure.point_m if c.enclosure.shape == "star" else 0))
             + c.enclosure.moat_w + c.enclosure.greenbelt_w) if c.enclosure else (pr.outline_m or 0.85 * Rb)
    n, dist, rt = rec.towns
    towns = []
    angles = [0.0] if n == 1 else list(np.linspace(-110, 110, n))
    for i, a in enumerate(angles):
        a = inland_deg + a + rng.uniform(-15, 15)
        tgt = C + max(dist * Rb, reach + rt + 150) * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])

        def okt(p):
            ring = [p + 0.6 * rt * np.array([math.cos(b), math.sin(b)]) for b in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
            xs, ys = np.array([q[0] for q in ring + [p]]), np.array([q[1] for q in ring + [p]])
            return (abs(p[0]) < half - rt and abs(p[1]) < half - rt and math.dist(p, C) > reach + rt + 100
                    and all(math.dist(p, q) > rq + rt + 150 for q, rq in placed)
                    and bool(np.all(f2.land(xs, ys)) and np.all(f2.z(xs, ys) > sea + 3)))
        p = _search(f2, tgt, rt, okt, rng, 0.6 * Rb)
        if p is None:
            continue
        name_ = rec.town_names[i] if i < len(rec.town_names) else f"town-{i + 1}"
        towns.append(Town(id=name_, center=(round(float(p[0]), 1), round(float(p[1]), 1)), radius=rt))
        placed.append((p, rt))
    c.towns = towns
    if towns:                      # the bounds take the towns in
        bx = [x0, y0, x1, y1]
        for tw in towns:
            bx = [min(bx[0], tw.center[0] - tw.radius - 80), min(bx[1], tw.center[1] - tw.radius - 80),
                  max(bx[2], tw.center[0] + tw.radius + 80), max(bx[3], tw.center[1] + tw.radius + 80)]
        c.bounds = tuple(round(float(np.clip(b, -half, half))) for b in bx)
    c.sites = []                   # the project's own civic ensembles were placed for its old shape
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def fit_nodes(layout: SiteLayout, ground: np.ndarray) -> SiteLayout:
    """Move any district whose core isn't on land (in the sea, on a steep cliff) to the nearest
    spot where it is, clear of the others; the rest stay exactly where they are. For rolls
    without a shape recipe (utopia), whose rules place districts by the rolled coast."""
    c = layout.city
    f = city.Fields(_probe(layout, [], LayoutPreset()), ground)
    sea = layout.sea_level
    x0, y0, x1, y1 = c.bounds

    def ok(p, r, others):
        ring = [p] + [p + 0.7 * r * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
        xs, ys = np.array([q[0] for q in ring]), np.array([q[1] for q in ring])
        return (x0 + r <= p[0] <= x1 - r and y0 + r <= p[1] <= y1 - r
                and all(math.dist(p, o.center) > r + o.radius + 90 for o in others)
                and bool(np.all(f.land(xs, ys)) and np.all(f.z(xs, ys) > sea + 3)))
    moved = False
    nodes = list(c.nodes)
    rng = np.random.default_rng([STREAM, c.seed, 1])
    for i, nd in enumerate(nodes):
        others = nodes[:i] + nodes[i + 1:]
        if ok(np.array(nd.center), nd.radius, others):
            continue
        p = _search(f, nd.center, nd.radius, lambda p: ok(p, nd.radius, others), rng, 0.6 * min(x1 - x0, y1 - y0))
        if p is not None:
            nodes[i] = nd.model_copy(update={"center": (round(float(p[0]), 1), round(float(p[1]), 1))})
            moved = True
    if not moved:
        return layout
    L = layout.model_copy(deep=True)
    L.city.nodes = nodes
    return SiteLayout.model_validate(L.model_dump(mode="json"))

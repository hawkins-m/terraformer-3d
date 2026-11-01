"""The background region round a city (CitySpec.background): open country out to about REACH x the
city's size, cheap to build, export and draw. Nothing in it is unique per building: forest and
hedges are one dome stand-in instanced, houses and barns one unit box with a gable, fields flat
draped polygons merged per slot (the greybox merges draped pieces), roads and rivers draped ribbons.

    far terrain  a coarse heightfield (far.npz, the format of terrain_build's far ring) from the
                 project's own terrain generator over the whole region (the coast, its river and
                 its rise continue), equal to the tile at the tile's edge, its own noise fading in
                 past it, and hills rising to the horizon on land
    the edge     it starts at the city's own irregular edge (its blocks, parks, buildings, streets
                 and trees on a GRID_M raster), not at its bounds: scattered houses and small farms
                 within about FRINGE_M of it, farmland and woodland out to about BELT_M, then mostly
                 forest and hills (the bands wobble with a noise, so no ring shows)
    highways     the plan's highways carried on straight out to the region's edge, on land
    rivers       the plan's rivers carried on upstream (they flow from their first point), meandering
    villages     every VILLAGE_EVERY_M along those highways past the fringe: a main street off the
                 highway with a few side streets, houses along them and a hall
    lanes        country roads off the highways and out of the city's edge, wandering, branching,
                 ending where they meet another road; farmsteads (a house, a barn or two) along them
    farmland     estates (Voronoi cells) of strip fields lined up with the nearest road or river,
                 a row now and then turned across, mixed widths; hedges or tree lines between some
    forest       woodland between the farms, nearly all of the land past the belt, every slope too
                 steep to farm (denser there): clumps on a jittered lattice

Everything is drawn from its own stream (STREAM, the city seed, the layout seed), never the city
plan's, so turning it on or off moves nothing in the city. The greybox (build_spec), the 3D viewer
(city_view) and the background map (plan_image) all read plan(); a city without the flag never calls it.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box as shp_box
from shapely.ops import unary_union

from .schema import SiteLayout

STREAM = 0xB4C6
REACH = 3.0                 # the region is this x the city's size across
MIN_RING_M = 3000.0         # at least this much country past the tile's edge
CELL_M = 30.0               # the far terrain's spacing, at least (else the region over ~1200 samples)
EDGE_BLEND_M = 1500.0       # past the tile's edge, the far field's own noise and hills fade in over this
HILL_M = 260.0              # hills to the horizon: up to this high at the region's edge (on land)
GRID_M = 20.0               # the land-use raster (distance from the city's edge)
CITY_CLEAR_M = 25.0         # nothing within this of the city's blocks, buildings, streets and trees
FRINGE_M = 900.0            # scattered houses and small farms out to about this past the city's edge
BELT_M = 4200.0             # farmland and woodland out to about this; past it mostly forest
EDGE_WOBBLE = 0.35          # the bands' distances vary by this share (a noise), so no ring shows
FOREST_STEP_M = 90.0        # forest clumps on a jittered lattice this far apart, at least ...
FOREST_MAX_N = 450          # ... or the region over this many per side, whichever is coarser
CLUMP_W = (55.0, 85.0)      # a clump's width and height: overlapping blobs read as woodland from afar
CLUMP_H = (16.0, 26.0)
STEEP = 0.2                 # slopes past this get a second lattice of clumps: denser forest on hills
ESTATE_M = 650.0            # farm estates (Voronoi cells of seeds this far apart), each lined up its own way
ROW_D = (220.0, 520.0)      # a row of strips this deep (small farms: SMALL_ROW_D) ...
SMALL_ROW_D = (110.0, 240.0)
STRIP_W = ((28.0, 55.0), (55.0, 105.0), (105.0, 170.0))   # ... strips of these widths (mixed)
STRIP_P = (0.4, 0.42, 0.18)
SMALL_STRIP_W = (20.0, 60.0)
CROSS_P = 0.22              # a row's strips turned to run across the others
FIELD_GAP_M = 6.0           # lanes and hedges between fields
FARM_SLOPE = 0.12           # steeper than this: forest, not fields
FARM_NEAR_M = 1500.0        # past the belt, fields only this near a road or river (in clearings)
HEDGE_P, TREELINE_P = 0.35, 0.1   # a field's long side has a hedge / a line of trees
HEDGE_SEG_M = 220.0
VILLAGE_EVERY_M = (3500.0, 6000.0)
VILLAGE_FROM_M = 1500.0     # the first village this far past the city's edge
MAIN_STREET_M = (380.0, 650.0)
SIDE_STREETS = (2, 5)
LANE_EVERY_M = (900.0, 1900.0)    # lanes leave the highways this far apart (and the city's edge ~EDGE_LANE_M)
EDGE_LANE_M = 900.0
LANE_LEN_M = (1500.0, 4000.0)
LANE_W, STREET_W, MAIN_W = 5.0, 6.0, 8.0
FARM_EVERY_M = (380.0, 900.0)     # farmsteads along the lanes and highways
HIGHWAY_W = 30.0
GROUP_M = 2000.0            # background slots are grouped per tile this big ...
FIELD_GROUP_M = 3000.0      # ... fields and lanes per bigger tiles: one merged mesh each in Unreal
LANE_GROUP_M = 4000.0


@dataclass
class Background:
    far: dict                                   # x0, y0, cell, h (rows south -> north), skirt_m
    half: float                                 # the region's half extent (m), round the origin
    ways: list = field(default_factory=list)    # (id, kind, LineString, width): highways and rivers
    fields: list = field(default_factory=list)  # (Polygon, crop: bool)
    forest: list = field(default_factory=list)  # (x, y, w, h)
    villages: list = field(default_factory=list)   # [(x, y, w, d, rot, h)] per village
    lanes: list = field(default_factory=list)   # (id, kind, LineString, width): lanes, village streets
    hedges: list = field(default_factory=list)  # (x, y, length, width, height, rot)
    farms: list = field(default_factory=list)   # (x, y, w, d, rot, h, kind): farmhouses, barns, houses

    def z(self, x, y) -> np.ndarray:
        """The far terrain's height, bilinear (inside the tile: the tile's, resampled)."""
        f = self.far
        h, x0, y0, cell = f["h"], f["x0"], f["y0"], f["cell"]
        n, m = h.shape
        fx = np.clip((np.asarray(x, float) - x0) / cell, 0, m - 1)
        fy = np.clip((np.asarray(y, float) - y0) / cell, 0, n - 1)
        i, j = np.minimum(np.floor(fy).astype(int), n - 2), np.minimum(np.floor(fx).astype(int), m - 2)
        a, b = fy - i, fx - j
        return (h[i, j] * (1 - a) * (1 - b) + h[i, j + 1] * (1 - a) * b + h[i + 1, j] * a * (1 - b)
                + h[i + 1, j + 1] * a * b)

    def slope(self, x, y, e: float = 40.0):
        return np.hypot(self.z(np.asarray(x) + e, y) - self.z(np.asarray(x) - e, y),
                        self.z(x, np.asarray(y) + e) - self.z(x, np.asarray(y) - e)) / (2 * e)

    def counts(self) -> dict:
        return {"forest_clumps": len(self.forest), "fields": len(self.fields), "villages": len(self.villages),
                "village_houses": sum(len(v) for v in self.villages), "ways": len(self.ways),
                "lanes": sum(1 for ln in self.lanes if ln[1] == "lane"), "village_streets": sum(1 for ln in self.lanes if ln[1] == "street"),
                "hedges": len(self.hedges), "farm_buildings": len(self.farms),
                "far_cells": int(self.far["h"].size), "extent_m": round(2 * self.half)}


def _smooth(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _waves(rng, k: int, wl: tuple[float, float]):
    """A smooth noise in about [-1, 1]: k plane waves of these wavelengths, random headings and phases."""
    a = rng.uniform(0, 2 * math.pi, k)
    lam = rng.uniform(*wl, k)
    ph = rng.uniform(0, 2 * math.pi, k)
    kx, ky = 2 * math.pi * np.cos(a) / lam, 2 * math.pi * np.sin(a) / lam

    def f(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return sum(np.sin(kx[i] * x + ky[i] * y + ph[i]) for i in range(k)) / math.sqrt(k / 2)
    return f


def region_half(layout: SiteLayout) -> float:
    from . import city_size
    c, t = layout.city, layout.terrain
    return max(REACH * city_size.across(c.bounds) / 2, t.extent_m / 2 + MIN_RING_M)


def far_terrain(layout: SiteLayout, tile: np.ndarray, rng) -> dict:
    """The region's heights (see the module docstring), on a grid whose lines meet the tile's edge."""
    from . import greybox, terrain_gen
    t = layout.terrain
    core = t.extent_m / 2
    want = region_half(layout)
    cell = max(CELL_M, 2 * want / 1200)
    cell = core / max(1, round(core / cell))           # whole cells from the tile's edge
    half = core + math.ceil((want - core) / cell) * cell
    n = int(round(2 * half / cell)) + 1
    big = t.model_copy(update={"extent_m": 2 * half, "resolution": n, "heightmap": None, "far": None})
    base = (terrain_gen.make(big) if big.settings is not None else greybox.make_terrain(big)).astype(np.float64)[::-1]
    s = np.linspace(-half, half, n)
    X, Y = np.meshgrid(s, s)                            # rows south -> north
    hs = greybox.Heights(tile, t.extent_m)
    at_edge = hs.many(np.clip(X, -core, core), np.clip(Y, -core, core))
    out = np.maximum(np.abs(X), np.abs(Y)) - core
    w = _smooth(0, EDGE_BLEND_M, out)
    h = at_edge * (1 - w) + base * w
    sea = layout.sea_level
    land = _smooth(sea + 1, sea + 15, h)
    hills = _waves(rng, 6, (2500, 9000))
    reach = _smooth(core, half, np.maximum(np.abs(X), np.abs(Y)))
    h = h + HILL_M * reach ** 1.3 * (0.55 + 0.45 * hills(X, Y)) * land
    return {"x0": -half, "y0": -half, "cell": cell, "h": h.astype(np.float32), "skirt_m": 60.0}


def _out_of(L: LineString, end, half: float, step: float = 50.0) -> np.ndarray:
    """Straight on from a line's end (0: its first point, -1: its last) to the region's edge."""
    pts = np.asarray(L.coords)
    if end == 0:
        pts = pts[::-1]
    tail = pts[-1]
    k = len(pts) - 1
    while k > 0 and math.dist(pts[k], tail) < 200:
        k -= 1
    d = tail - pts[k]
    d = d / max(float(np.linalg.norm(d)), 1e-9)
    run = np.arange(step, 4 * half, step)
    ext = tail + np.outer(run, d)
    inside = np.all(np.abs(ext) < half - 100, axis=1)
    stop = int(np.argmin(inside)) if not inside.all() else len(ext)
    return np.vstack([tail, ext[:stop]])


def _meander(pts: np.ndarray, rng, amp, wl) -> np.ndarray:
    if len(pts) < 3:
        return pts
    d = pts[-1] - pts[0]
    d = d / max(float(np.linalg.norm(d)), 1e-9)
    n = np.array([-d[1], d[0]])
    s = np.linalg.norm(pts - pts[0], axis=1)
    ph = rng.uniform(0, 2 * math.pi)
    off = amp * np.sin(2 * math.pi * s / wl + ph) * _smooth(0, 800, s)      # starts straight off the plan's end
    return pts + np.outer(off, n)


def _on_land(B: Background, pts: np.ndarray, sea: float) -> np.ndarray:
    """The run from the first point while it stays on land."""
    z = B.z(pts[:, 0], pts[:, 1])
    wet = np.nonzero(z < sea + 1.0)[0]
    return pts[:wet[0]] if len(wet) else pts


class _Edge:
    """The city on a GRID_M raster over the region: how far (m) each point is from its built edge
    (blocks, parks, markets, buildings, the walls' outline) and from anything of it at all (also its
    streets, trees and civic cores). Highways and rivers aren't in it: they run on into the country."""

    def __init__(self, P, half: float):
        from PIL import Image, ImageDraw
        from scipy import ndimage
        n = int(math.ceil(2 * half / GRID_M)) + 1
        self.n, self.half = n, half
        built, busy = Image.new("L", (n, n), 0), Image.new("L", (n, n), 0)
        db, du = ImageDraw.Draw(built), ImageDraw.Draw(busy)
        px = lambda x, y: ((x + half) / GRID_M, (half - y) / GRID_M)  # noqa: E731

        def fill(g, draws):
            for q in getattr(g, "geoms", [g]):
                if isinstance(q, Polygon) and not q.is_empty:
                    for d in draws:
                        d.polygon([px(*p) for p in q.exterior.coords], fill=255)

        c = P.layout.city
        for q in list(P.blocks) + list(P.parks) + list(P.interchanges) + list(P.culdesacs) + list(P.moat) + [m for m, _ in P.markets]:
            fill(q, (db, du))
        if c.enclosure is not None:
            from .city import wall_outline
            fill(wall_outline(c.enclosure), (db, du))
        rects = [(b.x, b.y, b.w, b.d, b.rot) for b in list(P.buildings) + list(P.piers)]
        rects += [(p[0], p[1], p[2], p[3], p[4]) for p in P.pads]
        for x, y, w, d, rot in rects:
            a = math.radians(rot)
            ca, sa = math.cos(a), math.sin(a)
            fill(Polygon([(x + u * ca - v * sa, y + u * sa + v * ca)
                          for u, v in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))]), (db, du))
        L = P.layout
        for ring in L.rings:
            r = (ring.radius + ring.width) / GRID_M
            cx, cy = px(*ring.center)
            for d in (db, du):
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        for pz in L.plazas:
            r = pz.radius / GRID_M + 1
            cx, cy = px(*pz.center)
            for d in (db, du):
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        for p in L.plots:
            cx, cy = px(*p.center)
            r = max(p.size[0], p.size[1]) / 2 / GRID_M + 1
            for d in (db, du):
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        for w in P.ways:
            if w.kind not in ("highway", "river"):
                du.line([px(*p) for p in w.line.coords], fill=255, width=max(1, int(round(w.width / GRID_M))) + 1)
        for t in P.trees:
            cx, cy = px(t[0], t[1])
            du.point((cx, cy), fill=255)
        self.built = ndimage.distance_transform_edt(np.asarray(built) == 0).astype(np.float32) * GRID_M
        self.busy = ndimage.distance_transform_edt(np.asarray(busy) == 0).astype(np.float32) * GRID_M

    def _ij(self, x, y):
        j = np.clip(np.rint((np.asarray(x, float) + self.half) / GRID_M).astype(int), 0, self.n - 1)
        i = np.clip(np.rint((self.half - np.asarray(y, float)) / GRID_M).astype(int), 0, self.n - 1)
        return i, j

    def d_built(self, x, y):
        return self.built[self._ij(x, y)]

    def d_busy(self, x, y):
        return self.busy[self._ij(x, y)]

    def outward(self, x: float, y: float) -> float:
        """The heading (deg) away from the city's edge at a point."""
        i, j = self._ij(x, y)
        i, j = int(np.clip(i, 1, self.n - 2)), int(np.clip(j, 1, self.n - 2))
        gx = float(self.built[i, j + 1] - self.built[i, j - 1])
        gy = float(self.built[i - 1, j] - self.built[i + 1, j])      # rows run north -> south
        return math.degrees(math.atan2(gy, gx))


class _Keep:
    """Shapes the country keeps off (roads, water, villages, yards), searchable by area."""

    def __init__(self):
        self.parts: list = []
        self._tree = None

    def add(self, g) -> None:
        if not g.is_empty:
            self.parts.append(g)
            self._tree = None

    def tree(self):
        if self._tree is None:
            self._tree = shapely.STRtree(self.parts)
        return self._tree

    def near(self, g) -> list:
        return [self.parts[i] for i in self.tree().query(g)] if self.parts else []

    def hits(self, g) -> bool:
        return any(p.intersects(g) for p in self.near(g))

    def hits_xy(self, x, y) -> np.ndarray:
        """Which points lie in a shape (vectorised)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        out = np.zeros(x.shape, bool)
        if not self.parts or not x.size:
            return out
        pts = shapely.points(x, y)
        pi, gi = self.tree().query(pts, predicate="intersects")
        out[np.unique(pi)] = True
        return out


def _heading(ln: LineString, s: float) -> float:
    a = ln.interpolate(max(0.0, s - 15)).coords[0]
    b = ln.interpolate(min(ln.length, s + 15)).coords[0]
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def plan(layout: SiteLayout, P, tile: np.ndarray) -> Background:
    """The background region of a planned city (P: city.plan's CityPlan, tile: the greybox heights)."""
    c = layout.city
    rng = np.random.default_rng([STREAM, int(c.seed), int(c.layout_seed)])
    sea = layout.sea_level
    B = Background(far=far_terrain(layout, tile, rng), half=0.0)
    B.half = -B.far["x0"]
    half = B.half
    E = _Edge(P, half)
    # highways and rivers, carried on from the plan
    for w in P.ways:
        if w.kind == "highway":
            a, b = np.asarray(w.line.coords[0]), np.asarray(w.line.coords[-1])
            end = 0 if np.hypot(*a) > np.hypot(*b) else -1        # the end farther out
            pts = _on_land(B, _out_of(w.line, end, half), sea)
            if len(pts) > 4:
                B.ways.append((f"bg-{w.id}", "highway", LineString(pts), HIGHWAY_W))
        elif w.kind == "river":
            head = Point(w.line.coords[0])
            if any(o is not w and o.kind == "river" and o.line.distance(head) < 100 for o in P.ways):
                continue                                        # it starts at a fork, not a source
            pts = _meander(_out_of(w.line, 0, half), rng, rng.uniform(150, 320), rng.uniform(1600, 2800))
            pts = pts[np.all(np.abs(pts) < half - 100, axis=1)]
            if len(pts) > 4:                                    # flowing down: from the region's edge to the plan's river
                B.ways.append((f"bg-{w.id}", "river", LineString(pts[::-1]), max(25.0, 0.8 * w.width)))
    # every highway and river out in the country: the background's and the plan's own past its edge
    hw = [ln for _, k, ln, _ in B.ways if k == "highway"] + [w.line for w in P.ways if w.kind == "highway"]
    rivers = [(ln, wd) for _, k, ln, wd in B.ways if k == "river"] + [(w.line, w.width) for w in P.ways if w.kind == "river"]
    K = _Keep()
    for ln in hw:
        K.add(ln.buffer(HIGHWAY_W / 2 + 12))
    for ln, wd in rivers:
        K.add(ln.buffer(wd / 2 + 30))
    wet = unary_union([ln.buffer(wd / 2 + 30) for ln, wd in rivers]) if rivers else Polygon()
    shapely.prepare(wet)
    hw_area = unary_union([ln.buffer(HIGHWAY_W / 2 + 12) for ln in hw]) if hw else Polygon()
    shapely.prepare(hw_area)
    zone = _waves(rng, 8, (3000, 9000))
    villages = _villages(B, rng, E, K, hw, wet, sea)
    _lanes(B, rng, E, K, hw, wet, hw_area, sea, villages)
    _farmsteads(B, rng, E, K, hw, sea)
    _fields(B, rng, E, K, zone, hw, rivers, P, sea)
    _forest(B, rng, E, K, zone, sea)
    return B


def _ok_ground(B, E, x, y, sea, clear: float = CITY_CLEAR_M, slope: float = 0.25) -> bool:
    return (abs(x) < B.half - 150 and abs(y) < B.half - 150 and float(B.z(x, y)) > sea + 1.5
            and float(E.d_busy(x, y)) > clear and float(B.slope(x, y)) < slope)


def _houses_along(B, rng, E, K, ln: LineString, step, setback, size, p, s0=0.0, s1=None, sea=0.0, taken=None):
    """Houses facing a street, both sides, every `step` m with chance p: [(x, y, w, d, rot, h)]."""
    out = []
    taken = taken if taken is not None else []
    s, s1 = s0 + rng.uniform(*step) / 2, ln.length if s1 is None else s1
    while s < s1:
        a = _heading(ln, s)
        p0 = np.asarray(ln.interpolate(s).coords[0])
        n = np.array([-math.sin(math.radians(a)), math.cos(math.radians(a))])
        for side in (-1, 1):
            if rng.random() > p:
                continue
            w, d = rng.uniform(*size[0]), rng.uniform(*size[1])
            q = p0 + side * n * (rng.uniform(*setback) + d / 2)
            x, y = float(q[0]), float(q[1])
            if not _ok_ground(B, E, x, y, sea) or K.hits(Point(x, y).buffer(d / 2)):
                continue
            if any(abs(x - o[0]) < 13 and abs(y - o[1]) < 13 for o in taken[-60:]):
                continue
            h = (round(x, 2), round(y, 2), round(w, 2), round(d, 2), round(a + rng.uniform(-4, 4), 2), round(rng.uniform(5, 8.5), 2))
            out.append(h)
            taken.append(h)
        s += rng.uniform(*step)
    return out


def _villages(B, rng, E, K, hw, wet, sea) -> list:
    """Villages along the highways past the fringe: a main street off the highway, side streets
    across it, houses along both and a hall on the main street. Returns their areas."""
    areas = []
    for ln in hw:
        s = rng.uniform(*VILLAGE_EVERY_M) * 0.5
        while s < ln.length:
            p = np.asarray(ln.interpolate(s).coords[0])
            if float(E.d_built(*p)) < VILLAGE_FROM_M or any(a.distance(Point(*p)) < 600 for a in areas):
                s += 400.0
                continue
            a = _heading(ln, s)
            side = rng.choice([-1, 1])
            out = math.radians(a + side * 90 + rng.uniform(-12, 12))
            d = np.array([math.cos(out), math.sin(out)])
            start = p + d * (HIGHWAY_W / 2 + 4)
            run = np.arange(0, rng.uniform(*MAIN_STREET_M), 25.0)
            pts = start + np.outer(run, d)
            ok = [_ok_ground(B, E, *q, sea, clear=VILLAGE_FROM_M / 2) and not wet.contains(Point(*q)) for q in pts]
            k = ok.index(False) if False in ok else len(ok)
            if k * 25 >= 280:
                streets = [LineString(pts[:k])]
                main, used = streets[0], []
                for t in np.sort(rng.uniform(110, main.length - 50, rng.integers(*SIDE_STREETS))):
                    if any(abs(t - u) < 70 for u in used):
                        continue
                    used.append(t)
                    c0 = np.asarray(main.interpolate(t).coords[0])
                    ang = math.radians(math.degrees(out) + 90 + rng.uniform(-8, 8))
                    e = np.array([math.cos(ang), math.sin(ang)])
                    a_, b_ = rng.uniform(80, 220), rng.uniform(80, 220)
                    seg = np.array([c0 - e * a_, c0 + e * b_])
                    mids = np.linspace(seg[0], seg[1], 9)
                    if all(_ok_ground(B, E, *q, sea, clear=VILLAGE_FROM_M / 2) and not wet.contains(Point(*q)) and not K.hits(Point(*q))
                           for q in mids):
                        streets.append(LineString(seg))
                houses: list = []
                vi = len(B.villages)
                size = ((8, 13), (7, 10))
                houses += _houses_along(B, rng, E, K, main, (17, 28), (9, 13), size, 0.85, s0=45, sea=sea, taken=houses)
                for st in streets[1:]:
                    houses += _houses_along(B, rng, E, K, st, (18, 30), (8, 12), size, 0.75, sea=sea, taken=houses)
                # houses off one street can stand on another: keep those clear of every street
                lines = shapely.STRtree(streets)
                houses = [h for h in houses if not lines.query(Point(h[0], h[1]).buffer(max(h[2], h[3]) / 2 + 3.5),
                                                               predicate="intersects").size]
                mid = np.asarray(main.interpolate(main.length * 0.45).coords[0])
                n = np.array([-d[1], d[0]])
                hx, hy = mid + n * 22
                if _ok_ground(B, E, hx, hy, sea):                  # the hall (or church) on the main street
                    houses = [h for h in houses if math.dist((h[0], h[1]), (hx, hy)) > 19]
                    houses.append((round(float(hx), 2), round(float(hy), 2), 13.0, 24.0, round(math.degrees(out), 2), 11.0))
                if len(houses) >= 12:
                    B.villages.append(houses)
                    for j, st in enumerate(streets):
                        B.lanes.append((f"v{vi}-{j}", "street", st, MAIN_W if j == 0 else STREET_W))
                    area = unary_union([st.buffer(45) for st in streets])
                    areas.append(area)
                    K.add(area)
            s += rng.uniform(*VILLAGE_EVERY_M)
    return areas


def _lanes(B, rng, E, K, hw, wet, hw_area, sea, villages) -> None:
    """Country lanes off the highways and out of the city's edge: a heading that wanders a little,
    stopping at water, the city, the region's edge or another road (where they join), with a
    branch now and then."""
    taken: set = set()                                 # 100 m cells under a lane
    vill = unary_union(villages) if villages else Polygon()
    shapely.prepare(vill)

    def cells(pts):
        return {(int(x // 100), int(y // 100)) for x, y in pts}

    def grow(p0, heading, max_len, joins=True):
        pts, a, s = [np.asarray(p0, float)], heading, 0.0
        turn = 0.0
        own: set = set()
        while s < max_len:
            turn = 0.6 * turn + rng.normal(0, 1.2)
            a += turn
            q = pts[-1] + 50 * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
            x, y = float(q[0]), float(q[1])
            if abs(x) > B.half - 150 or abs(y) > B.half - 150 or float(B.z(x, y)) < sea + 1.2:
                break
            if float(E.d_busy(x, y)) < 30 and s > 200 or wet.contains(Point(x, y)) or vill.contains(Point(x, y)):
                break
            pts.append(q)
            s += 50
            k = (int(x // 100), int(y // 100))
            if s > 150 and joins and (k in taken and k not in own or hw_area.contains(Point(x, y))):
                break                                  # met a road: joins it
            own |= {k}
        return np.array(pts)

    def keep(pts, kind="lane"):
        if len(pts) < 8:
            return None
        ln = LineString(pts)
        B.lanes.append((f"lane-{sum(1 for x in B.lanes if x[1] == 'lane')}", kind, ln, LANE_W))
        taken.update(cells(ln.segmentize(50).coords))
        K.add(ln.buffer(LANE_W / 2 + 6))
        return ln

    starts = []
    for ln in hw:
        s = rng.uniform(*LANE_EVERY_M) / 2
        while s < ln.length:
            p = np.asarray(ln.interpolate(s).coords[0])
            if float(E.d_busy(*p)) > 120:
                side = rng.choice([-1, 1])
                a = _heading(ln, s) + side * (90 + rng.uniform(-25, 25))
                d = np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
                starts.append((p + d * (HIGHWAY_W / 2 + 2), a))
            s += rng.uniform(*LANE_EVERY_M)
    # out of the city's edge, about every EDGE_LANE_M along it
    ii, jj = np.nonzero((E.built > 40) & (E.built < 60))
    order = rng.permutation(len(ii))
    picked: list = []
    for k in order:
        x, y = -E.half + jj[k] * GRID_M, E.half - ii[k] * GRID_M
        if E.d_busy(x, y) < 25 or float(B.z(x, y)) < sea + 2 or any(math.dist((x, y), q) < EDGE_LANE_M for q in picked):
            continue
        picked.append((x, y))
        starts.append((np.array([x, y]), E.outward(x, y) + rng.uniform(-15, 15)))
    for i in rng.permutation(len(starts)):
        p, a = starts[i]
        ln = keep(grow(p, a, rng.uniform(*LANE_LEN_M)))
        if ln is not None and ln.length > 1200 and rng.random() < 0.55:
            t = rng.uniform(0.3, 0.7) * ln.length
            b = _heading(ln, t) + rng.choice([-1, 1]) * (90 + rng.uniform(-15, 15))
            d = np.array([math.cos(math.radians(b)), math.sin(math.radians(b))])
            q = np.asarray(ln.interpolate(t).coords[0]) + d * (LANE_W + 2)
            keep(grow(q, b, rng.uniform(*LANE_LEN_M) * 0.6))


def _farmsteads(B, rng, E, K, hw, sea) -> None:
    """Farmsteads along the lanes and highways (a farmhouse and a barn or two behind it, in an L),
    and near the city scattered houses along every road, thinning out away from its edge."""
    roads = [(ln, LANE_W) for _, k, ln, _ in B.lanes if k == "lane"] + [(ln, HIGHWAY_W) for ln in hw]
    for ln, rw in roads:
        s = rng.uniform(*FARM_EVERY_M) / 2
        while s < ln.length:
            p = np.asarray(ln.interpolate(s).coords[0])
            a = _heading(ln, s)
            e = float(E.d_built(*p))
            if e < FRINGE_M:                                  # scattered houses near the city
                step = rng.uniform(60, 160)
                if rng.random() < 0.8 * (1 - e / FRINGE_M):
                    n = np.array([-math.sin(math.radians(a)), math.cos(math.radians(a))]) * rng.choice([-1, 1])
                    w, d = rng.uniform(8, 13), rng.uniform(7, 10)
                    q = p + n * (rw / 2 + rng.uniform(12, 24) + d / 2)
                    if _ok_ground(B, E, *q, sea) and not K.hits(Point(*q).buffer(d / 2)):
                        B.farms.append((round(float(q[0]), 2), round(float(q[1]), 2), round(w, 2), round(d, 2),
                                        round(a + rng.uniform(-5, 5), 2), round(rng.uniform(5, 8), 2), "house"))
                        K.add(Point(*q).buffer(max(w, d) / 2 + 8))
                s += step
                continue
            n = np.array([-math.sin(math.radians(a)), math.cos(math.radians(a))]) * rng.choice([-1, 1])
            t = np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
            yard = p + n * (rw / 2 + rng.uniform(30, 50))
            if _ok_ground(B, E, *yard, sea, slope=0.15) and not K.hits(Point(*yard).buffer(30)):
                hw_, hd = rng.uniform(10, 14), rng.uniform(8, 10)
                house = yard - n * 12
                parts = [(house, hw_, hd, a, rng.uniform(6.5, 8.5), "house")]
                bw, bd = rng.uniform(20, 34), rng.uniform(10, 15)
                parts.append((yard + n * 14 + t * rng.choice([-1, 1]) * 8, bw, bd, a, rng.uniform(7, 10), "barn"))
                if rng.random() < 0.55:
                    side = rng.choice([-1, 1])
                    parts.append((yard + t * side * (bw / 2 + 10), rng.uniform(16, 26), rng.uniform(9, 12), a + 90,
                                  rng.uniform(6, 8.5), "barn"))
                for q, w, d, r, h, kind in parts:
                    B.farms.append((round(float(q[0]), 2), round(float(q[1]), 2), round(w, 2), round(d, 2),
                                    round(r + rng.uniform(-3, 3), 2), round(h, 2), kind))
                K.add(Point(*yard).buffer(48))
            s += rng.uniform(*FARM_EVERY_M)


def _fields(B, rng, E, K, zone, hw, rivers, P, sea) -> None:
    """Farm estates of strip fields; see the module docstring."""
    half = B.half
    woods = _waves(rng, 7, (1400, 4200))
    lines = [ln for _, _, ln, _ in B.lanes] + hw + [ln for ln, _ in rivers]
    lines += [w.line for w in P.ways if w.kind in ("street", "avenue", "boulevard")]
    roads = shapely.STRtree(lines)
    g = np.arange(-half + ESTATE_M / 2, half, ESTATE_M)
    X, Y = np.meshgrid(g, g)
    X = (X + rng.uniform(-0.35, 0.35, X.shape) * ESTATE_M).ravel()
    Y = (Y + rng.uniform(-0.35, 0.35, Y.shape) * ESTATE_M).ravel()
    cells = shapely.voronoi_polygons(MultiPoint(np.c_[X, Y]), extend_to=shp_box(-half, -half, half, half), ordered=True)
    rows_d = (lambda small: SMALL_ROW_D if small else ROW_D)
    rect = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]])
    z = B.z(X, Y)
    e = E.d_built(X, Y) * (1 + EDGE_WOBBLE * zone(X, Y))
    wd = woods(X, Y)
    sl = B.slope(X, Y)
    near, dist = roads.query_nearest(shapely.points(X, Y), return_distance=True, all_matches=False)
    nearest = np.full(X.size, -1)
    gap = np.full(X.size, np.inf)
    nearest[near[0]], gap[near[0]] = near[1], dist
    for k, cell in enumerate(cells.geoms):
        x, y = float(X[k]), float(Y[k])
        if sl[k] > FARM_SLOPE or abs(x) > half - 300 or abs(y) > half - 300:
            continue                                   # (a seed in the city or the sea: its strips are tested one by one)
        small = e[k] < FRINGE_M
        if small:
            farm = wd[k] < 0.5
        elif e[k] < BELT_M:
            farm = wd[k] < 0.2
        else:
            farm = gap[k] < FARM_NEAR_M and wd[k] < -0.3 and sl[k] < 0.06
        if not farm:
            continue
        if gap[k] < 1200:                              # lined up with the nearest road or river
            ln = lines[nearest[k]]
            a = _heading(ln, ln.project(Point(x, y)))
        else:
            a = 90 * zone(x * 0.7, y * 0.7) + 40 * wd[k]
        a += rng.choice([0, 0, 0, 90]) + rng.uniform(-6, 6)
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        R = np.array([[ca, -sa], [sa, ca]])
        local = (np.asarray(cell.exterior.coords) - [x, y]) @ R      # u along the road, v across
        u0, v0 = local.min(axis=0)
        u1, v1 = local.max(axis=0)
        shapely.prepare(cell)
        v = v0
        while v < v1:
            depth = rng.uniform(*rows_d(small))
            cross = rng.random() < CROSS_P
            u = u0
            while u < u1:
                if small:
                    w = rng.uniform(*SMALL_STRIP_W)
                else:
                    w = rng.uniform(*STRIP_W[rng.choice(3, p=STRIP_P)])
                if cross:                              # strips along u, stacked across the row
                    ln_u = min(rng.uniform(250, 650), u1 - u)
                    vv = v
                    while vv < v + depth:
                        ww = min(w, v + depth - vv)
                        _strip(B, rng, E, K, cell, (u + ln_u / 2, vv + ww / 2), (ln_u, ww), R, (x, y), rect, sea)
                        vv += ww
                        w = rng.uniform(*SMALL_STRIP_W) if small else rng.uniform(*STRIP_W[rng.choice(3, p=STRIP_P)])
                    u += ln_u
                else:
                    _strip(B, rng, E, K, cell, (u + w / 2, v + depth / 2), (w, depth), R, (x, y), rect, sea)
                    u += w
            v += depth


def _strip(B, rng, E, K, cell, mid, size, R, origin, rect, sea) -> None:
    """One field: the strip in the estate's frame, cut to the estate, clear of roads, water and yards;
    a hedge or a line of trees along one long side now and then."""
    w, d = size[0] - FIELD_GAP_M, size[1] - FIELD_GAP_M
    if w < 12 or d < 12:
        return
    pts = (rect * [w, d] + mid) @ R.T + origin
    q = Polygon(pts).intersection(cell)
    if q.is_empty or not isinstance(q, Polygon) or q.area < 0.35 * w * d or q.area < 2000:
        return
    whole = q.area > 0.98 * w * d
    hit = K.near(q)
    if hit:
        q = q.difference(unary_union(hit))
        if not isinstance(q, Polygon) or q.interiors or q.area < 2000:
            if q.geom_type == "MultiPolygon":
                q = max(q.geoms, key=lambda g: g.area)
                if q.interiors or q.area < max(2000, 0.3 * w * d):
                    return
            else:
                return
        whole = False
    q = q.simplify(0.5)
    ring = np.asarray(q.segmentize(40).exterior.coords)
    if (np.any(B.z(ring[:, 0], ring[:, 1]) < sea + 1.5) or np.any(E.d_busy(ring[:, 0], ring[:, 1]) < CITY_CLEAR_M)
            or float(B.slope(*q.centroid.coords[0])) > FARM_SLOPE):
        return
    B.fields.append((q, bool(rng.random() < 0.6)))
    if not whole:
        return
    r = rng.random()
    if r > HEDGE_P + TREELINE_P:
        return
    long_u = size[1] >= size[0]                                  # the long side runs along v (else u)
    side = rng.choice([-1, 1]) * (size[0] if long_u else size[1]) / 2
    run = size[1] if long_u else size[0]
    along = np.array([0.0, 1.0]) if long_u else np.array([1.0, 0.0])
    off = np.array([side, 0.0]) if long_u else np.array([0.0, side])
    a = math.degrees(math.atan2(*(R @ along)[::-1]))
    if r < HEDGE_P:                                              # a hedge: stretched domes, HEDGE_SEG_M at most
        k = max(1, math.ceil(run / HEDGE_SEG_M))
        for i in range(k):
            c = (np.asarray(mid) + off + along * (-run / 2 + (i + 0.5) * run / k)) @ R.T + origin
            if float(B.z(*c)) > sea + 1.5:
                B.hedges.append((round(float(c[0]), 2), round(float(c[1]), 2), round(run / k + 4, 2),
                                 round(rng.uniform(5, 7), 2), round(rng.uniform(4, 6.5), 2), round(a, 2)))
    else:                                                        # a line of trees
        for t in np.arange(-run / 2 + 8, run / 2 - 4, rng.uniform(13, 19)):
            c = (np.asarray(mid) + off + along * t) @ R.T + origin
            if float(B.z(*c)) > sea + 1.5:
                B.forest.append((round(float(c[0]), 2), round(float(c[1]), 2), round(rng.uniform(9, 13), 2),
                                 round(rng.uniform(11, 17), 2)))


def _forest(B, rng, E, K, zone, sea) -> None:
    """Woodland: sparse copses in the fringe, between the farms in the belt, nearly everywhere past it
    and on every slope too steep to farm (a second, offset lattice there)."""
    half = B.half
    copse = _waves(rng, 7, (900, 2600))
    step = max(FOREST_STEP_M, 2 * half / FOREST_MAX_N)
    g = np.arange(-half + step / 2, half, step)
    X, Y = np.meshgrid(g, g)
    X = X + rng.uniform(-0.4, 0.4, X.shape) * step
    Y = Y + rng.uniform(-0.4, 0.4, Y.shape) * step
    x, y = X.ravel(), Y.ravel()
    sl = B.slope(x, y)
    steep = sl > STEEP                                  # denser on the hills: a second lattice, offset
    x2 = x[steep] + step / 2 + rng.uniform(-0.3, 0.3, int(steep.sum())) * step
    y2 = y[steep] + step / 2 + rng.uniform(-0.3, 0.3, int(steep.sum())) * step
    x, y = np.r_[x, x2], np.r_[y, y2]
    sl = np.r_[sl, B.slope(x2, y2)]
    z = B.z(x, y)
    e = E.d_built(x, y) * (1 + EDGE_WOBBLE * zone(x, y))
    cp = copse(x, y)
    zn = zone(x, y)
    keep = np.where(e < FRINGE_M, cp > -0.3,                            # copses near the city
                    np.where(e < BELT_M, cp > -1.5,                    # woods between the farms, a meadow or two
                             (zn > -0.8) | (cp > -0.6)))                # forest, a clearing now and then
    keep |= sl > FARM_SLOPE
    cand = keep & (z > sea + 2) & (np.abs(x) < half - step) & (np.abs(y) < half - step) & (E.d_busy(x, y) > CITY_CLEAR_M + 15)
    W = rng.uniform(*CLUMP_W, x.size)
    H = rng.uniform(*CLUMP_H, x.size) * (1 + 0.25 * np.clip(sl / STEEP, 0, 1))
    idx = np.nonzero(cand)[0]
    clear = ~K.hits_xy(x[idx], y[idx])
    if B.fields:
        fu = unary_union([q.buffer(10) for q, _ in B.fields])
        shapely.prepare(fu)
        clear &= ~shapely.contains_xy(fu, x[idx], y[idx])
    B.forest += [(round(float(x[i]), 2), round(float(y[i]), 2), round(float(W[i]), 2), round(float(H[i]), 2))
                 for i in idx[clear]]


def save_far(B: Background, path) -> None:
    f = B.far
    np.savez_compressed(path, x0=f["x0"], y0=f["y0"], cell=f["cell"], h=f["h"], skirt_m=f["skirt_m"])


def add_slots(B: Background, new, hs_tile, extent_m: float) -> None:
    """The region as greybox slots (`new`: build_spec's slot factory), grouped per tile: forest clumps
    and hedges (one dome, instanced), fields (draped, merged per slot), lanes and village streets
    (draped), farm and village buildings (a unit box and a gable), highways and rivers."""
    from .city import UNIT, _drape
    from .greybox import UNIT_BOX, poly, ribbon
    core = extent_m / 2

    def z_many(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        inside = (np.abs(x) < core) & (np.abs(y) < core)
        return np.where(inside, hs_tile.many(x, y), B.z(x, y))

    def key(x, y, m=GROUP_M):
        return f"x{int(math.floor(x / m))}y{int(math.floor(y / m))}"

    def buildings(sb, rows):
        zs = z_many([h[0] for h in rows], [h[1] for h in rows])
        for (x, y, w, d, rot, h, *kind), z in zip(rows, zs):
            piece = "village:barn" if kind and kind[0] == "barn" else "village:house"
            sb.add(piece, UNIT_BOX, (x, y, float(z) - 0.3), rot, (w, d, h))
            sb.add("village:roof", UNIT["gable"], (x, y, float(z) - 0.3 + h), rot + 90, (d + 0.6, w + 0.6, 0.45 * d))

    for wid, kind, ln, w in B.ways:
        pts = _drape(z_many, list(ln.coords), 1.9 if kind == "river" else 0.15, 25.0)
        o = pts[0]
        sb = new(f"way-{wid}", kind, tuple(o), category="terrain")
        sb.add(kind, ribbon([[p[0] - o[0], p[1] - o[1], p[2] - o[2]] for p in pts], w, 0.1), tuple(o))
    groups: dict[tuple[str, str], list] = {}
    for q, crop in B.fields:
        m = q.centroid
        groups.setdefault(("farmland", key(m.x, m.y, FIELD_GROUP_M)), []).append(("field", q, crop))
    for t in B.forest:
        groups.setdefault(("forest", key(t[0], t[1])), []).append(("clump", t))
    for t in B.hedges:
        groups.setdefault(("forest", key(t[0], t[1])), []).append(("hedge", t))
    for ln in B.lanes:
        m = ln[2].interpolate(0.5, normalized=True)
        groups.setdefault(("lanes", key(m.x, m.y, LANE_GROUP_M)), []).append(("lane", ln))
    for t in B.farms:
        groups.setdefault(("farms", key(t[0], t[1])), []).append(("farm", t))

    def at(it):
        if it[0] == "field":
            return it[1].centroid.x, it[1].centroid.y
        if it[0] == "lane":
            m = it[1][2].interpolate(0.5, normalized=True)
            return m.x, m.y
        return it[1][0], it[1][1]
    types = {"farmland": ("farmland", "terrain"), "forest": ("forest", "vegetation"), "lanes": ("lane", "terrain"),
             "farms": ("village", "building")}
    for (kind, k), items in sorted(groups.items()):
        xy = np.array([at(it) for it in items])
        cx, cy = float(xy[:, 0].mean()), float(xy[:, 1].mean())
        sb = new(f"{kind}-{k}", types[kind][0], (cx, cy, float(z_many(cx, cy))), category=types[kind][1])
        if kind == "farmland":
            for _, q, crop in items:
                pts = _drape(z_many, list(q.exterior.coords)[:-1], 0.3, 60.0)
                o = pts[0]
                sb.add("field:crop" if crop else "field:fallow", poly([[p[0] - o[0], p[1] - o[1], p[2] - o[2]] for p in pts], 0.1),
                       tuple(o))
        elif kind == "lanes":
            for _, (_, lk, ln, w) in items:
                pts = _drape(z_many, list(ln.coords), 0.4, 25.0)
                o = pts[0]
                sb.add("country:lane", ribbon([[p[0] - o[0], p[1] - o[1], p[2] - o[2]] for p in pts], w, 0.1), tuple(o))
        elif kind == "farms":
            buildings(sb, [it[1] for it in items])
        else:
            clumps = [it[1] for it in items if it[0] == "clump"]
            hedges = [it[1] for it in items if it[0] == "hedge"]
            zs = z_many([t[0] for t in clumps], [t[1] for t in clumps]) if clumps else []
            for (x, y, w, h), z in zip(clumps, zs):
                sb.add("forest:clump", UNIT["dome"], (x, y, float(z) - 2.0), 0.0, (w, w, 2 * h))
            zs = z_many([t[0] for t in hedges], [t[1] for t in hedges]) if hedges else []
            for (x, y, ln, w, h, rot), z in zip(hedges, zs):
                sb.add("forest:hedge", UNIT["dome"], (x, y, float(z) - 1.0), rot, (ln, w, 2 * h))
    for i, houses in enumerate(B.villages):
        cx, cy = float(np.mean([h[0] for h in houses])), float(np.mean([h[1] for h in houses]))
        sb = new(f"village-{i}", "village", (cx, cy, float(z_many(cx, cy))))
        buildings(sb, houses)


def plan_image(B: Background, layout: SiteLayout, P=None, px: int = 1600):
    """The whole region from above (site/background_plan.png): shaded ground, sea, fields, hedges,
    forest, villages and farms, lanes and ways, and the city's bounds (its own plan is city_plan.png)."""
    from PIL import Image, ImageDraw
    half = B.half
    s = px / (2 * half)
    h = B.far["h"][::-1].astype(np.float64)              # rows north -> south for the image
    gy, gx = np.gradient(h, B.far["cell"])
    shade = np.clip(0.75 + 1.8 * (gx * -0.6 + gy * 0.8) / (1 + np.hypot(gx, gy)), 0.35, 1.15)
    sea = h < layout.sea_level
    rgb = np.empty(h.shape + (3,))
    rgb[...] = np.array([158, 150, 128]) / 255
    rgb = rgb * shade[..., None]
    rgb[sea] = np.array([48, 84, 116]) / 255
    im = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((px, px), Image.BILINEAR)
    d = ImageDraw.Draw(im, "RGBA")
    tp = lambda x, y: ((x + half) * s, (half - y) * s)  # noqa: E731
    for q, crop in B.fields:
        d.polygon([tp(*p) for p in q.exterior.coords], fill=(196, 178, 96, 255) if crop else (150, 140, 86, 255))
    for x, y, ln, _, _, rot in B.hedges:
        a = math.radians(rot)
        dx, dy = math.cos(a) * ln / 2, math.sin(a) * ln / 2
        d.line([tp(x - dx, y - dy), tp(x + dx, y + dy)], fill=(56, 92, 46, 255), width=1)
    for x, y, w, _ in B.forest:
        r = max(1.0, w / 2 * s)
        cx, cy = tp(x, y)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(46, 82, 40, 255))
    for _, kind, ln, w in B.lanes:
        d.line([tp(*p) for p in ln.coords], fill=(200, 190, 165, 255) if kind == "lane" else (215, 205, 185, 255),
               width=max(1, int(w * s)))
    for houses in B.villages:
        for x, y, *_ in houses:
            cx, cy = tp(x, y)
            d.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=(170, 80, 60, 255))
    for x, y, *_, kind in B.farms:
        cx, cy = tp(x, y)
        d.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=(120, 60, 50, 255) if kind == "barn" else (170, 80, 60, 255))
    for _, kind, ln, w in B.ways:
        d.line([tp(*p) for p in ln.coords], fill=(60, 110, 160, 255) if kind == "river" else (90, 90, 90, 255),
               width=max(2, int(w * s)))
    if P is not None:
        for w in P.ways:
            if w.kind in ("highway", "river"):
                d.line([tp(*p) for p in w.line.coords], fill=(60, 110, 160, 255) if w.kind == "river" else (90, 90, 90, 255),
                       width=max(2, int(w.width * s)))
        for b in P.buildings[::max(1, len(P.buildings) // 30000)]:
            cx, cy = tp(b.x, b.y)
            d.point((cx, cy), fill=(120, 70, 60, 255))
    x0, y0, x1, y1 = layout.city.bounds
    d.rectangle([*tp(x0, y1), *tp(x1, y0)], outline=(255, 255, 255, 200), width=2)
    core = layout.terrain.extent_m / 2
    d.rectangle([*tp(-core, core), *tp(core, -core)], outline=(255, 255, 255, 90), width=1)
    return im

"""The background region round a city (CitySpec.background): open country out to about REACH x the
city's size, cheap to build, export and draw. Nothing in it is unique per building: forest is one
clump stand-in instanced over a jittered lattice, village houses one unit box, fields flat draped
polygons merged per slot (the greybox merges draped pieces), highways and rivers draped ribbons.

    far terrain  a coarse heightfield (far.npz, the format of terrain_build's far ring) from the
                 project's own terrain generator over the whole region (the coast, its river and
                 its rise continue), equal to the tile at the tile's edge, its own noise fading in
                 past it, and hills rising to the horizon on land
    highways     the plan's highways carried on straight out to the region's edge, on land
    rivers       the plan's rivers carried on upstream (they flow from their first point), meandering
    villages     every VILLAGE_EVERY_M along those highways past the city, a cluster of houses
    farmland     fields on lowland near highways and rivers: rotated patches of rectangles
    forest       elsewhere on land, and on every slope too steep to farm: clumps

Everything is drawn from its own stream (STREAM, the city seed, the layout seed), never the city
plan's, so turning it on or off moves nothing in the city. The greybox (build_spec), the 3D viewer
(city_view) and the background map (plan_image) all read plan(); a city without the flag never calls it.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box as shp_box
from shapely.ops import unary_union

from .schema import SiteLayout

STREAM = 0xB4C6
REACH = 3.0                 # the region is this x the city's size across
MIN_RING_M = 3000.0         # at least this much country past the tile's edge
CELL_M = 30.0               # the far terrain's spacing, at least (else the region over ~1200 samples)
EDGE_BLEND_M = 1500.0       # past the tile's edge, the far field's own noise and hills fade in over this
HILL_M = 260.0              # hills to the horizon: up to this high at the region's edge (on land)
CITY_CLEAR_M = 350.0        # nothing within this of the city's bounds
FOREST_STEP_M = 90.0        # forest clumps on a jittered lattice this far apart, at least ...
FOREST_MAX_N = 450          # ... or the region over this many per side, whichever is coarser
CLUMP_W = (55.0, 85.0)      # a clump's width and height: overlapping blobs read as woodland from afar
CLUMP_H = (16.0, 26.0)
PATCH_M = 1200.0            # farmland comes in patches this big, each turned its own way
FIELD_W, FIELD_D = (220.0, 420.0), (140.0, 280.0)
FIELD_GAP_M = 9.0           # lanes and hedges between fields
FARM_SLOPE = 0.12           # steeper than this: forest, not fields
FARM_NEAR_M = 4500.0        # fields lie within this of a highway or river (farther: forest and meadow)
VILLAGE_EVERY_M = (3500.0, 6000.0)
VILLAGE_FROM_M = 1200.0     # the first village this far past the city's bounds
VILLAGE_HOUSES = (14, 40)
HIGHWAY_W = 30.0
GROUP_M = 2000.0            # background slots are grouped per tile this big


@dataclass
class Background:
    far: dict                                   # x0, y0, cell, h (rows south -> north), skirt_m
    half: float                                 # the region's half extent (m), round the origin
    ways: list = field(default_factory=list)    # (id, kind, LineString, width)
    fields: list = field(default_factory=list)  # (Polygon, crop: bool)
    forest: list = field(default_factory=list)  # (x, y, w, h)
    villages: list = field(default_factory=list)   # [(x, y, w, d, rot, h)] per village

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

    def counts(self) -> dict:
        return {"forest_clumps": len(self.forest), "fields": len(self.fields), "villages": len(self.villages),
                "village_houses": sum(len(v) for v in self.villages), "ways": len(self.ways),
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


def plan(layout: SiteLayout, P, tile: np.ndarray) -> Background:
    """The background region of a planned city (P: city.plan's CityPlan, tile: the greybox heights)."""
    c = layout.city
    rng = np.random.default_rng([STREAM, int(c.seed), int(c.layout_seed)])
    sea = layout.sea_level
    B = Background(far=far_terrain(layout, tile, rng), half=0.0)
    B.half = -B.far["x0"]
    half = B.half
    x0, y0, x1, y1 = c.bounds
    city = shp_box(x0, y0, x1, y1).buffer(CITY_CLEAR_M, join_style="mitre")
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
    hw = [ln for _, k, ln, _ in B.ways if k == "highway"]
    wet = [ln.buffer(wd / 2 + 30) for _, k, ln, wd in B.ways if k == "river"]
    keep_off = unary_union([ln.buffer(HIGHWAY_W / 2 + 25) for ln in hw] + wet + [city])
    # villages along the highways, past the city
    taken: list[Polygon] = []
    for ln in hw:
        s = VILLAGE_FROM_M + rng.uniform(*VILLAGE_EVERY_M) * 0.5
        while s < ln.length:
            p = np.asarray(ln.interpolate(s).coords[0])
            q = np.asarray(ln.interpolate(min(s + 30, ln.length)).coords[0])
            d = (q - p) / max(float(np.linalg.norm(q - p)), 1e-9)
            n = np.array([-d[1], d[0]]) * rng.choice([-1, 1])
            ctr = p + n * rng.uniform(140, 240)
            if not city.contains(Point(*ctr)) and float(B.z(*ctr)) > sea + 2 and not any(v.contains(Point(*ctr)) for v in taken):
                houses = _village(B, rng, ctr, math.degrees(math.atan2(d[1], d[0])), keep_off, sea)
                if len(houses) >= 6:
                    B.villages.append(houses)
                    taken.append(Point(*ctr).buffer(450))
            s += rng.uniform(*VILLAGE_EVERY_M)
    keep_off = unary_union([keep_off] + taken)
    _country(B, rng, layout, keep_off, hw + [ln for _, k, ln, _ in B.ways if k == "river"])
    return B


def _village(B, rng, ctr, heading: float, keep_off, sea) -> list:
    """A cluster of houses round ctr, turned to the highway, clear of it."""
    out = []
    n = int(rng.integers(*VILLAGE_HOUSES))
    r = 70 + 9 * n
    for _ in range(4 * n):
        if len(out) >= n:
            break
        a, rr = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.uniform(0.02, 1))
        x, y = ctr[0] + rr * math.cos(a), ctr[1] + rr * math.sin(a)
        w, d = rng.uniform(8, 14), rng.uniform(7, 11)
        if keep_off.contains(Point(x, y)) or any(math.dist((x, y), (o[0], o[1])) < 16 for o in out):
            continue
        if float(B.z(x, y)) < sea + 1.5:
            continue
        out.append((round(x, 2), round(y, 2), round(w, 2), round(d, 2),
                    round(heading + rng.choice([0, 90]) + rng.uniform(-8, 8), 2), round(rng.uniform(5, 8.5), 2)))
    return out


def _country(B: Background, rng, layout, keep_off, lines) -> None:
    """Farmland near the highways and rivers on gentle lowland, forest elsewhere on land."""
    half, sea = B.half, layout.sea_level
    zone = _waves(rng, 8, (3000, 9000))
    step = max(FOREST_STEP_M, 2 * half / FOREST_MAX_N)
    near = unary_union([ln.buffer(FARM_NEAR_M) for ln in lines]) if lines else Polygon()
    from shapely import prepared
    near_p, off_p = prepared.prep(near), prepared.prep(keep_off)

    def slope(x, y):
        e = 40.0
        return np.hypot(B.z(x + e, y) - B.z(x - e, y), B.z(x, y + e) - B.z(x, y - e)) / (2 * e)

    # farmland patches
    g = np.arange(-half + PATCH_M / 2, half, PATCH_M)
    farm_cells = []
    for cy in g:
        for cx in g:
            if float(zone(cx, cy)) > 0.15 or not near_p.contains(Point(cx, cy)) or off_p.contains(Point(cx, cy)):
                continue
            if float(B.z(cx, cy)) < sea + 1.5 or float(slope(cx, cy)) > FARM_SLOPE:
                continue
            farm_cells.append((cx, cy))
    for cx, cy in farm_cells:
        rot = rng.uniform(0, 90)
        fw, fd = rng.uniform(*FIELD_W), rng.uniform(*FIELD_D)
        ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        reach = PATCH_M * 0.75
        for u in np.arange(-reach, reach, fw):
            for v in np.arange(-reach, reach, fd):
                px, py = cx + u * ca - v * sa, cy + u * sa + v * ca
                if abs(px - cx) > PATCH_M / 2 or abs(py - cy) > PATCH_M / 2:
                    continue
                w_, d_ = fw - FIELD_GAP_M, fd - FIELD_GAP_M
                corners = np.array([[-w_ / 2, -d_ / 2], [w_ / 2, -d_ / 2], [w_ / 2, d_ / 2], [-w_ / 2, d_ / 2]])
                R = np.array([[ca, -sa], [sa, ca]])
                pts = corners @ R.T + [px, py]
                q = Polygon(pts)
                if off_p.intersects(q) or np.any(B.z(pts[:, 0], pts[:, 1]) < sea + 1.5):
                    continue
                B.fields.append((q, bool(rng.random() < 0.6)))
    # forest: the rest of the land, clear of fields, ways, villages and the city
    g = np.arange(-half + step / 2, half, step)
    X, Y = np.meshgrid(g, g)
    X = X + rng.uniform(-0.4, 0.4, X.shape) * step
    Y = Y + rng.uniform(-0.4, 0.4, Y.shape) * step
    x, y = X.ravel(), Y.ravel()
    z = B.z(x, y)
    zn = zone(x, y)
    sl = slope(x, y)
    cand = (z > sea + 2) & ((zn > -0.25) | (sl > FARM_SLOPE)) & (np.abs(x) < half - step) & (np.abs(y) < half - step)
    W = rng.uniform(*CLUMP_W, x.size)
    H = rng.uniform(*CLUMP_H, x.size)
    idx = np.nonzero(cand)[0]
    shapely.prepare(keep_off)
    clear = ~shapely.contains_xy(keep_off, x[idx], y[idx])
    if B.fields:
        fu = unary_union([q.buffer(10) for q, _ in B.fields])
        shapely.prepare(fu)
        clear &= ~shapely.contains_xy(fu, x[idx], y[idx])
    B.forest = [(round(float(x[i]), 2), round(float(y[i]), 2), round(float(W[i]), 2), round(float(H[i]), 2))
                for i in idx[clear]]


def save_far(B: Background, path) -> None:
    f = B.far
    np.savez_compressed(path, x0=f["x0"], y0=f["y0"], cell=f["cell"], h=f["h"], skirt_m=f["skirt_m"])


def add_slots(B: Background, new, hs_tile, extent_m: float) -> None:
    """The region as greybox slots (`new`: build_spec's slot factory), grouped per GROUP_M tile:
    forest clumps (one dome, instanced), fields (draped, merged per slot), village houses, ways."""
    from .city import UNIT, _drape
    from .greybox import UNIT_BOX, poly, ribbon
    core = extent_m / 2

    def z_many(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        inside = (np.abs(x) < core) & (np.abs(y) < core)
        return np.where(inside, hs_tile.many(x, y), B.z(x, y))

    key = lambda x, y: f"x{int(math.floor(x / GROUP_M))}y{int(math.floor(y / GROUP_M))}"  # noqa: E731
    for wid, kind, ln, w in B.ways:
        pts = _drape(z_many, list(ln.coords), 1.9 if kind == "river" else 0.15, 25.0)
        o = pts[0]
        sb = new(f"way-{wid}", kind, tuple(o), category="terrain")
        sb.add(kind, ribbon([[p[0] - o[0], p[1] - o[1], p[2] - o[2]] for p in pts], w, 0.1), tuple(o))
    groups: dict[tuple[str, str], list] = {}
    for q, crop in B.fields:
        m = q.centroid
        groups.setdefault(("farmland", key(m.x, m.y)), []).append(("field", q, crop))
    for t in B.forest:
        groups.setdefault(("forest", key(t[0], t[1])), []).append(("clump", t))
    for (kind, k), items in sorted(groups.items()):
        xs = [it[1].centroid.x if it[0] == "field" else it[1][0] for it in items]
        ys = [it[1].centroid.y if it[0] == "field" else it[1][1] for it in items]
        cx, cy = float(np.mean(xs)), float(np.mean(ys))
        sb = new(f"{kind}-{k}", kind, (cx, cy, float(z_many(cx, cy))), category="terrain" if kind == "farmland" else "vegetation")
        if kind == "farmland":
            for _, q, crop in items:
                pts = _drape(z_many, list(q.exterior.coords)[:-1], 0.3, 60.0)
                o = pts[0]
                sb.add("field:crop" if crop else "field:fallow", poly([[p[0] - o[0], p[1] - o[1], p[2] - o[2]] for p in pts], 0.1),
                       tuple(o))
        else:
            zs = z_many([it[1][0] for it in items], [it[1][1] for it in items])
            for (_, (x, y, w, h)), z in zip(items, zs):
                sb.add("forest:clump", UNIT["dome"], (x, y, float(z) - 2.0), 0.0, (w, w, 2 * h))
    for i, houses in enumerate(B.villages):
        cx, cy = float(np.mean([h[0] for h in houses])), float(np.mean([h[1] for h in houses]))
        sb = new(f"village-{i}", "village", (cx, cy, float(z_many(cx, cy))))
        zs = z_many([h[0] for h in houses], [h[1] for h in houses])
        for (x, y, w, d, rot, h), z in zip(houses, zs):
            sb.add("village:house", UNIT_BOX, (x, y, float(z) - 0.3), rot, (w, d, h))
            sb.add("village:roof", UNIT["gable"], (x, y, float(z) - 0.3 + h), rot + 90, (d + 0.6, w + 0.6, 0.45 * d))


def plan_image(B: Background, layout: SiteLayout, P=None, px: int = 1600):
    """The whole region from above (site/background_plan.png): shaded ground, sea, fields, forest,
    villages, ways, and the city's bounds (its own plan is city_plan.png)."""
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
    for x, y, w, _ in B.forest:
        r = max(1.0, w / 2 * s)
        cx, cy = tp(x, y)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(46, 82, 40, 255))
    for houses in B.villages:
        for x, y, *_ in houses:
            cx, cy = tp(x, y)
            d.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=(170, 80, 60, 255))
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

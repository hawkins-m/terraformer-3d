"""The classic preset's coastal metro (LayoutPreset.metro): a city shaped like a real lakefront or
seaside metro rather than utopia's radial plan. city.py and city_shape.py call in here only when
the preset's `metro` is set, so every other preset plans exactly as before.

    downtown   one dense tower cluster on the coast at the river's mouth (city_shape.metro_shape
               places it from the terrain's water); inland, near the centre
    density    a smooth fall-off from downtown with noise on the distance (an irregular edge, no
               outline, wall or ring), ribbons of development out along the arterials and
               highways that fade into open land; tall at downtown, houses at the edge
    streets    the grid turned to the coast at downtown (city.with_preset sets grid_deg), 2-4
               straight diagonal arterials out of downtown, a lakeshore boulevard behind a
               lakefront park strip; highways come in, none goes round
    river      the terrain's own river if it has one (its forks join it as tributaries), else a
               meandering river from inland to the coast beside downtown that forks once or twice
               (a landlocked city's crosses it). Its banks are green and nothing is built in its
               corridor; ways over it are level bridge decks (deck, add_slots)
    green      the lakefront park, a few large parks, small parks between blocks (block_parks)
    industry   along the river, away from downtown

Everything comes from the spec (the downtown node, the terrain's coast and river, the bounds) and
the preset's own stream (city.layout_rng, the layout seed), never the city seed's.
"""
import math

import numpy as np
import shapely
import shapely.prepared
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from . import city
from .schema import CitySpec, SiteLayout, TerrainSpec

SCALE_K = 0.62          # the density's half-life distance, x half the bounds' short side
GRID_M = 20.0           # the density raster's spacing
RIBBON_M = 130.0        # development along an arterial or highway: this wide either side
LAKEFRONT_M = 170.0     # the lakefront park strip, from the shore inland
LAKESHORE_W = 34.0      # the lakeshore boulevard behind it
BANK_GREEN_M = 28.0     # the river's green banks either side
FORK_W = 0.75           # a fork's width, x the main river's
MOUTH_GAP_M = 170.0     # the river's mouth this far beside downtown's core


# --- the frame: where downtown is, which way is the sea --------------------------------------

def coast(t: TerrainSpec) -> str:
    """side (a shoreline), all (an island) or none (inland)."""
    return t.settings.coast if t.settings is not None else "side"


def axes(c: CitySpec, t: TerrainSpec) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors at downtown: towards the sea, and along the coast. An island's: out from its centre."""
    from .greybox import sea_axes
    if coast(t) == "all":
        C = np.array(city.main_centre(c), float)
        d = C - np.array(t.settings.centre, float)
        n = float(np.linalg.norm(d))
        u = d / n if n > 1 else sea_axes(t)[0]
        return u, np.array([-u[1], u[0]])
    return sea_axes(t)


def scale(c: CitySpec) -> float:
    x0, y0, x1, y1 = c.bounds
    return SCALE_K * 0.5 * min(x1 - x0, y1 - y0) * c.preset.falloff_scale


def grid_deg(layout: SiteLayout) -> float:
    """The grid's angle: along the coast at downtown, plus the preset's own grid_deg."""
    _, v = axes(layout.city, layout.terrain)
    return round(math.degrees(math.atan2(v[1], v[0])) % 90 + layout.city.preset.grid_deg, 3)


def _spread(rng, n: int, half: float) -> list[float]:
    """n angles (deg) across +-half, none straight ahead, jittered."""
    a = np.linspace(-half, half, n) if n > 1 else np.array([rng.choice([-1, 1]) * half / 2])
    a = np.where(np.abs(a) < 18, np.sign(a + 1e-9) * 22, a)
    return [float(x + rng.uniform(-8, 8)) for x in a]


def arterial_angles(c: CitySpec, t: TerrainSpec) -> list[float]:
    """2-4 diagonal arterials out of downtown, fanned inland (a landlocked city's all round)."""
    rng = city.layout_rng(c, "metro-arterials")
    n = int(rng.integers(2, 5))
    if coast(t) == "none":
        a0 = rng.uniform(0, 360)
        return [a0 + 360 * k / n + rng.uniform(-15, 15) for k in range(n)]
    u, _ = axes(c, t)
    inland = math.degrees(math.atan2(-u[1], -u[0]))
    return [inland + a for a in _spread(rng, n, 30 + 10 * n)]


def highway_angles(c: CitySpec, t: TerrainSpec) -> list[float]:
    """The highway spokes: into the city from inland and along the coast, never round it."""
    rng = city.layout_rng(c, "metro-highways")
    n = c.preset.highway_spokes
    if coast(t) == "none":
        a0 = rng.uniform(0, 360)
        return [a0 + 360 * k / n + rng.uniform(-10, 10) for k in range(n)]
    u, _ = axes(c, t)
    inland = math.degrees(math.atan2(-u[1], -u[0]))
    return [inland + a for a in _spread(rng, n, 82)]


def _rays(C, angles, r0: float, r1: float) -> list[LineString]:
    return [LineString([C + r0 * city._dir(a), C + r1 * city._dir(a)]) for a in angles]


# --- the river -------------------------------------------------------------------------------

def _meander(start, heading: float, length: float, amp: float, wl: float, ph: float) -> np.ndarray:
    """A meandering polyline from start along heading (deg); it leaves start straight (the bend grows in)."""
    s = np.arange(0.0, length + 1, 10.0)
    d, n = city._dir(heading), city._dir(heading + 90)
    off = amp * np.minimum(1.0, s / 250) * (np.sin(2 * math.pi * s / wl + ph) + 0.4 * np.sin(2 * math.pi * s / (0.43 * wl) + 2 * ph)
                                            - (math.sin(ph) + 0.4 * math.sin(2 * ph)))
    return np.asarray(start, float) + np.outer(s, d) + np.outer(off, n)


def _in_tile(t: TerrainSpec, pts: np.ndarray) -> np.ndarray:
    """The points up to where the line first leaves the tile."""
    half = t.extent_m / 2 - 5
    ok = np.all(np.abs(pts) < half, axis=1)
    return pts if ok.all() else pts[:int(np.argmin(ok))]


def _branch(t, rng, join, heading, length, w, wid, out):
    """A fork: from upstream down to `join` (it meets the river there)."""
    pts = _in_tile(t, _meander(join, heading, length, rng.uniform(70, 130), rng.uniform(900, 1300), rng.uniform(0, 2 * math.pi)))
    if len(pts) > 3:
        out.append((wid, LineString(pts[::-1]), w))
    return pts


def river_side(c: CitySpec) -> int:
    """Which side of downtown (along the coast) a generated river's mouth is."""
    return int(city.layout_rng(c, "metro-river").choice([-1, 1]))


def rivers(c: CitySpec, t: TerrainSpec) -> list:
    """The metro's own rivers as (id, line flowing down, width): the forks of the terrain's river
    when it has one, else a river beside downtown to the coast with its forks (a landlocked
    city's crosses it)."""
    from . import terrain_gen
    if not c.nodes:
        return []
    rng = city.layout_rng(c, "metro-river")
    side = int(rng.choice([-1, 1]))
    C = np.array(city.main_centre(c), float)
    S = scale(c)
    W = c.preset.river_w
    out: list = []
    nforks = int(rng.integers(1, 3))
    u, v = axes(c, t)
    own = terrain_gen.river_line(t)
    if own is not None:                       # the terrain's river: its forks join it upstream of downtown
        ln = own
        s0 = ln.project(Point(C))
        for k in range(nforks):
            s = s0 - (0.5 + 0.35 * k) * S * rng.uniform(0.9, 1.2)       # upstream (the line starts upstream)
            if s < 100:
                continue
            p, q = np.array(ln.interpolate(s).coords[0]), np.array(ln.interpolate(max(s - 50, 0)).coords[0])
            up = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            _branch(t, rng, p, up + (1 if k % 2 == 0 else -1) * side * rng.uniform(35, 55), 2.6 * S,
                    FORK_W * terrain_gen.terrain_lines(t)[0].width, f"river-fork{k}", out)
        return out
    r0 = c.nodes[0].radius
    gap = 1.05 * r0 + MOUTH_GAP_M + rng.uniform(0, 60)
    if coast(t) == "none":                    # across the city, beside downtown, a fork joining it
        a = math.degrees(math.atan2(v[1], v[0])) + rng.uniform(-35, 35)
        P0 = C + side * gap * city._dir(a + 90)
        far = 0.75 * t.extent_m
        pts = _meander(P0 - far * city._dir(a), a, 2 * far, rng.uniform(80, 130), rng.uniform(1000, 1400), rng.uniform(0, 2 * math.pi))
        pts = pts[np.all(np.abs(pts) < t.extent_m / 2 - 5, axis=1)]
        if len(pts) < 4:
            return []
        main = LineString(pts)
        out.append(("river", main, W))
        for k in range(nforks):
            s = main.project(Point(P0)) - (0.55 + 0.4 * k) * S
            if s < 100:
                continue
            p, q = np.array(main.interpolate(s).coords[0]), np.array(main.interpolate(s - 50).coords[0])
            up = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            _branch(t, rng, p, up + (1 if k % 2 == 0 else -1) * rng.uniform(35, 55), 2.4 * S, FORK_W * W, f"river-fork{k}", out)
        return out
    # to the coast: the mouth beside downtown, a short main stem inland, then it forks
    P0 = C + side * gap * v
    if coast(t) == "all":
        mouth = P0 + (float(terrain_gen.inland_of(t, *P0)) + 80) * u
    else:
        from .greybox import shore_at
        lat = float(P0 @ v)
        mouth = (float(shore_at(t, lat)) + 80) * u + lat * v
    inland = math.degrees(math.atan2(-u[1], -u[0]))
    stem = float(np.linalg.norm(mouth - P0)) + r0 + rng.uniform(150, 400)
    sp = _meander(mouth, inland, stem, rng.uniform(25, 45), rng.uniform(600, 800), rng.uniform(0, 2 * math.pi))
    out.append(("river", LineString(sp[::-1]), W))
    fork = sp[-1]
    spread = rng.uniform(30, 50)
    branches = []
    for k, sgn in enumerate((-1, 1)):
        branches.append(_branch(t, rng, fork, inland + sgn * spread + rng.uniform(-8, 8), 3.0 * S, FORK_W * W,
                                f"river-fork{k}", out))
    if nforks > 1:                            # a second fork on one branch
        b = branches[int(rng.integers(2))]
        j = min(len(b) - 1, int(rng.uniform(0.35, 0.6) * S / 10))
        if j > 5:
            hd = math.degrees(math.atan2(*(b[j] - b[j - 5])[::-1]))
            _branch(t, rng, b[j], hd + rng.choice([-1, 1]) * rng.uniform(30, 45), 2.2 * S, FORK_W * 0.8 * W, "river-fork2", out)
    return out


def water(c: CitySpec, t: TerrainSpec) -> list:
    """The rivers as city.layout_water's entries (id, way kind, WaterLine)."""
    from .terrain_gen import WaterLine
    return [(wid, "river", WaterLine(ln, round(w, 1), 4.0, "down")) for wid, ln, w in rivers(c, t)]


def river_lines(c: CitySpec, t: TerrainSpec) -> list[tuple[LineString, float]]:
    """Every river through the city, the terrain's included: (line, width)."""
    from . import terrain_gen
    return [(w.line, w.width) for w in terrain_gen.terrain_lines(t)] + [(ln, w) for _, ln, w in rivers(c, t)]


# --- density ---------------------------------------------------------------------------------

def _cache(f) -> dict:
    m = getattr(f, "_metro", None)
    if m is None:
        from .greybox import _value_noise
        c, t = f.city, f.t
        rng = city.layout_rng(c, "metro-noise")
        C = np.array(city.main_centre(c), float)
        S = scale(c)
        lines = _rays(C, arterial_angles(c, t), 0.0, 3.2 * S)
        if c.preset.highways in ("radial", "both"):
            lines += _rays(C, highway_angles(c, t), 0.5 * c.preset.downtown_m, 3.2 * S)
        m = {"C": C, "S": S, "n1": _value_noise(129, 5, rng), "n2": _value_noise(129, 14, rng), "ribbons": lines}
        f._metro = m
        m["raster"] = _raster(f, m)
    return m


def noise(f, x, y, which="n2"):
    """The metro's own noise ([-1, 1]) at points: n1 broad lobes, n2 finer."""
    g = _cache(f)[which]
    e, nn = f.t.extent_m, g.shape[0]
    j = np.clip(((np.asarray(x, float) / e + 0.5) * (nn - 1)).astype(int), 0, nn - 1)
    i = np.clip(((0.5 - np.asarray(y, float) / e) * (nn - 1)).astype(int), 0, nn - 1)
    return g[i, j]


def _raster(f, m) -> tuple:
    c = f.city
    x0, y0, x1, y1 = c.bounds
    xs, ys = np.arange(x0, x1 + GRID_M, GRID_M), np.arange(y0, y1 + GRID_M, GRID_M)
    X, Y = np.meshgrid(xs, ys)
    x, y = X.ravel(), Y.ravel()
    C, S = m["C"], m["S"]
    d = np.hypot(x - C[0], y - C[1])
    q = d / (S * (1 + 0.38 * noise(f, x, y, "n1") + 0.15 * noise(f, x, y)))
    dens = np.exp(-np.maximum(q, 0) ** 1.7)
    pts = shapely.points(x, y)
    rib = np.zeros_like(x)
    for ln in m["ribbons"]:
        rib = np.maximum(rib, np.exp(-(shapely.distance(ln, pts) / RIBBON_M) ** 2))
    strength = (0.18 + 0.27 * (1 - city._smooth(0.6 * S, 2.4 * S, d))) * (1 - city._smooth(2.2 * S, 3.1 * S, d))
    dens = np.maximum(dens, rib * strength * (1 + 0.15 * noise(f, x, y)))
    for nd in c.nodes[1:]:                    # the neighbourhood centres
        if nd.role != "harbour":
            dens = dens + 0.3 * nd.weight * np.exp(-(np.hypot(x - nd.center[0], y - nd.center[1]) / (0.35 * S)) ** 2) * (1 - dens)
    dens = dens + 0.15 * np.exp(-np.maximum(f.inland(x, y), 0) / 250) * np.exp(-(d / (1.4 * S)) ** 2)   # the lakefront
    dens = dens + 0.05 * noise(f, x, y)
    edge = np.minimum.reduce([x - x0, x1 - x, y - y0, y1 - y])
    dens = dens * city._smooth(0, 250, edge)
    dens = dens * (1 - city._smooth(0.5 * c.max_slope, c.max_slope, f.slope(x, y)))
    return x0, y0, len(xs), len(ys), np.clip(dens, 0, 1).reshape(X.shape)


def density(f, x, y):
    """Fields.density for a metro: the raster, bilinear; nothing outside the bounds."""
    x0, y0, nx, ny, R = _cache(f)["raster"]
    x, y = np.asarray(x, float), np.asarray(y, float)
    fx, fy = (x - x0) / GRID_M, (y - y0) / GRID_M
    inside = (fx >= 0) & (fx <= nx - 1) & (fy >= 0) & (fy <= ny - 1)
    i, j = np.clip(np.floor(fy).astype(int), 0, ny - 2), np.clip(np.floor(fx).astype(int), 0, nx - 2)
    a, b = np.clip(fx - j, 0, 1), np.clip(fy - i, 0, 1)
    v = (R[i, j] * (1 - a) * (1 - b) + R[i, j + 1] * a * (1 - b) + R[i + 1, j] * (1 - a) * b + R[i + 1, j + 1] * a * b)
    return np.where(inside, v, 0.0)


def in_downtown(f, x: float, y: float) -> bool:
    """The tower cluster: downtown_m round the centre, its edge wobbling with the noise."""
    C = _cache(f)["C"]
    return math.dist((x, y), C) < f.city.preset.downtown_m * (1 + 0.2 * float(noise(f, x, y)))


def midrise(f, x: float, y: float) -> float | None:
    """Past the towers, mid-rise towers thin out over another 0.8 x downtown_m: the chance of one
    here (None: none here)."""
    dm = f.city.preset.downtown_m
    dd = math.dist((x, y), _cache(f)["C"]) - dm
    if not dm or dd < 0 or dd > 0.8 * dm:
        return None
    return 0.7 * (1 - dd / (0.8 * dm))


# --- ways and green --------------------------------------------------------------------------

def _shore_runs(c: CitySpec, f, off: float, ok) -> list[np.ndarray]:
    """Polylines `off` metres inland of the shore along the city's waterfront, kept where ok(p)."""
    C, S = np.array(city.main_centre(c), float), scale(c)
    k = coast(f.t)
    if k == "none":
        return []
    if k == "all":
        from . import terrain_gen
        ts = f.t.settings
        aC = math.atan2(C[1] - ts.centre[1], C[0] - ts.centre[0])
        span = min(math.pi, 1.9 * S / ts.island_r_m)
        a = np.arange(aC - span, aC + span, city.STEP_M / ts.island_r_m)
        r = terrain_gen.island_radius(f.t, a) - off
        pts = np.c_[ts.centre[0] + r * np.cos(a), ts.centre[1] + r * np.sin(a)]
    else:
        latC = float(C @ f.v)
        lat = np.arange(latC - 1.9 * S, latC + 1.9 * S, city.STEP_M)
        pts = np.outer(f._shore(lat) - off, f.u) + np.outer(lat, f.v)
    return [np.array(r) for r in city._runs(pts, ok)]


def ways(c: CitySpec, f, ok) -> list:
    """The diagonal arterials out of downtown and the lakeshore boulevard (kept where ok(p))."""
    C, S = np.array(city.main_centre(c), float), scale(c)
    nd = c.nodes[0]
    out = []
    for k, a in enumerate(arterial_angles(c, f.t)):
        r0 = city.hub_reach(c, nd, a)
        pts = C + np.outer(np.arange(r0, 3.2 * S, city.STEP_M), city._dir(a))
        out += city._clipped(f"diag{k}", "avenue", pts, ok, c.avenue_w, nd.id, min_len=120.0)
    thr = c.urban_threshold * 0.5
    runs = _shore_runs(c, f, LAKEFRONT_M + LAKESHORE_W / 2, lambda p: ok(p) and float(f.density(*p)) > thr)
    for i, r in enumerate([r for r in runs if len(r) > 6]):
        out.append(city.Way("lakeshore" if i == 0 else f"lakeshore{i}", "boulevard", LineString(r), LAKESHORE_W, None))
    return out


def lakefront(c: CitySpec, f, P_ways) -> list[Polygon]:
    """The lakefront park strip: from the shore to the lakeshore boulevard, along it."""
    out = []
    for w in P_ways:
        if not w.id.startswith("lakeshore"):
            continue
        a = np.array(w.line.coords)
        sea = a + (LAKEFRONT_M + LAKESHORE_W / 2 + 60) * _seaward(f, a)
        out.append(Polygon(np.vstack([a, sea[::-1]])).buffer(0))
    return out


def _seaward(f, pts: np.ndarray) -> np.ndarray:
    """Unit vectors towards the sea at each point."""
    if coast(f.t) == "all":
        d = pts - np.array(f.t.settings.centre, float)
        return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    return np.tile(f.u, (len(pts), 1))


def large_parks(c: CitySpec, f) -> list[Polygon]:
    """A few large parks (2-4) out in the neighbourhoods, turned to the grid."""
    rng = city.layout_rng(c, "metro-parks")
    C, S = np.array(city.main_centre(c), float), scale(c)
    u, _ = axes(c, f.t)
    inland = math.degrees(math.atan2(-u[1], -u[0]))
    want = int(rng.integers(2, 5))
    rivers_ = unary_union([ln.buffer(w / 2 + BANK_GREEN_M) for ln, w in river_lines(c, f.t)]) if c.nodes else Polygon()
    out: list[Polygon] = []
    for _ in range(60):
        if len(out) >= want:
            break
        a = (inland + rng.uniform(-80, 80)) if coast(f.t) != "none" else rng.uniform(0, 360)
        p = C + rng.uniform(0.45, 1.15) * S * city._dir(a)
        w, d = rng.uniform(300, 480), rng.uniform(240, 380)
        q = city._rect(float(p[0]), float(p[1]), w, d, c.preset.grid_deg)
        if (math.dist(p, C) < c.preset.downtown_m + 0.6 * max(w, d)
                or any(math.dist(p, n.center) < n.radius + 0.6 * max(w, d) + 40 for n in c.nodes)
                or any(q.distance(o) < 300 for o in out)
                or not bool(np.all(f.land(*np.array(q.exterior.coords).T)))
                or q.intersection(rivers_).area > 0.3 * q.area):
            continue
        out.append(q)
    return out


def green(c: CitySpec, f, P_ways) -> Polygon:
    """The metro's green: the river's banks, the lakefront strip, the large parks."""
    banks = [w.line.buffer(w.width / 2 + BANK_GREEN_M) for w in P_ways if w.kind == "river"]
    g = unary_union(banks + lakefront(c, f, P_ways) + large_parks(c, f))
    cores = unary_union([city.hub_poly(c, n, n.radius + 10) for n in c.nodes])
    return g.difference(cores)


TILE_M = 150.0          # green is laid as lawn slabs no bigger than this (greybox poly: outline only, no holes)


def tiles(parks: list[Polygon]) -> list[Polygon]:
    """Parks cut on a TILE_M grid: a long bank or strip, or one round a river, becomes small simple pieces."""
    from shapely.geometry import box
    out = []
    for p in parks:
        x0, y0, x1, y1 = p.bounds
        if x1 - x0 <= TILE_M and y1 - y0 <= TILE_M and not p.interiors:
            out.append(p)
            continue
        for gx in np.arange(math.floor(x0 / TILE_M) * TILE_M, x1, TILE_M):
            for gy in np.arange(math.floor(y0 / TILE_M) * TILE_M, y1, TILE_M):
                out += [q for q in city._polys(p.intersection(box(gx, gy, gx + TILE_M, gy + TILE_M))) if q.area > 50]
    return out


def industrial_centre(c: CitySpec, f, ways_) -> tuple | None:
    """The industrial zone: on the river (a fork's, or the main), well away from downtown."""
    pr = c.preset
    C = np.array(city.main_centre(c), float)
    S = scale(c)
    lo = max(pr.downtown_m + pr.industrial_m + 150, 0.55 * S)
    cand = [np.array(w.line.interpolate(s).coords[0]) for w in ways_ if w.kind == "river"
            for s in np.arange(0, w.line.length, 40)]
    cand = [p for p in cand if lo < math.dist(p, C) < max(lo + 300, 1.3 * S)
            and float(f.density(*p)) > c.urban_threshold
            and all(math.dist(p, n.center) > n.radius + pr.industrial_m for n in c.nodes)]
    if not cand:
        return None
    return tuple(cand[int(city.layout_rng(c, "industrial").integers(len(cand)))])


# --- bridges ---------------------------------------------------------------------------------

def water_cover(ways_) -> object | None:
    """Where a way crossing water is a bridge: each river's width and banks (prepared), or None."""
    from .terrain_gen import BANK_M
    polys = [w.line.buffer(w.width / 2 + BANK_M) for w in ways_ if w.kind in ("river", "canal")]
    return shapely.prepared.prep(unary_union(polys)) if polys else None


def deck(pts: list[list[float]], cover) -> list[list[float]]:
    """A draped way's points with each run over the water levelled from bank to bank: a bridge deck."""
    if cover is None or len(pts) < 3:
        return pts
    wet = [cover.contains(Point(p[0], p[1])) for p in pts]
    if not any(wet):
        return pts
    out = [list(p) for p in pts]
    i = 0
    while i < len(out):
        if not wet[i]:
            i += 1
            continue
        j = i
        while j < len(out) and wet[j]:
            j += 1
        ends = [out[k][2] for k in (i - 1, j) if 0 <= k < len(out)]    # the dry points either side
        z = max(ends) if ends else out[i][2]
        for k in range(i, j):
            out[k][2] = z
        i = j
    return out

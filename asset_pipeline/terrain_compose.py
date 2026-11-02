"""Described terrains (Layout tab's "Describe the terrain"): a TerrainCompose spec (schema.py) — a base and
landforms with places, sizes, heights and steepness, plus city hints — built into the same heights, water
and coast as a terrain preset.

    compose(spec, seed)   the TerrainSettings for a spec: its base mapped onto the generator's own settings
                          (coast, ground, island, plateau, beach), the spec kept in settings.compose
    spec_fields(t, s)     the TerrainSpec fields the spec sets (the sea's side, a bay, a headland), which the
                          city's shoreline, waterfront and promenade read as for any coast
    overlay(...)          terrain_gen.make's extra landforms (peaks, volcanoes, ridges, hills, plateaus,
                          cliffs, valleys), from their own random stream, so the base stays a preset's
    lines(t)              the spec's rivers (a delta's channels) and lakes, as terrain_gen.WaterLine: carved,
                          masked as water and carried into the plan like a preset's river
    weight(t, x, y)       the city hints as placement weights in [0, 1] (city_shape.fit_nodes reads them),
                          None without hints
    line(spec)            the spec as one readable line ("Island · volcano (centre, large, crater) · ...")

Everything is relative to the tile (R0 = 7.5 % of its extent) and the city's centre, so a described
terrain grows with the city size like a preset. A preset never has a spec: its heights are untouched.
"""
import math

import numpy as np
from shapely.geometry import LineString, Polygon

from .schema import TerrainCompose, TerrainFeature, TerrainSettings, TerrainSpec

STREAM = 0xC0DE           # the landforms' random stream: (this, settings seed, feature index)
DIRS = {"east": 0.0, "north-east": 45.0, "north": 90.0, "north-west": 135.0, "west": 180.0,
        "south-west": 225.0, "south": 270.0, "south-east": 315.0}
SIZE = {"small": 0.55, "medium": 1.0, "large": 1.7}
SPREAD = {"gentle": 1.35, "moderate": 1.0, "steep": 0.75}          # a gentler landform spreads wider
HEIGHT = {"peak": 420.0, "volcano": 650.0, "ridge": 260.0, "hill": 80.0, "hills": 140.0, "plateau": 160.0, "cliffs": 120.0,
          "headland": 12.0, "valley": 70.0, "lake": 6.0, "bay": 0.0, "river": 0.0}
SHAPE = {"peak": 1.0, "volcano": 1.6, "ridge": 1.0, "hill": 1.8, "hills": 1.0, "plateau": 1.6, "cliffs": 1.0, "headland": 1.0,
         "valley": 0.8, "lake": 0.8, "bay": 1.0, "river": 1.0}
BASE = {   # the generator's settings for each base (TerrainSettings fields)
    "island": dict(coast="all", relief_m=12.0, rise=0.012, beach_m=60.0),
    "coast": dict(coast="side", relief_m=10.0, rise=0.012, beach_m=80.0),
    "inland": dict(coast="none", relief_m=10.0, rise=0.004),
    "valley": dict(coast="side", relief_m=14.0, rise=0.01, beach_m=40.0),
    "plateau": dict(coast="none", relief_m=8.0, rise=0.0),
}
RING_TYPES = ("volcano", "peak", "hill", "lake", "plateau")
CITY_R = 0.3125            # a described terrain's city_r_m (x the tile): the standard city's half width, what "beyond" clears
BEYOND_PAD = 0.6           # a landform beyond the city: its centre this many of its radii past city_r_m
EDGE_HILLS = 6             # hills round the city's edges, unless a count is given
VALLEY_RIDGE_M = 420.0     # a valley base's two ridges: their height, distance off the axis (x half the tile), width (x the tile)
VALLEY_RIDGE_OFF = 0.33
VALLEY_RIDGE_W = 0.075


def _unit(deg: float) -> np.ndarray:
    return np.array([math.cos(math.radians(deg)), math.sin(math.radians(deg))])


def _smooth(e0, e1, x):
    t_ = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0, 1)
    return t_ * t_ * (3 - 2 * t_)


def r0(t: TerrainSpec) -> float:
    return 0.075 * t.extent_m


def radius(t: TerrainSpec, f: TerrainFeature) -> float:
    return r0(t) * SIZE[f.size] * SHAPE[f.type] * SPREAD[f.steepness]


# the most a landform may rise (a lake's or valley's depth), whatever a description or the model says
MAX_HEIGHT = {"peak": 1400.0, "volcano": 1600.0, "ridge": 900.0, "hill": 250.0, "hills": 250.0, "plateau": 600.0, "cliffs": 200.0,
              "headland": 40.0, "valley": 300.0, "lake": 30.0, "bay": 0.0, "river": 0.0}


def height(f: TerrainFeature) -> float:
    h = min(f.height_m, MAX_HEIGHT[f.type]) if f.height_m is not None else HEIGHT[f.type]
    return h * (1.4 if f.size == "large" and f.height_m is None else 0.7 if f.size == "small" and f.height_m is None else 1.0)


# --- the spec -> the generator's settings and the TerrainSpec ------------------------------------

def compose(spec: TerrainCompose, seed: int = 0, extent_m: float = 8000.0) -> TerrainSettings:
    """The TerrainSettings for a spec (name "described"): its base's generator settings, the ground, a
    base plateau's table, no beach under cliffs; the spec kept in `compose`."""
    s = dict(BASE[spec.base])
    s["base"] = spec.ground
    s["city_r_m"] = round(CITY_R * extent_m, 1)
    if spec.ground == "flat":
        s["relief_m"], s["rise"] = 2.5, min(s.get("rise", 0.01), 0.002)
    elif spec.ground == "rugged":
        s["relief_m"] = s["relief_m"] * 1.6
    if spec.base == "island":
        s["island_r_m"] = round(0.27 * extent_m, 1)
    if spec.base == "plateau":
        top = next((f for f in spec.features if f.type == "plateau" and f.at == "centre"), None)
        s["plateau_m"] = height(top) if top is not None else 180.0
        s["plateau_r_m"] = round(0.3 * extent_m * (SIZE[top.size] if top is not None else 1.0), 1)
    if any(f.type == "cliffs" for f in spec.features):
        s["beach_m"] = 0.0
    return TerrainSettings.model_validate(s | {"name": "described", "seed": int(seed), "compose": spec.model_dump()})


def edge_r(t: TerrainSpec, s: TerrainSettings, f: TerrainFeature) -> float:
    """How far out a landform beyond the city (or round its edges) stands: past city_r_m by BEYOND_PAD of its
    radius, inside the tile (on an island, inside its coast)."""
    d = s.city_r_m + BEYOND_PAD * radius(t, f)
    return min(d, 0.75 * s.island_r_m) if s.compose.base == "island" else min(d, 0.8 * t.extent_m / 2)


def behind(spec: TerrainCompose) -> str:
    """The compass side away from the sea (inland bases: away from the default side)."""
    return min(DIRS, key=lambda k: abs(((DIRS[k] - DIRS[spec.side] - 180) + 180) % 360 - 180))


def _place(t: TerrainSpec, s: TerrainSettings, f: TerrainFeature, k: int = 0) -> np.ndarray:
    """A feature's centre: the city's centre, out along a compass direction (beyond: past the city's edge), on
    the coast, inland, or round the city's edges (one point on that ring: behind the city)."""
    from .greybox import sea_axes, shore_at
    C = np.array(s.centre, float)
    spec = s.compose
    u, v = sea_axes(t)
    half = t.extent_m / 2
    if f.at == "edges":
        return C + edge_r(t, s, f) * _unit(DIRS[behind(spec)])
    if f.at in DIRS:
        d = edge_r(t, s, f) if f.beyond else 0.55 * s.island_r_m if spec.base == "island" else 0.3 * half
        return C + d * _unit(DIRS[f.at])
    if f.at == "coast":
        if spec.base == "island":
            return C + 0.95 * s.island_r_m * _unit(DIRS[spec.side])
        if s.coast == "side":
            lat = float(C @ v)
            return lat * v + (float(shore_at(t, lat)) - 0.6 * radius(t, f)) * u
        return C
    if f.at == "inland":
        if spec.base == "island":
            return C
        if s.coast == "side":
            return C - 0.35 * half * u
        return C + 0.3 * half * _unit(90.0)
    return C


def spec_fields(t: TerrainSpec, s: TerrainSettings) -> dict:
    """The TerrainSpec fields a spec sets: the sea's side (coast, valley), its bay and headland on that
    coast (the city's shoreline: greybox.shore_at). Empty for a preset."""
    spec = s.compose
    if spec is None or s.coast != "side":
        return {}
    from .greybox import sea_axes
    out = {"sea_dir": DIRS[spec.side], "bay_m": 0.0, "headland_m": 0.0}     # only the spec's own bay and headland
    t2 = t.model_copy(update=out)
    u, v = sea_axes(t2)
    for f in spec.features:
        if f.type not in ("bay", "headland"):
            continue
        lat = 0.0 if f.at in ("centre", "coast", "everywhere", "inland") else float(_place(t2, s, f) @ v)
        depth = 0.1 * t.extent_m * SIZE[f.size]
        width = 0.08 * t.extent_m * SIZE[f.size] * SPREAD[f.steepness]
        if f.type == "bay" and not out.get("bay_m"):
            out |= {"bay_m": round(depth, 1), "bay_lateral_m": round(lat, 1), "bay_width_m": round(width, 1)}
        elif f.type == "headland":
            if abs(lat) < 1 or out.get("bay_m"):   # the spec's headland is on the city's lateral line
                out |= {"headland_m": round(0.7 * depth, 1), "headland_width_m": round(width, 1)}
            else:                                  # elsewhere: a bay pushed out to sea
                out |= {"bay_m": round(-0.7 * depth, 1), "bay_lateral_m": round(lat, 1), "bay_width_m": round(width, 1)}
    return out


# --- heights --------------------------------------------------------------------------------------

def overlay(t: TerrainSpec, x, y, h, inland, land_w) -> np.ndarray:
    """terrain_gen.make's landforms over the base heights `h` (before the coast: land_w fades them out at
    the shore so the sea stays the sea)."""
    s = t.settings
    spec = s.compose
    from .terrain_gen import _noise
    n = t.resolution
    # a smooth variation in [-1, 1] along the landforms (not ridged noise: its creases shade as dark streaks)
    soft = _noise(n, np.random.default_rng([STREAM, s.seed, 99]), ((4, 0.65), (9, 0.35)))
    add = np.zeros_like(h, dtype=np.float64)
    cliff = np.zeros_like(h, dtype=np.float64)      # cliffs rise from the shore itself: not faded out at it
    if spec.base == "valley":              # two ridges either side of the axis running to the sea
        from .greybox import sea_axes
        u, v = sea_axes(t)
        C = np.array(s.centre, float)
        lat = (x - C[0]) * v[0] + (y - C[1]) * v[1]
        off, wd = VALLEY_RIDGE_OFF * t.extent_m / 2, VALLEY_RIDGE_W * t.extent_m
        hr = VALLEY_RIDGE_M * (1.2 if spec.ground == "rugged" else 1.0)
        for side in (-1, 1):            # a smooth rounded crest, rising and falling a little along its length
            add += hr * np.exp(-((lat - side * off) / wd) ** 2) * (0.85 + 0.15 * soft)
        add -= 25.0 * np.exp(-(lat / (0.6 * off)) ** 2)
    for i, f in enumerate(spec.features):
        rng = np.random.default_rng([STREAM, s.seed, i])
        H, R = height(f), radius(t, f)
        P = _place(t, s, f)
        dx, dy = x - P[0], y - P[1]
        d = np.hypot(dx, dy)
        if f.type == "volcano":
            k = {"gentle": 1.7, "moderate": 1.3, "steep": 1.0}[f.steepness]
            cone = H * np.clip(1 - d / R, 0, 1) ** k * (0.95 + 0.05 * soft)
            if f.crater:                          # the cone cut off at the rim, a bowl sunk in it
                rc = 0.17 * R
                rim = H * (1 - rc / R) ** k
                cone = np.minimum(cone, rim) - 0.3 * H * np.clip(1 - (d / rc) ** 2, 0, 1)
            add += cone
        elif f.type == "peak":
            add += H * np.exp(-2.2 * (d / R) ** 2) * (0.9 + 0.1 * soft)
        elif f.type == "ridge":
            for j in range(f.count):
                a = DIRS.get(f.at, rng.uniform(0, 180)) + 90 + rng.uniform(-15, 15)   # across the way out
                e = _unit(a)
                q = P + (j - (f.count - 1) / 2) * 1.2 * R * _unit(a + 90)
                along = (x - q[0]) * e[0] + (y - q[1]) * e[1]
                perp = -(x - q[0]) * e[1] + (y - q[1]) * e[0]
                add += H * np.exp(-(perp / (0.45 * R)) ** 2) * (1 - _smooth(1.2 * R, 2.2 * R, np.abs(along))) * (0.85 + 0.15 * soft)
        elif f.type == "hill":            # one broad rounded hill (a city on a hill: gentle enough to build on)
            add += H * np.exp(-(d / R) ** 2) * (0.92 + 0.08 * soft)
        elif f.type == "hills" and f.at == "edges":       # round the city, not under it; none out at sea
            m = f.count if f.count > 1 else EDGE_HILLS
            C, er = np.array(s.centre, float), edge_r(t, s, f)
            sea = DIRS[spec.side] if spec.base in ("coast", "valley") else None
            a0 = rng.uniform(0, 2 * math.pi)
            for j in range(m):
                a = a0 + 2 * math.pi * (j + rng.uniform(-0.25, 0.25)) / m
                if sea is not None and abs(((math.degrees(a) - sea) + 180) % 360 - 180) < 60:
                    a += math.radians(120) * (1 if ((math.degrees(a) - sea) % 360) < 180 else -1)
                rr = er + rng.uniform(-0.25, 0.25) * R
                hx, hy = C[0] + rr * math.cos(a), C[1] + rr * math.sin(a)
                hr = 0.5 * R * rng.uniform(0.7, 1.3)
                add += H * rng.uniform(0.6, 1.2) * np.exp(-((x - hx) ** 2 + (y - hy) ** 2) / hr ** 2)
        elif f.type == "hills":
            m = f.count if f.count > 1 else 5
            for _ in range(m):        # each hill broad enough to read at the tile's scale
                a, rr = rng.uniform(0, 2 * math.pi), 1.8 * R * math.sqrt(rng.uniform(0, 1))
                hx, hy = P[0] + rr * math.cos(a), P[1] + rr * math.sin(a)
                hr = 0.5 * R * rng.uniform(0.7, 1.3)
                add += H * rng.uniform(0.6, 1.2) * np.exp(-((x - hx) ** 2 + (y - hy) ** 2) / hr ** 2)
        elif f.type == "plateau" and not (spec.base == "plateau" and f.at == "centre"):
            e = {"gentle": 0.35, "moderate": 0.18, "steep": 0.07}[f.steepness] * R
            add += H * (1 - _smooth(R - e, R + e, d))
        elif f.type == "cliffs":
            wd = {"gentle": 200.0, "moderate": 90.0, "steep": 40.0}[f.steepness]
            w = 1.0
            if f.at in DIRS and not (s.coast == "side" and f.at == spec.side):   # on the sea's side: the whole coast
                b = np.degrees(np.arctan2(y - s.centre[1], x - s.centre[0]))
                w = np.exp(-((((b - DIRS[f.at]) + 180) % 360 - 180) / 55.0) ** 2)
            if s.coast == "side" and t.bay_m > 0:     # a bay's harbour stays low: the cliffs open round it
                from .greybox import sea_axes
                _, v = sea_axes(t)
                lat = x * v[0] + y * v[1]
                w = w * (1 - 0.95 * np.exp(-((lat - t.bay_lateral_m) / (0.7 * t.bay_width_m)) ** 2))
            cliff += H * _smooth(0, wd, inland) * w
        elif f.type == "valley":
            from .greybox import sea_axes
            u, _ = sea_axes(t)
            e = u if s.coast == "side" else _unit(DIRS.get(f.at, 90.0) + 90)
            along = dx * e[0] + dy * e[1]
            perp = -dx * e[1] + dy * e[0]
            add -= H * np.exp(-(perp / (0.6 * R)) ** 2) * (1 - _smooth(2.5 * R, 4.0 * R, np.abs(along)))
    out = h + add * land_w + cliff
    if spec.base == "island":              # an island's bay or headland past its own coast (spec_fields can't)
        for f in spec.features:
            if f.type in ("bay", "headland"):
                P = _place(t, s, f.model_copy(update={"at": "coast" if f.at in ("centre", "everywhere", "inland") else f.at}))
                R = radius(t, f)
                g = np.exp(-((x - P[0]) ** 2 + (y - P[1]) ** 2) / (0.6 * R) ** 2)
                out = np.where(g > 0.4, -4.0, out) if f.type == "bay" else np.maximum(out, 6.0 * (g > 0.45) - 0.5)
    return out


# --- water ----------------------------------------------------------------------------------------

def _meander(pts: np.ndarray, amp: float, rng) -> np.ndarray:
    seg = np.diff(pts, axis=0)
    L = np.concatenate([[0.0], np.cumsum(np.hypot(*seg.T))])
    d = pts[-1] - pts[0]
    nrm = np.array([-d[1], d[0]]) / max(np.hypot(*d), 1e-9)
    ph = rng.uniform(0, 2 * math.pi)
    w = amp * (np.sin(2 * math.pi * L / 1100 + ph) + 0.4 * np.sin(2 * math.pi * L / 430 + 2 * ph))
    w = w * np.minimum(1.0, np.minimum(L, L[-1] - L) / 300)       # pinned at both ends
    return pts + np.outer(w, nrm)


def _line(a, b, step=10.0) -> np.ndarray:
    n = max(2, int(math.dist(a, b) / step))
    return np.linspace(np.asarray(a, float), np.asarray(b, float), n)


def lines(t: TerrainSpec) -> list:
    """The spec's rivers (one, or a delta's channels fanning out to the coast) and lakes, as WaterLines."""
    from .greybox import sea_axes
    from .terrain_gen import WaterLine
    s = t.settings
    spec = s.compose
    out = []
    u, v = sea_axes(t)
    half = t.extent_m / 2 - 5
    C = np.array(s.centre, float)
    for i, f in enumerate(spec.features):
        rng = np.random.default_rng([STREAM, s.seed, i, 1])
        if f.type == "river":
            P = _place(t, s, f)
            w = 70.0 * SIZE[f.size]
            if spec.base == "island":             # from the island's heart down to its coast
                down = _unit(DIRS[spec.side] if f.at in ("centre", "everywhere") else
                             DIRS.get(f.at, DIRS[spec.side]))
                src, mouth = C + 0.15 * s.island_r_m * down, C + 1.15 * s.island_r_m * down
            elif s.coast == "side":               # from beyond the inland edge to the sea, past P
                from .greybox import shore_at
                lat = float(P @ v)
                sh = float(shore_at(t, lat))
                src, mouth = lat * v - 1.2 * half * u, lat * v + (sh + 0.12 * half) * u
            else:                                 # inland: across the tile, past P
                a = DIRS.get(f.at, 90.0) + 90
                src, mouth = P - 1.4 * half * _unit(a), P + 1.4 * half * _unit(a)
            if f.count <= 1:
                pts = _meander(_line(src, mouth), 140.0 * SIZE[f.size], rng)
                out.append(WaterLine(LineString(_clip(pts, half)), round(w, 1), 5.0, "down"))
                continue
            split = mouth - 0.45 * half * (mouth - src) / max(math.dist(src, mouth), 1e-9)   # a delta: the stem, then channels fanning to the coast
            nrm = np.array([-(mouth - src)[1], (mouth - src)[0]]) / max(math.dist(src, mouth), 1e-9)
            stem = _meander(_line(src, split), 120.0, rng)
            out.append(WaterLine(LineString(_clip(stem, half)), round(w * 1.2, 1), 5.0, "down"))
            spread = 0.22 * t.extent_m
            for j in range(f.count):
                off = (j - (f.count - 1) / 2) / max(f.count - 1, 1) * 2 * spread
                end = mouth + off * nrm + rng.uniform(-0.05, 0.05) * spread * nrm
                ch = _meander(_line(split, end), 70.0, rng)
                out.append(WaterLine(LineString(_clip(ch, half)), round(max(w * 0.55, 22.0), 1), 3.0, "down"))
    return [w for w in out if len(w.line.coords) > 2]


LAKE_SHORE_M = 45.0        # a lake's shore shelves down to its water over this width


def lakes(t: TerrainSpec) -> list:
    """The spec's lakes: (organic outline Polygon, depth m) — a wobbling, slightly stretched round shape."""
    s = t.settings
    out = []
    for i, f in enumerate(s.compose.features if s is not None and s.compose is not None else []):
        if f.type != "lake":
            continue
        rng = np.random.default_rng([STREAM, s.seed, i, 2])
        R = 1.0 * radius(t, f)
        P = _place(t, s, f)
        a = np.linspace(0, 2 * math.pi, 97)[:-1]
        r = np.ones_like(a)
        for k in (2, 3, 4, 5, 7):
            r += rng.uniform(0.03, 0.12) * np.cos(k * a + rng.uniform(0, 2 * math.pi)) / (1 + 0.15 * k)
        stretch, turn = rng.uniform(1.0, 1.5), rng.uniform(0, math.pi)
        x, y = R * r * np.cos(a) * stretch, R * r * np.sin(a)
        pts = np.c_[P[0] + x * math.cos(turn) - y * math.sin(turn), P[1] + x * math.sin(turn) + y * math.cos(turn)]
        out.append((Polygon(pts).buffer(0), height(f)))
    return out


def _lake_window(t: TerrainSpec, poly, pad: float):
    from .greybox import grid_coords
    x, y = grid_coords(t)
    n = t.resolution
    x0, y0, x1, y1 = poly.bounds
    j0 = max(int((x0 - pad) / t.extent_m * (n - 1) + (n - 1) / 2), 0)
    j1 = min(int((x1 + pad) / t.extent_m * (n - 1) + (n - 1) / 2) + 2, n)
    i0 = max(int((0.5 - (y1 + pad) / t.extent_m) * (n - 1)), 0)
    i1 = min(int((0.5 - (y0 - pad) / t.extent_m) * (n - 1)) + 2, n)
    return (i0, i1, j0, j1), x[i0:i1, j0:j1], y[i0:i1, j0:j1]


def carve_lakes(h: np.ndarray, t: TerrainSpec) -> np.ndarray:
    """Each lake's basin: its water level just under the lowest tenth of its shore, the bed `depth` below,
    the ground shelving down to the water over LAKE_SHORE_M outside its outline."""
    import shapely
    from scipy import ndimage
    h = h.copy()
    sp = t.extent_m / (t.resolution - 1)
    for poly, depth in lakes(t):
        (i0, i1, j0, j1), x, y = _lake_window(t, poly, LAKE_SHORE_M + 3 * sp)
        if x.size == 0:
            continue
        inside = shapely.contains_xy(poly, x, y)
        if not inside.any():
            continue
        sub = h[i0:i1, j0:j1]
        level = lake_level(t, h, poly)
        d_in = ndimage.distance_transform_edt(inside) * sp
        d_out = ndimage.distance_transform_edt(~inside) * sp
        bed = level - 0.6 - depth * _smooth(0, 3 * LAKE_SHORE_M, d_in)
        shore = np.minimum(sub, level + 0.5 + (sub - level - 0.5) * _smooth(0, LAKE_SHORE_M, d_out))
        h[i0:i1, j0:j1] = np.where(inside, np.minimum(sub, bed), np.where(d_out < LAKE_SHORE_M, shore, sub))
    return h


def lake_level(t: TerrainSpec, h: np.ndarray, poly) -> float:
    """A lake's water level: the lowest tenth of the ground on a ring just past its shore (ground the basin
    leaves alone, so the terrain build and the plan read the same level), less a little."""
    n = t.resolution
    ring = np.asarray(poly.buffer(LAKE_SHORE_M + 10).exterior.segmentize(t.extent_m / (n - 1)).coords)
    j = np.clip(np.round((ring[:, 0] / t.extent_m + 0.5) * (n - 1)).astype(int), 0, n - 1)
    i = np.clip(np.round((0.5 - ring[:, 1] / t.extent_m) * (n - 1)).astype(int), 0, n - 1)
    return float(np.percentile(h[i, j], 10)) - 0.8


def lake_mask(t: TerrainSpec) -> np.ndarray | None:
    """Which height cells are a described lake's water (terrain_gen.water_mask adds them), or None."""
    import shapely
    ls = lakes(t)
    if not ls:
        return None
    mask = np.zeros((t.resolution, t.resolution), bool)
    for poly, _ in ls:
        (i0, i1, j0, j1), x, y = _lake_window(t, poly, 0.0)
        mask[i0:i1, j0:j1] |= shapely.contains_xy(poly, x, y)
    return mask


def _clip(pts: np.ndarray, half: float) -> np.ndarray:
    return pts[(np.abs(pts[:, 0]) < half) & (np.abs(pts[:, 1]) < half)]


# --- city hints -----------------------------------------------------------------------------------

def weight(t: TerrainSpec, x, y):
    """The city hints as a placement weight in [0, 1] at points (None: no hints): a ring round a feature's
    lower slopes, the coast, the valley floor or river, the plateau's top."""
    s = t.settings
    spec = s.compose if s is not None else None
    if spec is None or not spec.city:
        return None
    x, y = np.asarray(x, float), np.asarray(y, float)
    w = np.zeros(np.broadcast(x, y).shape)
    C = np.array(s.centre, float)
    for hint in spec.city:
        if hint.kind == "ring":
            fs = [f for f in spec.features if f.type in RING_TYPES]
            f = spec.features[hint.feature] if hint.feature is not None and hint.feature < len(spec.features) else (fs[0] if fs else None)
            if f is None:
                continue
            P, R = _place(t, s, f), radius(t, f)
            rr = 1.1 * R if f.type in ("volcano", "peak") else 1.25 * R      # the lower slopes, or round the shore
            w = np.maximum(w, np.exp(-((np.hypot(x - P[0], y - P[1]) - rr) / (0.3 * R)) ** 2))
        elif hint.kind == "coast":
            from .terrain_gen import inland_of
            ii = inland_of(t, x, y)
            w = np.maximum(w, np.where(ii > 0, np.exp(-ii / 500.0), 0.0))
        elif hint.kind in ("valley", "river"):
            lns = [wl.line for wl in lines(t)] if hint.kind == "river" or any(f.type == "river" for f in spec.features) else []
            if lns:
                from shapely.geometry import MultiLineString
                import shapely
                g = MultiLineString(lns)
                dist = shapely.distance(shapely.points(np.column_stack([x.ravel(), y.ravel()])), g).reshape(w.shape)
                w = np.maximum(w, np.exp(-(dist / 450.0) ** 2) * (dist > 60))
            elif spec.base == "valley":
                from .greybox import sea_axes
                _, v = sea_axes(t)
                lat = (x - C[0]) * v[0] + (y - C[1]) * v[1]
                w = np.maximum(w, np.exp(-(lat / (0.15 * t.extent_m)) ** 2))
        elif hint.kind == "plateau":
            if spec.base == "plateau":
                w = np.maximum(w, 1 - _smooth(0.7 * s.plateau_r_m, s.plateau_r_m, np.hypot(x - C[0], y - C[1])))
            for f in spec.features:
                if f.type == "plateau":
                    P, R = _place(t, s, f), radius(t, f)
                    w = np.maximum(w, 1 - _smooth(0.6 * R, 0.9 * R, np.hypot(x - P[0], y - P[1])))
    return w


# --- the readable line ----------------------------------------------------------------------------

BASE_WORDS = {"island": "Island", "coast": "Coast", "inland": "Inland", "valley": "Valley", "plateau": "Plateau"}
HINT_WORDS = {"coast": "city along the coast", "valley": "city in the valley", "river": "city along the river",
              "plateau": "city on the plateau"}


def line(spec: TerrainCompose, inherited: str | None = None) -> str:
    """The spec as a short line: "Island · volcano (centre, large, crater) · city rings the lower slopes"; the base
    first, with the terrain it was kept from when the description named none ("Inland (from flat plain)")."""
    head = BASE_WORDS[spec.base]
    bits = [f"sea {spec.side}"] if spec.base in ("coast", "valley") else []
    if inherited:
        bits.append(f"from {inherited}")
    if bits:
        head += f" ({', '.join(bits)})"
    if spec.ground != "rolling":
        head += f", {spec.ground}"
    parts = [head]
    for f in spec.features:
        bits = [f.at, f.size]
        if f.height_m is not None:
            bits.append(f"{f.height_m:.0f} m")
        if f.steepness != "moderate":
            bits.append(f.steepness)
        if f.crater:
            bits.append("crater")
        name = f.type.replace("river", "river channels" if f.count > 1 else "river")
        if f.type == "ridge" and height(f) >= 400:
            name = "mountains"
        parts.append(f"{f'{f.count} ' if f.count > 1 else ''}{name} ({', '.join(bits)})")
    for h in spec.city:
        if h.kind == "ring":
            fs = [f for f in spec.features if f.type in RING_TYPES]
            f = spec.features[h.feature] if h.feature is not None and h.feature < len(spec.features) else (fs[0] if fs else None)
            parts.append("city rings the lower slopes" if f is not None and f.type in ("volcano", "peak")
                         else f"city rings the {f.type}" if f is not None else "city in a ring")
        else:
            parts.append(HINT_WORDS[h.kind])
    return " · ".join(parts)


# --- the plan on a described terrain (city.plan) --------------------------------------------------

STEEP_CELL_M = 30.0        # the steep-ground mask's cell
PARK_TILE_M = 60.0         # parks cut this small, so each flat piece follows the slope (viewer, greybox, Unreal)
LOOKOUT_GRADE = 0.09       # the lookout road climbs this steeply, winding round the cone
WATER_TYPES = ("river", "canal")


def described(t: TerrainSpec) -> bool:
    return t.settings is not None and t.settings.compose is not None


def steep_ground(f, limit: float):
    """Ground steeper than `limit` (rise / run, the city's max_slope), as polygons on a STEEP_CELL_M grid:
    no streets, blocks or parks go there. Specks under three cells are dropped, small gaps filled."""
    from scipy import ndimage
    from shapely.geometry import box
    from shapely.ops import unary_union
    g = f.slope_grid
    n = g.shape[0]
    e = f.t.extent_m
    k = max(1, int(round(STEEP_CELL_M / (e / (n - 1)))))
    m = (n // k) * k
    coarse = g[:m, :m].reshape(m // k, k, m // k, k).max(axis=(1, 3))
    mask = ndimage.binary_closing(coarse > limit, iterations=1)
    lab, nlab = ndimage.label(mask)
    sizes = ndimage.sum(mask, lab, range(1, nlab + 1))
    keep = np.isin(lab, np.nonzero(sizes >= 3)[0] + 1)
    cell = e / (n - 1) * k
    ii, jj = np.nonzero(keep)
    x0, y1 = -e / 2, e / 2
    boxes = [box(x0 + j * cell, y1 - (i + 1) * cell, x0 + (j + 1) * cell, y1 - i * cell) for i, j in zip(ii, jj)]
    return unary_union(boxes).buffer(0.5 * cell).buffer(-0.5 * cell).simplify(0.3 * cell) if boxes else Polygon()


def off_steep(ways: list, steep) -> list:
    """The ways with their stretches over steep ground cut out (water stays; it carves its own bed)."""
    from .city import Way, _polys_lines
    from shapely.prepared import prep
    ps = prep(steep)
    out = []
    for w in ways:
        if w.kind in WATER_TYPES or not ps.intersects(w.line):
            out.append(w)
            continue
        parts = [g for g in _polys_lines(w.line.difference(steep)) if g.length > 30]
        for i, g in enumerate(parts):
            out.append(Way(w.id if len(parts) == 1 else f"{w.id}~s{i}", w.kind, g, w.width, w.district))
    return out


def lookout(L, f, steep) -> list:
    """At most one winding road up the first peak or volcano that stands on steep ground: from the foot of
    its slope nearest the city's centre, round and up the cone at LOOKOUT_GRADE to its rim (a crater's) or
    summit. Its steeper stretches become stairs (city.split_by_grade)."""
    from .city import main_centre, nearest_node, split_by_grade
    t = L.terrain
    s = t.settings
    cone = next((ft for ft in s.compose.features if ft.type in ("volcano", "peak")), None)
    if cone is None or steep.is_empty:
        return []
    P = _place(t, s, cone)
    R = radius(t, cone)
    from shapely.geometry import Point as SPoint
    part = min(getattr(steep, "geoms", [steep]), key=lambda g: g.distance(SPoint(*P)))
    if part.distance(SPoint(*P)) > 0.3 * R:
        return []
    from shapely.ops import nearest_points
    C = main_centre(L.city)
    start = np.array(nearest_points(part.exterior, SPoint(*C))[0].coords[0])
    stop_r = (0.24 if cone.crater else 0.08) * R
    pts, p, side = [start], start.copy(), 1.0
    for _ in range(600):
        e = 6.0
        gx = (float(f.z(p[0] + e, p[1])) - float(f.z(p[0] - e, p[1]))) / (2 * e)
        gy = (float(f.z(p[0], p[1] + e)) - float(f.z(p[0], p[1] - e))) / (2 * e)
        gm = math.hypot(gx, gy)
        to = P - p
        if math.hypot(*to) < stop_r:
            break
        if gm < LOOKOUT_GRADE:                     # gentle: straight on to the top
            d = to / max(math.hypot(*to), 1e-9)
        else:
            up = np.array([gx, gy]) / gm
            tan = side * np.array([-up[1], up[0]])
            th = math.asin(min(0.95, LOOKOUT_GRADE / gm))
            d = math.cos(th) * tan + math.sin(th) * up
        q = p + 20.0 * d
        if not bool(f.land(*q)):
            break
        p = q
        pts.append(p.copy())
    if len(pts) < 5 or math.hypot(*(P - pts[-1])) > 0.5 * R:
        return []
    ln = LineString(pts).simplify(2.0)
    return split_by_grade(f, ln, "lookout", "lane", L.city.street_w, nearest_node(L.city, *P))


def tiles(parks: list, steep) -> list:
    """Parks off steep ground, cut on a PARK_TILE_M grid (each piece flat, draped at its corners: small
    enough to follow the ground)."""
    from shapely.geometry import box
    from .city import _polys
    out = []
    for p in parks:
        q = p.difference(steep) if steep is not None and not steep.is_empty else p
        for a in _polys(q):
            x0, y0, x1, y1 = a.bounds
            if x1 - x0 <= PARK_TILE_M and y1 - y0 <= PARK_TILE_M and not a.interiors:
                out.append(a)
                continue
            for gx in np.arange(math.floor(x0 / PARK_TILE_M) * PARK_TILE_M, x1, PARK_TILE_M):
                for gy in np.arange(math.floor(y0 / PARK_TILE_M) * PARK_TILE_M, y1, PARK_TILE_M):
                    out += [r for r in _polys(a.intersection(box(gx, gy, gx + PARK_TILE_M, gy + PARK_TILE_M))) if r.area > 50]
    return out

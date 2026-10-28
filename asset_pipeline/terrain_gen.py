"""Generated terrain from TerrainSettings (schema.py), apart from the city layout preset.

    make(t)            the heights (float32 metres, rows north -> south) of a TerrainSpec with settings
    river_line(t)      the settings' river (a polyline across the tile, flowing to the sea), or None
    carve(h, t, lines) water lowered into the ground along lines (rivers fall towards the sea, canals
                       and channels lie level), with banks, or canyon walls
    water_mask(...)    which height cells are that water (city.Fields reads it: water is not land)

A TerrainSpec without settings keeps greybox.make_terrain's legacy coast, so every project made
before terrain settings, and utopia on it, plans exactly as before. The settings' own seed draws
everything here; nothing reads the city seed.

The coast stays the spec's (sea_dir, shore_m, its bays: shore_at) for coast "side", so the city's
waterfront, promenade and piers work as before. Coast "all" is an island of island_r_m round the
centre (the sea all round, z below sea level), "none" is inland (no sea in the tile).
"""
import functools
import hashlib
import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString

from .schema import TerrainSettings, TerrainSpec

STREAM = 0x7E77            # the settings' random stream: (this, seed)
WATER_DEPTH_M = 2.0        # a river's or canal's bed lies this far below its surface
BANK_M = 24.0              # its banks slope up to the ground over this width
RELIEF = {"flat": 0.08, "rolling": 1.0, "rugged": 2.2}


@dataclass(frozen=True)
class WaterLine:
    """Water lowered into the ground along a line: `flow` down (a river, falling from its first
    point to its last), flat (a canal or channel, level), its surface `depth_m` below the ground."""
    line: LineString
    width: float
    depth_m: float = 5.0
    flow: str = "down"
    canyon_m: float = 0.0
    canyon_w_m: float = 160.0


def _noise(n: int, rng, octaves=((4, 0.55), (11, 0.3), (29, 0.15))) -> np.ndarray:
    from .greybox import _value_noise
    return sum(w * _value_noise(n, k, rng) for k, w in octaves)


def _grid(t: TerrainSpec):
    from .greybox import grid_coords
    x, y = grid_coords(t)
    return x, y, t.extent_m / (t.resolution - 1)


def inland_dir(t: TerrainSpec) -> np.ndarray:
    """Unit vector from the coast inland (the opposite of sea_dir)."""
    from .greybox import sea_axes
    return -sea_axes(t)[0]


def island_radius(t: TerrainSpec, ang):
    """Coast all: the island's radius at each angle (round the centre), wobbling with the seed."""
    s = t.settings
    rng = np.random.default_rng([STREAM, s.seed, 3])
    r = np.ones_like(np.asarray(ang, float))
    for k in (2, 3, 5):
        r = r + rng.uniform(0.03, 0.08) * np.cos(k * ang + rng.uniform(0, 2 * math.pi))
    return s.island_r_m * r


def inland_of(t: TerrainSpec, x, y):
    """Metres from the coast, positive on land: the spec's shoreline (coast side), the island's
    (coast all), or far from any sea (coast none)."""
    from .greybox import sea_axes, shore_at
    s = t.settings
    x, y = np.asarray(x, float), np.asarray(y, float)
    if s.coast == "none":
        return np.full_like(x, 1e6)
    if s.coast == "all":
        dx, dy = x - s.centre[0], y - s.centre[1]
        return island_radius(t, np.arctan2(dy, dx)) - np.hypot(dx, dy)
    u, v = sea_axes(t)
    return shore_at(t, x * v[0] + y * v[1]) - (x * u[0] + y * u[1])


def river_line(t: TerrainSpec) -> LineString | None:
    """The settings' river: from beyond the tile's inland edge through the city (offset along the
    coast) to the sea, meandering. Its first point is upstream."""
    s = t.settings
    if s is None or not s.river:
        return None
    from .greybox import sea_axes
    u, v = sea_axes(t)
    a = math.radians(s.river_angle)
    d = np.array([u[0] * math.cos(a) - u[1] * math.sin(a), u[0] * math.sin(a) + u[1] * math.cos(a)])   # downstream
    nrm = np.array([-d[1], d[0]])
    rng = np.random.default_rng([STREAM, s.seed, 7])
    ph = rng.uniform(0, 2 * math.pi)
    C = np.array(s.centre) + s.river_offset_m * v
    L = 0.75 * t.extent_m
    k = np.arange(-L, L + 1, 10.0)
    pts = C + np.outer(k, d) + np.outer(s.river_meander_m * np.sin(2 * math.pi * k / 1100 + ph)
                                        + 0.4 * s.river_meander_m * np.sin(2 * math.pi * k / 430 + 2 * ph), nrm)
    half = t.extent_m / 2 - 5
    pts = pts[(np.abs(pts[:, 0]) < half) & (np.abs(pts[:, 1]) < half)]
    return LineString(pts) if len(pts) > 2 else None


def terrain_lines(t: TerrainSpec) -> list[WaterLine]:
    s = t.settings
    ln = river_line(t)
    if ln is None:
        return []
    return [WaterLine(ln, s.river_w_m, s.river_depth_m, "down", s.canyon_m, s.canyon_w_m)]


def make(t: TerrainSpec) -> np.ndarray:
    """The heights of a TerrainSpec with settings (see the module docstring)."""
    s = t.settings
    x, y, sp = _grid(t)
    n = t.resolution
    rng = np.random.default_rng([STREAM, s.seed])
    noise = _noise(n, rng)
    ridged = 1 - np.abs(_noise(n, rng, ((7, 0.6), (17, 0.4))))
    inland = inland_of(t, x, y)
    dx, dy = x - s.centre[0], y - s.centre[1]
    r, ang = np.hypot(dx, dy), np.arctan2(dy, dx)
    amp = s.relief_m * RELIEF[s.base]
    relief = amp * (0.5 + 0.5 * noise) + (amp * 0.6 * ridged if s.base == "rugged" else 0.0)
    if s.coast == "none":       # inland: rising gently away from the spec's sea side
        idir = inland_dir(t)
        h = 30 + s.rise * (x * idir[0] + y * idir[1] + t.extent_m / 2) + relief
    else:
        h = 3 + s.rise * np.maximum(inland, 0) + relief * (1 - np.exp(-np.maximum(inland, 0) / 200))
    if s.mountains_m:           # the ring round the city, open at its passes (the first one facing the sea)
        ring = np.exp(-((r - s.mountains_at_m) / (s.mountains_w_m / 2)) ** 2) * (0.7 + 0.3 * ridged)
        if s.mountain_gaps:
            idir = inland_dir(t)
            sea = math.atan2(-idir[1], -idir[0])
            for k in range(s.mountain_gaps):
                g = sea + 2 * math.pi * k / s.mountain_gaps
                da = np.angle(np.exp(1j * (ang - g)))
                ring = ring * (1 - np.exp(-(da / math.radians(s.gap_deg / 2)) ** 2))
        h = h + s.mountains_m * ring * (1 - np.exp(-np.maximum(inland, 0) / 250))
    if s.hills:                 # hills inside the city
        for _ in range(s.hills):
            a, rr = rng.uniform(0, 2 * math.pi), s.city_r_m * math.sqrt(rng.uniform(0.04, 0.6))
            hx, hy = s.centre[0] + rr * math.cos(a), s.centre[1] + rr * math.sin(a)
            hr = s.hill_r_m * rng.uniform(0.7, 1.3)
            h = h + s.hill_m * rng.uniform(0.6, 1.2) * np.exp(-((x - hx) ** 2 + (y - hy) ** 2) / hr ** 2)
    if s.plateau_m:             # a table under the city; terraces step down its sides
        lift = 1 - _smooth(s.plateau_r_m - 180, s.plateau_r_m + 180, r)
        if s.terraces:
            k = s.terraces
            stepped = np.floor(lift * k) / k
            frac = lift * k - np.floor(lift * k)
            lift = stepped + _smooth(0.82, 1.0, frac) / k
        h = h + s.plateau_m * lift * (1 - np.exp(-np.maximum(inland, 0) / 150))
    if s.coast != "none":       # a beach along the shore, then the sea shelving away
        if s.beach_m:
            w = 1 - _smooth(0, s.beach_m, inland)
            h = h * (1 - w) + (1.2 + 0.02 * np.maximum(inland, 0)) * w
        h = np.where(inland > 0, h, np.maximum(-0.08 * (-inland), -40.0) - 0.5)
    h = h.astype(np.float32)
    h, _ = carve(h, t, terrain_lines(t))
    return h


def _smooth(e0, e1, x):
    t_ = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0, 1)
    return t_ * t_ * (3 - 2 * t_)


def _raster(t: TerrainSpec, pts: np.ndarray):
    """Grid cells (i rows north -> south, j) of points, kept in the tile."""
    n = t.resolution
    j = np.round((pts[:, 0] / t.extent_m + 0.5) * (n - 1)).astype(int)
    i = np.round((0.5 - pts[:, 1] / t.extent_m) * (n - 1)).astype(int)
    ok = (i >= 0) & (i < n) & (j >= 0) & (j < n)
    return i[ok], j[ok], ok


@functools.lru_cache(maxsize=16)
def _distance(key: str, t_json: str, coords: bytes, reach: float):
    """(window, distance to the line in m, nearest sample index) over the line's window of the grid."""
    t = TerrainSpec.model_validate_json(t_json)
    pts = np.frombuffer(coords).reshape(-1, 2)
    sp = t.extent_m / (t.resolution - 1)
    i, j, ok = _raster(t, pts)
    if not len(i):
        return None
    pad = int(reach / sp) + 2
    n = t.resolution
    i0, i1, j0, j1 = max(i.min() - pad, 0), min(i.max() + pad + 1, n), max(j.min() - pad, 0), min(j.max() + pad + 1, n)
    mask = np.zeros((i1 - i0, j1 - j0), bool)
    idx = np.full(mask.shape, -1, np.int64)
    which = np.nonzero(ok)[0]
    mask[i - i0, j - j0] = True
    idx[i - i0, j - j0] = which
    dist, (ii, jj) = ndimage.distance_transform_edt(~mask, sampling=sp, return_indices=True)
    return (i0, i1, j0, j1), dist.astype(np.float32), idx[ii, jj]


def _line_samples(line: LineString, step: float) -> np.ndarray:
    ln = LineString(line.coords).segmentize(step)
    return np.asarray(ln.coords, float)


def _key(t: TerrainSpec, w: WaterLine) -> tuple[str, str, bytes, float]:
    pts = _line_samples(w.line, max(t.extent_m / (t.resolution - 1) / 2, 1.0))
    tj = t.model_dump_json(include={"extent_m", "resolution"})
    reach = w.width / 2 + BANK_M + (w.canyon_w_m if w.canyon_m else 0) + 10
    b = pts.tobytes()
    return hashlib.sha1(b + tj.encode() + str(reach).encode()).hexdigest(), tj, b, reach


def carve(h: np.ndarray, t: TerrainSpec, lines: list[WaterLine], sea_level: float = 0.0):
    """Lower water into the ground along each line: the surface `depth_m` below the ground beside
    it (a river's falls towards its last point, never rising; a flat one lies level at its lowest
    ground), the bed WATER_DEPTH_M under that, banks up to the ground over BANK_M (or canyon walls
    canyon_m high, canyon_w_m wide). The sea is left alone. Returns (heights, surface level grid
    with NaN off the water)."""
    if not lines:
        return h, None
    h = h.astype(np.float32).copy()
    level_grid = np.full(h.shape, np.nan, np.float32)
    for w in lines:
        key, tj, b, reach = _key(t, w)
        got = _distance(key, tj, b, reach)
        if got is None:
            continue
        (i0, i1, j0, j1), dist, near = got
        pts = np.frombuffer(b).reshape(-1, 2)
        i, j, ok = _raster(t, pts)
        ground = np.full(len(pts), np.nan)
        ground[np.nonzero(ok)[0]] = h[i, j]
        g = np.where(np.isnan(ground), np.nanmax(ground) if np.isfinite(ground).any() else 0.0, ground)
        win = max(3, int(160 / max(np.mean(np.hypot(*np.diff(pts, axis=0).T)), 0.5)))
        g = ndimage.uniform_filter1d(g, win, mode="nearest")
        if w.flow == "flat":
            level = np.full(len(g), float(np.min(g)) - w.depth_m)
        else:                                       # falls from upstream (index 0) to the sea, never rising
            level = np.minimum.accumulate(g) - w.depth_m
        level = np.maximum(level - w.canyon_m, sea_level + 0.3)     # a canyon: the river runs canyon_m further down
        sub, lev = h[i0:i1, j0:j1], level[near]
        half = w.width / 2
        on = sub > sea_level                        # never into the sea
        water = (dist < half) & on
        bed = lev - WATER_DEPTH_M
        bank = np.minimum(sub, lev + 0.4 + (sub - lev - 0.4) * _smooth(half, half + BANK_M, dist))
        if w.canyon_m:                              # walls: the canyon floor at the water, rising steeply to the rim
            wall = lev + 0.4 + w.canyon_m * _smooth(half + 6, half + w.canyon_w_m, dist) ** 0.6
            bank = np.minimum(bank, np.where(dist < half + w.canyon_w_m, wall, sub))
        new = np.where(water, np.minimum(sub, bed), np.where(on & (dist < half + BANK_M + (w.canyon_w_m if w.canyon_m else 0)),
                                                              bank, sub))
        h[i0:i1, j0:j1] = new
        lg = level_grid[i0:i1, j0:j1]
        level_grid[i0:i1, j0:j1] = np.where(water, lev, lg)
    return h, level_grid


def water_mask(t: TerrainSpec, lines: list[WaterLine]) -> np.ndarray | None:
    """Which cells of the height grid are lowered water (a river's or canal's width), or None."""
    if not lines:
        return None
    mask = np.zeros((t.resolution, t.resolution), bool)
    for w in lines:
        key, tj, b, reach = _key(t, w)
        got = _distance(key, tj, b, reach)
        if got is None:
            continue
        (i0, i1, j0, j1), dist, _ = got
        mask[i0:i1, j0:j1] |= dist < w.width / 2
    return mask


def z_range_for(h: np.ndarray) -> tuple[float, float]:
    """A z range holding these heights with room to spare (the 16-bit PNG's span)."""
    lo, hi = float(h.min()), float(h.max())
    return (min(-60.0, math.floor(lo / 20) * 20 - 20), max(160.0, math.ceil(hi / 50) * 50 + 50))


def with_settings(t: TerrainSpec, s: TerrainSettings) -> TerrainSpec:
    """The spec with these settings: generated (no heightmap or far build), its z range fitted."""
    t2 = t.model_copy(update={"settings": s, "heightmap": None, "far": None, "city_radius_m": 0.0})
    t2 = t2.model_copy(update={"z_range": z_range_for(make(t2))})
    return t2


# --- terrain presets -------------------------------------------------------------------------

_S = TerrainSettings
# name: (one line for the Terrain picker, the settings)
CATALOG: dict[str, tuple[str, TerrainSettings]] = {
    "flat-plain": ("A flat inland plain: barely a rise, no sea.",
                   _S(base="flat", relief_m=3, rise=0.004, coast="none")),
    "coastal-plain": ("A low coastal plain: gentle ground rising slowly from a wide beach.",
                      _S(base="rolling", relief_m=6, rise=0.008, beach_m=120)),
    "river-valley": ("A river valley: a river through the city to the sea between two ridges.",
                     _S(base="rolling", relief_m=18, rise=0.02, river=True, river_w_m=80, river_depth_m=5,
                        mountains_m=190, mountains_at_m=2300, mountains_w_m=1100, mountain_gaps=2, gap_deg=50)),
    "hill-city": ("A city of hills: steep knolls inside the city on rolling ground.",
                  _S(base="rolling", relief_m=22, rise=0.02, hills=6, hill_m=70, hill_r_m=240)),
    "mountain-bowl": ("A mountain bowl: a high ring of mountains round the city, one pass to the sea.",
                      _S(base="rolling", relief_m=14, rise=0.015, mountains_m=380, mountains_at_m=2200,
                         mountains_w_m=1000, mountain_gaps=1, gap_deg=45)),
    "plateau": ("A plateau: the city on a raised table with steep, broken sides.",
                _S(base="rugged", relief_m=8, rise=0.01, plateau_m=70, plateau_r_m=1300)),
    "island": ("An island: sea all round, a beach, a few hills.",
               _S(base="rolling", relief_m=12, rise=0.015, coast="all", island_r_m=2000, beach_m=80, hills=2, hill_m=40)),
    "canyon": ("A canyon: a river cut deep into a high inland plateau.",
               _S(base="rugged", relief_m=8, rise=0.0, coast="none", plateau_m=90, plateau_r_m=9000, river=True,
                  river_w_m=50, river_depth_m=4, canyon_m=60, canyon_w_m=140)),
}
PRESETS: dict[str, TerrainSettings] = {n: s.model_copy(update={"name": n}) for n, (_, s) in CATALOG.items()}
DESCRIPTIONS = {n: d for n, (d, _) in CATALOG.items()}


def get(name: str, seed: int | None = None) -> TerrainSettings:
    if name not in PRESETS:
        raise ValueError(f"unknown terrain preset {name!r} (presets: {', '.join(PRESETS)})")
    s = PRESETS[name].model_copy(deep=True)
    return s.model_copy(update={"seed": int(seed)}) if seed is not None else s


def summary() -> list[dict]:
    """For the UI: each terrain preset's name and one-line description."""
    return [{"name": n, "description": DESCRIPTIONS[n]} for n in PRESETS]

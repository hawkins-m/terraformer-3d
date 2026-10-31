"""The Layout tab's 3D city viewer (static/city3d.js): the city plan as compact arrays the browser
draws with three.js, with no Blender render.

    buildings   [x, y, z, w, d, h, rot_deg, type, district] per box (TYPES, districts by index); the
                greybox's hand edits (Re-read .blend: sw_site.slot_moves) move, copy or remove them
    ways        {kind, w, pts [[x, y, z]...]} draped on the ground (a metro's bridges level)
    parks       rings [[x, y]...] (exterior first, then holes); paving the same (markets, interchanges)
    terrain     the plan's heights, decimated to TERRAIN_N per side, rows north -> south

site/city3d.json caches it: written when the plan is made (sw_site.plan_city), made again when the
layout, the terrain or the greybox changed since (its `sig`).
"""
import math

import numpy as np

from . import city
from .project import ProjectStore, read_json, write_json

VERSION = 1
TYPES = ["tower", "midrise", "rowhouse", "house", "industry", "landmark", "other"]
TERRAIN_N = 257
WATER = ("river", "canal")
STEP_M = 20.0


def path(store: ProjectStore):
    from .stages import sw_site
    return sw_site.site_dir(store) / "city3d.json"


def sig(store: ProjectStore) -> list:
    """What the scene depends on: the layout, a terrain heightmap, the greybox and its build."""
    from .stages import sw_site
    sd = sw_site.site_dir(store)
    mt = lambda p: round(p.stat().st_mtime, 3) if p.is_file() else None  # noqa: E731
    files = [sd / "layout.json", sd / "greybox.json", sd / "build" / "built_slots.json"]
    try:
        hm = sw_site.load_layout(store).terrain.heightmap
    except FileNotFoundError:
        hm = None
    return [VERSION] + [mt(p) for p in files] + [mt(store.root / hm) if hm else None]


def building_type(kind: str, h: float, storey_m: float, landmark_m: float) -> int:
    if kind == "tower":
        return TYPES.index("landmark" if h > landmark_m else "tower" if h > 12 * storey_m else "midrise")
    if kind == "housing":
        return TYPES.index("midrise" if h > 4.6 * storey_m else "rowhouse")
    if kind in ("houses", "industrial", "mall"):
        return TYPES.index({"houses": "house", "industrial": "industry", "mall": "midrise"}[kind])
    return TYPES.index("other")


def slot_id(kind: str, x: float, y: float, tile_m: float) -> str:
    """The greybox slot a plan building is built in (city.add_slots groups them per kind and tile)."""
    return f"{kind}-x{int(math.floor(x / tile_m))}y{int(math.floor(y / tile_m))}"


def _r(v, nd=1):
    return round(float(v), nd)


def _edits(store: ProjectStore) -> dict | None:
    """{built slot id: [(slot id now, D)]} from the greybox's hand edits, or None (never built / read)."""
    from .stages import sw_site
    try:
        gb = sw_site.load_greybox(store)
    except FileNotFoundError:
        return None
    _, moves = sw_site.slot_moves(store, gb)
    return moves or None


def _moved(row: list, D: np.ndarray) -> list:
    """A building row carried by a slot's world transform D (turn about z, scale, move)."""
    x, y, z, w, d, h, rot = row[:7]
    k = np.linalg.norm(D[:3, :3], axis=0)
    p = D @ np.array([x, y, z, 1.0])
    turn = math.degrees(math.atan2(D[1, 0], D[0, 0]))
    return [_r(p[0]), _r(p[1]), _r(p[2]), _r(w * k[0]), _r(d * k[1]), _r(h * k[2]), _r(rot + turn), *row[7:]]


def scene(store: ProjectStore, P=None, heights: np.ndarray | None = None) -> dict:
    """The viewer's scene for the project's city (planned now unless P and its heights are given);
    written to site/city3d.json."""
    from .greybox import Heights
    from .stages import sw_site
    if P is None:
        layout = sw_site.load_layout(store)
        if layout.city is None:
            raise ValueError("the layout has no city spec")
        heights = sw_site.plan_terrain(store, layout)
        P = city.plan(layout, heights)
    L = P.layout
    c, t = L.city, L.terrain
    hs = Heights(heights, t.extent_m)
    sm = c.storey_m
    landmark_m = 1.1 * c.preset.tower_storeys[0] * sm
    districts = sorted({b.district for b in P.buildings if b.district} | {n.id for n in c.nodes})
    di = {d: i for i, d in enumerate(districts)}
    moves = _edits(store)
    rows = []

    def add(row, sid):
        if moves is None or sid not in moves:
            rows.append(row)
            return
        for _, D in moves[sid]:           # itself and its copies; none: deleted in Blender
            rows.append(row if np.allclose(D, np.eye(4), atol=1e-9) else _moved(row, D))
    for b in P.buildings:
        add([_r(b.x), _r(b.y), _r(b.z), _r(b.w), _r(b.d), _r(b.h), _r(b.rot),
             building_type(b.kind, b.h, sm, landmark_m), di.get(b.district, -1)], slot_id(b.kind, b.x, b.y, c.tile_m))
    for b in P.piers:
        add([_r(b.x), _r(b.y), _r(b.z), _r(b.w), _r(b.d), _r(b.h), _r(b.rot), TYPES.index("other"),
             di.get(b.district, -1)], "quay")
    for b in P.typed:                   # catalog buildings: their solid parts
        ty = TYPES.index("midrise" if b.family in ("housing", "mixed") and b.storeys >= 5
                         else "rowhouse" if b.family in ("housing", "mixed") else "other")
        for pt in b.parts:
            if pt.role in city.SOLID_ROLES and isinstance(pt.prim, str):
                rows.append([_r(pt.x), _r(pt.y), _r(pt.z), _r(pt.w), _r(pt.d), _r(pt.h), _r(pt.rot), ty, di.get(b.district, -1)])
    cover = None
    if c.preset.metro:
        from . import city_metro
        cover = city_metro.water_cover(P.ways)
    ways = []
    for w in P.ways:
        lift = city.WATER_LIFT if w.kind in WATER else (0.4 if w.kind == "highway" else 0.3)
        pts = city._drape(hs.many, list(w.line.coords), lift, STEP_M)
        if cover is not None and w.kind not in WATER:
            from . import city_metro
            pts = city_metro.deck(pts, cover)
        ways.append({"kind": w.kind, "w": _r(w.width), "pts": [[_r(a), _r(b_), _r(z)] for a, b_, z in pts]})

    def rings(q):
        q = q.simplify(1.5)
        return [[[_r(x), _r(y)] for x, y in r.coords[:-1]] for r in [q.exterior, *q.interiors]]
    parks = [rings(q) for q in P.parks if not q.is_empty]
    paving = [rings(q) for q in list(P.interchanges) + list(P.culdesacs) + [m[0] for m in P.markets] if not q.is_empty]
    moat = [rings(q) for q in P.moat if not q.is_empty]
    hd = sw_site._downsample(heights, TERRAIN_N)
    x0, y0, x1, y1 = c.bounds
    out = {"sig": sig(store), "version": VERSION, "types": TYPES, "districts": districts,
           "preset": c.preset.name, "storey_m": sm, "sea_level": L.sea_level,
           "extent": t.extent_m, "bounds": [x0, y0, x1, y1], "centre": list(city.main_centre(c)),
           "terrain": {"n": int(hd.shape[0]), "z": [_r(v) for v in hd.ravel()]},
           "buildings": rows, "ways": ways, "parks": parks, "paving": paving, "water": moat,
           "edited": moves is not None}
    write_json(path(store), out)
    return out


def load(store: ProjectStore) -> dict:
    """The cached scene when it still matches the project, else a new one."""
    d = read_json(path(store), default=None)
    if d and d.get("sig") == sig(store):
        return d
    return scene(store)

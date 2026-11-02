"""Extended building types (city mode): the generator places them on top of the plan, behind the
project setting `CitySpec.extended_types` ("Extended building types" on the Layout tab).

The setting is off for every project made before it and left out of layout.json while off, so those
plans, greyboxes and Unreal builds stay byte for byte as they were; new projects and Roll a city turn
it on. Each type is

  - placed by its own rules (`place`), from its own random stream (never the city seed's), after the
    rest of the plan: it clears the buildings, pads and trees under its footprint;
  - a Structure of pieces made from the shared unit meshes (box, gable, cylinder, cone, dome), so the
    greybox and the Unreal stand-in instance them like every other building;
  - its own greybox slot type (the type id; one slot per type and tile), coloured in the 3D viewer;
  - an entry in the Building types editor (`DEFAULTS`: the Look words frames and asset sheets use;
    the user's edits are kept in `CitySpec.extended_looks`). They are not catalog fill types: the
    catalog's mixes never pick them, and a project with no catalog keeps the plain massing.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point, Polygon

from .schema import CitySpec, Typology

T = Typology
# The Building types editor's entries (Look: In frames / Asset sheet / Roofs / Facades), in the order shown
DEFAULTS: dict[str, Typology] = {t.id: t for t in [
    T(id="castle", name="Castle", family="civic", form="courtyard", storeys=(3, 9), width=(120, 220), depth=(120, 220),
      max_count=1,
      prompt="a medieval stone castle on the central hill: a tall square keep, crenellated curtain walls, round corner "
             "towers with conical slate roofs and a twin-towered gatehouse facing the main road",
      desc="A medieval castle: a square stone keep about 30 m tall with corner turrets, a square curtain wall 12 m "
           "high with a crenellated wall walk, round corner towers under conical roofs, a gatehouse of two drum towers "
           "over an arched gate passage, a great hall with a pitched roof and a small chapel inside the bailey.",
      roofs=["conical slate tower roofs", "crenellated flat roof on the keep", "steep pitched slate on the hall"],
      facades=["rough-coursed limestone ashlar", "arrow slits", "machicolations under the parapets"]),
]}
del T

SHAPES = ("box", "gable", "cyl", "cone", "dome")      # the viewer's row column 9 (box when left out)
CONE = {"kind": "cone", "r": 0.5, "h": 1.0, "seg": 16}


@dataclass
class Piece:
    """One unit mesh, scaled: centre of its base (x, y, z), turn about z, size (w along the turn, d across, h)."""
    prim: str          # box | gable | cyl | cone | dome
    role: str
    x: float
    y: float
    z: float
    rot: float
    w: float
    d: float
    h: float


@dataclass
class Structure:
    type: str
    x: float
    y: float
    rot: float
    district: str | None
    foot: Polygon
    parts: list[Piece] = field(default_factory=list)


def on(c: CitySpec | None) -> bool:
    return bool(c is not None and getattr(c, "extended_types", False))


def types(c: CitySpec | None) -> list[Typology]:
    """The extended types' editor entries (the user's edits over the defaults); none while the setting is off."""
    if not on(c):
        return []
    own = {t.id: t for t in getattr(c, "extended_looks", [])}
    return [own.get(i, t) for i, t in DEFAULTS.items()]


def all_typologies(c: CitySpec | None) -> list[Typology]:
    """The catalog's building types and the extended ones: what frames, asset sheets and the library look up by id."""
    return (list(getattr(c, "typologies", [])) if c is not None else []) + types(c)


def rng(c: CitySpec, *key) -> np.random.Generator:
    from .city import _key
    return np.random.default_rng([0xE7E7, int(c.layout_seed), int(c.seed), *(_key(str(k)) for k in key)])


# --- building pieces in a structure's own frame (u along its turn, v across) ----------------------

class Frame:
    """Pieces placed in a local frame: (u, v) metres from the centre, turned by `rot` deg; each piece
    stands on the ground at its own place (the lowest of its corners and middle), sunk `sink` m."""

    def __init__(self, f, x: float, y: float, rot: float, sink: float = 0.4):
        self.f, self.x, self.y, self.rot, self.sink = f, x, y, rot, sink
        a = math.radians(rot)
        self.u, self.v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
        self.parts: list[Piece] = []

    def at(self, u: float, v: float) -> np.ndarray:
        return np.array([self.x, self.y]) + u * self.u + v * self.v

    def ground(self, u: float, v: float, w: float, d: float, top: bool = False) -> float:
        """The lowest ground under a w x d piece at (u, v), sunk (top: the highest, sunk the same)."""
        pts = np.array([self.at(u + su * w / 2, v + sv * d / 2) for su in (-1, 0, 1) for sv in (-1, 0, 1)])
        z = self.f.z(pts[:, 0], pts[:, 1])
        return float(np.max(z) if top else np.min(z)) - self.sink

    def add(self, prim: str, role: str, u: float, v: float, w: float, d: float, h: float, z: float | None = None,
            turn: float = 0.0) -> float:
        """A piece at (u, v), its base on the ground or at `z`; returns its top."""
        x, y = self.at(u, v)
        z = self.ground(u, v, w, d) if z is None else z
        self.parts.append(Piece(prim, role, round(float(x), 3), round(float(y), 3), round(z, 3),
                                round((self.rot + turn) % 360, 3), round(w, 3), round(d, 3), round(h, 3)))
        return z + h

    def wall(self, role: str, u0: float, v0: float, u1: float, v1: float, t: float, h: float, seg_m: float = 24.0) -> None:
        """A wall from (u0, v0) to (u1, v1), t thick: in segments of at most seg_m, each standing on its own
        lowest ground and reaching h above its highest (on a slope its top steps with the ground)."""
        n = max(1, math.ceil(math.hypot(u1 - u0, v1 - v0) / seg_m))
        along = u1 != u0
        for k in range(n):
            a, b = k / n, (k + 1) / n
            u, v = u0 + (u1 - u0) * (a + b) / 2, v0 + (v1 - v0) * (a + b) / 2
            ln = math.hypot(u1 - u0, v1 - v0) / n
            w, d = (ln, t) if along else (t, ln)
            lo = self.ground(u, v, w, d)
            hi = self.ground(u, v, w, d, top=True)
            self.add("box", role, u, v, w, d, h + hi - lo, z=lo)

    def tower(self, role: str, u: float, v: float, r: float, h: float, roof: float) -> None:
        """A round tower under a conical roof (a little wider than the tower: the eaves)."""
        top = self.add("cyl", role, u, v, 2 * r, 2 * r, h)
        if roof > 0:
            self.add("cone", f"{role}-roof", u, v, 2 * r + 1.2, 2 * r + 1.2, roof, z=top)

    def hall(self, role: str, u: float, v: float, w: float, d: float, h: float, roof: float, z: float | None = None) -> None:
        """A box under a pitched roof whose ridge runs along w."""
        top = self.add("box", role, u, v, w, d, h, z=z)
        # the unit gable's ridge runs along its own y: turned a quarter so it runs along w
        self.add("gable", f"{role}-roof", u, v, d + 0.6, w + 0.6, roof, z=top, turn=90)


def rect(x: float, y: float, w: float, d: float, rot: float) -> Polygon:
    from .city import _rect
    return _rect(x, y, w, d, rot)


# --- castle -------------------------------------------------------------------------------------

CASTLE_WALL_H = 12.0
CASTLE_WALL_T = 4.0
CASTLE_GATE_W = 14.0


def castle_node(c: CitySpec):
    """The node a castle stands on: the main centre's, in a walled-castle (an inner ward) or a star
    fortress layout; else None."""
    if not on(c) or not c.nodes:
        return None
    pr = c.preset
    if not (pr.inner_wall or (pr.walls != "none" and pr.wall_shape == "star")):
        return None
    from .city import main_centre
    C = main_centre(c)
    return min(c.nodes, key=lambda o: math.dist(o.center, C))


def castle_half(nd) -> float:
    """Half the curtain wall's side: most of the hub's core, within 40-120 m."""
    from .city import _rings
    return float(np.clip(0.62 * _rings(nd)["core"], 40.0, 120.0))


def castle_reach(nd) -> float:
    """How far from the node's centre the castle reaches towards its gate (the road ends there)."""
    return castle_half(nd) + 14.0


def castle_pad_r(nd) -> float:
    """A hill castle's levelled summit: its corners' reach."""
    return castle_reach(nd) * 1.42


def castle_centre(nd, f) -> tuple[float, float]:
    """Where the castle stands: the node's centre, or a hill node's summit."""
    from .city import summit
    return summit(nd, f.z) if nd.layout == "organic" else (float(nd.center[0]), float(nd.center[1]))


def castle_ground(c: CitySpec, f) -> Polygon | None:
    """The castle's ground (a disc round its corners), kept free of blocks and other roads; None without a castle."""
    nd = castle_node(c)
    if nd is None:
        return None
    return Point(castle_centre(nd, f)).buffer(castle_pad_r(nd) + 6, quad_segs=24)


def clip_ways(ways: list, ground: Polygon) -> list:
    """The ways with their stretches over the castle's ground cut out (its own gate road stays)."""
    from .city import Way, _polys_lines
    out = []
    for w in ways:
        if w.id == "castle-gate-av" or not w.line.intersects(ground):
            out.append(w)
            continue
        parts = [g for g in _polys_lines(w.line.difference(ground)) if g.length > 15]
        for i, g in enumerate(parts):
            out.append(Way(w.id if len(parts) == 1 else f"{w.id}~c{i}", w.kind, g, w.width, w.district))
    return out


def castle(f, c: CitySpec, nd, facing: float) -> Structure:
    """A castle on the node's hub: a square curtain wall with round corner towers (and mid-wall
    towers on a big one), a twin-towered gatehouse in the wall facing `facing` (deg: the main road
    comes from there), the keep at the back of the bailey, a great hall and a chapel."""
    s = castle_half(nd)
    cx, cy = castle_centre(nd, f)
    F = Frame(f, cx, cy, facing)
    t, H, g = CASTLE_WALL_T, CASTLE_WALL_H, CASTLE_GATE_W / 2
    F.wall("curtain", -s, -s, -s, s, t, H)
    for sv in (-1, 1):
        F.wall("curtain", -s, sv * s, s, sv * s, t, H)
        F.wall("curtain", s, sv * (g + 6), s, sv * s, t, H)     # the front wall either side of the gatehouse
    rc = float(np.clip(0.08 * s, 6.5, 9.0))
    for su in (-1, 1):
        for sv in (-1, 1):
            F.tower("corner-tower", su * s, sv * s, rc, H + 8, 2.2 * rc)
    if s > 70:                                    # a long wall gets a tower half way
        for u, v in ((-s, 0), (0, s), (0, -s)):
            F.tower("wall-tower", u, v, rc - 1.5, H + 5, 2 * (rc - 1.5))
    for sv in (-1, 1):                            # the gatehouse: two drum towers and the block over the passage
        F.tower("gate-tower", s + 2, sv * (g + 4), 7.0, H + 9, 13.0)
    z0 = F.ground(s, 0, 12, 2 * g)
    F.add("box", "gate-passage", s, 0, 12, 2 * g + 2, H + 6 - 8.5, z=z0 + 8.5)
    kw, kd = float(np.clip(0.42 * s, 24, 46)), float(np.clip(0.36 * s, 20, 40))
    ku = -s + t + kw / 2 + 0.18 * s
    kz = F.ground(ku, 0, kw, kd)
    top = F.add("box", "keep", ku, 0, kw, kd, 30.0, z=kz)
    for su in (-1, 1):
        for sv in (-1, 1):
            F.tower("keep-turret", ku + su * kw / 2, sv * kd / 2, 3.6, top + 5 - kz, 7.0)
    F.hall("great-hall", 0.42 * s, s - t / 2 - 9, min(0.6 * s, 55.0), 14.0, 11.0, 6.0)
    F.hall("chapel", 0.45 * s, -(s - t / 2 - 8), min(0.3 * s, 24.0), 11.0, 10.0, 5.5)
    reach = s + rc + 2
    return Structure("castle", cx, cy, facing, nd.id, rect(cx, cy, 2 * reach, 2 * reach, facing), F.parts)


def castle_facing(c: CitySpec, P, nd) -> float:
    """The bearing the castle's gate faces: up the castle-gate avenue (a walled castle), else the
    widest road ending at the hub (of those, the one nearest the outer wall's nearest gate), else
    that gate, else south."""
    from .city import _angdiff, ring_point
    ctr = np.array(nd.center, float)
    bearing = lambda p: math.degrees(math.atan2(p[1] - ctr[1], p[0] - ctr[0])) % 360  # noqa: E731
    road = next((w for w in P.ways if w.id == "castle-gate-av"), None)
    if road is not None:
        return round(bearing(road.line.coords[-1]), 3)
    gate = None
    if P.gates and c.enclosure:
        gate = bearing(min((ring_point(c, a) for a in P.gates), key=lambda q: math.dist(q, ctr)))
    ends = [(w.width, bearing(q)) for w in P.ways if w.kind in ("avenue", "arterial", "boulevard", "street")
            for q in (w.line.coords[0], w.line.coords[-1]) if math.dist(q, ctr) < nd.radius + 40]
    if ends:
        top = max(wd for wd, _ in ends)
        cands = [b for wd, b in ends if wd >= top - 0.5]
        b = min(cands, key=lambda b: abs(_angdiff(b, gate)) if gate is not None else 0.0)
    else:
        b = gate if gate is not None else 270.0
    if c.preset.streets == "grid":       # square to the grid: the side nearest that bearing
        g = c.preset.grid_deg
        b = g + 90 * round(_angdiff(b, g) / 90)
    return round(b % 360, 3)


# --- the plan -------------------------------------------------------------------------------------

def place(P, f) -> None:
    """Add the extended types to a finished plan (CityPlan.extended) and clear what stood under them."""
    c = P.layout.city
    if not on(c):
        return
    out: list[Structure] = []
    nd = castle_node(c)
    if nd is not None:
        out.append(castle(f, c, nd, castle_facing(c, P, nd)))
    for st in out:
        clear_under(P, st.foot)
    P.extended = out


def clear_under(P, foot: Polygon) -> None:
    """Take out the buildings, pads, trees and market stalls standing in a footprint."""
    from shapely.prepared import prep
    from .city import _rect
    g = prep(foot)
    P.buildings = [b for b in P.buildings if not g.intersects(_rect(b.x, b.y, b.w, b.d, b.rot))]
    P.typed = [b for b in P.typed if not g.contains(Point(b.x, b.y))]
    P.pads = [p for p in P.pads if not g.intersects(_rect(p[0], p[1], p[2], p[3], p[4]))]
    P.trees = [t for t in P.trees if not g.contains(Point(t[0], t[1]))]
    P.markets = [(sq, [b for b in st if not g.contains(Point(b.x, b.y))]) for sq, st in P.markets]


def stats(P) -> dict:
    """{type: count} of the extended structures."""
    out: dict[str, int] = {}
    for st in P.extended:
        out[st.type] = out.get(st.type, 0) + 1
    return out


# --- greybox slots and the viewer -----------------------------------------------------------------

def unit(prim: str) -> dict:
    from .city import UNIT
    return CONE if prim == "cone" else UNIT[prim]


def scale(p: Piece) -> list:
    """The unit mesh's scale for a piece (the unit dome is r 0.5 and only 0.5 tall)."""
    return [p.w, p.d, p.h * 2 if p.prim == "dome" else p.h]


def add_slots(P, new, hs) -> None:
    """One greybox slot per type and tile (slot type = the type id, its editor entry the typology)."""
    from .city import majority_district
    c = P.layout.city
    groups: dict[tuple[str, str], list[Structure]] = {}
    for st in P.extended:
        key = f"x{int(math.floor(st.x / c.tile_m))}y{int(math.floor(st.y / c.tile_m))}"
        groups.setdefault((st.type, key), []).append(st)
    for (typ, key), sts in sorted(groups.items()):
        cx, cy = float(np.mean([s.x for s in sts])), float(np.mean([s.y for s in sts]))
        district = majority_district(c, [s.district for s in sts if s.district], cx, cy)
        sb = new(f"{typ}-{key}", typ, (cx, cy, float(hs(cx, cy))), district=district, category=CATEGORY.get(typ, "building"),
                 typology=typ)
        for st in sts:
            for p in st.parts:
                sb.add(f"{typ}:{p.role}", unit(p.prim), (p.x, p.y, p.z), p.rot, scale(p))


CATEGORY = {"castle": "building"}
PLAN_COLORS = {"castle": (138, 127, 106)}


def view_rows(P, type_index, district_index) -> list[tuple[list, str]]:
    """The viewer's rows for the structures' pieces ([x y z w d h rot type district (shape)]) and their slot ids."""
    from .city_view import _r, slot_id
    c = P.layout.city
    out = []
    for st in P.extended:
        ti, di, sid = type_index(st.type), district_index(st.district), slot_id(st.type, st.x, st.y, c.tile_m)
        for p in st.parts:
            row = [_r(p.x), _r(p.y), _r(p.z), _r(p.w), _r(p.d), _r(p.h), _r(p.rot), ti, di]
            if p.prim != "box":
                row.append(SHAPES.index(p.prim))
            out.append((row, sid))
    return out

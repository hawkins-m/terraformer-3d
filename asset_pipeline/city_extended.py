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
    T(id="church", name="Church", family="civic", form="hall", storeys=(3, 5), width=(14, 34), depth=(36, 60),
      prompt="a stone parish church with a tall bell tower and a slender spire on the square",
      desc="A parish church: a long stone nave under a steep pitched roof, a semicircular apse at the east end, "
           "sometimes a transept, and a square bell tower at the west front topped by a slender spire or a low "
           "pyramid roof; a round-arched portal facing the square.",
      roofs=["steep pitched slate or clay tile", "tall pointed spire", "half-dome over the apse"],
      facades=["dressed stone with buttresses", "tall arched windows", "rose window over the portal"]),
    T(id="medical", name="Medical center", family="civic", form="podium_tower", storeys=(8, 16), width=(60, 100),
      depth=(50, 70), max_count=3,
      prompt="a modern hospital: a broad podium and a white bed tower with a rooftop helipad beside the main road",
      desc="A medical center: a four-storey podium of clinics with a glazed entrance canopy towards the main "
           "road, a slab bed tower of eight to twelve storeys with ribbon windows, a side wing, rooftop plant and "
           "a round helipad on the tower roof.",
      roofs=["flat roof with rooftop plant", "round helipad with markings"],
      facades=["white panels and ribbon windows", "glazed entrance hall", "sun-shading fins"]),
    T(id="airport", name="Airport", family="infrastructure", form="open", storeys=(1, 4), width=(2600, 3300),
      depth=(600, 800), max_count=1,
      prompt="an airport outside the city: a long runway, a terminal with jet bridges, an apron with parked "
             "airliners and a control tower",
      desc="An airport: a 2.6-3.2 km asphalt runway with a parallel taxiway, a concrete apron, a long terminal "
           "under a curved roof with two boarding piers, a slender control tower with a glazed cab, three arched "
           "hangars and a car park.",
      roofs=["long curved metal roof on the terminal", "barrel-vaulted hangar roofs"],
      facades=["curtain-wall glazing", "corrugated metal hangar walls", "concrete apron and asphalt runway"]),
    T(id="power", name="Power grid", family="infrastructure", form="open", storeys=(1, 12), width=(6, 74),
      depth=(6, 54),
      prompt="high-voltage pylons and power lines striding out of the industrial district, fenced substations",
      desc="The power grid: fenced substations of transformers and steel gantries with a small control house, "
           "and lines of steel lattice pylons about 40 m tall with two crossarms, carrying cables from the "
           "industrial district out past the city's edge.",
      roofs=["flat roof on the control house"],
      facades=["galvanised steel lattice", "grey transformer tanks", "chain-link fence and gravel yard"]),
]}
del T

SHAPES = ("box", "gable", "cyl", "cone", "dome", "vault")      # the viewer's row column 9 (box when left out)
CONE = {"kind": "cone", "r": 0.5, "h": 1.0, "seg": 16}


@dataclass
class Piece:
    """One unit mesh, scaled: centre of its base (x, y, z), turn about z, size (w along the turn, d across, h)."""
    prim: str          # box | gable | cyl | cone | dome | vault
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

    def slab(self, role: str, u: float, v: float, w: float, d: float, lift: float = 0.4) -> float:
        """A flat slab (runway, apron, yard) from below its lowest ground to `lift` above its highest."""
        lo, hi = self.ground(u, v, w, d), self.ground(u, v, w, d, top=True)
        return self.add("box", role, u, v, w, d, hi - lo + lift + self.sink, z=lo)

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
    G = Ground(P, f)
    for st in out:
        G.take(st.foot)
    out += churches(P, f, G)
    out += medical_centers(P, f, G)
    port, roads = airports(P, f, G)
    out += port
    for w in roads:
        clear_under(P, w.line.buffer(w.width / 2 + 2))
    P.ways += roads
    out += power_grid(P, f, G)
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


# --- where a structure may stand ------------------------------------------------------------------

class Ground:
    """What a new structure must keep off: roads and water (their own width), markets, parks, plazas,
    walls, the moat and the structures placed so far; and land, slope and the tile's edge."""

    def __init__(self, P, f):
        from shapely.strtree import STRtree
        self.P, self.f = P, f
        L = P.layout
        self.half = L.terrain.extent_m / 2 - 60
        self.way_polys = [w.line.buffer(w.width / 2 + 2, cap_style="flat") for w in P.ways]
        self.ways = STRtree(self.way_polys) if self.way_polys else None
        other = [sq for sq, _ in P.markets] + list(P.moat) + list(P.interchanges)
        other += [Point(pz.center).buffer(pz.radius) for pz in L.plazas]
        other += [rect(w.x, w.y, w.prim.get("w", 2 * w.prim.get("r", 1)), w.prim.get("d", 2 * w.prim.get("r", 1)), w.rot)
                  for w in P.walls]
        # the civic cores' kit pieces: radials, rings and plots (rotundas, stoas, ring rows)
        from shapely.geometry import LineString
        from .greybox import expand_rows
        for rd in L.radials:
            u = np.array([math.cos(math.radians(rd.angle)), math.sin(math.radians(rd.angle))])
            a, b = np.array(rd.center) + rd.r_from * u, np.array(rd.center) + rd.r_to * u
            if rd.r_to > rd.r_from:
                other.append(LineString([tuple(a), tuple(b)]).buffer(rd.width / 2 + 2, cap_style="flat"))
        for rg in L.rings:
            other.append(Point(rg.center).buffer(rg.radius + rg.width / 2 + 2).difference(
                Point(rg.center).buffer(max(rg.radius - rg.width / 2 - 2, 0.1))))
        for pl in expand_rows(L):
            if pl.type == "stoa" and pl.arc:
                other.append(Point(pl.center).buffer(pl.arc[0] + 8).difference(Point(pl.center).buffer(max(pl.arc[0] - 8, 0.1))))
            elif pl.size:
                other.append(rect(pl.center[0], pl.center[1], pl.size[0] + 4, pl.size[1] + 4, pl.rot))
        self.other = STRtree(other) if other else None
        self.parks = STRtree(P.parks) if P.parks else None
        self.taken: list[Polygon] = []

    def way_hits(self, g: Polygon, ignore=()) -> bool:
        if self.ways is None:
            return False
        return any(int(j) not in ignore for j in self.ways.query(g, predicate="intersects"))

    def free(self, g: Polygon, max_slope: float = 0.18, ignore=(), parks: bool = False) -> bool:
        """g stands on land inside the tile, on ground no steeper than max_slope across it, off every
        road, water, square and structure (and park, unless `parks`)."""
        x0, y0, x1, y1 = g.bounds
        if max(abs(x0), abs(x1), abs(y0), abs(y1)) > self.half:
            return False
        pts = np.array(list(g.exterior.coords)[:-1] + [g.centroid.coords[0]])
        if not bool(np.all(self.f.land(pts[:, 0], pts[:, 1]))):
            return False
        z = self.f.z(pts[:, 0], pts[:, 1])
        if (float(np.max(z)) - float(np.min(z))) / max(math.sqrt(g.area), 1.0) > max_slope:
            return False
        if self.way_hits(g, ignore) or any(g.intersects(t) for t in self.taken):
            return False
        if self.other is not None and len(self.other.query(g, predicate="intersects")):
            return False
        if not parks and self.parks is not None and len(self.parks.query(g, predicate="intersects")):
            return False
        return True

    def take(self, g: Polygon) -> None:
        self.taken.append(g.buffer(4))


def frontage(G: Ground, kinds: tuple, near, d_range: tuple, w: float, d: float, step: float = 40.0,
             max_slope: float = 0.15, parks: bool = False) -> list[tuple]:
    """Sites w (along the road) x d beside a road of one of `kinds`, between d_range metres of `near`:
    [(distance, x, y, rot facing the road, way index)], nearest first. A site's front is its +u side, so
    rot points from the site to the road."""
    out = []
    for i, wy in enumerate(G.P.ways):
        if wy.kind not in kinds:
            continue
        ln = wy.line
        for s in np.arange(step / 2, ln.length - step / 2, step):
            p = np.array(ln.interpolate(s).coords[0])
            dist = math.dist(p, near)
            if not d_range[0] <= dist <= d_range[1]:
                continue
            q = np.array(ln.interpolate(min(s + 5, ln.length)).coords[0]) - np.array(ln.interpolate(max(s - 5, 0)).coords[0])
            a = math.degrees(math.atan2(q[1], q[0]))
            for side in (1, -1):
                n = a + 90 * side                       # from the road to the site
                c = p + (wy.width / 2 + 5 + d / 2) * np.array([math.cos(math.radians(n)), math.sin(math.radians(n))])
                g = rect(float(c[0]), float(c[1]), d, w, n + 180)
                if G.free(g, max_slope=max_slope, ignore=(i,), parks=parks):
                    out.append((dist, float(c[0]), float(c[1]), (n + 180) % 360, i))
    out.sort(key=lambda t: t[0])
    return out


def built_tree(P):
    """A KD-tree over the city's buildings and blocks (for distance to the built-up edge), or None."""
    from scipy.spatial import cKDTree
    pts = [(b.x, b.y) for b in P.buildings] + [(b.x, b.y) for b in P.typed]
    pts += [b.centroid.coords[0] for b in P.blocks]
    return cKDTree(np.array(pts)) if pts else None


# --- church ---------------------------------------------------------------------------------------

def church(f, x: float, y: float, rot: float, r: np.random.Generator, district=None, small: bool = False) -> Structure:
    """A church whose front faces `rot`: a nave under a pitched roof, an apse at the back, a west tower at
    the front with a spire (or a low pyramid roof on a plain tower), sometimes a transept."""
    F = Frame(f, x, y, rot)
    nw = float(r.uniform(10, 13) if small else r.uniform(12, 16))
    nl = float(r.uniform(22, 28) if small else r.uniform(30, 44))
    wh = float(r.uniform(9, 11) if small else r.uniform(13, 17))
    F.hall("nave", 0, 0, nl, nw, wh, 0.55 * nw)
    F.add("cyl", "apse", -nl / 2, 0, nw, nw, wh - 2)
    F.add("dome", "apse-roof", -nl / 2, 0, nw, nw, 0.4 * nw, z=F.ground(-nl / 2, 0, nw, nw) + wh - 2)
    wide = nw
    if not small and r.random() < 0.4:
        F.hall("transept", -nl / 4, 0, nw, 2.1 * nw, wh, 0.55 * nw)
        wide = 2.1 * nw
    tw = float(np.clip(0.6 * nw, 6, 9))
    th = float(r.uniform(1.9, 2.5) * wh)
    tu = nl / 2 + tw / 2 - 1
    top = F.add("box", "tower", tu, 0, tw, tw, th)
    spire = r.random() < 0.65
    F.add("cone", "spire" if spire else "tower-roof", tu, 0, tw * 1.05, tw * 1.05, float(r.uniform(1.4, 2.2) * th / 2.4)
          if spire else 0.5 * tw, z=top)
    u0, u1 = -nl / 2 - nw / 2, tu + tw / 2
    return Structure("church", x, y, rot, district, rect(*F.at((u0 + u1) / 2, 0), u1 - u0 + 6, wide + 6, rot), F.parts)


def churches(P, f, G: Ground) -> list[Structure]:
    """A church on each market square, town square and district square: just outside the square, its front
    to it (the castle's forecourt excepted)."""
    c = P.layout.city
    from .city import nearest_node
    out = []
    castle = castle_node(c)
    squares = [sq for sq, _ in P.markets]
    squares += [Point(pz.center).buffer(pz.radius) if pz.shape == "round" else rect(*pz.center, 2 * pz.radius, 2 * pz.radius, pz.rot)
                for pz in P.layout.plazas if pz.type == "paved" and (castle is None or pz.district != castle.id)]
    for i, sq in enumerate(squares):
        r = rng(c, "church", i)
        cx, cy = sq.centroid.coords[0]
        R = math.sqrt(sq.area / math.pi)
        a0 = float(r.uniform(0, 360))
        for k in range(16):
            b = a0 + 22.5 * k
            u = np.array([math.cos(math.radians(b)), math.sin(math.radians(b))])
            st = None
            for off in (8, 20, 34):
                p = np.array([cx, cy]) + (R + off + 26) * u
                cand = church(f, float(p[0]), float(p[1]), (b + 180) % 360, rng(c, "church", i))
                if G.free(cand.foot.buffer(-2)):
                    st = cand
                    break
            if st is not None:
                st.district = nearest_node(c, st.x, st.y)
                G.take(st.foot)
                out.append(st)
                break
    return out


# --- medical center -------------------------------------------------------------------------------

def medical(f, x: float, y: float, rot: float, r: np.random.Generator, storey_m: float, district=None) -> Structure:
    """A hospital: a four-storey podium, a bed tower on it with a helipad, an entrance canopy to the road."""
    F = Frame(f, x, y, rot)
    pw, pd = 96.0, 64.0                       # pw along the road (v), pd deep (u)
    ph = 4 * storey_m
    z0 = F.ground(0, 0, pd, pw)
    top = F.add("box", "podium", 0, 0, pd, pw, ph + (F.ground(0, 0, pd, pw, top=True) - z0), z=z0)
    n = int(r.integers(8, 13))
    tt = F.add("box", "bed-tower", -6, 0, 22, 62, n * storey_m, z=top)
    F.add("cyl", "helipad", -6, 18, 18, 18, 0.8, z=tt)
    F.add("box", "rooftop-plant", -6, -18, 12, 14, 4, z=tt)
    F.add("box", "wing", -pd / 2 + 10, -pw / 2 + 16, 20, 32, (n // 2) * storey_m, z=top)
    F.add("box", "entrance-canopy", pd / 2 + 4, 0, 8, 26, 5.0)
    return Structure("medical", x, y, rot, district, rect(x, y, pd + 18, pw + 6, rot), F.parts)


def medical_centers(P, f, G: Ground) -> list[Structure]:
    """One per city on an avenue or arterial just past downtown (more in a large / metro city)."""
    from .city import main_centre, nearest_node
    from . import city_size
    c = P.layout.city
    C = main_centre(c)
    k = city_size.factor(c)
    d0 = max(c.preset.downtown_m * k, 300.0) * 1.05
    sites = frontage(G, ("avenue", "arterial", "boulevard"), C, (d0, d0 + 900 * k), 96.0, 70.0)
    want = {"standard": 1, "large": 2, "metro": 3}.get(c.size, 1)
    out = []
    for dist, x, y, rot, wi in sites:
        if len(out) >= want:
            break
        if any(math.dist((x, y), (s.x, s.y)) < 1500 for s in out):
            continue
        st = medical(f, x, y, rot, rng(c, "medical", len(out)), c.storey_m)
        if not G.free(st.foot, ignore=(wi,)):
            continue
        st.district = nearest_node(c, x, y)
        G.take(st.foot)
        out.append(st)
    return out


# --- airport --------------------------------------------------------------------------------------

AIRPORT_RUNWAY_M = {"large": 2600.0, "metro": 3200.0}
AIRPORT_CLEAR_M = 500.0      # this far from the nearest building or block


def airport(f, x: float, y: float, rot: float, length: float, district=None) -> Structure:
    """An airport along `rot`: a runway and a parallel taxiway in 40 m segments that follow the ground, an
    apron, a terminal with two piers, a control tower, hangars and a car park."""
    F = Frame(f, x, y, rot, sink=0.2)
    seg = 40.0
    for name, v, wd in (("runway", 0.0, 45.0), ("taxiway", 180.0, 23.0)):
        n = int(length // seg)
        for k in range(n):
            u = -length / 2 + seg * (k + 0.5)
            F.slab(name, u, v, seg + 0.5, wd, 0.5)
    F.slab("apron", 0, 330, 560, 170, 0.4)
    tz = F.ground(0, 470, 340, 50)
    top = F.add("box", "terminal", 0, 470, 340, 50, 16, z=tz)
    F.add("vault", "terminal-roof", 0, 470, 52, 340, 7, z=top, turn=90)     # the barrel along the terminal
    for u in (-110, 110):
        F.add("box", "pier", u, 390, 18, 120, 9)
    ct = F.add("cyl", "control-tower", 230, 455, 9, 9, 42)
    F.add("box", "control-cab", 230, 455, 14, 14, 7, z=ct)
    for k in range(3):
        u = -280 - 70 * k
        top = F.add("box", "hangar", u, 340, 60, 52, 14)
        F.add("vault", "hangar-roof", u, 340, 60, 52, 9, z=top)
    F.slab("car-park", 0, 570, 260, 110, 0.3)
    return Structure("airport", x, y, rot, district, rect(*F.at(0, 260), length + 120, 760, rot), F.parts)


def airports(P, f, G: Ground) -> tuple[list[Structure], list]:
    """Large and metro cities: one airport outside the city's edge on flat land, as near the centre as
    that allows, with an access road to the nearest highway or arterial. Returns (structures, new ways)."""
    from .city import Way, main_centre, nearest_node
    c = P.layout.city
    if c.size not in AIRPORT_RUNWAY_M:
        return [], []
    tree = built_tree(P)
    if tree is None:
        return [], []
    length = AIRPORT_RUNWAY_M[c.size]
    C = np.array(main_centre(c))
    half = G.half - length / 2
    gx, gy = np.meshgrid(np.arange(-half, half + 1, 300.0), np.arange(-half, half + 1, 300.0))
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    pts = pts[(tree.query(pts)[0] > AIRPORT_CLEAR_M + 300) & np.asarray(f.land(pts[:, 0], pts[:, 1]), bool)]
    best = None
    along = np.linspace(-length / 2, length / 2, 27)
    for x, y in pts:
        for a in range(0, 180, 15):
            u = np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
            v = np.array([-u[1], u[0]])
            g = rect(*(np.array([x, y]) + 260 * v), length + 120, 760, a)
            cs = np.array(g.exterior.coords)[:4]
            if np.max(np.abs(cs)) > G.half:
                continue
            edge = np.vstack([np.linspace(cs[0], cs[1], 9), np.linspace(cs[2], cs[3], 9)])
            if tree.query(edge)[0].min() < AIRPORT_CLEAR_M:
                continue
            rw = np.array([x, y]) + np.outer(along, u)
            if not bool(np.all(f.land(rw[:, 0], rw[:, 1]))):
                continue
            zr = float(np.ptp(f.z(rw[:, 0], rw[:, 1])))
            if zr > 25:
                continue
            score = math.dist((x, y), C) + 40 * zr
            if (best is None or score < best[0]) and G.free(g, max_slope=0.03, parks=True):
                best = (score, float(x), float(y), float(a))
    if best is None:
        return [], []
    st = airport(f, best[1], best[2], best[3], length)
    st.district = nearest_node(c, st.x, st.y)
    G.take(st.foot)
    tx, ty = Frame(f, st.x, st.y, st.rot).at(0, 640)
    roads = [w for w in P.ways if w.kind in ("highway", "arterial", "avenue")]
    ways = []
    if roads:
        from shapely.ops import nearest_points
        w = min(roads, key=lambda w: w.line.distance(Point(tx, ty)))
        q = nearest_points(w.line, Point(tx, ty))[0]
        from shapely.geometry import LineString
        ln = LineString([(tx, ty), (q.x, q.y)])
        pts = np.array([ln.interpolate(s).coords[0] for s in np.arange(0, ln.length, 25)] or [(tx, ty)])
        if 40 < ln.length < 4000 and bool(np.all(f.land(pts[:, 0], pts[:, 1]))):
            ways.append(Way("airport-road", "arterial", ln, c.arterial_w, st.district))
    return [st], ways


# --- power grid -----------------------------------------------------------------------------------

PYLON_EVERY_M = 300.0
PYLON_H = 38.0


def substation(f, x: float, y: float, rot: float, district=None) -> Structure:
    """A substation yard: a gravel slab, transformers, a gantry and a control house."""
    F = Frame(f, x, y, rot, sink=0.2)
    F.slab("yard", 0, 0, 70, 50, 0.3)
    for k in range(4):
        F.add("box", "transformer", -21 + 14 * k, -8, 7, 5, 5.5)
    for u in (-28, 28):
        F.add("box", "gantry-post", u, 12, 1.2, 1.2, 16)
    F.add("box", "gantry-beam", 0, 12, 58, 1.2, 1.2, z=F.ground(0, 12, 58, 1.2) + 15)
    F.hall("control-house", 22, -18, 16, 8, 4.5, 2.0)
    return Structure("power", x, y, rot, district, rect(x, y, 74, 54, rot), F.parts)


def pylon_line(f, pts: list, district=None) -> Structure:
    """Pylons at `pts` (a mast under two crossarms across the line) and two wires between each pair, each
    span in four pieces whose heights step from one crossarm to the next."""
    pts = [np.asarray(p, float) for p in pts]
    a = math.degrees(math.atan2(pts[-1][1] - pts[0][1], pts[-1][0] - pts[0][0]))
    parts: list[Piece] = []
    tops = []
    for p in pts:
        F = Frame(f, float(p[0]), float(p[1]), a)
        F.add("box", "pylon-base", 0, 0, 7, 7, 10)
        top = F.add("box", "pylon-mast", 0, 0, 2.6, 2.6, PYLON_H)
        F.add("box", "pylon-arm", 0, 0, 1.6, 22, 1.4, z=top - 8)
        F.add("box", "pylon-arm", 0, 0, 1.6, 15, 1.4, z=top - 2)
        tops.append(top - 8)
        parts += F.parts
    for i in range(len(pts) - 1):
        p0, p1 = pts[i], pts[i + 1]
        b = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
        n = np.array([-math.sin(math.radians(b)), math.cos(math.radians(b))])
        span = math.dist(p0, p1)
        for side in (-10.0, 10.0):
            for k in range(4):
                t = (k + 0.5) / 4
                q = p0 + (p1 - p0) * t + side * n
                z = tops[i] + (tops[i + 1] - tops[i]) * t - 1.5 * math.sin(math.pi * t)
                parts.append(Piece("box", "wire", round(float(q[0]), 3), round(float(q[1]), 3), round(z, 3),
                                   round(b % 360, 3), round(span / 4 + 0.5, 3), 0.35, 0.35))
    from shapely.geometry import LineString
    foot = LineString([tuple(p) for p in pts]).buffer(6)
    return Structure("power", float(pts[0][0]), float(pts[0][1]), a, district, foot, parts)


def power_grid(P, f, G: Ground) -> list[Structure]:
    """A substation in the industrial zone and pylon lines from it out past the city's edge (two, at +-30
    deg off the way out from the centre), each ending at a substation."""
    from .city import industrial_centre, main_centre, nearest_node
    c = P.layout.city
    if c.preset.industrial == "none":
        return []
    ind = industrial_centre(c, f, P.ways)
    tree = built_tree(P)
    if ind is None or tree is None:
        return []
    ind = np.array(ind, float)
    C = np.array(main_centre(c), float)
    out = []
    sub = None                                            # the zone's substation: the nearest free yard on a road
    for dist, x, y, rot, wi in frontage(G, ("street", "avenue", "arterial", "highway"), ind,
                                        (0.0, 2.5 * c.preset.industrial_m), 74.0, 54.0, step=30.0):
        cand = substation(f, x, y, rot)
        if G.free(cand.foot, ignore=(wi,)):
            sub = cand
            break
    if sub is None:
        return []
    sub.district = nearest_node(c, sub.x, sub.y)
    G.take(sub.foot)
    out.append(sub)
    away = math.degrees(math.atan2(ind[1] - C[1], ind[0] - C[0])) if math.dist(ind, C) > 1 else 0.0
    for j, turn in enumerate((-30.0, 30.0)):
        u = np.array([math.cos(math.radians(away + turn)), math.sin(math.radians(away + turn))])
        start = np.array([sub.x, sub.y])
        pts, s, outside = [], 120.0, 0
        edge = False
        while s < 9000:
            p = start + s * u
            if np.max(np.abs(p)) > G.half or not bool(f.land(*p)):
                edge = np.max(np.abs(p)) > G.half           # out of the tile: past the city too
                break
            q = p
            for nudge in (0.0, 30.0, -30.0, 60.0, -60.0):          # off a road or water
                q = p + nudge * u
                if not G.way_hits(Point(*q).buffer(5)):
                    break
            pts.append(q)
            outside = outside + 1 if tree.query(q)[0] > 300 else 0
            if outside >= 2:
                break
            s += PYLON_EVERY_M
        if len(pts) < 3 or (outside < 2 and not edge):
            continue
        line = pylon_line(f, pts, nearest_node(c, *pts[len(pts) // 2]))
        out.append(line)
        end = pts[-1] + 60 * u
        es = substation(f, float(end[0]), float(end[1]), away + turn)
        if G.free(es.foot):
            es.district = nearest_node(c, *end)
            G.take(es.foot)
            out.append(es)
    return out



class FarGround:
    """The background region's ground for a Frame (city_background.Background.z; land above the sea)."""

    def __init__(self, B, sea: float):
        self.B, self.sea = B, sea

    def z(self, x, y):
        return self.B.z(x, y)

    def land(self, x, y):
        return np.asarray(self.B.z(x, y)) > self.sea + 0.5


# --- greybox slots and the viewer -----------------------------------------------------------------

def unit(prim: str) -> dict:
    from .city import UNIT
    return CONE if prim == "cone" else UNIT[prim]


def scale(p: Piece) -> list:
    """The unit mesh's scale for a piece (the unit dome is r 0.5 and only 0.5 tall)."""
    return [p.w, p.d, p.h * 2 if p.prim == "dome" else p.h]


def add_slots(P, new, hs) -> None:
    """The plan's structures as greybox slots (emit)."""
    from .city import majority_district
    c = P.layout.city
    emit(P.extended, new, hs, c.tile_m, district=lambda ds, x, y: majority_district(c, ds, x, y))


def emit(structures: list, new, hs, tile_m: float, prefix: str = "", district=None) -> None:
    """One greybox slot per type and tile (slot type = the type id, its editor entry the typology), ids
    `prefix`ed; district(districts, x, y): the slot's district (None: none)."""
    groups: dict[tuple[str, str], list[Structure]] = {}
    for st in structures:
        key = f"x{int(math.floor(st.x / tile_m))}y{int(math.floor(st.y / tile_m))}"
        groups.setdefault((st.type, key), []).append(st)
    for (typ, key), sts in sorted(groups.items()):
        cx, cy = float(np.mean([s.x for s in sts])), float(np.mean([s.y for s in sts]))
        d = district([s.district for s in sts if s.district], cx, cy) if district else None
        sb = new(f"{prefix}{typ}-{key}", typ, (cx, cy, float(hs(cx, cy))), district=d, category=CATEGORY.get(typ, "building"),
                 typology=typ)
        for st in sts:
            for p in st.parts:
                sb.add(f"{typ}:{p.role}", unit(p.prim), (p.x, p.y, p.z), p.rot, scale(p))


CATEGORY = {"castle": "building", "church": "building", "medical": "building", "airport": "structure",
            "power": "structure"}
PLAN_COLORS = {"castle": (138, 127, 106), "church": (226, 214, 180), "medical": (236, 236, 240),
               "airport": (150, 150, 156), "power": (90, 96, 110)}


def view_rows(P, type_index, district_index, structures=None) -> list[tuple[list, str]]:
    """The viewer's rows for the structures' pieces ([x y z w d h rot type district (shape)]) and their slot ids
    (by default the plan's structures)."""
    from .city_view import _r, slot_id
    c = P.layout.city
    out = []
    for st in P.extended if structures is None else structures:
        ti, di, sid = type_index(st.type), district_index(st.district), slot_id(st.type, st.x, st.y, c.tile_m)
        for p in st.parts:
            row = [_r(p.x), _r(p.y), _r(p.z), _r(p.w), _r(p.d), _r(p.h), _r(p.rot), ti, di]
            if p.prim != "box":
                row.append(SHAPES.index(p.prim))
            out.append((row, sid))
    return out

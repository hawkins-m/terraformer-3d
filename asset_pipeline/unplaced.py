"""Use unplaced assets (`ap ue build --use-unplaced`; Unreal tab › Build city in Unreal): built library
assets the city plan gives no place (next_build.outside_reason "… with no share or slots") take over a
share of places that fit them, at build time only. The city plan, greybox, shots and frames stay as
they are; turning the option off puts the plan's own builds back in every place it took.

Who: entries of a catalog typology (its family says where they go), not excluded, passing the
build's gate (next_build.gate: Approved, or Active with Use Active versions), with a model: the
Active version when it isn't procedural (next_build.building_model), else the procedural build on
disk. Kit modules, attached pieces and variants already appear and are never candidates.

Where (FAMILY_GROUP): housing and mixed take housing and mixed places, civic takes civic places,
infrastructure takes places on the waterfront or the city's edge, public realm takes open ground
(parks, plazas). One-off landmarks (ONE_OFF_WORDS, or a typology with max_count <= 3) take 1 or 2
places, open ground first, then matching civic places; repeated types up to REPEAT_SHARE of the
places that fit them (at least one). Picks go round-robin across districts.

A place is a planned build's placement (a typed building, or a slot's single build): never a kit
module, a prop, an attached piece, a variant's placement, a parent with attached pieces, one in
a slot a facade kit replaces whole (kit_slots: the kit's build would stand there instead), or one in
a slot a locked shot's frame shows (avoid_locked; greybox-locked wides have no frame to keep). Its
build passes the gate too, so swapping never holds a slot back. Its footprint fits when the asset,
scaled uniformly (SCALE) and turned a quarter if that fits better, is within FIT_TOL of it on both
sides; the build whose placements are most numerous gives first, and no build gives more than
DONOR_SHARE of its placements. On open ground the asset stands at its own size (down to 1 - FIT_TOL
to fit) on a free spot of the lawn or paving that overlaps nothing: no other slot's building or
structure, no piece of its own slot but trees (not even the pivot of the lawn under it, which
Unreal's placement check counts) and no other open-ground pick; the trees under it leave the
greybox. A park with no such spot is skipped and the report names it (rep["no_spot"]).

Deterministic: ties break on a hash of (seed, asset, place), so a rebuild picks the same places.
Swapped placements keep their key and slot, so they stay attached to their slot actor (hand moves
kept) and a rebuild after toggling changes only those places.
"""
import hashlib
import math

import numpy as np

from .project import ProjectStore, read_json

FIT_TOL = 0.25              # footprint within this (each side), after scaling
SCALE = (0.5, 2.0)          # uniform scale an asset may take to fit a place
REPEAT_SHARE = 0.10         # a repeated type takes up to this share of the places that fit it
DONOR_SHARE = 0.25          # a build gives at most this share of its placements
ONE_OFF_MAX = 2             # one-off landmarks: 1 to this many
ONE_OFF_WORDS = ("temple", "rotunda", "campanile", "lighthouse", "theatre", "theater", "amphitheatre", "odeon",
                 "basilica", "thermae", "pantheon", "obelisk", "monument", "nymphaeum", "tower")
FAMILY_GROUP = {"housing": "housing", "mixed": "housing", "civic": "civic",
                "infrastructure": "infrastructure", "public_realm": "public_realm"}
OPEN_TYPES = {"park", "plaza", "garden"}
WATER_TYPES = {"sea", "canal", "lagoon", "river", "quay", "moat"}
WATERFRONT_M = 80.0         # a place this close to water is on the waterfront
EDGE_PCT = 75               # a place this far out (percentile of building slots) is on the edge
TREE_NEAR = 25.0            # m: an open-ground spot's corners each have a park tree this close
GRID_M = 8.0                # open-ground spot search step
PIN_M = 1.0                 # m: an open-ground model stays this clear of its slot's piece pivots
SEED = 0


def _h(seed: int, *parts) -> str:
    return hashlib.sha1(":".join(map(str, (seed, *parts))).encode()).hexdigest()


# --- pure parts ------------------------------------------------------------------------------

def fit(aw: float, ad: float, w: float, d: float, tol: float = FIT_TOL, scale=SCALE) -> dict | None:
    """Does an asset of footprint aw x ad fit a place of w x d? Uniform scale k (to the place's
    area, within `scale`), turned a quarter when that fits better: {k, turn, err}, err the worst
    side's mismatch; None past tol."""
    if min(aw, ad, w, d) <= 0:
        return None
    k = float(np.clip(math.sqrt(w * d / (aw * ad)), *scale))
    best = None
    for turn, (x, y) in ((False, (aw, ad)), (True, (ad, aw))):
        err = max(abs(k * x - w) / w, abs(k * y - d) / d)
        if best is None or err < best["err"] - 1e-9:
            best = {"k": k, "turn": turn, "err": round(err, 4)}
    return best if best["err"] <= tol else None


def one_off(entry: str, typology: str | None, max_count: int | None) -> bool:
    words = f"{entry} {typology or ''}".replace("-", " ").replace("_", " ").split()
    return (max_count is not None and max_count <= 3) or any(w in ONE_OFF_WORDS for w in words)


def matches(group: str, place: dict) -> bool:
    """May an asset of this family group take this place (a placement)?"""
    if group == "infrastructure":
        return place["group"] in ("housing", "infrastructure") and (place.get("waterfront") or place.get("edge"))
    return group in ("housing", "civic") and place["group"] == group


def open_matches(asset: dict, site: dict) -> bool:
    """May an asset stand on this open ground (park, plaza)? Infrastructure only on the waterfront
    or the edge; public realm and other one-off landmarks anywhere."""
    if asset["group"] == "infrastructure":
        return bool(site.get("waterfront") or site.get("edge"))
    return asset["group"] == "public_realm" or asset["one_off"]


def _spread(cands: list[dict], n: int) -> list[dict]:
    """n of the ranked candidates, round-robin across districts (each district's best first)."""
    by: dict = {}
    for c in cands:
        by.setdefault(c.get("district") or "", []).append(c)
    queues = sorted(by.values(), key=lambda q: cands.index(q[0]))
    out = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def select(assets: list[dict], places: list[dict], opens: list[dict], open_fit=None, seed: int = SEED) -> dict:
    """The picks: {entry: [{kind: "swap" | "open", key | slot, from, k, turn, district, ...}]} and
    skipped {entry: why}. assets: [{entry, group, one_off, max_count, w, d, typology, stretch}]; places:
    placements [{key, slot, entry, group, typology, district, w, d, waterfront, edge}]. A model of no
    set size (stretch: a TRELLIS model) takes its own typology's places at their size, as its own
    placements would (city_library.use_active_models); opens: open-ground slots
    [{slot, district, waterfront, edge}]; open_fit(asset, slot id) -> {x, y, yaw, k, ...} or None
    (a free spot), tried in rank order only as far as needed. Assets take turns, one place each per
    round (one-offs first), so the first asset can't use up a shared build's places."""
    count: dict[str, int] = {}
    for p in places:
        count[p["entry"]] = count.get(p["entry"], 0) + 1
    left = {e: int(DONOR_SHARE * n) for e, n in count.items()}
    queues = []
    for a in sorted(assets, key=lambda a: (not a["one_off"], a["entry"])):
        e = a["entry"]
        swaps = []
        for p in places:
            if not matches(a["group"], p) or (a["one_off"] and a["group"] != "civic"):
                continue                                  # (one-off non-civic landmarks: open ground only)
            own = a.get("stretch") and a.get("typology") and a["typology"] == p.get("typology")
            f = {"k": 1.0, "turn": False, "err": 0.0, "own": True} if own else fit(a["w"], a["d"], p["w"], p["d"])
            if f:
                swaps.append(p | f | {"kind": "swap", "from": p["entry"], "_rank": (-count[p["entry"]], _h(seed, e, p["key"]))})
        swaps.sort(key=lambda c: c["_rank"])
        by_donor: dict = {}                               # the biggest donor first, spread across districts within it
        for c in swaps:
            by_donor.setdefault(c["from"], []).append(c)
        swaps = [c for q in by_donor.values() for c in _spread(q, len(q))]
        sites = sorted((o | {"kind": "open", "from": None} for o in opens if open_matches(a, o)),
                       key=lambda o: _h(seed, e, o["slot"]))
        sites = _spread(sites, len(sites))
        want = (min(ONE_OFF_MAX, a.get("max_count") or ONE_OFF_MAX) if a["one_off"]
                else max(1, round(REPEAT_SHARE * (len(swaps) + len(sites)))))
        queue = sites + swaps if a["one_off"] or not swaps else swaps + sites
        queues.append({"a": a, "queue": queue, "want": want, "got": [], "fits": bool(queue)})
    taken, picks, skipped = set(), {}, {}
    active = [q for q in queues if q["queue"]]
    while active:
        for q in list(active):
            while q["queue"]:
                c = q["queue"].pop(0)
                if c["kind"] == "swap":
                    if c["key"] in taken or left.get(c["entry"], 0) <= 0:
                        continue
                    left[c["entry"]] -= 1
                    taken.add(c["key"])
                    q["got"].append({k: v for k, v in c.items() if k != "_rank"})
                    break
                if ("open", c["slot"]) in taken or open_fit is None:
                    continue
                spot = open_fit(q["a"], c["slot"])
                if spot:
                    taken.add(("open", c["slot"]))
                    q["got"].append(c | spot)
                    break
            if len(q["got"]) >= q["want"] or not q["queue"]:
                active.remove(q)
    for q in queues:
        e, a = q["a"]["entry"], q["a"]
        if q["got"]:
            picks[e] = q["got"]
        elif q["fits"]:
            skipped[e] = "the places that fit it are taken or have no free spot"
        else:
            skipped[e] = {"public_realm": "no free park or plaza",
                          "infrastructure": "no waterfront or edge place fits it"}.get(a["group"], "no place of its family fits it")
    return {"picks": picks, "skipped": skipped}


def rect(x: float, y: float, w: float, d: float, yaw: float):
    from shapely.geometry import Polygon
    a = math.radians(yaw)
    c, s = math.cos(a), math.sin(a)
    return Polygon([(x + c * u - s * v, y + s * u + c * v) for u, v in
                    ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))])


def free_spot(area, trees: list, obstacles, w: float, d: float, yaw: float, centre, step: float = GRID_M) -> dict | None:
    """A spot for a w x d footprint (turned yaw) inside `area` (a polygon: the lawn or paving),
    clear of `obstacles` (a shapely geometry), with a tree within TREE_NEAR of each corner and the centre when
    trees are given (inside the planted park, not a bare corner of its box); the free spot nearest
    `centre`, at full size, else down to 1 - FIT_TOL. {x, y, yaw, k} or None."""
    from shapely.geometry import Point
    from shapely.prepared import prep
    inside, block = prep(area), prep(obstacles) if obstacles is not None and not obstacles.is_empty else None
    T = np.asarray(trees, float).reshape(-1, 2)
    x0, y0, x1, y1 = area.bounds
    pts = [(x, y) for x in np.arange(x0 + step / 2, x1, step) for y in np.arange(y0 + step / 2, y1, step)]
    pts.sort(key=lambda p: (round(math.hypot(p[0] - centre[0], p[1] - centre[1]), 3), p))
    for k in (1.0, 0.875, 1 - FIT_TOL):
        for x, y in pts:
            if not inside.contains(Point(x, y)):
                continue
            r = rect(x, y, w * k, d * k, yaw)
            if not inside.contains(r) or (block is not None and block.intersects(r)):
                continue
            if len(T) and any(np.min(np.hypot(*(T - c).T)) > TREE_NEAR for c in [*r.exterior.coords[:4], (x, y)]):
                continue
            return {"x": float(x), "y": float(y), "yaw": float(yaw), "k": k}
    return None


def placed_matrix(B: np.ndarray, centre, mlo, mhi, dims, k: float, turn: bool) -> np.ndarray:
    """The world matrix that stands a model (bounds mlo, mhi) on a place: B the place's world
    matrix, centre (x, y, base z) of the footprint in B's frame, dims (w, d, h) the asset's size
    at k = 1 (metres), turned a quarter about Z when turn."""
    ext = np.maximum(np.subtract(mhi, mlo), 1e-6)
    S = np.diag([k * dims[0] / ext[0], k * dims[1] / ext[1], k * dims[2] / ext[2], 1.0])
    R = np.eye(4)
    if turn:
        R[:2, :2] = [[0.0, -1.0], [1.0, 0.0]]
    T = np.eye(4)
    T[:3, 3] = centre
    C = np.eye(4)
    C[:3, 3] = [-(mlo[0] + mhi[0]) / 2, -(mlo[1] + mhi[1]) / 2, -mlo[2]]
    return np.asarray(B, float) @ T @ R @ S @ C


def lines(rep: dict) -> list[str]:
    """The "Will also place" lines: "temple: 2 (park-a, plaza-b)", "insula-copper: 3 (from insula-limestone x3)"."""
    out = []
    for e, ps in sorted((rep or {}).get("picks", {}).items()):
        took = []
        froms: dict = {}
        for p in ps:
            if p["kind"] == "open":
                took.append(f"{p['slot']} (open ground)")
            else:
                froms[p["from"]] = froms.get(p["from"], 0) + 1
                took.append(p["slot"])
        what = ", ".join([f"{n} from {f}" for f, n in sorted(froms.items())] + [f"{sum(p['kind'] == 'open' for p in ps)} on open ground"]
                         * any(p["kind"] == "open" for p in ps))
        out.append(f"{e}: {len(ps)} ({what}; slots {', '.join(sorted(set(took)))})")
    return out


# --- from the project ------------------------------------------------------------------------

def locked_slots(store: ProjectStore) -> set[str]:
    """Slots a locked shot's frame shows (its id pass); greybox-locked shots have no frame to keep."""
    from .stages import s0_frames, sw_shots
    out = set()
    for shot, key in s0_frames.locked(store).items():
        if key == s0_frames.LOCK_GREYBOX:
            continue
        ids = read_json(sw_shots.shot_dir(store, shot) / "ids.json", default=None) or {}
        out |= set((ids.get("slots") or {}).keys())
    return out


def _families(store: ProjectStore) -> dict:
    from .stages import sw_site
    try:
        layout = sw_site.load_layout(store)
    except FileNotFoundError:
        return {}
    return {t.id: t for t in (layout.city.typologies if layout.city else [])}


def _model(store: ProjectStore, entry: str, ent: dict, bounds: dict) -> dict | None:
    """The model an unplaced asset stands with: its non-procedural Active version, else its
    procedural build on disk: {glb, mesh, active (n or None), kind, n, dims (w, d, h at k = 1), lo, hi}."""
    from . import next_build
    from .assembly import glb_bounds
    m = next_build.building_model(store, entry)
    pb = None if m else next_build.procedural_build(store, entry)
    if m is None and pb is None:
        return None
    glb = m["glb"] if m else pb["glb"]
    if glb not in bounds:
        bounds[glb] = [v.tolist() for v in glb_bounds(store.root / glb)]
    lo, hi = bounds[glb]
    brief = read_json(store.root / "library" / "kits" / f"{entry}.json", default=None) or {}
    size = brief.get("dims") or ent.get("size_m")
    ext = np.subtract(hi, lo)
    dims = list(map(float, size[:3])) if m and size else [float(v) for v in ext]   # TRELLIS models: the entry's size
    cl = (m or {}).get("cleaned")
    return {"glb": glb, "lo": lo, "hi": hi, "dims": dims, "n": (m or pb)["n"], "kind": m["kind"] if m else "procedural",
            "active": m["n"] if m else None,
            "mesh": f"unplaced_{entry}" + (f"_v{m['n']}" if m else "") + (f"_{cl['usage']}" if cl else "")}


def candidates(store: ProjectStore, ok, bounds: dict | None = None, standing: set[str] = frozenset()) -> tuple[list[dict], dict]:
    """The unplaced assets: [{entry, group, one_off, max_count, w, d, model}] and {entry: why not}.
    standing: entries the plan's placements already use (a TRELLIS model at its planned buildings)."""
    from . import library_entries as le, next_build
    from .stages import sw_library
    dec = sw_library.decisions(store)
    rel = next_build._relations(store, dec)
    typ = _families(store)
    from .stages import sw_site
    gb = sw_site.load_greybox(store)
    used = next_build._prop_entries(store, gb) | next_build._building_entries(store)
    bounds = {} if bounds is None else bounds
    out, why = [], {}
    for eid, ent in sorted(le._all_entries(store).items()):
        if eid in used or eid in standing or ent.get("kind") == "vegetation":
            continue
        reason, appears = next_build.outside_reason(eid, ent, dec.get(eid), rel)
        if appears or reason == "excluded":
            continue
        t = typ.get(ent.get("typology") or "")
        group = FAMILY_GROUP.get(t.family) if t else None
        if group is None:
            why[eid] = "no catalog family"
            continue
        if not ok(eid):
            why[eid] = "not approved"
            continue
        m = _model(store, eid, ent, bounds)
        if m is None:
            why[eid] = "no model built"
            continue
        out.append({"entry": eid, "group": group, "one_off": one_off(eid, t.id, t.max_count), "max_count": t.max_count,
                    "w": m["dims"][0], "d": m["dims"][1], "typology": t.id, "stretch": m["active"] is not None, "model": m})
    return out, why


def family_models(store: ProjectStore, ok, bounds: dict | None = None) -> list[dict]:
    """Every building entry of a catalog family that may stand (ok) with a model, placed or not:
    [{entry, family, w, d, model}] (city_library.swap_fit: a stretched place swaps to the best
    fitting). Not kit modules, attached pieces, variants, excluded entries or ones whose own model
    would stretch past city_library.STRETCH_CAP at its size."""
    from . import library_entries as le, next_build
    from .city_library import STRETCH_CAP, _ratio
    from .stages import sw_library
    dec = sw_library.decisions(store)
    rel = next_build._relations(store, dec)
    typ = _families(store)
    bounds = {} if bounds is None else bounds
    out = []
    for eid, ent in sorted(le._all_entries(store).items()):
        if (ent.get("kind") not in (None, "building") or (dec.get(eid) or {}).get("decision") in ("exclude", "kit")
                or rel["attached"].get(eid) or rel["variant"].get(eid) or ent.get("part") in sw_library.KIT_TYPES):
            continue
        t = typ.get(ent.get("typology") or "")
        if t is None or not t.family or not ok(eid):
            continue
        m = _model(store, eid, ent, bounds)
        if m is None:
            continue
        ext = np.subtract(m["hi"], m["lo"])
        if _ratio(m["dims"][0] / max(ext[0], 1e-9), m["dims"][1] / max(ext[1], 1e-9)) > STRETCH_CAP:
            continue
        out.append({"entry": eid, "family": t.family, "w": m["dims"][0], "d": m["dims"][1], "model": m})
    return out


def places(store: ProjectStore, gb: dict, placements: list[dict], ok, avoid_locked: bool,
           planned: dict[str, int] | None = None) -> list[dict]:
    """The placements an unplaced asset may take (module doc): with their group, district,
    waterfront and edge. planned {typed slot: buildings}: a block that stays greybox (fewer
    placeable builds than buildings: city_library.choose) has none to give."""
    from . import library_entries as le, next_build
    from .stages import sw_library
    slots = {s["id"]: s for s in gb["slots"]}
    dec = sw_library.decisions(store)
    parents = set(next_build._relations(store, dec)["attached"].values())
    ents, typ = le._all_entries(store), _families(store)
    locked = locked_slots(store) if avoid_locked else set()
    kit = kit_slots(store, ok)
    geo = _geography(gb)
    good: dict[str, int] = {}
    for p in placements:
        if p.get("typed") and not p.get("layer_of") and ok(p["entry"]):
            good[p["slot"]] = good.get(p["slot"], 0) + 1
    out = []
    for p in placements:
        s = slots.get(p.get("slot"))
        if p.get("typed") and planned is not None and good.get(p.get("slot"), 0) < planned.get(p.get("slot"), 0):
            continue                                      # its block stays greybox anyway
        e = p["entry"]
        if (s is None or p.get("kit") or p.get("prop") or p.get("layer_of") or p.get("variant_of") or p.get("unplaced")
                or p.get("planned") or e in parents or s["id"] in locked or s["id"] in kit or not ok(e)):
            continue
        ent = ents.get(e) or {}
        if ent.get("kind") not in (None, "building") or (ent.get("part") in sw_library.KIT_TYPES):
            continue                                      # an ensemble's piece (a forum gate), not a building
        t = typ.get(p.get("typology") or s.get("typology") or ent.get("typology") or "")
        civic = s["type"] in sw_library.KIT_TYPES or "-civic-" in s["id"]      # (a civic core's blocks)
        group = FAMILY_GROUP.get(t.family) if t else "civic" if civic else "housing"
        out.append({"key": p["key"], "slot": s["id"], "entry": e, "group": group, "typology": t.id if t else None,
                    "district": s.get("district"),
                    "w": float(p.get("w") or 0), "d": float(p.get("d") or 0)} | geo(p.get("x"), p.get("y"), s))
    return out


def kit_slots(store: ProjectStore, ok) -> set[str]:
    """Slots a facade kit replaces whole (city_library.choose): its kit has every module built and
    each passes the gate."""
    from . import kit_assembly
    lib = store.root / "3d" / "library"
    out = set()
    for k in kit_assembly.kits(store):
        mods = [a[0] for a in k["modules"].values()]
        if all((lib / a / "procedural.glb").is_file() and (lib / a / "procedural" / "job.json").is_file() and ok(a) for a in mods):
            out |= set(k["slots"])
    return out


def _geography(gb: dict):
    """f(x, y, slot) -> {waterfront, edge}: within WATERFRONT_M of a water slot's box; past the
    EDGE_PCT percentile of building slots' distance from their centroid."""
    def centre(s):
        (x0, y0, _), (x1, y1, _) = s["bbox"]
        return (x0 + x1) / 2, (y0 + y1) / 2
    water = [s["bbox"] for s in gb["slots"] if s["type"] in WATER_TYPES]
    blds = np.array([centre(s) for s in gb["slots"] if s.get("category") == "building"] or [(0.0, 0.0)])
    mid = blds.mean(0)
    edge_r = float(np.percentile(np.hypot(*(blds - mid).T), EDGE_PCT))

    def f(x, y, s):
        if x is None:
            x, y = centre(s)
        near = any(b[0][0] - WATERFRONT_M <= x <= b[1][0] + WATERFRONT_M and b[0][1] - WATERFRONT_M <= y <= b[1][1] + WATERFRONT_M
                   for b in water)
        return {"waterfront": near, "edge": bool(math.hypot(x - mid[0], y - mid[1]) >= edge_r)}
    return f


def opens(store: ProjectStore, gb: dict, index: dict | None, avoid_locked: bool):
    """The open-ground slots ([{slot, district, waterfront, edge}]) and open_fit(asset, slot) for select."""
    from shapely.ops import unary_union

    from . import kit_assembly
    from .stages.sw_library import SURFACE_TYPES
    locked = locked_slots(store) if avoid_locked else set()
    geo = _geography(gb)
    slots = {s["id"]: s for s in gb["slots"]}
    sites = [{"slot": s["id"], "district": s.get("district")} | geo(None, None, s)
             for s in gb["slots"] if s["type"] in OPEN_TYPES and s["id"] not in locked]
    cache: dict = {}

    def boxes(s):
        return [rect(*_box2d(W, lo, hi)) for W, lo, hi in kit_assembly.slot_boxes(s, index)]

    def obstacles():                                  # buildings and structures; surfaces' boxes span the city
        if "obs" not in cache:
            cache["obs"] = unary_union([b for s in gb["slots"] if s["type"] not in SURFACE_TYPES for b in boxes(s)])
        return cache["obs"]

    picked: list = []                                 # this build's open-ground footprints so far

    def open_fit(a, sid):
        from shapely.geometry import Point, box
        s = slots[sid]
        S = np.asarray(s["matrix"], float)
        trees, ground, own = [], [], []
        (x0, y0, _), (x1, y1, _) = s["bbox"]
        for p in s["pieces"]:
            W = S @ np.asarray(p["matrix_local"], float)
            if p["piece"].startswith("tree:"):
                trees.append(W[:2, 3])
                continue
            b = ((index or {}).get(p.get("mesh")) or {}).get("bounds")
            own.append(Point(*W[:2, 3]).buffer(PIN_M))       # its pivot: the placement check's overlap test
            if p["piece"] in ("lawn", "paving"):
                ground.append(rect(*_box2d(W, *b)) if b else box(x0, y0, x1, y1))
            elif b:
                own.append(rect(*_box2d(W, *b)))              # a pool, a garden bed: stands clear of it
        if not ground:
            open_fit.no_spot.setdefault(a["entry"], []).append(sid)
            return None
        area = unary_union(ground).buffer(-2.0)
        if area.is_empty:
            open_fit.no_spot.setdefault(a["entry"], []).append(sid)
            return None
        yaw = math.degrees(math.atan2(S[1, 0], S[0, 0]))
        cen = np.mean(trees, 0) if trees else np.array(area.centroid.coords[0])
        spot = free_spot(area, trees if s["type"] == "park" else [], unary_union([obstacles(), *own, *picked]),
                         a["w"], a["d"], yaw, cen)
        if spot is None:
            open_fit.no_spot.setdefault(a["entry"], []).append(sid)
            return None
        picked.append(rect(spot["x"], spot["y"], a["w"] * spot["k"], a["d"] * spot["k"], yaw))
        foot = rect(spot["x"], spot["y"], a["w"] * spot["k"] + 2, a["d"] * spot["k"] + 2, yaw)
        drop = [i for i, p in enumerate(s["pieces"]) if p["piece"].startswith("tree:")
                and foot.contains(Point(*(S @ np.asarray(p["matrix_local"], float))[:2, 3]))]
        return spot | {"turn": False, "pieces": drop, "z": float(S[2, 3])}
    open_fit.no_spot = {}                             # {entry: [open slots with no clear spot for it]}
    return sites, open_fit


def _box2d(W, lo, hi) -> tuple[float, float, float, float, float]:
    """(x, y, w, d, yaw) of a piece's box on the ground."""
    W = np.asarray(W, float)
    kx, ky = float(np.linalg.norm(W[:3, 0])), float(np.linalg.norm(W[:3, 1]))
    c = W @ np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2], 1.0])
    return float(c[0]), float(c[1]), float(hi[0] - lo[0]) * kx, float(hi[1] - lo[1]) * ky, math.degrees(math.atan2(W[1, 0], W[0, 0]))


def plan(store: ProjectStore, gb: dict, placements: list[dict], ok, index: dict | None, avoid_locked: bool = True,
         seed: int = SEED, bounds: dict | None = None, planned: dict[str, int] | None = None) -> dict:
    """The picks for this build: {picks, skipped, assets {entry: model}}; nothing changes yet.
    planned: {typed slot: buildings} (places)."""
    bounds = {} if bounds is None else bounds
    assets, why = candidates(store, ok, bounds, {p["entry"] for p in placements})
    sites, open_fit = opens(store, gb, index, avoid_locked)
    rep = select(assets, places(store, gb, placements, ok, avoid_locked, planned), sites, open_fit, seed)
    rep["skipped"] |= why
    rep["no_spot"] = open_fit.no_spot
    rep["assets"] = {a["entry"]: a["model"] for a in assets}
    return rep


def apply(store: ProjectStore, gb: dict, rep: dict, placements: list[dict], entries: dict, bounds: dict) -> list[dict]:
    """Swap the picked placements (in place) to their unplaced asset; returns the new open-ground
    placements (placed after the mixed-slot rule, like props: their trees leave the slot)."""
    from pathlib import Path

    from .assembly import glb_bounds
    from .city_library import world_matrix
    by_key = {p["key"]: p for p in placements}
    new = []
    for e, ps in sorted(rep["picks"].items()):
        m = rep["assets"][e]
        extra = {"entry": e, "glb": m["glb"], "mesh": m["mesh"]} | (
            {"active": m["active"], "active_kind": m["kind"]} if m["active"] is not None else {})
        if m["active"] is None:
            entries[e] = (Path("3d/library") / e / "procedural" / "job.json").as_posix()
        for c in ps:
            if c["kind"] == "swap":
                p = by_key[c["key"]]
                if p["glb"] not in bounds:
                    bounds[p["glb"]] = [v.tolist() for v in glb_bounds(store.root / p["glb"])]
                plo, phi = bounds[p["glb"]]
                centre = [(plo[0] + phi[0]) / 2, (plo[1] + phi[1]) / 2, plo[2]]
                dims = np.subtract(phi, plo).tolist() if c.get("own") else m["dims"]     # its own typology: that build's size
                M = placed_matrix(world_matrix(p), centre, m["lo"], m["hi"], dims, c["k"], c["turn"])
                p.update(extra | {"matrix": M.tolist(), "unplaced": {"from": c["from"], "k": c["k"]}})
                p.pop("vrole", None)
            else:
                B = world_matrix({"x": c["x"], "y": c["y"], "z": c["z"], "yaw": c["yaw"]})
                M = placed_matrix(B, [0.0, 0.0, 0.0], m["lo"], m["hi"], m["dims"], c["k"], False)
                n = sum(1 for q in new if q["slot"] == c["slot"])
                new.append(extra | {"key": f"unplaced_{e}_{c['slot']}_{n}".replace("-", "m"), "slot": c["slot"],
                                    "matrix": M.tolist(), "pieces": c["pieces"], "unplaced": {"from": None, "k": c["k"]},
                                    "open": True})
    return new


def no_spot_lines(rep: dict) -> list[str]:
    """The open-ground places skipped for want of a clear spot: "temple: 2 parks (park-a, park-b); placed 1"."""
    out = []
    for e, sids in sorted((rep or {}).get("no_spot", {}).items()):
        got = sum(p["kind"] == "open" for p in rep["picks"].get(e, []))
        out.append(f"{e}: {len(sids)} {'park' if len(sids) == 1 else 'parks'} ({', '.join(sids)}); "
                   + (f"placed {got} elsewhere" if got else "not placed on open ground"))
    return out


def summary(rep: dict | None) -> dict:
    """What the build report and the preview show: {swapped, open, lines, skipped, no_spot_lines}."""
    if not rep:
        return {"swapped": 0, "open": 0, "lines": [], "skipped": {}, "no_spot_lines": []}
    ps = [p for v in rep["picks"].values() for p in v]
    return {"swapped": sum(p["kind"] == "swap" for p in ps), "open": sum(p["kind"] == "open" for p in ps),
            "lines": lines(rep), "skipped": dict(sorted(rep["skipped"].items())), "no_spot_lines": no_spot_lines(rep),
            "assets": [{"entry": e, "count": len(v), "slots": sorted({p["slot"] for p in v}),
                        "from": sorted({p["from"] for p in v if p["from"]}), "open": sum(p["kind"] == "open" for p in v)}
                       for e, v in sorted(rep["picks"].items())]}


def preview(store: ProjectStore, use_active: bool = False, avoid_locked: bool = True, seed: int = SEED) -> dict:
    """The "Will also place" list for the next build (summary), computed as the build will."""
    from . import city_library, next_build
    from .stages import s7_export, sw_site
    gb = sw_site.load_greybox(store)
    index = read_json(s7_export.export_dir(store) / "mesh_index.json", default=None)
    placements, _, buildings, _ = city_library.base_placements(store, gb)
    return summary(plan(store, gb, placements, next_build.gate(store, use_active), index, avoid_locked, seed,
                        planned={sid: len(v) for sid, v in buildings.items()}))

"""Kit assembly: block slots assigned to a facade kit are built from its modules, for any project.

A kit module is an asset with a role:
    bay      repeats along a facade
    corner   goes at a block's outer corners (an arcade corner: its front (-Y) and left (-X)
             faces are facades, its outer corner at its bounds' (lo x, lo y))
    end      optional: caps a run where no corner module stands (an inner corner of an L)
    filler   optional: closes what the bays can't (narrower than a bay)
Modules face -Y (building_parts' facade frame), x along the facade.

Roles: the user's choice (Manage entry… › Kit modules; site/library_review.json, the asset's own
record: "kit_role", "none" for not a module), else detected from the asset id's words (…-corner,
…-bay), the brief (an arcade on two adjacent faces is a corner, on its front only a bay) and the
kit piece's description.

Kits: an entry decided "kit" (its modules are <entry>-<piece>), or standalone entries with a role
grouped by their kit id (the asset's record "kit", else its id without the role word:
my-block-bay -> my-block). A kit's slots: the kit entry's slot_ids plus its modules' own.

Per slot (`assemble`): the footprint outline (the union of the slot's greybox masses, `outline`;
one assembly per separate block and tier), a corner module at each outer corner turned to face
out, each edge filled with bays scaled along the facade by at most SCALE_TOL to close it; when
that isn't enough, fillers, else a gap (reported). Inner corners take an end module per run if
the kit has one, else the two runs meet there.

Heights (vertical.py): each module is built at the height of the masses under it (a height
variant with the storeys that fill it); only past a builder's most storeys, or for a module with
no building brief, do layers stack, each with its vertical role (bottom, middle, top: no ground
floors above the first, no roof features under another layer). A mass standing on a larger one
(a setback, `outline`) is a tier of its own: its modules stand on the modules under them, the
lower tier's without roof features, the upper's without ground floors. Placements carry Blender
world matrices; city_library grounds each column's first layer and stacks the rest on it.
"""
import math
import re
from pathlib import Path

import numpy as np

from .project import ProjectStore, read_json, write_json

ROLES = ("bay", "corner", "end", "filler")
SCALE_TOL = 0.10           # a run's bays scale along the facade by at most this to close an edge
GAP_TOL = 0.05             # m: a gap or overlap below this isn't reported
MIN_PIECE_H = 2.0          # m: greybox pieces lower than this (paving, kerbs) aren't the block's mass
MIN_PIECE_AREA = 4.0       # m2: nor are thinner ones (piers, columns)
FUSE_M = 0.3               # m: masses closer than this make one block
SIT_M = 0.6                # m: a mass whose base is this close to another's top stands on it
SETBACK = 1.3              # a mass on others whose footprint is this many times its own is a setback tier
CORNER_DEG = 10.0          # an outer corner further than this from square is reported

ROLE_WORDS = (("corner", ("corner", "corners")), ("end", ("end", "cap", "terminal")),
              ("filler", ("filler", "fill", "infill", "spacer")), ("bay", ("bay", "bays", "module", "repeat")))


# --- roles -----------------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def role_from_words(text: str) -> str | None:
    ws = set(_words(text))
    return next((r for r, keys in ROLE_WORDS if ws & set(keys)), None)


def role_from_brief(brief: dict | None) -> str | None:
    """An arcade brief on two adjacent faces is a corner, on its front alone a bay."""
    b = brief or {}
    if b.get("form") != "arcade":
        return None
    faces = set((b.get("features") or {}).get("faces") or ["front"])
    if faces & {"front", "back"} and faces & {"left", "right"}:
        return "corner"
    return "bay" if faces <= {"front", "back"} else None


def detect_role(asset: str, piece: str | None = None, brief: dict | None = None, description: str = "") -> str | None:
    """The role an asset's words or brief point to: its piece name (or id), then the brief, then
    the description."""
    return (role_from_words(piece if piece is not None else asset) or role_from_brief(brief)
            or role_from_words(description))


def _decisions(store: ProjectStore) -> dict:
    from .stages import sw_library
    return sw_library.decisions(store)


def _entries(store: ProjectStore) -> dict[str, dict]:
    from . import library_entries
    return library_entries._all_entries(store)


def _brief(store: ProjectStore, asset: str) -> dict | None:
    from . import kit_proc
    return read_json(kit_proc.spec_path(store, asset), default=None)


def modules(store: ProjectStore, entry: str) -> list[dict]:
    """The kit modules Manage entry… shows for an entry: a kit decision's modules, else the entry
    itself. [{asset, role (effective, or None), detected, source ("yours" / "detected" / None)}]."""
    ents = _entries(store)
    if entry not in ents:
        raise KeyError(f"no library entry {entry}")
    return _modules(store, entry, ents, _decisions(store))


def _modules(store: ProjectStore, entry: str, ents: dict, d: dict) -> list[dict]:
    dec = d.get(entry) or {}
    if dec.get("decision") == "kit":
        items = [(f"{entry}-{p}", p, pc.get("description", "")) for p, pc in (dec.get("pieces") or {}).items()]
    else:
        e = ents[entry]
        items = [(entry, None, dec.get("description") or e.get("description_auto") or "")]
    out = []
    for asset, piece, desc in items:
        det = detect_role(asset, piece, _brief(store, asset), desc)
        mine = (d.get(asset) or {}).get("kit_role")
        role = None if mine == "none" else (mine or det)
        out.append({"asset": asset, "role": role, "detected": det, "source": "yours" if mine else ("detected" if det else None)})
    return out


def set_roles(store: ProjectStore, entry: str, roles: dict[str, str | None]) -> list[dict]:
    """Manage entry… › Kit modules: {asset: role | "none" | None (back to detected)} for the
    entry's modules."""
    from .stages import sw_library
    own = {m["asset"] for m in modules(store, entry)}
    d = _decisions(store)
    for asset, role in roles.items():
        if asset not in own:
            raise KeyError(f"{asset} is not a module of {entry}")
        if role not in (None, "", "none", *ROLES):
            raise ValueError(f"role must be one of {ROLES}, none, or empty (detected)")
        rec = d.setdefault(asset, {})
        if role:
            rec["kit_role"] = role
        else:
            rec.pop("kit_role", None)
        if not rec:
            d.pop(asset)
    write_json(sw_library.decisions_path(store), d)
    return modules(store, entry)


def kits(store: ProjectStore) -> list[dict]:
    """Every kit with roles: [{kit, slots [ids], modules {role: [assets]}}], only kits that have a
    bay and at least one slot."""
    d, ents = _decisions(store), _entries(store)
    groups: dict[str, dict] = {}
    for eid, e in sorted(ents.items()):
        dec = d.get(eid) or {}
        if dec.get("decision") == "exclude":
            continue
        for m in _modules(store, eid, ents, d):
            if not m["role"]:
                continue
            if dec.get("decision") == "kit":
                kid = eid
            else:
                kid = (d.get(eid) or {}).get("kit") or "-".join(w for w in eid.split("-") if role_from_words(w) != m["role"]) or eid
            g = groups.setdefault(kid, {"kit": kid, "slots": [], "modules": {}})
            g["modules"].setdefault(m["role"], []).append(m["asset"])
            g["slots"] += [s for s in e.get("slot_ids") or [] if s not in g["slots"]]
    for kid, g in groups.items():
        g["slots"] += [s for s in (ents.get(kid) or {}).get("slot_ids") or [] if s not in g["slots"]]
    return [g for _, g in sorted(groups.items()) if g["modules"].get("bay") and g["slots"]]


# --- the footprint outline -------------------------------------------------------------------

def slot_boxes(slot: dict, index: dict | None) -> list[tuple[np.ndarray, list, list]]:
    """The slot's greybox pieces as (world 4x4, lo, hi) boxes (bounds from the mesh export
    index); without the index or bounds, the slot's world bbox."""
    S = np.asarray(slot["matrix"], float)
    out = []
    for p in slot.get("pieces") or []:
        b = ((index or {}).get(p.get("mesh")) or {}).get("bounds")
        if b:
            out.append((S @ np.asarray(p["matrix_local"], float), b[0], b[1]))
    if not out:
        out = [(np.eye(4), slot["bbox"][0], slot["bbox"][1])]
    return out


def outline(boxes) -> list[dict]:
    """The blocks a slot's masses make: [{"pts": outer ring CCW [(x, y)], "z0", "height", "tier",
    "role", "masses" [(polygon, base, top)]}]. Low and thin pieces don't count; masses within
    FUSE_M merge; courtyards are ignored. A mass standing on others (its base on their top) whose
    footprint is clearly smaller (SETBACK) starts a tier above theirs; one about as large (a
    greybox building's upper storeys over its ground storey) continues their tier. Roles
    (vertical.py): single (no tier on or under it), bottom, middle, top."""
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    polys = []
    for W, lo, hi in boxes:
        W = np.asarray(W, float)
        corners = [(W @ np.array([x, y, lo[2], 1.0]))[:2] for x, y in
                   ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]
        h = (hi[2] - lo[2]) * float(np.linalg.norm(W[:3, 2]))
        poly = Polygon(corners)
        if h < MIN_PIECE_H or poly.area < MIN_PIECE_AREA:
            continue
        base = float((W @ np.array([0, 0, lo[2], 1.0]))[2])
        polys.append((poly, base, base + h))
    if not polys:
        return []
    tier = {}
    for i in sorted(range(len(polys)), key=lambda i: polys[i][1]):          # from the lowest base up
        P, b, _ = polys[i]
        under = [j for j in tier if abs(polys[j][2] - b) < SIT_M and P.intersection(polys[j][0]).area > 0.25 * P.area]
        if not under:
            tier[i] = 0
            continue
        t = max(tier[j] for j in under)
        tier[i] = t + 1 if unary_union([polys[j][0] for j in under]).area >= SETBACK * P.area else t
    out = []
    for t in sorted(set(tier.values())):
        mine = [polys[i] for i in tier if tier[i] == t]
        u = unary_union([p for p, *_ in mine]).buffer(FUSE_M / 2, join_style="mitre").buffer(-FUSE_M / 2, join_style="mitre")
        for g in getattr(u, "geoms", [u]):
            g = orient(Polygon(g.exterior).simplify(0.05), 1.0)
            ms = [m for m in mine if m[0].intersects(g)]
            z0 = min(b for _, b, _ in ms)
            out.append({"pts": [tuple(map(float, c)) for c in list(g.exterior.coords)[:-1]], "z0": z0,
                        "height": max(t_ for *_, t_ in ms) - z0, "tier": t, "masses": ms, "_poly": g})
    for blk in out:
        below = any(o["tier"] == blk["tier"] - 1 and o["_poly"].intersects(blk["_poly"]) for o in out)
        above = any(o["tier"] == blk["tier"] + 1 and o["_poly"].intersects(blk["_poly"]) for o in out)
        blk["role"] = {(False, False): "single", (False, True): "bottom", (True, True): "middle", (True, False): "top"}[(below, above)]
    for blk in out:
        del blk["_poly"]
    return out


def local_height(masses, foot) -> tuple[float, float] | None:
    """(base, height) of the masses under a module's footprint polygon (those covering at least
    a tenth of it), or None."""
    ms = [(b, t) for P, b, t in masses if P.intersection(foot).area > 0.1 * foot.area]
    if not ms:
        return None
    z0 = min(b for b, _ in ms)
    return z0, max(t for _, t in ms) - z0


# --- the assembly ----------------------------------------------------------------------------

def _clean(pts) -> list[np.ndarray]:
    """CCW points without repeats or straight-through vertices."""
    P = [np.asarray(p, float) for p in pts]
    if sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(P, P[1:] + P[:1])) < 0:
        P = P[::-1]
    changed = True
    while changed and len(P) > 3:
        changed = False
        for i in range(len(P)):
            a, b, c = P[i - 1], P[i], P[(i + 1) % len(P)]
            u, v = b - a, c - b
            if np.linalg.norm(u) < 1e-6 or abs(u[0] * v[1] - u[1] * v[0]) < 1e-6 * np.linalg.norm(u) * np.linalg.norm(v) \
                    and u @ v > 0:
                del P[i]
                changed = True
                break
    return P


def module_matrix(at, t, lo, k: float = 1.0, z: float = 0.0) -> np.ndarray:
    """A module's Blender world 4x4: x along unit t, its front (-Y) facing out (t turned -90 deg),
    scaled k along x, its (lo x, lo y) bounds corner at the point `at`, its base (lo z) at z."""
    R = np.array([[t[0], -t[1]], [t[1], t[0]]])
    W = np.eye(4)
    W[:2, :2] = R @ np.diag([k, 1.0])
    W[:2, 3] = np.asarray(at, float) - R @ np.array([lo[0] * k, lo[1]])
    W[2, 3] = z - lo[2]
    return W


def fill(span: float, bay_w: float, filler_w: float | None = None) -> dict:
    """How a run of `span` m is closed: {bays, k (their scale along the facade), fillers, kf,
    gap (m left open)}. Bays scale by at most SCALE_TOL; then fillers (also within SCALE_TOL)."""
    if span <= GAP_TOL:
        return {"bays": 0, "k": 1.0, "fillers": 0, "kf": 1.0, "gap": max(span, 0.0)}
    best = None
    for n in {max(1, math.floor(span / bay_w)), max(1, math.ceil(span / bay_w))}:
        k = span / (n * bay_w)
        if abs(k - 1) <= SCALE_TOL + 1e-9 and (best is None or abs(k - 1) < abs(best[1] - 1)):
            best = (n, k)
    if best:
        return {"bays": best[0], "k": best[1], "fillers": 0, "kf": 1.0, "gap": 0.0}
    n = math.floor(span / (bay_w * (1 + SCALE_TOL)))         # bays at their widest, the rest left over
    k = min(1 + SCALE_TOL, span / (n * bay_w)) if n else 1.0
    rest = span - n * bay_w * k
    m, kf = 0, 1.0
    if filler_w:
        m = math.floor(rest / (filler_w * (1 - SCALE_TOL)) + 1e-9)
        if m:
            kf = min(1 + SCALE_TOL, rest / (m * filler_w))
            rest -= m * filler_w * kf
    return {"bays": n, "k": k, "fillers": m, "kf": kf, "gap": max(rest, 0.0)}


def assemble(pts, mods: dict[str, tuple], height: float, z0: float = 0.0, stack: bool = True) -> tuple[list[dict], dict]:
    """A block's modules around its outline pts (any orientation). mods: {role: (lo, hi)} module
    bounds (Blender frame); a bay is required. Returns ([{role, matrix, course, edge, k}],
    report {courses, gaps [{edge, m}], inner_corners, off_square [deg], short_edges [{edge, m}],
    scaled [{edge, k}]}). stack False: the ring of modules only (course 0; `placements` fits
    their heights), else whole copies stacked to the height."""
    if "bay" not in mods:
        raise ValueError("a kit needs a bay module")
    P = _clean(pts)
    n = len(P)
    size = {r: (np.subtract(hi, lo)) for r, (lo, hi) in mods.items()}
    lo_of = {r: lo for r, (lo, _) in mods.items()}
    bay_w, bay_d, bay_h = size["bay"]
    T = [(P[(i + 1) % n] - P[i]) for i in range(n)]
    L = [float(np.linalg.norm(t)) for t in T]
    T = [t / max(ln, 1e-9) for t, ln in zip(T, L)]
    cross = [T[i - 1][0] * T[i][1] - T[i - 1][1] * T[i][0] for i in range(n)]    # at vertex i
    convex = [c > 0 for c in cross]
    courses = max(1, round(height / bay_h)) if bay_h > 0 and stack else 1
    rep = {"courses": courses, "gaps": [], "inner_corners": sum(1 for c in convex if not c), "off_square": [],
           "short_edges": [], "scaled": []}
    base: list[dict] = []
    for i in range(n):
        if convex[i] and "corner" in mods:
            turn = math.degrees(math.atan2(cross[i], float(T[i - 1] @ T[i])))
            if abs(turn - 90) > CORNER_DEG:
                rep["off_square"].append(round(turn - 90, 1))
            base.append({"role": "corner", "matrix": module_matrix(P[i], T[i], lo_of["corner"], 1.0, z0), "edge": i, "k": 1.0})
    for i in range(n):
        j = (i + 1) % n
        start = end = 0.0
        if convex[i]:
            start = size["corner"][0] if "corner" in mods else 0.0
        elif "end" in mods:
            start = size["end"][0]
            base.append({"role": "end", "matrix": module_matrix(P[i], T[i], lo_of["end"], 1.0, z0), "edge": i, "k": 1.0})
        if convex[j]:
            end = size["corner"][1] if "corner" in mods else bay_d     # without a corner the next run covers it
        elif "end" in mods:
            end = size["end"][0]
            base.append({"role": "end", "matrix": module_matrix(P[i] + T[i] * (L[i] - end), T[i], lo_of["end"], 1.0, z0),
                         "edge": i, "k": 1.0})
        span = L[i] - start - end
        if span < -GAP_TOL:
            rep["short_edges"].append({"edge": i, "m": round(-span, 2)})
            continue
        f = fill(span, bay_w, size["filler"][0] if "filler" in mods else None)
        if f["bays"] and abs(f["k"] - 1) > 1e-6:
            rep["scaled"].append({"edge": i, "k": round(f["k"], 4)})
        if f["gap"] > GAP_TOL:
            rep["gaps"].append({"edge": i, "m": round(f["gap"], 2)})
        seq = ["bay"] * f["bays"]
        seq[len(seq) // 2:len(seq) // 2] = ["filler"] * f["fillers"]       # fillers mid-run
        s = start + f["gap"] / 2                                            # a gap split between the run's ends
        for r in seq:
            k = f["k"] if r == "bay" else f["kf"]
            base.append({"role": r, "matrix": module_matrix(P[i] + T[i] * s, T[i], lo_of[r], k, z0), "edge": i, "k": k})
            s += size[r][0] * k
    out = []
    for c in range(courses):
        for b in base:
            W = np.array(b["matrix"])
            W[2, 3] += c * size[b["role"]][2]
            out.append(b | {"matrix": W, "course": c})
    return out, rep


def module_outline(W: np.ndarray, lo, hi) -> list[tuple[float, float]]:
    """A placed module's footprint corners in the world (for checks)."""
    return [tuple((W @ np.array([x, y, lo[2], 1.0]))[:2]) for x, y in
            ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]


# --- the project's placements ----------------------------------------------------------------

def _key(*parts) -> str:
    return "_".join(str(p) for p in parts).replace("-", "m")


def placements(store: ProjectStore, gb: dict, index: dict | None, bounds: dict, fitter=None) -> tuple[list[dict], list[dict]]:
    """Kit placements for every slot assigned to a kit: ([placement], [report per slot]).
    Placements carry key, entry (the module's asset id), mesh (one asset per module and height
    variant), glb, matrix, slot, kit, role, tier, course (the layer in its column), vrole (its
    vertical role) and, above a column's first layer, base (that layer's key, or the key of the
    column it stands on in the tier below) and lift (m). bounds: {glb: [lo, hi]} (filled in);
    fitter: vertical.Fitter (height variants; built in Blender when missing). A slot whose kit
    has an unbuilt module gets none."""
    from shapely.geometry import Polygon

    from . import vertical
    from .assembly import glb_bounds
    fitter = fitter or vertical.Fitter(store)
    lib = store.root / "3d" / "library"
    slots = {s["id"]: s for s in gb["slots"]}

    def bounds_of(rel):
        if rel not in bounds:
            bounds[rel] = [v.tolist() for v in glb_bounds(store.root / rel)]
        return bounds[rel]
    out, reps = [], []
    for kit in kits(store):
        mods, missing, extra = {}, [], []
        for role, assets in kit["modules"].items():
            a = assets[0]
            extra += assets[1:]
            glb = lib / a / "procedural.glb"
            if not (glb.is_file() and (lib / a / "procedural" / "job.json").is_file()):
                missing.append(a)
                continue
            rel = glb.relative_to(store.root).as_posix()
            bounds_of(rel)
            mods[role] = (a, rel)
        for sid in kit["slots"]:
            s = slots.get(sid)
            if s is None:
                continue
            r = {"slot": sid, "kit": kit["kit"], "unbuilt": missing, "unused": extra, "blocks": []}
            reps.append(r)
            if missing:
                continue
            m_bounds = {role: tuple(bounds[rel]) for role, (_, rel) in mods.items()}
            columns = []                               # (tier, footprint, first layer's key, top over its origin)
            for bi, blk in enumerate(outline(slot_boxes(s, index))):
                ring, br = assemble(blk["pts"], m_bounds, blk["height"], blk["z0"], stack=False)
                fit = {"fitted": 0, "stacked": 0, "copies": 0, "layers": 1}
                for idx, p in enumerate(ring):
                    a, rel = mods[p["role"]]
                    lo, hi = m_bounds[p["role"]]
                    foot = Polygon(module_outline(p["matrix"], lo, hi))
                    z0, H = local_height(blk["masses"], foot) or (blk["z0"], blk["height"])
                    plan = fitter.layers(a, H, blk["role"])
                    if plan is None:                   # no building brief: copies of its own build
                        k = max(1, round(H / max(hi[2] - lo[2], 1e-6)))
                        col = [(rel, None)] * k
                        fit["copies" if k > 1 else "fitted"] += 1
                    else:
                        col = [(fitter.glb(a, ly), ly["role"]) for ly in plan]
                        fit["stacked" if len(col) > 1 else "fitted"] += 1
                    fit["layers"] = max(fit["layers"], len(col))
                    lifts = vertical.stack_lifts([bounds_of(g) for g, _ in col])
                    under = None
                    if blk["tier"]:                    # stands on the column it covers most in the tier below
                        low = [u for u in columns if u[0] == blk["tier"] - 1]
                        under = max(low, key=lambda u: (u[1].intersection(foot).area, -u[1].distance(foot)), default=None)
                    first = None
                    for c, ((g, vrole), lift) in enumerate(zip(col, lifts)):
                        key = _key(a, sid, bi, idx, c)
                        name = Path(g).stem
                        row = {"key": key, "entry": a, "mesh": f"kit_{a}" if g == rel else f"kit_{a}_{name}", "glb": g,
                               "matrix": p["matrix"].tolist(), "slot": sid, "typed": False, "kit": kit["kit"],
                               "role": p["role"], "tier": blk["tier"], "course": c, "vrole": vrole}
                        if under is not None:
                            row |= {"base": under[2], "lift": round(under[3] - bounds_of(col[0][0])[0][2] + lift, 4)}
                        elif c:
                            row |= {"base": first, "lift": round(lift, 4)}
                        first = first or key
                        out.append(row)
                    top = (under[3] - bounds_of(col[0][0])[0][2] if under else 0.0) + lifts[-1] + bounds_of(col[-1][0])[1][2]
                    columns.append((blk["tier"], foot, under[2] if under else first, top))
                r["blocks"].append(br | {"modules": sum(1 for _ in ring), "courses": fit["layers"], "tier": blk["tier"],
                                         "vrole": blk["role"], "fit": {k: v for k, v in fit.items() if k != "layers"}})
    return out, reps


def report_lines(reps: list[dict]) -> list[str]:
    """What the build report says about kit slots: gaps, short edges, unbuilt modules."""
    out = []
    for r in reps:
        if r["unbuilt"]:
            out.append(f"kit {r['kit']} in {r['slot']}: not placed, unbuilt module(s) {', '.join(r['unbuilt'])}")
            continue
        for b in r["blocks"]:
            if b["gaps"]:
                out.append(f"kit {r['kit']} in {r['slot']}: gaps " + ", ".join(f"{g['m']} m (edge {g['edge']})" for g in b["gaps"]))
            if b["short_edges"]:
                out.append(f"kit {r['kit']} in {r['slot']}: corners overlap on short edges "
                           + ", ".join(f"{g['m']} m (edge {g['edge']})" for g in b["short_edges"]))
            if b["off_square"]:
                out.append(f"kit {r['kit']} in {r['slot']}: corners off square by {b['off_square']} deg")
            fit = b.get("fit") or {}
            if fit.get("stacked"):
                out.append(f"kit {r['kit']} in {r['slot']}: {fit['stacked']} module(s) in up to {b['courses']} layers "
                           "(taller than one build makes)")
            if fit.get("copies"):
                out.append(f"kit {r['kit']} in {r['slot']}: {fit['copies']} module(s) stacked as copies (no building brief: "
                           "their ground floors and roofs repeat)")
    return out

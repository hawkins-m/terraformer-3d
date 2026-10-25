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
one assembly per separate block), a corner module at each outer corner turned to face out, each
edge filled with bays scaled along the facade by at most SCALE_TOL to close it; when that isn't
enough, fillers, else a gap (reported). Inner corners take an end module per run if the kit has
one, else the two runs meet there. A slot taller than one module stacks courses. Placements
carry Blender world matrices; city_library grounds course 0 and stacks the rest on it.
"""
import math
import re

import numpy as np

from .project import ProjectStore, read_json, write_json

ROLES = ("bay", "corner", "end", "filler")
SCALE_TOL = 0.10           # a run's bays scale along the facade by at most this to close an edge
GAP_TOL = 0.05             # m: a gap or overlap below this isn't reported
MIN_PIECE_H = 2.0          # m: greybox pieces lower than this (paving, kerbs) aren't the block's mass
MIN_PIECE_AREA = 4.0       # m2: nor are thinner ones (piers, columns)
FUSE_M = 0.3               # m: masses closer than this make one block
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
    """The blocks a slot's masses make: [{"pts": outer ring CCW [(x, y)], "z0", "height"}]. Low
    and thin pieces don't count; masses within FUSE_M merge; courtyards are ignored."""
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
    u = unary_union([p for p, *_ in polys]).buffer(FUSE_M / 2, join_style="mitre").buffer(-FUSE_M / 2, join_style="mitre")
    out = []
    for g in getattr(u, "geoms", [u]):
        g = orient(Polygon(g.exterior).simplify(0.05), 1.0)
        mine = [(b, t) for p, b, t in polys if p.intersects(g)]
        z0 = min(b for b, _ in mine)
        out.append({"pts": [tuple(map(float, c)) for c in list(g.exterior.coords)[:-1]], "z0": z0,
                    "height": max(t for _, t in mine) - z0})
    return out


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


def assemble(pts, mods: dict[str, tuple], height: float, z0: float = 0.0) -> tuple[list[dict], dict]:
    """A block's modules around its outline pts (any orientation). mods: {role: (lo, hi)} module
    bounds (Blender frame); a bay is required. Returns ([{role, matrix, course, edge, k}],
    report {courses, gaps [{edge, m}], inner_corners, off_square [deg], short_edges [{edge, m}],
    scaled [{edge, k}]})."""
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
    courses = max(1, round(height / bay_h)) if bay_h > 0 else 1
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


def placements(store: ProjectStore, gb: dict, index: dict | None, bounds: dict) -> tuple[list[dict], list[dict]]:
    """Kit placements for every slot assigned to a kit: ([placement], [report per slot]).
    Placements carry key, entry (the module's asset id), mesh (one asset per module), glb,
    matrix, slot, kit, role, course and, above course 0, base (its course-0 key) and lift (m).
    bounds: {glb: [lo, hi]} (filled in). A slot whose kit has an unbuilt module gets none."""
    from .assembly import glb_bounds
    lib = store.root / "3d" / "library"
    slots = {s["id"]: s for s in gb["slots"]}
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
            if rel not in bounds:
                bounds[rel] = [v.tolist() for v in glb_bounds(glb)]
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
            for bi, blk in enumerate(outline(slot_boxes(s, index))):
                placed, br = assemble(blk["pts"], m_bounds, blk["height"], blk["z0"])
                r["blocks"].append(br | {"modules": len(placed)})
                first = {}
                for pi, p in enumerate(placed):
                    a, rel = mods[p["role"]]
                    idx = pi % (len(placed) // br["courses"])
                    key = _key(a, sid, bi, idx, p["course"])
                    row = {"key": key, "entry": a, "mesh": f"kit_{a}", "glb": rel, "matrix": p["matrix"].tolist(),
                           "slot": sid, "typed": False, "kit": kit["kit"], "role": p["role"], "course": p["course"]}
                    if p["course"] == 0:
                        first[idx] = key
                    else:
                        lo, hi = bounds[rel]
                        row |= {"base": first[idx], "lift": p["course"] * (hi[2] - lo[2])}
                    out.append(row)
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
    return out

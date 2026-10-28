"""The city's library layer for the Unreal manifest (`ap export --assets`, `ap ue build`): which
library builds stand in the city level instead of their greybox.

Placements are those of the look scenes (`look._candidates` with no camera: the whole plan):
typed buildings (one instance build per planned building, no slot id) and kit slots (the entry's
build at the slot's pose). Attached pieces come with their placed parent
(`assembly.child_placements`). Duplicates used as variants (variants.py) share their original's
prop and kit-slot placements, split evenly and the same between builds.

Which builds: Approved in library review (default), or every built asset's Active build
(`use_active`, for previews); these choices come from next_build.py, which also explains them. An entry whose Active version isn't procedural (a TRELLIS model)
stands with that model at every one of its placements, scaled to each footprint and height,
instead of its procedural builds (`use_active_models`; the build report names them): its Clean up
output for the asset's tier when one exists for that version, else the raw model.

Slots assigned to a facade kit (kit_assembly: corner, bay, end and filler modules around the slot's
footprint outline) are assembled from the kit's modules: every module placement, each its own
library actor, when all the kit's modules are approved; the slot is then replaced whole.

Heights (vertical.py): a procedural build standing alone in a slot (a kit slot's entry, a
representative build) is built at the height of the slot's masses (a height variant), after
the variant split, so the split doesn't depend on it; only when that isn't possible do layers
stack (keys <key>_L<n>, "layer_of" their first), each with its vertical role. Typed buildings'
instance builds are already built at their planned storeys.

Mixed slots (no partial slots):
  - a kit slot whose build is placed is replaced whole (its greybox goes);
  - a typed slot (a block of several buildings) is replaced only when every planned building in
    it has a placed build: the greybox pieces standing on those footprints (+1 m, as in the look
    scenes) go and the rest of the slot (courts, paving) stays;
  - otherwise the whole slot stays greybox and its builds are not placed (`held`).

Each placed build is an actor of its own in Unreal, attached to its slot's actor: the manifest
carries its transform relative to the slot, so a slot moved by hand in Unreal takes its builds
along. Their GLBs go to export/library/ without embedded textures (`strip_textures`: the
importer gives them the entry's material instances; Interchange would import every map).
"""
import json
import math
import shutil
import struct
from pathlib import Path

import numpy as np

from . import ue_coords
from .project import ProjectStore, read_json

MATERIAL_OVERRIDES = "site/material_overrides.json"   # {entry: {tint, metallic, roughness}}: any asset
FOOT_MARGIN = 1.0          # m: a greybox piece within this of a placed footprint goes with it
LIB_DIR = "library"        # export/library/<asset id>.glb


# --- pure parts ------------------------------------------------------------------------------

def slot_of_point(slots: list[dict], typology: str, x: float, y: float) -> str | None:
    """The typed slot of a building: of its typology, its bbox around (x, y); the nearest slot
    centre when bboxes overlap."""
    best = None
    for s in slots:
        if s.get("typology") != typology:
            continue
        (x0, y0, _), (x1, y1, _) = s["bbox"]
        if x0 - FOOT_MARGIN <= x <= x1 + FOOT_MARGIN and y0 - FOOT_MARGIN <= y <= y1 + FOOT_MARGIN:
            m = np.asarray(s["matrix"], float)
            d = math.hypot(m[0, 3] - x, m[1, 3] - y)
            if best is None or d < best[0]:
                best = (d, s["id"])
    return best[1] if best else None


def choose(placements: list[dict], planned: dict[str, int], ok) -> tuple[list[dict], dict[str, str], list[dict]]:
    """The mixed-slot rule. placements carry "slot" (kit slots: theirs; typed: slot_of_point) and
    "typed"; planned {typed slot: planned buildings in it}; ok(entry): may this entry's build be
    placed. Returns (placed, {slot: "whole" | "footprints"}, held)."""
    placed, replaced, held = [], {}, []
    by_slot: dict[str, list[dict]] = {}
    for p in placements:
        if p.get("slot") is None:
            held.append(p | {"why": "no greybox slot"})
            continue
        by_slot.setdefault(p["slot"], []).append(p)
    for sid, ps in sorted(by_slot.items()):
        kit = [p for p in ps if p.get("kit")]             # a kit assembly: all its modules, or none
        if kit:
            bad = sorted({p["entry"] for p in kit if not ok(p["entry"])})
            if not bad:
                placed += kit
                replaced[sid] = "whole"
                held += [p | {"why": "a kit assembly stands in this slot", "superseded": True} for p in ps if not p.get("kit")]
                continue
            held += [p | {"why": f"not approved: {', '.join(bad)}"} for p in kit]
            ps = [p for p in ps if not p.get("kit")]
            if not ps:
                continue
        typed = ps[0].get("typed", False)
        layers = [p for p in ps if p.get("layer_of")]          # a stacked build's upper layers go with it
        ps = [p for p in ps if not p.get("layer_of")]
        good = [p for p in ps if ok(p["entry"])]
        if not typed:
            if good:
                mine = [q for q in layers if q["layer_of"] == good[0]["key"]]
                placed += good[:1] + mine           # one build per kit slot
                replaced[sid] = "whole"
                held += [p | {"why": "another build stands in this slot", "superseded": True} for p in ps + layers
                         if p is not good[0] and p not in mine]
            else:
                held += [p | {"why": "not approved"} for p in ps]
            continue
        need = planned.get(sid, 0)
        if need and len(good) >= need:
            placed += good
            replaced[sid] = "footprints"
        else:
            why = "not approved" if len(good) < len(ps) else f"{len(good)} of {need} buildings in the slot have a build"
            held += [p | {"why": why} for p in ps]
    return placed, replaced, held


def held_reasons(held: list[dict]) -> dict[str, int]:
    """{why: builds} of the builds held back (the build report's breakdown)."""
    out: dict[str, int] = {}
    for h in held:
        why = "not approved" if h["why"].startswith("not approved") else (
            "block not fully built" if "buildings in the slot have a build" in h["why"] else h["why"])
        out[why] = out.get(why, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def footprint_of(W: np.ndarray, lo, hi) -> dict:
    """A placed model's outline on the ground: {x, y (world centre), w, d, yaw} from its world
    matrix (a turn about Z, a scale per axis) and its own bounds (lo, hi; Blender frame)."""
    kx, ky = float(np.linalg.norm(W[:3, 0])), float(np.linalg.norm(W[:3, 1]))   # (kit bays scale along x)
    c = W @ np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2], 1.0])
    return {"x": float(c[0]), "y": float(c[1]), "w": float(hi[0] - lo[0]) * kx, "d": float(hi[1] - lo[1]) * ky,
            "yaw": math.degrees(math.atan2(W[1, 0], W[0, 0])), "base": float(c[2])}


def grounded(W: np.ndarray, lo, hi, ground, rule: str) -> np.ndarray:
    """W moved up or down so the model's base stands on the ground under its outline (rule:
    ground.RULES)."""
    f = footprint_of(W, lo, hi)
    out = np.array(W, float)
    out[2, 3] += ground.under(f["x"], f["y"], f["w"], f["d"], f["yaw"], rule) - f["base"]
    return out


def on_footprint(x: float, y: float, foot: dict) -> bool:
    a = math.radians(foot["yaw"])
    dx, dy = x - foot["x"], y - foot["y"]
    u, v = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
    return abs(u) <= foot["w"] / 2 + FOOT_MARGIN and abs(v) <= foot["d"] / 2 + FOOT_MARGIN


def kept_pieces(slot: dict, mode: str | None, feet: list[dict], drop=()) -> list[dict]:
    """The slot's greybox pieces that stay: all (not replaced), none ("whole"), those not
    standing on a placed footprint ("footprints"), and never the pieces in drop (props replaced
    by their builds, "pieces")."""
    if mode is None:
        return slot["pieces"]
    if mode == "whole":
        return []
    S = np.asarray(slot["matrix"], float)
    out = []
    for i, p in enumerate(slot["pieces"]):
        if i in drop:
            continue
        x, y = (S @ np.asarray(p["matrix_local"], float))[:2, 3]
        if not any(on_footprint(x, y, f) for f in feet):
            out.append(p)
    return out


def piece_pose(slot: dict, piece: dict) -> tuple[float, float, float, float]:
    """A greybox piece's world (x, y, base z, yaw deg): its origin is its base (the stand-in meshes
    have local z from 0)."""
    W = np.asarray(slot["matrix"], float) @ np.asarray(piece["matrix_local"], float)
    return float(W[0, 3]), float(W[1, 3]), float(W[2, 3]), math.degrees(math.atan2(W[1, 0], W[0, 0]))


def prop_groups(slot: dict, prop_pieces: dict[str, str], mains) -> list[tuple[int, str, list[int]]]:
    """The props in a slot: (main piece index, noun, [its other pieces]) per prop, each other
    piece (posts, tables, a hut's roof) given to the nearest main piece of the same noun."""
    S = np.asarray(slot["matrix"], float)
    pos = [(S @ np.asarray(p["matrix_local"], float))[:2, 3] for p in slot["pieces"]]
    groups = {i: (prop_pieces[p["piece"]], []) for i, p in enumerate(slot["pieces"]) if p["piece"] in mains}
    for i, p in enumerate(slot["pieces"]):
        noun = prop_pieces.get(p["piece"])
        if noun is None or i in groups:
            continue
        near = [(float(np.linalg.norm(pos[i] - pos[g])), g) for g, (n, _) in groups.items() if n == noun]
        if near:
            groups[min(near)[1]][1].append(i)
    return [(g, n, sub) for g, (n, sub) in sorted(groups.items())]


def world_matrix(p: dict) -> np.ndarray:
    """A placement's Blender world 4x4: an attached piece's own matrix, else (x, y, z, yaw)."""
    if "matrix" in p:
        return np.asarray(p["matrix"], float)
    a = math.radians(p["yaw"])
    B = np.eye(4)
    B[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    B[:3, 3] = [p["x"], p["y"], p["z"]]
    return B


def asset_id(key: str) -> str:
    return "lib_" + "".join(c if c.isalnum() or c == "_" else "_" for c in key)


def actor_rows(placed: list[dict], slots: dict[str, dict]) -> dict[str, list[dict]]:
    """{slot id: [{key, asset, entry, parent, relative, world}]}: UE transforms of the placed
    builds, relative to their slot's actor and in the world."""
    out: dict[str, list[dict]] = {}
    for p in placed:
        S = np.asarray(slots[p["slot"]]["matrix"], float)
        B = world_matrix(p)
        out.setdefault(p["slot"], []).append({
            "key": p["key"], "asset": asset_id(p.get("mesh") or p["key"]), "entry": p["entry"],
            "parent": p.get("parent"), "relative": ue_coords.to_ue(np.linalg.inv(S) @ B), "world": ue_coords.to_ue(B)}
            | ({"vrole": p["vrole"]} if p.get("vrole") else {}) | ({"on": p["base"]} if p.get("base") else {}))
    return out


TEX_KEYS = ("baseColorTexture", "metallicRoughnessTexture", "normalTexture", "occlusionTexture", "emissiveTexture")


def strip_textures(data: bytes) -> bytes:
    """A GLB without its embedded images: textures, images and samplers go, materials lose their
    texture slots (names and factors stay: the importer assigns its own instances by material
    name), and the binary chunk keeps only the buffer views something else still uses."""
    n = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + n])
    off = 20 + n
    binb = data[off + 8:off + 8 + struct.unpack_from("<I", data, off)[0]] if off < len(data) else b""
    drop = {im["bufferView"] for im in js.get("images", []) if "bufferView" in im}
    for k in ("images", "textures", "samplers"):
        js.pop(k, None)
    for m in js.get("materials", []):
        pbr = m.get("pbrMetallicRoughness") or {}
        for k in TEX_KEYS:
            pbr.pop(k, None)
            m.pop(k, None)
        m.get("extensions", {}).pop("KHR_materials_emissive_strength", None)
    used = [e for e in js.get("extensionsUsed", []) if e != "KHR_texture_transform"]
    if used:
        js["extensionsUsed"] = used
    else:
        js.pop("extensionsUsed", None)
    js.pop("extensionsRequired", None) if not js.get("extensionsRequired") else None
    views, remap, out = js.get("bufferViews", []), {}, bytearray()
    kept = []
    for i, v in enumerate(views):
        if i in drop:
            continue
        while len(out) % 4:
            out.append(0)
        start = v.get("byteOffset", 0)
        chunk = binb[start:start + v["byteLength"]]
        remap[i] = len(kept)
        kept.append(v | {"byteOffset": len(out)})
        out += chunk
    js["bufferViews"] = kept
    for a in js.get("accessors", []):
        if "bufferView" in a:
            a["bufferView"] = remap[a["bufferView"]]
        for part in ("indices", "values"):
            sp = (a.get("sparse") or {}).get(part)
            if sp:
                sp["bufferView"] = remap[sp["bufferView"]]
    while len(out) % 4:
        out.append(0)
    if js.get("buffers"):
        js["buffers"][0]["byteLength"] = len(out)
    jb = json.dumps(js, separators=(",", ":"), sort_keys=True).encode()
    jb += b" " * (-len(jb) % 4)
    total = 12 + 8 + len(jb) + (8 + len(out) if out else 0)
    glb = struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(jb), 0x4E4F534A) + jb
    if out:
        glb += struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    return glb


def _glb_parts(data: bytes) -> tuple[dict, bytes]:
    n = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + n])
    off = 20 + n
    return js, (data[off + 8:off + 8 + struct.unpack_from("<I", data, off)[0]] if off < len(data) else b"")


def baked_materials(data: bytes) -> list[dict]:
    """A GLB's own materials, in order (the order Unreal gives its material slots): which baked
    maps each has, its metallic and roughness as the importer sets them (glTF factor x the mean of
    the metallic-roughness map's blue / green channel), and its base colour map's mean sRGB colour
    (for the placement report's texture check)."""
    import io

    from PIL import Image
    js, binb = _glb_parts(data)

    def image(tex):
        if tex is None:
            return None
        src = js["textures"][tex["index"]].get("source")
        im = js["images"][src] if src is not None else None
        if not im or "bufferView" not in im:
            return None
        v = js["bufferViews"][im["bufferView"]]
        a = binb[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        return np.asarray(Image.open(io.BytesIO(a)).convert("RGB"), float) / 255
    out = []
    for m in js.get("materials", []):
        pbr = m.get("pbrMetallicRoughness") or {}
        base, mr = image(pbr.get("baseColorTexture")), image(pbr.get("metallicRoughnessTexture"))
        mf, rf = pbr.get("metallicFactor", 1.0), pbr.get("roughnessFactor", 1.0)
        out.append({"maps": sorted(k for k in ("baseColorTexture", "metallicRoughnessTexture") if k in pbr)
                    + ["normalTexture"] * ("normalTexture" in m),
                    "metallic": round(float(mf * (mr[..., 2].mean() if mr is not None else 1.0)), 3),
                    "roughness": round(float(rf), 3),            # the map's green channel carries the rest
                    "base_mean": [round(float(v), 3) for v in base.reshape(-1, 3).mean(0)] if base is not None
                    else [round(float(v), 3) for v in pbr.get("baseColorFactor", [1, 1, 1])[:3]]})
    return out


# --- from the project ------------------------------------------------------------------------

def count_planned(buildings, typed: list[dict]) -> dict[str, int]:
    """{typed slot id: distinct planned buildings in it}. buildings: (typology, x, y). Two at the
    same spot (rounded to the metre, as library_instances keys its builds) share one build, so
    they count once."""
    seen: dict[str, set] = {}
    for t, x, y in buildings:
        sid = slot_of_point(typed, t, x, y)
        if sid:
            seen.setdefault(sid, set()).add((t, round(x), round(y)))
    return {sid: len(v) for sid, v in seen.items()}


def city_plan(store: ProjectStore):
    from . import city
    from .stages import sw_site
    layout = sw_site.load_layout(store)
    return city.plan(layout, sw_site.plan_terrain(store, layout))


def planned_buildings(store: ProjectStore, gb: dict, P=None) -> dict[str, list[tuple]]:
    """{typed slot id: [(typology, x, y, base z, yaw) of each distinct planned building in it]}.
    P: the city plan (city_plan), when the caller has it."""
    from . import library_instances
    P = P or city_plan(store)
    typed = [s for s in gb["slots"] if s.get("typology")]
    out: dict[str, dict] = {}
    for b in P.typed:
        x, y, z, *_ = library_instances.footprint(b)
        sid = slot_of_point(typed, b.typology, x, y)
        if sid:
            out.setdefault(sid, {})[(b.typology, round(x), round(y))] = (b.typology, x, y, z, b.rot)
    return {sid: list(v.values()) for sid, v in out.items()}


def planned_per_slot(store: ProjectStore, gb: dict) -> dict[str, int]:
    """{typed slot id: how many distinct planned buildings stand in it}."""
    return {sid: len(v) for sid, v in planned_buildings(store, gb).items()}


def representative_placements(store: ProjectStore, gb: dict, cands: list[dict], buildings: dict) -> list[dict]:
    """Typed slots holding at most one building that no instance build covers (a forum portico, a
    market hall away from the shots): the entry's own build at that building's footprint (at the
    slot's pose when the plan has no building there), replacing the slot whole."""
    lib = store.root / "3d" / "library"
    covered = {p["slot"] for p in cands if p.get("slot")}
    slots = {s["id"]: s for s in gb["slots"]}
    out = []
    from .stages import sw_library
    for ent in sw_library.claims(store):
        e = ent["id"]
        glb, job = lib / e / "procedural.glb", lib / e / "procedural" / "job.json"
        brief = read_json(store.root / "library" / "kits" / f"{e}.json", default=None)
        if not (ent.get("slot_ids") and glb.exists() and job.exists() and brief):
            continue                                 # (a building or a kit piece such as a forum gate)
        for sid in ent["slot_ids"]:
            s = slots.get(sid)
            bs = buildings.get(sid, [])
            if s is None or not s.get("typology") or sid in covered or len(bs) > 1:
                continue
            if bs:
                _, x, y, z, yaw = bs[0]
            else:
                m = np.asarray(s["matrix"], float)
                x, y, z, yaw = float(m[0, 3]), float(m[1, 3]), float(m[2, 3]), math.degrees(math.atan2(m[1, 0], m[0, 0]))
            w, d, *_ = brief.get("dims") or ent.get("size_m") or [10.0, 10.0, 10.0]
            out.append({"key": f"{e}_{sid}".replace("-", "m"), "entry": e, "glb": glb.relative_to(store.root).as_posix(),
                        "x": float(x), "y": float(y), "z": float(z), "yaw": float(yaw), "w": w, "d": d, "slot": sid,
                        "typed": False, "representative": True})
    return out


def model_placements(store: ProjectStore, P, cands: list[dict]) -> list[dict]:
    """Typed buildings no instance build covers whose entry's Active version is a model (TRELLIS,
    no procedural build needed): a placement at the building's planned footprint and height
    ("planned": use_active_models stands the model there). Keyed as its instance build would be,
    so building instances later keeps the place. An entry that is procedural needs its instance
    builds (`ap library build-instances`). An entry with no model of its own takes its stand-in's
    (`ap library stand-in`: an excluded entry stays excluded and its buildings take another's model)."""
    from . import library_entries, library_instances
    from .stages import sw_library
    ents = set(library_entries._all_entries(store))
    stand = sw_library.stand_ins(store)
    have: dict[str, list] = {}
    for p in cands:
        have.setdefault(p.get("typology"), []).append((p["x"], p["y"]))
    models: dict[str, dict | None] = {}
    out, seen = [], set()
    for b in P.typed:
        e = library_instances.entry_of(b, ents)
        if e is None:
            continue
        x, y, z, w, d = library_instances.footprint(b)
        if any(math.hypot(x - u, y - v) < 1.0 for u, v in have.get(b.typology, [])):
            continue
        key = f"{e}_{round(x)}_{round(y)}".replace("-", "m")
        if e not in models:
            models[e] = active_model(store, e)
        if models[e] is None and stand.get(e):
            e = stand[e]
            if e not in models:
                models[e] = active_model(store, e)
        if models[e] is None or key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "entry": e, "typology": b.typology, "glb": models[e]["glb"], "x": float(x), "y": float(y),
                    "z": float(z), "yaw": float(b.rot), "w": float(w), "d": float(d), "h": float(b.h), "planned": True})
    return out


def model_slot_placements(store: ProjectStore, gb: dict, cands: list[dict], buildings: dict) -> list[dict]:
    """Slots an entry owns (library.json slot_ids, seen in a frame or not) whose Active version is a
    model (TRELLIS) and that has no procedural build: the model at the slot's pose and the entry's size
    ("planned", as model_placements; use_active_models fits it to the slot's masses when it has them).
    Typed slots holding planned buildings (buildings {slot: [...]}) and slots a placement already
    covers are left to their own paths; a typed slot with none (a forum rotunda) is a slot of its own."""
    from . import library_entries
    from .stages import sw_library
    lib = store.root / "3d" / "library"
    slots = {s["id"]: s for s in gb["slots"]}
    covered = {p["slot"] for p in cands if p.get("slot")}
    ents = library_entries._all_entries(store)
    out = []
    for ent in sw_library.claims(store):
        e = ent["id"]
        if (lib / e / "procedural.glb").exists() and (lib / e / "procedural" / "job.json").exists():
            continue
        mdl = None
        for sid in ent.get("slot_ids") or []:
            s = slots.get(sid)
            if s is None or buildings.get(sid) or sid in covered or s.get("category") == "terrain":
                continue
            mdl = mdl or active_model(store, e)
            if mdl is None:
                break
            brief = read_json(store.root / "library" / "kits" / f"{e}.json", default=None) or {}
            w, d, h = (brief.get("dims") or (ents.get(e) or {}).get("size_m") or [10.0, 10.0, 10.0])[:3]
            m = np.asarray(s["matrix"], float)
            out.append({"key": f"{e}_{sid}".replace("-", "m"), "entry": e, "glb": mdl["glb"], "slot": sid, "typed": False,
                        "x": float(m[0, 3]), "y": float(m[1, 3]), "z": float(m[2, 3]),
                        "yaw": math.degrees(math.atan2(m[1, 0], m[0, 0])), "w": float(w), "d": float(d), "h": float(h),
                        "planned": True})
            covered.add(sid)
    return out


PROP_MAINS = ("market:hut", "market:tent", "market:canopy", "market:stall")


def prop_placements(store: ProjectStore, gb: dict, assign: dict | None = None) -> tuple[list[dict], dict[str, set], list[str]]:
    """TRELLIS props on their greybox pieces: each main prop piece becomes its entry's Active model
    (footprint centred, base on the piece's base, fitted to the plan size: assembly.model) at the
    piece's yaw: its Clean up output for the asset's tier when one exists, else the raw model
    (next_build.prop_model; the mesh is prop_<entry>_<tier> when cleaned); it and its other pieces leave the greybox. assign: {original: {placement key:
    entry}} (variants.assignments): a placement given to a variant takes the variant's model.
    Returns (placements, {slot: piece indices they replace}, [prop entries with no model])."""
    from . import assembly, next_build
    from .stages.sw_library import PROP_PIECES, _slug
    out, drop, missing, models, tiers = [], {}, set(), {}, {}
    for s in gb["slots"]:
        for main, noun, sub in prop_groups(s, PROP_PIECES, PROP_MAINS):
            o = _slug(noun)
            key = f"{o}_{s['id']}_{main}".replace("-", "m")
            e = ((assign or {}).get(o) or {}).get(key, o)
            if e not in models:
                pm = next_build.prop_model(store, e)
                models[e] = assembly.model(store, e, glb=pm["glb"] if pm else None)
                tiers[e] = pm["cleaned"]["usage"] if pm and pm["cleaned"] else None
            mdl = models[e]
            if mdl["glb"] is None:
                missing.add(e)
                continue
            x, y, z, yaw = piece_pose(s, s["pieces"][main])
            W = assembly._t([x, y, z]) @ assembly._rz(yaw) @ assembly.piece_matrix(mdl)
            out.append({"key": key, "entry": e, "mesh": f"prop_{e}" + (f"_{tiers[e]}" if tiers[e] else ""),
                        "glb": Path(mdl["glb"]).relative_to(store.root).as_posix(), "matrix": W.tolist(),
                        "slot": s["id"], "prop": True, "pieces": [main, *sub]} | ({"variant_of": o} if e != o else {}))
    return out, drop, sorted(missing)


def apply_variants(store: ProjectStore, placements: list[dict], assign: dict, entries: dict) -> None:
    """Kit-slot placements (in place) given to a variant (variants.assignments) take the variant's
    procedural build at the same pose; a variant whose Active version isn't a procedural build
    leaves the placement to the original. entries gains the variants' job.json."""
    lib = store.root / "3d" / "library"
    for p in placements:
        if p.get("typology"):                        # a typed building's own instance build
            continue
        v = (assign.get(p["entry"]) or {}).get(p["key"])
        if not v or v == p["entry"]:
            continue
        glb, job = lib / v / "procedural.glb", lib / v / "procedural" / "job.json"
        brief = read_json(store.root / "library" / "kits" / f"{v}.json", default=None) or {}
        if not (glb.is_file() and job.is_file()):
            continue
        w, d, *_ = brief.get("dims") or [p["w"], p["d"]]
        p.update(variant_of=p["entry"], entry=v, glb=glb.relative_to(store.root).as_posix(), w=w, d=d)
        entries[v] = job.relative_to(store.root).as_posix()


def active_model(store: ProjectStore, entry: str) -> dict | None:
    """An entry's non-procedural Active version as building placements take it (next_build.building_model:
    {n, kind, glb, raw, cleaned}); None when it is procedural or has no model."""
    from . import next_build
    return next_build.building_model(store, entry)


def model_label(m: dict) -> str:
    """Which file an Active model places: "Cleaned (game, decimate) 30,000 faces" or "raw GLB"."""
    c = m.get("cleaned")
    if not c:
        return "raw GLB (Clean up output is optional and not chosen)"
    how = ", ".join(x for x in (c.get("usage"), c.get("method")) if x)
    return f"Cleaned ({how})" + (f" {c['faces']:,} faces" if c.get("faces") else "")


def use_active_models(store: ProjectStore, gb: dict, index: dict | None, placements: list[dict], bounds: dict) -> dict[str, dict]:
    """Placements (in place) of entries whose Active version isn't procedural take that model
    instead of their procedural build: one mesh per entry and version, scaled to the placement's
    footprint (a typed building's own; a slot's masses, else the build's) and height (the slot's
    masses, else the procedural build it replaces), centred on it. Kit modules and props are
    left alone (props already take the Active model). Returns {entry: {n, kind, placements}}."""
    from . import kit_assembly
    from .assembly import glb_bounds
    slots = {s["id"]: s for s in gb["slots"]}

    def bounds_of(rel):
        if rel not in bounds:
            bounds[rel] = [v.tolist() for v in glb_bounds(store.root / rel)]
        return bounds[rel]
    models: dict[str, dict | None] = {}
    out: dict[str, dict] = {}
    for p in placements:
        if p.get("kit") or p.get("prop") or p.get("layer_of") or p.get("unplaced"):
            continue
        e = p["entry"]
        if e not in models:
            models[e] = active_model(store, e)
        m = models[e]
        if m is None:
            continue
        B = world_matrix(p)
        mlo, mhi = bounds_of(m["glb"])
        if p.get("planned"):                            # no procedural build: the plan's footprint and height
            w, d, h, cx, cy, z0 = p["w"], p["d"], p["h"], 0.0, 0.0, 0.0
        else:
            plo, phi = bounds_of(p["glb"])
            w, d, h = phi[0] - plo[0], phi[1] - plo[1], phi[2] - plo[2]
            cx, cy, z0 = (plo[0] + phi[0]) / 2, (plo[1] + phi[1]) / 2, plo[2]
        if p.get("typed") or p.get("typology"):
            w, d = p.get("w") or w, p.get("d") or d
        elif slots.get(p.get("slot")) is not None:      # a slot's build: the slot's masses
            ms = [m_ for b in kit_assembly.outline(kit_assembly.slot_boxes(slots[p["slot"]], index)) for m_ in b["masses"]]
            if ms:
                from shapely.ops import unary_union
                inv = np.linalg.inv(B)
                pts = np.array([(inv @ np.array([x, y, 0.0, 1.0]))[:2] for P, *_ in ms for x, y in P.exterior.coords])
                (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
                w, d, cx, cy = x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2
                h = max(t for *_, t in ms) - min(b for _, b, _ in ms)
        ext = np.subtract(mhi, mlo)
        k = [w / max(ext[0], 1e-6), d / max(ext[1], 1e-6), h / max(ext[2], 1e-6)]
        mc = [(mlo[0] + mhi[0]) / 2, (mlo[1] + mhi[1]) / 2, mlo[2]]
        T = np.eye(4)
        T[:3, 3] = [cx, cy, z0]
        M = B @ T @ np.diag([*k, 1.0])
        M[:3, 3] -= M[:3, :3] @ np.asarray(mc)
        cl = m.get("cleaned")
        p.update(glb=m["glb"], mesh=f"active_{e}_v{m['n']}" + (f"_{cl['usage']}" if cl else ""), matrix=M.tolist(),
                 active=m["n"], active_kind=m["kind"], procedural=None if p.get("planned") else p["glb"])
        p.pop("vrole", None)
        r = out.setdefault(e, {"n": m["n"], "kind": m["kind"], "model": model_label(m), "placements": 0})
        r["placements"] += 1
    return out


def active_lines(active: dict[str, dict]) -> list[str]:
    """The build report's line per entry placed with its non-procedural Active version."""
    return [f"{e}: {r['placements']} placements use Active v{r['n']} {r['kind'].upper() if r['kind'] == 'trellis' else r['kind']}"
            + (f", {r['model']}" if r.get("model") else "") for e, r in sorted(active.items())]


def fit_heights(store: ProjectStore, gb: dict, index: dict | None, placements: list[dict], fitter, bounds: dict) -> list[dict]:
    """Single-building slot placements (not typed, kit or prop) at their slot's height: each takes
    its layers' first GLB and role, the layers above it follow as placements of their own
    (<key>_L<n>: layer_of, base, lift). Models with no building brief stay as they are."""
    from . import kit_assembly, vertical
    from .assembly import glb_bounds
    slots = {s["id"]: s for s in gb["slots"]}

    def bounds_of(rel):
        if rel not in bounds:
            bounds[rel] = [v.tolist() for v in glb_bounds(store.root / rel)]
        return bounds[rel]
    out = []
    for p in placements:
        s = slots.get(p.get("slot"))
        if (p.get("typed") or p.get("typology") or p.get("kit") or p.get("prop") or p.get("active") or p.get("unplaced")
                or s is None):
            out.append(p)
            continue
        ms = [m for b in kit_assembly.outline(kit_assembly.slot_boxes(s, index)) for m in b["masses"]]
        plan = fitter.layers(p["entry"], max(t for *_, t in ms) - min(b for _, b, _ in ms), "single") if ms else None
        if not plan:
            out.append(p)
            continue
        glbs = [fitter.glb(p["entry"], ly) for ly in plan]
        lifts = vertical.stack_lifts([bounds_of(g) for g in glbs])
        for c, (g, ly, lift) in enumerate(zip(glbs, plan, lifts)):
            q = p | {"glb": g, "vrole": ly["role"]}
            if c:
                q |= {"key": f"{p['key']}_L{c}", "layer_of": p["key"], "base": p["key"], "lift": round(lift, 4)}
            out.append(q)
    return out


def ok_entries(store: ProjectStore, use_active: bool):
    """ok(entry) for `choose`: Approved in library review, or (use_active) any built entry (next_build.gate)."""
    from . import next_build
    return next_build.gate(store, use_active)


def settings(store: ProjectStore):
    from .schema import UnrealSettings
    return store.load().unreal or UnrealSettings()


def ground_all(store: ProjectStore, placements: list[dict], ground, rule: str, bounds: dict) -> None:
    """Every placement on the ground under its model's outline (in place): its z (or matrix) and
    its outline ("foot"). A stacked kit module (base, lift) stands `lift` m above its base's
    grounded place instead."""
    from .assembly import glb_bounds
    by_key = {p["key"]: p for p in placements}
    for p in sorted(placements, key=lambda p: "base" in p):     # bases first
        if p["glb"] not in bounds:
            bounds[p["glb"]] = [v.tolist() for v in glb_bounds(store.root / p["glb"])]
        lo, hi = bounds[p["glb"]]
        if "base" in p:
            W = world_matrix(p)
            W[2, 3] = world_matrix(by_key[p["base"]])[2, 3] + p["lift"]
        else:
            W = grounded(world_matrix(p), lo, hi, ground, rule)
        if "matrix" in p:
            p["matrix"] = W.tolist()
        else:
            p["z"] = float(W[2, 3])
        p["foot"] = footprint_of(W, lo, hi)


def base_placements(store: ProjectStore, gb: dict) -> tuple[list[dict], dict, dict, dict]:
    """The plan's own building placements, before models and heights: typed buildings' instance
    builds, slots' single builds and representative builds, variants applied. (placements,
    entries {entry: job.json}, planned buildings {typed slot: [...]}, variants.assignments)."""
    from .look import _candidates
    cands, _, entries = _candidates(store, None, math.inf)
    P = city_plan(store)
    cands += model_placements(store, P, cands)
    typed = [s for s in gb["slots"] if s.get("typology")]
    for p in cands:
        if p.get("slot") is None:
            p["typed"] = True
            p["slot"] = slot_of_point(typed, p.get("typology"), p["x"], p["y"])
    buildings = planned_buildings(store, gb, P)
    cands += model_slot_placements(store, gb, cands, buildings)
    reps = representative_placements(store, gb, cands, buildings)
    for p in reps:
        entries[p["entry"]] = (Path("3d/library") / p["entry"] / "procedural" / "job.json").as_posix()
    from . import variants
    assign = variants.assignments(store, gb)        # duplicates used as variants share the placements
    apply_variants(store, cands + reps, assign, entries)
    return cands + reps, entries, buildings, assign


def plan(store: ProjectStore, gb: dict, use_active: bool = False, index: dict | None = None, fitter=None,
         log=print, unplaced: dict | None = None) -> dict:
    """{placed (with attached pieces), replaced, held, entries (materials), missing_children,
    bounds {glb: [lo, hi]}, kits (kit_assembly reports), height_variants (built now) and
    height_variant_s (their Blender seconds), unplaced (unplaced.plan's picks or None)}. Everything
    placed stands on the ground (ground.Ground, the project's ground rule) or on the layer under
    it. index: the mesh export index (the kit slots' footprint outlines and heights; without it,
    their bboxes); fitter: vertical.Fitter (height variants, built in Blender when missing);
    unplaced: {avoid_locked, seed} to use unplaced assets (unplaced.py), None for the plan's own."""
    from . import assembly
    from .ground import Ground
    base, entries, buildings, assign = base_placements(store, gb)
    from . import kit_assembly, vertical
    fitter = fitter or vertical.Fitter(store, log=log)
    bounds: dict = fitter.bounds                     # (a dry fitter's variants: bounds from their parts)
    ok = ok_entries(store, use_active)
    extra, picks = [], None
    if unplaced is not None:                         # before models and heights: a swapped place keeps its own
        from . import unplaced as up
        picks = up.plan(store, gb, base, ok, index, unplaced.get("avoid_locked", True), unplaced.get("seed", up.SEED), bounds,
                        planned={sid: len(v) for sid, v in buildings.items()})
        extra = up.apply(store, gb, picks, base, entries, bounds)
    active = use_active_models(store, gb, index, base, bounds)
    singles = fit_heights(store, gb, index, base, fitter, bounds)    # after the split: it keeps its keys
    kit_pl, kit_reps = kit_assembly.placements(store, gb, index, bounds, fitter)
    for p in kit_pl:
        entries[p["entry"]] = (Path("3d/library") / p["entry"] / "procedural" / "job.json").as_posix()
    placed, replaced, held = choose(singles + kit_pl, {sid: len(v) for sid, v in buildings.items()}, ok)
    ground, rule = Ground.from_project(store), settings(store).ground_rule
    ground_all(store, placed, ground, rule, bounds)          # parents first: their pieces follow them
    kids, kid_entries, missing = assembly.child_placements(store, placed)
    by_key = {p["key"]: p for p in placed}
    kids = [k | {"slot": by_key[k["parent"]]["slot"]} for k in kids]
    props, _, no_model = prop_placements(store, gb, assign)
    ground_all(store, kids + props + extra, ground, rule, bounds)
    drop: dict[str, set] = {}
    for p in extra + props:                       # one model per prop entry: all its pieces, or none
        if not ok(p["entry"]) or replaced.get(p["slot"]) == "whole":
            held.append(p | ({"why": "not approved"} if not ok(p["entry"]) else {"why": "its slot is replaced whole", "superseded": True}))
            continue
        placed.append(p)
        drop.setdefault(p["slot"], set()).update(p["pieces"])
        replaced.setdefault(p["slot"], "pieces")
    used = {p["entry"] for p in placed + kids if not p.get("active")}     # (an Active model keeps its own maps)
    entries = {e: j for e, j in {**kid_entries, **entries}.items() if e in used}
    active = {e: r | {"placements": sum(1 for p in placed if p["entry"] == e and p.get("active"))} for e, r in active.items()}
    active = {e: r for e, r in active.items() if r["placements"]}
    if picks is not None:                         # what actually stands: a held slot's swap doesn't count
        stand = {p["key"] for p in placed}
        picks["placed"] = sum(1 for p in placed if p.get("unplaced"))
        picks["held"] = sum(1 for v in picks["picks"].values() for c in v
                            if (c.get("key") or "") not in stand and c["kind"] == "swap")
    parents = [p for p in placed if not p.get("prop")]
    return {"placed": placed + kids, "parents": len(parents), "props": len(placed) - len(parents),
            "replaced": replaced, "held": held, "entries": entries, "missing_children": missing,
            "drop_pieces": drop, "no_model": no_model, "bounds": bounds, "ground_rule": rule, "kits": kit_reps,
            "height_variants": fitter.built, "height_variant_s": round(fitter.build_s, 1), "active": active,
            "unplaced": picks}


OUTER_RING = "/Game/AP/Terrain/SM_OuterRing"            # ap terrain ue (terrain_build.ue_assets)
TERRAIN_MI = "/Game/AP/Terrain/MI_AP_TerrainAuto"


def terrain_section(gb: dict) -> dict | None:
    """The importer's terrain step: the outer ring's greybox slot (it goes when SM_OuterRing is in
    the project) and the terrain slots that take the auto material."""
    ids = [s["id"] for s in gb["slots"] if s["type"] == "terrain"]
    if not ids:
        return None
    outer = next((i for i in ids if i.endswith("outer")), None)
    return {"outer_slot": outer, "material_slots": [i for i in ids if i != outer],
            "outer_ring": OUTER_RING, "auto_material": TERRAIN_MI}


def add_to_manifest(store: ProjectStore, m: dict, gb: dict, index: dict, out: Path, use_active: bool = False,
                    log=print, unplaced: dict | None = None) -> dict:
    """Put the library layer into a stand-in manifest (in place): the placed builds' GLBs under
    export/library/, their assets, each replaced slot's remaining greybox and its builds."""
    from . import kit_assembly, ue
    from .stages.s7_export import manifest_components
    pl = plan(store, gb, use_active, index, log=log, unplaced=unplaced)
    slots = {s["id"]: s for s in gb["slots"]}
    feet: dict[str, list[dict]] = {}
    for p in pl["placed"]:
        if p.get("typed"):                           # typed builds clear the pieces under their real outline
            feet.setdefault(p["slot"], []).append(p["foot"])
    rows = actor_rows(pl["placed"], slots)
    lib = out / LIB_DIR
    shutil.rmtree(lib, ignore_errors=True)
    lib.mkdir(parents=True)
    own = {}                                     # entries whose every material the importer rebuilds
    for e, j in sorted(pl["entries"].items()):
        spec = ue.library_slots(store.root / j)
        if spec and set(spec) >= set((read_json(store.root / j, default={}) or {}).get("materials", {})):
            own[e] = spec
    assets = {}
    for p in pl["placed"]:
        aid = asset_id(p.get("mesh") or p["key"])
        if aid in assets:
            continue
        dst = lib / f"{aid}.glb"
        src = store.root / p["glb"]
        raw = src.read_bytes()
        # the importer gives these the entry's materials, so their maps go (Unreal would import
        # every one); TRELLIS builds and painted kit pieces keep their GLB's own materials
        dst.write_bytes(strip_textures(raw) if p["entry"] in own and not p.get("active") else raw)
        assets[aid] = {"id": aid, "kind": "library", "entry": p["entry"], "glb": f"{LIB_DIR}/{aid}.glb",
                       "source": p["glb"], "bounds": pl["bounds"][p["glb"]],
                       "stripped_kb": round((len(raw) - dst.stat().st_size) / 1024),
                       "generated": bool(p.get("active") or p.get("prop"))}    # a TRELLIS model: its normals matter most
        if p["entry"] not in own or p.get("active"):   # its own baked maps go into its material instances
            assets[aid]["baked"] = baked_materials(raw)
    for s in m["slots"]:
        mode = pl["replaced"].get(s["id"])
        if mode is None:
            continue
        s["components"] = manifest_components(kept_pieces(slots[s["id"]], mode, feet.get(s["id"], []),
                                                          pl["drop_pieces"].get(s["id"], ())), index)
        s["library"] = rows.get(s["id"], [])
        s["replaced"] = mode
    m["assets"] = [a for a in m["assets"] if any(a["id"] == c["asset"] for s in m["slots"] for c in s["components"])]
    m["library_assets"] = sorted(assets.values(), key=lambda a: a["id"])
    m["library_entries"] = own
    over = read_json(store.root / MATERIAL_OVERRIDES, default={}) or {}
    m["material_overrides"] = {e: v for e, v in over.items() if not e.startswith("_") and e in {a["entry"] for a in assets.values()}}
    m["source"] = "library+greybox"
    m["terrain"] = terrain_section(gb)
    held = [h for h in pl["held"] if not h.get("superseded")]     # superseded: a kit or another build stands in its slot
    held_entries = sorted({h["entry"] for h in held})
    m["library"] = {"use_active": use_active, "ground_rule": pl["ground_rule"], "placed": len(pl["placed"]), "parents": pl["parents"],
                    "props": pl["props"], "no_model": pl["no_model"],
                    "slots_replaced": len(pl["replaced"]), "held": len(held), "held_entries": held_entries,
                    "superseded": len(pl["held"]) - len(held), "held_why": held_reasons(held),
                    "missing_children": pl["missing_children"],
                    "kit_modules": sum(1 for p in pl["placed"] if p.get("kit")),
                    "kit_notes": kit_assembly.report_lines(pl["kits"]),
                    "stacked_layers": sum(1 for p in pl["placed"] if p.get("vrole") in ("middle", "top")),
                    "height_variants_built": pl["height_variants"], "height_variant_s": pl["height_variant_s"],
                    "active_models": active_lines(pl["active"])}
    if pl["unplaced"] is not None:
        from . import unplaced as up
        m["library"]["unplaced"] = up.summary(pl["unplaced"]) | {"placed": pl["unplaced"]["placed"],
                                                                  "held": pl["unplaced"]["held"]} | unplaced
    for line in m["library"]["kit_notes"] + m["library"]["active_models"] + ((m["library"].get("unplaced") or {}).get("lines") or []):
        log(line)
    log(f"library: {pl['parents']} builds + {pl['props']} props + {len(pl['placed']) - pl['parents'] - pl['props']} attached pieces in "
        f"{len(pl['replaced'])} slots; {len(held)} held back as greybox"
        + (f" ({m['library']['superseded']} more not needed: a kit or another build stands in their slot)" if m["library"]["superseded"] else ""))
    return m["library"]

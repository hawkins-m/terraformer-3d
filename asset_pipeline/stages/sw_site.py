"""World mode, site: layout.json -> terrain heightmap -> greybox.blend -> greybox.json.

The layout generates the greybox; after that the .blend is the truth (hand edits in
Blender) until the UE import, after which the UE level is (PLAN.md "World mode").
greybox.json is always read back from the .blend, never from the layout.

Layout under the project:
    site/layout.json             SiteLayout (edit, then `ap site build`)
    site/terrain.png + .json     16-bit heightmap (rows north -> south) and its z range / size
    site/greybox.blend           built, then hand-edited (.prev.blend: the one before a rebuild)
    site/greybox.json            slots + shots + untagged meshes, read from the .blend
    site/build.json              sha256 of the .blend as last built or written by the pipeline
    site/build/                  spec.json + terrain.npy handed to Blender, blender.log,
                                 built_slots.json (every slot as built: what hand edits are measured from)
    site/city.json + city_plan.png   city mode: the plan's stats and a top-down plan
"""
import hashlib
import json
import math
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

from .. import blender, city, config, greybox
from ..project import ProjectStore, TruncatedImage, load_image, read_json, save_image, warn, write_json
from ..schema import (CitySpec, CivicNode, District, Kit, Plaza, Plot, Radial, Ring, RingRow, SiteLayout,
                      Shot, ShotSpec, Slot, TerrainSpec)

SITE = "site"
SCRIPT = config.REPO_ROOT / "scripts" / "blender_greybox.py"
BLENDER_TERRAIN_MAX = 337   # samples per side of the greybox terrain mesh (~7 m on 2.4 km)
BLENDER_TERRAIN_STEP_M = 8.0  # finer than this isn't needed; larger sites get more samples
BLENDER_TERRAIN_CAP = 1009
MOVE_M, TURN_DEG, SIZE_TOL = 0.05, 0.1, 0.01   # a slot edit smaller than this is no edit
MERGED_KINDS = {"ribbon", "poly"}  # pieces Blender merges into one mesh per slot and piece (blender_greybox MERGED)


class SiteEdited(ValueError):
    """greybox.blend was edited by hand; rebuilding from the layout would replace it."""


def site_dir(store: ProjectStore) -> Path:
    return store.root / SITE


def blend_path(store: ProjectStore) -> Path:
    return site_dir(store) / "greybox.blend"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_layout(store: ProjectStore) -> SiteLayout:
    data = read_json(site_dir(store) / "layout.json")
    if data is None:
        raise FileNotFoundError(f"no site/layout.json in {store.root} (run `ap site init`)")
    return SiteLayout.model_validate(data)


def save_layout(store: ProjectStore, layout: SiteLayout) -> None:
    write_json(site_dir(store) / "layout.json", layout.model_dump(mode="json"))


def starter_layout() -> SiteLayout:
    """A small neutral layout to start from (one of each element; no project content).
    Real layouts live in the project folder, never in this repo."""
    return SiteLayout(
        name="starter",
        districts=[District(id="centre", radius=(0, 80)), District(id="outer", radius=(80, 1e9))],
        kits=[Kit(id="colonnade", module_m=4, storey_m=6, column_d_m=0.9, ring_radii=[22, 110])],
        rings=[Ring(id="inner", radius=60, width=12), Ring(id="green", radius=125, width=20, role="garden")],
        radials=[Radial(id=f"r{a:03d}", angle=a, width=16, r_from=45, r_to=400) for a in (0, 90, 180, 270)],
        plazas=[Plaza(id="centre", radius=45)],
        plots=[Plot(id="rotunda", type="rotunda", size=(36, 36, 36), kit="colonnade"),
               Plot(id="stoa-a", type="stoa", arc=(110, 20, 70), kit="colonnade")],
        rows=[RingRow(id="block", radius=180, count=8, angle_offset=45, type="block", size=(30, 20, 16),
                      kit="colonnade"),
              RingRow(id="villa", radius=280, count=16, angle_offset=11.25, type="villa", size=(34, 34, 8),
                      kit="colonnade")],
        shots=[ShotSpec(id="wide", tier="wide", pos=(-600, -500, 300), look_at=(0, 0, 0), lens_mm=28),
               ShotSpec(id="street", pos=(0, -52, 19.7), look_at=(0, 0, 28), lens_mm=24)])


def starter_city() -> SiteLayout:
    """A neutral city-mode layout (no project content): a coast, four civic nodes of
    different sizes, one monument."""
    return SiteLayout(
        name="starter city",
        terrain=TerrainSpec(extent_m=8000, resolution=2017, shore_m=1400, headland_m=150, headland_width_m=900,
                            hill_height_m=180, hill_from_m=300, hill_to_m=3200, coast_amp_m=160,
                            coast_wavelength_m=2600, city_radius_m=0, z_range=(-60, 400)),
        kits=[Kit(id="civic", module_m=4, storey_m=6, column_d_m=0.9)],
        city=CitySpec(nodes=[
            CivicNode(id="centre", center=(0, -600), radius=300, role="forum", monument="rotunda", radials=8,
                      spiral_deg_per_100m=4, weight=1.3, avenue_m=1800),
            CivicNode(id="harbour", center=(-1500, -1120), radius=170, role="harbour", radials=5, rot=20),
            CivicNode(id="market", center=(1500, -500), radius=150, role="market", radials=6, rot=10, weight=0.8),
            CivicNode(id="hill", center=(-600, 750), radius=200, role="hill", radials=6, weight=0.9)],
            bounds=(-2500, -1500, 2500, 1500)))


def city_shots(layout: SiteLayout, store: ProjectStore | None = None) -> list[ShotSpec]:
    """Auto-placed city-mode shots for a layout, on the ground the build uses: the generated
    terrain, or the project's heightmap (`store` needed to read it), with the node pads."""
    if layout.terrain.heightmap:
        if store is None:
            raise ValueError("city_shots: a heightmap terrain is read from the project (give the store)")
        return city.shots(layout, terrain(store, layout, write=False))
    return city.shots(layout, city.apply_pads(layout, greybox.make_terrain(layout.terrain)))


def plan_city(store: ProjectStore) -> dict:
    """City mode without Blender: site/city_plan.png (top-down plan) and site/city.json
    (area shares, counts, warnings). Returns the stats."""
    layout = load_layout(store)
    if layout.city is None:
        raise ValueError("the layout has no city spec (ap site init --city, or add \"city\" to layout.json)")
    h = plan_terrain(store, layout)
    P = city.plan(layout, h)
    save_image(city.plan_image(P, h), site_dir(store) / "city_plan.png")
    write_json(site_dir(store) / "city.json", P.stats)
    from .. import city_view
    city_view.scene(store, P, h)          # the Layout tab's 3D viewer (site/city3d.json)
    return P.stats


USER_SHOT_FIELDS = ("prompt_append", "prompt_override", "nudge_pos", "nudge_target", "lens_override",
                    "tier_override", "refs", "ref_strength", "locked")


def set_city_shots(store: ProjectStore, only: list[str] | None = None) -> list[ShotSpec]:
    """Replace the layout's shots with the auto-placed city shots (rebuild to apply), or
    only the shots in `only` (the others keep their cameras). The user's edits to a shot
    (prompt, references, nudges, lens, tier) carry over by id; a locked shot keeps its
    camera (its frame is done)."""
    layout = load_layout(store)
    old = {s.id: s for s in layout.shots}
    shots = city_shots(layout, store)
    auto = {s.id for s in shots}
    shots = [old[s.id] if s.id in old and old[s.id].locked else s for s in shots]
    shots += [s for s in layout.shots if s.locked and s.id not in auto]
    for s in shots:
        if s.id in old and not s.locked:
            for k in USER_SHOT_FIELDS:
                setattr(s, k, getattr(old[s.id], k))
    if only is not None:
        new = {s.id: s for s in shots if s.id in only}
        shots = [new.pop(s.id, s) for s in layout.shots] + list(new.values())
    layout.shots = shots
    save_layout(store, layout)
    return layout.shots


def import_catalog(store: ProjectStore, path: Path) -> SiteLayout:
    """Merge a typology catalog YAML (typologies, districts, materials, overlays, checks)
    into site/layout.json."""
    from .. import catalog
    layout = catalog.apply(load_layout(store), catalog.load(path))
    save_layout(store, layout)
    return layout


def export_catalog(store: ProjectStore) -> str:
    from .. import catalog
    return catalog.dump(load_layout(store))


def init(store: ProjectStore, layout_file: Path | None = None, force: bool = False,
         city_mode: bool = False) -> SiteLayout:
    """Copy a layout (default: the neutral starter, or with city_mode the starter city) into
    the project's site/ and switch the project to world mode. A city layout without shots
    gets the auto-placed ones."""
    dst = site_dir(store) / "layout.json"
    if dst.exists() and not force:
        raise FileExistsError(f"{dst} exists (use force to replace it)")
    layout = (SiteLayout.model_validate(json.loads(Path(layout_file).read_text())) if layout_file
              else starter_city() if city_mode else starter_layout())
    if city_mode and not layout_file:        # a new city starts on the new-project preset (existing ones keep theirs)
        from .. import layout_presets
        layout.city.preset = layout_presets.get(layout_presets.NEW_PROJECT)
    if layout.city is not None and not layout.shots:
        layout.shots = city_shots(layout, store)
    save_layout(store, layout)
    project = store.load()
    project.mode = "world"
    store.save(project)
    return layout


def edited(store: ProjectStore) -> bool:
    """Has greybox.blend changed since the pipeline last wrote it?"""
    b = blend_path(store)
    rec = read_json(site_dir(store) / "build.json", default={}) or {}
    return b.is_file() and rec.get("sha256") != _sha(b)


def _record(store: ProjectStore, **extra) -> None:
    rec = read_json(site_dir(store) / "build.json", default={}) or {}
    rec.update(extra, sha256=_sha(blend_path(store)))
    write_json(site_dir(store) / "build.json", rec)


# --- terrain -----------------------------------------------------------------------------

def plan_terrain(store: ProjectStore, layout: SiteLayout) -> np.ndarray:
    """The heights the city is planned on. With a built far terrain (terrain.far), the tile as it
    was generated, without the far build: the plan reads the heightfield everywhere and
    reshuffles on any change (even the 16-bit rounding of a re-saved heightmap moved 2,307 of
    2,317 buildings), so it keeps its own terrain; the greybox meshes and piece heights use the
    real one, which matches it inside the walls."""
    if not layout.terrain.far:
        return terrain(store, layout)
    plain = layout.model_copy(deep=True)
    plain.terrain.heightmap, plain.terrain.far = None, None
    return terrain(store, plain, write=False)


def plan_ground(store: ProjectStore, layout: SiteLayout) -> np.ndarray:
    """The ground the plan is made on, before the node pads and the preset's own ground: what
    a roll places a city's districts on (plan_terrain without the pads)."""
    t = layout.terrain
    if t.far or not t.heightmap:
        return greybox.make_terrain(t.model_copy(update={"heightmap": None, "far": None}))
    a = np.array(Image.open(store.root / t.heightmap))
    return greybox.from_png16(a, t.z_range)


def terrain(store: ProjectStore, layout: SiteLayout, write: bool = True) -> np.ndarray:
    """Generate (or load) the heightmap, write site/terrain.png + .json (write); returns metres."""
    t = layout.terrain
    out = site_dir(store)
    if t.heightmap:
        a = np.array(Image.open(store.root / t.heightmap))
        if a.ndim != 2 or a.shape[0] != a.shape[1]:
            raise ValueError(f"{t.heightmap}: expected a square single-channel 16-bit PNG")
        h = greybox.from_png16(a, t.z_range)
    else:
        h = greybox.make_terrain(t)
    if layout.city is not None:
        h = city.apply_pads(layout, h)
    lo, hi = float(h.min()), float(h.max())
    if lo < t.z_range[0] or hi > t.z_range[1]:
        raise ValueError(f"terrain spans {lo:.1f}..{hi:.1f} m, outside z_range {t.z_range}: widen it")
    if write:
        save_image(Image.fromarray(greybox.to_png16(h, t.z_range)), out / "terrain.png", unchanged_ok=True)   # polls re-plan: no rewrite
        write_json(out / "terrain.json", {"extent_m": t.extent_m, "resolution": int(h.shape[0]),
                                          "z_range": list(t.z_range), "min_z": round(lo, 3), "max_z": round(hi, 3),
                                          "rows": "north (+Y) -> south"})
    return h


def site_heights(store: ProjectStore, z_range=None) -> np.ndarray | None:
    """site/terrain.png in metres (z_range: terrain.json's; None when there's no terrain.png). A file
    that stays truncated or unreadable (load_image's retries) is made again from the layout, with a
    warning (project.warn) instead of an error: the same heights terrain() last wrote."""
    png = site_dir(store) / "terrain.png"
    if not png.is_file():
        return None
    if z_range is None:
        z_range = (read_json(site_dir(store) / "terrain.json", default=None) or {}).get("z_range") or load_layout(store).terrain.z_range
    try:
        return greybox.from_png16(np.array(load_image(png)), tuple(z_range))
    except TruncatedImage as e:
        warn(f"{png.name} unreadable ({str(e).split(': ', 1)[-1]}): made it again from the layout")
        return terrain(store, load_layout(store))


def _downsample(h: np.ndarray, max_n: int) -> np.ndarray:
    step = max(1, math.ceil((h.shape[0] - 1) / (max_n - 1)))
    return h[::step, ::step]


# --- build / extract ---------------------------------------------------------------------

def build(store: ProjectStore, force: bool = False) -> dict:
    """layout.json -> greybox.blend -> greybox.json. Refuses to replace a hand-edited
    .blend unless force (the old one is kept as greybox.prev.blend either way)."""
    layout = load_layout(store)
    b = blend_path(store)
    if edited(store) and not force:
        raise SiteEdited(f"{b} was edited since it was built; rebuilding replaces those edits "
                         f"(use force; the old file is kept as greybox.prev.blend)")
    t = time.time()
    h = terrain(store, layout)
    hp = plan_terrain(store, layout) if layout.terrain.far else h
    spec = greybox.build_spec(layout, h, plan_heights=hp)
    work = site_dir(store) / "build"
    work.mkdir(parents=True, exist_ok=True)
    small = _downsample(h, max(BLENDER_TERRAIN_MAX, min(BLENDER_TERRAIN_CAP,
                                                        math.ceil(layout.terrain.extent_m / BLENDER_TERRAIN_STEP_M) + 1)))
    np.save(work / "terrain.npy", small)
    spec["terrain"]["npy"] = str(work / "terrain.npy")
    if layout.terrain.far:                    # the terrain beyond the tile (terrain_build): far ring + outer ring
        fd = store.root / layout.terrain.far
        spec["terrain"]["far"] = [str(fd / n) for n in ("far.npz", "outer.npz") if (fd / n).is_file()]
    spec["terrain"]["resolution"] = int(small.shape[0])
    if spec.get("city"):
        write_json(site_dir(store) / "city.json", spec["city"])
        save_image(city.plan_image(city.plan(layout, hp), hp), site_dir(store) / "city_plan.png")
    write_json(work / "spec.json", spec)
    if b.exists():
        shutil.copyfile(b, b.with_suffix(".prev.blend"))
    tmp = b.with_name(".greybox.tmp.blend")
    tmp.unlink(missing_ok=True)
    blender.run(SCRIPT, ["--build", work / "spec.json", "--out", tmp], work / "blender.log", "greybox build")
    if not tmp.is_file():
        raise RuntimeError(f"blender wrote no greybox (see {work / 'blender.log'})")
    tmp.replace(b)
    _record(store, built=time.strftime("%Y-%m-%dT%H:%M:%S"))
    data = extract(store, rebase=True)
    store.log_run({"stage": "site.build", "slots": len(data["slots"]), "shots": len(data["shots"]),
                   "pieces": sum(len(s["pieces"]) for s in data["slots"]), "seconds": round(time.time() - t, 1)})
    return data


def extract(store: ProjectStore, rebase: bool = False) -> dict:
    """Read the tagged objects of greybox.blend into site/greybox.json, with its slot_edits
    against the greybox as built (slot_edits). rebase: this is the greybox as built (build); an
    unedited one with no built_slots.json yet is too."""
    b = blend_path(store)
    if not b.is_file():
        raise FileNotFoundError(f"no {b} (run `ap site build`)")
    was_edited = edited(store)
    work = site_dir(store) / "build"
    out = work / "extract.json"
    out.unlink(missing_ok=True)
    blender.run(SCRIPT, ["--extract", out], work / "blender.log", "greybox extract", blend=b)
    if not out.is_file():
        raise RuntimeError(f"blender wrote no extract (see {work / 'blender.log'})")
    data = json.loads(out.read_text())
    if data["fixed"] and not was_edited:
        _record(store)  # the pipeline's own fixes don't count as hand edits
    sources = {s["id"]: s.pop("source") for s in data["slots"] if s.get("source")}
    data["slots"] = [Slot.model_validate(s).model_dump(mode="json") for s in data["slots"]]
    data["shots"] = [Shot.model_validate(s).model_dump(mode="json") for s in data["shots"]]
    if rebase or not (was_edited or (work / "built_slots.json").is_file()):   # (as built: one built before the file)
        write_json(work / "built_slots.json", {"slots": {s["id"]: _as_built(s) for s in data["slots"]}})
    built = built_slots(store)
    data["slot_edits"] = slot_edits(built, data["slots"], sources) if built is not None else None
    data["blend_sha256"] = _sha(b)
    data["geometry_sha256"] = geometry_sha(data)
    write_json(site_dir(store) / "greybox.json", data)
    return data


def material_of(slot: dict, materials: list):
    """The layout material a greybox slot uses: its own tag (city typologies, landmark
    ensembles), else the first material listing its type (and district, if limited)."""
    if slot.get("material"):
        return next((m for m in materials if m.id == slot["material"]), None)
    return next((m for m in materials if slot["type"] in m.types
                 and (not m.districts or slot.get("district") in m.districts)), None)


def geometry_sha(data: dict) -> str:
    """Hash of the slots alone (not the cameras): what every shot's passes depend on."""
    return hashlib.sha256(json.dumps(data["slots"], sort_keys=True).encode()).hexdigest()


# --- hand edits: every slot against the greybox as built ---------------------------------------

def _as_built(slot: dict) -> dict:
    return {"typology": slot.get("typology"), "matrix": slot["matrix"], "bbox": slot["bbox"],
            "pieces": {p["name"]: p["matrix_local"] for p in slot["pieces"]}}


def _pose(loc, rot_z: float, scale=None) -> list[list[float]]:
    a = math.radians(rot_z)
    M = np.eye(4)
    M[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    M[:3, :3] = M[:3, :3] * np.asarray(scale or (1, 1, 1), float)
    M[:3, 3] = loc
    return M.tolist()


def built_slots(store: ProjectStore) -> dict | None:
    """{slot id: {typology, matrix, bbox (None when unknown), pieces {name: matrix_local}}}: the
    greybox as last built (site/build/built_slots.json); for one built before that file, the
    spec Blender built it from. None: never built."""
    work = site_dir(store) / "build"
    data = read_json(work / "built_slots.json", default=None)
    if data is not None:
        return data["slots"]
    spec = read_json(work / "spec.json", default=None)
    if spec is None:
        return None
    return {s["id"]: {"typology": s.get("typology"), "matrix": _pose(s["loc"], s["rot_z"]), "bbox": None,
                      "pieces": {(f"{s['id']}.{p['piece']}" if p["prim"]["kind"] in MERGED_KINDS else p["name"]):
                                 np.eye(4).tolist() if p["prim"]["kind"] in MERGED_KINDS
                                 else _pose(p["loc"], p["rot_z"], p.get("scale")) for p in s["pieces"]}}
            for s in spec["slots"]}


def _piece_move(built: dict, slot: dict) -> np.ndarray:
    """The move all of a slot's pieces share in its own frame (its child meshes moved together in
    Blender, the slot's empty left where it was); identity when they don't all share one."""
    rel = []
    for p in slot["pieces"]:
        b = built["pieces"].get(p["name"]) or built["pieces"].get(re.sub(r"\.\d{3}$", "", p["name"]))
        if b is not None:
            rel.append(np.asarray(p["matrix_local"], float) @ np.linalg.inv(np.asarray(b, float)))
    if rel and all(np.allclose(r, rel[0], atol=1e-3) for r in rel):
        return rel[0]
    return np.eye(4)


def slot_delta(built: dict, slot: dict) -> np.ndarray:
    """D: the hand edit of a slot as a world transform, from the slot as built (or the slot it was
    duplicated from) to the slot now: D @ (a world point of the built slot) = where it is now."""
    return (np.asarray(slot["matrix"], float) @ _piece_move(built, slot)
            @ np.linalg.inv(np.asarray(built["matrix"], float)))


def delta_kind(D: np.ndarray, at) -> tuple[bool, bool]:
    """(moved: the slot's origin `at` (as built) shifted, or the slot turned; resized: scaled
    along any axis)."""
    k = np.linalg.norm(D[:3, :3], axis=0)
    turn = math.degrees(math.atan2(D[1, 0] / k[0], D[0, 0] / k[0]))
    shift = (D @ [*at, 1.0])[:3] - np.asarray(at, float)
    return bool(np.linalg.norm(shift) > MOVE_M or abs(turn) > TURN_DEG), bool(np.any(np.abs(k - 1) > SIZE_TOL))


def origin(sid: str, sources: dict, built: dict) -> str | None:
    """The built slot a new slot was duplicated from: its source tag (a copy of a copy: that
    one's), else its id without Blender-style -<n> suffixes; None for a slot made from scratch."""
    seen = set()
    while sid not in built and sid not in seen:
        seen.add(sid)
        nxt = sources.get(sid) or (re.sub(r"-\d+$", "", sid) if re.search(r"-\d+$", sid) else None)
        if nxt is None:
            return None
        sid = nxt
    return sid if sid in built else None


def slot_edits(built: dict, slots: list[dict], sources: dict) -> dict:
    """The greybox's hand edits against the greybox as built: slot ids moved, resized, removed
    (deleted in Blender), added {new id: the built slot it was duplicated from, or None}."""
    moved, resized, added = [], [], {}
    for s in slots:
        if s["id"] not in built:
            if s.get("category") != "terrain":      # (the terrain and sea Blender makes, not in an old spec)
                added[s["id"]] = origin(s["id"], sources, built)
            continue
        b = built[s["id"]]
        m, r = delta_kind(slot_delta(b, s), np.asarray(b["matrix"], float)[:3, 3])
        moved += [s["id"]] * m
        resized += [s["id"]] * r
    now = {s["id"] for s in slots}
    return {"moved": moved, "resized": resized, "removed": sorted(set(built) - now), "added": added}


def edits_line(edits: dict | None) -> str:
    """"N moved, N resized, N removed, N added" (Re-read .blend's summary)."""
    e = edits or {}
    return ", ".join(f"{len(e.get(k) or ())} {k}" for k in ("moved", "resized", "removed", "added"))


def slot_moves(store: ProjectStore, gb: dict) -> tuple[list[dict], dict[str, list[tuple[str, np.ndarray]]]]:
    """Where each slot as built is now: (the slots as built [{id, typology, matrix, bbox}], {built
    slot id: [(slot id now, D)]}): itself (unless deleted) and each duplicate of it, D its
    slot_delta. Placements made for the greybox as built (the city plan's typed buildings) follow
    with D. Never built: the slots as they are, no moves."""
    built = built_slots(store)
    if built is None:
        return [s for s in gb["slots"]], {}
    now = {s["id"]: s for s in gb["slots"]}
    added = (gb.get("slot_edits") or {}).get("added")
    if added is None:
        added = {sid: origin(sid, {}, built) for sid in now if sid not in built}
    def delta(b, slot):                     # under the edit thresholds (float noise in the .blend): none
        D = slot_delta(b, slot)
        return D if any(delta_kind(D, np.asarray(b["matrix"], float)[:3, 3])) else np.eye(4)
    out: dict[str, list] = {sid: [] for sid in built}
    for sid in built:
        if sid in now:
            out[sid].append((sid, delta(built[sid], now[sid])))
    for sid, src in sorted(added.items()):
        if src in built and sid in now:
            out[src].append((sid, delta(built[src], now[sid])))
    slots = []
    for sid, b in built.items():
        box = b["bbox"]
        if box is None:                     # (built before built_slots.json: the bbox now, moved back)
            if sid not in now:
                continue
            lo, hi = np.asarray(now[sid]["bbox"], float)
            inv = np.linalg.inv(out[sid][0][1])
            pts = np.array([(inv @ [x, y, z, 1.0])[:3] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            box = [pts.min(0).tolist(), pts.max(0).tolist()]
        slots.append({"id": sid, "typology": b["typology"], "matrix": b["matrix"], "bbox": box})
    return slots, out


_greybox_memo = threading.local()


@contextmanager
def reuse_greybox():
    """Within this block, on this thread only, load_greybox parses greybox.json once per
    project and hands every caller the same dict. For read-only listings that call it many
    times (the UI's frames listing: ~35 parses of a city greybox, ~6 s). Code inside must
    not modify what it gets; other threads (jobs) are unaffected."""
    prev = getattr(_greybox_memo, "d", None)
    _greybox_memo.d = {}
    try:
        yield
    finally:
        _greybox_memo.d = prev


def load_greybox(store: ProjectStore) -> dict:
    memo = getattr(_greybox_memo, "d", None)
    if memo is not None and str(store.root) in memo:
        return memo[str(store.root)]
    data = read_json(site_dir(store) / "greybox.json")
    if data is None:
        raise FileNotFoundError("no site/greybox.json (run `ap site build`)")
    if memo is not None:
        memo[str(store.root)] = data
    return data


def stale(store: ProjectStore) -> bool:
    """greybox.json no longer matches the .blend (run extract)."""
    b = blend_path(store)
    data = read_json(site_dir(store) / "greybox.json", default=None)
    return bool(b.is_file() and (not data or data.get("blend_sha256") != _sha(b)))


def add_shot(store: ProjectStore, shot: ShotSpec) -> dict:
    """Add or replace a shot camera in the .blend, and in the layout (so a rebuild keeps it).
    The layout keeps the auto values and the user's edits apart; the .blend gets the camera
    with the edits applied."""
    layout = load_layout(store)
    layout.shots = [s for s in layout.shots if s.id != shot.id] + [shot]
    save_layout(store, layout)
    shot = shot.effective()
    b = blend_path(store)
    if not b.is_file():
        raise FileNotFoundError(f"no {b} (run `ap site build`)")
    was_edited = edited(store)
    work = site_dir(store) / "build"
    path = work / f"shot_{shot.id}.json"
    write_json(path, shot.model_dump(mode="json"))
    blender.run(SCRIPT, ["--add-shot", path], work / "blender.log", f"add shot {shot.id}", blend=b)
    if not was_edited:
        _record(store)
    return extract(store)


def shot_spec(store: ProjectStore, shot: str) -> ShotSpec:
    """The layout's spec for a shot; a camera that exists only in the .blend (added by hand)
    is adopted into the layout from its current position."""
    layout = load_layout(store)
    spec = next((s for s in layout.shots if s.id == shot), None)
    if spec is not None:
        return spec
    cam = next((s for s in load_greybox(store)["shots"] if s["id"] == shot), None)
    if cam is None:
        raise KeyError(f"no shot {shot}")
    m = np.array(cam["matrix"], float)
    pos = m[:3, 3]
    look = pos - m[:3, 2] * 100.0          # a Blender camera looks down its local -Z
    spec = ShotSpec(id=shot, tier=cam["tier"], pos=tuple(pos), look_at=tuple(look), lens_mm=cam["lens_mm"],
                    sensor_mm=cam["sensor_mm"], resolution=tuple(cam["resolution"]), district=cam.get("district"),
                    notes=cam.get("notes", ""))
    layout.shots.append(spec)
    save_layout(store, layout)
    return spec


def update_shot(store: ProjectStore, shot: str, **fields) -> ShotSpec:
    """Save user edits (prompt_append, prompt_override, nudges, lens / tier overrides)."""
    spec = shot_spec(store, shot)
    layout = load_layout(store)
    spec = spec.model_copy(update=fields)
    ShotSpec.model_validate(spec.model_dump())
    layout.shots = [spec if s.id == shot else s for s in layout.shots]
    save_layout(store, layout)
    return spec


def summary(store: ProjectStore) -> dict:
    data = load_greybox(store)
    types: dict[str, int] = {}
    pieces: dict[str, int] = {}
    for s in data["slots"]:
        types[s["type"]] = types.get(s["type"], 0) + 1
        for p in s["pieces"]:
            pieces[p["piece"]] = pieces.get(p["piece"], 0) + 1
    e = data.get("slot_edits")
    was_edited = edited(store)
    return {"slots": len(data["slots"]), "types": dict(sorted(types.items())),
            "pieces": dict(sorted(pieces.items())), "shots": [s["id"] for s in data["shots"]],
            "untagged": data["untagged"], "edited": was_edited, "stale": stale(store),
            "slot_edits": edits_line(e) if e and (was_edited or any(e.values())) else None}


def plan_overlap(store: ProjectStore, shot: str) -> dict:
    """How well the greybox's ground (water, grass, paving, gravel) matches what a shot's locked
    frame paints, on the plan: the frame's plan (site/reconcile/<shot>/plan_frame.png, from
    frame_plan) against a top-down render of the current greybox over the same bounds. Each run is
    appended to plan_iou.json's history."""
    from .. import frame_plan
    d = site_dir(store) / "reconcile" / shot
    proj = read_json(d / "projection.json", default=None)
    if proj is None or not (d / "plan_frame.png").exists():
        raise FileNotFoundError(f"{d}: no frame plan (projection.json, plan_frame.png) for {shot}")
    fr = np.asarray(Image.open(d / "plan_frame.png").convert("RGB"))
    b = proj["bounds"]
    cell = (b[2] - b[0]) / fr.shape[1]
    job = {"bounds": b, "cell": cell, "out_png": str(d / "plan_greybox.png"), "out_json": str(d / "greybox_objects.json")}
    write_json(d / "topdown_job.json", job)
    blender.run(config.REPO_ROOT / "scripts/blender_plan_topdown.py", [d / "topdown_job.json"],
                d / "topdown.log", "greybox plan", blend=blend_path(store))
    gb = np.asarray(Image.open(d / "plan_greybox.png").convert("RGB"))
    rep = frame_plan.overlap(frame_plan.decode(fr), frame_plan.decode(gb, linear=True), cell)
    rep["greybox_sha"] = (read_json(site_dir(store) / "greybox.json", default={}) or {}).get("blend_sha256", "")[:12]
    rep["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    old = read_json(d / "plan_iou.json", default={}) or {}
    hist = old.get("history") or ([{"iou": old["after"], "note": "reconcile 2026-10-11"}] if "after" in old else [])
    write_json(d / "plan_iou.json", rep | {"history": hist + [{"iou": rep["iou"], "at": rep["at"]}]})
    return rep

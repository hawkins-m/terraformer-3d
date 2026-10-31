"""World dressing for `ap ue build` (and "Build city in Unreal"): optional ground textures, rocks,
vegetation and water, for any project. Each option is cheap by default; the user picks what their
machine can take. The options are project.json "unreal.dressing" (schema.DressingOptions), the
last-used set; `prepare` writes export/dressing.json and `unreal/ap_dressing.py` applies it inside
the import run.

    ground       a world-aligned ground material on the terrain: grass on flats, dirt patches by
                 macro noise, rock on steep ground, sand near sea level; macro variation (Light).
                 Streets and plazas take stone paving (`paving`): large slabs on avenues and
                 streets, small cobbles on lanes and stairs, large flagstones on plazas and the
                 forum's radials, world-aligned with the same macro variation and a second, larger
                 texture scale blended in with camera distance, so it doesn't tile from the air
    rocks        instanced small and large rocks in sparse clusters on open ground, more on slopes,
                 at edges and on the shore; never on buildings, streets, plazas or water (Light-Medium)
    vegetation   tree meshes for the greybox's placeholder trees (and the street-tree slots), olives in
                 open green, sparse shrubs and grass in green zones only (Medium-Heavy)
    water        simple: a sea material (depth colour, panning waves, shoreline foam) on the sea
                 plane (Light); ocean: the Water plugin's ocean, experimental (Heavy), falling back
                 to simple when it can't be made headless
    megascans    the project's dressing library (role -> Unreal asset path, or for a mesh role a
                 list of paths, project.json "unreal.dressing_library") where it has the role;
                 a list is picked from at random per instance (seeded: a rebuild repeats it)

Where each role comes from, first that applies: Megascans (ticked and mapped), the CC0 default
(config/models.toml feature "dressing": ambientCG ground sets, Poly Haven rocks and shrub; fetched on
first use), else a stylised Blender mesh (scripts/blender_dressing.py: umbrella pine, cypress, olive,
grass tufts, and rocks or shrub when their download is missing). The report names the source per role.

Placement is deterministic per seed. Where things may go comes from a top-down class render of the
greybox (scripts/blender_plan_topdown.py: building, paving, water, grass, tree, open ground) and the
ground heights (ground.Ground). dressing.json carries a key over everything it depends on (options,
greybox, vegetation slots, terrain, sources, VERSION): Unreal re-places instances only when it changes,
and an option unticked removes what it placed on the next build.

Megascans never come from the pipeline (Unreal-only license, added through Fab with the user's
login): `scan` finds them in the Unreal project's Content (/Game/Fab, /Game/Megascans, /Game/Quixel and
folders the user adds; a Windows project through its WSL path) and proposes roles from their names.
"""
import hashlib
import io
import json
import math
import os
import re
import zipfile
from pathlib import Path

import numpy as np

from . import config
from .project import ProjectStore, load_image, read_json, save_image, write_json
from .schema import DressingOptions

VERSION = "1"                  # bump when placement rules change: every project re-places
# role -> (option that uses it, kind, what it is)
ROLES = {
    "rock_small":   ("rocks", "mesh", "small rocks"),
    "rock_large":   ("rocks", "mesh", "large rocks and boulders"),
    "tree_pine":    ("vegetation", "mesh", "umbrella / stone pine"),
    "tree_cypress": ("vegetation", "mesh", "Italian cypress"),
    "tree_olive":   ("vegetation", "mesh", "olive tree"),
    "shrub":        ("vegetation", "mesh", "shrub"),
    "grass":        ("vegetation", "mesh", "grass clump"),
    "ground_grass": ("ground", "surface", "grass ground surface"),
    "ground_dirt":  ("ground", "surface", "dirt / soil surface"),
    "ground_rock":  ("ground", "surface", "rock surface (steep ground)"),
    "ground_sand":  ("ground", "surface", "sand surface (shore)"),
    "paving_slabs":      ("ground", "surface", "large stone slabs (avenues, streets)"),
    "paving_cobbles":    ("ground", "surface", "small cobbles (lanes, stairs)"),
    "paving_flagstones": ("ground", "surface", "large flagstones (plazas, the forum floor)"),
}
OPTIONS = {   # what the UI shows: label, performance tag, one-line tooltip
    "ground": ("Ground textures", "Light", "A world-aligned terrain material: grass on flats, dirt patches, "
               "rock on steep ground, sand near sea level, with macro variation so it doesn't tile."),
    "rocks": ("Rocks", "Light–Medium", "Instanced rock clusters on open ground, more on slopes and edges; "
              "never in building slots, streets or plazas."),
    "vegetation": ("Vegetation", "Medium–Heavy", "Tree meshes for the placeholder trees (pines and cypress along "
                   "streets and parks, olives in open green), sparse shrubs and grass in green zones."),
    "water_simple": ("Simple sea", "Light", "A water material on the sea plane: depth colour, waves, shoreline foam."),
    "water_ocean": ("Ocean (Water plugin, experimental)", "Heavy", "Unreal's Water plugin ocean (enabled in the "
                    ".uproject); falls back to Simple sea when it can't be made headless."),
    "megascans": ("Use Megascans assets", "", "Your Megascans (added through Fab) for the roles mapped in the "
                  "dressing library; free CC0 assets for the rest."),
}
SCAN_ROOTS = ("Fab", "Megascans", "Quixel")       # under Content/: /Game/Fab, /Game/Megascans, /Game/Quixel
OWN = "/Game/AP/"                                 # the pipeline's own content: never a Megascans asset
PLUGIN_NAMES = ("Fab", "Bridge", "MegascansPlugin", "QuixelBridge")
CULL_M = {"grass": (40, 60), "shrub": (150, 200), "rock_small": (300, 400)}   # instance fade start, end (m)
PROPOSE_MAX = 5                # name matches proposed per mesh role
SEA_DEPTH_M = 30.0             # depth texture: 1.0 = this deep or more
GRASS_TILE_M = 3.0             # the grass on park and garden lawns repeats every this many metres


# --- options and library (project.json) ----------------------------------------------------------

def _unreal(store: ProjectStore):
    from .schema import UnrealSettings
    return store.load().unreal or UnrealSettings()


def options(store: ProjectStore) -> DressingOptions:
    """The project's last-used dressing (a new project: [defaults.dressing]; projects from before it: all off)."""
    return _unreal(store).dressing


def assets_of(v) -> list[str]:
    """The Unreal asset paths a library entry holds: "" -> [], "/Game/A" -> ["/Game/A"], a list -> its
    non-empty, de-duplicated paths."""
    items = [v] if isinstance(v, str) else list(v or [])
    return list(dict.fromkeys(game_path(i) for i in items if isinstance(i, str) and i.strip()))


def game_path(s: str) -> str:
    """A pasted asset reference as a /Game path: Unreal's Copy Reference (StaticMesh'/Game/A/B.B')
    or a quoted path, trimmed to /Game/A/B.B."""
    s = s.strip()
    m = re.search(r"/Game/[^\s'\"]+", s)
    return m.group(0) if m else s


def _clean_entry(role: str, v):
    a = assets_of(v)
    if ROLES[role][1] != "mesh":
        return a[0] if a else ""           # ground surfaces stay single; "": the user chose CC0
    return a[0] if len(a) == 1 else a if a else ""


def save(store: ProjectStore, opts: DressingOptions | None = None, library: dict | None = None,
         folders: list[str] | None = None) -> None:
    """Remember options, the role -> Unreal asset mapping, or the extra scan folders."""
    p = store.load()
    u = _unreal(store).model_copy(deep=True)
    if opts is not None:
        u.dressing = opts
    if library is not None:
        u.dressing_library = {k: _clean_entry(k, v) for k, v in library.items() if k in ROLES}   # "": the user chose CC0
    if folders is not None:
        u.dressing_folders = [f.strip() for f in folders if f.strip()]
    p.unreal = u
    store.save(p)


def merged(store: ProjectStore, **changes) -> DressingOptions:
    """The last-used options with these set (None values ignored): `ap ue build` flags."""
    cur = options(store).model_dump()
    cur.update({k: v for k, v in changes.items() if v is not None})
    return DressingOptions(**cur)


def roles_for(opts: DressingOptions) -> list[str]:
    on = {"ground": opts.ground, "rocks": opts.rocks, "vegetation": opts.vegetation}
    return [r for r, (opt, _, _) in ROLES.items() if on[opt]]


# --- sources: Megascans, CC0, Blender fallback ----------------------------------------------------

def root() -> Path:
    return config.ap_root() / "dressing"


def cc0_models() -> dict:
    """{role: manifest Model} of the CC0 defaults (config/models.toml feature "dressing")."""
    from . import models
    return {m.select: m for m in models.load() if m.feature == "dressing" and m.select in ROLES}


def ensure_cc0(roles: list[str], log=print) -> dict:
    """{role: (Model, error or None)}: the CC0 assets of these roles, downloaded through the model
    manifest on first use (sizes and checksums verified)."""
    from . import models
    have = cc0_models()
    want = [have[r] for r in roles if r in have]
    out = {}
    missing = [m for m in want if models.status(m)["state"] != "installed"]
    if missing:
        log(f"dressing: downloading {len(missing)} CC0 asset(s) ({models.gb(sum(m.size for m in missing))})")
        errs = {}

        def emit(event, **k):
            if event == "error":
                errs[k.get("model", "")] = k.get("message", "")
        models.download(missing, emit=emit)
    for r in roles:
        m = have.get(r)
        if m is not None:
            out[r] = (m, None if models.status(m)["state"] == "installed" else "download failed")
    return out


def cc0_files(m) -> dict:
    """A CC0 asset's files for Unreal: a mesh role's .gltf, or a surface's maps (extracted once
    from the ambientCG zip): {color, normal (DirectX), rough}."""
    from . import models
    paths = [models.file_path(m, f) for f in m.files]
    gltf = [p for p in paths if p.suffix == ".gltf"]
    if gltf:
        return {"mesh": str(gltf[0])}
    z = paths[0]
    aid = m.repo.split(":", 1)[1]
    d = z.parent / "maps"
    want = {"color": "Color", "normal": "NormalDX", "rough": "Roughness"}
    out = {k: d / f"{aid}_{m.revision}_{s}.jpg" for k, s in want.items()}
    if not all(p.is_file() for p in out.values()):
        d.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            for p in out.values():
                p.write_bytes(zf.read(p.name))
    return {k: str(p) for k, p in out.items()}


FALLBACK_SCRIPT = config.REPO_ROOT / "scripts" / "blender_dressing.py"


def fallback_meshes(roles: list[str], log=print) -> dict:
    """{role: GLB} stylised meshes from Blender, made once per version of the script."""
    from . import blender
    if not roles:
        return {}
    tag = hashlib.sha256(FALLBACK_SCRIPT.read_bytes()).hexdigest()[:10]
    d = root() / "fallback" / tag
    todo = [r for r in roles if not (d / f"{r}.glb").is_file()]
    if todo:
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / "job.json", {"out_dir": str(d), "roles": todo, "seed": 7})
        log(f"dressing: making fallback meshes in Blender ({', '.join(todo)})")
        blender.run(FALLBACK_SCRIPT, [d / "job.json"], d / "blender.log", "dressing fallback meshes")
    return {r: str(d / f"{r}.glb") for r in roles}


def sources(roles: list[str], library: dict, megascans: bool, log=print) -> dict:
    """{role: {"source": megascans | cc0 | fallback | none, ...}} for these roles. Megascans when
    ticked and mapped (Unreal falls back to the next source if the asset is gone), else CC0, else a
    Blender mesh (surfaces have no fallback: the terrain keeps its own material)."""
    out, rest = {}, []
    for r in roles:
        got_assets = assets_of(library.get(r))
        if megascans and got_assets:
            out[r] = {"source": "megascans", "asset": got_assets[0], "assets": got_assets[:1] if ROLES[r][1] != "mesh" else got_assets}
        else:
            rest.append(r)
    got = ensure_cc0(rest + [r for r in out if r not in rest], log)    # CC0 behind every Megascans role too
    need_fb = []
    for r in roles:
        m, err = got.get(r, (None, "no CC0 asset for this role"))
        cc0 = {"source": "cc0", "id": m.id, "title": m.title, "page": m.page, **cc0_files(m)} if m and not err else None
        if r in out:
            if cc0:
                out[r]["fallback"] = cc0
            elif ROLES[r][1] == "mesh":
                need_fb.append(r)
            continue
        if cc0:
            out[r] = cc0
        elif ROLES[r][1] == "mesh":
            need_fb.append(r)
            out[r] = {"source": "fallback", "why": err}
        else:
            out[r] = {"source": "none", "why": err}
    for r, glb in fallback_meshes(need_fb, log).items():
        fb = {"source": "fallback", "mesh": glb, "why": out[r].get("why", "")}
        if out[r]["source"] == "megascans":
            out[r]["fallback"] = fb
        else:
            out[r] = fb
    return out


# --- where dressing may go: a top-down class render of the greybox -------------------------------

def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def work_dir(store: ProjectStore) -> Path:
    from .stages import s7_export
    return s7_export.export_dir(store) / "dressing"


def area(store: ProjectStore, gb: dict) -> tuple[float, float, float, float]:
    """The dressed rectangle (world metres): the city terrain slot's extent, else the greybox's
    slots, at most 8 km across."""
    t = next((s for s in gb["slots"] if s["id"] == "terrain"), None)
    if t:
        (x0, y0, _), (x1, y1, _) = t["bbox"]
    else:
        boxes = [s["bbox"] for s in gb["slots"] if s.get("type") not in ("sea", "terrain")] or [[[-200, -200, 0], [200, 200, 0]]]
        x0, y0 = min(b[0][0] for b in boxes) - 50, min(b[0][1] for b in boxes) - 50
        x1, y1 = max(b[1][0] for b in boxes) + 50, max(b[1][1] for b in boxes) + 50
    cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, min(4000.0, max(x1 - x0, y1 - y0) / 2)
    return cx - half, cy - half, cx + half, cy + half


def class_map(store: ProjectStore, gb: dict, log=print) -> dict:
    """{"cls": class indices (frame_plan.CLASSES; row 0 = north), "bounds", "cell"}: the greybox
    from above, cached per greybox."""
    from . import blender, frame_plan
    from .stages import sw_site
    b = area(store, gb)
    cell = max(2.0, (b[2] - b[0]) / 4096)
    key = hashlib.sha256(json.dumps([gb.get("blend_sha256"), b, cell, _sha(config.REPO_ROOT / "scripts/blender_plan_topdown.py")])
                         .encode()).hexdigest()[:12]
    d = work_dir(store)
    png, npz = d / "topdown.png", d / f"classes_{key}.npz"
    if not npz.is_file():
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("classes_*.npz"):
            old.unlink()
        job = {"bounds": list(b), "cell": cell, "out_png": str(png), "out_json": str(d / "topdown_objects.json")}
        write_json(d / "topdown_job.json", job)
        log("dressing: greybox plan from above (Blender)")
        blender.run(config.REPO_ROOT / "scripts/blender_plan_topdown.py", [d / "topdown_job.json"], d / "topdown.log",
                    "dressing plan", blend=sw_site.blend_path(store))
        cls = frame_plan.decode(np.asarray(load_image(png).convert("RGB")), linear=True)
        np.savez_compressed(npz, cls=cls)
    return {"cls": np.load(npz)["cls"], "bounds": b, "cell": cell}


class Grid:
    """Lookups on the class map in world metres."""

    def __init__(self, cm: dict, ground, slots: list[dict] = ()):
        from scipy import ndimage

        from .frame_plan import CLASSES
        self.cls, self.b, self.cell, self.ground = cm["cls"], cm["bounds"], cm["cell"], ground
        k = {c: CLASSES.index(c) for c in CLASSES}
        c = self.cls
        self.open = (c == k["other"]) | (c == k["gravel"]) | (c == k["hill"])
        self.green = (c == k["grass"]) | (c == k["tree"])
        self.water = c == k["water"]
        hard = (c == k["building"]) | (c == k["paving"])
        r = max(1, int(round(3.0 / self.cell)))
        self.edge = ndimage.binary_dilation(hard, iterations=r)                 # within ~3 m of buildings, streets
        self.shore = ndimage.binary_dilation(self.water, iterations=max(1, int(round(6.0 / self.cell))))
        enclosed = ndimage.binary_fill_holes(hard)                              # blocks and plazas ringed by streets
        self.open_in = ndimage.binary_erosion(self.open, iterations=1) & ~enclosed & ~self.slot_mask(slots)
        self.green_in = ndimage.binary_erosion(self.green, iterations=1) & ~self.edge
        self.trees = c == k["tree"]

    def slot_mask(self, slots) -> np.ndarray:
        """Building, structure and plaza slots' bounds: no rocks there, courtyards included."""
        m = np.zeros(self.cls.shape, bool)
        for s in slots:
            if s.get("category") in ("building", "structure") or s.get("type") in ("plaza", "market", "forum"):
                (x0, y0, _), (x1, y1, _) = s["bbox"]
                i0, j0 = self.ij(x0, y1)
                i1, j1 = self.ij(x1, y0)
                m[int(i0):int(i1) + 1, int(j0):int(j1) + 1] = True
        return m

    def ij(self, x, y):
        x0, y0, x1, y1 = self.b
        i = np.clip(((y1 - np.asarray(y)) / self.cell).astype(int), 0, self.cls.shape[0] - 1)
        j = np.clip(((np.asarray(x) - x0) / self.cell).astype(int), 0, self.cls.shape[1] - 1)
        return i, j

    def at(self, mask, x, y):
        i, j = self.ij(x, y)
        return mask[i, j]

    def slope_deg(self, x, y, d=2.0):
        g = self.ground
        dzdx = (np.asarray(g.sample.many(np.asarray(x) + d, np.asarray(y))) - g.sample.many(np.asarray(x) - d, np.asarray(y))) / (2 * d) \
            if g.sample else np.zeros(np.shape(x))
        dzdy = (np.asarray(g.sample.many(np.asarray(x), np.asarray(y) + d)) - g.sample.many(np.asarray(x), np.asarray(y) - d)) / (2 * d) \
            if g.sample else np.zeros(np.shape(x))
        return np.degrees(np.arctan(np.hypot(dzdx, dzdy)))

    def z(self, x, y):
        g = self.ground
        return np.asarray(g.sample.many(np.asarray(x, float), np.asarray(y, float))) if g.sample else np.full(np.shape(x), g.base)


def _jitter(b, spacing, rng):
    """Jittered grid points over bounds b (x0, y0, x1, y1), in a fixed order."""
    xs = np.arange(b[0] + spacing / 2, b[2], spacing)
    ys = np.arange(b[1] + spacing / 2, b[3], spacing)
    gx, gy = np.meshgrid(xs, ys)
    jit = rng.uniform(-0.5, 0.5, (2,) + gx.shape) * spacing
    return (gx + jit[0]).ravel(), (gy + jit[1]).ravel()


def _inst(x, y, z, yaw, s=(1.0, 1.0, 1.0), pitch=0.0, roll=0.0, v=0, sink=0.0):
    """One instance for Unreal: [x, y, z (cm, Y mirrored), yaw, pitch, roll (deg, UE), sx, sy, sz,
    variant, sink (fraction of the mesh's height)]."""
    return [round(float(x) * 100, 1), round(-float(y) * 100, 1), round(float(z) * 100, 1), round(-float(yaw), 2),
            round(float(pitch), 2), round(float(roll), 2), round(float(s[0]), 3), round(float(s[1]), 3), round(float(s[2]), 3),
            int(v), round(float(sink), 3)]


def place_rocks(grid: Grid, density: float, rng) -> dict:
    """Sparse clusters on open ground: more on slopes, at the edges of streets and buildings and on
    the shore; each rock on open ground itself, randomly turned, tilted, scaled and slightly sunk."""
    out = {"rock_small": [], "rock_large": []}
    cx, cy = _jitter(grid.b, 45.0 / math.sqrt(density), rng)
    u = rng.uniform(0, 1, cx.shape)
    ok = grid.at(grid.open_in, cx, cy)
    slope = np.clip((grid.slope_deg(cx, cy) - 8.0) / 25.0, 0, 1)
    w = 0.05 + 0.5 * slope + 0.3 * grid.at(grid.edge, cx, cy) + 0.3 * grid.at(grid.shore, cx, cy)
    keep = ok & (u < w)
    for x0, y0, sl in zip(cx[keep], cy[keep], slope[keep]):
        n = int(rng.integers(2, 6))
        large = int(rng.uniform() < 0.2 + 0.4 * sl)
        a = rng.uniform(0, 2 * math.pi, n + large)
        r = rng.uniform(0.0, 4.5, n + large)
        xs, ys = x0 + r * np.cos(a), y0 + r * np.sin(a)
        good = grid.at(grid.open_in, xs, ys)
        zs = grid.z(xs, ys)
        for k in range(n + large):
            role = "rock_large" if k >= n else "rock_small"
            s = rng.uniform(0.7, 1.5) if role == "rock_large" else rng.uniform(0.5, 1.4)
            yaw, pitch, roll = rng.uniform(0, 360), rng.uniform(-10, 10), rng.uniform(-10, 10)
            v, sink = int(rng.integers(0, 64)), rng.uniform(0.12, 0.3)
            if good[k]:
                out[role].append(_inst(xs[k], ys[k], zs[k], yaw, (s, s, s), pitch, roll, v, sink))
    return out


TREE_RE = re.compile(r"^tree:(\w+?)(-trunk|-crown)?$")


def placeholder_trees(gb: dict, index: dict) -> tuple[list[dict], list[dict]]:
    """The greybox's placeholder trees ([{x, y, z, h, w, species}]) and the slot components that draw
    only them ([{slot, components: [ISM names]}]) for Unreal to hide."""
    trees, hide = [], []
    for s in gb["slots"]:
        m = np.asarray(s["matrix"], float)
        groups: dict[tuple, dict] = {}
        tree_assets, other_assets = set(), set()
        for pc in s.get("pieces", []):
            aid = (index.get(pc["mesh"]) or {}).get("id")
            mt = TREE_RE.match(pc["piece"])
            if not mt:
                other_assets.add(aid)
                continue
            tree_assets.add(aid)
            w = m @ np.asarray(pc["matrix_local"], float)
            bounds = (index.get(pc["mesh"]) or {}).get("bounds") or [[-0.5, -0.5, 0], [0.5, 0.5, 1]]
            sx, sz = np.linalg.norm(w[:3, 0]), np.linalg.norm(w[:3, 2])
            x, y, z = w[0, 3], w[1, 3], w[2, 3]
            g = groups.setdefault((round(x, 1), round(y, 1)), {"x": x, "y": y, "z": z, "top": z, "w": 0.0, "species": mt.group(1)})
            g["z"] = min(g["z"], z + bounds[0][2] * sz)
            g["top"] = max(g["top"], z + bounds[1][2] * sz)
            g["w"] = max(g["w"], (bounds[1][0] - bounds[0][0]) * sx)
        for g in groups.values():
            trees.append({"x": g["x"], "y": g["y"], "z": g["z"], "h": max(2.0, g["top"] - g["z"]), "w": max(1.0, g["w"]),
                          "species": g["species"]})
        only = sorted(a for a in tree_assets - other_assets if a)
        if only:
            hide.append({"slot": s["id"], "components": [f"ISM_{a}" for a in only]})
    return trees, hide


def drop_placeholders(man: dict, hide: list[dict]) -> int:
    """The placeholder trees the dressing's tree roles replace (hide: placeholder_trees) out of the
    manifest's slots (in place), so the import never places them (a park's SM_tree-* stood under
    the dressing's trees). Returns the components dropped; the next export brings them back."""
    drop = {(h["slot"], c) for h in hide for c in h["components"]}
    n = 0
    for s in man["slots"]:
        keep = [c for c in s["components"] if (s["id"], f"ISM_{c['asset']}") not in drop]
        n += len(s["components"]) - len(keep)
        s["components"] = keep
    return n


SPECIES = {"pine": "tree_pine", "cypress": "tree_cypress", "olive": "tree_olive"}


def place_vegetation(grid: Grid, density: float, rng, trees: list[dict], street: list[dict], zones: list[dict]) -> dict:
    """Trees where the plan has them (its species; street-tree slots: pines with some cypresses),
    olives in open green, shrubs along green edges and in front gardens, grass clumps in green zones."""
    out = {r: [] for r in ("tree_pine", "tree_cypress", "tree_olive", "shrub", "grass")}
    for t in trees:
        role = SPECIES.get(t["species"], "tree_pine")
        out[role].append(_inst(t["x"], t["y"], t["z"], rng.uniform(0, 360), (t["w"], t["w"], t["h"]), v=int(rng.integers(0, 64))))
    for t in street:
        cyp = t.get("kind") == "street" and rng.uniform() < 0.3
        h = rng.uniform(10, 14) if cyp else rng.uniform(9, 13)
        w = 2.4 if cyp else float(t.get("crown_m") or 8.0)
        out["tree_cypress" if cyp else "tree_pine"].append(
            _inst(t["x"], t["y"], grid.z([t["x"]], [t["y"]])[0], rng.uniform(0, 360), (w, w, h), v=int(rng.integers(0, 64))))
    # olives in open green: well inside lawns and parks, apart from the plan's trees
    ox, oy = _jitter(grid.b, 38.0 / math.sqrt(density), rng)
    u = rng.uniform(0, 1, ox.shape)
    pick = grid.at(grid.green_in, ox, oy) & ~grid.at(grid.trees, ox, oy) & (u < 0.35)
    for x, y, z in zip(ox[pick], oy[pick], grid.z(ox[pick], oy[pick])):
        h = rng.uniform(4.5, 6.5)
        out["tree_olive"].append(_inst(x, y, z, rng.uniform(0, 360), (h * 0.9, h * 0.9, h), v=int(rng.integers(0, 64))))
    # shrubs: green edges (beside paths and buildings) and the library builds' front gardens
    sx, sy = _jitter(grid.b, 7.0 / math.sqrt(density), rng)
    u = rng.uniform(0, 1, sx.shape)
    near = grid.at(grid.edge, sx, sy)
    pick = grid.at(grid.green, sx, sy) & (u < np.where(near, 0.45, 0.06))
    for x, y, z in zip(sx[pick], sy[pick], grid.z(sx[pick], sy[pick])):
        s = rng.uniform(0.7, 1.3)
        out["shrub"].append(_inst(x, y, z, rng.uniform(0, 360), (s, s, s), v=int(rng.integers(0, 64)), sink=0.05))
    for zn in zones:
        n = max(1, int(round(zn["w"] * zn["d"] / 6.0 * density * 0.5)))
        a = math.radians(zn["yaw"])
        for _ in range(min(n, 12)):
            uu, vv = rng.uniform(-0.5, 0.5) * zn["w"], rng.uniform(-0.5, 0.5) * zn["d"]
            x, y = zn["x"] + uu * math.cos(a) - vv * math.sin(a), zn["y"] + uu * math.sin(a) + vv * math.cos(a)
            s = rng.uniform(0.6, 1.1)
            out["shrub"].append(_inst(x, y, zn["z"], rng.uniform(0, 360), (s, s, s), v=int(rng.integers(0, 64)), sink=0.05))
    # grass clumps in green zones only
    gx, gy = _jitter(grid.b, 5.0 / math.sqrt(density), rng)
    u = rng.uniform(0, 1, gx.shape)
    pick = grid.at(grid.green_in, gx, gy) & (u < 0.3)
    for x0, y0 in zip(gx[pick], gy[pick]):
        n = int(rng.integers(3, 7))
        a, r = rng.uniform(0, 2 * math.pi, n), rng.uniform(0, 1.2, n)
        xs, ys = x0 + r * np.cos(a), y0 + r * np.sin(a)
        ok, zs = grid.at(grid.green, xs, ys), grid.z(xs, ys)
        for k in range(n):
            s = rng.uniform(0.7, 1.4)
            if ok[k]:
                out["grass"].append(_inst(xs[k], ys[k], zs[k], rng.uniform(0, 360), (s, s, s), v=int(rng.integers(0, 64))))
    return out


# --- water ----------------------------------------------------------------------------------------

def sea_slots(man: dict) -> list[str]:
    return [s["id"] for s in man["slots"] if s.get("type") == "sea"]


RIVER_TYPES = ("river", "canal")       # lowered water inland: the sea's material at a steady depth
RIVER_DEPTH = 0.15                     # its depth map value (of SEA_DEPTH_M): mid-blue, no shore foam


def river_slots(man: dict) -> list[str]:
    return [s["id"] for s in man["slots"] if s.get("type") in RIVER_TYPES]


def river_depth() -> str:
    """A flat depth map for rivers and canals (their bed isn't below the sea plane, so the sea's
    depth map would make them all shore foam); made once."""
    from PIL import Image
    p = root() / "generated" / "T_AP_RiverDepth.png"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        save_image(Image.new("RGB", (8, 8), (round(RIVER_DEPTH * 255), 0, 0)), p)
    return str(p)


def sea_depth(store: ProjectStore, grid: Grid, sea_z: float) -> dict:
    """A depth map under the sea plane over the dressed area, in Unreal's frame (u along +X, v along
    +Y = Blender -Y): R = depth / SEA_DEPTH_M. {png, origin_cm, size_cm}."""
    from PIL import Image
    x0, y0, x1, y1 = grid.b
    n = 1024
    us = np.linspace(x0, x1, n)
    vs = np.linspace(-y1, -y0, n)        # UE y = -Blender y
    gx, gy = np.meshgrid(us, -vs)
    depth = np.clip((sea_z - grid.z(gx.ravel(), gy.ravel()).reshape(gx.shape)) / SEA_DEPTH_M, 0, 1)
    png = work_dir(store) / "T_AP_SeaDepth.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    save_image(Image.fromarray((depth * 255).astype(np.uint8), "L").convert("RGB"), png)
    return {"png": str(png), "origin_cm": [x0 * 100, -y1 * 100], "size_cm": (x1 - x0) * 100}


def _noise(n: int, beta: float, rng, aniso: tuple[float, float] = (1.0, 1.0), equalise: bool = True,
           low_cut: float = 0.0) -> np.ndarray:
    """Tileable fractal noise (white noise shaped to a 1/f^beta spectrum by FFT: isotropic, no
    stripes), equalised to an even 0-1 spread (else unit standard deviation). aniso stretches it
    (wind-driven waves); low_cut removes wavelengths longer than n / low_cut pixels."""
    f = np.fft.fftfreq(n) * n
    fx, fy = np.meshgrid(f * aniso[0], f * aniso[1])
    k = np.hypot(fx, fy)
    k[0, 0] = 1.0
    spec = np.fft.fft2(rng.standard_normal((n, n))) / k ** beta
    if low_cut:
        spec *= 1 - np.exp(-(k / low_cut) ** 4)
    spec[0, 0] = 0
    h = np.real(np.fft.ifft2(spec))
    if not equalise:
        return h / h.std()
    return h.ravel().argsort().argsort().reshape(h.shape) / (h.size - 1)


def wave_normal() -> str:
    """A tileable wave normal map (DirectX green) from wind-stretched fractal noise, made once."""
    from PIL import Image
    p = root() / "generated" / "T_AP_WaveNormal_v5.png"
    if p.is_file():
        return str(p)
    h = _noise(512, 2.6, np.random.default_rng(11), aniso=(1.0, 2.2), equalise=False, low_cut=7.0)
    gy_, gx_ = np.gradient(h)
    k = 0.35 / np.abs(np.concatenate([gx_.ravel(), gy_.ravel()])).std()      # slopes around 0.35
    nx, ny, nz = -gx_ * k, gy_ * k, np.ones_like(h)
    ln = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    rgb = np.stack([nx / ln, ny / ln, nz / ln], -1) * 0.5 + 0.5
    p.parent.mkdir(parents=True, exist_ok=True)
    save_image(Image.fromarray((rgb * 255).astype(np.uint8), "RGB"), p)
    return str(p)


def white_mask() -> str:
    """A small white linear map: the ground material's default roughness (a Masks sampler can't
    default to an sRGB engine texture)."""
    from PIL import Image
    p = root() / "generated" / "T_AP_WhiteMask.png"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        save_image(Image.new("RGB", (8, 8), (255, 255, 255)), p)
    return str(p)


def macro_noise() -> str:
    """Two tileable fractal noise fields (R, G), equalised to an even 0-1 spread so the ground
    material's thresholds mean the same on any surface; made once."""
    from PIL import Image
    p = root() / "generated" / "T_AP_MacroNoise_v2.png"
    if p.is_file():
        return str(p)
    rng = np.random.default_rng(5)
    rgb = np.stack([_noise(512, 1.4, rng), _noise(512, 1.2, rng), np.zeros((512, 512))], -1)
    p.parent.mkdir(parents=True, exist_ok=True)
    save_image(Image.fromarray((rgb * 255).astype(np.uint8), "RGB"), p)
    return str(p)


# --- paving -------------------------------------------------------------------------------------

# kind -> (role, slot types, texture repeat in metres (x, y): ambientCG's measured size)
PAVING = {"Slabs": ("paving_slabs", ("avenue", "street"), (1.9, 3.8)),
          "Cobbles": ("paving_cobbles", ("lane", "stair"), (1.15, 1.15)),
          "Flagstones": ("paving_flagstones", ("plaza", "radial"), (3.5, 3.5))}
NOT_PAVED = {"pool", "water", "tree", "lawn", "garden"}     # words of a slot's pieces that keep their own look


def paving(man: dict, src: dict) -> dict:
    """{kind: {role, tile_m, src, slots [{slot, components [ISM names]}]}} for the way and plaza slots
    of the manifest (only their paved pieces: a plaza's pool stays water)."""
    out = {}
    for kind, (role, types, tile) in PAVING.items():
        rows = []
        for s in man["slots"]:
            if s.get("type") not in types:
                continue
            comps = sorted({f"ISM_{c['asset']}" for c in s.get("components") or []
                            if not NOT_PAVED & set(c["asset"].split("-"))})
            if comps:
                rows.append({"slot": s["id"], "components": comps})
        if rows:
            out[kind] = {"role": role, "tile_m": list(tile), "src": src.get(role), "slots": rows}
    return out


# --- the plan Unreal applies ----------------------------------------------------------------------

def _sea_z(gb: dict) -> float:
    s = next((s for s in gb["slots"] if s.get("type") == "sea"), None)
    return float(s["matrix"][2][3]) if s else 0.0


def prepare(store: ProjectStore, opts: DressingOptions, log=print) -> dict:
    """Write export/dressing.json for the import (always: with everything off it removes earlier
    dressing). Recomputes placement only when its key changes. Returns the dressing record."""
    from . import ue
    from .ground import Ground
    from .stages import s7_export, sw_site
    out = s7_export.export_dir(store) / "dressing.json"
    man = s7_export.load_manifest(store)
    lib = _unreal(store).dressing_library
    rec = {"version": VERSION, "options": opts.model_dump(), "warnings": []}
    if opts.megascans:
        lib = rescan(store, log) or lib
    roles = roles_for(opts)
    src = sources(roles, lib, opts.megascans, log) if roles else {}
    gb = sw_site.load_greybox(store)
    placed = ("rocks", "rock_density", "vegetation", "vegetation_density", "seed")
    deps = {"v": VERSION, "opts": {k: v for k, v in opts.model_dump().items() if k in placed},
            "greybox": gb.get("blend_sha256") or gb.get("geometry_sha256"),
            "src": {r: v for r, v in src.items() if ROLES[r][1] == "mesh"},
            "veg": _sha(sw_site.site_dir(store) / "vegetation.json"), "zones": _sha(s7_export.export_dir(store) / "vegetation.json"),
            "terrain": _sha(sw_site.site_dir(store) / "terrain.png")}
    key = hashlib.sha256(json.dumps(deps, sort_keys=True, default=str).encode()).hexdigest()[:16]
    old = read_json(out, default=None) or {}
    rec |= {"key": key, "sources": src, "roles": roles}
    if old.get("key") == key and "instances" in old:
        rec |= {k: old[k] for k in ("instances", "hide", "counts", "area") if k in old}    # (no area: rocks and vegetation off)
        log("dressing: placement unchanged")
    else:
        rec |= {"instances": {}, "hide": [], "counts": {}}
        if opts.rocks or opts.vegetation:
            grid = Grid(class_map(store, gb, log), Ground.from_project(store), gb["slots"])
            rec["area"] = list(grid.b)
            if opts.rocks:
                rec["instances"] |= place_rocks(grid, opts.rock_density, np.random.default_rng([opts.seed, 1]))
            if opts.vegetation:
                index = read_json(s7_export.export_dir(store) / "mesh_index.json", default={}) or {}
                trees, rec["hide"] = placeholder_trees(gb, index)
                from . import vegetation
                street = (vegetation.load(store) or {}).get("street_trees", [])
                zones = [p for p in (read_json(s7_export.export_dir(store) / "vegetation.json", default={}) or {}).get("points", [])
                         if p["type"] == "front_garden"]
                rec["instances"] |= place_vegetation(grid, opts.vegetation_density, np.random.default_rng([opts.seed, 2]),
                                                     trees, street, zones)
        rec["counts"] = {r: len(v) for r, v in rec["instances"].items()}
    rec["fit"] = {r: ("size" if r.startswith("tree_") else "native") for r in rec["instances"]}
    rec["cull_cm"] = {r: [a * 100, b * 100] for r, (a, b) in CULL_M.items()}
    rec["ground"] = None
    if opts.ground:
        t = man.get("terrain") or {}
        rec["ground"] = {"slots": t.get("material_slots") or [s["id"] for s in man["slots"] if s.get("type") == "terrain"],
                         "outer": bool(t.get("outer_ring")), "sea_z_cm": _sea_z(gb) * 100, "noise": macro_noise(),
                         "white_mask": white_mask(),
                         "layers": {r: src.get(r) for r in roles if ROLES[r][1] == "surface" and not r.startswith("paving_")},
                         "paving": paving(man, src)}
    rec["green"] = None
    if opts.vegetation:
        rows = (man.get("terrain") or {}).get("ground_slots") or []
        if rows:                                 # the parks' and gardens' lawns: grass (the import gave them the terrain material)
            g = src.get("ground_grass") or sources(["ground_grass"], lib, opts.megascans, log)["ground_grass"]
            rec["green"] = {"role": "ground_grass", "src": g, "tile_m": [GRASS_TILE_M] * 2, "slots": rows,
                            "noise": macro_noise(), "white_mask": white_mask()}
        if drop_placeholders(man, rec["hide"]):
            write_json(s7_export.export_dir(store) / "manifest.json", man)
    rec["water"] = None
    if opts.water != "off":
        sea = sea_slots(man)
        rivers = river_slots(man)
        if not sea and not rivers:
            rec["warnings"].append("water: the greybox has no sea slot; nothing to dress")
        elif not sea:                            # inland: rivers only, the sea's material at a steady depth
            rec["water"] = {"mode": opts.water, "slots": [], "rivers": rivers, "river_depth": river_depth(), "waves": wave_normal()}
        else:
            grid = Grid(class_map(store, gb, log), Ground.from_project(store))
            rec["water"] = {"mode": opts.water, "slots": sea, "sea_z_cm": _sea_z(gb) * 100,
                            "depth": sea_depth(store, grid, _sea_z(gb)), "waves": wave_normal()}
            if rivers:
                rec["water"] |= {"rivers": rivers, "river_depth": river_depth()}
            if opts.water == "ocean":
                rec["water"]["plugin_enabled_now"] = ue.enable_plugin(store, "Water")
    write_json(out, rec)
    return rec


# --- Megascans: scan the Unreal project, guess roles ----------------------------------------------

WORDS = {   # role -> words in a name or folder that suggest it (first match wins, in this order)
    "tree_cypress": ("cypress", "cupressus"),
    "tree_olive": ("olive", "olea"),
    "tree_pine": ("umbrella pine", "stone pine", "pinus pinea", "pine"),
    "grass": ("grass", "clump", "tuft"),
    "shrub": ("shrub", "bush", "hedge", "fern", "plant"),
    "rock_large": ("boulder", "cliff", "large rock", "rock large", "big rock", "outcrop"),
    "rock_small": ("pebble", "small rock", "rock small", "stone", "rubble", "rock"),
}
SURFACE_WORDS = {"paving_cobbles": ("cobble", "sett"), "paving_flagstones": ("flagstone", "flag stone", "plaza"),
                 "paving_slabs": ("paving", "pavement", "slab", "roman road"),
                 "ground_sand": ("sand", "beach", "dune"), "ground_rock": ("rock", "cliff", "stone", "gravel"),
                 "ground_grass": ("grass", "lawn", "meadow", "moss"), "ground_dirt": ("dirt", "soil", "mud", "ground", "earth", "forest floor")}


def content_dir(store: ProjectStore) -> Path:
    """The Unreal project's Content folder as this machine sees it (a Windows project under WSL:
    its translated path)."""
    from . import ue, wsl
    d = str(ue.project_dir(store))
    return Path(wsl.to_linux(d) if wsl.is_windows_path(d) else d) / "Content"


def _words(s: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def asset_class(path: Path) -> str | None:
    """static_mesh | foliage | material_instance | None, from the name and the package's own bytes
    (no editor needed)."""
    n = path.stem
    head = path.read_bytes()[:400_000] if path.stat().st_size < 50_000_000 else b""
    if re.match(r"(?i)^(ft|fol|foliage)_", n) or b"FoliageType_InstancedStaticMesh" in head:
        return "foliage"
    if re.match(r"(?i)^mi_", n) or (b"MaterialInstanceConstant" in head and b"StaticMeshDescription" not in head and not re.match(r"(?i)^(sm|s)_", n)):
        return "material_instance"
    if re.match(r"(?i)^(sm|s)_", n) or b"StaticMeshDescription" in head or b"NaniteSettings" in head:
        return "static_mesh"
    return None


def guess_role(name: str, folder: str, cls: str) -> str | None:
    """The dressing role a Megascans asset's name and folder suggest, or None."""
    w = _words(folder + " " + name)
    if cls == "material_instance":
        if not any(k in w for k in (" surface", " surfaces", " ground ", " terrain ", " decal")) and \
                any(k in w for k in (" 3d ", " plant", " tree", " rock ", " boulder")):
            return None                 # a 3D asset's own material, not a surface
        for role, words in SURFACE_WORDS.items():
            if any(f" {k} " in w for k in words):
                return role
        return None
    for role, words in WORDS.items():
        if any(f" {k} " in w for k in words):
            return role
    return None


def _rank(p: dict) -> tuple:
    """Prefer foliage types and full-detail meshes, then the shortest, then by path."""
    w = _words(p["path"])
    lod = 0 if (" lod0 " in w or " high " in w or " tier 1 " in w) else 1 if " lod" not in w else 2
    return (p["class"] != "foliage", lod, len(p["path"]), p["path"])


def scan(store: ProjectStore, folders: list[str] | None = None) -> dict:
    """Megascans-type assets in the Unreal project ({candidates: {role: [{path, class, name}]},
    proposal: {role: path}, folders_scanned, content}). folders: extra /Game/... folders (or
    absolute disk folders inside Content) beyond /Game/Fab, /Game/Megascans, /Game/Quixel."""
    content = content_dir(store)
    extra = folders if folders is not None else _unreal(store).dressing_folders
    dirs = []
    for f in [*SCAN_ROOTS, *extra]:
        f = str(f).strip()
        if f.startswith("/Game/"):
            dirs.append(content / f.removeprefix("/Game/"))
        elif os.path.isabs(f):
            dirs.append(Path(f))
        else:
            dirs.append(content / f)
    cands: dict[str, list] = {r: [] for r in ROLES}
    all_meshes: list[dict] = []        # every scanned static mesh and foliage type, whatever its name
    seen, scanned = set(), []
    for d in dirs:
        if not d.is_dir():
            continue
        scanned.append(str(d))
        for p in sorted(d.rglob("*.uasset")):
            if p in seen:
                continue
            seen.add(p)
            cls = asset_class(p)
            if cls is None:
                continue
            rel = p.relative_to(content).with_suffix("")
            game = "/Game/" + rel.as_posix()
            if game.startswith(OWN):                 # (a scan folder of the whole Content sees it too)
                continue
            ue_path = f"{game}.{p.stem}"
            if cls in ("static_mesh", "foliage"):
                all_meshes.append({"path": ue_path, "class": cls, "name": p.stem})
            role = guess_role(p.stem, rel.parent.as_posix(), cls)
            if role is None or (ROLES[role][1] == "surface") != (cls == "material_instance"):
                continue
            cands[role].append({"path": ue_path, "class": cls, "name": p.stem})
    for r in cands:
        cands[r].sort(key=_rank)
    all_meshes.sort(key=lambda m: (m["name"].lower(), m["path"]))
    return {"content": str(content), "folders_scanned": scanned, "candidates": cands, "all_meshes": all_meshes,
            "proposal": {r: [c["path"] for c in cs[:PROPOSE_MAX]] if ROLES[r][1] == "mesh" and len(cs) > 1 else cs[0]["path"]
                         for r, cs in cands.items() if cs}}


def _exists(content: Path, ue_path: str) -> bool:
    return (content / ue_path.split(".")[0].removeprefix("/Game/")).with_suffix(".uasset").is_file()


def rescan(store: ProjectStore, log=print) -> dict:
    """Every build with Megascans ticked: roles mapped to assets that are gone, or never mapped, take
    the scan's proposal; the user's own mapping stays, including a role set to none (""). Saves and
    returns the library."""
    lib = dict(_unreal(store).dressing_library)
    s = scan(store)
    content = Path(s["content"])
    changed = []
    for r in ROLES:
        have = assets_of(lib.get(r))
        if have and all(a.startswith(OWN) for a in have):     # an earlier scan took the pipeline's own: not a choice
            lib.pop(r)
            have = []
        if r in lib and (not have or any(_exists(content, a) for a in have)):
            if len(have) > 1:          # drop the ones that are gone, keep the rest
                lib[r] = [a for a in have if _exists(content, a)]
            continue
        new = s["proposal"].get(r)
        if new != lib.get(r):
            changed.append(r)
            if new:
                lib[r] = new
            else:
                lib.pop(r, None)
    if changed:
        save(store, library=lib)
        log(f"dressing: Megascans mapping updated for {', '.join(changed)}")
    return lib


def plugin_status() -> dict:
    """Is the Fab (or older Quixel Bridge) plugin in the user's Unreal install? With the text the
    Megascans tooltip shows."""
    from . import ue, wsl
    try:
        cmd = ue.editor_cmd()
    except FileNotFoundError:
        return {"found": None, "plugins": [], "help": "Unreal isn't set up yet (Setup)."}
    eng = Path(wsl.to_linux(str(cmd)) if wsl.is_windows_path(str(cmd)) else cmd).parents[2]
    found = []
    for sub in ("Plugins/Marketplace", "Plugins/Fab", "Plugins/Bridge", "Plugins/Editor", "Plugins/Enterprise", "Plugins"):
        d = eng / sub
        if not d.is_dir():
            continue
        for name in PLUGIN_NAMES:
            if list(d.glob(f"{name}.uplugin")) + list(d.glob(f"*/{name}.uplugin")) + list(d.glob(f"*/*/{name}.uplugin")):
                found.append(name)
    found = sorted(set(found))
    if found:
        hlp = ("Your Unreal has the " + " and ".join(found) + " plugin. To add Megascans: in Unreal open the Fab window "
               "(Window > Fab), find a pack and Add to Project; or on Windows in the Epic Games Launcher's Fab tab, "
               "Add to Project. Then click Find my assets.")
    else:
        hlp = ("This Unreal has no Fab or Quixel Bridge plugin (Linux builds ship without it). Add Megascans on another "
               "machine with the same engine version (Fab), then in Unreal there right-click the imported folder > "
               "Asset Actions > Migrate into this project's Content folder (or copy that folder over), and click "
               "Find my assets.")
    return {"found": bool(found), "plugins": found, "help": hlp, "engine": str(eng)}


# --- report ---------------------------------------------------------------------------------------

def summary(rec: dict | None, ue_rep: dict | None) -> dict:
    """What the build report shows: options that ran, instances per role, source per role."""
    if not rec:
        return {}
    o = rec["options"]
    ran = [k for k in ("ground", "rocks", "vegetation") if o.get(k)] + ([f"water ({o['water']})"] if o.get("water") != "off" else [])
    u = (ue_rep or {}).get("dressing") or {}
    used = u.get("sources") or {}
    src = {r: used.get(r) or (rec["sources"].get(r) or {}).get("source", "none") for r in rec.get("roles", [])}
    n_assets = {r: len(assets_of(s.get("assets") or s.get("asset"))) for r, s in rec["sources"].items()
                if s.get("source") == "megascans" and r in src and src[r] == "megascans"}
    return {"ran": ran, "assets": u.get("assets") or n_assets, "counts": u.get("counts") or rec.get("counts") or {}, "sources": src,
            "water": u.get("water"), "hidden": u.get("hidden", 0), "removed": u.get("removed", []),
            "placed": u.get("placed"), "warnings": rec.get("warnings", []) + u.get("warnings", [])}


def report_lines(d: dict) -> list[str]:
    if not d:
        return []
    if not d["ran"]:
        return ["dressing: off" + (f" (removed {', '.join(d['removed'])})" if d.get("removed") else "")]
    lines = [f"dressing: {', '.join(d['ran'])}" + ("" if d.get("placed") is not False else " (instances unchanged)")]
    by: dict[str, list] = {}
    for r, s in d["sources"].items():
        by.setdefault(s, []).append(r)
    if d["counts"]:
        lines.append("  instances: " + ", ".join(f"{r} {n:,}" for r, n in d["counts"].items() if n))
    lines.append("  sources: " + "; ".join(f"{s}: {', '.join(rs)}" for s, rs in sorted(by.items())))
    if d.get("assets"):
        lines.append("  assets used: " + ", ".join(f"{r} {n}" for r, n in d["assets"].items()))
    if d.get("hidden"):
        lines.append(f"  placeholder trees hidden in {d['hidden']} slots")
    if d.get("water"):
        lines.append(f"  water: {d['water']}")
    if d.get("removed"):
        lines.append(f"  removed: {', '.join(d['removed'])}")
    lines += [f"  warning: {w}" for w in d["warnings"]]
    return lines

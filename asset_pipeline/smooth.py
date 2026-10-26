"""Smooth shading and ground removal for generated models (TRELLIS.2), any project.

TRELLIS meshes come with vertices split along their UV seams and normals that shade faceted once a
renderer recomputes them (Unreal did: recompute_normals on, and glTF has no smoothing groups).
`fix_glb` welds the vertices and shades the mesh smooth by angle in Blender
(scripts/blender_fix_glb.py; [trellis] smooth_angle, 30 deg like the procedural builds), keeping
UVs, textures and materials; Unreal then imports the GLB's own normals (ap_import.keep_normals).

TRELLIS also often turns a reference view's cast shadow or floor into a thin slab under the model,
wider than what stands on it. `fix_glb` with `ground` cuts it off where it meets the walls and
re-seats the model at z=0 (scripts/blender_ground.py has the rules); plinths, podiums and steps,
which have height and match the footprint, stay. Thresholds: [trellis] ground_* settings.

Where it runs:
    every TRELLIS result, before it becomes a version (s5_3d.run): smoothing, then ground removal
    ([trellis] remove_ground);
    Clean up (scripts/blender_cleanup.py --smooth-angle, --ground);
    existing versions: `fix_version` writes the fixed model as a new version (it becomes the Active
    one when its source was): `smooth_version`, `ground_version`, or both in one pass; `fix_all`
    does every asset whose Active TRELLIS model still needs it. When no ground is found, no version
    is made and the source version records "no ground found".
    `ap library smooth|remove-ground PROJECT [ASSET ...] [--all]`, and the UI.
"""
import os
import shutil
from pathlib import Path

from . import blender, config, filestore
from .project import ProjectStore, read_json, write_json

SCRIPT = config.REPO_ROOT / "scripts" / "blender_fix_glb.py"
SUFFIX = "_smooth"
GROUND_SUFFIX = "_noground"
GROUND_KEYS = ("max_thickness", "min_overhang", "min_area")


def angle() -> float:
    return float(config.get("trellis.smooth_angle", 30))


def ground_settings() -> dict:
    """The slab thresholds (fractions: of the model's height, of its footprint's size and area)."""
    return {"max_thickness": float(config.get("trellis.ground_max_thickness", 0.05)),
            "min_overhang": float(config.get("trellis.ground_min_overhang", 0.06)),
            "min_area": float(config.get("trellis.ground_min_area", 0.04))}


def ground_on_build() -> bool:
    return bool(config.get("trellis.remove_ground", True))


def fix_glb(src: Path, dst: Path, angle_deg: float | None = None, ground: dict | None = None,
            log_path: Path | None = None) -> dict:
    """src -> dst, welded and shaded smooth by angle (angle_deg 0: shading kept), its ground slab
    removed when `ground` (thresholds) is given; returns the Blender report."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    job = dst.with_name(dst.stem + "_fixjob.json")
    report = dst.with_name(dst.stem + "_fixreport.json")
    write_json(job, {"in": str(src), "out": str(dst), "report": str(report),
                     "angle": angle() if angle_deg is None else float(angle_deg), "ground": ground})
    try:
        blender.run(SCRIPT, [job], log_path or dst.with_name("smooth.log"), "model fix")
        rep = read_json(report)
        if rep is None or not dst.is_file():
            raise RuntimeError(f"fixing {src.name} wrote no result")
        return rep
    finally:
        job.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def smooth_glb(src: Path, dst: Path, angle_deg: float | None = None, log_path: Path | None = None) -> dict:
    """src -> dst welded and shaded smooth by angle; returns the Blender report."""
    return fix_glb(src, dst, angle_deg, None, log_path)


def _smooth_stats(rep: dict) -> dict:
    return {k: rep[k] for k in ("angle", "welded", "sharp_edges", "seconds")}


def ground_line(g: dict | None) -> str:
    """The version card's line: "Ground removed: N faces" or "No ground found" ("" unchecked)."""
    if not g:
        return ""
    return f"Ground removed: {g['removed_faces']:,} faces" if g.get("removed_faces") else "No ground found"


def postprocess(glb: Path, stats_file: Path) -> dict | None:
    """A fresh TRELLIS result smoothed and its ground removed in place, as the settings say (its
    stats JSON records both). None when both are switched off."""
    a, ground = angle(), (ground_settings() if ground_on_build() else None)
    if a <= 0 and ground is None:
        return None
    tmp = glb.with_name(glb.stem + "_fixtmp.glb")
    rep = fix_glb(glb, tmp, max(a, 0), ground)
    tmp.replace(glb)
    stats = read_json(stats_file, default={}) or {}
    if a > 0:
        stats["smoothed"] = _smooth_stats(rep)
    if ground is not None:
        stats["ground"] = rep["ground"]
    write_json(stats_file, stats)
    return {k: stats[k] for k in ("smoothed", "ground") if k in stats}


def is_smoothed(store: ProjectStore, row: dict) -> bool:
    if row["kind"] != "trellis" or not row["files"]:
        return False
    glb = store.root / row["files"][0]
    return glb.stem.endswith(SUFFIX) or bool((read_json(glb.with_suffix(".json"), default={}) or {}).get("smoothed"))


def _stats(store: ProjectStore, row: dict) -> dict:
    return read_json((store.root / row["files"][0]).with_suffix(".json"), default={}) or {}


def ground_checked(store: ProjectStore, row: dict) -> bool:
    """A TRELLIS version whose ground was looked for already (removed, or none found)."""
    if row["kind"] != "trellis" or not row["files"]:
        return False
    return Path(row["files"][0]).stem.endswith(GROUND_SUFFIX) or "ground" in _stats(store, row)


def ground_info(store: ProjectStore, row: dict) -> dict | None:
    if row["kind"] != "trellis" or not row["files"]:
        return None
    return _stats(store, row).get("ground")


def fix_version(store: ProjectStore, asset: str, n: int | None = None, plan: str | None = None,
                smooth: bool = True, ground: bool = False, angle_deg: float | None = None) -> dict:
    """An existing TRELLIS version (default: the Active one) smoothed and/or with its ground removed,
    as a new version beside it: <stem>[_smooth][_noground].glb, .json, _input.png. It becomes the
    Active version when its source was. When ground removal alone finds no ground, no version is
    made: the source records it (its card says "No ground found") and n is None.
    Returns {asset, from, n, seconds, welded?, sharp_edges?, ground?}."""
    from . import versions
    plan = plan or versions.PLAN
    cur = versions.current(store, asset, plan)
    n = cur if n is None else n
    if n is None:
        raise ValueError(f"{asset}: no version to fix")
    row = versions.get(store, asset, n, plan)
    if row["kind"] != "trellis":
        raise ValueError(f"{asset} v{n} is {row['kind']}, not a TRELLIS model (procedural builds are smooth and "
                         f"stand on their own base already)")
    if not smooth and not ground:
        raise ValueError("nothing to do: ask for smoothing, ground removal or both")
    if smooth and is_smoothed(store, row):
        raise ValueError(f"{asset} v{n} is smoothed already")
    if ground and ground_checked(store, row):
        raise ValueError(f"{asset} v{n}: its ground was removed or looked for already")
    src = store.root / row["files"][0]
    if not src.is_file():
        raise FileNotFoundError(f"{asset} v{n}: {src} is missing")
    tmp = src.with_name(src.stem + "_fixtmp.glb")
    rep = fix_glb(src, tmp, (angle_deg if angle_deg is not None else angle()) if smooth else 0,
                  ground_settings() if ground else None, src.parent / "smooth.log")
    g = rep.get("ground")
    stats = read_json(src.with_suffix(".json"), default={}) or {}
    if ground and not g.get("removed_faces") and not smooth:      # nothing to remove: no new version
        tmp.unlink(missing_ok=True)
        f, t = src.with_suffix(".json"), None
        if f.is_file():
            t = f.stat().st_mtime                     # result_time: the record doesn't make it newer
        write_json(f, stats | {"ground": g})
        if t is not None:
            os.utime(f, (t, t))
        store.log_run({"stage": "3d.ground", "asset": asset, "from": n, "n": None, "removed_faces": 0})
        return {"asset": asset, "from": n, "n": None, "seconds": rep["seconds"], "ground": g}
    removed = bool(g and g.get("removed_faces"))
    stem = src.stem + (SUFFIX if smooth else "") + (GROUND_SUFFIX if removed else "")
    dst = src.with_name(stem + ".glb")
    filestore.detach(dst)
    tmp.replace(dst)
    extra = {"from_version": n}
    new_stats = stats | {"glb": str(dst)}
    if smooth:
        new_stats["smoothed"] = extra | _smooth_stats(rep)
    if ground:
        new_stats["ground"] = extra | g
    write_json(dst.with_suffix(".json"), new_stats)
    src_input = store.root / (row["source"].get("input") or "")
    inp = src.with_name(stem + "_input.png")
    if src_input.is_file() and src_input != inp:
        shutil.copyfile(src_input, inp)
    filestore.intern(store, dst)
    rel = lambda p: p.relative_to(store.root).as_posix()  # noqa: E731
    new = versions.record_trellis(store, plan, asset, {
        "view": row["source"].get("view"), "mode": row["source"].get("mode"), "seed": row["source"].get("seed"),
        "glb": rel(dst), "input": rel(inp) if inp.is_file() else row["source"].get("input"), "stats": dst.with_suffix(".json").name})
    what = ([f"smooth shading ({rep['angle']:g} deg)"] if smooth else []) + \
           ([f"the ground removed ({g['removed_faces']:,} faces)"] if removed else [])
    rows = versions.load(store, asset, plan)
    for r in rows:
        if r["n"] == new["n"]:
            r["note"] = f"v{n} with " + ", ".join(what)
    versions._save(store, asset, rows, plan)
    if versions.current(store, asset, plan) != (new["n"] if cur == n else cur):    # record_trellis may have moved it
        versions.set_current(store, asset, new["n"] if cur == n else cur, plan)
    store.log_run({"stage": "3d.smooth" if smooth and not ground else "3d.ground" if not smooth else "3d.fix",
                   "asset": asset, "from": n, "n": new["n"], "seconds": rep["seconds"],
                   **({"removed_faces": g.get("removed_faces", 0)} if ground else {})})
    out = {"asset": asset, "from": n, "n": new["n"], "seconds": rep["seconds"]}
    if smooth:
        out |= {"welded": rep["welded"], "sharp_edges": rep["sharp_edges"]}
    if ground:
        out["ground"] = g
    return out


def smooth_version(store: ProjectStore, asset: str, n: int | None = None, plan: str | None = None,
                   angle_deg: float | None = None) -> dict:
    """An existing TRELLIS version (default: the Active one) smoothed as a new version beside it
    (fix_version). Returns {asset, from, n, seconds, welded, sharp_edges}."""
    return fix_version(store, asset, n, plan, smooth=True, angle_deg=angle_deg)


def ground_version(store: ProjectStore, asset: str, n: int | None = None, plan: str | None = None) -> dict:
    """An existing TRELLIS version (default: the Active one) with its ground removed, as a new
    version beside it (fix_version); n None when no ground was found."""
    return fix_version(store, asset, n, plan, smooth=False, ground=True)


def candidates(store: ProjectStore, plan: str | None = None, op: str = "smooth") -> list[str]:
    """Assets whose Active version is a TRELLIS model not smoothed yet (op "smooth"), or whose
    ground wasn't looked for yet (op "ground")."""
    from . import versions
    plan = plan or versions.PLAN
    out = []
    for a in versions.assets_of(store, plan):
        n = versions.current(store, a, plan)
        if n is None:
            continue
        row = versions.get(store, a, n, plan)
        if row["kind"] == "trellis" and not (is_smoothed if op == "smooth" else ground_checked)(store, row):
            out.append(a)
    return out


def fix_all(store: ProjectStore, assets: list[str] | None = None, plan: str | None = None, log=print,
            smooth: bool = True, ground: bool = False) -> dict:
    """Every asset given (default: all candidates) smoothed and/or with its ground removed, in one
    pass each (smoothing first); each asset gets only what it still needs. One failure doesn't stop
    the rest."""
    from . import versions
    plan = plan or versions.PLAN
    todo = assets if assets is not None else list(dict.fromkeys(
        (candidates(store, plan, "smooth") if smooth else []) + (candidates(store, plan, "ground") if ground else [])))
    done, failed = [], {}
    for i, a in enumerate(todo, 1):
        try:
            row = versions.get(store, a, versions.current(store, a, plan), plan)
            sm, gr = smooth and not is_smoothed(store, row), ground and not ground_checked(store, row)
            r = fix_version(store, a, plan=plan, smooth=sm, ground=gr)
            done.append(r)
            what = ", ".join(x for x in ("smoothed" if sm else "", ground_line(r.get("ground")).lower()) if x)
            log(f"{a} ({i} of {len(todo)}): {what}" + (f", v{r['from']} -> v{r['n']}" if r["n"] else "")
                + f", {r['seconds']} s")
        except Exception as e:  # noqa: BLE001 - per asset
            failed[a] = str(e).splitlines()[0]
            log(f"{a}: {failed[a]}")
    return {"done": done, "failed": failed}


def smooth_all(store: ProjectStore, assets: list[str] | None = None, plan: str | None = None, log=print) -> dict:
    """Every asset given (default: all candidates) smoothed; one failure doesn't stop the rest."""
    return fix_all(store, assets, plan, log, smooth=True, ground=False)

"""Smooth shading for generated models (TRELLIS.2), any project.

TRELLIS meshes come with vertices split along their UV seams and normals that shade faceted once a
renderer recomputes them (Unreal did: recompute_normals on, and glTF has no smoothing groups).
`smooth_glb` welds the vertices and shades the mesh smooth by angle in Blender
(scripts/blender_smooth_glb.py; [trellis] smooth_angle, 30 deg like the procedural builds), keeping
UVs, textures and materials; Unreal then imports the GLB's own normals (ap_import.keep_normals).

Where it runs:
    every TRELLIS result, before it becomes a version (s5_3d.run);
    Clean up (scripts/blender_cleanup.py --smooth-angle);
    existing versions: `smooth_version` writes the smoothed model as a new version (it becomes the
    Active one when its source was), `smooth_all` does every asset whose Active version is an
    unsmoothed TRELLIS model. `ap library smooth PROJECT [ASSET ...] [--all]`, and the UI.
"""
import shutil
from pathlib import Path

from . import blender, config, filestore
from .project import ProjectStore, read_json, write_json

SCRIPT = config.REPO_ROOT / "scripts" / "blender_smooth_glb.py"
SUFFIX = "_smooth"


def angle() -> float:
    return float(config.get("trellis.smooth_angle", 30))


def smooth_glb(src: Path, dst: Path, angle_deg: float | None = None, log_path: Path | None = None) -> dict:
    """src -> dst welded and shaded smooth by angle; returns the Blender report."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    job = dst.with_name(dst.stem + "_smoothjob.json")
    report = dst.with_name(dst.stem + "_smoothreport.json")
    write_json(job, {"in": str(src), "out": str(dst), "report": str(report),
                     "angle": angle() if angle_deg is None else float(angle_deg)})
    try:
        blender.run(SCRIPT, [job], log_path or dst.with_name("smooth.log"), "smooth shading")
        rep = read_json(report)
        if rep is None or not dst.is_file():
            raise RuntimeError(f"smoothing {src.name} wrote no result")
        return rep
    finally:
        job.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def postprocess(glb: Path, stats_file: Path) -> dict | None:
    """A fresh TRELLIS result smoothed in place (its stats JSON records it). None when switched off."""
    a = angle()
    if a <= 0:
        return None
    tmp = glb.with_name(glb.stem + "_smoothtmp.glb")
    rep = smooth_glb(glb, tmp, a)
    tmp.replace(glb)
    stats = read_json(stats_file, default={}) or {}
    stats["smoothed"] = {k: rep[k] for k in ("angle", "welded", "sharp_edges", "seconds")}
    write_json(stats_file, stats)
    return stats["smoothed"]


def is_smoothed(store: ProjectStore, row: dict) -> bool:
    if row["kind"] != "trellis" or not row["files"]:
        return False
    glb = store.root / row["files"][0]
    return glb.stem.endswith(SUFFIX) or bool((read_json(glb.with_suffix(".json"), default={}) or {}).get("smoothed"))


def smooth_version(store: ProjectStore, asset: str, n: int | None = None, plan: str | None = None,
                   angle_deg: float | None = None) -> dict:
    """An existing TRELLIS version (default: the Active one) smoothed as a new version beside it:
    <stem>_smooth.glb, .json, _input.png. It becomes the Active version when its source was.
    Returns {asset, from, n, seconds, welded, sharp_edges}."""
    from . import versions
    plan = plan or versions.PLAN
    cur = versions.current(store, asset, plan)
    n = cur if n is None else n
    if n is None:
        raise ValueError(f"{asset}: no version to smooth")
    row = versions.get(store, asset, n, plan)
    if row["kind"] != "trellis":
        raise ValueError(f"{asset} v{n} is {row['kind']}, not a TRELLIS model (procedural builds are smooth already)")
    if is_smoothed(store, row):
        raise ValueError(f"{asset} v{n} is smoothed already")
    src = store.root / row["files"][0]
    if not src.is_file():
        raise FileNotFoundError(f"{asset} v{n}: {src} is missing")
    stem = src.stem + SUFFIX
    dst = src.with_name(stem + ".glb")
    rep = smooth_glb(src, dst, angle_deg, src.parent / "smooth.log")
    stats = read_json(src.with_suffix(".json"), default={}) or {}
    write_json(dst.with_suffix(".json"), stats | {"glb": str(dst), "smoothed": {
        "from_version": n, **{k: rep[k] for k in ("angle", "welded", "sharp_edges", "seconds")}}})
    src_input = store.root / (row["source"].get("input") or "")
    inp = src.with_name(stem + "_input.png")
    if src_input.is_file():
        shutil.copyfile(src_input, inp)
    filestore.intern(store, dst)
    rel = lambda p: p.relative_to(store.root).as_posix()  # noqa: E731
    new = versions.record_trellis(store, plan, asset, {
        "view": row["source"].get("view"), "mode": row["source"].get("mode"), "seed": row["source"].get("seed"),
        "glb": rel(dst), "input": rel(inp) if inp.is_file() else row["source"].get("input"), "stats": dst.with_suffix(".json").name})
    rows = versions.load(store, asset, plan)
    for r in rows:
        if r["n"] == new["n"]:
            r["note"] = f"v{n} with smooth shading ({rep['angle']:g} deg)"
    versions._save(store, asset, rows, plan)
    if versions.current(store, asset, plan) != (new["n"] if cur == n else cur):    # record_trellis may have moved it
        versions.set_current(store, asset, new["n"] if cur == n else cur, plan)
    store.log_run({"stage": "3d.smooth", "asset": asset, "from": n, "n": new["n"], "seconds": rep["seconds"]})
    return {"asset": asset, "from": n, "n": new["n"], "seconds": rep["seconds"], "welded": rep["welded"],
            "sharp_edges": rep["sharp_edges"]}


def candidates(store: ProjectStore, plan: str | None = None) -> list[str]:
    """Assets whose Active version is a TRELLIS model not smoothed yet."""
    from . import versions
    plan = plan or versions.PLAN
    out = []
    for a in versions.assets_of(store, plan):
        n = versions.current(store, a, plan)
        if n is None:
            continue
        row = versions.get(store, a, n, plan)
        if row["kind"] == "trellis" and not is_smoothed(store, row):
            out.append(a)
    return out


def smooth_all(store: ProjectStore, assets: list[str] | None = None, plan: str | None = None, log=print) -> dict:
    """Every asset given (default: all candidates) smoothed; one failure doesn't stop the rest."""
    todo = assets if assets is not None else candidates(store, plan)
    done, failed = [], {}
    for i, a in enumerate(todo, 1):
        try:
            done.append(smooth_version(store, a, plan=plan))
            log(f"smoothed {a} ({i} of {len(todo)}): v{done[-1]['from']} -> v{done[-1]['n']}, {done[-1]['seconds']} s")
        except Exception as e:  # noqa: BLE001 - per asset
            failed[a] = str(e).splitlines()[0]
            log(f"{a}: {failed[a]}")
    return {"done": done, "failed": failed}

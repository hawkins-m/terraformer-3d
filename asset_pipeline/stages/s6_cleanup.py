"""Stage 6: a 3D result -> a cleaned GLB in headless Blender (scripts/blender_cleanup.py).

Every asset: uniform scale to the plan's real-world size (height fit by default), pivot at
the base centre, transforms applied. Game-tagged assets also get decimated to a triangle
budget for their category with base colour, roughness and normal maps baked from the
original. Cine and hero assets keep the full mesh and textures. Blender runs on the CPU
(Workbench/Cycles CPU), so this never competes with GPU jobs.

Layout under the project:
    cleanup/<plan>/<asset id>/<source stem>_<usage>.glb + .json (report)
"""
import json
import time
from pathlib import Path

from .. import blender, config, smooth
from ..project import ProjectStore
from . import s1_plan, s5_3d

OUT = "cleanup"
BLENDER = None   # None: blender.blender_bin() (tests patch this with a fake)
SCRIPT = config.REPO_ROOT / "scripts" / "blender_cleanup.py"
# Game triangle budgets by plan category (the bake carries the detail). Measured
# 2026-10-05 in Cycles: a market stall (thin posts, awning) warped at 15k and was close to
# the original at 30k; a house was fine at 30k. Others are starting points.
BUDGETS = {"prop": 10000, "structure": 30000, "building": 30000, "vegetation": 15000,
           "rock": 8000, "vehicle": 25000, "terrain": 10000, "other": 12000}


def out_dir(store: ProjectStore, plan: str, asset_id: str) -> Path:
    return store.root / OUT / plan / asset_id


def _asset(store: ProjectStore, plan: str, asset_id: str):
    p = s1_plan.load(store, plan)
    if not p:
        raise FileNotFoundError(f"no plan {plan}")
    for a in p.assets:
        if a.id == asset_id:
            return a
    raise ValueError(f"no asset {asset_id!r} in plan {plan}")


def run(store: ProjectStore, plan: str, asset_id: str, glb: str | None = None,
        fit: str = "height", budget: int | None = None, texture_size: int = 2048) -> dict:
    """Clean the asset's 3D model: for a library asset the version in use (versions.current_glb),
    else the newest 3D result; or `glb`, project-relative. Returns the report."""
    if fit not in ("height", "geomean"):
        raise ValueError("fit must be height or geomean")
    a = _asset(store, plan, asset_id)
    cur = None
    if not glb and plan == "library":
        from .. import versions
        cur = versions.current_glb(store, asset_id, plan)
    if glb:
        src = store.root / glb
    elif cur is not None:
        src = cur
    else:
        results = s5_3d.results(store, plan, asset_id)
        if not results:
            raise ValueError(f"{plan}/{asset_id}: no 3D result yet (Make 3D first)")
        src = store.root / results[0]["glb"]
    if not src.is_file():
        raise FileNotFoundError(f"no such GLB: {src}")
    d = a.dimensions
    retopo = (budget or BUDGETS.get(a.category, 10000)) if a.usage == "game" else 0
    out = out_dir(store, plan, asset_id)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"{src.stem}_{a.usage}.glb"
    report = dst.with_suffix(".json")
    args = ["--in", src, "--out", dst, "--report", report,
            "--dims", f"{d.width},{d.depth},{d.height}", "--fit", fit,
            "--retopo", retopo, "--texture-size", texture_size,
            "--smooth-angle", str(float(config.get("trellis.smooth_angle", 30)))]
    if smooth.ground_on_build():
        args += ["--ground", ",".join(str(v) for v in smooth.ground_settings().values())]
    t = time.time()
    blender.run(SCRIPT, args, out / "blender.log", f"cleanup of {plan}/{asset_id}", exe=BLENDER)
    if not report.is_file():
        tail = (out / "blender.log").read_text().splitlines()[-15:]
        raise RuntimeError("blender wrote no report:\n" + "\n".join(tail))
    rep = json.loads(report.read_text()) | {"usage": a.usage, "glb": dst.relative_to(store.root).as_posix(),
                                            "source": src.relative_to(store.root).as_posix(),
                                            # what this run asked for (Views and 3D's Cleanup details)
                                            "fit": fit, "budget": retopo or None,
                                            "bake_size": texture_size if retopo else None,
                                            "textures": texture_sizes(dst)}
    report.write_text(json.dumps(rep, indent=2))
    if plan == "library":                 # the version it came from records the step (Views and 3D's ticks)
        from .. import versions
        versions.note_cleaned(store, asset_id, src, a.usage, dst)
    store.log_run({"stage": "cleanup", "plan": plan, "asset": asset_id, "usage": a.usage,
                   "faces": rep["output_faces"], "warnings": rep["warnings"],
                   "seconds": round(time.time() - t, 1)})
    return rep


_TEX_CACHE: dict[tuple, list | None] = {}


def texture_sizes(glb: Path) -> list[int] | None:
    """The largest texture in a GLB, [w, h] (None: no embedded image, or unreadable). Read from the
    image headers only; cached per file and mtime."""
    import io
    import struct

    from PIL import Image
    try:
        key = (str(glb), glb.stat().st_mtime)
    except OSError:
        return None
    if key in _TEX_CACHE:
        return _TEX_CACHE[key]
    best = None
    try:
        data = glb.read_bytes()
        n = struct.unpack_from("<I", data, 12)[0]
        js = json.loads(data[20:20 + n])
        off = 20 + n
        binb = data[off + 8:off + 8 + struct.unpack_from("<I", data, off)[0]] if off < len(data) else b""
        for im in js.get("images", []):
            bv = js["bufferViews"][im["bufferView"]] if "bufferView" in im else None
            if bv is None:
                continue
            o = bv.get("byteOffset", 0)
            w, h = Image.open(io.BytesIO(binb[o:o + bv["byteLength"]])).size
            if best is None or w * h > best[0] * best[1]:
                best = [w, h]
    except Exception:   # noqa: BLE001  (a details line: an odd file just shows none)
        best = None
    _TEX_CACHE[key] = best
    return best


def details(store: ProjectStore, glb: str) -> dict | None:
    """A Clean up output's details from its report, only what was recorded (older reports have no
    budget or fit; their texture size is read from the output GLB): {tier, faces_in, faces_out,
    budget, method, textures [w, h], bake_size, dims_m, pivot, fit}; None without a report."""
    p = store.root / glb
    rep = json.loads(p.with_suffix(".json").read_text()) if p.with_suffix(".json").is_file() else None
    if rep is None:
        return None
    tex = rep.get("textures") if "textures" in rep else texture_sizes(p)
    out = {"tier": rep.get("usage"), "faces_in": rep.get("input_faces"), "faces_out": rep.get("output_faces"),
           "budget": rep.get("budget"), "method": rep.get("retopo_method"), "textures": tex,
           "bake_size": rep.get("bake_size"), "dims_m": rep.get("output_dims_m"),
           "pivot": "base centre" if "base_z" in rep else None, "fit": rep.get("fit")}
    return {k: v for k, v in out.items() if v is not None}


def results(store: ProjectStore, plan: str, asset_id: str) -> list[dict]:
    """Cleanup reports for an asset, newest first."""
    out = out_dir(store, plan, asset_id)
    reps = []
    for f in sorted(out.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        rep = json.loads(f.read_text())
        if (store.root / rep.get("glb", "")).is_file():
            reps.append({k: rep.get(k) for k in ("glb", "source", "usage", "output_faces", "output_dims_m",
                                                  "dims_vs_plan", "warnings", "retopo_method", "seconds")})
    return reps


def _choice_path(store: ProjectStore, plan: str, asset_id: str) -> Path:
    return store.root / OUT / plan / f"{asset_id}.chosen.json"      # beside the asset's folder, not among its reports


def choices(store: ProjectStore, plan: str, asset_id: str) -> dict:
    """The Clean up outputs the user chose for the city build, per tier: {usage: project-relative GLB}.
    Nothing is chosen unless the user picked it (Use this version on a cleaned card)."""
    try:
        return json.loads(_choice_path(store, plan, asset_id).read_text())
    except (OSError, ValueError):
        return {}


def choose(store: ProjectStore, plan: str, asset_id: str, glb: str) -> dict:
    """Make this Clean up output the one the city build uses for its tier."""
    rep = next((r for r in results(store, plan, asset_id) if r["glb"] == glb), None)
    if rep is None:
        raise ValueError("no such Clean up output")
    c = choices(store, plan, asset_id) | {rep["usage"]: glb}
    _choice_path(store, plan, asset_id).write_text(json.dumps(c, indent=2))
    return rep


def unchoose(store: ProjectStore, plan: str, asset_id: str, usage: str | None = None) -> None:
    """Back to the raw model for the tier (all tiers without `usage`)."""
    c = {} if usage is None else {k: v for k, v in choices(store, plan, asset_id).items() if k != usage}
    p = _choice_path(store, plan, asset_id)
    if c:
        p.write_text(json.dumps(c, indent=2))
    else:
        p.unlink(missing_ok=True)


def is_chosen(store: ProjectStore, plan: str, asset_id: str, glb: str) -> bool:
    return glb in choices(store, plan, asset_id).values()


def delete(store: ProjectStore, plan: str, asset_id: str, glb: str) -> dict:
    """Delete a Clean up output (its GLB and report). When the city build used it, the asset goes back
    to its raw model first. Returns {deleted, was_used}."""
    d = out_dir(store, plan, asset_id).resolve()
    p = (store.root / glb).resolve()
    if p.parent != d or p.suffix != ".glb" or not p.is_file():
        raise ValueError("not a Clean up output of this asset")
    used = is_chosen(store, plan, asset_id, glb)
    if used:
        for k, v in list(choices(store, plan, asset_id).items()):
            if v == glb:
                unchoose(store, plan, asset_id, k)
    p.with_suffix(".json").unlink(missing_ok=True)
    p.unlink()
    return {"deleted": glb, "was_used": used}


def chosen(store: ProjectStore, plan: str, asset_id: str, source: str) -> dict | None:
    """The Clean up output of `source` the user chose for the asset's tier ({glb, usage, faces, method}),
    else None: the city build takes the raw model unless a cleaned result was picked."""
    try:
        usage = _asset(store, plan, asset_id).usage
    except (FileNotFoundError, ValueError):
        return None
    want = choices(store, plan, asset_id).get(usage)
    for r in results(store, plan, asset_id):
        if want and r["glb"] == want and r.get("source") == source and r.get("usage") == usage:
            return {"glb": r["glb"], "usage": usage, "faces": r.get("output_faces"), "method": r.get("retopo_method")}
    return None


def for_source(store: ProjectStore, plan: str, asset_id: str, source: str) -> dict | None:
    """The newest Clean up output of `source` (a project-relative GLB, e.g. the Active version's) for
    the asset's own tier (its plan usage: game, cine, hero): {glb, usage, faces, method}; None when
    there is none, or the asset isn't in the plan."""
    try:
        usage = _asset(store, plan, asset_id).usage
    except (FileNotFoundError, ValueError):
        return None
    for r in results(store, plan, asset_id):
        if r.get("source") == source and r.get("usage") == usage:
            return {"glb": r["glb"], "usage": usage, "faces": r.get("output_faces"), "method": r.get("retopo_method")}
    return None


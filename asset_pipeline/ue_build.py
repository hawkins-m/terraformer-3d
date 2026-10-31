"""`ap ue build`: the whole planned city in the Unreal city level, headless, in one command.

    refuse if the editor has the project open -> backup -> export with library builds
    (city_library; Approved, or Active with use_active) -> terrain assets when the outer ring
    changed -> import (layout kept: only stale slots move back, unless reset_layout; prune with
    the expected count from the level's own read-back) with the project-default light
    (look.city_lighting) -> optional shot renders -> one short report, ending with the
    placement check (placement_check: sunk, floating, moved, off-slot or overlapping library
    actors, or "placement OK").

Its time estimate counts the work before it starts (queued_work: missing height variants,
changed assets to import); each build records what its parts took (estimates.ue_build_stages).

Renders go on the GPU ComfyUI doesn't use. Heavy Nanite scenes have lost the device on GPU 0
(docs/notes/unreal.md), so the city renders on GPU 1, and only while ComfyUI is down there;
otherwise the render step refuses with the reason (the rest of the build stands).
"""
import time

from . import landscape, layout_history, ue
from .project import ProjectStore, collecting_warnings, read_json, write_json
from .stages import s7_export, sw_site

def _vision_gpu() -> int:
    from . import config
    return int(config.backends().get("jobs", {}).get("gpu0_gpu", 0))


def render_gpu_index() -> int:
    """The city renders on ComfyUI's GPU ([jobs] comfy_gpu), only while ComfyUI is down."""
    from . import config
    return int(config.backends().get("jobs", {}).get("comfy_gpu", 1))


def _level_actors(store: ProjectStore, map_: str) -> tuple[dict[str, dict], set[str]]:
    """The level's pipeline slot actors {id: UE transform} and shot ids (a level not built yet:
    none)."""
    if not (ue.project_dir(store) / "Content" / map_.removeprefix("/Game/")).with_suffix(".umap").is_file():
        return {}, set()
    rep = ue.run_script(store, "ap_pull_layout.py", {"map": map_}, "UE layout read")
    if "error" in rep:
        raise RuntimeError(f"UE layout read failed: {rep['error']}")
    mine = [a for a in rep["actors"] if a["tags"].get("ap:source") == "pipeline"]
    return ({a["tags"]["ap:id"]: a["transform"] for a in mine if "ap:id" in a["tags"]},
            {a["tags"]["ap:shot"] for a in mine if "ap:shot" in a["tags"]})


def report_path(store: ProjectStore):
    """The last build's report, as the UI shows it (written by the UI's build job)."""
    return ue.work_dir(store) / "ap_build_report.json"


def expected_prune(ue_slots, ue_shots, man: dict) -> int:
    """How many pipeline actors the import will find missing from the manifest: the count the
    prune check needs to delete them headless."""
    return len(set(ue_slots) - {s["id"] for s in man["slots"]}) + len(ue_shots - {s["id"] for s in man["shots"]})


def render_gpu(comfy_up: bool, vram: dict[int, float]) -> tuple[int | None, str]:
    """(gpu, why): ComfyUI's GPU when ComfyUI is down and its VRAM is free; None with the reason otherwise."""
    RENDER_GPU = render_gpu_index()
    if comfy_up:
        return None, (f"render skipped: ComfyUI is running on GPU {RENDER_GPU}, the only GPU safe for the city "
                      f"(heavy Nanite scenes lose the device on GPU {_vision_gpu()}). Run `ap ue build --render` again once ComfyUI is down.")
    used = vram.get(RENDER_GPU, 99.0)
    if used > ue.VRAM_LIMIT_GB:
        return None, f"render skipped: GPU {RENDER_GPU} has {used:.1f} GB of other work in use"
    return RENDER_GPU, f"rendering on GPU {RENDER_GPU} (VRAM in use: " + ", ".join(
        f"GPU {k} {v:.1f} GB" for k, v in sorted(vram.items())) + ")"


# the stages the UI shows (Jobs panel), in run order, and the build step each one covers
STAGES = ["backup", "export", "terrain", "dressing", "lighting", "import", "check"]
STAGE_OF = {"backup": "backup", "export": "export", "terrain assets": "terrain", "landscape": "terrain", "dressing": "dressing", "lighting": "lighting",
            "level read": "import", "import": "import", "check": "check", "render": "render"}


def generated_landscape(store: ProjectStore, log=print) -> dict:
    """A generated terrain's Landscape for the import: the APLandscape plugin in the project, the
    auto material (MI_AP_TerrainAuto) when missing or older, the raw heights; added to the manifest
    as "landscape_build" (unreal/ap_import.py place_landscape)."""
    from . import terrain_build
    ue.install_plugin(store, ue.LANDSCAPE_PLUGIN, log)
    done = read_json(store.root / terrain_build.FAR_DIR / "ue_assets.json", default={}) or {}
    mi = ue.project_dir(store) / "Content/AP/Terrain/MI_AP_TerrainAuto.uasset"
    if not mi.is_file() or done.get("version") != terrain_build.TERRAIN_MAT_VERSION:
        terrain_build.ue_assets(store)
    out = s7_export.export_dir(store)
    rec = landscape.export(store, out)
    man = s7_export.load_manifest(store)
    man["landscape_build"] = rec
    write_json(out / "manifest.json", man)
    return rec


def terrain_due(store: ProjectStore) -> bool:
    """The outer ring's Unreal assets need (re)making: built, and newer than what Unreal has, or
    made with an older terrain material (terrain_build.TERRAIN_MAT_VERSION)."""
    from . import terrain_build
    glb = store.root / terrain_build.FAR_DIR / "outer.glb"
    done = store.root / terrain_build.FAR_DIR / "ue_assets.json"
    ring = ue.project_dir(store) / "Content/AP/Terrain/SM_OuterRing.uasset"
    old = (read_json(done, default={}) or {}).get("version", "1") != terrain_build.TERRAIN_MAT_VERSION
    return glb.is_file() and (not ring.is_file() or not done.is_file() or old or glb.stat().st_mtime > done.stat().st_mtime)


def queued_work(store: ProjectStore, use_active: bool = False, render: bool = False, unplaced: dict | None = None) -> dict:
    """The work a build would do, counted before it starts (its time estimate): {backup, terrain,
    height_variants (missing ones Blender builds), imports (assets whose GLB changed since the
    last import: ue.record_imported; everything the first time), first_import, shots}, and
    error when the library plan couldn't be counted. Builds nothing, writes nothing."""
    from . import city_library, vertical
    w = {"backup": ue.project_dir(store).is_dir(), "terrain": terrain_due(store), "height_variants": 0,
         "imports": 0, "first_import": False, "shots": 0}
    try:
        gb = sw_site.load_greybox(store)
        w["shots"] = len(gb.get("shots") or []) if render else 0
        index = read_json(s7_export.export_dir(store) / "mesh_index.json", default=None)
        fitter = vertical.Fitter(store, log=lambda *a: None, dry=True)
        pl = city_library.plan(store, gb, use_active, index, fitter=fitter, log=lambda *a: None,
                               **({"unplaced": unplaced} if unplaced is not None else {}))
        missing = set(fitter.missing)
        prev = read_json(ue.imported_path(store), default=None)
        held = (prev or {}).get("library") or {}
        seen, n = set(), 0
        for p in pl["placed"]:
            aid = city_library.asset_id(p.get("mesh") or p["key"])
            if aid in seen:
                continue
            seen.add(aid)
            src = store.root / p["glb"]
            n += p["glb"] in missing or not src.is_file() or held.get(aid) != ue.file_sha(src)
        if prev is None:                         # the first import: the stand-in meshes too
            n += len({v["id"] for v in (index or {}).values()})
        w |= {"height_variants": len(missing), "imports": n, "first_import": prev is None}
    except Exception as e:  # noqa: BLE001 - an estimate never stops a build
        w["error"] = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
    return w


def _record_times(store: ProjectStore, steps: dict, lib: dict, rep: dict) -> None:
    """What this build's parts took, for the next one's estimate (estimates.ue_build_stages):
    seconds per height variant, per imported asset, and each stage's fixed rest."""
    from . import estimates
    hv_n, hv_s = len(lib.get("height_variants_built") or []), float(lib.get("height_variant_s") or 0)
    secs = rep.get("import_seconds") or {}
    imp_s = float(sum(secs.values()))
    by: dict[str, float] = {}
    for name, s in steps.items():
        by[STAGE_OF[name]] = by.get(STAGE_OF[name], 0.0) + s
    by.pop("render", None)
    if "export" in by:
        by["export"] -= hv_s
    if "import" in by:
        by["import"] -= imp_s
    slug = store.root.name
    if hv_n and hv_s:
        estimates.record_time("ue.height_variant", hv_n, hv_s, slug)
    if secs:
        estimates.record_time("ue.import_asset", len(secs), imp_s, slug)
    for st, s in by.items():
        estimates.record_time(f"ue.build.{st}", 1, max(0.0, s), slug)


def build(store: ProjectStore, **kw) -> dict:
    """_build (below), with the warnings readers raise on the way (project.warn: a truncated image
    made again or skipped, ...) added to the report's instead of failing the build."""
    with collecting_warnings() as extra:
        out = _build(store, **kw)
    out["warnings"] += [w for w in extra if w not in out["warnings"]]
    return out


def _build(store: ProjectStore, use_active: bool = False, reset_layout: bool = False, render: bool = False,
           log=print, on_step=None, progress: dict | None = None, dressing=None, unplaced: dict | None = None) -> dict:
    """dressing: schema.DressingOptions for this build (remembered for the project); None = the
    project's last-used ones. unplaced: {avoid_locked, seed} to also place built assets the plan
    has no slot for (unplaced.py); None = the plan's own assets only. on_step(name): called as
    each step starts (the UI job's stage). Under a UI job, Stop
    is checked between steps (the editor and Blender runs stop at once: blender.run_logged).
    progress: a dict (the UI job's tag) kept up to date with stage_t0 (the current stage's
    start), hv_started and hv_t0 (height variants started, the first one's start)."""
    from . import dressing as dress
    from . import look, terrain_build, vertical
    from .jobs import check_canceled
    t0 = time.time()
    ue.check_target(store)                      # never another project's Unreal folder or backups
    up = ue.uproject(store)
    if ue.editor_running(up):
        raise RuntimeError(f"the Unreal editor has {up.name} open: close it first (ap ue build writes the city level)")
    warnings: list[str] = []
    steps: dict[str, float] = {}
    if progress is not None:
        outer_log = log

        def log(msg):
            if vertical.HV_LOG in str(msg):
                progress.setdefault("hv_t0", time.time())
                progress["hv_started"] = progress.get("hv_started", 0) + 1
            outer_log(msg)

    def step(name, fn):
        check_canceled()
        if on_step:
            on_step(name)
        if progress is not None and progress.get("stage_name") != STAGE_OF[name]:
            progress.update(stage_name=STAGE_OF[name], stage_t0=time.time())
        t = time.time()
        log(f"{name}…")
        out = fn()
        steps[name] = round(time.time() - t, 1)
        return out

    if ue.project_dir(store).is_dir():
        step("backup", lambda: ue.backup(store))
    c = step("export", lambda: s7_export.export(store, assets=True, use_active=use_active, log=log,
                                                 **({"unplaced": unplaced} if unplaced is not None else {})))
    man = s7_export.load_manifest(store)
    if terrain_due(store):
        step("terrain assets", lambda: terrain_build.ue_assets(store))
    elif not (store.root / terrain_build.FAR_DIR / "outer.glb").is_file() and not landscape.wanted(store):
        warnings.append("no outer ring built (ap terrain outer): the terrain-outer greybox stays")
    if landscape.wanted(store):                 # a generated terrain: a Landscape of its own (heightmap projects keep theirs)
        lrec = step("landscape", lambda: generated_landscape(store, log))
        log(f"landscape: {lrec['size'][0]} x {lrec['size'][1]} samples, {lrec['spacing_m']} m apart")
    opts = dressing if dressing is not None else dress.options(store)
    dress.save(store, opts)
    drec = step("dressing", lambda: dress.prepare(store, opts, log=log))
    light = step("lighting", lambda: look.city_lighting(store))
    ue_slots, ue_shots = step("level read", lambda: _level_actors(store, man["map"]))
    reset_only = None
    if not reset_layout and ue_slots:            # default: only stale slots move back; hand moves stay
        current = {s["id"]: s["matrix"] for s in sw_site.load_greybox(store)["slots"]}
        cls = layout_history.classify(ue_slots, current, layout_history.greybox_versions(store, log=lambda *a: None))
        reset_only = sorted(cls["stale"])
        if cls["hand"]:
            log(f"kept {len(cls['hand'])} slot(s) moved by hand in Unreal")
    expect = expected_prune(ue_slots, ue_shots, man)
    rep = step("import", lambda: ue.import_(store, reset_layout=reset_layout or reset_only is not None, prune=True,
                                            expect=expect, reset_only=reset_only, lighting=light,
                                            dressing=s7_export.export_dir(store) / "dressing.json"))
    warnings += rep.get("warnings", [])
    if rep.get("prune_refused") is not None:
        warnings.append(f"prune refused: {rep['prune_refused']} orphans, expected {expect}")
    if rep.get("lighting_kept"):
        warnings.append(f"light settings changed in Unreal and kept: {', '.join(rep['lighting_kept'])}")
    if not rep.get("landscape"):
        warnings.append("no Landscape in the level (optional, manual: USAGE.md 'Terrain beyond the city'); "
                        "the terrain is the Nanite mesh")
    if rep.get("sample_errors"):
        warnings += [f"placement check: {e}" for e in rep["sample_errors"][:5]]
    placement = None
    if rep.get("library_actors") is not None:     # the placement check, every build
        from . import city_library, placement_check
        from .ground import Ground
        gb = sw_site.load_greybox(store)
        placement = step("check", lambda: placement_check.check(
            s7_export.load_manifest(store), rep["library_actors"], rep.get("slot_instances") or {},
            {s["id"]: s["bbox"] for s in gb["slots"]}, Ground.from_project(store), city_library.settings(store)))
    shots = None
    if render:
        gpu, why = render_gpu(look.comfy_running(), ue.gpu_vram())
        log(why)
        if gpu is None:
            warnings.append(why)
        else:
            shots = step("render", lambda: ue._render(store, man["map"], [{"id": s["id"], "resolution": s["resolution"]}
                                                                          for s in man["shots"]],
                                                       ue.work_dir(store) / "shots", "ap_shot_render", 5400, adapter=gpu))
    lib = man.get("library") or {}
    grey = [s for s in man["slots"] if not s.get("replaced")]             # untouched: no build in them
    greybox_slots = sum(1 for s in grey if s.get("category") != "terrain")   # (streets, parks and ground counted apart)
    out = {"ok": bool(rep.get("ok")), "use_active": use_active, "placed": (rep.get("library") or {}).get("placed", 0),
           "props": lib.get("props", 0), "attached": lib.get("placed", 0) - lib.get("parents", 0) - lib.get("props", 0),
           "slots_replaced": lib.get("slots_replaced", 0), "no_model": lib.get("no_model", []),
           "greybox_slots": greybox_slots, "ground_slots": len(grey) - greybox_slots,
           "greybox_instances": c["instances"], "held": lib.get("held", 0), "held_why": lib.get("held_why") or {},
           "superseded": lib.get("superseded", 0),
           "held_entries": lib.get("held_entries", []), "imported": len(rep["assets"]["imported"]),
           "skipped": len(rep["assets"]["skipped"]), "reset": len(rep["actors"]["reset"]),
           "pruned": len(rep.get("pruned", [])), "terrain_outer": bool(rep.get("terrain_outer")),
           "shots": None if shots is None else len(shots.get("shots", [])), "warnings": warnings,
           "placement": placement, "active_models": lib.get("active_models", []), "stretch_fits": lib.get("stretch_fits", []),
           "unplaced": lib.get("unplaced"),
           "dressing": dress.summary(drec, rep),
           "steps": steps, "seconds": round(time.time() - t0, 1)}
    _record_times(store, steps, lib, rep)
    store.log_run({"stage": "ue.build", **{k: v for k, v in out.items() if k not in ("warnings", "held_entries", "placement", "active_models", "stretch_fits",
                                                                                    "dressing", "unplaced")},
                   "unplaced_swapped": None if out["unplaced"] is None else out["unplaced"]["placed"],
                   "placement_ok": None if placement is None else placement["ok"]})
    return out


STRETCH_NOTE = "models kept within 1.4:1; runs of copies, same-family swaps, else capped"


def report_lines(r: dict) -> list[str]:
    """The short report `ap ue build` prints."""
    extra = [f"{r.get('props', 0)} props"] * bool(r.get("props")) + [f"{r['attached']} attached pieces"] * bool(r["attached"])
    lines = [f"{'OK' if r['ok'] else 'PROBLEMS'}: {r['placed']} library actors placed"
             + (f" ({r['placed'] - r.get('props', 0) - r['attached']} builds, {', '.join(extra)})" if extra else "")
             + f" in {r['slots_replaced']} slots; {r['greybox_slots']} building slots still greybox"
             + (f" (+{r['ground_slots']} streets, parks and ground)" if r.get("ground_slots") else "")
             + (f", {r['held']} builds held back ("
                + (", ".join(f"{n} {w}" for w, n in r["held_why"].items()) if r.get("held_why") else "slot not fully built or not approved")
                + ")" if r["held"] else "")
             + (f"; {r['superseded']} builds not needed (a kit or another build stands in their slot)" if r.get("superseded") else ""),
             f"assets: {r['imported']} imported, {r['skipped']} unchanged; {r['reset']} stale slots moved back; "
             f"{r['pruned']} orphans pruned; outer ring {'placed' if r['terrain_outer'] else 'not placed'}"
             + (f"; {r['shots']} shots rendered" if r["shots"] is not None else ""),
             f"time: {r['seconds'] / 60:.1f} min (" + ", ".join(f"{k} {v:.0f} s" for k, v in r["steps"].items()) + ")"]
    lines += r.get("active_models") or []
    if r.get("stretch_fits"):
        lines.append(f"stretch cap ({STRETCH_NOTE}):")
        lines += [f"  {x}" for x in r["stretch_fits"]]
    u = r.get("unplaced")
    if u:
        lines.append(f"unplaced assets: {u['placed']} places taken ({u['swapped']} swapped, {u['open']} on open ground"
                     + (f"; {u['held']} in slots held back" if u.get("held") else "") + ")")
        lines += [f"  {x}" for x in u["lines"]]
        if u.get("no_spot_lines"):
            lines.append("open ground skipped (no spot clear of everything, even at 3/4 size):")
            lines += [f"  {x}" for x in u["no_spot_lines"]]
    from .dressing import report_lines as dressing_lines
    lines += dressing_lines(r.get("dressing") or {})
    if r.get("placement") is not None:
        from .placement_check import lines as check_lines
        lines += check_lines(r["placement"])
    lines += [f"warning: {w}" for w in r["warnings"]]
    return lines

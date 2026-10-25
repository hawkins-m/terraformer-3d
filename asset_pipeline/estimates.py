"""How long a job will take, from how long the same kind of job took before.

Every job that finishes successfully is recorded (record(), called by the job queue) in
$AP_ROOT/logs/job_times.jsonl: {time, kind, variant, items, seconds, project}. Times are machine
facts (this machine's GPUs and CPU), so the log is shared by all projects.

An estimate is items x seconds per item, where seconds per item is the median of the last
RECENT recorded runs of that kind (and variant: TRELLIS mode), each divided by its item count.
Without recorded runs it falls back to the stage timings in the projects' runs.jsonl (stages
log their own seconds, e.g. 3d.trellis, frames.generate), then to DEFAULTS (a guess, said so).

`ap ue build` (ue.build) is estimated per stage from the work counted before it starts
(ue_build.queued_work, the job's tag["work"]): missing height variants x seconds per Blender
build, changed assets x seconds per import, plus each stage's fixed part (ue_build_stages). Each
build records those parts itself (ue_build: record_time), with UE_BUILD_DEFAULTS until it has.

An "item" is what a job repeats: a frame, a sheet, a shot, an asset of a batch (items()).
Jobs run one at a time per GPU lane (CPU: a few slots), so a job waits behind the jobs ahead of
it in its lanes; wait() estimates that from their own estimates.
"""
import json
import statistics
import time
from pathlib import Path

from . import config

LONG_S = 600                    # over this a job asks before it starts (the UI's long-job dialog)
RECENT = 15                     # recorded runs per kind (and variant) the median is taken over

# seconds per item when nothing is recorded yet: rough guesses for this kind of machine
DEFAULTS = {
    "frames.generate": 50, "refs.generate": 40, "views.cut": 5, "shots.render": 15, "shots.camera": 10,
    "3d.trellis": 180, "library.trellis": 180, "cleanup": 60,
    "library.build": 60, "library.rebuild": 120, "library.brief": 150, "library.brief_build": 150,
    "library.reroll": 60, "library.guide": 60, "library.sheets": 80, "library.instances": 300,
    "library.plan": 30, "library.check": 120, "library.pending": 120, "library.silhouette": 120,
    "library.restore": 30, "library.assembly": 60,
    "site.build": 20, "site.extract": 20, "site.preview": 60, "site.plan": 10, "shots.add": 30,
    "site.vegetation": 30, "site.overlap": 30,
    "style.explore": 150, "style.derive": 60, "style.draft": 60, "plan.analyze": 80, "plan.refine": 20,
    "ue.build": 900, "ue.export": 60, "ue.backup": 30, "ue.init": 300, "ue.import": 120, "ue.render": 45, "ue.pull": 60,
    "ue.look": 300, "terrain.analyse": 60, "terrain.build": 180, "terrain.outer": 120, "terrain.apply": 60,
    "terrain.landscape": 300, "terrain.ue": 300, "demo": 1800, "vlm.up": 60, "vlm.down": 5,
}
# `ap ue build`'s parts (recorded by ue_build after each build): per height variant, per imported
# asset, and each stage's fixed part (Blender and editor start-up, the level read, the light...)
UE_BUILD_DEFAULTS = {"ue.height_variant": 20, "ue.import_asset": 4, "ue.build.backup": 30, "ue.build.export": 90,
                     "ue.build.terrain": 120, "ue.build.lighting": 5, "ue.build.import": 180, "ue.build.check": 10}
MODE_FACTOR = {"512": 0.6}      # TRELLIS 512 against 1024_cascade, until 512 runs are recorded
# job kind -> the stage name it logs in runs.jsonl, and what one logged run covers (its items)
RUN_STAGES = {
    "3d.trellis": ("3d.trellis", lambda r: 1), "library.trellis": ("3d.trellis", lambda r: 1),
    "frames.generate": ("frames.generate", lambda r: r.get("n") or 1),
    "refs.generate": ("refs.generate", lambda r: r.get("n") or 1),
    "library.sheets": ("refs.generate", lambda r: 1),                  # a batch item: one unit's sheets
    "shots.render": ("shots.render", lambda r: max(1, _count(r.get("shots")))),
    "views.cut": ("views.cut", lambda r: 1), "site.build": ("site.build", lambda r: 1),
    "ue.export": ("export", lambda r: 1), "ue.import": ("ue.import", lambda r: 1), "ue.build": ("ue.build", lambda r: 1),
    "ue.render": ("ue.render", lambda r: max(1, r.get("shots") if isinstance(r.get("shots"), int) else _count(r.get("shots")))),
}
# a job's lanes in the order it uses them (jobs.hop): the first is where it is submitted
HOPS = {"library.brief_build": ["gpu0", "cpu"], "library.brief": ["gpu0", "cpu"], "library.reroll": ["gpu0", "cpu"]}
COMFY_KINDS = {"frames.generate", "refs.generate", "views.cut", "library.sheets", "library.check", "style.explore",
               "style.derive", "plan.refine"}
GPU0_KINDS = {"3d.trellis", "library.trellis", "plan.analyze", "style.draft", "library.guide", "library.pending",
              "vlm.up", "vlm.down"}


def lane_of(kind: str) -> str:
    """The lane a kind is usually submitted to (the tooltips' table; the endpoint decides)."""
    return "comfy" if kind in COMFY_KINDS else "gpu0" if kind in GPU0_KINDS or kind in HOPS else "cpu"


def kinds() -> dict:
    """{kind or kind/variant: {per_item_s, source, lanes, lane_names}} for every known kind."""
    out = {}
    for k in DEFAULTS:
        for var in (("1024_cascade", "512") if k in ("3d.trellis", "library.trellis") else ("",)):
            s, src = per_item(k, var)
            ls = lanes(k, lane_of(k))
            out[f"{k}/{var}" if var else k] = {"per_item_s": round(s, 1), "source": src, "lanes": ls,
                                               "lane_names": [LANE_NAMES.get(x, x) for x in ls]}
    return out


LANE_NAMES = {"gpu0": "GPU 0", "comfy": "GPU 1 (ComfyUI)", "cpu": "the CPU", "service": "the ComfyUI service"}
BLOCKS = {
    "gpu0": "GPU 0 busy: no TRELLIS, Reroll, Brief & build or vision model until done",
    "comfy": "GPU 1 busy: no sheets, frames, SAM checks or style images until done",
    "cpu": "Takes a CPU slot: other Blender and Unreal jobs share the rest",
    "service": "ComfyUI can't be started or stopped meanwhile",
}


def _count(x) -> int:
    if isinstance(x, (list, tuple)):
        return len(x)
    if isinstance(x, str) and x.startswith("["):      # runs.jsonl stores long lists as text
        return x.count(",") + 1
    return 0


def log_path() -> Path:
    return config.ap_root() / "logs" / "job_times.jsonl"


def items(kind: str, tag: dict | None, store=None) -> int:
    """How many items a job with this tag repeats (1 when it doesn't say)."""
    t = tag or {}
    if kind == "frames.generate":
        return max(1, len(t.get("shots") or [1])) * int(t.get("n") or 4)
    if kind == "refs.generate":
        return max(1, len(t.get("units") or [1])) * int(t.get("n") or 2)
    if kind == "views.cut":
        return max(1, len(t.get("sheets") or [1]))
    if kind == "shots.render":
        return max(1, len(t.get("shots") or [1]))
    if kind == "ue.render":
        shots = t.get("shots")
        if not shots and store is not None:
            try:
                from .stages import sw_site
                shots = sw_site.load_greybox(store)["shots"]
            except (FileNotFoundError, KeyError, ValueError):
                shots = []
        return max(1, len(shots or [1]))
    return max(1, int(t.get("n") or 1))


def variant(kind: str, tag: dict | None) -> str:
    return str((tag or {}).get("mode") or "") if kind in ("3d.trellis", "library.trellis") else ""


def record(job, log=None) -> None:
    """Append a finished job's run time (successful jobs only)."""
    if job.status != "done" or not job.started or not job.finished:
        return
    record_time(job.kind, items(job.kind, job.tag), job.finished - job.started, job.project,
                variant(job.kind, job.tag), log)


def record_time(kind: str, n: int, seconds: float, project: str | None = None, var: str = "", log=None) -> None:
    """Append one run time: `n` items of `kind` took `seconds`."""
    rec = {"time": time.time(), "kind": kind, "variant": var, "items": n, "seconds": round(seconds, 1), "project": project}
    p = Path(log) if log else log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass                                          # an estimate is a nicety: never fail a job over it


_cache: dict = {}


def _read_jsonl(p: Path) -> list[dict]:
    try:
        st = p.stat()
    except FileNotFoundError:
        return []
    hit = _cache.get(p)
    if hit and hit[0] == (st.st_mtime_ns, st.st_size):
        return hit[1]
    rows = []
    with p.open() as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    _cache[p] = ((st.st_mtime_ns, st.st_size), rows)
    return rows


def per_item(kind: str, var: str = "", log=None, projects_dir: Path | None = None) -> tuple[float, str]:
    """(seconds per item, where it comes from)."""
    rows = [r for r in _read_jsonl(Path(log) if log else log_path()) if r.get("kind") == kind
            and (r.get("variant") or "") == var and r.get("seconds")]
    if rows:
        xs = [r["seconds"] / max(1, r.get("items") or 1) for r in rows[-RECENT:]]
        return statistics.median(xs), f"median of {len(xs)} recorded run{'s' if len(xs) != 1 else ''}"
    if kind in RUN_STAGES:
        stage, per = RUN_STAGES[kind]
        base = projects_dir or config.projects_dir()
        xs = []
        for p in sorted(base.glob("*/runs.jsonl")) if base.is_dir() else []:
            for r in _read_jsonl(p):
                if r.get("stage") == stage and r.get("seconds") and (not var or str(r.get("mode") or "") == var):
                    xs.append(r["seconds"] / max(1, per(r)))
        if xs:
            xs = xs[-RECENT * 2:]
            return statistics.median(xs), f"median of {len(xs)} logged stage run{'s' if len(xs) != 1 else ''} (runs.jsonl)"
    if kind in UE_BUILD_DEFAULTS:
        return float(UE_BUILD_DEFAULTS[kind]), "default guess (no builds recorded yet)"
    if kind.startswith("terrain.") and kind not in DEFAULTS:
        return 120.0, "default guess (no runs recorded yet)"
    if kind not in DEFAULTS:
        return 60.0, "default guess (unknown job kind)"
    return DEFAULTS[kind] * MODE_FACTOR.get(var, 1.0), "default guess (no runs recorded yet)"


def lanes(kind: str, lane: str) -> list[str]:
    return HOPS.get(kind, [lane]) if lane == HOPS.get(kind, [lane])[0] else [lane]


def estimate(kind: str, lane: str, tag: dict | None = None, store=None, queue=None, label: str | None = None) -> dict:
    """{kind, items, seconds, per_item_s, source, lanes, lane_names, blocks, long, wait_s, ahead}."""
    n = items(kind, tag, store)
    s, src = per_item(kind, variant(kind, tag))
    ls = lanes(kind, lane)
    out = {"kind": kind, "label": label or kind, "items": n, "per_item_s": round(s, 1), "seconds": round(n * s),
           "source": src, "lanes": ls, "lane_names": [LANE_NAMES.get(x, x) for x in ls],
           "blocks": [BLOCKS[x] for x in ls if x in BLOCKS]}
    if kind == "ue.build" and (tag or {}).get("work"):
        st = ue_build_stages(tag)
        total = sum(x["seconds"] for x in st)
        out |= {"per_item_s": round(total, 1), "seconds": round(total), "stages_est": st,
                "breakdown": breakdown(tag["work"]),
                "source": "recorded builds" if any(x["recorded"] for x in st) else "default guesses (no builds recorded yet)"}
    out["long"] = out["seconds"] > LONG_S
    out |= wait(queue, ls[0]) if queue is not None else {"wait_s": 0, "ahead": 0}
    return out


def ue_build_stages(tag: dict) -> list[dict]:
    """An `ap ue build`'s stages (tag["stages"]) with their estimates from the work queued
    (tag["work"]): [{stage, items, per_item_s, fixed_s, seconds, recorded}]. Export builds the
    missing height variants, Import imports the changed assets, Render renders the shots."""
    w = tag.get("work") or {}
    per = {"export": ("ue.height_variant", w.get("height_variants") or 0),
           "import": ("ue.import_asset", w.get("imports") or 0),
           "render": ("ue.render", w.get("shots") or 1)}
    out = []
    for st in tag.get("stages") or []:
        skip = (st == "backup" and w.get("backup") is False) or (st == "terrain" and not w.get("terrain"))
        fixed, fsrc = (0.0, "") if skip or st == "render" else per_item(f"ue.build.{st}")
        kind, n = per.get(st, (None, 0))
        each, esrc = per_item(kind) if kind and n and not skip else (0.0, "")
        out.append({"stage": st, "items": n if kind and not skip else 0, "per_item_s": round(each, 1),
                    "fixed_s": round(fixed, 1), "seconds": round(fixed + (n * each if kind else 0)),
                    "recorded": fsrc.startswith("median") or esrc.startswith("median")})
    return out


def breakdown(work: dict) -> str:
    """The queued work in a line: "~120 height variants to build, ~40 imports"."""
    if work.get("error"):
        return f"couldn't count the queued work ({work['error']}): stage defaults only"
    hv, imp = work.get("height_variants") or 0, work.get("imports") or 0
    parts = [f"~{hv} height variant{'s' * (hv != 1)} to build" if hv else "no height variants to build",
             f"~{imp} import{'s' * (imp != 1)}" + (" (first import: everything)" if work.get("first_import") and imp else "")
             if imp else "nothing changed to import"]
    if work.get("terrain"):
        parts.append("terrain assets")
    if work.get("shots"):
        parts.append(f"{work['shots']} shot{'s' * (work['shots'] != 1)} to render")
    return ", ".join(parts)


def stage_left(tag: dict, stage_elapsed: float, done: int | None = None, rate: float | None = None) -> float:
    """Seconds left of a running `ap ue build`: the current stage from its own progress (`done`
    of its items, at `rate` s each when measured this run; else its estimate less the time it
    has run), then the stages after it at their estimates."""
    st = ue_build_stages(tag)
    at = tag.get("stage", 0)
    if not st or at >= len(st):
        return 0.0
    cur = st[at]
    if done is not None and cur["items"]:
        each = rate if rate else cur["per_item_s"]
        fixed_left = max(0.0, cur["fixed_s"] - stage_elapsed) if not done else 0.0
        left = max(0, cur["items"] - done) * each + fixed_left
    else:
        left = max(0.0, cur["seconds"] - stage_elapsed)
    return left + sum(x["seconds"] for x in st[at + 1:])


def job_seconds(job, store=None) -> float:
    """A submitted job's own estimate (the Jobs panel's time left for jobs it can't count)."""
    if job.kind == "ue.build" and (job.tag or {}).get("work"):
        return float(sum(x["seconds"] for x in ue_build_stages(job.tag)))
    return items(job.kind, job.tag, store) * per_item(job.kind, variant(job.kind, job.tag))[0]


def wait(queue, lane: str) -> dict:
    """How long a new job on `lane` would wait: what the jobs ahead of it there have left."""
    now, left, ahead = time.time(), 0.0, 0
    for j in queue.active():
        if lane not in (j.holds or []) and j.lane != lane:
            continue
        ahead += 1
        est = getattr(j, "est_s", None) or job_seconds(j)
        left += max(0.0, est - (now - j.started)) if j.started else est
    slots = getattr(queue, "slots", {}).get(lane, 1) or 1
    return {"wait_s": round(left / slots), "ahead": ahead}

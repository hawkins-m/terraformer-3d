"""The helper processes the app's jobs start (TRELLIS, headless Unreal) and the local vision
model server, for Stop jobs: stop() ends them all and says what VRAM that freed.

A tracked process was started in its own session (start_new_session), so its process group is
its pid; stopping it signals the whole group (TRELLIS's python under its bash wrapper). The
vision model server is the one vlm.state() names (only servers this tool started). ComfyUI is
never touched here.
"""
import threading
import time
from pathlib import Path
from typing import Callable

from . import oscompat

_LIVE: dict[int, dict] = {}          # pgid -> {"what", "gpu"}
_lock = threading.Lock()


def track(pid: int, what: str, gpu: int | None = None) -> Callable[[], None]:
    """Record a helper process the app started; returns the function that forgets it (call it
    once the process has ended)."""
    with _lock:
        _LIVE[pid] = {"what": what, "gpu": gpu}

    def forget():
        with _lock:
            _LIVE.pop(pid, None)
    return forget


def group_pids(pgid: int) -> set[int]:
    """Live processes of a process group (from /proc; elsewhere just its leader, if alive)."""
    if not Path("/proc").is_dir():
        return {pgid} if oscompat.group_alive(pgid) else set()
    out = set()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        if fields[0] != "Z" and int(fields[2]) == pgid:
            out.add(int(d.name))
    return out


def loaded() -> list[dict]:
    """What Stop jobs would end: [{what, gpu, pid, vlm?}], the vision model server first."""
    from . import vlm
    out = []
    st = vlm.state()
    if st:
        out.append({"what": "vision model", "gpu": st.get("gpu"), "pid": st["pid"], "vlm": True})
    with _lock:
        out += [{"what": v["what"], "gpu": v["gpu"], "pid": pid} for pid, v in _LIVE.items()
                if pid > 1 and pid != oscompat.own_group()]
    return out


def snapshot(vram_fn=None) -> list[dict]:
    """loaded(), each with the VRAM its process group holds now ({gpu: GB}). Taken before the
    jobs are canceled: their cancel already stops TRELLIS and Unreal, and freed memory reads 0."""
    if vram_fn is None:
        from .gpu import vram_by_process as vram_fn
    targets = loaded()
    try:
        vram = vram_fn() if targets else {}
    except Exception:  # noqa: BLE001 - no GPU tool: stop them anyway, say nothing about VRAM
        vram = {}
    for t in targets:
        pids = group_pids(t["pid"]) | {t["pid"]}
        t["vram"] = {g: gb for g, by in vram.items() if (gb := sum(by.get(p, 0.0) for p in pids)) > 0}
    return targets


def _kill_group(pgid: int, wait_s: float = 10) -> None:
    for force in (False, True):
        try:
            oscompat.kill_tree(pgid, force=force)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + (min(wait_s, 3) if oscompat.IS_WINDOWS and not force else wait_s)
        while time.time() < deadline:
            if not group_pids(pgid):
                return
            time.sleep(0.1)


def stop(targets: list[dict], log=print) -> dict:
    """End each of snapshot()'s targets that still runs. {freed: [{gpu, gb, what: [...]}],
    stopped: [what], message}."""
    from . import vlm
    stopped = []
    for t in targets:
        if t.get("vlm"):
            if vlm.state():
                vlm.down(force=True, log=log)
        else:
            _kill_group(t["pid"])
            with _lock:
                _LIVE.pop(t["pid"], None)
        stopped.append(t)
    return report(stopped)


def report(stopped: list[dict]) -> dict:
    per_gpu: dict[int, dict] = {}
    for t in stopped:
        for g, gb in (t.get("vram") or {}).items():
            e = per_gpu.setdefault(int(g), {"gpu": int(g), "gb": 0.0, "what": []})
            e["gb"] += gb
            if t["what"] not in e["what"]:
                e["what"].append(t["what"])
    lines = [f"Freed {_gb(e['gb'])} on GPU {g}: {', '.join(e['what'])}." for g, e in sorted(per_gpu.items())]
    freed = [per_gpu[g] | {"gb": round(per_gpu[g]["gb"], 1)} for g in sorted(per_gpu)]
    rest = list(dict.fromkeys(t["what"] for t in stopped if not t.get("vram")))
    if rest:
        lines.append(f"Stopped {', '.join(rest)}.")
    return {"freed": freed, "stopped": [t["what"] for t in stopped],
            "message": " ".join(lines) or "No model server or headless Unreal was running."}


def _gb(gb: float) -> str:
    return f"{int(gb + 0.5)} GB" if gb >= 10 else f"{int(gb * 10 + 0.5) / 10} GB"

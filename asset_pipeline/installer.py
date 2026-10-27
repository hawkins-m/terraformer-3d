"""The setup wizard: a classic installer in the browser, served by a small stdlib-only server so it
runs before the app's own packages are installed (scripts/install.sh, windows/install.bat) and on
an installed system (`ap setup`).

    python3.12 -m asset_pipeline.installer [--port N] [--no-open] [--profile ID]

Pages (installer/web/): Welcome, License, Hardware profile, Components, Destination, Connections,
Ready, Installing, What you still need (needs.py, also the end of `ap doctor`), Finish. Pages 3-6 are data: the hardware profiles (profiles.PROFILES), the
model manifest with the CC0 dressing assets (config/models.toml), and the setup items
(setup.ITEMS, grouped by their `wizard` page), so a new model or setting shows up by itself.

Installing runs here, in a thread that outlives the browser tab: save the settings, the app's
Python environment, the `ap` command, the selected models (`ap models download --json`, whose
progress events drive the progress bar) and the model runtimes (installer/runtimes.toml). Its
state is kept in <config dir>/installer-state.json (never API keys), so closing the window, or
the server, and opening it again resumes where it stopped: finished steps are skipped and a
partial download continues from its .part file.
"""
import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, models, needs, profiles, setup, wsl

WEB = config.REPO_ROOT / "installer" / "web"
ASSETS = config.REPO_ROOT / "installer" / "assets"
RUNTIMES = config.REPO_ROOT / "installer" / "runtimes.toml"
LICENSE = config.REPO_ROOT / "LICENSE"
# what the Components page calls each manifest feature (an unknown one shows its own name)
FEATURES = {"comfy": "ComfyUI image models", "vlm": "Vision model (Qwen3-VL)", "segdepth": "Segmentation and depth",
            "trellis": "TRELLIS.2 (irregular assets)", "dressing": "World dressing (CC0 assets)"}
UNIT = 200 * 1024**2          # a non-download step counts like this many bytes in the overall progress bar
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".json": "application/json",
         ".png": "image/png", ".ico": "image/x-icon", ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".svg": "image/svg+xml"}


def state_path() -> Path:
    return config.config_dir() / "installer-state.json"


def load_state() -> dict:
    try:
        st = json.loads(state_path().read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("page", "welcome")
    st.setdefault("values", {})
    st.setdefault("components", None)          # None: the defaults (required, the selected vision model, dressing)
    st.setdefault("license", False)
    st.setdefault("install", {"status": "idle", "steps": [], "errors": []})
    return st


def save_state(st: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, p)


# --- what the pages show ------------------------------------------------------------------------

def free_bytes(path: str | Path) -> int | None:
    p = Path(os.path.expanduser(str(path or "~")))
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def components() -> list[dict]:
    out = []
    for m in models.load():
        st = models.status(m)
        out.append({"id": m.id, "title": m.title, "feature": m.feature, "group": FEATURES.get(m.feature, m.feature),
                    "need": m.need, "select": m.select, "size": m.size, "installed": st["state"] == "installed",
                    "missing_bytes": st["missing_bytes"], "license": m.license, "used_by": m.used_by,
                    "gated": bool(m.gated), "vram_gb": m.vram_gb})
    return out


def default_components(comps: list[dict], vlm: str) -> list[str]:
    """Required ones, the chosen vision model, the CC0 dressing assets and whatever is installed."""
    return [c["id"] for c in comps if c["need"] == "required" or c["installed"] or c["feature"] == "dressing"
            and c["need"] == "optional" or c["need"] == "local.model" and c["select"] == vlm]


def catalog() -> dict:
    """Everything pages 3-6 read: profiles, the machine, components, setup items by page."""
    st = setup.state()
    hw = profiles.detect()
    items = []
    for it in st["items"]:
        if it["id"] == "hardware_profile":
            it["notes"] = {k: p.note for k, p in profiles.PROFILES.items()}
            it["tested"] = {k: p.tested for k, p in profiles.PROFILES.items()}
        items.append(it)
    comps = components()
    return {"items": items, "gpus": st["gpus"], "detected": {k: hw[k] for k in ("vendor", "count", "profile", "wsl")},
            "wsl": st["wsl"], "components": comps, "features": FEATURES, "first_run": st["first_run"],
            "default_components": default_components(comps, str(config.get("local.model") or "32b")),
            "storage_free": free_bytes(config.ap_root()), "installed": installed(load_state())}


def installed(st: dict) -> bool:
    return st["install"].get("status") == "done" or (not setup.first_run() and app_python() is not None)


def assets() -> list[str]:
    return sorted(p.name for p in ASSETS.iterdir() if p.is_file()) if ASSETS.is_dir() else []


# --- the install steps --------------------------------------------------------------------------

def venv_dir() -> Path:
    return Path(os.path.expanduser(str(config.get("paths.venv") or ""))) if config.get("paths.venv") \
        else config.REPO_ROOT / ".venv"


def app_python() -> str | None:
    """A Python with the app installed: this one (`ap setup`), else the app venv's."""
    if sys.prefix != sys.base_prefix and Path(sys.prefix, "bin", "ap").exists():
        return sys.executable
    py = venv_dir() / "bin" / "python"
    return str(py) if (venv_dir() / "bin" / "ap").exists() and py.exists() else None


def _holds(cond: str, values: dict, features: set[str]) -> bool:
    if cond.startswith("feature:"):
        return cond.removeprefix("feature:") in features
    key, _, want = cond.partition("=")
    return fnmatch.fnmatch(str(values.get(key, config.get(key, "")) or ""), want)


def runtimes() -> list[dict]:
    with open(RUNTIMES, "rb") as f:
        return tomllib.load(f).get("runtime", [])


def setting_values(values: dict) -> dict:
    """Setup item values by their config key ("comfyui.backend"), for the runtimes' conditions."""
    return {setup.BY_ID[k].key: v for k, v in values.items() if k in setup.BY_ID}


def plan(values: dict, selected: list[str]) -> list[dict]:
    comps = {c["id"]: c for c in components()}
    want = [comps[i] for i in selected if i in comps]
    todo = [c for c in want if not c["installed"]]
    steps = [{"id": "settings", "kind": "settings", "label": "Saving your settings", "weight": UNIT},
             {"id": "app", "kind": "app", "label": "Installing the Terraformer app", "weight": 4 * UNIT},
             {"id": "command", "kind": "command", "label": "Adding the ap command", "weight": UNIT // 4}]
    if todo:
        steps.append({"id": "models", "kind": "models", "label": "Downloading models", "ids": [c["id"] for c in todo],
                      "weight": sum(c["missing_bytes"] for c in todo)})
    feats = {c["feature"] for c in want}
    sv = setting_values(values)
    for r in runtimes():
        if all(_holds(c, sv, feats) for c in r.get("when", [])):
            steps.append({"id": "runtime:" + r["id"], "kind": "runtime", "label": "Installing " + r["label"],
                          "command": r["command"], "weight": int(r.get("weight", 2)) * UNIT})
    for s in steps:
        s.update(status="pending", done=0)
    return steps


class Installer:
    """Runs the plan in a thread; every change goes to the state file (progress at most once a second)."""

    def __init__(self, models_cmd=None, run=None):
        self.lock = threading.RLock()
        self.st = load_state()
        if self.st["install"].get("status") == "running":
            self.st["install"]["status"] = "interrupted"      # the server stopped mid-install: resume on request
            save_state(self.st)
        self.thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.stop = False
        self._saved = 0.0
        self.models_cmd = models_cmd or self._models_cmd
        self.run_cmd = run or self._run

    # state -------------------------------------------------------------------------------------
    @property
    def ins(self) -> dict:
        return self.st["install"]

    def save(self, force: bool = True) -> None:
        now = time.time()
        if force or now - self._saved >= 1.0:
            self._saved = now
            save_state(self.st)

    def update(self, data: dict) -> dict:
        with self.lock:
            for k in ("page", "values", "components", "license"):
                if k in data:
                    self.st[k] = data[k]
            self.save()
            return self.st

    def progress(self) -> dict:
        with self.lock:
            steps = self.ins.get("steps", [])
            total = sum(s["weight"] for s in steps) or 1
            done = sum(s["weight"] if s["status"] in ("done", "skipped") else s.get("done", 0) for s in steps)
            return {k: v for k, v in self.ins.items() if k != "steps"} | {
                "steps": [{k: s[k] for k in ("id", "label", "status")} for s in steps],
                "overall": {"done": done, "total": total, "fraction": min(done / total, 1.0)}}

    # running -----------------------------------------------------------------------------------
    def start(self, secrets: dict | None = None) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.progress()
            vals, sel = self.st["values"], self.st["components"]
            if sel is None:
                sel = default_components(components(), str(vals.get("vlm_model") or "32b"))
            resuming = self.ins.get("status") in ("paused", "interrupted", "failed")
            old = {s["id"]: s["status"] for s in self.ins.get("steps", [])} if resuming else {}
            steps = plan(vals, sel)
            for s in steps:                      # finished steps stay finished; models re-check what is there
                if s["kind"] == "runtime" and s["id"] in self.st.setdefault("done_runtimes", []):
                    s["status"] = "skipped"
                elif s["kind"] != "models" and old.get(s["id"]) in ("done", "skipped"):
                    s["status"] = old[s["id"]]
            self.st["install"] = {"status": "running", "steps": steps, "errors": [], "current": None,
                                  "started": time.time()}
            self.stop = False
            self.save()
            self.thread = threading.Thread(target=self._work, args=(dict(secrets or {}),), daemon=True)
            self.thread.start()
            return self.progress()

    def cancel(self) -> dict:
        with self.lock:
            self.stop = True
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        if self.thread:
            self.thread.join(timeout=10)
        with self.lock:
            if self.ins.get("status") == "running":
                self.ins["status"] = "paused"
                self.save()
            return self.progress()

    def _work(self, secrets: dict) -> None:
        for step in self.ins["steps"]:
            if self.stop:
                return
            if step["status"] in ("done", "skipped"):
                continue
            with self.lock:
                step["status"] = "running"
                self.ins["current"] = {"label": step["label"], "done": None, "total": None}
                self.save()
            try:
                ok = getattr(self, "_step_" + step["kind"])(step, secrets)
            except Exception as e:                    # noqa: BLE001 - shown on the Installing page
                self._error(step, str(e))
                ok = False
            with self.lock:
                if self.stop:
                    step["status"] = "pending"
                    return
                step["status"] = "done" if ok else "failed"
                step["done"] = step["weight"] if ok else step.get("done", 0)
                if ok and step["kind"] == "runtime":
                    self.st.setdefault("done_runtimes", []).append(step["id"])
                self.save()
        with self.lock:
            failed = [s for s in self.ins["steps"] if s["status"] == "failed"]
            self.ins.update(status="failed" if failed else "done", current=None, finished=time.time())
            self.save()

    def _error(self, step: dict, message: str, fix: str = "", model: str = "") -> None:
        with self.lock:
            self.ins["errors"].append({"step": step["id"], "model": model, "message": message, "fix": fix})

    def _log(self, name: str) -> Path:
        d = config.ap_root() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"installer-{name}.log"

    def _run(self, cmd: list[str], log: Path, env: dict | None = None) -> int:
        with open(log, "a") as out:
            out.write(f"\n$ {' '.join(cmd)}\n")
            out.flush()
            self.proc = subprocess.Popen(cmd, cwd=config.REPO_ROOT, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, env=os.environ | (env or {}))
            return self.proc.wait()

    # steps ---------------------------------------------------------------------------------------
    def _step_settings(self, step: dict, secrets: dict) -> bool:
        vals = self.st["values"]
        shown = {k: v for k, v in vals.items() if k in setup.BY_ID and setup._shown(setup.BY_ID[k], vals)}
        setup.save(shown, {k: v for k, v in secrets.items() if k in config.SECRET_KEYS})
        return True

    def _step_app(self, step: dict, _s: dict) -> bool:
        if app_python():
            return True
        log = self._log("app")
        venv = venv_dir()
        if not (venv / "bin" / "python").exists() and self.run_cmd([sys.executable, "-m", "venv", str(venv)], log):
            self._error(step, f"Couldn't create the Python environment {venv}.", f"See {log}.")
            return False
        for cmd in ([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", "--upgrade", "pip"],
                    [str(venv / "bin" / "python"), "-m", "pip", "install", "-q", "-e", str(config.REPO_ROOT)]):
            if self.run_cmd(cmd, log):
                self._error(step, "Installing the app's Python packages failed.", f"See {log}; then Retry.")
                return False
        return True

    def _step_command(self, step: dict, _s: dict) -> bool:
        link = Path.home() / ".local" / "bin" / "ap"
        target = config.REPO_ROOT / "scripts" / "ap"
        if link.is_symlink() and link.resolve() == target.resolve():
            return True
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
        return True

    def _models_cmd(self, ids: list[str]) -> list[str]:
        return [app_python() or sys.executable, "-m", "asset_pipeline.cli", "models", "download", "--json", *ids]

    def _step_models(self, step: dict, _s: dict) -> bool:
        titles = {c["id"]: c["title"] for c in components()}
        sizes: dict[str, int] = {}            # model -> bytes to get (the plan's)
        got: dict[str, int] = {}              # model -> finished files' bytes
        cur: dict[str, int] = {}              # model -> the current file's bytes so far
        fsize: dict[tuple, int] = {}
        failed = False
        with open(self._log("models"), "a") as log:
            self.proc = subprocess.Popen(self.models_cmd(step["ids"]), cwd=config.REPO_ROOT, stdout=subprocess.PIPE,
                                         stderr=log, stdin=subprocess.DEVNULL, text=True, bufsize=1)
            for line in self.proc.stdout:
                log.write(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                e, m = ev.get("event"), ev.get("model", "")
                with self.lock:
                    if e == "plan":
                        sizes = {it["model"]: it["bytes"] for it in ev.get("items", [])}
                    elif e == "file_start":
                        fsize[(m, ev["file"])] = ev["size"]
                        cur[m] = ev.get("resume_from", 0)
                    elif e == "progress":
                        cur[m] = ev["done"]
                    elif e == "file_done":
                        got[m] = got.get(m, 0) + fsize.get((m, ev["file"]), 0)
                        cur[m] = 0
                    elif e == "error":
                        failed = True
                        self._error(step, ev.get("message", ""), ev.get("fix", ""), m)
                    elif e == "done" and ev.get("failed"):
                        failed = True
                    if e in ("file_start", "progress", "verify", "file_done", "model_done") and m:
                        verb = "Checking" if e == "verify" else "Downloading"
                        self.ins["current"] = {"label": f"{verb} {titles.get(m, m)}", "model": m,
                                               "done": got.get(m, 0) + cur.get(m, 0), "total": sizes.get(m, 0)}
                    total = sum(sizes.values()) or 1
                    done = sum(got.values()) + sum(cur.values())
                    step["done"] = int(step["weight"] * min(done / total, 1.0))
                    self.ins["download"] = {"done": done, "total": sum(sizes.values())}
                    self.save(force=e not in ("progress",))
            rc = self.proc.wait()
        return rc == 0 and not failed and not self.stop

    def _step_runtime(self, step: dict, _s: dict) -> bool:
        cmd = [re.sub(r"\{([a-z0-9_.]+)\}", lambda m: str(config.get(m.group(1), "")), a) for a in step["command"]]
        name = step["id"].removeprefix("runtime:")
        log = self._log(name)
        env = {"AP_ROOT": str(config.ap_root()), "AP_TORCH": profiles.torch_tag()}
        if self.run_cmd(cmd, log, env):
            self._error(step, f"{step['label']} failed.", f"See {log}; then Retry.")
            return False
        return True


# --- launching the app ----------------------------------------------------------------------------

def open_browser(url: str) -> None:
    """The default browser; inside WSL, the Windows one."""
    if wsl.is_wsl():
        for cmd in (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Start-Process '{url}'"],
                    ["cmd.exe", "/c", "start", "", url]):
            try:
                if subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                continue
    else:
        import webbrowser
        if webbrowser.open(url):
            return
    print(f"Open {url} in your browser.")


def launch_app() -> str:
    """Start Terraformer the way its launcher does; returns the app's address."""
    from .launcher import DEFAULT_PORT
    url = f"http://localhost:{DEFAULT_PORT}/"
    detached = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "start_new_session": True}
    bat = ""
    if wsl.is_wsl():                              # the Terraformer window install.ps1 wrote
        try:
            local = subprocess.run(["cmd.exe", "/c", "echo %LOCALAPPDATA%"], capture_output=True, text=True,
                                   timeout=20).stdout.strip()
            bat = local + "\\Terraformer\\Terraformer.bat" if local else ""
        except (OSError, subprocess.SubprocessError):
            bat = ""
    if bat and Path(wsl.to_linux(bat)).is_file():
        subprocess.Popen(["cmd.exe", "/c", "start", "", bat], **detached)
    elif (Path.home() / ".local" / "bin" / "terraformer-launch").is_file():
        subprocess.Popen([str(Path.home() / ".local" / "bin" / "terraformer-launch")], **detached)
    else:
        log = config.ap_root() / "logs" / "ui.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as out:
            subprocess.Popen([str(config.REPO_ROOT / "scripts" / "ap"), "ui", "--port", str(DEFAULT_PORT)],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return url


# --- the server -----------------------------------------------------------------------------------

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def make_handler(inst: Installer, server_state: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                # quiet: the console shows only the address
            pass

        def _local(self) -> bool:
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
            return host in LOCAL_HOSTS               # DNS rebinding: only localhost names

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _file(self, base: Path, name: str) -> None:
            p = (base / name).resolve()
            if not name or "/" in name or "\\" in name or p.parent != base.resolve() or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            data, ctype = p.read_bytes(), TYPES.get(p.suffix.lower(), "application/octet-stream")
            rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
            if rng and (rng.group(1) or rng.group(2)):        # video wants ranges
                a = int(rng.group(1)) if rng.group(1) else max(len(data) - int(rng.group(2)), 0)
                b = int(rng.group(2)) if rng.group(1) and rng.group(2) else len(data) - 1
                b = min(b, len(data) - 1)
                return self._send(206, data[a:b + 1], ctype, {"Content-Range": f"bytes {a}-{b}/{len(data)}",
                                                              "Accept-Ranges": "bytes"})
            self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._local():
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._file(WEB, "index.html")
            if path.startswith("/web/"):
                return self._file(WEB, path[5:])
            if path.startswith("/assets/"):
                return self._file(ASSETS, path[8:])
            if path == "/api/state":
                with inst.lock:
                    st = {k: v for k, v in inst.st.items() if k != "install"}
                return self._json(st | {"install": inst.progress(), "assets": assets(), "wsl": wsl.is_wsl(),
                                        "profile_hint": server_state.get("profile", ""),
                                        "license_text": LICENSE.read_text() if LICENSE.is_file() else ""})
            if path == "/api/catalog":
                return self._json(catalog())
            if path == "/api/progress":
                return self._json(inst.progress())
            if path == "/api/needs":                  # "What you still need" (Check again re-reads it)
                q = parse_qs(urlparse(self.path).query)
                return self._json(needs.report((q.get("folder") or [""])[0] or None))
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # JSON only: a page elsewhere can't send it here without a CORS preflight, which is never answered
            if not self._local() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(403, b"forbidden", "text/plain")
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "bad JSON"}, 400)
            path = urlparse(self.path).path
            if path == "/api/state":
                inst.update(data)
                return self._json({"ok": True})
            if path == "/api/test":
                try:
                    return self._json(setup.test(data["id"], data.get("value"), data.get("values") or {}))
                except ImportError:
                    return self._json({"id": data.get("id"), "ok": True, "warn": True,
                                       "message": "Tested once the app is installed: this check needs the app's "
                                                  "Python packages, which Installing adds. Run ap setup afterwards."})
                except (KeyError, ValueError) as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/needs/use":               # use a folder that already holds ComfyUI models: nothing moves
                folder = str(data.get("folder") or "").strip()
                if not folder or not Path(os.path.expanduser(folder)).is_dir():
                    return self._json({"error": "not a folder"}, 400)
                setup.save({"comfy_models_dir": folder})
                return self._json(needs.report())
            if path == "/api/disk":
                return self._json({"free": free_bytes(data.get("path") or config.ap_root())})
            if path == "/api/install":
                return self._json(inst.start(data.get("secrets")))
            if path == "/api/cancel":
                return self._json(inst.cancel())
            if path == "/api/finish":
                url = launch_app() if data.get("launch") else ""
                self._json({"ok": True, "url": url})
                threading.Timer(1.0, server_state["httpd"].shutdown).start()
                return
            self._send(404, b"not found", "text/plain")

    return Handler


def find_running(port: int) -> bool:
    """A wizard already answers on this port (opened from another terminal)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/progress", timeout=1) as r:
            return "overall" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def serve(port: int | None = None, open_page: bool = True, profile: str = "", installer: Installer | None = None):
    port = int(port or config.get("installer.port", 8705))
    if find_running(port):
        url = f"http://localhost:{port}/"
        print(f"The setup wizard is already running at {url}")
        if open_page:
            open_browser(url)
        return None
    inst = installer or Installer()
    server_state: dict = {"profile": profile}
    if profile and not inst.st["values"].get("hardware_profile"):
        inst.st["values"]["hardware_profile"] = profile
    for p in range(port, port + 10):                # the configured port, or the next free one
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), make_handler(inst, server_state))
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"installer: ports {port}-{port + 9} are all in use")
    server_state["httpd"] = httpd
    url = f"http://localhost:{httpd.server_address[1]}/"
    print(f"Terraformer setup wizard: {url}  (keep this window open while it installs; Ctrl+C stops it, "
          "and running it again resumes)")
    if open_page:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Run the installer again (or ap setup) to resume.")
    finally:
        httpd.server_close()
    return httpd


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python3.12 -m asset_pipeline.installer", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=None, help="port (default [installer] port, 8705)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--profile", default="", choices=["", *profiles.PROFILES], help="preselect a hardware profile")
    a = ap.parse_args(argv)
    serve(a.port, not a.no_open, a.profile)


if __name__ == "__main__":
    main()

"""Windows (WSL2) + NVIDIA, beta: running inside WSL2 and calling Windows-installed tools.

Inside WSL the pipeline is the normal Linux install. What differs:
  - Blender and Unreal may be Windows programs (C:\\...\\blender.exe). Setup accepts a Windows path
    and stores its Linux form (wslpath -u: <drive root>/c/...); the .exe runs through WSL interop.
  - A Windows program can't open Linux paths, so `windows_cmd()` gives it Windows ones (wslpath -w:
    \\\\wsl.localhost\\<distro>\\...): command-line arguments, the JSON job files they name (a
    translated copy, <name>.win.json, so the original stays readable here) and the path variables
    passed through WSLENV. After the run, `read_back()` turns the Windows paths in the JSON files it
    wrote back into Linux paths.
  - Storage belongs on the WSL filesystem (the default, ~/.local/share/terraformer): the Windows
    drives (C:\\ and the rest) are slow from WSL and break the hard links the file store and backups use.

Untested: nothing here has run on a Windows machine yet (UNTESTED below says what).
"""
import json
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath

LABEL = "Windows (WSL2) + NVIDIA, beta"
UNTESTED = [
    "none of the WSL2 paths has run on a Windows machine yet (tested with mocked wslpath and interop)",
    "Windows Blender and Unreal through WSL interop: Stop ends the Linux side; the Windows program may keep running",
    "Unreal on Windows reading projects on the WSL filesystem (\\\\wsl.localhost): keep its projects folder on a Windows drive",
    "CUDA wheels, TRELLIS.2 extensions and the llama.cpp Vulkan build on WSL's NVIDIA driver",
    "the open-editor check can't see a Unreal Editor window on Windows",
]
# environment variables whose value is a path a Windows program reads (WSLENV /p translates them)
PATH_ENV = ("AP_UE_ARGS",)

_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/]")
_WIN_UNC = re.compile(r"^(?:\\\\|//)(?:wsl\.localhost|wsl\$)[\\/]([^\\/]+)(.*)$", re.I)


def is_wsl(proc: Path = Path("/proc")) -> bool:
    """Running inside WSL (1 or 2): WSL_DISTRO_NAME, or "microsoft" in the kernel release."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    for f in ("sys/kernel/osrelease", "version"):
        try:
            if "microsoft" in (proc / f).read_text().lower():
                return True
        except OSError:
            pass
    return False


def is_wsl2(proc: Path = Path("/proc")) -> bool:
    """WSL 2 (a real Linux kernel, GPU passthrough): "WSL2" in the kernel release."""
    try:
        return "wsl2" in (proc / "sys/kernel/osrelease").read_text().lower()
    except OSError:
        return is_wsl(proc) and Path("/run/WSL").exists()


def distro() -> str:
    return os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"


@lru_cache(maxsize=1)
def drive_root() -> str:
    """Where WSL mounts the Windows drives: [automount] root in /etc/wsl.conf, default /mnt."""
    try:
        for ln in Path("/etc/wsl.conf").read_text().splitlines():
            k, _, v = ln.partition("=")
            if k.strip() == "root" and v.strip():
                return "/" + v.strip().strip("\"'").strip("/")
    except OSError:
        pass
    return "/mnt"


def is_windows_path(s: str) -> bool:
    """C:\\..., C:/... or \\\\wsl.localhost\\<distro>\\... (a path typed or printed on Windows)."""
    return bool(s) and bool(_WIN_DRIVE.match(s) or _WIN_UNC.match(s))


def on_windows_drive(p: str | Path) -> bool:
    """A Linux path on a Windows drive (<drive root>/c/...): slow from WSL, no hard links."""
    parts = PurePosixPath(str(p)).parts
    root = PurePosixPath(drive_root()).parts
    return len(parts) > len(root) and parts[:len(root)] == root and re.fullmatch(r"[a-z]", parts[len(root)]) is not None


def _wslpath(flag: str, p: str) -> str | None:
    if not shutil.which("wslpath"):
        return None
    try:
        r = subprocess.run(["wslpath", flag, p], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def to_linux(s: str) -> str:
    """A Windows path as WSL sees it (wslpath -u; without wslpath: by the drive root and distro).
    Anything else is returned as it is."""
    s = str(s).strip().strip('"')
    if not is_windows_path(s):
        return s
    if (out := _wslpath("-u", s)):
        return out
    if (m := _WIN_DRIVE.match(s)):
        rest = s[m.end():].replace("\\", "/")
        return f"{drive_root()}/{m.group(1).lower()}/{rest}".rstrip("/")
    m = _WIN_UNC.match(s)
    return "/" + m.group(2).replace("\\", "/").strip("/")


def to_windows(p: str | Path) -> str:
    """A Linux path as a Windows program needs it (wslpath -w). A path that doesn't exist yet
    (an output) converts through its nearest existing folder."""
    p = str(p)
    if (out := _wslpath("-w", p)):
        return out
    head, tail = Path(p), []
    while not head.exists() and head != head.parent:
        tail.insert(0, head.name)
        head = head.parent
    if tail and head != Path(p) and (out := _wslpath("-w", str(head))):
        return "\\".join([out.rstrip("\\"), *tail])
    if on_windows_drive(p):
        rest = PurePosixPath(p).parts[len(PurePosixPath(drive_root()).parts):]
        return f"{rest[0].upper()}:\\" + "\\".join(rest[1:])
    return f"\\\\wsl.localhost\\{distro()}" + p.replace("/", "\\")


def is_windows_exe(exe: str | Path) -> bool:
    """A Windows program called from WSL: a .exe, inside WSL."""
    return str(exe).lower().endswith(".exe") and is_wsl()


def _linux_path(s: str) -> bool:
    """An absolute path on this filesystem: its top folder exists (so "/Game/AP/..." stays as it is)."""
    if not s.startswith("/") or s.startswith("//"):
        return False
    top = PurePosixPath(s).parts[1:2]
    return bool(top) and Path("/", top[0]).exists()


def _arg(a: str, conv) -> str:
    """One argument with its Linux paths converted by conv: the whole argument, the value of
    -name=value, or the space-separated words of "py /x/script.py"."""
    if _linux_path(a):
        return conv(a)
    m = re.match(r"^(-{1,2}[A-Za-z][\w.-]*=)(.*)$", a, re.S)     # -script=/x.py /y.json, --out=/z
    head, body = (m.group(1), m.group(2)) if m else ("", a)
    if "/" not in body:
        return a
    return head + " ".join(conv(t) if _linux_path(t) else t for t in body.split(" "))


def _walk(v, fn):
    if isinstance(v, dict):
        return {k: _walk(x, fn) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x, fn) for x in v]
    return fn(v) if isinstance(v, str) else v


def _paths_in(v) -> list[str]:
    out: list[str] = []
    _walk(v, lambda s: out.append(s) if _linux_path(s) else None)
    return out


def _win_json(p: Path) -> tuple[Path, list[str]] | None:
    """A copy of a JSON job file with its Linux paths as Windows paths: (copy, the paths in it)."""
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    out = p.with_name(p.stem + ".win.json")
    out.write_text(json.dumps(_walk(data, lambda s: to_windows(s) if _linux_path(s) else s), indent=1))
    return out, _paths_in(data)


def windows_cmd(cmd: list, env: dict | None = None) -> tuple[list[str], dict, list[Path], list[Path]]:
    """(cmd, env, watch, copies) for running a Windows program from WSL: Linux path arguments become
    Windows paths, a .json job file becomes its translated copy, PATH_ENV variables are passed
    through WSLENV (path-translated); watch lists the folders read_back() looks at afterwards, copies
    the translated job files it removes.
    Unchanged when cmd[0] isn't a Windows program."""
    env = dict(os.environ if env is None else env)
    cmd = [str(c) for c in cmd]
    if not cmd or not is_windows_exe(cmd[0]):
        return cmd, env, [], []
    named: list[str] = []
    copies: list[Path] = []

    def conv(a: str) -> str:
        named.append(a)
        if a.endswith(".json") and Path(a).is_file() and (w := _win_json(Path(a))):
            named.extend(w[1])
            copies.append(w[0])
            return to_windows(w[0])
        return to_windows(a)
    out = [cmd[0], *(_arg(a, conv) for a in cmd[1:])]
    share = [x for x in env.get("WSLENV", "").split(":") if x]
    for k in PATH_ENV:
        if not env.get(k):
            continue
        v = env[k]
        if v.endswith(".json") and Path(v).is_file() and (w := _win_json(Path(v))):
            named += [v, *w[1]]
            copies.append(w[0])
            env[k] = str(w[0])
        share = [x for x in share if x.split("/")[0] != k] + [f"{k}/p"]
    if share:
        env["WSLENV"] = ":".join(share)
    watch = []
    for n in named:
        p = Path(n)
        watch.append(p if p.is_dir() else p.parent)
    return out, env, list(dict.fromkeys(watch)), copies


def read_back(watch: list[Path], since: float, copies: list[Path] = ()) -> list[Path]:
    """After a Windows program ran: the Windows paths in the JSON files it wrote (in the watched
    folders and one level below, modified since `since`) become Linux paths again. Returns the
    files rewritten. The translated job copies are removed."""
    for c in copies:
        c.unlink(missing_ok=True)
    done = []
    seen = set()
    for d in watch:
        if not d.is_dir():
            continue
        for f in [*d.glob("*.json"), *d.glob("*/*.json")]:
            if f in seen or f.name.endswith(".win.json"):
                continue
            seen.add(f)
            try:
                if f.stat().st_mtime < since - 1:
                    continue
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            new = _walk(data, lambda s: to_linux(s) if is_windows_path(s) else s)
            if new != data:
                f.write_text(json.dumps(new, indent=1))
                done.append(f)
    return done


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    """subprocess.run for a command that may be a Windows program (paths translated both ways)."""
    t = time.time()
    cmd, env, watch, copies = windows_cmd(cmd, kw.pop("env", None))
    try:
        return subprocess.run(cmd, env=env, **kw)
    finally:
        read_back(watch, t, copies)


def status() -> dict:
    """What `ap doctor` and Setup show about WSL."""
    if not is_wsl():
        return {"wsl": False}
    return {"wsl": True, "wsl2": is_wsl2(), "distro": distro(), "label": LABEL, "untested": UNTESTED,
            "drive_root": drive_root(), "interop": Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
            or Path("/proc/sys/fs/binfmt_misc/WSLInterop-late").exists()}

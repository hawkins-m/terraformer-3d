"""The operating-system differences in one place: Linux (and WSL) on one side, native Windows
(beta, the greybox path) on the other.

Processes: a helper the app starts runs in its own process group (POSIX: a new session, so the
group id is its pid; Windows: CREATE_NEW_PROCESS_GROUP), and stopping it ends the whole tree
(killpg; taskkill /T). Process names, command lines and port owners come from /proc and `ss`
on Linux and from tasklist, PowerShell CIM and netstat on Windows. Files: hard links and
symlinks fall back to copies where the file system or the user's rights don't allow them, and
atomic writes go through os.replace. Linux behaviour is unchanged by this module: every POSIX
branch is what the callers did before.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
NATIVE_WINDOWS_NOTE = ("not supported on native Windows yet (beta covers the greybox path): "
                       "use the WSL install (windows\\install.bat, WSL choice) for TRELLIS and the vision model")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def detached() -> dict:
    """Popen keyword arguments that put the child in its own process group, so kill_tree() can
    end it with everything it started and a Ctrl+C in the app's console doesn't reach it."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW}
    return {"start_new_session": True}


def own_group() -> int | None:
    """This process's group id (POSIX), None on Windows."""
    return None if IS_WINDOWS else os.getpgrp()


def alive(pid: int | None) -> bool:
    """The process runs (a zombie doesn't count)."""
    if not pid:
        return False
    if IS_WINDOWS:
        return _win_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = Path(f"/proc/{pid}/stat")
    try:
        return not (st.is_file() and ") Z" in st.read_text())
    except OSError:
        return True


def _win_alive(pid: int) -> bool:
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.GetLastError() == 5            # access denied: it exists
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259        # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def group_alive(pgid: int) -> bool:
    if IS_WINDOWS:
        return _win_alive(pgid)
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_tree(pid: int, force: bool = False, group: bool = True) -> None:
    """Signal a process (group=True: its whole group / tree). force: SIGKILL, taskkill /F.
    Raises ProcessLookupError when it is already gone."""
    if IS_WINDOWS:
        if not _win_alive(pid):
            raise ProcessLookupError(pid)
        cmd = ["taskkill", "/PID", str(pid)] + (["/T"] if group else []) + (["/F"] if force else [])
        subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    os.killpg(pid, sig) if group else os.kill(pid, sig)


def stop_tree(pid: int, wait_s: float = 10, group: bool = True) -> None:
    """Terminate, wait up to wait_s, then force. Quiet when it is already gone. On Windows a
    console program ignores taskkill's polite close, so the force follows after a short wait."""
    for force in (False, True):
        try:
            kill_tree(pid, force=force, group=group)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + (min(wait_s, 3) if IS_WINDOWS and not force else wait_s)
        while time.time() < deadline:
            if not (group_alive(pid) if group else alive(pid)):
                return
            time.sleep(0.1)


def stop_popen(proc: subprocess.Popen, wait_s: float = 10) -> None:
    """End a child started with detached() and its group, then reap it."""
    try:
        kill_tree(proc.pid)
        proc.wait(timeout=wait_s)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        try:
            kill_tree(proc.pid, force=True)
        except ProcessLookupError:
            pass
        proc.wait(timeout=wait_s)


def process_name(pid: int) -> str:
    """The program name (Linux comm; Windows image name without .exe), "" when unknown."""
    if IS_WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=15, creationflags=_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        m = re.match(r'"([^"]+)"', out.strip())
        return re.sub(r"\.exe$", "", m.group(1), flags=re.I) if m else ""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def process_argv(pid: int) -> list[str]:
    """A process's command line as a list, [] when it can't be read."""
    if IS_WINDOWS:
        ps = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                 timeout=30, creationflags=_NO_WINDOW).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return []
        return split_windows_cmdline(out) if out else []
    try:
        return [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def processes_named(image: str) -> list[list[str]]:
    """Windows: the command lines of running processes whose image name is `image` (e.g.
    "UnrealEditor.exe"), each split into a list. [] elsewhere or when it can't be read."""
    if not IS_WINDOWS:
        return []
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='{image}'\" | "
          "ForEach-Object { $_.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [split_windows_cmdline(ln) for ln in out.splitlines() if ln.strip()]


def split_windows_cmdline(s: str) -> list[str]:
    """Split a Windows command line the way most programs' C runtime does (quotes group,
    backslashes are literal except before a quote)."""
    out, cur, quoted, have, i = [], [], False, False, 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            j = i
            while j < len(s) and s[j] == "\\":
                j += 1
            n = j - i
            if j < len(s) and s[j] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i = j + 1
                else:
                    i = j
                have = True
                continue
            cur.append("\\" * n)
            i, have = j, True
            continue
        if c == '"':
            quoted, have = not quoted, True
        elif c in " \t" and not quoted:
            if have:
                out.append("".join(cur))
                cur, have = [], False
        else:
            cur.append(c)
            have = True
        i += 1
    if have:
        out.append("".join(cur))
    return out


def port_owner(port: int) -> int | None:
    """The pid listening on a local TCP port (ss on Linux, netstat on Windows), or None."""
    if IS_WINDOWS:
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15,
                                 creationflags=_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_netstat(out, port)
    try:
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"pid=(\d+)", out)
    return int(m.group(1)) if m else None


def parse_netstat(out: str, port: int) -> int | None:
    for ln in out.splitlines():
        f = ln.split()
        if len(f) >= 5 and f[0].upper() == "TCP" and f[3].upper() == "LISTENING" and f[1].rsplit(":", 1)[-1] == str(port):
            return int(f[4]) if f[4].isdigit() and f[4] != "0" else None
    return None


def short_path(p: Path | str) -> str:
    """A path without spaces for a command-line value that is split on spaces again by the program
    (Unreal's -ExecCmds=py ..., -script="file args"): on Windows its 8.3 short name when it has spaces
    (and the volume keeps 8.3 names), else the path as it is."""
    s = str(p)
    if not IS_WINDOWS or " " not in s:
        return s
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(s, buf, len(buf))
    return buf.value if 0 < n < len(buf) else s


def windows_cmdline(cmd: list[str]) -> str:
    """A Windows command line for cmd: like subprocess.list2cmdline, except that a -Key=value option
    whose value has spaces becomes -Key="value", the form Unreal's parser reads (a quote before the
    dash makes it read only up to the first space)."""
    out = []
    for a in map(str, cmd):
        m = re.match(r'^(-[A-Za-z][\w.]*=)(.*\s.*)$', a)
        if m and '"' not in a:
            out.append(f'{m.group(1)}"{m.group(2)}"')
        else:
            out.append(subprocess.list2cmdline([a]))
    return " ".join(out)


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard link src at dst, or copy it where links aren't possible (FAT/exFAT drives, another
    volume, a Windows share)."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def symlink_or_copy(target: str | Path, link: Path) -> None:
    """link -> target (target relative to link's folder or absolute). Windows needs Developer
    Mode or admin rights for symlinks: without them the target is copied."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        src = Path(target) if Path(target).is_absolute() else link.parent / target
        if src.is_dir():
            shutil.copytree(src, link)
        else:
            shutil.copy2(src, link)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write via a temp file in the same folder and os.replace (atomic on both systems)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def exe(name: str) -> str:
    """A program's file name on this system (blender -> blender.exe on Windows)."""
    return name + ".exe" if IS_WINDOWS and not name.lower().endswith(".exe") else name


def venv_bin(env: Path, prog: str = "python") -> Path:
    """A program inside a venv: bin/prog (POSIX), Scripts\\prog.exe (Windows)."""
    return Path(env) / ("Scripts" if IS_WINDOWS else "bin") / exe(prog)


def config_home() -> Path:
    """Where per-user config folders go: $XDG_CONFIG_HOME or ~/.config; %APPDATA% on Windows."""
    if IS_WINDOWS and not os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")).expanduser()


def data_home() -> Path:
    """Where per-user data goes: $XDG_DATA_HOME or ~/.local/share; %LOCALAPPDATA% on Windows."""
    if IS_WINDOWS and not os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")).expanduser()


def run_git(args: list[str], **kw) -> subprocess.CompletedProcess:
    """git with these arguments; a CompletedProcess with returncode 127 when git isn't installed
    (a native Windows machine without Git for Windows)."""
    try:
        return subprocess.run(["git", *args], **kw)
    except FileNotFoundError:
        empty = "" if kw.get("text") else b""
        return subprocess.CompletedProcess(["git", *args], 127, empty, empty)


def open_url(url: str) -> None:
    """Open a URL in the default browser."""
    if IS_WINDOWS:
        os.startfile(url)        # noqa: S606 - the user's default browser
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

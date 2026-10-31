"""Finding the greybox path's tools on native Windows (beta): Blender, Unreal Engine and ComfyUI.

Only the usual install places are looked at, never the registry's uninstall lists:
  Blender   %ProgramFiles%\\Blender Foundation\\Blender *\\blender.exe, the Microsoft Store and
            Steam copies, and blender.exe on PATH;
  Unreal    the Epic launcher's own list (%ProgramData%\\Epic\\UnrealEngineLauncher\\
            LauncherInstalled.dat) and %ProgramFiles%\\Epic Games\\UE_*;
  ComfyUI   the portable build (ComfyUI_windows_portable\\python_embeded + ComfyUI\\main.py) in the
            storage folder, the user's folders and drive roots; the ComfyUI desktop app (its
            server, port 8000, and the base path in %APPDATA%\\ComfyUI\\config.json); a git checkout
            with its own venv (venv\\Scripts\\python.exe or .venv).

    python -m asset_pipeline.win_tools [comfy|blender|unreal]     prints what it finds as JSON
(windows/install.ps1 asks it which ComfyUI to use).
"""
import glob
import json
import os
import shutil
import string
import sys
from pathlib import Path

UE_TAIL = "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
DESKTOP_PORT = 8000                    # the ComfyUI desktop app's server
PORTABLE_LAUNCH = ("{py} -s main.py --windows-standalone-build --listen 127.0.0.1 --port {{port}}")


def _env_dirs(*names: str) -> list[Path]:
    return [Path(os.environ[n]) for n in names if os.environ.get(n)]


def _program_dirs() -> list[Path]:
    return list(dict.fromkeys(_env_dirs("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")))


def _drives() -> list[str]:
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]


def _q(p: Path | str) -> str:
    """A path for a launch command line: quoted when it has spaces."""
    s = str(p)
    return f'"{s}"' if " " in s else s


def blender_candidates() -> list[str]:
    out = [shutil.which("blender") or ""]
    for base in _program_dirs():
        out += sorted(glob.glob(str(base / "Blender Foundation" / "Blender*" / "blender.exe")), reverse=True)
        out += glob.glob(str(base / "Steam" / "steamapps" / "common" / "Blender" / "blender.exe"))
    for base in _env_dirs("LOCALAPPDATA"):
        out += sorted(glob.glob(str(base / "Programs" / "Blender Foundation" / "Blender*" / "blender.exe")), reverse=True)
        out += glob.glob(str(base / "Microsoft" / "WindowsApps" / "blender*.exe"))
    return [p for p in dict.fromkeys(out) if p and Path(p).is_file()]


def unreal_candidates() -> list[str]:
    out = []
    for pd in _env_dirs("ProgramData"):
        dat = pd / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
        try:
            apps = json.loads(dat.read_text(encoding="utf-8")).get("InstallationList", [])
        except (OSError, ValueError):
            apps = []
        for a in apps:
            if str(a.get("AppName", "")).startswith("UE_"):
                out.append(str(Path(a.get("InstallLocation", "")) / UE_TAIL))
    for base in _program_dirs():
        out += sorted(glob.glob(str(base / "Epic Games" / "UE_*" / UE_TAIL)), reverse=True)
    found = [p for p in dict.fromkeys(out) if p and Path(p).is_file()]
    return sorted(found, key=lambda p: ("UE_5.8" not in p, p))        # 5.8 (tested) first


def _portable(root: Path) -> dict | None:
    py, main = root / "python_embeded" / "python.exe", root / "ComfyUI" / "main.py"
    if py.is_file() and main.is_file():
        return {"kind": "portable", "cwd": str(main.parent), "models_dir": str(main.parent / "models"),
                "launch": PORTABLE_LAUNCH.format(py=_q(py)), "url": "http://127.0.0.1:8188"}
    return None


def _checkout(root: Path) -> dict | None:
    if not (root / "main.py").is_file() or not (root / "comfy").is_dir():
        return None
    for v in ("venv", ".venv"):
        py = root / v / "Scripts" / "python.exe"
        if py.is_file():
            return {"kind": "git", "cwd": str(root), "models_dir": str(root / "models"),
                    "launch": f"{_q(py)} main.py --listen 127.0.0.1 --port {{port}}", "url": "http://127.0.0.1:8188"}
    return None


def _desktop() -> dict | None:
    exe = next((p for b in _env_dirs("LOCALAPPDATA") for p in
                (b / "Programs" / "@comfyorgcomfyui-electron" / "ComfyUI.exe", b / "Programs" / "ComfyUI" / "ComfyUI.exe")
                if p.is_file()), None)
    if not exe:
        return None
    base = ""
    for ad in _env_dirs("APPDATA"):
        try:
            base = json.loads((ad / "ComfyUI" / "config.json").read_text(encoding="utf-8")).get("basePath", "")
        except (OSError, ValueError):
            pass
    return {"kind": "desktop", "cwd": base, "models_dir": str(Path(base) / "models") if base else "",
            "launch": _q(exe), "url": f"http://127.0.0.1:{DESKTOP_PORT}", "exe": str(exe)}


def comfy_installs(storage: Path | str | None = None) -> list[dict]:
    """ComfyUI installs on this machine, best first: {kind, cwd, models_dir, launch, url}."""
    roots = [Path(storage)] if storage else []
    roots += [Path.home(), Path.home() / "Documents", Path.home() / "Downloads", Path.home() / "Desktop"]
    roots += [Path(d) for d in _drives()]
    found = []
    for r in dict.fromkeys(roots):
        for name in ("ComfyUI_windows_portable", "ComfyUI_windows_portable_nvidia", "ComfyUI"):
            if (hit := _portable(r / name) or _checkout(r / name)):
                found.append(hit)
    if (d := _desktop()):
        found.append(d)
    return list({f["cwd"] + f["kind"]: f for f in found}.values())


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "comfy"
    out = {"comfy": lambda: comfy_installs(argv[1] if len(argv) > 1 else None),
           "blender": blender_candidates, "unreal": unreal_candidates}.get(what)
    if not out:
        print(__doc__)
        return 2
    print(json.dumps(out(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

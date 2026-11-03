"""What the Unreal editor shows when it opens a city: the World Partition region it loads with the
level, and the viewport camera.

A World Partition level (a large / metro city) opens with nothing loaded. The editor loads the
regions saved for the level in the project's per-user settings (Saved/Config/<Platform>Editor/
EditorPerProjectUserSettings.ini, [/Script/Engine.WorldPartitionEditorPerProjectUserSettings]
PerWorldEditorSettings: what Load Region from Selection writes), so the build writes the city's
region there. Streaming for Play and Movie Render Queue is not touched. A standard city's level
is a plain one: everything loads with it.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import city, city_size, oscompat

SECTION = "[/Script/Engine.WorldPartitionEditorPerProjectUserSettings]"
KEY = "PerWorldEditorSettings"
MARGIN_M = 200.0              # round the city's bounds
Z_CM = 1e7                    # the region spans every height


def region_box(layout) -> dict | None:
    """The box to load in the editor, UE cm: a large city's bounds, a metro city's detail radius
    round its main centre (the rest stays unloaded to keep the editor responsive). None: a standard
    city, whose level isn't partitioned. Blender's Y is UE's -Y."""
    c = layout.city
    if c is None or getattr(c, "size", "standard") == "standard":
        return None
    x0, y0, x1, y1 = (v - MARGIN_M if i < 2 else v + MARGIN_M for i, v in enumerate(c.bounds))
    if c.size == "metro":
        cx, cy = city.main_centre(c)
        r = city_size.detail_radius(c)
        x0, y0, x1, y1 = max(x0, cx - r), max(y0, cy - r), min(x1, cx + r), min(y1, cy + r)
    return {"min": [x0 * 100, -y1 * 100, -Z_CM], "max": [x1 * 100, -y0 * 100, Z_CM]}


def box_text(box: dict) -> str:
    (ax, ay, az), (bx, by, bz) = box["min"], box["max"]
    return (f"(Min=(X={ax:.6f},Y={ay:.6f},Z={az:.6f}),Max=(X={bx:.6f},Y={by:.6f},Z={bz:.6f}),IsValid=True)")


def _split(s: str) -> list[str]:
    """The comma-separated parts of s at the top level (not inside parentheses or quotes)."""
    parts, depth, quoted, cur = [], 0, False, []
    for ch in s:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p for p in parts if p.strip()]


def merge_world(value: str, world: str, box: dict) -> str:
    """The PerWorldEditorSettings value ("(("/Game/.../A.A", (LoadedEditorRegions=(...))), ...)") with
    `world`'s loaded region set to `box`: its other settings and the other worlds stay."""
    inner = value.strip()
    inner = inner[1:-1] if inner.startswith("(") and inner.endswith(")") else ""
    regions = f"LoadedEditorRegions=({box_text(box)})"
    out, found = [], False
    for item in _split(inner):
        key, _, struct = item.strip()[1:-1].partition(",")
        if key.strip().strip('"') != world:
            out.append(item.strip())
            continue
        found = True
        fields = [f for f in _split(struct.strip()[1:-1]) if not f.strip().startswith("LoadedEditorRegions=")]
        out.append(f'("{world}",({",".join([regions, *(f.strip() for f in fields)])}))')
    if not found:
        out.append(f'("{world}",({regions}))')
    return f"({','.join(out)})"


def merge_ini(text: str, world: str, box: dict) -> str:
    """The user settings file's text with `world`'s region set (the section and key added when missing)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == SECTION.lower()), None)
    if start is None:
        lines += ([""] if lines and lines[-1].strip() else []) + [SECTION, f"{KEY}={merge_world('', world, box)}"]
        return eol.join(lines) + eol
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    for i in range(start + 1, end):
        if lines[i].startswith(KEY + "="):
            lines[i] = f"{KEY}={merge_world(lines[i][len(KEY) + 1:], world, box)}"
            break
    else:
        lines.insert(start + 1, f"{KEY}={merge_world('', world, box)}")
    return eol.join(lines) + eol


def has_world(text: str, world: str) -> bool:
    """Does the user settings file already hold settings for `world`?"""
    return bool(re.search(rf'^{KEY}=.*"{re.escape(world)}"', text, re.M))


def user_settings(project_dir: Path, windows: bool | None = None) -> Path:
    """The project's per-user editor settings file (a Windows editor, native or through WSL, writes under WindowsEditor)."""
    windows = oscompat.IS_WINDOWS if windows is None else windows
    return project_dir / "Saved" / "Config" / ("WindowsEditor" if windows else "LinuxEditor") / "EditorPerProjectUserSettings.ini"


def set_loaded_region(ini: Path, world: str, box: dict, keep_existing: bool = False) -> bool:
    """Save `box` as the region the editor loads with `world` (its object path, /Game/…/A.A) in the
    settings file `ini`, the editor closed. keep_existing: leave a region the user already has for
    that level. Returns whether the file changed."""
    text = ini.read_text(encoding="utf-8", errors="replace") if ini.is_file() else ""
    if keep_existing and has_world(text, world):
        return False
    new = merge_ini(text, world, box)
    if new == text:
        return False
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text(new, encoding="utf-8", newline="")
    return True

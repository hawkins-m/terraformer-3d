"""A generated terrain as an Unreal Landscape (`ap ue build`): the heights the greybox was built on
(site/terrain.png, pads and lowered water included) as a raw 16-bit heightmap, imported by the
APLandscape editor plugin (unreal/plugins/APLandscape: UE 5.8's Python can't create a Landscape) at
the right scale and position under the city, with MI_AP_TerrainAuto. The generated terrain's greybox
mesh is hidden under it. A heightmap project is left alone: its Landscape (a manual import, USAGE.md
"Terrain beyond the city") stays as it is.

Size: the tile's samples resampled to whole components (SECTIONS x QUADS quads each), the spacing as
close to the tile's own as that allows. Heights: Landscape units of SCALE_Z / 128 cm around the
terrain's middle height, SCALE_Z grown in steps of 100 when the range needs it.
"""
import hashlib
import math

import numpy as np

from .project import ProjectStore, read_json, write_json

SECTIONS = 2                # sections per component
QUADS = 63                  # quads per section (Landscape mode's 63 / 127 / 255)
SCALE_Z_CM = 100.0          # Landscape mode's default: 256 m over the 16-bit range
RAW = "terrain/landscape.r16"     # under export/
MATERIAL = "/Game/AP/Terrain/MI_AP_TerrainAuto"
LABEL = "AP_Landscape"


def wanted(store: ProjectStore) -> bool:
    """A generated terrain (no heightmap) the greybox has been built on."""
    from .stages import sw_site
    if not (sw_site.site_dir(store) / "terrain.png").is_file():
        return False
    return not sw_site.load_layout(store).terrain.heightmap


def size_for(n: int) -> int:
    """The Landscape sample count nearest n whose quads are whole components."""
    q = SECTIONS * QUADS
    return max(1, round((n - 1) / q)) * q + 1


def resample(h: np.ndarray, n: int) -> np.ndarray:
    """h (square, rows north -> south) at n x n samples over the same extent (bilinear)."""
    if h.shape[0] == n:
        return h.astype(np.float64)
    from scipy.ndimage import map_coordinates
    t = np.linspace(0, h.shape[0] - 1, n)
    I, J = np.meshgrid(t, t, indexing="ij")
    return map_coordinates(h.astype(np.float64), [I, J], order=1)


def encode(h: np.ndarray) -> tuple[np.ndarray, float, float]:
    """(uint16 heights, middle height m, scale z cm): Unreal's height = (v - 32768) * scale / 128 cm
    over the actor's z."""
    lo, hi = float(h.min()), float(h.max())
    mid = (lo + hi) / 2
    scale = SCALE_Z_CM
    while (hi - mid) * 100 > 32767 * scale / 128:
        scale += 100.0
    v = np.clip(np.round(32768 + (h - mid) * 100 * 128 / scale), 0, 65535).astype("<u2")
    return v, mid, scale


def spec(h: np.ndarray, extent_m: float) -> dict:
    """The import values for heights h (rows north -> south, columns west -> east, the tile
    centred on the origin): {size, sections, quads, location_cm, scale_cm} in Unreal's frame
    (+Y south: the first row is the north edge)."""
    n = size_for(h.shape[0])
    step = extent_m / (n - 1)
    _, mid, scale = encode(h)
    return {"size": [n, n], "sections": SECTIONS, "quads": QUADS,
            "location_cm": [round(-extent_m / 2 * 100, 3), round(-extent_m / 2 * 100, 3), round(mid * 100, 3)],
            "scale_cm": [round(step * 100, 6), round(step * 100, 6), scale]}


def export(store: ProjectStore, out_dir) -> dict:
    """Write the raw heightmap under out_dir (export/) and return the manifest's "landscape_build":
    {raw, sha, size, sections, quads, location_cm, scale_cm, material, label, hide_slots}."""
    from . import greybox
    from .stages import sw_site
    layout = sw_site.load_layout(store)
    meta = read_json(sw_site.site_dir(store) / "terrain.json")
    from PIL import Image
    h = greybox.from_png16(np.array(Image.open(sw_site.site_dir(store) / "terrain.png")), meta["z_range"])
    s = spec(h, meta["extent_m"])
    v, _, _ = encode(resample(h, s["size"][0]))
    raw = out_dir / RAW
    raw.parent.mkdir(parents=True, exist_ok=True)
    data = v.tobytes()
    raw.write_bytes(data)
    gb = sw_site.load_greybox(store)
    rec = s | {"raw": str(raw), "sha": hashlib.sha256(data + repr(sorted(s.items())).encode()).hexdigest()[:16],
               "material": MATERIAL, "label": LABEL,
               "hide_slots": [x["id"] for x in gb["slots"] if x.get("type") == "terrain"],
               "spacing_m": round(s["scale_cm"][0] / 100, 4), "extent_m": layout.terrain.extent_m}
    write_json(out_dir / "terrain" / "landscape.json", rec)
    return rec


def ground_at(rec: dict, raw: bytes, x: float, y: float) -> float:
    """The Landscape's height (m) at Blender (x, y): for checks."""
    n = rec["size"][0]
    v = np.frombuffer(raw, "<u2").reshape(n, n).astype(np.float64)
    step = rec["scale_cm"][0] / 100
    j = (x + (n - 1) * step / 2) / step
    i = ((n - 1) * step / 2 - y) / step
    i0, j0 = min(max(int(math.floor(i)), 0), n - 2), min(max(int(math.floor(j)), 0), n - 2)
    a, b = i - i0, j - j0
    z = (v[i0, j0] * (1 - a) * (1 - b) + v[i0 + 1, j0] * a * (1 - b) + v[i0, j0 + 1] * (1 - a) * b
         + v[i0 + 1, j0 + 1] * a * b)
    return float(rec["location_cm"][2] / 100 + (z - 32768) * rec["scale_cm"][2] / 128 / 100)

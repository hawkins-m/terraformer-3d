"""Stage 3: starred reference sheets -> individual views, cut out with SAM 3.1.

- object sheet: the asset's noun is segmented; the largest distinct objects, left to
  right, become views 1..3, each with the view the sheet asked for there (front / side /
  back; Flux often draws three-quarter views anyway, so treat it as a hint).
- kit sheet: every piece's noun is segmented; pieces are assigned left to right in the
  order the sheet asked for them (only when the count matches; otherwise unassigned).
- material sheets aren't cut (a texture isn't a 3D asset).
If SAM finds fewer objects than expected, the sheet is split on its white background
instead (segment.white_components).
Views found by SAM get the floor and cast shadow under the object matted out to pure white
(matte_ground, [views] matte_ground), so TRELLIS doesn't build them as a ground slab.

Layout under the project:
    views/<plan>/<unit>/<sheet stem>_v<i>.png   the view on white, square, padded (see band_crops)
    views/<plan>/<unit>/meta.json               {"views": [{file, sheet, asset, position, asked, method, ...}]}
"""
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .. import config, filestore, review, segment
from ..comfy.client import ComfyClient, connect
from ..jobs import check_canceled
from ..project import ProjectStore, read_json, write_json
from . import s2_refs

VIEWS = "views"
ASKED = ("front", "side", "back")


def _unit_for_sheet(store: ProjectStore, sheet_key: str) -> s2_refs.Unit:
    parts = Path(sheet_key).parts
    if len(parts) != 4 or parts[0] != s2_refs.REFS:
        raise ValueError(f"not a reference sheet: {sheet_key}")
    plan, key = parts[1], parts[2]
    for u in s2_refs.units(store, plan):
        if u.key == key:
            return u
    raise ValueError(f"{sheet_key}: no included asset or kit {key!r} in plan {plan} any more")


def view_dir(store: ProjectStore, unit: s2_refs.Unit) -> Path:
    return store.root / VIEWS / unit.plan / unit.key


def _dedupe(dets: list[segment.Detection]) -> list[segment.Detection]:
    """Largest first; drop masks mostly inside one already kept (a part of an object, or
    the same object found under two nouns)."""
    kept = []
    for d in sorted(dets, key=lambda d: -d.area_frac):
        if all(segment.overlap(d.mask, k.mask) <= 0.5 for k in kept):
            kept.append(d)
    return kept


def y0_of(top: int, bottom: int, base: float) -> int:
    return int(bottom - base * (bottom - top))


def matte_ground(px: np.ndarray, mask: np.ndarray, x0: int, x1: int, grow: float = 0.03,
                 base: float = 0.25, white: int = 245, chroma: int = 24, smooth: float = 6.0) -> int:
    """The floor and cast shadow under one view, in place: in columns x0..x1, in the object's lowest
    `base` of height or below it, outside its SAM mask grown by `grow` x its size, the pixels that
    look like shadow (near-grey: channel spread < `chroma`; soft: their 7 px neighbourhood's
    brightness varies by < `smooth`), and grey pixels at their rim, become pure white. SAM masks often miss part of a plinth or a
    thin part, and those have edges and texture, so they stay; higher up nothing changes. Returns
    the pixels whitened. A mask that misses much of its band's content (SAM found part of the
    object, or one of two) is no guide: nothing changes (-1)."""
    from scipy import ndimage
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return 0
    top, bottom = ys.min(), ys.max()
    size = max(bottom - top, xs.max() - xs.min()) + 1
    content = px[:y0_of(top, bottom, base), x0:x1].min(axis=2) < white       # above the floor zone
    grown = ndimage.distance_transform_edt(~mask[:, x0:x1]) <= max(2.0, grow * size)
    if content.any() and (content & grown[:len(content)]).sum() < 0.9 * content.sum():
        return -1
    y0 = y0_of(top, bottom, base)
    sub = px[y0:, x0:x1].astype(np.float32)
    near = grown[y0:]
    lum = sub.mean(axis=2)
    mean = ndimage.uniform_filter(lum, 7)
    std = np.sqrt(np.maximum(ndimage.uniform_filter(lum * lum, 7) - mean * mean, 0))
    grey = (sub.max(axis=2) - sub.min(axis=2) < chroma) & (sub.min(axis=2) < white)
    hit = grey & (std < smooth) & ~near
    # a shadow's hard rim against the white reads as an edge: grey pixels next to it go too, unless
    # strongly textured (stone joints, a railing)
    hit |= ndimage.binary_dilation(hit, iterations=3) & grey & (std < 6 * smooth) & ~near
    left = grey & ~hit & ~near                          # specks left of it (a rim's corners)
    lab, n = ndimage.label(left)
    if n:
        sizes = np.bincount(lab.ravel())
        touch = np.unique(lab[ndimage.binary_dilation(hit) & left])
        small = np.zeros(n + 1, bool)
        small[touch] = sizes[touch] <= 64
        small[0] = False
        hit |= small[lab]
    px[y0:, x0:x1][hit] = 255
    return int(hit.sum())


def band_crops(sheet: Image.Image, dets: list[segment.Detection], white: int = 245,
               pad_frac: float = 0.08, matte: bool = False, matted: list | None = None) -> list[Image.Image]:
    """One crop per view (dets sorted left to right): the sheet's full-height band between
    the midpoints to the neighbouring views, trimmed to its non-white content, padded to a
    white square. Masks are only used to find the views: tight masks drop white or thin
    parts (snow on a shrub, the flowers in a planter), and TRELLIS removes the white
    background itself (BiRefNet). matte: the floor and shadow under each view whitened first
    (matte_ground; the pixel counts go to `matted`)."""
    w, h = sheet.size
    px = np.array(sheet)
    edges = [0] + [(dets[i - 1].bbox[2] + dets[i].bbox[0]) // 2 for i in range(1, len(dets))] + [w]
    crops = []
    for i, d in enumerate(dets):
        x0, x1 = edges[i], edges[i + 1]
        if x1 <= x0:  # overlapping boxes: fall back to the view's own box
            x0, x1 = d.bbox[0], d.bbox[2]
        if matte:
            n = matte_ground(px, d.mask, x0, x1, white=white)
            if matted is not None:
                matted.append(n)
        band = px[:, x0:x1].min(axis=2) < white
        ys, xs = np.nonzero(band)
        if not len(xs):
            bx0, by0, bx1, by1 = d.bbox
        else:
            bx0, by0, bx1, by1 = x0 + xs.min(), ys.min(), x0 + xs.max() + 1, ys.max() + 1
        side = int(max(bx1 - bx0, by1 - by0) * (1 + 2 * pad_frac))
        sq = Image.new("RGB", (side, side), "white")
        region = Image.fromarray(px).crop((bx0, by0, bx1, by1))
        sq.paste(region, ((side - region.width) // 2, (side - region.height) // 2))
        crops.append(sq)
    return crops


def cut(store: ProjectStore, sheet: str, client: ComfyClient | None = None) -> list[dict]:
    """Cut one sheet into views (replacing earlier views of that sheet)."""
    key = review.rel(store, sheet)
    unit = _unit_for_sheet(store, key)
    if unit.kind == "material":
        return []
    c = client or connect()
    path = store.root / key
    expected = len(unit.assets) if unit.kind == "kit" else len(ASKED)
    nouns = list(dict.fromkeys((a.noun or a.name) for a in unit.assets))
    t = time.time()
    dets = []
    for noun in nouns:
        check_canceled()
        dets += segment.detect(c, path, noun, max_dets=expected + 3)
    dets = _dedupe(dets)
    if dets:  # stray specks and props next to the object are much smaller than the views
        biggest = dets[0].area_frac
        dets = [d for d in dets if d.area_frac >= 0.2 * biggest]
    method = "sam"
    if len(dets) < expected:
        fallback = segment.white_components(path)
        if len(fallback) > len(dets):
            dets, method = fallback, "white"
    dets = sorted(dets[:expected], key=lambda d: (d.bbox[0] + d.bbox[2]) / 2)

    out = view_dir(store, unit)
    out.mkdir(parents=True, exist_ok=True)
    meta = read_json(out / "meta.json", default=None) or {"plan": unit.plan, "unit": unit.key, "views": []}
    stale = [v for v in meta["views"] if v["sheet"] == key]
    review.forget(store, [(out / v["file"]).relative_to(store.root).as_posix() for v in stale])
    for v in stale:
        filestore.release(store, out / v["file"])
    meta["views"] = [v for v in meta["views"] if v["sheet"] != key]

    views = []
    sheet_img = Image.open(path).convert("RGB")
    matte = method == "sam" and bool(config.get("views.matte_ground", True))
    matted = []
    for i, (d, crop) in enumerate(zip(dets, band_crops(sheet_img, dets, matte=matte, matted=matted))):
        f = out / f"{Path(key).stem}_v{i + 1}.png"
        filestore.detach(f)
        crop.save(f)
        filestore.intern(store, f)
        if unit.kind == "kit":
            asset = unit.assets[i].id if len(dets) == expected else None
            asked = None
        else:
            asset, asked = unit.assets[0].id, ASKED[i] if len(dets) == len(ASKED) else None
        views.append({"file": f.name, "sheet": key, "asset": asset, "position": i + 1,
                      "asked": asked, "method": method, "bbox": list(d.bbox),
                      "area_frac": round(d.area_frac, 4), "matted_px": matted[i] if matte else None})
    meta["views"] += views
    write_json(out / "meta.json", meta)
    store.log_run({"stage": "views.cut", "sheet": key, "unit": unit.key, "n": len(views),
                   "expected": expected, "method": method, "seconds": round(time.time() - t, 1)})
    return views


def cut_starred(store: ProjectStore, log=print) -> int:
    """Cut every starred sheet that has no views yet. Returns the number of views made."""
    n = 0
    for key in review.starred(store, s2_refs.REFS + "/"):
        try:
            unit = _unit_for_sheet(store, key)
        except ValueError as e:
            log(f"skip {e}")
            continue
        if unit.kind == "material":
            continue
        meta = read_json(view_dir(store, unit) / "meta.json", default=None) or {"views": []}
        if any(v["sheet"] == key for v in meta["views"]):
            continue
        log(key)
        n += len(cut(store, key))
    return n


def views(store: ProjectStore, plan: str, asset_id: str) -> list[dict]:
    """Views of one asset (from its own sheets and from kit sheets), with project-relative
    keys under "key"."""
    out = []
    for d in sorted((store.root / VIEWS / plan).glob("*")):
        meta = read_json(d / "meta.json", default=None) or {"views": []}
        for v in meta["views"]:
            if v["asset"] == asset_id and (d / v["file"]).is_file():
                out.append(v | {"key": (d / v["file"]).relative_to(store.root).as_posix()})
    return out

"""Shared by blender_fix_glb.py and blender_cleanup.py: find and remove a ground slab under a
generated mesh (asset_pipeline/smooth.py has the why).

TRELLIS often turns a reference view's cast shadow or floor into a thin plate under the model, wider
than what stands on it. Seen from above, a slab is the part of the model's lowest layer that nothing
taller stands on:
1. footprint: where the model has geometry between `max_thickness` and 30% of its height (walls,
   piers, a plinth, steps' risers, a trunk), on a grid over its plan, and its convex hull (a
   courtyard, a pool or the passage between piers is inside the structure, not ground);
2. low layer: where it has geometry only below `max_thickness` x its height;
3. a slab is low layer reaching more than `min_overhang` x the footprint's size beyond that hull
   and covering at least `min_area` of the footprint's area. A plinth, podium or step is as tall as
   it is wide-ish and sits inside the footprint (its risers are the footprint), so it is kept;
4. the slab's top is the 90th percentile height of the overhanging layer. The mesh is cut at that
   height plus a margin (the cut faces' UVs interpolated, so textures stay), everything below goes,
   the cut is closed with flat faces underneath, stray bits of slab left above the cut go, and the
   model is re-seated so its new base is at z=0.
"""
import bmesh
import numpy as np

DEFAULTS = {"max_thickness": 0.05, "min_overhang": 0.06, "min_area": 0.04}
GRID = 128


def _tris(me):
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    idx = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", idx)
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)[idx.reshape(-1, 3)]           # [n, 3 corners, xyz]


def _samples(tri: np.ndarray, cell: float):
    """Points over every triangle, about two per grid cell of its area (corners for small ones)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)        # 3D: walls get samples up their height
    pts = [a, b, c, (a + b + c) / 3]
    k = np.minimum(np.ceil(2 * area / (cell * cell)), 256).astype(int)
    big = np.nonzero(k > 1)[0]
    if len(big):
        rep = np.repeat(big, k[big])
        rng = np.random.default_rng(0)
        u, v = rng.random(len(rep)), rng.random(len(rep))
        flip = u + v > 1
        u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
        pts.append(a[rep] + u[:, None] * (b[rep] - a[rep]) + v[:, None] * (c[rep] - a[rep]))
    return np.concatenate(pts)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    """Binary dilation by a disc of radius r cells (no scipy in Blender's Python)."""
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r:
                continue
            out |= np.roll(np.roll(np.pad(m, r), dy, 0), dx, 1)[r:-r or None, r:-r or None]
    return out


def _hull(m: np.ndarray) -> np.ndarray:
    """The convex hull of a grid mask's cells, as a mask (courtyards and gaps between piers inside)."""
    pts = sorted(set(map(tuple, np.argwhere(m))))
    if len(pts) < 3:
        return m.copy()

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]
    hull = half(pts) + half(pts[::-1])
    ii, jj = np.indices(m.shape)
    inside = np.ones(m.shape, bool)
    for (a0, a1), (b0, b1) in zip(hull, hull[1:] + hull[:1]):
        inside &= (b0 - a0) * (jj - a1) - (b1 - a1) * (ii - a0) >= -0.5 * max(abs(b0 - a0), abs(b1 - a1), 1)
    return inside | m


def detect(me, max_thickness=None, min_overhang=None, min_area=None) -> dict:
    """Where a ground slab is, without changing the mesh: {found, cut_z, thickness, overhang, area,
    reason}. Heights are fractions of the model's height, overhang of the footprint's size, area of
    the footprint's area."""
    t_max = DEFAULTS["max_thickness"] if max_thickness is None else float(max_thickness)
    o_min = DEFAULTS["min_overhang"] if min_overhang is None else float(min_overhang)
    a_min = DEFAULTS["min_area"] if min_area is None else float(min_area)
    tri = _tris(me)
    if not len(tri):
        return {"found": False, "reason": "empty mesh"}
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    h = float(hi[2] - lo[2])
    span = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    if h <= 0 or span <= 0:
        return {"found": False, "reason": "flat model"}
    cell = span / GRID
    p = _samples(tri, cell)
    ij = np.clip(((p[:, :2] - lo[:2]) / cell).astype(int), 0, GRID)
    z = (p[:, 2] - lo[2]) / h
    band = t_max
    foot = np.zeros((GRID + 1, GRID + 1), bool)
    sel = (z > band) & (z < 0.3)
    foot[ij[sel, 0], ij[sel, 1]] = True
    low = np.zeros_like(foot)
    sel = z <= band
    low[ij[sel, 0], ij[sel, 1]] = True
    if not foot.any():
        return {"found": False, "reason": "nothing stands above the lowest layer"}
    fi, fj = np.nonzero(foot)
    foot_size = max(fi.max() - fi.min(), fj.max() - fj.min()) + 1
    r = max(1, int(round(o_min * foot_size)))
    hull = _hull(foot)
    outside = low & ~_dilate(hull, r)
    area = outside.sum() / foot.sum()
    rep = {"overhang_cells": int(outside.sum()), "footprint_cells": int(foot.sum()), "area": round(float(area), 3)}
    if not outside.any() or area < a_min:
        return rep | {"found": False, "reason": "no low layer reaches beyond the footprint"}
    # how far the slab reaches: distance (cells) from the overhang to the footprint, at most
    reach, grow = r, _dilate(hull, r)
    while (outside & ~grow).any() and reach < GRID:
        reach += 2
        grow = _dilate(grow, 2)
    in_out = outside[ij[:, 0], ij[:, 1]] & (z <= band)
    top = float(np.percentile(z[in_out], 90)) if in_out.any() else band
    thick = top
    margin = max(0.15 * thick, 0.004)
    return rep | {"found": True, "thickness": round(thick, 4), "overhang": round(reach / foot_size, 3),
                  "cut_z": float(lo[2] + min(top + margin, band) * h), "min_z": float(lo[2]), "height": h}


def remove_ground(obj, max_thickness=None, min_overhang=None, min_area=None) -> dict:
    """Detect and remove a ground slab from a mesh object (Blender, applied transforms, Z up) and
    re-seat it at z=0. Returns {removed_faces, ...detect()}; removed_faces 0 = no ground found."""
    me = obj.data
    d = detect(me, max_thickness, min_overhang, min_area)
    if not d["found"]:
        return d | {"removed_faces": 0}
    bm = bmesh.new()
    bm.from_mesh(me)
    cut = d["cut_z"]
    below = sum(1 for f in bm.faces if all(v.co.z < cut for v in f.verts))
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6 * d["height"], plane_co=(0, 0, cut),
                                 plane_no=(0, 0, 1), clear_inner=True)
    cut_edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_valid]
    # stray bits of slab that poked above the cut: small pieces lying wholly just above it
    seen, strays = set(), []
    near = cut + 1.5 * d["thickness"] * d["height"]
    total = len(bm.faces)
    for f in bm.faces:
        if f in seen:
            continue
        comp, stack = [], [f]
        seen.add(f)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for k in e.link_faces:
                    if k not in seen:
                        seen.add(k)
                        stack.append(k)
        if len(comp) < 0.02 * total and max(v.co.z for g in comp for v in g.verts) <= near:
            strays += comp
    bmesh.ops.delete(bm, geom=strays, context="FACES")
    # close the cut from below (piers, walls): flat faces, unseen once the model stands on the ground
    edges = [e for e in cut_edges if e.is_valid and e.is_boundary]
    filled = bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"] if edges else []
    for v in bm.verts:
        v.co.z -= cut
    bm.to_mesh(me)
    bm.free()
    me.update()
    return d | {"removed_faces": below + len(strays), "stray_faces": len(strays),
                "filled_faces": len(filled), "cut_height": round((cut - d["min_z"]) / d["height"], 4)}

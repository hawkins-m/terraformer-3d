"""Fix a generated GLB in headless Blender (TRELLIS outputs; asset_pipeline/smooth.py): smooth its
shading, remove a ground slab, or both, in one pass.

    blender -b --factory-startup --python scripts/blender_fix_glb.py -- JOB.json

JOB: {"in": GLB, "out": GLB, "report": JSON, "angle": 30 (0 = keep the shading), "weld": 1e-5,
      "ground": null | {"max_thickness": 0.05, "min_overhang": 0.06, "min_area": 0.04}}

TRELLIS meshes come with vertices split along every UV seam and largely per-face normals, so they
shade faceted. Here:
1. weld: merge vertices closer than `weld` x the bounding box diagonal (positions only: UVs live on
   the face corners, so seams and textures stay as they were);
2. smooth by angle: every face smooth, edges sharper than `angle` degrees marked sharp (the
   procedural builds' default, building_proc.SMOOTH_ANGLE: a dome comes out smooth, steps and
   cornices stay crisp);
3. ground (when asked): a thin slab under the model that reaches beyond what stands on it is cut
   off and the model re-seated at z=0 (blender_ground.py has the rules);
4. export with those normals (and the original images, materials and UVs).
Reports vertices before and after, sharp edges, the ground found, and the seconds each step took.
"""
import json
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_ground import remove_ground  # noqa: E402
from blender_shading import weld_and_smooth  # noqa: E402

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
t0 = time.time()
angle = float(job.get("angle", 30.0) or 0)
rep = {"in": job["in"], "out": job["out"], "angle": angle}
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
# SMOOTH: smooth faces without the file's custom normals (they are what we replace)
bpy.ops.import_scene.gltf(filepath=job["in"], import_shading="SMOOTH" if angle > 0 else "NORMALS")
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
rep["import_s"] = round(time.time() - t0, 1)
rep["verts_before"] = sum(len(o.data.vertices) for o in meshes)
if angle > 0:
    t = time.time()
    welded, sharp = 0, 0
    for o in meshes:
        r = weld_and_smooth(o, angle, float(job.get("weld", 1e-5)))
        welded, sharp = welded + r["welded"], sharp + r["sharp_edges"]
    rep["welded"], rep["sharp_edges"] = welded, sharp
    rep["verts_after_weld"] = sum(len(o.data.vertices) for o in meshes)
    rep["smooth_s"] = round(time.time() - t, 1)
if job.get("ground") is not None:
    t = time.time()
    # one object in world space, so the slab is judged against the whole model
    for o in bpy.context.scene.objects:
        o.select_set(o.type == "MESH")
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    g = remove_ground(obj, **job["ground"])
    rep["ground"] = {k: g[k] for k in ("removed_faces", "thickness", "overhang", "cut_height", "reason") if k in g}
    rep["ground_s"] = round(time.time() - t, 1)
t = time.time()
bpy.ops.export_scene.gltf(filepath=job["out"], export_format="GLB", export_normals=True, export_texcoords=True,
                          export_materials="EXPORT", export_image_format="AUTO", export_yup=True)
rep["export_s"] = round(time.time() - t, 1)
rep["seconds"] = round(time.time() - t0, 1)
json.dump(rep, open(job["report"], "w"), indent=1)
print("REPORT " + json.dumps(rep))

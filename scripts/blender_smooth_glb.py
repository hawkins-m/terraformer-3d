"""Smooth a generated GLB's shading in headless Blender (TRELLIS outputs; asset_pipeline/smooth.py).

    blender -b --factory-startup --python scripts/blender_smooth_glb.py -- JOB.json

JOB: {"in": GLB, "out": GLB, "report": JSON, "angle": 30, "weld": 1e-5}

TRELLIS meshes come with vertices split along every UV seam and largely per-face normals, so they
shade faceted. Here:
1. weld: merge vertices closer than `weld` x the bounding box diagonal (positions only: UVs live on
   the face corners, so seams and textures stay as they were);
2. smooth by angle: every face smooth, edges sharper than `angle` degrees marked sharp (the
   procedural builds' default, building_proc.SMOOTH_ANGLE: a dome comes out smooth, steps and
   cornices stay crisp);
3. export with those normals (and the original images, materials and UVs).
Reports vertices before and after, sharp edges, and the seconds each step took.
"""
import json
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_shading import weld_and_smooth  # noqa: E402

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
t0 = time.time()
rep = {"in": job["in"], "out": job["out"], "angle": job.get("angle", 30.0)}
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
# SMOOTH: smooth faces without the file's custom normals (they are what we replace)
bpy.ops.import_scene.gltf(filepath=job["in"], import_shading="SMOOTH")
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
rep["import_s"] = round(time.time() - t0, 1)
rep["verts_before"] = sum(len(o.data.vertices) for o in meshes)
t = time.time()
welded, sharp = 0, 0
for o in meshes:
    r = weld_and_smooth(o, rep["angle"], float(job.get("weld", 1e-5)))
    welded, sharp = welded + r["welded"], sharp + r["sharp_edges"]
rep["welded"], rep["sharp_edges"] = welded, sharp
rep["verts_after_weld"] = sum(len(o.data.vertices) for o in meshes)
rep["smooth_s"] = round(time.time() - t, 1)
t = time.time()
bpy.ops.export_scene.gltf(filepath=job["out"], export_format="GLB", export_normals=True, export_texcoords=True,
                          export_materials="EXPORT", export_image_format="AUTO", export_yup=True)
rep["export_s"] = round(time.time() - t, 1)
rep["seconds"] = round(time.time() - t0, 1)
json.dump(rep, open(job["report"], "w"), indent=1)
print("REPORT " + json.dumps(rep))

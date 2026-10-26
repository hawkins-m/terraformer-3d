"""Shared by blender_fix_glb.py and blender_cleanup.py: weld a generated mesh and shade it smooth
by angle (asset_pipeline/smooth.py has the why)."""
import math

import bmesh
import bpy


def weld_and_smooth(obj, angle_deg: float = 30.0, weld: float = 1e-5) -> dict:
    """Merge vertices closer than `weld` x the bounding-box diagonal (positions only: UVs live on
    the face corners, so seams and textures stay), clear imported custom normals, shade every face
    smooth and mark edges sharper than angle_deg sharp. Returns {welded, sharp_edges}."""
    me = obj.data
    if me.has_custom_normals:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    xs = [v.co for v in me.vertices]
    diag = math.dist([min(c[i] for c in xs) for i in range(3)], [max(c[i] for c in xs) for i in range(3)]) if xs else 1.0
    bm = bmesh.new()
    bm.from_mesh(me)
    n0 = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=weld * (diag or 1.0))
    welded = n0 - len(bm.verts)
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    me.set_sharp_from_angle(angle=math.radians(angle_deg))
    sharp = 0
    sh = me.attributes.get("sharp_edge")
    if sh is not None:
        vals = [False] * len(me.edges)
        sh.data.foreach_get("value", vals)
        sharp = sum(vals)
    me.update()
    return {"welded": welded, "sharp_edges": sharp}

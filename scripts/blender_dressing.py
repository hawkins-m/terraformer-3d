"""Stylised fallback meshes for world-dressing roles that have no suitable CC0 asset (or whose CC0
download isn't there), as GLBs in metres with the base at z = 0 (asset_pipeline/dressing.py).

    blender -b --factory-startup --python scripts/blender_dressing.py -- JOB.json

JOB: {"out_dir": ..., "roles": ["tree_pine", ...], "seed": 7}
Each role's GLB holds a few variants (one mesh each, all at the origin); Unreal uses every mesh in
the file as a variant. Low-poly on purpose: they are instanced by the thousand.
    rock_small, rock_large   displaced icospheres, flattened
    tree_cypress             a narrow lumpy spindle on a short trunk (Italian cypress)
    tree_pine                a tall bare trunk under a wide flat cloud crown (umbrella / stone pine)
    tree_olive               a short split trunk under a loose grey-green crown
    shrub                    a few overlapping leafy blobs
    grass                    a tuft of thin blades (two-sided)
"""
import json
import math
import random
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
rng = random.Random(job.get("seed", 7))

COL = {"rock": (0.36, 0.33, 0.29), "bark": (0.16, 0.11, 0.07), "pine": (0.07, 0.14, 0.05),
       "cypress": (0.035, 0.08, 0.03), "olive": (0.2, 0.25, 0.15), "shrub": (0.08, 0.15, 0.05),
       "grass": (0.16, 0.24, 0.07)}


def material(name, rgb, rough=0.9, two_sided=False):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1.0)
    p.inputs["Roughness"].default_value = rough
    m.use_backface_culling = not two_sided
    return m


def blob(bm, centre, size, lumps=0.18, subdiv=2, mat=0):
    """A displaced icosphere (ellipsoid `size`) added to bm."""
    geom = bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)["verts"]
    seed = rng.random() * 100
    for v in geom:
        n = v.co.normalized()
        k = 1.0 + lumps * (math.sin(n.x * 5.1 + seed) * math.sin(n.y * 4.3 + seed * 0.7) * math.sin(n.z * 3.7 + seed * 1.3)
                           + rng.uniform(-0.5, 0.5))
        v.co = Vector((n.x * size[0] * k, n.y * size[1] * k, n.z * size[2] * k)) + Vector(centre)
    for f in {f for v in geom for f in v.link_faces}:
        f.material_index = mat
        f.smooth = False


def cylinder(bm, base, top, r0, r1, segs=7, mat=0):
    """A tapered cylinder from base to top (points)."""
    base, top = Vector(base), Vector(top)
    axis = top - base
    rot = axis.to_track_quat("Z", "Y").to_matrix().to_4x4()
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r0, radius2=r1, depth=axis.length,
                                matrix=Matrix.Translation(base + axis / 2) @ rot)
    for f in {f for v in res["verts"] for f in v.link_faces}:
        f.material_index = mat


def obj(name, bm, mats):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    lo = min(v.co.z for v in me.vertices)             # base at z = 0
    for v in me.vertices:
        v.co.z -= lo
    return o


def rock(name, size):
    bm = bmesh.new()
    blob(bm, (0, 0, 0), (size * rng.uniform(0.8, 1.2), size * rng.uniform(0.6, 0.9), size * rng.uniform(0.4, 0.6)),
         lumps=0.28, subdiv=2)
    return obj(name, bm, [material("rock", COL["rock"], 0.85)])


def cypress(name):
    bm = bmesh.new()
    h = rng.uniform(10.0, 13.0)
    cylinder(bm, (0, 0, 0), (0, 0, 1.6), 0.22, 0.16, mat=0)
    n = 6
    for i in range(n):                       # a spindle of stacked blobs, widest a third of the way up
        t = (i + 0.5) / n
        w = 1.0 * math.sin(math.pi * (0.12 + 0.8 * t)) ** 0.7 + 0.15
        blob(bm, (rng.uniform(-0.08, 0.08), rng.uniform(-0.08, 0.08), 1.2 + t * (h - 1.4)), (w, w, (h - 1.2) / n * 0.9),
             lumps=0.12, mat=1)
    return obj(name, bm, [material("bark", COL["bark"]), material("cypress", COL["cypress"], 0.95)])


def pine(name):
    bm = bmesh.new()
    h = rng.uniform(10.0, 13.0)
    lean = Vector((rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6), 0))
    top = Vector((0, 0, h - 2.2)) + lean
    cylinder(bm, (0, 0, 0), top, 0.5, 0.32, mat=0)
    for _ in range(3):                       # a few bare branches into the crown
        d = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), 0)).normalized() * rng.uniform(1.5, 2.5)
        cylinder(bm, top - Vector((0, 0, 0.8)), top + d + Vector((0, 0, 0.6)), 0.2, 0.12, segs=5, mat=0)
    for i in range(8):                       # the cloud crown: a low dome, higher in the middle
        a, r = rng.uniform(0, 2 * math.pi), (0.0 if i == 0 else rng.uniform(1.2, 3.0))
        blob(bm, top + Vector((r * math.cos(a), r * math.sin(a), 0.9 + 0.5 * (1 - r / 3.0))),
             (rng.uniform(2.0, 2.8), rng.uniform(2.0, 2.8), rng.uniform(1.2, 1.6)), lumps=0.18, mat=1)
    return obj(name, bm, [material("bark", COL["bark"]), material("pine", COL["pine"], 0.95)])


def olive(name):
    bm = bmesh.new()
    split = Vector((0, 0, rng.uniform(1.0, 1.4)))
    cylinder(bm, (0, 0, 0), split, 0.35, 0.28, mat=0)
    tops = []
    for k in range(2):                       # the trunk splits in two leaning stems
        a = rng.uniform(0, 2 * math.pi) + k * math.pi
        t = split + Vector((math.cos(a) * 0.9, math.sin(a) * 0.9, 1.3))
        cylinder(bm, split, t, 0.22, 0.14, segs=6, mat=0)
        tops.append(t)
    for _ in range(6):                       # a loose round crown over both
        c = rng.choice(tops)
        blob(bm, c + Vector((rng.uniform(-1.2, 1.2), rng.uniform(-1.2, 1.2), rng.uniform(0.2, 1.3))),
             (rng.uniform(1.0, 1.6), rng.uniform(1.0, 1.6), rng.uniform(0.8, 1.1)), lumps=0.2, mat=1)
    return obj(name, bm, [material("bark", COL["bark"]), material("olive", COL["olive"], 0.9)])


def shrub(name):
    bm = bmesh.new()
    for _ in range(4):
        blob(bm, (rng.uniform(-0.35, 0.35), rng.uniform(-0.35, 0.35), rng.uniform(0.3, 0.6)),
             (rng.uniform(0.4, 0.6), rng.uniform(0.4, 0.6), rng.uniform(0.35, 0.5)), lumps=0.2, subdiv=1)
    return obj(name, bm, [material("shrub", COL["shrub"], 0.95)])


def grass(name):
    bm = bmesh.new()
    for _ in range(14):
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(0, 0.12)
        x, y = r * math.cos(a), r * math.sin(a)
        h, w = rng.uniform(0.3, 0.6), 0.025
        tip = (x + math.cos(a) * h * 0.35, y + math.sin(a) * h * 0.35, h)
        n = Vector((-math.sin(a), math.cos(a), 0)) * w
        vs = [bm.verts.new((x - n.x, y - n.y, 0)), bm.verts.new((x + n.x, y + n.y, 0)), bm.verts.new(tip)]
        bm.faces.new(vs)
    return obj(name, bm, [material("grass", COL["grass"], 0.95, two_sided=True)])


MAKERS = {"rock_small": lambda n: rock(n, 0.5), "rock_large": lambda n: rock(n, 1.4), "tree_cypress": cypress,
          "tree_pine": pine, "tree_olive": olive, "shrub": shrub, "grass": grass}
VARIANTS = {"grass": 2}

made = {}
for role in job["roles"]:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for i in range(VARIANTS.get(role, 3)):
        MAKERS[role](f"{role}_{i}")
    path = f"{job['out_dir']}/{role}.glb"
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=False, export_apply=True)
    made[role] = path
print("REPORT " + json.dumps({"made": made}))

"""World dressing in the Unreal level (editor Python, UE 5.8): run inside `ap_import.py` when its args
name export/dressing.json (asset_pipeline/dressing.py writes it on every `ap ue build`).

What it makes (all under /Game/AP/Dressing):
    <role>/<file>_<sha>/          a CC0 or fallback mesh file imported as is (every static mesh in it
                                  is a variant); Megascans roles use the mapped asset (a foliage
                                  type's mesh) and fall back to these when it's gone
    Materials/M_AP_GroundDress    world-aligned ground: grass, dirt patches (macro noise), rock by
                                  slope, sand near sea level, macro brightness variation
    Materials/M_AP_SeaSimple      opaque sea: depth colour from a depth map, two panning wave
                                  normals, shoreline foam
    AP_Dress_<role>               one actor per role, a HierarchicalInstancedStaticMeshComponent per
                                  variant (no collision; grass and shrubs fade out: cull distances)
    AP_Dress_ocean                the Water plugin's ocean (experimental; else the simple sea)

Ownership: actors tagged ap:dress=<role> and components tagged ap:dress_hidden / ap:dress_mat are the
dressing's. Every run removes roles no longer in the plan, shows placeholders it no longer hides and
clears material overrides it no longer sets. Instances are re-placed only when the plan's key differs
from the one on the role's actor (ap:dress_key).
"""
import json
import math
import os
import sys

import unreal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ap_import  # noqa: E402
import ap_material_preview as amp  # noqa: E402

eal = unreal.EditorAssetLibrary
mel = unreal.MaterialEditingLibrary
ROOT = "/Game/AP/Dressing"
MATS = f"{ROOT}/Materials"
TEX = f"{ROOT}/Textures"
GROUND_MAT, SEA_MAT = f"{MATS}/M_AP_GroundDress", f"{MATS}/M_AP_SeaSimple"
MAT_VERSION = "4"
HIDDEN, OVERRIDE = "ap:dress_hidden", "ap:dress_mat"


def log(msg):
    ap_import.log(f"dress {msg}")


# --- small helpers --------------------------------------------------------------------------------

def comp_tags(c):
    return [str(t) for t in c.get_editor_property("component_tags")]


def set_comp_tag(c, tag, on):
    tags = [t for t in comp_tags(c) if t != tag] + ([tag] if on else [])
    c.set_editor_property("component_tags", [unreal.Name(t) for t in tags])


def add_hism(actor, name):
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    root = sds.k2_gather_subobject_data_for_instance(actor)[0]
    handle, fail = sds.add_new_subobject(
        unreal.AddNewSubobjectParams(parent_handle=root, new_class=unreal.HierarchicalInstancedStaticMeshComponent))
    if str(fail):
        raise RuntimeError(f"{actor.get_actor_label()}: can't add {name}: {fail}")
    sds.rename_subobject(handle, unreal.Text(name))
    return unreal.SubobjectDataBlueprintFunctionLibrary.get_object(unreal.SubobjectDataBlueprintFunctionLibrary.get_data(handle))


def import_texture(path, name, kind):
    """A generated map (sea depth, waves, macro noise), re-imported in place when its content changes."""
    target = f"{TEX}/{name}"
    digest = ap_import.sha(path)
    tex = eal.load_asset(target) if eal.does_asset_exist(target) else None
    if tex is None or eal.get_metadata_tag(tex, "ap:sha") != digest:
        task = unreal.AssetImportTask()
        task.filename, task.destination_path, task.destination_name = path, TEX, name
        task.automated, task.replace_existing, task.save = True, True, False
        amp.tools.import_asset_tasks([task])
        tex = eal.load_asset(target)
        if tex is None:
            raise RuntimeError(f"could not import {path}")
        if kind == "normal":
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
            tex.set_editor_property("srgb", False)
        elif kind == "data":
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
            tex.set_editor_property("srgb", False)
        if kind == "data_clamp":
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("address_x", unreal.TextureAddress.TA_CLAMP)
            tex.set_editor_property("address_y", unreal.TextureAddress.TA_CLAMP)
        eal.set_metadata_tag(tex, "ap:sha", digest)
        eal.save_loaded_asset(tex)
    return tex


class Graph:
    """Builds a material's node graph (UE's MaterialEditingLibrary, positions only for looks)."""

    def __init__(self, m):
        self.m, self.y = m, 0

    def e(self, cls, x=-1200, y=None, **props):
        if y is None:
            self.y += 120
            y = self.y
        n = mel.create_material_expression(self.m, cls, x, y)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def link(self, a, b, pin="", out=""):
        mel.connect_material_expressions(a, out, b, pin)

    def scalar(self, name, v):
        return self.e(unreal.MaterialExpressionScalarParameter, -2400, parameter_name=name, default_value=v)

    def vec(self, name, rgb):
        return self.e(unreal.MaterialExpressionVectorParameter, -2400, parameter_name=name,
                      default_value=unreal.LinearColor(*rgb, 1.0))

    def op(self, cls, a, b):
        n = self.e(cls)
        self.link(a, n, "A")
        self.link(b, n, "B")
        return n

    def mul(self, a, b):
        return self.op(unreal.MaterialExpressionMultiply, a, b)

    def add(self, a, b):
        return self.op(unreal.MaterialExpressionAdd, a, b)

    def sub(self, a, b):
        return self.op(unreal.MaterialExpressionSubtract, a, b)

    def div(self, a, b):
        return self.op(unreal.MaterialExpressionDivide, a, b)

    def const(self, v):
        return self.e(unreal.MaterialExpressionConstant, -2400, r=v)

    def sat(self, a):
        n = self.e(unreal.MaterialExpressionSaturate)
        self.link(a, n)
        return n

    def mask(self, a, r=False, g=False, b=False, out=""):
        n = self.e(unreal.MaterialExpressionComponentMask, r=r, g=g, b=b, a=False)
        self.link(a, n, "", out)
        return n

    def lerp(self, a, b, alpha, out=""):
        n = self.e(unreal.MaterialExpressionLinearInterpolate)
        self.link(a, n, "A", out)
        self.link(b, n, "B", out)
        self.link(alpha, n, "Alpha")
        return n

    def tex(self, name, texture, uv, kind="color", clamp=False):
        sampler = {"normal": unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL, "data": unreal.MaterialSamplerType.SAMPLERTYPE_MASKS,
                   "rough": unreal.MaterialSamplerType.SAMPLERTYPE_MASKS}.get(kind, unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
        n = self.e(unreal.MaterialExpressionTextureSampleParameter2D, -1800, parameter_name=name, texture=texture,
                   sampler_type=sampler,
                   sampler_source=unreal.SamplerSourceMode.SSM_CLAMP_WORLD_GROUP_SETTINGS if clamp
                   else unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS)
        self.link(uv, n, "UVs")
        return n

    def out(self, node, prop, pin=""):
        mel.connect_material_property(node, pin, prop)


def _material(path):
    m = eal.load_asset(path) if eal.does_asset_exist(path) else None
    if m and eal.get_metadata_tag(m, "ap:version") == MAT_VERSION:
        return m, False
    if m:
        mel.delete_all_material_expressions(m)
    else:
        folder, name = path.rsplit("/", 1)
        m = amp.tools.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    return m, True


def _done(m):
    mel.recompile_material(m)
    eal.set_metadata_tag(m, "ap:version", MAT_VERSION)
    eal.save_loaded_asset(m)


def _instance(path, parent):
    folder, name = path.rsplit("/", 1)
    mi = eal.load_asset(path) if eal.does_asset_exist(path) else amp.tools.create_asset(
        name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    mel.set_material_instance_parent(mi, parent)
    return mi


# --- ground ---------------------------------------------------------------------------------------

LAYERS = (("Grass", "ground_grass", 3.0), ("Dirt", "ground_dirt", 3.5), ("Rock", "ground_rock", 6.0), ("Sand", "ground_sand", 4.0))
WHITE, FLAT = "/Engine/EngineResources/WhiteSquareTexture", "/Engine/EngineMaterials/DefaultNormal"
TINTS = {"Grass": (1.0, 0.93, 0.72)}          # a drier, warmer grass than the lawn scans (editable in the instance)


def ground_material(noise_tex, white_mask):
    m, fresh = _material(GROUND_MAT)
    if not fresh:
        return m
    g = Graph(m)
    wp = g.e(unreal.MaterialExpressionWorldPosition, -2800, 0)
    xy = g.mask(wp, r=True, g=True)
    z = g.mask(wp, b=True)
    cm = g.const(100.0)
    white, flat = ap_import.engine_asset(WHITE), ap_import.engine_asset(FLAT)
    cols, nrms, rghs = [], [], []
    for name, _, tile in LAYERS:
        uv = g.div(xy, g.mul(g.scalar(f"{name}TileM", tile), cm))
        c = g.mul(g.tex(f"{name}Color", white, uv), g.vec(f"{name}Tint", TINTS.get(name, (1, 1, 1))))
        cols.append(c)
        nrms.append(g.tex(f"{name}Normal", flat, uv, "normal"))
        rghs.append(g.mask(g.tex(f"{name}Rough", white_mask, uv, "rough"), g=True))
    # macro noise: brightness and the dirt patches. Mostly a large sample (410 m) with a smaller one
    # (71 m) on rotated coordinates, so no tile repeats along the axes (from the air a short axis-
    # aligned tile reads as a lattice of dots)
    ma = g.mask(g.tex("MacroNoise", noise_tex, g.div(xy, g.mul(g.scalar("MacroTileM", 410.0), cm)), "data"), r=True)
    u, v = g.mask(xy, r=True), g.mask(xy, g=True)
    rot = g.e(unreal.MaterialExpressionAppendVector)
    g.link(g.sub(g.mul(u, g.const(0.8)), g.mul(v, g.const(0.6))), rot, "A")
    g.link(g.add(g.mul(u, g.const(0.6)), g.mul(v, g.const(0.8))), rot, "B")
    mb = g.mask(g.tex("MacroNoise2", noise_tex, g.div(rot, g.mul(g.scalar("MacroTile2M", 71.0), cm)), "data"), g=True)
    mix = g.add(g.mul(ma, g.const(0.65)), g.mul(mb, g.const(0.35)))
    dirt = g.sat(g.div(g.sub(mix, g.sub(g.const(1.0), g.scalar("DirtAmount", 0.3))), g.scalar("DirtSoft", 0.06)))
    nz = g.mask(g.e(unreal.MaterialExpressionVertexNormalWS, -2800), b=True)
    rock = g.sat(g.div(g.sub(g.scalar("RockSlopeCos", 0.82), nz), g.scalar("SlopeBlend", 0.07)))
    top = g.add(g.mul(g.scalar("SandTopM", 2.0), cm), g.scalar("SeaLevelCm", 0.0))
    sand = g.sat(g.div(g.sub(top, z), g.mul(g.scalar("SandBlendM", 1.5), cm)))

    def blend(xs, out=""):
        a = g.lerp(xs[0], xs[1], dirt, out)
        a = g.lerp(a, xs[3], sand, out)
        return g.lerp(a, xs[2], rock, out)
    bright = g.lerp(g.sub(g.const(1.0), g.scalar("MacroStrength", 0.25)), g.add(g.const(1.0), g.scalar("MacroStrength2", 0.25)), ma)
    g.out(g.mul(blend(cols), bright), unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(blend(nrms, "RGB"), unreal.MaterialProperty.MP_NORMAL)
    g.out(g.mul(blend(rghs), g.scalar("RoughnessMultiplier", 1.0)), unreal.MaterialProperty.MP_ROUGHNESS)
    _done(m)
    log("built ground material")
    return m


def mi_textures(path):
    """A Megascans surface material instance's {color, normal, rough} textures (by parameter name)."""
    mi = unreal.load_asset(path.split(".")[0]) if path else None
    if mi is None:
        return None
    tex = {}
    for p in mi.get_editor_property("texture_parameter_values"):
        n, t = str(p.parameter_info.name).lower(), p.parameter_value
        if t is None:
            continue
        if any(k in n for k in ("albedo", "basecolor", "base color", "diffuse")):
            tex.setdefault("color", t)
        elif "normal" in n:
            tex.setdefault("normal", t)
        elif "rough" in n or "ord" in n:
            tex.setdefault("rough", t)
    # a map whose colour space doesn't fit its sampler fails the whole material: leave it out
    ok = {"color": lambda t: t.get_editor_property("srgb"),
          "normal": lambda t: t.get_editor_property("compression_settings") == unreal.TextureCompressionSettings.TC_NORMALMAP,
          "rough": lambda t: not t.get_editor_property("srgb")}
    tex = {k: t for k, t in tex.items() if ok[k](t)}
    return tex if "color" in tex else None


def ground(spec, noise_png, eas, report, used):
    """The ground material on the terrain slots and the outer ring."""
    noise = import_texture(noise_png, "T_AP_MacroNoise", "data")
    mi = _instance(f"{MATS}/MI_AP_GroundDress", ground_material(noise, import_texture(spec["white_mask"], "T_AP_WhiteMask", "data")))
    for name, role, _ in LAYERS:
        src = spec["layers"].get(role) or {}
        tex = None
        if src.get("source") == "megascans":
            tex = mi_textures(src.get("asset"))
            if tex:
                used[role] = "megascans"
            else:
                report["warnings"].append(f"{role}: Megascans {src.get('asset')} not found or has no maps: CC0 used")
                src = src.get("fallback") or {}
        if tex is None and src.get("color"):
            tex = {k: amp.import_texture(src[k], TEX, k) for k in ("color", "normal", "rough")}
            used[role] = "cc0"
        if tex is None:
            used[role] = "none"
            continue
        for k, p in (("color", "Color"), ("normal", "Normal"), ("rough", "Rough")):
            if tex.get(k) is not None:
                mel.set_material_instance_texture_parameter_value(mi, f"{name}{p}", tex[k])
    mel.set_material_instance_scalar_parameter_value(mi, "SeaLevelCm", float(spec.get("sea_z_cm", 0.0)))
    mel.update_material_instance(mi)
    eal.save_loaded_asset(mi)
    set_on = []
    actors = {ap_import.tagged(a).get("ap:id"): a for a in eas.get_all_level_actors() if "ap:id" in ap_import.tagged(a)}
    for sid in spec["slots"]:
        for ism in ap_import.ism_components(actors[sid]).values() if sid in actors else []:
            for i in range(ism.get_num_materials()):
                ism.set_material(i, mi)
            set_comp_tag(ism, OVERRIDE, True)
            set_on.append(ism)
    for a in eas.get_all_level_actors():
        if ap_import.tagged(a).get("ap:terrain") == "outer":
            c = a.static_mesh_component
            for i in range(c.get_num_materials()):
                c.set_material(i, mi)
            set_comp_tag(c, OVERRIDE, True)
            set_on.append(c)
    return set_on


# --- water ----------------------------------------------------------------------------------------

def sea_material(depth_tex, wave_tex):
    m, fresh = _material(SEA_MAT)
    if not fresh:
        return m
    g = Graph(m)
    wp = g.e(unreal.MaterialExpressionWorldPosition, -2800, 0)
    xy = g.mask(wp, r=True, g=True)
    cm = g.const(100.0)
    origin = g.mask(g.vec("DepthOrigin", (0, 0, 0)), r=True, g=True)
    uvd = g.div(g.sub(xy, origin), g.scalar("DepthSize", 800000.0))
    depth = g.mask(g.tex("DepthTex", depth_tex, uvd, "data", clamp=True), r=True)
    col = g.lerp(g.vec("ShallowColor", (0.05, 0.32, 0.3)), g.vec("DeepColor", (0.008, 0.05, 0.09)),
                 g.sat(g.mul(depth, g.scalar("DepthContrast", 2.5))))

    def waves(tile, sx, sy):
        uv = g.div(xy, g.mul(g.scalar(tile[0], tile[1]), cm))
        pan = g.e(unreal.MaterialExpressionPanner, speed_x=sx, speed_y=sy)
        g.link(uv, pan, "Coordinate")
        return g.tex(f"Waves{tile[0]}", wave_tex, pan, "normal")
    w1, w2 = waves(("WaveTileM", 14.0), 0.012, 0.007), waves(("WaveTile2M", 41.0), -0.005, 0.009)
    n = g.lerp(w1, w2, g.const(0.5), "RGB")
    flat = g.e(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0, 0, 1, 1))
    g.out(g.lerp(flat, n, g.scalar("WaveStrength", 0.7)), unreal.MaterialProperty.MP_NORMAL)
    # foam: shallow water (depth map), broken up by the waves' red channel
    shallow = g.sat(g.sub(g.const(1.0), g.mul(depth, g.scalar("FoamDepthScale", 25.0))))
    foam = g.sat(g.mul(g.mul(shallow, g.add(g.mask(w1, r=True), g.const(0.3))), g.scalar("FoamStrength", 1.4)))
    g.out(g.lerp(col, g.vec("FoamColor", (0.75, 0.78, 0.78)), foam), unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.lerp(g.scalar("Roughness", 0.04), g.const(0.6), foam), unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(g.scalar("Specular", 0.6), unreal.MaterialProperty.MP_SPECULAR)
    _done(m)
    log("built sea material")
    return m


def simple_sea(spec, eas):
    d = spec["depth"]
    depth = import_texture(d["png"], "T_AP_SeaDepth", "data_clamp")
    waves = import_texture(spec["waves"], "T_AP_WaveNormal", "normal")
    mi = _instance(f"{MATS}/MI_AP_SeaSimple", sea_material(depth, waves))
    mel.set_material_instance_vector_parameter_value(mi, "DepthOrigin", unreal.LinearColor(d["origin_cm"][0], d["origin_cm"][1], 0, 1))
    mel.set_material_instance_scalar_parameter_value(mi, "DepthSize", float(d["size_cm"]))
    mel.set_material_instance_texture_parameter_value(mi, "DepthTex", depth)
    mel.update_material_instance(mi)
    eal.save_loaded_asset(mi)
    set_on = []
    for a in eas.get_all_level_actors():
        if ap_import.tagged(a).get("ap:id") in spec["slots"]:
            for ism in ap_import.ism_components(a).values():
                for i in range(ism.get_num_materials()):
                    ism.set_material(i, mi)
                set_comp_tag(ism, OVERRIDE, True)
                set_on.append(ism)
    return set_on


def ocean(spec, eas, existing, report):
    """The Water plugin's ocean at sea level, its spline kept small at the origin (under the city:
    the ocean fills everything outside it), extents over the whole terrain. Returns (actor, None) or
    (None, why it can't be made)."""
    cmd = unreal.SystemLibrary.get_command_line().lower()
    if "-run=" in cmd or "-nullrhi" in cmd:
        # UE 5.8: placing a water body runs the WaterEditor module's landscape-brush setup, which
        # asserts on Slate and kills the commandlet (2026-10-25); never try it headless
        return None, ("UE 5.8's Water plugin crashes the headless editor when it places a water body; the plugin "
                      "is enabled, so you can add a Water Body Ocean by hand in the editor")
    cls = getattr(unreal, "WaterBodyOcean", None)
    if cls is None:
        why = "the Water plugin isn't loaded"
        if spec.get("plugin_enabled_now"):
            why += " (it was just enabled in the .uproject but didn't load in this run)"
        return None, why
    a = existing.get("ocean")
    try:
        if a is None:
            a = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, spec["sea_z_cm"]))
        if a is None:
            return None, "spawning WaterBodyOcean failed headless"
        a.set_actor_label("AP_Dress_ocean")
        ap_import.set_tags(a, ["ap:dress=ocean", "ap:source=pipeline-dress"])
        a.set_folder_path("AP/Dressing")
        size = float(spec["depth"]["size_cm"]) * 2
        comp = a.get_editor_property("water_body_component") if hasattr(a, "get_editor_property") else None
        notes = []
        for obj, prop, val in ((comp, "ocean_extents", unreal.Vector2D(size, size)),):
            try:
                obj.set_editor_property(prop, val)
            except Exception as e:  # noqa: BLE001 - property names move between engine versions
                notes.append(f"{prop}: {e}")
        zcls = getattr(unreal, "WaterZone", None)
        zones = [z for z in eas.get_all_level_actors() if zcls and isinstance(z, zcls)]
        if zcls and not zones:
            z = eas.spawn_actor_from_class(zcls, unreal.Vector(0, 0, spec["sea_z_cm"]))
            if z is not None:
                ap_import.set_tags(z, ["ap:dress=ocean_zone", "ap:source=pipeline-dress"])
                z.set_folder_path("AP/Dressing")
                zones = [z]
        for z in zones:
            try:
                z.set_editor_property("zone_extent", unreal.Vector2D(size, size))
            except Exception as e:  # noqa: BLE001
                notes.append(f"zone_extent: {e}")
        if notes:
            report["warnings"].append("ocean: " + "; ".join(notes))
        return a, None
    except Exception as e:  # noqa: BLE001 - anything the plugin refuses headless
        return None, f"{type(e).__name__}: {e}"


# --- meshes and instances -------------------------------------------------------------------------

def import_meshes(role, path):
    """Every static mesh of a CC0 or fallback file, imported once per content (folder per hash)."""
    digest = ap_import.sha(path)[:10]
    stem = os.path.splitext(os.path.basename(path))[0].replace(".", "_").replace("-", "_")
    folder = f"{ROOT}/{role}/{stem}_{digest}"

    def meshes():
        if not eal.does_directory_exist(folder):
            return []
        return sorted((eal.load_asset(p) for p in eal.list_assets(folder, recursive=True)
                       if str(eal.find_asset_data(p).asset_class_path.asset_name) == "StaticMesh"),
                      key=lambda m: m.get_name())
    got = meshes()
    if not got:
        task = unreal.AssetImportTask()
        task.filename, task.destination_path = path, folder
        task.automated, task.replace_existing, task.save = True, True, False
        amp.tools.import_asset_tasks([task])
        got = meshes()
        for m in got:
            ns = m.get_editor_property("nanite_settings")
            ns.enabled = True
            m.set_editor_property("nanite_settings", ns)
            ap_import.keep_normals(m)
        eal.save_directory(folder, only_if_is_dirty=False, recursive=True)
        log(f"imported {role}: {len(got)} mesh(es) from {os.path.basename(path)}")
    return got


def megascans_mesh(path):
    obj = unreal.load_asset(path.split(".")[0]) if path else None
    if obj is None:
        return None
    if isinstance(obj, unreal.StaticMesh):
        return obj
    try:
        m = obj.get_editor_property("mesh")       # a foliage type
        return m if isinstance(m, unreal.StaticMesh) else None
    except Exception:  # noqa: BLE001
        return None


def role_meshes(role, src, report):
    """(meshes, source used) for a mesh role: Megascans when mapped and there, else its fallback."""
    if src.get("source") == "megascans":
        paths = src.get("assets") or [src.get("asset")]
        got = [m for m in (megascans_mesh(p) for p in paths) if m is not None]
        for p in paths:
            if megascans_mesh(p) is None:
                report["warnings"].append(f"{role}: Megascans {p} not found")
        if got:
            return got, "megascans"
        report["warnings"].append(f"{role}: no Megascans asset found: using the "
                                  f"{(src.get('fallback') or {}).get('source', 'no')} asset")
        src = src.get("fallback") or {}
    if src.get("mesh"):
        return import_meshes(role, src["mesh"]), src["source"]
    return [], "none"


def transforms(rows, meshes, fit):
    """Instance rows (dressing._inst) -> per-variant transforms: fit "size" scales the mesh to the
    row's metres (width, height), "native" multiplies its own size; each sits base-down, centred,
    sunk by its fraction of its height."""
    boxes = []
    for m in meshes:
        b = m.get_bounding_box()
        boxes.append((b.min, b.max))
    out = [[] for _ in meshes]
    for x, y, z, yaw, pitch, roll, s0, s1, s2, v, sink in rows:
        k = v % len(meshes)
        lo, hi = boxes[k]
        dx, dy, dz = max(hi.x - lo.x, 1.0), max(hi.y - lo.y, 1.0), max(hi.z - lo.z, 1.0)
        if fit == "size":
            sz = s2 * 100.0 / dz
            sxy = min(max(s0 * 100.0 / max(dx, dy), 0.6 * sz), 1.6 * sz)
            sc = (sxy, sxy, sz)
        else:
            sc = (s0, s1, s2)
        ox, oy = -(lo.x + hi.x) / 2 * sc[0], -(lo.y + hi.y) / 2 * sc[1]
        oz = -lo.z * sc[2] - sink * dz * sc[2]
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        out[k].append(unreal.Transform(location=unreal.Vector(x + ox * c - oy * s, y + ox * s + oy * c, z + oz),
                                       rotation=unreal.Rotator(roll, pitch, yaw), scale=unreal.Vector(*sc)))
    return out


def place_role(eas, role, rows, meshes, fit, key, cull, actor):
    if actor is None:
        actor = eas.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0))
    actor.set_actor_label(f"AP_Dress_{role}")
    ap_import.set_tags(actor, [f"ap:dress={role}", f"ap:dress_key={key}", "ap:source=pipeline-dress"])
    actor.set_folder_path("AP/Dressing")
    have = {c.get_name(): c for c in actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent)}
    per = transforms(rows, meshes, fit)
    for i, (mesh, ts) in enumerate(zip(meshes, per)):
        name = f"HISM_{role}_{i}"
        c = have.pop(name, None) or add_hism(actor, name)
        c.set_static_mesh(mesh)
        c.clear_instances()
        if ts:
            c.add_instances(ts, False, False)
        c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        if cull:
            c.set_cull_distances(int(cull[0]), int(cull[1]))
        if role == "grass":
            c.set_cast_shadow(False)
    for c in have.values():          # fewer variants than last time
        c.clear_instances()
        c.set_static_mesh(None)
    return actor


# --- the run --------------------------------------------------------------------------------------

def apply(path, eas, report):
    d = json.load(open(path))
    rep = {"sources": {}, "counts": {}, "placed": False, "hidden": 0, "water": None, "removed": [], "warnings": []}
    report["dressing"] = rep
    actors = {}
    for a in eas.get_all_level_actors():
        k = ap_import.tagged(a).get("ap:dress")
        if k:
            actors[k] = a
    insts = d.get("instances") or {}
    # removed roles (and the ocean, unless it stays)
    keep = set(insts) | ({"ocean", "ocean_zone"} if (d.get("water") or {}).get("mode") == "ocean" else set())
    for k, a in list(actors.items()):
        if k not in keep:
            rep["removed"].append(k)
            eas.destroy_actor(a)
            actors.pop(k)
    # instances
    for role, rows in insts.items():
        src = d["sources"].get(role) or {}
        a = actors.get(role)
        same = a is not None and ap_import.tagged(a).get("ap:dress_key") == d["key"]
        meshes, used = role_meshes(role, src, rep)
        rep["sources"][role] = used
        rep["counts"][role] = len(rows) if meshes else 0
        rep.setdefault("assets", {})[role] = len(meshes)
        if not meshes:
            rep["warnings"].append(f"{role}: no mesh; nothing placed")
            if a is not None:
                eas.destroy_actor(a)
            continue
        if same:
            continue
        place_role(eas, role, rows, meshes, d["fit"].get(role, "native"), d["key"], d["cull_cm"].get(role), a)
        rep["placed"] = True
    # placeholder trees: hide the plan's, show again what is no longer hidden
    slot_actors = {ap_import.tagged(a).get("ap:id"): a for a in eas.get_all_level_actors() if "ap:id" in ap_import.tagged(a)}
    want = {(h["slot"], c) for h in d.get("hide") or [] for c in h["components"]}
    for sid, a in slot_actors.items():
        for name, c in ap_import.ism_components(a).items():
            hide = (sid, name) in want
            if hide or HIDDEN in comp_tags(c):
                c.set_visibility(not hide, False)
                c.set_hidden_in_game(hide)
                set_comp_tag(c, HIDDEN, hide)
    rep["hidden"] = len({s for s, _ in want})
    # materials: ground, sea
    set_on = []
    if d.get("ground"):
        g = {}
        set_on += ground(d["ground"], d["ground"]["noise"], eas, rep, g)
        rep["sources"].update(g)
    w = d.get("water")
    sea_hidden = []
    if w:
        mode = w["mode"]
        if mode == "ocean":
            oc, why = ocean(w, eas, actors, rep)
            if oc is None:
                rep["warnings"].append(f"ocean not made headless ({why}): Simple sea instead")
                mode = "simple (ocean failed)"
            else:
                rep["water"] = "ocean (Water plugin, experimental)"
                for a in eas.get_all_level_actors():         # the ocean replaces the sea plane
                    if ap_import.tagged(a).get("ap:id") in w["slots"]:
                        for c in ap_import.ism_components(a).values():
                            c.set_visibility(False, False)
                            set_comp_tag(c, HIDDEN, True)
                            sea_hidden.append(c.get_path_name())
        if mode != "ocean":
            set_on += simple_sea(w, eas)
            rep["water"] = mode
            for k in ("ocean", "ocean_zone"):
                if k in actors:
                    eas.destroy_actor(actors.pop(k))
                    rep["removed"].append(k)
    # overrides set earlier and not now: back to the mesh's own (only ours; the import's stay)
    ids = {c.get_path_name() for c in set_on}
    for a in eas.get_all_level_actors():
        for c in a.get_components_by_class(unreal.MeshComponent):
            if OVERRIDE in comp_tags(c) and c.get_path_name() not in ids:
                for i in range(c.get_num_materials()):
                    m = c.get_material(i)
                    if m is not None and m.get_path_name().startswith(MATS):
                        c.set_material(i, None)
                set_comp_tag(c, OVERRIDE, False)
    if w is None or w["mode"] != "ocean" or "failed" in str(rep["water"]):
        for a in eas.get_all_level_actors():         # the sea plane back when the ocean goes
            if ap_import.tagged(a).get("ap:type") == "sea":
                for c in ap_import.ism_components(a).values():
                    if HIDDEN in comp_tags(c) and c.get_path_name() not in sea_hidden:
                        c.set_visibility(True, False)
                        set_comp_tag(c, HIDDEN, False)
    log(f"done: {rep['counts']} placed={rep['placed']} water={rep['water']}")
    return rep

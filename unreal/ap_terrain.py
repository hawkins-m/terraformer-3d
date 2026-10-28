"""Terrain delivery assets (editor Python, UE 5.8; run headless by `ap terrain ue`, -nullrhi):

    /Game/AP/Terrain/M_AP_LandscapeAuto   slope- and height-based auto material: grass low and flat,
                                          scrub higher up, rock on steep slopes (world-aligned maps,
                                          no UVs needed; works on a Landscape and on meshes); macro
                                          colour and roughness noise and a second, larger texture
                                          scale blended in with camera distance (as the dressing's
                                          ground and paving), so it doesn't tile from the air
    /Game/AP/Terrain/MI_AP_TerrainAuto    its instance, the values below
    /Game/AP/Terrain/SM_OuterRing         the outer ring (Nanite), the instance assigned
The Landscape itself is a manual import (UE 5.8's Python can't create one from a heightmap):
USAGE.md "Terrain beyond the city".

args.json: {"rock": {"color", "normal", "rough"}, "detail": {"color", "normal", "rough"}, "noise": the
            macro noise map, "version": the material's (terrain_build.TERRAIN_MAT_VERSION),
            "outer_glb": path, "params": {name: value}, "vectors": {name: [r, g, b]}, "report"}
"""
import json
import os
import sys
import traceback

import unreal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ap_import  # noqa: E402
import ap_material_preview as amp  # noqa: E402

eal = unreal.EditorAssetLibrary
mel = unreal.MaterialEditingLibrary
log = ap_import.log
ROOT = "/Game/AP/Terrain"
MAT = f"{ROOT}/M_AP_LandscapeAuto"


def auto_material(rock, detail, noise, version):
    m = eal.load_asset(MAT) if eal.does_asset_exist(MAT) else None
    if m and eal.get_metadata_tag(m, "ap:version") == version:
        return m
    if m:
        mel.delete_all_material_expressions(m)
    else:
        m = amp.tools.create_asset("M_AP_LandscapeAuto", ROOT, unreal.Material, unreal.MaterialFactoryNew())

    def expr(cls, x, y, **props):
        e = mel.create_material_expression(m, cls, x, y)
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    def scalar(name, v, x, y):
        return expr(unreal.MaterialExpressionScalarParameter, x, y, parameter_name=name, default_value=v)

    def vec(name, rgb, x, y):
        return expr(unreal.MaterialExpressionVectorParameter, x, y, parameter_name=name,
                    default_value=unreal.LinearColor(*rgb, 1.0))

    def op(cls, a, b, x, y, pa="A", pb="B"):
        e = expr(cls, x, y)
        mel.connect_material_expressions(a, "", e, pa)
        mel.connect_material_expressions(b, "", e, pb)
        return e

    def tex(name, t, uv, x, y, sampler=None):
        props = {"parameter_name": name, "texture": t}
        if sampler:
            props["sampler_type"] = sampler
        e = expr(unreal.MaterialExpressionTextureSampleParameter2D, x, y, **props)
        mel.connect_material_expressions(uv, "", e, "UVs")
        return e

    def lerp(a, b, alpha, x, y, pa=""):
        e = expr(unreal.MaterialExpressionLinearInterpolate, x, y)
        mel.connect_material_expressions(a, pa, e, "A")
        mel.connect_material_expressions(b, pa, e, "B")
        mel.connect_material_expressions(alpha, "", e, "Alpha")
        return e

    def sat(a, x, y):
        e = expr(unreal.MaterialExpressionSaturate, x, y)
        mel.connect_material_expressions(a, "", e, "")
        return e

    wp = expr(unreal.MaterialExpressionWorldPosition, -2600, 0)
    xy = expr(unreal.MaterialExpressionComponentMask, -2400, 0, r=True, g=True, b=False, a=False)
    mel.connect_material_expressions(wp, "", xy, "")
    zc = expr(unreal.MaterialExpressionComponentMask, -2400, 200, r=False, g=False, b=True, a=False)
    mel.connect_material_expressions(wp, "", zc, "")
    uv_rock = op(unreal.MaterialExpressionDivide, xy, scalar("RockTileCm", 800.0, -2400, -150), -2200, -100)
    uv_det = op(unreal.MaterialExpressionDivide, xy, scalar("DetailTileCm", 300.0, -2400, 120), -2200, 100)
    # height: 0 below ScrubFromM, 1 above ScrubFromM + ScrubBlendM
    zm = op(unreal.MaterialExpressionDivide, zc, scalar("CmPerM", 100.0, -2400, 300), -2200, 260)
    hz = op(unreal.MaterialExpressionSubtract, zm, scalar("ScrubFromM", 180.0, -2200, 380), -2000, 300)
    hf = sat(op(unreal.MaterialExpressionDivide, hz, scalar("ScrubBlendM", 120.0, -2000, 420), -1800, 320), -1600, 320)
    # slope: 0 flat, 1 where the normal tips past RockSlope (its cosine) by SlopeBlend
    nrm = expr(unreal.MaterialExpressionVertexNormalWS, -2400, 600)
    nz = expr(unreal.MaterialExpressionComponentMask, -2200, 600, r=False, g=False, b=True, a=False)
    mel.connect_material_expressions(nrm, "", nz, "")
    sz = op(unreal.MaterialExpressionSubtract, scalar("RockSlopeCos", 0.82, -2200, 700), nz, -2000, 650)
    sf = sat(op(unreal.MaterialExpressionDivide, sz, scalar("SlopeBlend", 0.08, -2000, 760), -1800, 680), -1600, 680)
    # far: a second, larger scale (not a whole multiple) takes over with camera distance
    dist = expr(unreal.MaterialExpressionDistance, -2400, 900)
    mel.connect_material_expressions(expr(unreal.MaterialExpressionCameraPositionWS, -2600, 900), "", dist, "A")
    mel.connect_material_expressions(wp, "", dist, "B")
    cm = expr(unreal.MaterialExpressionConstant, -2600, 1050, r=100.0)
    near = op(unreal.MaterialExpressionMultiply, scalar("NearM", 60.0, -2400, 1000), cm, -2200, 1000)
    far = op(unreal.MaterialExpressionMultiply, scalar("FarM", 250.0, -2400, 1100), cm, -2200, 1100)
    fa = sat(op(unreal.MaterialExpressionDivide, op(unreal.MaterialExpressionSubtract, dist, near, -2000, 950),
                op(unreal.MaterialExpressionSubtract, far, near, -2000, 1100), -1900, 1000), -1800, 1000)
    far_k = scalar("FarScale", 3.7, -2400, 1250)
    uv_rock_far = op(unreal.MaterialExpressionDivide, uv_rock, far_k, -2000, -100)
    uv_det_far = op(unreal.MaterialExpressionDivide, uv_det, far_k, -2000, 120)

    def both(name, t, uvn, uvf, x, y, sampler=None, pa=""):
        return lerp(tex(name, t, uvn, x, y, sampler), tex(name, t, uvf, x, y + 60, sampler), fa, x + 200, y, pa)
    rt = lambda k: amp.import_texture(rock[k], f"{ROOT}/Textures", k)  # noqa: E731
    rc = both("RockColor", rt("color"), uv_rock, uv_rock_far, -1700, -500)
    rn = both("RockNormal", rt("normal"), uv_rock, uv_rock_far, -1700, -250, unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL, "RGB")
    rr = both("RockRough", rt("rough"), uv_rock, uv_rock_far, -1700, 0, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS, "G")
    dc = both("DetailColor", amp.import_texture(detail["color"], f"{ROOT}/Textures", "color"), uv_det, uv_det_far, -1700, 200)
    # macro: low-frequency colour and roughness noise (a 410 m and a 71 m sample)
    nt = amp.import_texture(noise, f"{ROOT}/Textures", "rough")
    ma = expr(unreal.MaterialExpressionComponentMask, -1500, 1300, r=True, g=False, b=False, a=False)
    mel.connect_material_expressions(tex("MacroNoise", nt, op(unreal.MaterialExpressionDivide, xy, op(
        unreal.MaterialExpressionMultiply, scalar("MacroTileM", 410.0, -2400, 1400), cm, -2200, 1400), -2000, 1400),
        -1700, 1400, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS), "", ma, "")
    mb = expr(unreal.MaterialExpressionComponentMask, -1500, 1500, r=False, g=True, b=False, a=False)
    mel.connect_material_expressions(tex("MacroNoise2", nt, op(unreal.MaterialExpressionDivide, xy, op(
        unreal.MaterialExpressionMultiply, scalar("MacroTile2M", 71.0, -2400, 1500), cm, -2200, 1500), -2000, 1500),
        -1700, 1500, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS), "", mb, "")
    mix = op(unreal.MaterialExpressionAdd, op(unreal.MaterialExpressionMultiply, ma, scalar("MacroA", 0.65, -1500, 1600), -1300, 1350),
             op(unreal.MaterialExpressionMultiply, mb, scalar("MacroB", 0.35, -1500, 1650), -1300, 1500), -1100, 1400)
    bright = lerp(scalar("MacroLow", 0.8, -1100, 1550), scalar("MacroHigh", 1.15, -1100, 1600), mix, -900, 1450)
    macro_tint = lerp(vec("MacroTintA", [1.0, 0.97, 0.9], -1100, 1700), vec("MacroTintB", [0.9, 0.95, 0.92], -1100, 1800),
                      mix, -900, 1700)
    rock_col = op(unreal.MaterialExpressionMultiply, rc, vec("RockTint", [0.62, 0.6, 0.55], -1500, -600), -1300, -550)
    det = expr(unreal.MaterialExpressionComponentMask, -1500, 200, r=True, g=False, b=False, a=False)
    mel.connect_material_expressions(dc, "", det, "")
    detail_k = op(unreal.MaterialExpressionMultiply, det, scalar("DetailStrength", 1.6, -1500, 300), -1300, 250)
    grass = op(unreal.MaterialExpressionMultiply, vec("GrassTint", [0.07, 0.11, 0.035], -1300, 100), detail_k, -1100, 150)
    scrub = op(unreal.MaterialExpressionMultiply, vec("ScrubTint", [0.08, 0.085, 0.05], -1300, 400), detail_k, -1100, 400)
    low = lerp(grass, scrub, hf, -900, 250)
    col = lerp(low, rock_col, sf, -700, 0)
    col = op(unreal.MaterialExpressionMultiply, op(unreal.MaterialExpressionMultiply, col, bright, -600, 0), macro_tint, -500, 0)
    mel.connect_material_property(col, "", unreal.MaterialProperty.MP_BASE_COLOR)
    rough = expr(unreal.MaterialExpressionLinearInterpolate, -700, 500)
    mel.connect_material_expressions(scalar("VegRough", 0.92, -900, 600), "", rough, "A")
    mel.connect_material_expressions(rr, "", rough, "B")              # (its G: ambientCG roughness maps are grey)
    mel.connect_material_expressions(sf, "", rough, "Alpha")
    rvar = op(unreal.MaterialExpressionMultiply, op(unreal.MaterialExpressionSubtract, mix, scalar("Half", 0.5, -900, 1900), -700, 1850),
              scalar("RoughVariation", 0.15, -900, 1950), -600, 1900)
    rough = sat(op(unreal.MaterialExpressionAdd, rough, rvar, -500, 500), -400, 500)
    mel.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    flat = expr(unreal.MaterialExpressionConstant3Vector, -1100, 800, constant=unreal.LinearColor(0, 0, 1, 1))
    nl = expr(unreal.MaterialExpressionLinearInterpolate, -700, 800)
    mel.connect_material_expressions(flat, "", nl, "A")
    mel.connect_material_expressions(rn, "", nl, "B")
    mel.connect_material_expressions(sf, "", nl, "Alpha")
    mel.connect_material_property(nl, "", unreal.MaterialProperty.MP_NORMAL)
    mel.recompile_material(m)
    eal.set_metadata_tag(m, "ap:version", version)
    eal.save_loaded_asset(m)
    log(f"built {MAT} v{version}")
    return m


def main(args):
    rep = {"ok": False}
    m = auto_material(args["rock"], args["detail"], args["noise"], args.get("version", "1"))
    mi_path = f"{ROOT}/MI_AP_TerrainAuto"
    mi = eal.load_asset(mi_path) if eal.does_asset_exist(mi_path) else amp.tools.create_asset(
        "MI_AP_TerrainAuto", ROOT, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    mel.set_material_instance_parent(mi, m)
    for k, v in (args.get("params") or {}).items():
        mel.set_material_instance_scalar_parameter_value(mi, k, float(v))
    for k, v in (args.get("vectors") or {}).items():
        mel.set_material_instance_vector_parameter_value(mi, k, unreal.LinearColor(*v, 1.0))
    mel.update_material_instance(mi)
    eal.save_loaded_asset(mi)
    rep["material"], rep["instance"], rep["version"] = MAT, mi_path, args.get("version", "1")
    if args.get("outer_glb"):
        import ap_look_test as alt
        mesh = alt.import_static(args["outer_glb"], ROOT, "OuterRing")
        for i in range(len(mesh.get_editor_property("static_materials"))):
            mesh.set_material(i, mi)
        eal.save_loaded_asset(mesh)
        rep["outer_ring"] = mesh.get_path_name()
    eal.save_directory(ROOT, only_if_is_dirty=False, recursive=True)
    rep["ok"] = True
    return rep


if __name__ == "__main__":
    args = json.load(open(sys.argv[-1]))
    try:
        rep = main(args)
    except Exception as e:
        rep = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
        unreal.log_error(rep["traceback"])
    with open(args["report"], "w") as f:
        json.dump(rep, f, indent=1)
    log(f"terrain assets done ok={rep['ok']}")

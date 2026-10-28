"""World dressing (asset_pipeline/dressing.py): options remembered per project, deterministic
placement that keeps out of buildings, streets, plazas and water, the placeholder trees it replaces,
the cache key, the Megascans scan (a mocked Content folder, a Windows project through WSL) and the
report. Unreal itself is not run here (unreal/ap_dressing.py: the scratch run, docs/notes/unreal.md)."""
import numpy as np
import pytest

from asset_pipeline import dressing, frame_plan, ue, wsl
from asset_pipeline.ground import Ground
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import DressingOptions

K = {c: frame_plan.CLASSES.index(c) for c in frame_plan.CLASSES}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("AP_UE_PROJECTS", str(tmp_path / "unreal"))
    return ProjectStore.create("dress-demo")


def grid(slots=()):
    """400 x 400 m at 2 m: open ground; a building block with a courtyard in the middle; a street
    across; a lawn; sea along the south; a slope rising to the north-east."""
    n = 200
    cls = np.full((n, n), K["other"], np.uint8)
    cls[80:120, 80:120] = K["building"]
    cls[90:110, 90:110] = K["other"]                      # a courtyard: open, but ringed by the building
    cls[60:64, :] = K["paving"]                           # a street (rows are north -> south)
    cls[20:50, 20:60] = K["grass"]
    cls[170:, :] = K["water"]
    h = np.add.outer(np.linspace(30, 0, 64), np.linspace(0, 30, 64))   # row 0 = north
    return dressing.Grid({"cls": cls, "bounds": (-200.0, -200.0, 200.0, 200.0), "cell": 2.0}, Ground(h, 400.0), slots)


def where(g, rows):
    xs, ys = np.array([r[0] for r in rows]) / 100, -np.array([r[1] for r in rows]) / 100
    i, j = g.ij(xs, ys)
    return g.cls[i, j], xs, ys


# --- options --------------------------------------------------------------------------------------

def test_options_are_remembered_per_project_and_flags_override_them(store):
    assert not dressing.options(store).any()                       # a new project: nothing
    dressing.save(store, DressingOptions(rocks=True, rock_density=2.0, water="simple"))
    o = dressing.options(store)
    assert o.rocks and o.rock_density == 2.0 and o.water == "simple" and not o.ground
    m = dressing.merged(store, ground=True, rocks=None, seed=7)   # unset flags keep the last-used choice
    assert m.ground and m.rocks and m.seed == 7 and m.water == "simple"
    dressing.save(store, library={"rock_small": "/Game/Fab/R/SM_R.SM_R", "nope": "x", "grass": ""}, folders=[" /Game/Mine ", ""])
    u = dressing._unreal(store)
    assert u.dressing_library == {"rock_small": "/Game/Fab/R/SM_R.SM_R", "grass": ""} and u.dressing_folders == ["/Game/Mine"]
    assert dressing.options(store).rocks                           # saving the library kept the options
    assert dressing.roles_for(DressingOptions(ground=True)) == ["ground_grass", "ground_dirt", "ground_rock", "ground_sand",
                                                                "paving_slabs", "paving_cobbles", "paving_flagstones"]


def test_ways_and_plazas_get_paving_by_kind_and_keep_their_pools():
    slot = lambda sid, typ, *assets: {"id": sid, "type": typ, "components": [{"asset": a} for a in assets]}  # noqa: E731
    man = {"slots": [slot("way-a", "avenue", "way-a-avenue"), slot("st-1", "street", "street-1"), slot("ln-1", "lane", "way-l-lane"),
                     slot("sx", "stair", "way-s-stair", "stair-tread"), slot("pl", "plaza", "paving-1", "pool-water-2"),
                     slot("r0", "radial", "radial-x"), slot("park", "park", "park-lawn", "tree-pine"), slot("b", "insula", "box")]}
    pv = dressing.paving(man, {"paving_slabs": {"source": "cc0", "color": "c.jpg"}})
    assert set(pv) == {"Slabs", "Cobbles", "Flagstones"}
    assert [r["slot"] for r in pv["Slabs"]["slots"]] == ["way-a", "st-1"] and pv["Slabs"]["src"]["color"] == "c.jpg"
    assert [r["slot"] for r in pv["Cobbles"]["slots"]] == ["ln-1", "sx"] and pv["Cobbles"]["tile_m"] == [1.15, 1.15]
    assert {r["slot"]: r["components"] for r in pv["Flagstones"]["slots"]} == {"pl": ["ISM_paving-1"], "r0": ["ISM_radial-x"]}
    assert pv["Flagstones"]["src"] is None and pv["Slabs"]["tile_m"] == [1.9, 3.8]


def test_density_is_bounded():
    with pytest.raises(ValueError):
        DressingOptions(rock_density=10)


# --- placement ------------------------------------------------------------------------------------

def test_rocks_are_deterministic_per_seed_and_stay_on_open_ground():
    slots = [{"category": "building", "type": "insula", "bbox": [[-30, 130, 0], [-10, 150, 0]]}]
    g = grid(slots)
    a = dressing.place_rocks(g, 3.0, np.random.default_rng([1, 1]))
    b = dressing.place_rocks(g, 3.0, np.random.default_rng([1, 1]))
    c = dressing.place_rocks(g, 3.0, np.random.default_rng([2, 1]))
    assert a == b and a != c
    rows = a["rock_small"] + a["rock_large"]
    assert len(rows) > 20
    cls, xs, ys = where(g, rows)
    assert set(cls.tolist()) == {K["other"]}                      # never buildings, streets, lawns or water
    inner = (np.abs(xs) < 20) & (np.abs(ys) < 20)
    assert not inner.any()                                         # nor the courtyard the building rings
    assert not ((xs > -30) & (xs < -10) & (ys > 130) & (ys < 150)).any()   # nor a building slot's bounds
    r = rows[0]
    assert 0.1 <= r[10] <= 0.35 and -10 <= r[4] <= 10              # sunk a little, tilted a little


def test_more_rocks_on_slopes_and_with_density():
    g = grid()
    few = dressing.place_rocks(g, 0.5, np.random.default_rng(3))
    many = dressing.place_rocks(g, 3.0, np.random.default_rng(3))
    assert sum(map(len, many.values())) > 2 * sum(map(len, few.values()))


def test_placeholder_trees_and_the_components_to_hide():
    index = {"tree:cypress#a": {"id": "cone-a", "bounds": [[-0.5, -0.5, 0], [0.5, 0.5, 1]]},
             "dome#b": {"id": "dome-b", "bounds": [[-0.5, -0.5, 0], [0.5, 0.5, 0.5]]},
             "trunk#c": {"id": "cone-a", "bounds": [[-0.5, -0.5, 0], [0.5, 0.5, 1]]},
             "lawn#d": {"id": "lawn-d", "bounds": [[0, 0, 0], [1, 1, 0.1]]}}

    def m(x, y, z, sx, sz):
        return [[sx, 0, 0, x], [0, sx, 0, y], [0, 0, sz, z], [0, 0, 0, 1]]
    gb = {"slots": [
        {"id": "park-1", "matrix": m(100, 0, 5, 1, 1), "pieces": [
            {"piece": "tree:cypress", "mesh": "tree:cypress#a", "matrix_local": m(0, 0, 0, 2, 12)},
            {"piece": "tree:pine-trunk", "mesh": "trunk#c", "matrix_local": m(10, 0, 0, 1.4, 7)},
            {"piece": "tree:pine-crown", "mesh": "dome#b", "matrix_local": m(10, 0, 6, 9, 4)},
            {"piece": "lawn", "mesh": "lawn#d", "matrix_local": m(0, 0, 0, 1, 1)}]},
        {"id": "thermae", "matrix": m(0, 0, 0, 1, 1), "pieces": [
            {"piece": "thermae:dome", "mesh": "dome#b", "matrix_local": m(0, 0, 0, 20, 10)}]}]}
    trees, hide = dressing.placeholder_trees(gb, index)
    by = {t["species"]: t for t in trees}
    assert set(by) == {"cypress", "pine"}
    assert by["cypress"]["h"] == pytest.approx(12) and by["cypress"]["w"] == pytest.approx(2)
    assert by["pine"]["x"] == pytest.approx(110) and by["pine"]["h"] == pytest.approx(8) and by["pine"]["w"] == pytest.approx(9)
    assert hide == [{"slot": "park-1", "components": ["ISM_cone-a", "ISM_dome-b"]}]   # the thermae's dome stays


def test_vegetation_keeps_plan_species_and_green_zones():
    g = grid()
    trees = [{"x": 0.0, "y": 150.0, "z": 3.0, "h": 11.0, "w": 2.0, "species": "cypress"},
             {"x": 10.0, "y": 150.0, "z": 3.0, "h": 10.0, "w": 8.0, "species": "pine"}]
    street = [{"kind": "street", "x": 50.0, "y": 72.0, "crown_m": 7.0}]
    out = dressing.place_vegetation(g, 1.0, np.random.default_rng(4), trees, street, [])
    assert out == dressing.place_vegetation(g, 1.0, np.random.default_rng(4), trees, street, [])
    assert out["tree_cypress"][0][8] == 11.0 and out["tree_pine"][0][6] == 8.0   # the plan's size
    assert len(out["tree_pine"]) + len(out["tree_cypress"]) == 3
    for role in ("grass", "shrub"):
        cls, _, _ = where(g, out[role])
        assert out[role] and set(cls.tolist()) <= {K["grass"], K["tree"]}       # green zones only


def test_the_key_moves_only_with_what_placement_depends_on(store, monkeypatch):
    from asset_pipeline.stages import s7_export, sw_site
    gb = {"slots": [], "blend_sha256": "g1"}
    monkeypatch.setattr(s7_export, "load_manifest", lambda st: {"slots": [], "map": "/Game/M"})
    monkeypatch.setattr(sw_site, "load_greybox", lambda st: gb)
    monkeypatch.setattr(dressing, "sources", lambda roles, lib, mega, log=print: {r: {"source": "fallback", "mesh": r} for r in roles})
    monkeypatch.setattr(dressing, "class_map", lambda st, g, log=print: {"cls": grid().cls, "bounds": (-200, -200, 200, 200), "cell": 2.0})
    monkeypatch.setattr(Ground, "from_project", classmethod(lambda cls, st: Ground(None)))
    log = []

    def key(**o):
        return dressing.prepare(store, DressingOptions(rocks=True, **o), log=log.append)["key"]
    k = key()
    assert key(ground=True) == k and key(water="off") == k and "dressing: placement unchanged" in log
    assert key(seed=2) != k and key(rock_density=2.0) != k
    gb["blend_sha256"] = "g2"                                       # the plan changed
    assert key() != k
    rec = dressing.prepare(store, DressingOptions(), log=log.append)  # everything off: nothing to place
    assert rec["instances"] == {} and rec["ground"] is None and rec["water"] is None


# --- Megascans ------------------------------------------------------------------------------------

def fake_content(root):
    """A Content folder the way Fab and Bridge leave Megascans (names and class bytes)."""
    files = {"Fab/Megascans/3D/Mossy_Boulder_abc/High/SM_Mossy_Boulder_abc.uasset": b"..StaticMeshDescription..",
             "Fab/Megascans/3D/Small_Rock_xyz/SM_Small_Rock_xyz.uasset": b"..StaticMeshDescription..",
             "Fab/Megascans/3D/Mossy_Boulder_abc/High/MI_Mossy_Boulder_abc.uasset": b"..MaterialInstanceConstant..",
             "Megascans/3D_Plants/Italian_Cypress_q1/S_Italian_Cypress_q1_lod0.uasset": b"..StaticMeshDescription..",
             "Megascans/3D_Plants/Stone_Pine_q2/FT_Stone_Pine_q2.uasset": b"..FoliageType_InstancedStaticMesh..",
             "Megascans/3D_Plants/Olea_Europaea_q3/S_Olea_Europaea_q3.uasset": b"..StaticMeshDescription..",
             "Megascans/3D_Plants/Wild_Grass_q4/S_Wild_Grass_q4.uasset": b"..StaticMeshDescription..",
             "Quixel/Surfaces/Beach_Sand_s1/MI_Beach_Sand_s1_2K.uasset": b"..MaterialInstanceConstant..",
             "Quixel/Surfaces/Forest_Ground_s2/MI_Forest_Ground_s2_2K.uasset": b"..MaterialInstanceConstant..",
             "Quixel/Surfaces/Rock_Cliff_s3/MI_Rock_Cliff_s3_2K.uasset": b"..MaterialInstanceConstant..",
             "Quixel/Surfaces/Rock_Cliff_s3/T_Rock_Cliff_s3_Albedo.uasset": b"..Texture2D..",
             "MyPacks/Bush_Laurel/SM_Bush_Laurel.uasset": b"..StaticMeshDescription..",
             "Other/SM_Pine_Elsewhere.uasset": b"..StaticMeshDescription.."}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_scan_proposes_roles_from_names_and_folders(store, tmp_path):
    content = ue.project_dir(store) / "Content"
    fake_content(content)
    s = dressing.scan(store)
    p = s["proposal"]
    assert p["rock_large"] == "/Game/Fab/Megascans/3D/Mossy_Boulder_abc/High/SM_Mossy_Boulder_abc.SM_Mossy_Boulder_abc"
    assert p["rock_small"].endswith("SM_Small_Rock_xyz.SM_Small_Rock_xyz")
    assert p["tree_cypress"].endswith("S_Italian_Cypress_q1_lod0") and p["tree_olive"].endswith("S_Olea_Europaea_q3")
    assert p["tree_pine"].endswith("FT_Stone_Pine_q2.FT_Stone_Pine_q2")              # a foliage type
    assert p["grass"].endswith("S_Wild_Grass_q4")
    assert p["ground_sand"].endswith("MI_Beach_Sand_s1_2K") and p["ground_dirt"].endswith("MI_Forest_Ground_s2_2K")
    assert p["ground_rock"].endswith("MI_Rock_Cliff_s3_2K")
    assert "shrub" not in p                                       # MyPacks isn't scanned yet; Other never
    assert not any("Elsewhere" in c["path"] for cs in s["candidates"].values() for c in cs)
    assert not any("MI_Mossy_Boulder" in c["path"] for cs in s["candidates"].values() for c in cs)  # a mesh's own material
    s = dressing.scan(store, folders=["/Game/MyPacks"])
    assert s["proposal"]["shrub"] == "/Game/MyPacks/Bush_Laurel/SM_Bush_Laurel.SM_Bush_Laurel"


def test_rescan_keeps_the_users_mapping_and_fills_gone_or_empty_roles(store):
    content = ue.project_dir(store) / "Content"
    fake_content(content)
    mine = "/Game/Megascans/3D_Plants/Wild_Grass_q4/S_Wild_Grass_q4.S_Wild_Grass_q4"
    dressing.save(store, library={"rock_small": mine, "rock_large": "/Game/Fab/Gone/SM_Gone.SM_Gone", "tree_olive": ""})
    lib = dressing.rescan(store, log=lambda *a: None)
    assert lib["rock_small"] == mine                              # the user's choice, still there: kept
    assert lib["tree_olive"] == ""                                # set to none (CC0) by the user: kept
    assert lib["rock_large"].endswith("SM_Mossy_Boulder_abc")     # gone: the scan's proposal
    assert lib["tree_cypress"].endswith("S_Italian_Cypress_q1_lod0") and dressing._unreal(store).dressing_library == lib


def test_a_windows_project_is_scanned_through_its_wsl_path(store, tmp_path, monkeypatch):
    win_root = tmp_path / "mnt" / "c" / "Users" / "me" / "Unreal" / "dress-demo"
    fake_content(win_root / "Content")
    monkeypatch.setattr(ue, "project_dir", lambda st: "C:\\Users\\me\\Unreal\\dress-demo")
    monkeypatch.setattr(wsl, "to_linux", lambda s: str(win_root) if s.startswith("C:") else s)
    s = dressing.scan(store)
    assert s["content"] == str(win_root / "Content") and s["proposal"]["rock_large"].endswith("SM_Mossy_Boulder_abc")


def test_plugin_status_says_how_to_add_megascans(tmp_path, monkeypatch):
    eng = tmp_path / "UE" / "Engine"
    cmd = eng / "Binaries" / "Linux" / "UnrealEditor-Cmd"
    cmd.parent.mkdir(parents=True)
    cmd.write_text("")
    monkeypatch.setattr(ue, "editor_cmd", lambda: cmd)
    st = dressing.plugin_status()
    assert st["found"] is False and "Migrate" in st["help"]
    (eng / "Plugins" / "Marketplace" / "Fab").mkdir(parents=True)
    (eng / "Plugins" / "Marketplace" / "Fab" / "Fab.uplugin").write_text("{}")
    st = dressing.plugin_status()
    assert st["found"] and st["plugins"] == ["Fab"] and "Add to Project" in st["help"]


# --- report ---------------------------------------------------------------------------------------

def test_report_names_options_counts_and_sources():
    rec = {"options": DressingOptions(rocks=True, vegetation=True, water="ocean").model_dump(), "roles": ["rock_small", "tree_pine"],
           "sources": {"rock_small": {"source": "cc0"}, "tree_pine": {"source": "megascans"}}, "counts": {"rock_small": 9}, "warnings": []}
    ue_rep = {"dressing": {"sources": {"rock_small": "cc0", "tree_pine": "fallback"}, "counts": {"rock_small": 9, "tree_pine": 1200},
                           "placed": True, "hidden": 3, "water": "simple (ocean failed)",
                           "warnings": ["ocean not made headless (x): Simple sea instead"]}}
    s = dressing.summary(rec, ue_rep)
    assert s["ran"] == ["rocks", "vegetation", "water (ocean)"] and s["sources"]["tree_pine"] == "fallback"
    text = "\n".join(dressing.report_lines(s))
    assert "rock_small 9, tree_pine 1,200" in text and "cc0: rock_small" in text and "fallback: tree_pine" in text
    assert "ocean failed" in text and "Simple sea instead" in text
    off = dressing.summary({"options": DressingOptions().model_dump(), "roles": [], "sources": {}}, {"dressing": {"removed": ["rock_small"]}})
    assert dressing.report_lines(off) == ["dressing: off (removed rock_small)"]


def test_every_role_has_a_cc0_asset_or_a_blender_fallback():
    import re
    script = dressing.FALLBACK_SCRIPT.read_text()
    makers = set(re.findall(r'"(\w+)": (?:lambda|\w+)', script.split("MAKERS =")[1].split("}")[0]))
    cc0 = dressing.cc0_models()
    for role, (_, kind, _) in dressing.ROLES.items():
        assert role in cc0 or (kind == "mesh" and role in makers), role
    for m in cc0.values():
        assert m.license == "CC0-1.0" and m.root == "dressing"


def test_library_lists_and_copy_reference(store):
    assert dressing.game_path("StaticMesh'/Game/Fab/R/SM_R.SM_R'") == "/Game/Fab/R/SM_R.SM_R"
    assert dressing.assets_of(["/Game/A.A", " /Game/A.A", "StaticMesh'/Game/B.B'", ""]) == ["/Game/A.A", "/Game/B.B"]
    assert dressing.assets_of("") == [] and dressing.assets_of("/Game/A.A") == ["/Game/A.A"]
    assert dressing._clean_entry("rock_small", ["/Game/A.A"]) == "/Game/A.A"
    assert dressing._clean_entry("rock_small", ["/Game/A.A", "/Game/B.B"]) == ["/Game/A.A", "/Game/B.B"]
    assert dressing._clean_entry("ground_dirt", ["/Game/A.A", "/Game/B.B"]) == "/Game/A.A"
    assert dressing._clean_entry("grass", []) == ""
    dressing.save(store, library={"rock_small": ["/Game/A.A", "/Game/B.B"], "grass": ["/Game/G.G"]})
    assert dressing._unreal(store).dressing_library == {"rock_small": ["/Game/A.A", "/Game/B.B"], "grass": "/Game/G.G"}
    src = dressing.sources([], {"rock_small": ["/Game/A.A", "/Game/B.B"]}, True)
    assert src == {}
    rec = {"options": {"rocks": True, "water": "off"}, "roles": ["rock_small"],
           "sources": {"rock_small": {"source": "megascans", "asset": "/Game/A.A", "assets": ["/Game/A.A", "/Game/B.B"]}}}
    d = dressing.summary(rec, {"dressing": {"sources": {"rock_small": "megascans"}, "assets": {"rock_small": 2}}})
    assert d["assets"] == {"rock_small": 2} and "  assets used: rock_small 2" in dressing.report_lines(d)


def test_a_scan_of_the_whole_content_never_takes_the_pipelines_own_assets(store):
    content = ue.project_dir(store) / "Content"
    fake_content(content)
    own = content / "AP/Materials/Instances/MI_mat_paving.uasset"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_bytes(b"..MaterialInstanceConstant..")
    s = dressing.scan(store, folders=[str(content)])
    assert not any(c["path"].startswith("/Game/AP/") for cs in s["candidates"].values() for c in cs)
    dressing.save(store, library={"paving_slabs": "/Game/AP/Materials/Instances/MI_mat_paving.MI_mat_paving"})
    assert "paving_slabs" not in dressing.rescan(store, log=lambda *a: None)    # an earlier scan's pick of it: dropped

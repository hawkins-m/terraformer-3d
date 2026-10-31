"""City sizes (city_size.py), the background region (city_background.py) and what large cities cost
(greybox collection instancing, the viewer's chunks, the Unreal manifest, detail by distance): a
standard city plans, saves and exports byte for byte as before; large and metro grow the city and its
terrain and come back to standard exactly; the background never moves the city."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from asset_pipeline import city, city_background, city_library, city_size, city_view, greybox, layout_presets
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.schema import CitySpec, SiteLayout
from asset_pipeline.stages import s7_export, sw_site
from test_city import heights, small_city
from test_classic_metro import on_terrain, shaped, spec_digest
from test_layout_presets import digest

# classic (the coastal metro) before city sizes (commit 9210e25); every other preset is pinned in test_classic_metro
CLASSIC = {
    "coastal-plain|3": "52ef23423b93043a",
    "-|7": "c0c6343bb467eb8c",
    "island|2": "67e2d2947fa90ec8",
    "flat-plain|4": "89a117ea51b4f88c",
}
CLASSIC_SPEC = "a974ab2e47277bb8"


@pytest.mark.parametrize("key", list(CLASSIC))
@pytest.mark.golden
def test_standard_classic_plans_exactly_as_before_sizes(key):
    terrain, seed = key.split("|")
    A, h = shaped(on_terrain(None if terrain == "-" else terrain, int(seed)), "classic", int(seed))
    assert A.city.size == "standard" and digest(city.plan(A, h)) == CLASSIC[key]


@pytest.mark.golden
def test_standard_classic_greybox_slots_exactly_as_before_sizes():
    L = small_city()
    assert spec_digest(layout_presets.apply(L, "classic"), heights(L)) == CLASSIC_SPEC


def test_new_fields_stay_out_of_a_standard_layout_and_its_spec():
    d = small_city().model_dump(mode="json")
    assert not {"size", "sized_from", "background", "detail_radius_m"} & set(d["city"])
    spec = greybox.build_spec(small_city(trees=False), heights(small_city()))
    assert "instancing" not in spec and "background" not in spec
    assert city_size.factor(small_city().city) == 1.0 and city_size.detail_radius(small_city().city) is None
    c = CitySpec(size="metro", background=True, detail_radius_m=900)
    assert {"size": "metro", "background": True, "detail_radius_m": 900} == {k: c.model_dump(mode="json")[k] for k in ("size", "background", "detail_radius_m")}


def _coastal():
    return on_terrain("coastal-plain", 3)


@pytest.mark.parametrize("size,across", [("large", 8000), ("metro", 14000)])
def test_large_and_metro_scale_the_city_and_grow_its_terrain(size, across):
    L = _coastal()
    S = city_size.sized(L, size)
    c, t = S.city, S.terrain
    assert c.size == size and city_size.across(c.bounds) == pytest.approx(across, abs=2)
    k = city_size.factor(c)
    assert k == pytest.approx(across / city_size.across(L.city.bounds), rel=1e-3)
    assert t.extent_m / 2 >= max(abs(v) for v in c.bounds) + city_size.MARGIN_M
    assert t.extent_m / (t.resolution - 1) <= city_size.CELL_MAX_M or t.resolution == city_size.RESOLUTION[-1]
    assert t.shore_m == pytest.approx(L.terrain.shore_m * k, abs=0.1)
    assert t.settings.city_r_m == pytest.approx(L.terrain.settings.city_r_m * k, abs=0.1)
    h = greybox.make_terrain(t)
    assert t.z_range[0] <= float(h.min()) and float(h.max()) <= t.z_range[1]


def test_back_to_standard_restores_the_city_exactly():
    L = _coastal()
    for size in ("large", "metro"):
        M = city_size.sized(L, size)
        assert city_size.sized(M, "large").city.bounds == city_size.sized(L, "large").city.bounds   # from the standard, not compounded
        assert city_size.sized(M, "standard").model_dump(mode="json") == L.model_dump(mode="json")


def test_a_heightmap_terrain_cant_grow():
    L = _coastal()
    L.terrain.heightmap = "site/terrain_in.png"
    with pytest.raises(ValueError, match="heightmap"):
        city_size.sized(L, "large")
    assert city_size.sized(L, "standard") is L
    with pytest.raises(ValueError, match="unknown city size"):
        city_size.sized(L, "huge")


def _small_large(name="classic"):
    """A small classic city made 1.6 x bigger, on a coarse grid so it plans fast."""
    L = small_city()
    L.city.sized_from = {"bounds": list(L.city.bounds), "terrain": L.terrain.model_dump(mode="json"),
                         "nodes": [n.model_dump(mode="json") for n in L.city.nodes]}
    L.city.size = "large"
    L.city.bounds = tuple(round(v * 1.6) for v in L.city.bounds)
    L.terrain = L.terrain.model_copy(update={"extent_m": 4200, "resolution": 257})
    return layout_presets.apply(SiteLayout.model_validate(L.model_dump(mode="json")), name)


def test_a_larger_classic_grows_its_downtown_parks_and_highways_and_scales_once():
    from asset_pipeline import city_metro
    std = layout_presets.apply(small_city(), "classic")
    big = _small_large()
    a, b = city.with_preset(std), city.with_preset(big)
    assert b.city.preset.highway_spokes == a.city.preset.highway_spokes + 1
    assert city_metro.downtown_r(b.city) > 1.5 * city_metro.downtown_r(a.city)
    again = city.with_preset(b)                       # the plan's own layout isn't scaled a second time
    assert again.city.preset.highway_spokes == b.city.preset.highway_spokes
    assert again.city.preset.downtown_m == b.city.preset.downtown_m
    P0, P1 = city.plan(std, heights(std)), city.plan(big, heights(big))
    assert len(P1.buildings) > 1.6 * len(P0.buildings)


def test_other_presets_scale_their_footprint():
    base = layout_presets.apply(small_city(), "market-town")
    big = _small_large("market-town")
    assert city.with_preset(big).city.preset.outline_m == pytest.approx(base.city.preset.outline_m * 1.6, abs=0.2)


# --- the background region ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def backed():
    L = layout_presets.apply(small_city(), "classic")
    L.city.background = True
    L = SiteLayout.model_validate(L.model_dump(mode="json"))
    h = heights(L)
    P = city.plan(L, h)
    return L, h, P, city_background.plan(P.layout, P, h)


def test_the_background_never_moves_the_city(backed):
    L, h, P, _ = backed
    off = L.model_copy(deep=True)
    off.city.background = False
    assert digest(city.plan(off, h)) == digest(P)


def test_the_background_is_round_the_city_and_cheap(backed):
    L, h, P, B = backed
    c = B.counts()
    assert B.half >= city_background.REACH * city_size.across(L.city.bounds) / 2 - 1
    assert c["forest_clumps"] > 500 and c["fields"] > 20 and c["ways"] >= 1
    x0, y0, x1, y1 = L.city.bounds
    clear = city_background.CITY_CLEAR_M - 1
    inside = [t for t in B.forest if x0 - clear < t[0] < x1 + clear and y0 - clear < t[1] < y1 + clear]
    assert not inside                                            # nothing in the city
    z = B.z(np.array([t[0] for t in B.forest]), np.array([t[1] for t in B.forest]))
    assert float(z.min()) > L.sea_level                          # on land
    # the far terrain meets the tile at its edge, and climbs to hills away from it
    core = L.terrain.extent_m / 2
    hs = greybox.Heights(h, L.terrain.extent_m)
    edge = np.linspace(-core, core, 9)
    assert np.allclose(B.z(edge, np.full(9, core)), hs.many(edge, np.full(9, core)), atol=1.0)
    again = city_background.plan(P.layout, P, h)
    assert again.forest == B.forest and again.counts() == c     # its own stream: reproducible


def test_background_slots_are_instanced_pieces_and_the_viewer_gets_them(backed, tmp_path):
    L, h, P, B = backed
    keep: dict = {}
    spec = greybox.build_spec(L, h, keep=keep)
    assert keep["background"].counts() == B.counts() and spec["background"] == B.counts()
    forest = [s for s in spec["slots"] if s["type"] == "forest"]
    assert forest and {p["piece"] for s in forest for p in s["pieces"]} == {"forest:clump"}
    assert len({json.dumps(p["prim"], sort_keys=True) for s in forest for p in s["pieces"]}) == 1   # one mesh
    city_background.save_far(B, tmp_path / "far.npz")
    z = np.load(tmp_path / "far.npz")
    assert float(z["x0"]) == -B.half and z["h"].shape[0] == z["h"].shape[1]
    core = L.terrain.extent_m / 2
    assert (core - (-B.half)) / float(z["cell"]) == pytest.approx(round((core + B.half) / float(z["cell"])))  # lines on the tile's edge
    v = city_view.background(B, greybox.Heights(h, L.terrain.extent_m), L.terrain.extent_m)
    assert len(v["forest"]) == 5 * len(B.forest) and len(v["fields"]) == len(B.fields)
    assert v["terrain"]["n"] <= city_view.BG_TERRAIN_N and len(v["terrain"]["z"]) == v["terrain"]["n"] ** 2
    json.dumps(v)
    im = city_background.plan_image(B, P.layout, P, px=400)
    assert im.size == (400, 400)


# --- the costs of a large city -----------------------------------------------------------------

def test_large_specs_ask_for_instancing_and_the_manifest_for_hism_world_partition_and_its_own_level():
    big = _small_large()
    assert greybox.build_spec(big, heights(big)).get("instancing") is True
    gb = {"slots": [], "shots": []}
    std = s7_export.manifest(gb, {}, small_city(), "my-project")
    assert std["map"] == "/Game/AP/Maps/MyProject" and not {"instancing", "world_partition", "size"} & set(std)
    m = s7_export.manifest(gb, {}, big, "my-project")
    assert m["map"] == "/Game/AP/Maps/MyProjectLarge" and m["instancing"] == "hism" and m["world_partition"] and m["size"] == "large"


def test_library_builds_stand_only_near_the_shots_and_downtown(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    big = _small_large()
    big.city.detail_radius_m = 500
    sw_site.save_layout(store, big)
    cx, cy = city.main_centre(big.city)
    box = lambda x, y: [[x - 10, y - 10, 0], [x + 10, y + 10, 20]]  # noqa: E731
    cam = np.eye(4)
    cam[:3, 3] = [cx + 1500, cy, 50]
    gb = {"shots": [{"id": "s", "matrix": cam.tolist()}],
          "slots": [{"id": "core", "bbox": box(cx + 100, cy)}, {"id": "by-shot", "bbox": box(cx + 1900, cy)},
                    {"id": "edge", "bbox": box(cx + 400, cy + 300)}, {"id": "far", "bbox": box(cx - 1200, cy + 900)}]}
    far, radius = city_library.far_slots(store, gb)
    assert radius == 500 and far == {"far"}
    big.city.detail_radius_m = None
    sw_site.save_layout(store, big)
    assert city_library.far_slots(store, gb)[1] == city_size.default_detail_radius()
    sw_site.save_layout(store, small_city())
    assert city_library.far_slots(store, gb) == (set(), None)            # standard: everywhere


def test_the_viewer_scene_marks_its_size(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    big = _small_large()
    sw_site.save_layout(store, big)
    h = heights(big)
    d = city_view.scene(store, city.plan(big, h), h)
    assert d["size"] == "large" and "background" not in d
    sw_site.save_layout(store, small_city())
    d = city_view.scene(store, city.plan(small_city(), heights(small_city())), heights(small_city()))
    assert "size" not in d


def test_the_cli_takes_sizes(tmp_path, monkeypatch, capsys):
    from asset_pipeline import city_roll, cli, ue
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    ProjectStore.create("p")
    seen = {}
    st = {"buildings": 1, "warnings": []}
    monkeypatch.setattr(city_size, "set_size", lambda store, size, **kw: seen.update(size=size, **kw) or st)
    cli.main(["site", "size", "p", "metro", "--background", "--detail-radius", "1800"])
    assert seen == {"size": "metro", "background": True, "detail_radius_m": 1800.0}
    monkeypatch.setattr(city_roll, "roll", lambda store, seed, keep, **kw: seen.update(roll=kw["size"]) or
                        {"seed": 1, "keep": [], "stats": st, "tries": 1})
    cli.main(["site", "roll", "p", "--size", "large"])
    assert seen["roll"] == "large"
    monkeypatch.setattr(ue, "frame_rate", lambda store, adapter: seen.update(gpu=adapter) or {"adapter": adapter, "fps_mean": 60})
    monkeypatch.setattr(ue, "city_map", lambda store: "/Game/AP/Maps/P.P")
    cli.main(["ue", "fps", "p", "--gpu", "1"])
    assert seen["gpu"] == 1 and "60 fps" in capsys.readouterr().out
    cli.main(["site", "size", "--list"])
    assert "Metro, about 14 km across" in capsys.readouterr().out


# --- Blender: collection instances ---------------------------------------------------------------

REAL_BLENDER = Path("/snap/bin/blender")


@pytest.mark.blender
@pytest.mark.skipif(not REAL_BLENDER.exists(), reason="needs Blender")
def test_an_instancing_greybox_reads_back_every_piece(tmp_path, monkeypatch):
    """A large city's greybox: repeated pieces as collection instances on points, one object per slot
    and mesh; extract gives back every piece with its pose, and the export finds each mesh once."""
    from asset_pipeline import blender
    big = _small_large()
    h = heights(big)
    spec = greybox.build_spec(big, h)
    spec["slots"] = [s for s in spec["slots"] if s["type"] in ("houses", "housing", "tower")][:12]
    np.save(tmp_path / "t.npy", h[::8, ::8])
    spec["terrain"].update(npy=str(tmp_path / "t.npy"), resolution=int(h[::8, ::8].shape[0]))
    write_json(tmp_path / "spec.json", spec)
    script = Path(sw_site.SCRIPT)
    blender.run(script, ["--build", tmp_path / "spec.json", "--out", tmp_path / "g.blend"], tmp_path / "b.log", "build")
    blender.run(script, ["--extract", tmp_path / "g.json"], tmp_path / "e.log", "extract", blend=tmp_path / "g.blend")
    log = (tmp_path / "b.log").read_text()
    n = sum(len(s["pieces"]) for s in spec["slots"])
    assert f"{n} pieces" in log and "instancers" in log
    got = {s["id"]: s for s in json.load(open(tmp_path / "g.json"))["slots"]}
    for s in spec["slots"]:
        want = sorted((round(p["loc"][0], 2), round(p["loc"][1], 2)) for p in s["pieces"])
        have = sorted((round(p["matrix_local"][0][3], 2), round(p["matrix_local"][1][3], 2)) for p in got[s["id"]]["pieces"])
        assert have == want, s["id"]
        p0, g0 = s["pieces"][0], None
        for g in got[s["id"]]["pieces"]:
            if abs(g["matrix_local"][0][3] - p0["loc"][0]) < 0.01 and abs(g["matrix_local"][1][3] - p0["loc"][1]) < 0.01:
                g0 = np.array(g["matrix_local"])
        sx = float(np.linalg.norm(g0[:3, 0]))
        assert sx == pytest.approx((p0.get("scale") or [1, 1, 1])[0], abs=0.01)
        yaw = math.degrees(math.atan2(g0[1, 0], g0[0, 0])) % 360
        assert min(abs(yaw - p0["rot_z"] % 360), 360 - abs(yaw - p0["rot_z"] % 360)) < 0.05

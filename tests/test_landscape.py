"""A generated terrain as an Unreal Landscape (landscape.py): whole components, the heights' encoding
and the import values in Unreal's frame. Synthetic heights only."""
import numpy as np
import pytest

from asset_pipeline import landscape as ls


@pytest.mark.parametrize("n,want", [(2017, 2017), (2000, 2017), (513, 505), (100, 127), (3, 127)])
def test_sizes_are_whole_components(n, want):
    assert ls.size_for(n) == want and (want - 1) % (ls.SECTIONS * ls.QUADS) == 0


def test_heights_round_trip_and_the_scale_grows_with_the_range():
    h = np.linspace(-80.0, 160.0, 127 * 127).reshape(127, 127)
    v, mid, scale = ls.encode(h)
    assert v.dtype == np.dtype("<u2") and mid == 40.0 and scale == 100.0
    back = mid + (v.astype(float) - 32768) * scale / 128 / 100
    assert np.abs(back - h).max() < 0.01
    _, _, big = ls.encode(np.array([[0.0, 900.0]]))                   # 900 m: past 256 m, the scale grows
    assert big > 100.0 and big % 100 == 0


def test_the_landscape_sits_under_the_tile_its_rows_north_to_south():
    n = 253
    y = np.linspace(1.0, -1.0, n)[:, None] * np.ones((1, n))         # rows north (+Y) -> south: height = y
    x = np.ones((n, 1)) * np.linspace(-1.0, 1.0, n)[None, :]
    h = 10 * y + 3 * x
    s = ls.spec(h, 2000.0)
    assert s["size"] == [253, 253] and s["location_cm"][:2] == [-100000.0, -100000.0]
    assert s["scale_cm"][0] == pytest.approx(2000.0 * 100 / 252)
    rec = s | {"location_cm": s["location_cm"]}
    raw = ls.encode(ls.resample(h, 253))[0].tobytes()
    for bx, by in ((0.0, 0.0), (800.0, 900.0), (-950.0, -400.0)):     # Blender x east, y north
        assert ls.ground_at(rec, raw, bx, by) == pytest.approx(10 * by / 1000 + 3 * bx / 1000, abs=0.02)


def test_resampling_keeps_the_edges():
    h = np.arange(100.0).reshape(10, 10)
    r = ls.resample(h, 19)
    assert r.shape == (19, 19) and r[0, 0] == 0 and r[-1, -1] == 99 and r[0, -1] == 9


def test_only_a_generated_terrain_with_a_built_greybox_wants_a_landscape(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from asset_pipeline.project import ProjectStore
    from asset_pipeline.stages import sw_site
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("g")
    terrain = SimpleNamespace(heightmap=None)
    monkeypatch.setattr(sw_site, "load_layout", lambda st: SimpleNamespace(terrain=terrain))
    assert not ls.wanted(store)                                      # no greybox built yet
    sw_site.site_dir(store).mkdir(parents=True, exist_ok=True)
    (sw_site.site_dir(store) / "terrain.png").write_bytes(b"png")
    assert ls.wanted(store)
    terrain.heightmap = "site/terrain_far/core.png"                  # a heightmap project keeps its own Landscape
    assert not ls.wanted(store)


def test_rivers_and_canals_take_the_seas_material_at_a_steady_depth():
    from asset_pipeline import dressing
    man = {"slots": [{"id": "sea", "type": "sea"}, {"id": "terrain-river0", "type": "river"}, {"id": "canal1", "type": "canal"},
                     {"id": "street-1", "type": "street"}]}
    assert dressing.sea_slots(man) == ["sea"] and dressing.river_slots(man) == ["terrain-river0", "canal1"]
    from PIL import Image
    im = Image.open(dressing.river_depth())
    assert im.getpixel((0, 0))[0] == round(dressing.RIVER_DEPTH * 255)    # no shore foam: deeper than the foam's reach

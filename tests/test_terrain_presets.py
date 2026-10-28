"""Terrain presets (terrain_gen.PRESETS), the Terrain picker's API, and the layout presets that need
water lowering real water into the ground (manhattan, canal-city, island-city, harbour-port)."""
import math
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from asset_pipeline import city, city_roll, greybox, layout_presets, terrain_gen
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.schema import TerrainSpec
from asset_pipeline.stages import sw_site
from asset_pipeline.ui import app as ui_app

STATIC = Path(ui_app.STATIC)


def tile(name, **kw):
    t = TerrainSpec(extent_m=8000, resolution=401, shore_m=1400)
    return terrain_gen.with_settings(t, terrain_gen.get(name, seed=2).model_copy(update=kw))


def heights(t):
    return greybox.Heights(greybox.make_terrain(t), t.extent_m)


def test_eight_presets_each_with_a_description():
    assert list(terrain_gen.PRESETS) == ["flat-plain", "coastal-plain", "river-valley", "hill-city", "mountain-bowl",
                                         "plateau", "island", "canyon"]
    for p in terrain_gen.summary():
        assert p["description"] and terrain_gen.PRESETS[p["name"]].name == p["name"]
    with pytest.raises(ValueError):
        terrain_gen.get("nope")


@pytest.mark.parametrize("name", list(terrain_gen.PRESETS))
def test_each_preset_makes_its_own_ground_within_its_z_range(name):
    t = tile(name)
    h = greybox.make_terrain(t)
    assert np.isfinite(h).all() and t.z_range[0] <= h.min() and h.max() <= t.z_range[1]


def test_what_each_preset_is():
    flat = greybox.make_terrain(tile("flat-plain"))
    assert flat.min() > 0 and np.ptp(flat) < 60                                         # inland, nearly level
    coast = heights(tile("coastal-plain"))
    assert coast(0, -1700) < 0 and 0 < coast(0, -1440) < 3                              # the sea, a wide beach
    rv = tile("river-valley")
    mask = terrain_gen.water_mask(rv, terrain_gen.terrain_lines(rv))
    assert mask.sum() > 100                                                             # a real river
    hv = heights(rv)
    assert hv(2300, 0) > hv(0, 0) + 100                                                 # ridges beside it
    hill = greybox.make_terrain(tile("hill-city"))
    assert np.percentile(hill, 99) > np.percentile(hill, 50) + 40
    bowl = heights(tile("mountain-bowl"))
    assert min(bowl(2200 * math.cos(a), 2200 * math.sin(a)) for a in np.radians([0, 90, 180])) > bowl(0, 0) + 250
    assert bowl(0, -2200) < bowl(0, 2200) - 200                                         # its pass faces the sea
    pl = heights(tile("plateau"))
    assert pl(0, 0) > pl(0, 2200) + 40
    isl = heights(tile("island"))
    assert isl(0, 0) > 0 and all(isl(3000 * math.cos(a), 3000 * math.sin(a)) < 0 for a in np.radians(range(0, 360, 45)))
    cy = tile("canyon")
    hc = heights(cy)
    ln = terrain_gen.river_line(cy)
    m = ln.interpolate(0.5, normalized=True)
    assert hc(m.x + 300, m.y) - hc(m.x, m.y) > 50                                        # deep below the plateau


def _bed_below_banks(layout, line, depth=2.5):
    h = city.apply_pads(layout, greybox.make_terrain(layout.terrain))
    hs = greybox.Heights(h, layout.terrain.extent_m)
    f = city.Fields(city.with_preset(layout), h)
    m = line.interpolate(0.5, normalized=True)
    nrm = np.array(line.coords[-1]) - np.array(line.coords[0])
    nrm = np.array([-nrm[1], nrm[0]]) / np.linalg.norm(nrm)
    side = np.array([m.x, m.y]) + 120 * nrm
    return hs(m.x, m.y) < hs(*side) - depth and bool(f.water(m.x, m.y))


@pytest.mark.parametrize("name,kind", [("manhattan", "channel"), ("canal-city", "canal")])
def test_water_presets_lower_real_water_into_the_ground(name, kind):
    L = layout_presets.apply(sw_site.starter_city(), name)
    lines = [w for wid, _, w in city.layout_water(L.city, L.terrain) if wid.startswith(kind)]
    assert lines and any(_bed_below_banks(L, w.line) for w in lines)


def test_harbour_port_cuts_dock_basins_below_the_sea_and_island_city_its_strait():
    L = layout_presets.apply(sw_site.starter_city(), "harbour-port")
    basins = city.dock_basins(L)
    assert len(basins) == 3                                    # the starter city's one harbour district
    h = city.apply_pads(L, greybox.make_terrain(L.terrain))
    hs = greybox.Heights(h, L.terrain.extent_m)
    f = city.Fields(L, h)
    for q in basins:
        p = q.representative_point()
        assert hs(p.x, p.y) < L.sea_level and not bool(f.land(p.x, p.y))      # real water, not land
    I = layout_presets.apply(sw_site.starter_city(), "island-city")
    hi = city.apply_pads(I, greybox.make_terrain(I.terrain))
    C = np.array(city.main_centre(I.city))
    o = city.Outline(I.city, type("F", (), {"u": greybox.sea_axes(I.terrain)[0]})())
    gap = C + (float(o.radius_at(0.0)) + 150) * np.array([1.0, 0.0])
    assert greybox.Heights(hi, I.terrain.extent_m)(*gap) < I.sea_level


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("t", name="T", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    monkeypatch.setattr(sw_site, "plan_city", lambda store: {"buildings": 100, "warnings": []})
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    return TestClient(ui_app.create_app(JobQueue())), s


def _wait(c, j):
    for _ in range(300):
        st = c.get(f"/api/jobs/{j['id']}").json()
        if st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    return st


def test_the_terrain_picker_api_and_cli(client, capsys):
    c, s = client
    st = c.get("/api/projects/t/site/roll").json()["terrain"]
    assert st["name"] is None and [p["name"] for p in st["presets"]] == list(terrain_gen.PRESETS)
    assert c.post("/api/projects/t/site/terrain", json={"preset": "nope"}).status_code == 400
    j = c.post("/api/projects/t/site/terrain", json={"preset": "hill-city", "seed": 12}).json()
    r = _wait(c, j)
    assert r["status"] == "done" and r["result"]["terrain"] == "hill-city" and r["result"]["seed"] == 12
    L = sw_site.load_layout(s)
    assert L.terrain.settings.name == "hill-city" and L.terrain.settings.seed == 12 and L.city.preset.name == "utopia"
    assert c.get("/api/projects/t/site/roll").json()["terrain"]["name"] == "hill-city"
    from asset_pipeline import cli
    cli.main(["terrain", "presets"])
    assert "mountain-bowl" in capsys.readouterr().out
    cli.main(["terrain", "roll", "t", "--preset", "island", "--seed", "3"])
    out = capsys.readouterr().out
    assert "terrain island, terrain seed 3" in out and "ap terrain roll t --preset island --seed 3" in out
    # a heightmap project: replaced only when confirmed
    L = sw_site.load_layout(s)
    L.terrain.heightmap = "site/hm.png"
    from PIL import Image
    Image.fromarray(greybox.to_png16(greybox.make_terrain(L.terrain), L.terrain.z_range)).save(sw_site.site_dir(s) / "hm.png")
    sw_site.save_layout(s, L)
    assert c.post("/api/projects/t/site/terrain", json={"preset": "plateau"}).status_code == 409
    r = _wait(c, c.post("/api/projects/t/site/terrain", json={"preset": "plateau", "replace_heightmap": True}).json())
    assert r["status"] == "done" and sw_site.load_layout(s).terrain.heightmap is None


def test_the_picker_sits_beside_the_layout_picker():
    html = (STATIC / "index.html").read_text()
    i, j = html.index('id="city-layout-preset"'), html.index('id="city-terrain-preset"')
    assert i < j < html.index('id="city-roll-keep-shape"')
    js = (STATIC / "city.js").read_text()
    assert 'siteAction("site/terrain"' in js and "Replace the heightmap?" in js

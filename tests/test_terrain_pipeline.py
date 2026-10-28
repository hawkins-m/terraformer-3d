"""A reroll runs on through Greybox (Blender build) and Shots (auto shots placed, one rendered) on a
generated terrain (terrain settings) and on a heightmap terrain. Real Blender; skipped without it."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from asset_pipeline import city_roll, greybox, terrain_gen
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_shots, sw_site

REAL_BLENDER = Path("/snap/bin/blender")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Pipe Test", start="city")
    L = sw_site.starter_city()
    L.terrain.resolution = 505                     # a quicker build; the pipeline is the same
    write_json(sw_site.site_dir(s) / "layout.json", L.model_dump(mode="json"))
    return s


def run_through(store):
    L = sw_site.load_layout(store)
    assert L.shots and any(s.tier == "medium" for s in L.shots)           # the roll placed the auto shots
    data = sw_site.build(store, force=True)
    assert data["slots"] and (sw_site.site_dir(store) / "greybox.blend").is_file()
    assert {"terrain", "housing"} & {s["type"] for s in data["slots"]}
    shot = next(s.id for s in L.shots if s.tier == "medium")
    out = sw_shots.render(store, [shot])
    assert shot in out
    depth = next((store.root / "shots" / shot).glob("depth*.png"), None)
    assert depth is not None and Image.open(depth).size[0] > 100


@pytest.mark.skipif(not REAL_BLENDER.exists(), reason="needs Blender")
def test_a_reroll_on_a_generated_terrain_runs_through_greybox_and_shots(store):
    city_roll.set_terrain(store, terrain_gen.get("river-valley", seed=3), log=lambda *a: None)
    city_roll.roll(store, seed=11, preset="classic", log=lambda *a: None)
    L = sw_site.load_layout(store)
    assert L.terrain.settings.name == "river-valley" and L.city.preset.name == "classic"
    run_through(store)


@pytest.mark.skipif(not REAL_BLENDER.exists(), reason="needs Blender")
def test_a_reroll_on_a_heightmap_terrain_runs_through_greybox_and_shots(store):
    L = sw_site.load_layout(store)
    h = greybox.make_terrain(L.terrain)
    Image.fromarray(greybox.to_png16(h, L.terrain.z_range)).save(sw_site.site_dir(store) / "hm.png")
    L.terrain.heightmap = "site/hm.png"
    sw_site.save_layout(store, L)
    city_roll.roll(store, seed=11, preset="walled-castle", log=lambda *a: None)
    L = sw_site.load_layout(store)
    assert L.terrain.heightmap == "site/hm.png" and L.terrain.settings is None
    run_through(store)

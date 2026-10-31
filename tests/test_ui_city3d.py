"""The Layout tab's 3D city viewer: city_view.py's scene from the plan (cached, hand edits applied),
its API, three.js vendored (no CDN), and the viewer in a real browser (3D, top-down, colour by
height, toggles, hover; the plan image when WebGL is off). Synthetic projects."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from asset_pipeline import city, city_view, layout_presets
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, read_json
from asset_pipeline.stages import sw_site
from asset_pipeline.ui import app as ui_app
from test_ui_layout import BROWSER_PY, STATIC, _browse


def planned_city(store: ProjectStore) -> dict:
    """A classic city on the starter terrain (a smaller height grid), planned: site/city3d.json written."""
    L = layout_presets.apply(sw_site.starter_city(), "classic")
    L.terrain = L.terrain.model_copy(update={"resolution": 513})
    sw_site.save_layout(store, L)
    return sw_site.plan_city(store)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    return ProjectStore.create("p", start="city")


def test_the_plan_writes_the_viewers_scene_and_it_is_reused_until_something_changes(store):
    stats = planned_city(store)
    d = read_json(city_view.path(store))
    assert d["sig"] == city_view.sig(store) and d["types"] == city_view.TYPES
    assert len(d["buildings"]) >= stats["buildings"] > 1000
    rows = np.array(d["buildings"])
    assert rows.shape[1] == 9 and set(rows[:, 7].astype(int)) <= set(range(len(d["types"])))
    assert {"tower", "rowhouse", "house"} <= {d["types"][int(t)] for t in rows[:, 7]}
    assert rows[:, 8].max() < len(d["districts"]) and (rows[:, 5] > 0).all()
    n = d["terrain"]["n"]
    assert n <= city_view.TERRAIN_N and len(d["terrain"]["z"]) == n * n
    kinds = {w["kind"] for w in d["ways"]}
    assert {"river", "street", "highway"} <= kinds and d["parks"] and all(len(w["pts"][0]) == 3 for w in d["ways"])
    assert city_view.load(store) == d                                   # cached: no new plan
    before = city_view.path(store).stat().st_mtime_ns
    lay = sw_site.site_dir(store) / "layout.json"
    lay.write_text(lay.read_text())                                     # the layout saved again: made anew
    assert city_view.load(store)["sig"] == city_view.sig(store) and city_view.path(store).stat().st_mtime_ns != before


def test_hand_edits_move_copy_and_remove_buildings(store, monkeypatch):
    planned_city(store)
    L = sw_site.load_layout(store)
    b0 = city.plan(L, sw_site.plan_terrain(store, L)).buildings[0]
    first = [round(b0.x, 1), round(b0.y, 1)]
    sid = city_view.slot_id(b0.kind, b0.x, b0.y, L.city.tile_m)
    D = np.eye(4)
    D[:3, 3] = [100.0, 0.0, 0.0]
    monkeypatch.setattr(city_view, "_edits", lambda s: {sid: [(sid, D), (sid + "-1", np.eye(4))]})
    near = lambda rows, x, y: any(abs(b[0] - x) < 0.2 and abs(b[1] - y) < 0.2 for b in rows)  # noqa: E731
    rows = city_view.scene(store)["buildings"]
    assert near(rows, first[0] + 100, first[1]) and near(rows, *first)          # moved, and a copy where it was
    monkeypatch.setattr(city_view, "_edits", lambda s: {sid: []})
    assert not near(city_view.scene(store)["buildings"], *first)                # deleted in Blender: gone


def test_the_api_serves_the_scene_and_404s_without_a_city(store, tmp_path):
    c = TestClient(ui_app.create_app(JobQueue()))
    assert c.get("/api/projects/p/site/city3d").status_code == 404           # no layout yet
    planned_city(store)
    r = c.get("/api/projects/p/site/city3d")
    assert r.status_code == 200 and r.json()["buildings"] and r.json()["preset"] == "classic"


def test_three_js_is_vendored_and_loaded_from_the_app_only():
    vendor = STATIC / "vendor" / "three"
    assert (vendor / "three.module.min.js").stat().st_size > 500_000 and (vendor / "LICENSE").is_file()
    assert "from './three.module.min.js'" in (vendor / "OrbitControls.js").read_text()
    js = (STATIC / "city3d.js").read_text()
    assert "https://" not in js and "http://" not in js
    assert 'import("/static/vendor/three/three.module.min.js")' in js
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/city.js") < html.index("/static/city3d.js") and 'id="city3d-view"' in html
    lic = (STATIC.parents[2] / "THIRD_PARTY_LICENSES.md").read_text()
    assert "three.js" in lic


VIEWER_SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_selector("button[data-tab=city]"); pg.wait_for_timeout(1000)
    pg.click("button[data-tab=city]")
    pg.wait_for_function("() => /buildings/.test(document.querySelector('#city3d-label')?.textContent || '')", timeout=90000)
    out["label"] = pg.locator("#city3d-label").inner_text()
    out["count"] = pg.evaluate("() => c3dData.buildings.length")
    out["instances"] = pg.evaluate("() => c3d.meshes.reduce((n, m) => n + m.count, 0)")
    out["canvas"] = pg.locator("#city3d-view canvas").count()
    pg.click("#city3d-top"); pg.wait_for_timeout(500)
    out["top"] = [pg.locator("#city3d-top").inner_text(), pg.evaluate("() => c3dTop && c3d.pan.enabled && !c3d.orbit.enabled")]
    pg.select_option("#city3d-color", "height"); pg.wait_for_timeout(300)
    out["ramp"] = pg.locator("#city3d-legend .c3d-ramp").count()
    pg.uncheck('[data-c3d-show="buildings"]'); pg.wait_for_timeout(200)
    out["hidden"] = pg.evaluate("() => !c3d.groups.buildings.visible && c3d.groups.roads.visible")
    out["errors"] = errs
    nogl = b.new_page(viewport={"width": 1400, "height": 1000})
    nogl.add_init_script("HTMLCanvasElement.prototype.getContext = () => null")
    nogl.goto(url); nogl.wait_for_selector("button[data-tab=city]"); nogl.wait_for_timeout(1000)
    nogl.click("button[data-tab=city]")
    nogl.wait_for_function("() => /WebGL/.test(document.querySelector('#city3d-label')?.textContent || '')", timeout=90000)
    out["fallback"] = [nogl.locator("#city3d-view img.c3d-plan").count(), nogl.locator("#city3d-top").is_disabled()]
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_the_viewer_in_a_browser(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, VIEWER_SCRIPT, seed=planned_city)
    assert out["canvas"] == 1 and out["count"] > 1000 and out["instances"] == out["count"]
    assert out["label"].startswith(f"{out['count']:,} buildings")
    assert out["top"] == ["3D view", True] and out["ramp"] == 1 and out["hidden"]
    assert out["fallback"] == [1, True]
    assert out["errors"] == []

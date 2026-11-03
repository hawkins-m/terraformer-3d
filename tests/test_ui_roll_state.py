"""The Layout tab's "Start from a random city" box belongs to one project: a switch or New project reloads
every field from it, a new project starts on classic with the extended types ticked, and the Layout
picker lists classic first, the rest alphabetically, Random preset last."""
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from asset_pipeline import city_roll, config, layout_presets
from asset_pipeline.project import ProjectStore
from asset_pipeline.schema import TerrainCompose
from asset_pipeline.stages import sw_site
from test_ui_layout import BROWSER_PY, REPO, _free_port

SPEC = {"base": "coast", "side": "south", "ground": "rolling", "features": [{"type": "hills", "at": "inland", "size": "medium"}], "city": []}


def test_layout_picker_order_and_the_rolls_are_untouched():
    names = [p["name"] for p in layout_presets.summary()]
    assert names[0] == "classic" and names[1:] == sorted(names[1:])
    assert set(names) == set(layout_presets.PRESETS) and len(names) == 20
    assert list(layout_presets.PRESETS)[:3] == ["utopia", "classic", "grid"]        # random_name still draws from this order


def test_a_project_without_a_city_shows_the_new_project_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = city_roll.summary(ProjectStore.create("fresh"))
    assert s["preset"] == "classic" and s["size"]["extended_types"] is True
    assert s["terrain"]["name"] is None and s["terrain"]["described"] is None
    own = ProjectStore.create("own")
    sw_site.init(own, city_mode=True)
    lay = sw_site.load_layout(own)
    lay.city.preset = layout_presets.get("utopia")
    lay.city.extended_types = False
    sw_site.save_layout(own, lay)
    s = city_roll.summary(own)
    assert s["preset"] == "utopia" and not s["size"]["extended_types"]               # a saved layout is shown as saved


SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {}
def state(pg):
    return pg.evaluate("""() => ({
      describe: document.querySelector('#city-terrain-describe').value,
      chosen: document.querySelector('#city-terrain-chosen').textContent,
      terrain: document.querySelector('#city-terrain-preset').value,
      editHidden: document.querySelector('#city-terrain-spec-box').hidden,
      edit: document.querySelector('#city-terrain-spec').value,
      layout: document.querySelector('#city-layout-preset').value,
      layoutSeed: document.querySelector('#city-layout-seed').value,
      terrainSeed: document.querySelector('#city-terrain-seed').value,
      rollSeed: document.querySelector('#city-roll-seed').value,
      randoms: [document.querySelector('#city-layout-seed-random').checked, document.querySelector('#city-terrain-seed-random').checked],
      size: document.querySelector('#city-size').value,
      extended: document.querySelector('#city-extended').checked,
      keep: [...document.querySelectorAll('#city-roll-keep [data-keep]:checked')].map(x => x.dataset.keep),
      options: [...document.querySelectorAll('#city-layout-preset option')].map(o => o.value),
    })""")
def go(pg, slug):
    pg.select_option("#project", slug); pg.wait_for_timeout(1500)
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_selector("#project", timeout=20000)
    pg.click("button[data-tab=city]"); pg.wait_for_timeout(1500)
    out["desc1"] = state(pg)
    pg.click("#city-terrain-spec-edit"); out["desc1_open"] = state(pg)
    pg.fill("#city-roll-seed", "77"); pg.check("#city-layout-seed-random"); pg.check('[data-keep]')
    go(pg, "plain"); out["plain"] = state(pg)
    go(pg, "fresh"); out["fresh"] = state(pg)
    pg.click("#city-terrain-spec-edit"); out["fresh_open"] = state(pg)
    go(pg, "desc"); out["desc2"] = state(pg)
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_each_project_shows_its_own_state_in_the_roll_box(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("AP_ROOT", str(root))
    desc = ProjectStore.create("desc")
    sw_site.init(desc, city_mode=True)
    city_roll.remember_described(desc, TerrainCompose.model_validate(SPEC), "a coast with hills")
    sw_site.init(ProjectStore.create("plain"), city_mode=True)
    ProjectStore.create("fresh")                                           # New project: no layout yet
    config.save_user({"paths": {"ap_root": str(root)}})
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "asset_pipeline.cli", "ui", "--port", str(port)], env=dict(os.environ, AP_ROOT=str(root)),
                           cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        drive = tmp_path / "drive.py"
        drive.write_text(SCRIPT)
        r = subprocess.run([str(BROWSER_PY), str(drive), f"http://127.0.0.1:{port}/?project=desc"], capture_output=True, text=True, timeout=120)
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
        assert line, r.stdout + r.stderr
        out = json.loads(line[7:])
    finally:
        srv.terminate()
        srv.wait(10)
    assert out["errors"] == []
    d = out["desc1"]
    assert d["describe"] == "a coast with hills" and d["terrain"] == ""
    assert json.loads(out["desc1_open"]["edit"])["base"] == "coast"
    assert out["desc1_open"]["edit"] and not out["desc1_open"]["editHidden"]
    assert d["options"][0] == "classic" and d["options"][-1] == "random"
    assert d["options"][1:-1] == sorted(d["options"][1:-1])
    for k in ("plain", "fresh"):                                           # nothing carried over from the described project
        s = out[k]
        assert s["describe"] == "" and s["chosen"] == "" and s["edit"] == "" and s["editHidden"]
        assert s["rollSeed"] == "" and s["randoms"] == [False, False] and s["keep"] == []
        assert s["layout"] == "classic" and s["terrain"] == "" and s["extended"] is True and s["size"] == "standard"
    assert out["fresh_open"]["edit"] == "" and not out["fresh_open"]["editHidden"]   # opened by hand: empty, no coast/rolling default
    assert out["desc2"]["describe"] == "a coast with hills"                  # and back: its own words again

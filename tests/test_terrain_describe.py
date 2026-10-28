"""Describe the terrain (terrain_describe.py): words to a terrain preset and tweaks, by the running
local model's text mode or by keywords when it isn't running; the API, the text box and the CLI."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import terrain_describe as td
from asset_pipeline import vlm
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_site
from asset_pipeline.ui import app as ui_app

STATIC = Path(ui_app.STATIC)


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr(vlm, "state", lambda: None)          # never a real server in these tests


@pytest.mark.parametrize("text,preset,tweaks", [
    ("a river valley between rugged mountains", "river-valley", {"base": "rugged", "mountains_m": 320.0}),
    ("an island with sandy beaches", "island", {"beach_m": 150.0}),
    ("a city in a deep canyon", "canyon", {"canyon_m": 90.0}),
    ("a flat landlocked prairie", "flat-plain", {}),
    ("seven hills above the coast", "hill-city", {}),
    ("a terraced plateau", "plateau", {"terraces": 4}),
    ("a city ringed by high mountains with a river", "mountain-bowl", {"mountains_m": 520.0, "river": True}),
])
def test_keywords_pick_a_preset_and_tweaks(text, preset, tweaks):
    r = td.describe(text)
    assert r["source"] == "keywords" and r["preset"] == preset
    assert {k: v for k, v in r["tweaks"].items() if k in tweaks} == tweaks
    for k, v in tweaks.items():
        assert getattr(r["settings"], k) == v
    assert r["settings"].name == preset and preset in r["summary"]


def test_no_matching_word_falls_back_to_the_pickers_preset():
    assert td.describe("somewhere nice", prefer="plateau")["preset"] == "plateau"
    assert td.describe("somewhere nice")["preset"] == "coastal-plain"
    with pytest.raises(ValueError):
        td.describe("   ")


def test_the_running_local_model_decides_when_it_answers_well(monkeypatch):
    calls = []

    class Fake:
        def json_text(self, system, prompt, images, schema):
            calls.append((prompt, images, schema))
            return json.dumps({"preset": "mountain-bowl", "tweaks": {"river": True, "hills": 3, "nonsense": 1},
                               "reason": "bowl of peaks with a stream"})
    monkeypatch.setattr(vlm, "state", lambda: {"url": "http://x", "pid": 1})
    monkeypatch.setattr(vlm, "client", lambda st: Fake())
    r = td.describe("a city in a bowl of peaks with a stream")
    assert r["source"] == "vlm" and r["preset"] == "mountain-bowl"
    assert r["tweaks"] == {"river": True, "hills": 3} and r["settings"].river and r["settings"].hills == 3
    prompt, images, schema = calls[0]
    assert images == [] and "mountain-bowl" in prompt and schema["properties"]["preset"]["enum"][0] == "flat-plain"


@pytest.mark.parametrize("answer", ["not json", json.dumps({"preset": "volcano", "tweaks": {}}),
                                    json.dumps({"preset": "island", "tweaks": {"base": "lumpy"}})])
def test_a_bad_answer_falls_back_to_keywords(monkeypatch, answer):
    monkeypatch.setattr(vlm, "state", lambda: {"url": "http://x", "pid": 1})
    monkeypatch.setattr(vlm, "client", lambda st: type("F", (), {"json_text": lambda self, *a: answer})())
    r = td.describe("an island")
    assert r["source"] == "keywords" and r["preset"] == "island"


def test_the_model_is_never_started_for_it(monkeypatch):
    monkeypatch.setattr(vlm, "up", lambda *a, **k: pytest.fail("started the model"))
    assert td.describe("a canyon")["source"] == "keywords"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("t", name="T", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    monkeypatch.setattr(sw_site, "plan_city", lambda store: {"buildings": 100, "warnings": []})
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    return TestClient(ui_app.create_app(JobQueue())), s


def test_describe_api_shows_the_choice_and_apply_makes_exactly_it(client, capsys):
    c, s = client
    r = c.post("/api/projects/t/site/terrain/describe", json={"text": "a rugged island with cliffs"}).json()
    assert r["preset"] == "island" and r["source"] == "keywords" and r["settings"]["base"] == "rugged"
    assert r["settings"]["beach_m"] == 0.0 and "rugged" in r["summary"]
    j = c.post("/api/projects/t/site/terrain", json={"settings": r["settings"], "seed": 8}).json()
    for _ in range(300):
        st = c.get(f"/api/jobs/{j['id']}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert st["status"] == "done", st
    t = sw_site.load_layout(s).terrain.settings
    assert (t.name, t.base, t.beach_m, t.seed, t.coast) == ("island", "rugged", 0.0, 8, "all")
    assert c.post("/api/projects/t/site/terrain/describe", json={"text": " "}).status_code == 400
    from asset_pipeline import cli
    cli.main(["terrain", "roll", "t", "--describe", "a flat plain with a wide river", "--seed", "4"])
    out = capsys.readouterr().out
    assert "described as flat-plain (keywords)" in out and "river_w_m=130.0" in out
    t = sw_site.load_layout(s).terrain.settings
    assert t.river and t.river_w_m == 130.0 and t.seed == 4


def test_the_text_box_sits_with_the_terrain_picker():
    html = (STATIC / "index.html").read_text()
    assert html.index('id="city-terrain-preset"') < html.index('id="city-terrain-describe"') < html.index('id="city-roll-keep-shape"')
    assert "Describe the terrain" in html and 'id="city-terrain-chosen"' in html
    js = (STATIC / "city.js").read_text()
    assert "site/terrain/describe" in js and "keyword matching" in js

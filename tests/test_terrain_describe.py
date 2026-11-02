"""Describe the terrain (terrain_describe.py): words to a TerrainCompose spec, by the running local model's text
mode (validated, asked once more if it doesn't validate) or a keyword and phrase parser that combines every
landform it finds; the API shows the spec as a line and Apply terrain builds exactly it."""
import json

import pytest

from asset_pipeline import terrain_describe as TD
from asset_pipeline.schema import TerrainCompose

FIVE = {
    "island city with a volcano in the centre and cities circling it":
        "Island · volcano (centre, large, crater) · city rings the lower slopes",
    "a river valley between two ridges with the city along the river":
        "Valley (sea south) · river (centre, medium) · city along the river",
    "coastal cliffs on the west, a bay with a harbour, hills inland":
        "Coast (sea west) · cliffs (west, medium, steep) · bay (coast, medium) · hills (inland, medium) · city along the coast",
    "a high plateau with a lake in the middle": "Plateau · plateau (centre, large) · lake (centre, medium)",
    "flat delta with many river channels": "Coast (sea south), flat · 5 river channels (centre, medium)",
}


@pytest.mark.parametrize("text,line", list(FIVE.items()))
def test_keywords_combine_every_landform_they_find(text, line):
    r = TD.describe(text, use_vlm=False)
    assert r["source"] == "keywords" and r["line"] == line and r["matched"] and r["ignored"] == []


def test_the_volcano_island_is_one_spec_with_a_ring_round_the_cone():
    spec = TD.keywords("island city with a volcano in the centre and cities circling it")["spec"]
    assert spec.base == "island" and [f.type for f in spec.features] == ["volcano"]
    v = spec.features[0]
    assert v.at == "centre" and v.crater and v.size == "large"
    assert [(h.kind, h.feature) for h in spec.city] == [("ring", 0)]


def test_places_sizes_counts_heights_and_negation():
    spec = TD.keywords("three steep hills in the north-east, a small lake to the west, a 600 m peak and no river")["spec"]
    by = {f.type: f for f in spec.features}
    assert set(by) == {"hills", "lake", "peak"}
    assert by["hills"].at == "north-east" and by["hills"].count == 3 and by["hills"].steepness == "steep"
    assert by["lake"].at == "west" and by["lake"].size == "small" and by["peak"].height_m == 600
    assert not TD.keywords("a coast without cliffs")["spec"].features


def test_what_it_cant_show_is_listed_and_nothing_known_is_not_matched():
    r = TD.describe("three hills, a snowy desert", use_vlm=False)
    assert r["ignored"] == ["a snowy desert"] and r["matched"]
    r = TD.describe("purple unicorns", use_vlm=False)
    assert not r["matched"] and r["ignored"] == ["purple unicorns"]
    with pytest.raises(ValueError):
        TD.describe("  ")


GOOD = {"spec": {"base": "island", "features": [{"type": "volcano", "at": "centre", "size": "large", "crater": True}],
                 "city": [{"kind": "ring", "feature": 0}]}, "ignored": ["sunset"]}


def test_the_model_decides_when_its_answer_validates():
    calls = []

    def ask(system, prompt):
        calls.append(prompt)
        return json.dumps(GOOD)
    r = TD.describe("an island with a volcano", ask=ask)
    assert r["source"] == "vlm" and r["tries"] == 1 and r["ignored"] == ["sunset"] and len(calls) == 1
    assert r["line"].startswith("Island · volcano (centre, large, crater)")
    assert '"TerrainFeature"' in calls[0] or "TerrainFeature" in calls[0]          # the schema is in the prompt


def test_an_invalid_answer_is_asked_once_more_with_the_error():
    answers = [json.dumps({"spec": {"base": "moon", "features": []}}), json.dumps(GOOD)]
    prompts = []

    def ask(system, prompt):
        prompts.append(prompt)
        return answers.pop(0)
    r = TD.describe("an island with a volcano", ask=ask)
    assert r["source"] == "vlm" and r["tries"] == 2 and "didn't validate" in prompts[1]


@pytest.mark.parametrize("bad", ["not json", json.dumps({"spec": {"base": "moon"}}), json.dumps(["x"])])
def test_two_bad_answers_fall_back_to_keywords(bad):
    r = TD.describe("a high plateau with a lake in the middle", ask=lambda s, p: bad)
    assert r["source"] == "keywords" and r["line"] == FIVE["a high plateau with a lake in the middle"]


def test_a_server_failure_falls_back_to_keywords_and_the_model_is_never_started(monkeypatch):
    from asset_pipeline import vlm

    def boom(s, p):
        raise ConnectionError("down")
    assert TD.describe("an island", ask=boom)["source"] == "keywords"
    monkeypatch.setattr(vlm, "state", lambda: None)
    monkeypatch.setattr(vlm, "up", lambda *a, **k: pytest.fail("started the model"), raising=False)
    assert TD.describe("an island")["source"] == "keywords"


def test_every_spec_the_parser_makes_builds(tmp_path):
    from asset_pipeline import terrain_gen
    from asset_pipeline.stages import sw_site
    t = sw_site.starter_city().terrain.model_copy(update={"resolution": 257})
    for text in FIVE:
        spec = TD.describe(text, use_vlm=False)["spec"]
        s = TD.settings(spec, 3, t.extent_m)
        assert TerrainCompose.model_validate(s.compose.model_dump()) == spec
        terrain_gen.make(terrain_gen.with_settings(t, s))


import time  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from asset_pipeline.jobs import JobQueue  # noqa: E402
from asset_pipeline.project import ProjectStore, write_json  # noqa: E402
from asset_pipeline.stages import sw_site  # noqa: E402
from asset_pipeline.ui import app as ui_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("t", name="T", start="city")
    L = sw_site.starter_city()
    L.terrain = L.terrain.model_copy(update={"resolution": 257})
    write_json(sw_site.site_dir(s) / "layout.json", L.model_dump(mode="json"))
    monkeypatch.setattr(sw_site, "plan_city", lambda store: {"buildings": 100, "warnings": []})
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    from asset_pipeline import vlm
    monkeypatch.setattr(vlm, "state", lambda: None)
    return TestClient(ui_app.create_app(JobQueue())), s


def _wait(c, j):
    for _ in range(400):
        st = c.get(f"/api/jobs/{j['id']}").json()
        if st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    return st


def test_describe_api_shows_the_spec_and_apply_builds_exactly_it(client, capsys):
    c, s = client
    before = (sw_site.site_dir(s) / "layout.json").read_text()
    text = "island city with a volcano in the centre and cities circling it"
    r = c.post("/api/projects/t/site/terrain/describe", json={"text": text}).json()
    assert r["source"] == "keywords" and r["line"] == FIVE[text] and r["ignored"] == [] and r["matched"]
    assert r["compose"]["base"] == "island" and r["compose"]["features"][0]["type"] == "volcano"
    assert (sw_site.site_dir(s) / "layout.json").read_text() == before            # Describe alone changes nothing
    st = _wait(c, c.post("/api/projects/t/site/terrain", json={"compose": r["compose"], "seed": 8}).json())
    assert st["status"] == "done", st.get("error")
    assert st["result"]["described"] == {"source": "spec", "line": FIVE[text]}
    t = sw_site.load_layout(s).terrain.settings
    assert (t.name, t.seed, t.coast) == ("described", 8, "all") and t.compose.model_dump() == r["compose"]
    from asset_pipeline import city_roll
    assert city_roll.summary(s)["terrain"]["line"] == FIVE[text]
    assert c.post("/api/projects/t/site/terrain/describe", json={"text": " "}).status_code == 400
    from asset_pipeline import cli
    cli.main(["terrain", "roll", "t", "--describe", "flat delta with many river channels", "--seed", "4"])
    out = capsys.readouterr().out
    assert "described (keywords): Island, flat · 5 river channels" in out     # the active island kept: it has a sea
    assert sw_site.load_layout(s).terrain.settings.compose.features[0].count == 5


def test_a_spec_typed_by_hand_is_checked_and_shown(client):
    c, _ = client
    good = {"base": "plateau", "features": [{"type": "lake", "at": "centre"}]}
    r = c.post("/api/projects/t/site/terrain/describe", json={"compose": good}).json()
    assert r["source"] == "spec" and r["line"] == "Plateau · lake (centre, medium)"
    bad = c.post("/api/projects/t/site/terrain/describe", json={"compose": {"base": "moon"}})
    assert bad.status_code == 400 and "terrain spec" in bad.json()["detail"]


def test_the_layout_tab_shows_the_line_and_applies_the_spec():
    from test_ui_layout import STATIC
    html = (STATIC / "index.html").read_text()
    assert html.index('id="city-terrain-preset"') < html.index('id="city-terrain-describe"') < html.index('id="city-terrain-chosen"')
    js = (STATIC / "city.js").read_text()
    assert "site/terrain/describe" in js and 'value="described"' in js and "out.compose = spec.compose" in js
    assert "Ignored:" in js and "local vision model" in js and "keywords (the local model isn't running)" in js


def test_edit_shows_the_spec_json_and_checks_it():
    from test_ui_layout import STATIC
    html = (STATIC / "index.html").read_text()
    assert html.index('id="city-terrain-describe-go"') < html.index('id="city-terrain-spec-edit"') < html.index('id="city-terrain-spec"')
    assert 'id="city-terrain-spec-check"' in html and "hidden" in html[html.index('id="city-terrain-spec-box"') - 5:][:60]
    js = (STATIC / "city.js").read_text()
    assert "function paintSpecEditor" in js and "{compose}" in js and "Not valid JSON" in js


# --- the base kept from the Terrain pick, landforms round the city (not under it) ------------------

def _current(name):
    from asset_pipeline import terrain_gen
    return TD.current(terrain_gen.get(name), sea_dir=-90.0)


def _built(spec, seed=5):
    from asset_pipeline import terrain_gen
    t = sw_site.starter_city().terrain.model_copy(update={"resolution": 257})
    t = terrain_gen.with_settings(t, TD.settings(spec, seed, t.extent_m))
    return t, terrain_gen.make(t)


def _core(t, h, half=(1500.0, 900.0)):
    """The heights over the city's core (the starter city's middle, round the terrain's centre)."""
    n = t.resolution
    xs = (np.arange(n) / (n - 1) - 0.5) * t.extent_m
    ys = (0.5 - np.arange(n) / (n - 1)) * t.extent_m
    C = t.settings.centre
    jj = np.abs(xs - C[0]) < half[0]
    ii = np.abs(ys - C[1]) < half[1]
    return h[np.ix_(ii, jj)]


import numpy as np  # noqa: E402


def test_plains_with_mountains_and_hills_stay_inland_with_a_flat_core():
    cur = _current("flat-plain")
    assert (cur["base"], cur["ground"]) == ("inland", "flat")
    r = TD.describe("add mountains and hills", use_vlm=False, current=cur)
    spec = r["spec"]
    assert spec.base == "inland" and spec.ground == "flat" and r["matched"]
    assert r["line"].startswith("Inland (from flat plain), flat · ")              # the base first, where it came from
    by = {f.type: f for f in spec.features}
    assert set(by) == {"ridge", "hills"} and by["ridge"].at == "north" and by["ridge"].beyond
    assert by["hills"].at == "edges" and not any(f.at == "centre" for f in spec.features)
    t, h = _built(spec)
    assert t.settings.coast == "none" and (h > 0).all()                           # no sea anywhere
    core = _core(t, h)
    assert core.max() - core.min() < 40                                          # nothing standing on the city's core
    assert h.max() > 300                                                         # the mountains are there, out beyond


def test_an_island_stays_an_island():
    r = TD.describe("add hills", use_vlm=False, current=_current("island"))
    assert r["spec"].base == "island" and r["line"].startswith("Island (from island) · hills (edges")
    assert TD.describe("add hills", use_vlm=False, current=_current("coastal-plain"))["line"].startswith(
        "Coast (sea south, from coastal plain) · hills (edges")


def test_a_sea_only_when_the_words_ask_for_one():
    cur = _current("flat-plain")
    for text in ("a lake", "cliffs on the west", "two ridges", "a volcano"):
        assert TD.describe(text, use_vlm=False, current=cur)["spec"].base == "inland", text
    for text in ("a beach", "hills by the sea", "a harbour", "an ocean to the east", "a lake shore"):
        assert TD.describe(text, use_vlm=False, current=cur)["spec"].base == "coast", text
    assert TD.describe("an ocean to the east", use_vlm=False, current=cur)["spec"].side == "east"
    assert TD.describe("a beach", use_vlm=False, current=_current("island"))["spec"].base == "island"


def test_a_city_on_a_hill_by_the_sea_has_its_hill_in_the_centre():
    for cur in (None, _current("flat-plain")):
        spec = TD.describe("a city on a hill by the sea", use_vlm=False, current=cur)["spec"]
        assert spec.base == "coast" and [(f.type, f.at) for f in spec.features] == [("hill", "centre")]
    t, h = _built(spec)
    from asset_pipeline import city
    core = _core(t, h, (600.0, 400.0))
    assert core.mean() > _core(t, h, (2400.0, 1400.0)).mean() + 15               # the core stands up
    L = sw_site.starter_city()
    L.terrain = t
    f = city.Fields(L, h)
    assert float(np.percentile(f.slope_grid, 99)) < L.city.max_slope              # gentle enough to build on


def test_a_landform_put_in_the_centre_stays_there():
    spec = TD.describe("a volcano in the centre", use_vlm=False, current=_current("flat-plain"))["spec"]
    assert (spec.base, spec.features[0].at, spec.features[0].beyond) == ("inland", "centre", False)


def test_the_model_keeps_the_current_base_and_moves_what_it_put_under_the_city():
    prompts = []
    answer = {"spec": {"base": "coast", "side": "south", "features": [{"type": "ridge", "at": "centre", "size": "large"},
                                                                      {"type": "hills", "at": "centre"}]}}

    def ask(system, prompt):
        prompts.append(prompt)
        return json.dumps(answer)
    r = TD.describe("add mountains and hills", ask=ask, current=_current("flat-plain"))
    assert "The terrain now: base inland, ground flat." in prompts[0] and "keep the terrain's base" in prompts[0]
    spec = r["spec"]
    assert r["source"] == "vlm" and (spec.base, spec.ground) == ("inland", "flat")
    assert [(f.type, f.at, f.beyond) for f in spec.features] == [("ridge", "north", True), ("hills", "edges", False)]
    assert r["line"].startswith("Inland (from flat plain), flat · ")


def test_describe_api_keeps_the_terrain_dropdowns_base(client):
    c, s = client
    r = c.post("/api/projects/t/site/terrain/describe", json={"text": "add mountains and hills", "current": "flat-plain"}).json()
    assert r["compose"]["base"] == "inland" and r["line"].startswith("Inland (from flat plain), flat")
    r = c.post("/api/projects/t/site/terrain/describe", json={"text": "add hills", "current": "described",
                                                              "current_compose": {"base": "island"}}).json()
    assert r["compose"]["base"] == "island"
    r = c.post("/api/projects/t/site/terrain/describe", json={"text": "add hills", "current": ""}).json()
    assert r["line"].startswith("Coast (sea south, from the project's coast)")
    js = (__import__("test_ui_layout").STATIC / "city.js").read_text()
    assert "current: sel.options.length ? sel.value : null" in js and "current_compose" in js

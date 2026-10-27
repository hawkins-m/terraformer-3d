"""Roll a city (city_roll.py, Layout tab's dice, ap site roll): random city rules within believable
ranges plus a seed, locks, the last 10 rolls, the housing-share retry. Synthetic projects; the plan
itself is stubbed except in one end-to-end roll."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import city_roll
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, read_json, write_json
from asset_pipeline.schema import SiteLayout
from asset_pipeline.stages import sw_site
from asset_pipeline.ui import app as ui_app

STATIC = Path(ui_app.STATIC)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create(None, name="Roll Test", start="city")
    write_json(sw_site.site_dir(s) / "layout.json", sw_site.starter_city().model_dump(mode="json"))
    return s


@pytest.fixture
def fast(monkeypatch):
    """No real plan or shots: plan_city returns these stats (a list: one per call, the last repeats)."""
    calls = {"stats": [{"buildings": 100, "warnings": []}], "seeds": []}

    def plan(store):
        calls["seeds"].append(sw_site.load_layout(store).city.seed)
        st = calls["stats"]
        return st.pop(0) if len(st) > 1 else st[0]
    monkeypatch.setattr(sw_site, "plan_city", plan)
    monkeypatch.setattr(sw_site, "set_city_shots", lambda store, only=None: [])
    return calls


def test_a_seed_rolls_the_same_rules_and_every_roll_is_valid():
    base = sw_site.starter_city()
    for seed in range(40):
        L, rules = city_roll.rolled(base, seed)
        assert L.city.seed == seed and rules == city_roll.random_rules(seed)
        SiteLayout.model_validate(L.model_dump(mode="json"))
        c, t = L.city, L.terrain
        hw, hh = c.bounds[2], c.bounds[3]
        assert 2000 <= hw <= 2800 and 1200 <= hh <= 1700 and 1150 <= t.shore_m <= 1750
        assert 3 <= len(c.nodes) <= 5 and {"centre", "harbour"} <= {n.id for n in c.nodes}
        for a in c.nodes:                                   # nodes apart, on land, inside the city
            assert abs(a.center[0]) <= hw and -t.shore_m < a.center[1] <= hh
            for b in c.nodes:
                if a is not b:
                    assert ((a.center[0] - b.center[0]) ** 2 + (a.center[1] - b.center[1]) ** 2) ** 0.5 > a.radius + b.radius
        assert 1 <= c.green_corridors <= 4 and 0.04 <= c.park_share <= 0.10 and 2 <= c.markets <= 5
        assert 0.10 <= c.urban_threshold <= 0.15 and c.storeys[0] <= c.storeys[1] and c.storeys[2] <= c.storeys[3]
    assert city_roll.random_rules(1) != city_roll.random_rules(2)


def test_kept_groups_never_change_and_the_rest_still_follow_the_seed():
    base = sw_site.starter_city()
    now = city_roll.rules_of(base)
    L, rules = city_roll.rolled(base, 5, keep=["size", "waterfront", "civic"])
    after = city_roll.rules_of(L)
    for g in ("size", "waterfront", "civic"):
        assert after[g] == now[g], g
    free = city_roll.random_rules(5)
    assert after["green"] == free["green"] and after["density"] == free["density"]
    assert rules["green"] == free["green"]                   # locks don't shift the other groups' draws
    with pytest.raises(ValueError, match="unknown rule group"):
        city_roll.rolled(base, 5, keep=["nope"])


def test_roll_saves_plans_and_remembers_the_last_ten(store, fast):
    r = city_roll.roll(store, seed=123, log=lambda *a: None)
    assert r["seed"] == 123 and fast["seeds"] == [123] and sw_site.load_layout(store).city.seed == 123
    for s in range(1, 13):
        city_roll.roll(store, seed=s, keep=["size"], log=lambda *a: None)
    h = city_roll.history(store)
    assert len(h["rolls"]) == 10 and h["pos"] == 9 and [x["seed"] for x in h["rolls"]] == list(range(3, 13))
    assert h["rolls"][-1]["keep"] == ["size"]
    st = city_roll.summary(store)
    assert st["seed"] == 12 and st["count"] == 10 and st["pos"] == 9 and set(st["groups"]) == set(city_roll.GROUPS)
    # step back and forth: the rules and seed come back, re-planned
    back = city_roll.step(store, -1, log=lambda *a: None)
    assert back["seed"] == 11 and back["pos"] == 8 and sw_site.load_layout(store).city.seed == 11
    assert city_roll.rules_of(sw_site.load_layout(store)) == city_roll.rules_of(
        city_roll.apply_rules(sw_site.starter_city(), h["rolls"][8]["rules"], 11))
    assert city_roll.step(store, 1, log=lambda *a: None)["seed"] == 12
    with pytest.raises(ValueError, match="no next roll"):
        city_roll.step(store, 1)


def test_a_random_roll_failing_the_housing_check_rolls_again_a_typed_seed_stays(store, fast):
    bad = {"buildings": 50, "warnings": ["housing is 41% of the built footprint (want >= 60%)"]}
    fast["stats"] = [bad, bad, {"buildings": 90, "warnings": []}]
    r = city_roll.roll(store, log=lambda *a: None)
    assert r["tries"] == 3 and len(set(fast["seeds"])) == 3 and r["seed"] == fast["seeds"][-1]
    fast["stats"], fast["seeds"] = [bad], []
    r = city_roll.roll(store, seed=77, log=lambda *a: None)
    assert r["tries"] == 1 and r["seed"] == 77 and r["stats"]["warnings"]


def test_a_new_project_without_a_layout_starts_from_the_starter_city_own_layouts_cant_roll(tmp_path, monkeypatch, fast):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("fresh")
    assert city_roll.roll(s, seed=3, log=lambda *a: None)["seed"] == 3
    assert sw_site.load_layout(s).city is not None and s.load().mode == "world"
    own = ProjectStore.create("own")
    sw_site.init(own)                                         # the starter layout: no city rules
    with pytest.raises(ValueError, match="needs a city layout"):
        city_roll.roll(own, seed=1)


def test_a_real_roll_plans_a_believable_city(store):
    r = city_roll.roll(store, seed=2024, log=lambda *a: None, shots=False)
    st = r["stats"]
    assert st["buildings"] > 1000 and st["footprint_share"]["housing"] >= 0.6 and not st["warnings"]
    assert (sw_site.site_dir(store) / "city_plan.png").is_file()


def test_api_and_cli(store, fast, capsys):
    c = TestClient(ui_app.create_app(JobQueue()))
    assert c.get("/api/projects/roll-test/site/roll").json()["count"] == 0
    assert c.post("/api/projects/roll-test/site/roll", json={"keep": ["bogus"]}).status_code == 400
    assert c.post("/api/projects/roll-test/site/roll/step", json={"delta": -1}).status_code == 400
    j = c.post("/api/projects/roll-test/site/roll", json={"seed": 9, "keep": ["green"]}).json()
    assert j["kind"] == "site.plan" and j["tag"]["roll"]
    import time
    for _ in range(100):
        if c.get(f"/api/jobs/{j['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert c.get(f"/api/jobs/{j['id']}").json()["result"]["seed"] == 9
    st = c.get("/api/projects/roll-test/site/roll").json()
    assert st["seed"] == 9 and st["count"] == 1 and st["last"]["keep"] == ["green"]
    from asset_pipeline import cli
    cli.main(["site", "roll", "roll-test", "--seed", "31", "--keep", "size,waterfront"])
    out = capsys.readouterr().out
    assert "seed 31" in out and "ap site roll roll-test --seed 31 --keep size,waterfront" in out
    cli.main(["site", "roll", "roll-test", "--step", "previous"])
    assert "seed 9" in capsys.readouterr().out


def test_the_dice_sits_after_the_catalog_and_asks_with_cancel_the_default():
    html = (STATIC / "index.html").read_text()
    tab = re.search(r'<section class="tabpanel" data-tab="city".*?</section>', html, re.S).group(0)
    assert tab.index('id="catalog"') < tab.index('id="city-roll"') and 'id="city-roll-btn"' in tab
    actions = re.search(r'<div class="row stage-actions" id="city-actions">.*?</div>', tab, re.S).group(0)
    assert "city-roll" not in actions                         # not beside Update / Re-plan
    assert 'class="dice"' in tab and "Roll a city" in tab and 'id="city-roll-seed"' in tab
    js = (STATIC / "city.js").read_text()
    assert ('"WARNING: This /roll command will give you a random assortment of buildings to start your city. '
            'If you click continue, all progress done already will be erased."') in js
    assert '{label: "Continue", onClick: () => { ok = true; }}, {label: "Cancel", default: true}' in js
    assert 'siteAction("site/roll"' in js and 'siteAction("site/roll/step"' in js and "data-keep" in js
    assert 'p.start === "city"' in js                        # the new-project hint

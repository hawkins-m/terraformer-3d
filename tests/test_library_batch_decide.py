"""Library › Asset review batch decisions: every card the Show filter shows (cards with a frame crop
left alone unless asked), or the ticked cards; Reference sheets' Generate missing follows."""
import shutil

import pytest
from fastapi.testclient import TestClient

from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_library, sw_site
from asset_pipeline.ui import app as ui_app

from test_ui_layout import BROWSER_PY, _browse, _entry


def _seed(store: ProjectStore):
    """Two entries with a crop, three without, one vegetation, all undecided; one more decided
    (description only) and planned, with no sheets yet: Generate missing (1)."""
    crop = [_entry(i, "harbour") | {"best": f"shots/med-harbour/frame_{i}.png", "share": 0.02} for i in ("crop-a", "crop-b")]
    bare = [_entry(i, "harbour") for i in ("bare-a", "bare-b", "bare-c", "planned")]
    veg = [_entry("tree-a", "harbour") | {"kind": "vegetation"}]
    write_json(sw_site.site_dir(store) / "library.json", {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [],
                                                          "checked": True, "entries": crop, "unseen": bare + veg, "surfaces": {}})
    sw_library.decisions_path(store).unlink(missing_ok=True)
    shutil.rmtree(store.root / "refs/library", ignore_errors=True)
    sw_library.decide(store, "planned", "description")
    sw_library.asset_plan(store, only=["planned"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    _seed(store)
    return TestClient(ui_app.create_app(JobQueue())), store


def _missing(c):
    return sorted(u["key"] for u in c.get("/api/projects/p/refs").json() if not u["sheets"])


def test_batch_decision_updates_the_plan_and_skips_vegetation_and_kits(client):
    c, store = client
    assert _missing(c) == ["planned"]
    sw_library.decide(store, "bare-c", "kit", pieces={"bay": {"description": "a bay", "size": [3, 3, 4]}})
    r = c.post("/api/projects/p/library/decisions", json={"entries": ["bare-a", "bare-b", "bare-c", "tree-a"],
                                                          "decision": "description"}).json()
    assert r["done"] == ["bare-a", "bare-b"]
    assert {s["entry"] for s in r["skipped"]} == {"bare-c", "tree-a"}
    assert r["decisions"]["bare-c"]["decision"] == "kit" and "tree-a" not in r["decisions"]
    assert _missing(c) == ["bare-a", "bare-b", "planned"]                 # the new ones are sheet units now
    r = c.post("/api/projects/p/library/decisions", json={"entries": ["bare-a", "planned"], "decision": "exclude"}).json()
    assert r["done"] == ["bare-a", "planned"] and _missing(c) == ["bare-b"]
    r = c.post("/api/projects/p/library/decisions", json={"entries": ["bare-b"], "decision": None}).json()
    assert "bare-b" not in r["decisions"] and _missing(c) == []           # the last one out: the plan keeps none


def test_batch_frame_crop_needs_a_crop_and_unknown_entries_404(client):
    c, _ = client
    r = c.post("/api/projects/p/library/decisions", json={"entries": ["crop-a", "bare-a"], "decision": "source"}).json()
    assert r["done"] == ["crop-a"] and r["skipped"] == [{"entry": "bare-a", "why": "no crop to use"}]
    assert "crop-a" in _missing(c)
    assert c.post("/api/projects/p/library/decisions", json={"entries": ["nope"], "decision": "exclude"}).status_code == 404


SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_selector("#assets-list .asset-card", state="attached", timeout=20000)
    pg.click("button[data-tab=library]"); pg.click("button[data-sub=assets]")
    pg.locator("#lib-filter").select_option("todo"); pg.wait_for_timeout(300)
    out["missing0"] = pg.inner_text("#refs-missing")
    out["desc"] = pg.inner_text("#lib-batch-description"); out["excl"] = pg.inner_text("#lib-batch-exclude")
    pg.check("#lib-batch-crops"); out["desc_crops"] = pg.inner_text("#lib-batch-description")
    pg.uncheck("#lib-batch-crops")
    # Description only for all shown: the dialog lists the assets, cards with a crop are left alone
    pg.click("#lib-batch-description")
    pg.wait_for_selector("#lib-batch-confirm")
    out["listed"] = pg.locator("#lib-batch-confirm .lib-batch-list li").all_inner_texts()
    out["note"] = pg.inner_text("#lib-batch-confirm .hint")
    pg.click("#lib-batch-confirm [data-role=confirm]")
    pg.wait_for_function("document.querySelector('#refs-missing').textContent.includes('(4)')", timeout=10000)
    out["missing1"] = pg.inner_text("#refs-missing")
    out["shown_after"] = sorted(pg.eval_on_selector_all("#assets-list .asset-card", "els => els.map(e => e.dataset.entry)"))
    # tick two cards and exclude just those
    for e in ("crop-a", "crop-b"):
        pg.check(f'#assets-list [data-tick="{e}"]')
    out["ticked"] = pg.inner_text("#lib-ticked-n")
    pg.click('#lib-ticked [data-tick-decision="exclude"]')
    pg.wait_for_selector("#lib-batch-confirm")
    out["listed2"] = len(pg.locator("#lib-batch-confirm .lib-batch-list li").all_inner_texts())
    pg.click("#lib-batch-confirm [data-role=confirm]"); pg.wait_for_timeout(1500)
    out["decisions"] = pg.evaluate("Object.fromEntries(Object.entries(libraryData.decisions).map(([k, v]) => [k, v.decision]))")
    out["ticked_after"] = pg.evaluate("lib.ticked.size")
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_batch_decide_in_the_browser(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, SCRIPT, seed=_seed)
    assert out["missing0"] == "Generate missing (1)"
    assert out["desc"] == "Description only for all shown (3)…" and out["excl"] == "Exclude all shown (3)…"
    assert out["desc_crops"] == "Description only for all shown (5)…"           # tree-a (vegetation) never counts
    assert sorted(out["listed"]) == ["bare a", "bare b", "bare c"] and "2 with a frame crop" in out["note"]
    assert out["missing1"] == "Generate missing (4)"                             # Reference sheets follow
    assert out["shown_after"] == ["crop-a", "crop-b", "tree-a"]
    assert out["ticked"] == "2 ticked" and out["listed2"] == 2
    d = out["decisions"]
    assert d["crop-a"] == d["crop-b"] == "exclude" and d["bare-a"] == "description" and "tree-a" not in d
    assert out["ticked_after"] == 2 and out["errors"] == []

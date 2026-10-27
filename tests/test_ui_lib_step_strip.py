"""The Library step strip does the work: each step goes where it happens with the list filtered to
what waits there, and 2 Sheets / 4 Build offer their batches over the whole library."""
import shutil

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import review
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore
from asset_pipeline.stages import sw_library
from asset_pipeline.ui import app as ui_app

from test_ui_layout import BROWSER_PY, _browse, _project
from test_ui_versions_refresh import _trellis


def _seed(store: ProjectStore):
    """On top of test_ui_layout's 48 decided props (harbour-area holds the harbour ones), each with
    an unstarred sheet: one undecided, one harbour and one academy asset without sheets, one
    starred, one built."""
    sw_library.decide(store, "harbour-prop-00", None)
    for a in ("harbour-prop-01", "academy-prop-00"):
        shutil.rmtree(store.root / "refs/library" / a)
    review.set_star(store, "refs/library/harbour-prop-03/sheet_000.png", True)
    _trellis(store, "harbour-prop-02", 1)


def test_area_batches_can_run_over_the_whole_library(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    store = ProjectStore.create("p")
    _project(store, 4)
    _seed(store)
    c = TestClient(ui_app.create_app(JobQueue()))
    url = "/api/projects/p/areas/batch"
    area = c.post(url, json={"action": "sheets", "area": "harbour-area", "dry_run": True}).json()
    every = c.post(url, json={"action": "sheets", "everywhere": True, "dry_run": True}).json()
    assert area["items"] == ["harbour-prop-01"]
    assert every["items"] == ["academy-prop-00", "harbour-prop-01"]          # across all areas
    assert c.post(url, json={"action": "build", "everywhere": True, "area": "harbour-area", "dry_run": True}).status_code == 400
    assert c.post(url, json={"action": "trellis", "everywhere": True, "dry_run": True}).json()["count"] == 0   # no view chosen


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
    pg.goto(url); pg.wait_for_selector("#lib-steps .ls-step", state="attached", timeout=20000)
    out["strip"] = pg.eval_on_selector_all("#lib-steps .ls-step", "els => els.map(e => e.getAttribute('aria-label'))")
    vis = "sel => [...document.querySelectorAll(sel)].filter(e => e.offsetParent).length"
    def step(s, wait=600):
        pg.locator(f'.lib-steps:visible [data-ls="{s}"]').click(); pg.wait_for_timeout(wait)
    def where():
        return {"tab": pg.evaluate("currentTab"), "sub": pg.evaluate("localStorage.getItem('ap.sub')")}
    pg.click("button[data-tab=views]")             # from the other tab's strip: it goes to Library
    step("decide")
    out["decide"] = where() | {"filter": pg.input_value("#lib-filter"), "cards": pg.evaluate(vis, "#assets-list .asset-card")}
    step("sheets", 1500)
    out["sheets"] = where() | {"filter": pg.input_value("#refs-filter"), "units": pg.evaluate(vis, "#refs-list .unit"),
                               "offer": pg.locator(".lib-offer:visible [data-offer]").all_inner_texts()}
    pg.locator('.lib-offer:visible [data-offer="sheets"]').click()
    pg.wait_for_selector("#lib-offer-confirm")
    out["confirm"] = pg.inner_text("#lib-offer-confirm .wbody")
    pg.click("#lib-offer-confirm .wbuttons button:has-text('Cancel')")
    step("star")
    out["star"] = where() | {"filter": pg.input_value("#refs-filter"), "units": pg.evaluate(vis, "#refs-list .unit"),
                             "offer": pg.locator(".lib-offer:visible").count()}
    step("build", 1500)
    out["build"] = where() | {"offer": pg.locator(".lib-offer:visible [data-offer]").all_inner_texts(),
                              "need_view": pg.locator(".lib-offer:visible [data-offer-views]").inner_text(),
                              "strip_here": pg.evaluate(vis, "#views-steps .ls-step")}
    pg.locator(".lib-offer:visible [data-offer-views]").click(); pg.wait_for_timeout(500)
    out["view"] = {"filter": pg.input_value("#views-filter"), "rows": pg.evaluate(vis, "#review-list .asset-review")}
    step("approve")
    out["approve"] = where() | {"filter": pg.input_value("#views-filter"),
                                "rows": pg.eval_on_selector_all("#review-list .asset-review", "els => els.filter(e => e.offsetParent).map(e => e.dataset.asset)"),
                                "offer": pg.locator(".lib-offer:visible").count()}
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_following_the_strip_left_to_right(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, SCRIPT, seed=_seed)
    assert out["strip"] == ["1 Decide: 1 waiting", "2 Sheets: 2 waiting", "3 Choose Reference: 43 waiting", "4 Build: 1 waiting", "5 Approve: 1 waiting"]
    assert out["decide"] == {"tab": "library", "sub": "assets", "filter": "todo", "cards": 1}
    assert out["sheets"]["tab"] == "library" and out["sheets"]["sub"] == "refs" and out["sheets"]["filter"] == "missing"
    assert out["sheets"]["units"] == 2 and out["sheets"]["offer"] == ["Make sheets for all (2) across all areas…"]
    assert "academy prop 00" in out["confirm"] and "harbour prop 01" in out["confirm"]
    assert out["star"]["filter"] == "star" and out["star"]["units"] == 43 and out["star"]["offer"] == 0
    assert out["build"]["tab"] == "views" and out["build"]["strip_here"] == 5
    assert out["build"]["offer"] == ["Build all procedural (0)…", "Make 3D for TRELLIS with a chosen view (0)…"]
    assert out["build"]["need_view"] == "1 need a view picked"                 # the starred TRELLIS asset
    assert out["view"] == {"filter": "view", "rows": 1}
    assert out["approve"]["filter"] == "approve" and out["approve"]["rows"] == ["harbour-prop-02"] and out["approve"]["offer"] == 0
    assert out["errors"] == []

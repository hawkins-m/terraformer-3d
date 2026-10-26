"""Known, fixable job errors: a plain message plus a one-click button to the option that fixes it
(jobs.Fixable). Brief & build's "no near-front view" says "No view is straight-on enough to measure
automatically." in the job dialog and the Jobs panel, with "Try another view to measure", which
reopens Brief & build with View to measure on the best-scoring view and each view's score shown.
The raw error goes to the log only."""
import time

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import library_entries as le, library_review as lr
from asset_pipeline.jobs import Fixable, JobQueue
from asset_pipeline.stages import sw_library
from asset_pipeline.ui import app as ui_app

from test_library_entries import _populate, proj  # noqa: F401
from test_ui_layout import BROWSER_PY, _browse

PLAIN = "No view is straight-on enough to measure automatically."
SHEET = "refs/library/hall-a/sheet_000.png"


def _wait(c, job):
    for _ in range(300):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def test_fixable_job_error_keeps_the_raw_text_in_the_log(capfd):
    q = JobQueue()
    j = q.submit("x", "p", lambda: (_ for _ in ()).throw(Fixable("Plain words.", {"action": "a", "label": "Do it"},
                                                                  detail="raw: view 0 0.12")), lane="cpu")
    for _ in range(200):
        if j.status == "error":
            break
        time.sleep(0.01)
    assert j.public()["error"] == "Plain words." and j.public()["fix"] == {"action": "a", "label": "Do it"}
    assert "raw: view 0 0.12" in capfd.readouterr().err


@pytest.mark.parametrize("path", ["brief_build", "reroll", "guide"])
def test_no_near_front_view_is_a_plain_message_with_the_best_view(proj, monkeypatch, capfd, path):  # noqa: F811
    _populate(proj, "hall-a")
    sw_library.set_route(proj, "hall-a", "procedural")
    raw = f"no near-front view on {SHEET}: view 0 0.21, view 1 0.32, view 2 0.18 (pick one: 0 / 1 / 2 / all)"

    def fail(*a, **k):
        raise le.NoFrontView(raw, [(0, 0.21), (1, 0.32), (2, 0.18)])
    monkeypatch.setattr(le, "brief_from_sheet", fail)
    monkeypatch.setattr(lr, "rebuild", lambda *a, **k: {})
    if path == "reroll":                         # a brief with no measured view: Auto measures again
        monkeypatch.setattr(lr, "reroll", lambda store, asset, *a, **k: le.brief_from_sheet(store, asset, SHEET, "front"))
    if path == "guide":
        from asset_pipeline import brief_guide
        monkeypatch.setattr(brief_guide, "propose", lambda store, asset, sheet, g, view: le.brief_from_sheet(store, asset, sheet, view))
    c = TestClient(ui_app.create_app(JobQueue()))
    body = {"brief_build": {"sheet": SHEET, "rebrief": True}, "reroll": {"guidance": ""},
            "guide": {"sheet": SHEET, "guidance": "taller windows"}}[path]
    j = _wait(c, c.post(f"/api/projects/p/library/asset/hall-a/{path}", json=body).json())
    assert j["status"] == "error" and j["error"] == PLAIN
    assert j["fix"] == {"action": "brief_build", "label": "Try another view to measure", "asset": "hall-a",
                        "sheet": SHEET, "view": "1", "scores": {"0": 0.21, "1": 0.32, "2": 0.18}}
    assert raw in capfd.readouterr().err         # the raw error: the log only


def test_form_needed_offers_the_form_fields(proj, monkeypatch):  # noqa: F811
    _populate(proj, "hall-a")
    sw_library.set_route(proj, "hall-a", "procedural")
    monkeypatch.setattr(le, "brief_from_sheet", lambda *a, **k: (_ for _ in ()).throw(le.FormNeeded("hall-a has no building form: choose one.")))
    c = TestClient(ui_app.create_app(JobQueue()))
    j = _wait(c, c.post("/api/projects/p/library/asset/hall-a/brief_build", json={"sheet": SHEET, "rebrief": True}).json())
    assert j["error"] == "hall-a has no building form: choose one."
    assert j["fix"]["label"] == "Choose the form and size" and j["fix"]["focus"] == "form"


def test_the_auto_view_error_carries_the_scores(proj, monkeypatch):  # noqa: F811
    from asset_pipeline import building_proc
    monkeypatch.setattr(building_proc, "front_view", lambda store, sheets, e: {
        "best": None, "scores": [(SHEET, 0, 0.2), (SHEET, 1, 0.4), (SHEET, 2, 0.1)]})
    with pytest.raises(le.NoFrontView) as e:
        le.brief_from_sheet(proj, "hall-a", SHEET, view="front", form="cube", dims=[10, 8, 6])
    assert e.value.scores == [(0, 0.2), (1, 0.4), (2, 0.1)] and "pick one: 0 / 1 / 2 / all" in str(e.value)


BROWSER_SCRIPT = r'''
import json, sys, time
from playwright.sync_api import sync_playwright
url = sys.argv[1]
SHEET = "refs/library/hall-x/sheet_000.png"
FIX = {"action": "brief_build", "label": "Try another view to measure", "asset": "hall-x", "sheet": SHEET,
       "view": "1", "scores": {"0": 0.21, "1": 0.32, "2": 0.18}}
JOB = {"id": 9002, "kind": "library.brief_build", "project": "p", "lane": "gpu0", "status": "error",
       "tag": {"asset": "hall-x", "sheet": SHEET}, "created": time.time() - 5, "started": time.time() - 5,
       "finished": time.time(), "error": "No view is straight-on enough to measure automatically.", "fix": FIX,
       "result": None, "note": ""}
UNIT = {"key": "hall-x", "plan": "library", "title": "Hall X", "kind": "object", "guidance": "",
        "assets": [{"name": "Hall X", "count": 1, "dimensions": [10, 8, 6]}], "sheets": [SHEET],
        "next": {"route": "procedural", "has_brief": False, "has_build": False, "guidance": "",
                 "form": {"form": "cube", "suggested": "cube", "size_m": [10, 8, 6]}}}

def fulfil(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
def project(route):
    d = route.fetch().json(); d["jobs"] = d.get("jobs", []) + [dict(JOB)]; d.setdefault("stars", {})[SHEET] = True; fulfil(route, d)
def refs(route):
    fulfil(route, route.fetch().json() + [UNIT])
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/api/projects/p", project)
    pg.route("**/api/projects/p/refs", refs)
    pg.route("**/api/jobs/9002", lambda r: fulfil(r, dict(JOB)))
    pg.goto(url); pg.wait_for_selector("#jobs-msg .job-fix", timeout=20000)
    out = {"panel": pg.locator("#jobs-msg").inner_text()}
    pg.evaluate("openJobDialog(9002)"); pg.wait_for_selector(".loader-window .ld-fix .job-fix", timeout=5000)
    out["dialog"] = [pg.locator(".loader-window .ld-line2").inner_text(), pg.locator(".loader-window .ld-fix").inner_text()]
    pg.click(".loader-window .ld-fix .job-fix")
    pg.wait_for_selector("#bb-view", timeout=5000)
    out["loader_open"] = pg.locator(".loader-window").count()
    out["view"] = pg.locator("#bb-view").input_value()
    out["options"] = pg.locator("#bb-view option").all_inner_texts()
    out["focused"] = pg.evaluate("document.activeElement.id")
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_try_another_view_button_in_a_browser(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, BROWSER_SCRIPT)
    assert PLAIN in out["panel"] and "Try another view to measure" in out["panel"] and "near-front" not in out["panel"]
    assert out["dialog"] == [f"Failed: {PLAIN}", "Try another view to measure"]
    assert out["loader_open"] == 0                                    # the job window closes, Brief & build opens
    assert out["view"] == "1" and out["focused"] == "bb-view"
    assert out["options"] == ["Auto (the most frontal view)", "Left view (0.21)", "Middle view (0.32, best)",
                              "Right view (0.18)", "The whole sheet"]
    assert out["errors"] == []

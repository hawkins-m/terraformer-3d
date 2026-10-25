"""A job sent to the background keeps its progress in the Jobs panel across tab switches, and the
page survives a slow server, failed polls and a redraw that throws (no page errors).

The bug: with a long Library › Areas › Instances job running, Run in background then a tab switch
took the page down. The poll asked for a full load every 2 s whether or not the last one had come
back, so on a busy server loads piled up; a failed poll was an uncaught error, and a redraw that
threw stopped the Jobs panel (renderShell ran last)."""
import pytest

from test_ui_layout import BROWSER_PY, _browse

SCRIPT = r'''
import json, sys, time
from playwright.sync_api import sync_playwright
url = sys.argv[1]
T = {"bg": None}
JOB = {"id": 9001, "kind": "library.instances", "project": "p", "lane": "cpu", "status": "running",
       "tag": {"area": "harbour-area", "n": 3}, "created": time.time(), "finished": None, "error": None, "result": None, "note": ""}
FAIL = [0]

def fulfil(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
def project(route):                     # the stubbed long job is listed in the project's jobs
    if FAIL[0] > 0:
        FAIL[0] -= 1
        return route.fulfill(status=500, body="stub failure")
    d = route.fetch().json(); d["jobs"] = d.get("jobs", []) + [dict(JOB)]; fulfil(route, d)
def status(route):                      # one more instance done every 2 s after Run in background
    d = route.fetch().json()
    done = 0 if T["bg"] is None else min(3, int((time.time() - T["bg"]) // 2))
    d.setdefault("progress", {})[str(JOB["id"])] = {"done": done, "total": 3, "line": "instances"}; fulfil(route, d)
def batch(route):
    if json.loads(route.request.post_data or "{}").get("dry_run"):
        return fulfil(route, {"action": "instances", "count": 3, "items": ["a", "b", "c"], "notes": [], "estimate": None})
    fulfil(route, dict(JOB))
# a slow server: each project load takes `__slow` ms (longer than the 2 s poll); counts loads in flight
SLOW = """(() => { const f = window.fetch; window.__inflight = 0; window.__max = 0; window.__slow = 0;
  window.fetch = async (u, o) => { const p = /\\/api\\/projects\\/p$/.test(String(u));
    if (p) { window.__inflight++; window.__max = Math.max(window.__max, window.__inflight); }
    try { if (p && window.__slow) await new Promise(r => setTimeout(r, window.__slow)); return await f(u, o); }
    finally { if (p) window.__inflight--; } }; })()"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs, cons = [], []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("crash", lambda: errs.append("page crashed"))
    pg.on("console", lambda m: cons.append(m.text) if m.type == "error" else None)
    pg.add_init_script(SLOW)
    pg.route("**/api/projects/p", project)
    pg.route("**/api/projects/p/status", status)
    pg.route("**/api/projects/p/areas/batch", batch)
    pg.route("**/api/jobs/9001", lambda r: fulfil(r, dict(JOB)))
    pg.goto(url); pg.wait_for_selector("#lanes .lane", state="attached", timeout=20000)
    # Library › Areas › Instances…, as the user starts it
    pg.click("button[data-tab=library]"); pg.click("button[data-sub=areas]")
    pg.wait_for_selector("#area-batch-instances", timeout=10000); pg.wait_for_timeout(500)
    pg.evaluate("document.querySelector('#area-batch-instances').disabled = false")
    pg.click("#area-batch-instances"); pg.click("#area-batch-confirm [data-role=confirm]")
    pg.wait_for_selector(".loader-window [data-role=background]", timeout=10000)
    pg.click(".loader-window [data-role=background]")
    T["bg"] = time.time()
    pg.evaluate("window.__max = 0; window.__slow = 2500")
    FAIL[0] = 2                                                     # two polls fail
    pg.evaluate("() => { window.renderUnreal = function renderUnreal() { throw new Error('stub redraw bug'); }; }")   # a redraw that throws
    cpu = []
    for t in ["style", "frames", "library", "views", "city", "site", "shots", "unreal"] * 2:
        btn = pg.locator(f"button[data-tab={t}]")
        if btn.is_visible(): btn.click()
        pg.wait_for_timeout(700)
        cpu.append(pg.locator('#lanes .lane[data-lane="cpu"]').inner_text())
    out = {"errors": errs, "cpu": cpu, "max_loads": pg.evaluate("window.__max"),
           "dialogs": pg.locator("#dialogs .modal-back").count(),
           "logged": [c for c in cons if c.startswith("Terraformer:")],
           "console": [c for c in cons if not c.startswith("Terraformer:") and not c.startswith("Failed to load resource")],
           "help": pg.evaluate("MENUS.find(m => m[0] === 'Help')[2].map(i => i[0])")}
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_tab_switches_during_a_background_job(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, SCRIPT)
    assert out["errors"] == [] and out["console"] == [], out
    assert out["dialogs"] == 0                                       # Run in background stays closed
    assert out["max_loads"] == 1, out["max_loads"]                    # polls never pile up on a slow server
    shown = [next((n for n in range(4) if f"{n} / 3" in c), None) for c in out["cpu"]]
    assert all(c.startswith("CPU:") and "Area instances" in c for c in out["cpu"]), out["cpu"]
    assert shown[-1] == 3 and len(set(shown) - {None}) >= 2, out["cpu"]   # progress kept moving across tabs
    assert any("refresh failed" in c for c in out["logged"])          # failed polls: logged, then kept going
    assert any("renderUnreal failed" in c for c in out["logged"])     # a broken redraw: logged, panel still drawn
    assert "Closing the browser doesn't stop jobs; closing the Terraformer window does." in out["help"]

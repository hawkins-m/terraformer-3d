"""Stop jobs: cancels every job, stops and unloads what the app started (the vision model server,
TRELLIS, headless Unreal) and says what VRAM that freed; ComfyUI is left running. Synthetic
processes (sleep) stand in for the servers; the VRAM reading is stubbed."""
import json
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import blender, gpu, procs
from asset_pipeline.comfy import service as comfy_service
from asset_pipeline.jobs import JobQueue, check_canceled
from asset_pipeline.ui import app as ui_app


def _group(cmd="sleep 60 & sleep 60; wait"):
    """A process group like TRELLIS's (a wrapper and its child), in its own session."""
    p = subprocess.Popen(["sh", "-c", cmd], start_new_session=True)
    for _ in range(50):
        if len(procs.group_pids(p.pid)) >= 2:
            break
        time.sleep(0.05)
    return p


def _gone(p, timeout=15):
    p.wait(timeout=timeout)
    for _ in range(100):
        if not procs.group_pids(p.pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    (tmp_path / "logs").mkdir()
    yield tmp_path
    procs._LIVE.clear()


def _fake_vlm(root):
    p = _group("sleep 60")
    (root / "logs" / "vlm.json").write_text(json.dumps({"model": "32b", "backend": "llamacpp", "url": "http://127.0.0.1:9",
                                                        "pid": p.pid, "gpu": 0}))
    return p


def test_stop_ends_the_vision_model_and_trellis_and_says_what_it_freed(root):
    v, t, u = _fake_vlm(root), _group(), _group("sleep 60")
    procs.track(t.pid, "TRELLIS", 0)
    procs.track(u.pid, "headless Unreal (UE import)")
    child = max(procs.group_pids(t.pid))
    assert [x["what"] for x in procs.loaded()] == ["vision model", "TRELLIS", "headless Unreal (UE import)"]
    snap = procs.snapshot(lambda: {0: {v.pid: 27.04, child: 9.5}, 1: {4242: 3.0}})     # GPU 1: someone else's
    r = procs.stop(snap, log=lambda *a: None)
    assert r["message"] == "Freed 37 GB on GPU 0: vision model, TRELLIS. Stopped headless Unreal (UE import)."
    assert r["freed"] == [{"gpu": 0, "gb": 36.5, "what": ["vision model", "TRELLIS"]}]
    assert all(_gone(p) for p in (v, t, u))
    assert not (root / "logs" / "vlm.json").exists() and procs.loaded() == []
    assert procs.stop(procs.snapshot(lambda: {}))["message"] == "No model server or headless Unreal was running."


def test_run_logged_tracks_headless_unreal_while_it_runs(root):
    seen = []
    th = threading.Thread(target=lambda: blender.run_logged(["sh", "-c", "sleep 0.6"], root / "x.log", "UE import",
                                                            track="headless Unreal"))
    th.start()
    for _ in range(40):
        seen = [x["what"] for x in procs.loaded()]
        if seen:
            break
        time.sleep(0.02)
    th.join()
    assert seen == ["headless Unreal (UE import)"] and procs.loaded() == []


def test_stop_all_api_cancels_jobs_frees_vram_and_leaves_comfyui(root, monkeypatch):
    v, t = _fake_vlm(root), _group()
    forget = procs.track(t.pid, "TRELLIS", 0)
    monkeypatch.setattr(gpu, "vram_by_process", lambda: {0: {v.pid: 27.0}})
    monkeypatch.setattr(comfy_service, "stop", lambda *a, **k: pytest.fail("ComfyUI must keep running"))
    q = JobQueue()
    c = TestClient(ui_app.create_app(q))

    def work():                          # a TRELLIS-like job: its cancel kills the process group
        from asset_pipeline.jobs import current_job
        current_job().on_cancel(lambda: procs._kill_group(t.pid))
        while True:
            check_canceled()
            time.sleep(0.05)
    j = q.submit("3d.trellis", "p", work, lane="gpu0")
    for _ in range(100):
        if j.status == "running":
            break
        time.sleep(0.02)
    r = c.post("/api/jobs/stop_all", json={}).json()
    assert r["canceled"] == [j.id] and r["message"] == "Freed 27 GB on GPU 0: vision model. Stopped TRELLIS."
    assert _gone(v) and _gone(t) and not (root / "logs" / "vlm.json").exists()
    forget()
    for _ in range(100):
        if j.status == "canceled":
            break
        time.sleep(0.02)
    assert j.status == "canceled"


BROWSER_SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
CALLS = []
def fulfil(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
def status(route):                      # no job running, the vision model loaded
    d = route.fetch().json(); d["loaded"] = [] if CALLS else ["vision model"]; fulfil(route, d)
def stop_all(route):
    CALLS.append(route.request.method)
    fulfil(route, {"freed": [{"gpu": 0, "gb": 27.0, "what": ["vision model"]}], "stopped": ["vision model"],
                   "canceled": [], "message": "Freed 27 GB on GPU 0: vision model."})
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/api/projects/p/status", status)
    pg.route("**/api/jobs/stop_all", stop_all)
    pg.goto(url); pg.wait_for_selector("#lanes .lane", state="attached", timeout=20000); pg.wait_for_timeout(500)
    out = {"before": pg.locator("#tb-stop").is_disabled()}         # enabled: a model is loaded, no job runs
    pg.click("#tb-stop"); pg.wait_for_selector(".app-notice", timeout=5000)
    out |= {"notice": pg.locator(".app-notice .why").inner_text(), "calls": CALLS}
    pg.wait_for_selector("#tb-stop[disabled]", timeout=5000)
    out["after"] = [pg.locator("#tb-stop").inner_text(), pg.locator("#tb-stop").is_disabled()]
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


from test_ui_layout import BROWSER_PY, _browse  # noqa: E402


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_stop_jobs_button_with_only_a_model_loaded_in_a_browser(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, BROWSER_SCRIPT)
    assert out["before"] is False and out["calls"] == ["POST"] and out["notice"] == "Freed 27 GB on GPU 0: vision model."
    assert out["after"] == ["Stop jobs", True] and out["errors"] == []

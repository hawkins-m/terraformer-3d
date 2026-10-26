"""The setup wizard's server and install runner (asset_pipeline/installer.py): what pages 3-6 read,
the install plan, progress from `ap models download --json` events (a fake download), resume, and
the server's guards. The page flow and sounds are in tests/js/wizard_core.test.js."""
import http.client
import json
import sys
import textwrap
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from asset_pipeline import cli, config, installer, models, setup


@pytest.fixture(autouse=True)
def _machine(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(setup, "detect", lambda: {"storage": str(tmp_path / "storage"), "hardware_profile": "rocm-2gpu",
                                                  "comfy_backend": "sdk", "comfy_url": "http://127.0.0.1:8188"})
    monkeypatch.setattr(installer.profiles, "detect", lambda: {"vendor": "amd", "count": 2, "gpus": [],
                                                               "profile": "rocm-2gpu", "wsl": False})
    monkeypatch.setattr(setup.gpu, "list_gpus", lambda: [])
    monkeypatch.setattr(installer, "app_python", lambda: sys.executable)
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    config.backends.cache_clear()


def fake_download(tmp_path, events: list[dict], sleep: float = 0.0):
    """A stand-in for `ap models download --json`: prints these events, one per line."""
    script = tmp_path / "fake_download.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys, time
        for ev in json.loads({json.dumps(json.dumps(events))}):
            print(json.dumps(ev), flush=True)
            time.sleep({sleep})
    """))
    return lambda ids: [sys.executable, str(script)]


def wait(inst: installer.Installer, status=("done", "failed", "paused"), timeout=20):
    t0 = time.time()
    while inst.progress()["status"] not in status:
        assert time.time() - t0 < timeout, inst.progress()
        time.sleep(0.02)
    return inst.progress()


GB = 10**9
EVENTS = [
    {"event": "plan", "models": 2, "files": 2, "bytes": 30 * GB, "resume_bytes": 0,
     "items": [{"model": "flux1-dev-fp8", "bytes": 20 * GB}, {"model": "sam3.1", "bytes": 10 * GB}]},
    {"event": "file_start", "model": "flux1-dev-fp8", "file": "checkpoints/f.safetensors", "size": 20 * GB, "resume_from": 0},
    {"event": "progress", "model": "flux1-dev-fp8", "file": "checkpoints/f.safetensors", "done": 12.4 * GB, "total": 20 * GB},
    {"event": "verify", "model": "flux1-dev-fp8", "file": "checkpoints/f.safetensors"},
    {"event": "file_done", "model": "flux1-dev-fp8", "file": "checkpoints/f.safetensors"},
    {"event": "model_done", "model": "flux1-dev-fp8"},
    {"event": "file_start", "model": "sam3.1", "file": "sam/s.pt", "size": 10 * GB, "resume_from": 4 * GB},
    {"event": "progress", "model": "sam3.1", "file": "sam/s.pt", "done": 10 * GB, "total": 10 * GB},
    {"event": "file_done", "model": "sam3.1", "file": "sam/s.pt"},
    {"event": "model_done", "model": "sam3.1"},
    {"event": "done", "installed": 2, "failed": 0},
]


def snapshots(monkeypatch) -> list[dict]:
    """The install state after every event (the state file itself gets progress once a second)."""
    seen = []
    real = installer.Installer.save
    monkeypatch.setattr(installer.Installer, "save",
                        lambda self, force=True: (seen.append(json.loads(json.dumps(self.ins))), real(self, force)))
    return seen


# --- what the pages read -----------------------------------------------------------------------------

def test_components_come_from_the_manifest_with_required_ones_and_dressing_by_default():
    comps = installer.components()
    ids = {c["id"] for c in comps}
    assert ids == {m.id for m in models.load()}
    assert {c["group"] for c in comps if c["feature"] == "dressing"} == {"World dressing (CC0 assets)"}
    default = installer.default_components(comps, "32b")
    assert "flux1-dev-fp8" in default and "vlm-32b" in default and "vlm-8b" not in default
    assert "cc0-grass004" in default and "trellis2-4b" not in default
    assert not [c for c in comps if c["need"] == "experimental" and c["id"] in default]
    assert "vlm-8b" in installer.default_components(comps, "8b")


def test_catalog_groups_setup_items_by_wizard_page_and_lists_the_profiles():
    cat = installer.catalog()
    by_page = {}
    for it in cat["items"]:
        by_page.setdefault(it["wizard"], []).append(it["id"])
    assert by_page["hardware"][0] == "hardware_profile" and "vlm_model" in by_page["hardware"]
    assert by_page["storage"] == ["storage"]
    assert {"comfy_backend", "comfy_api_key", "blender", "unreal"} <= set(by_page["connections"])
    prof = next(i for i in cat["items"] if i["id"] == "hardware_profile")
    assert set(prof["choices"]) == set(installer.profiles.PROFILES) and prof["notes"]["nvidia"]
    assert prof["tested"]["rocm-2gpu"] and not prof["tested"]["wsl-nvidia"]


def test_a_new_setting_shows_up_on_the_wizard_without_editing_it(monkeypatch):
    extra = setup.Item("new_thing", "New thing", "tools.blender", "A setting added later.")
    monkeypatch.setattr(setup, "ITEMS", [*setup.ITEMS, extra])
    monkeypatch.setitem(setup.BY_ID, "new_thing", extra)
    it = next(i for i in installer.catalog()["items"] if i["id"] == "new_thing")
    assert it["wizard"] == "advanced"              # the Connections page's Advanced list


def test_plan_runs_runtimes_whose_conditions_hold():
    sel = ["flux1-dev-fp8", "vlm-32b", "oneformer-ade20k", "trellis2-4b"]
    steps = installer.plan({"comfy_backend": "sdk", "vlm_model": "32b", "hardware_profile": "rocm-2gpu"}, sel)
    ids = [s["id"] for s in steps]
    assert ids[:4] == ["settings", "app", "command", "models"]
    assert {"runtime:comfy-api-proxy", "runtime:vlm-llamacpp", "runtime:segdepth", "runtime:rocm-toolchain",
            "runtime:trellis2"} <= set(ids)
    assert "runtime:vlm-transformers" not in ids
    steps = installer.plan({"comfy_backend": "httpx", "vlm_model": "8b", "hardware_profile": "nvidia"}, ["vlm-8b"])
    ids = [s["id"] for s in steps]
    assert "runtime:vlm-transformers" in ids
    assert not {"runtime:comfy-api-proxy", "runtime:rocm-toolchain", "runtime:trellis2"} & set(ids)


# --- installing ----------------------------------------------------------------------------------------

def test_progress_follows_the_download_events(tmp_path, monkeypatch):
    seen = snapshots(monkeypatch)
    inst = installer.Installer(models_cmd=fake_download(tmp_path, EVENTS), run=lambda cmd, log, env=None: 0)
    inst.update({"values": {"storage": str(tmp_path / "storage"), "comfy_backend": "httpx", "vlm_model": "32b"},
                 "components": ["flux1-dev-fp8", "sam3.1"]})
    inst.start()
    p = wait(inst)
    assert p["status"] == "done", p
    assert p["overall"]["fraction"] == 1.0
    cur = [s["current"] for s in seen if (s.get("current") or {}).get("model") == "flux1-dev-fp8"]
    assert {"label": "Downloading FLUX.1-dev (fp8)", "model": "flux1-dev-fp8", "done": 12.4 * GB, "total": 20 * GB} in cur
    assert any(c["label"].startswith("Checking FLUX.1-dev") for c in cur)
    sam = [s["current"] for s in seen if (s.get("current") or {}).get("model") == "sam3.1"]
    assert sam[0]["done"] == 4 * GB                 # a resumed file starts where its .part ended
    assert seen[-1]["download"] == {"done": 30 * GB, "total": 30 * GB}
    assert (tmp_path / "home" / ".local" / "bin" / "ap").is_symlink()
    assert config.get("comfyui.backend") == "httpx"   # the settings step saved the user config


def test_a_failed_download_is_reported_with_its_fix(tmp_path):
    events = [EVENTS[0], {"event": "error", "model": "flux1-redux-dev", "kind": "gated", "message": "gated repo",
                          "fix": "accept the license"}, {"event": "done", "installed": 0, "failed": 1}]
    inst = installer.Installer(models_cmd=fake_download(tmp_path, events), run=lambda cmd, log, env=None: 0)
    inst.update({"values": {"comfy_backend": "httpx"}, "components": ["flux1-redux-dev"]})
    inst.start()
    p = wait(inst)
    assert p["status"] == "failed"
    assert p["errors"] == [{"step": "models", "model": "flux1-redux-dev", "message": "gated repo", "fix": "accept the license"}]
    assert [s["status"] for s in p["steps"]][:4] == ["done", "done", "done", "failed"]


def test_secrets_go_to_secrets_toml_never_the_state_file(tmp_path):
    inst = installer.Installer(models_cmd=fake_download(tmp_path, [EVENTS[-1]]), run=lambda cmd, log, env=None: 0)
    inst.update({"values": {"comfy_backend": "httpx"}, "components": []})
    inst.start({"HF_TOKEN": "hf_abcdefghijklmnopqrstuvwxyz"})
    wait(inst)
    assert config.secrets()["HF_TOKEN"] == "hf_abcdefghijklmnopqrstuvwxyz"
    assert "hf_abc" not in installer.state_path().read_text()


def test_resume_after_the_window_and_the_server_closed(tmp_path, monkeypatch):
    calls = []
    run = lambda cmd, log, env=None: calls.append(cmd) or 0          # noqa: E731
    slow = fake_download(tmp_path, EVENTS[:3] + [{"event": "progress", "model": "flux1-dev-fp8", "file": "f",
                                                  "done": 13 * GB, "total": 20 * GB}] * 200, sleep=0.05)
    sel = ["flux1-dev-fp8", "trellis2-4b"]
    inst = installer.Installer(models_cmd=slow, run=run)
    inst.update({"page": "installing", "values": {"comfy_backend": "httpx", "hardware_profile": "nvidia"}, "components": sel})
    inst.start()
    t0 = time.time()
    while (inst.progress().get("current") or {}).get("model") != "flux1-dev-fp8":
        assert time.time() - t0 < 10
        time.sleep(0.02)
    # the server stops mid-download (its state says running), then starts again
    inst.stop = True
    inst.proc.terminate()
    inst.thread.join(5)
    st = json.loads(installer.state_path().read_text())
    st["install"]["status"] = "running"
    installer.save_state(st)
    again = installer.Installer(models_cmd=fake_download(tmp_path, EVENTS), run=run)
    assert again.progress()["status"] == "interrupted"            # the page resumes it by itself
    assert again.st["page"] == "installing" and again.st["components"] == sel
    calls.clear()
    again.start()
    p = wait(again)
    assert p["status"] == "done", p
    by = {s["id"]: s["status"] for s in p["steps"]}
    assert by["settings"] == by["app"] == by["command"] == "done"
    assert [c[0] for c in calls] == ["scripts/install_trellis2.sh"]   # only the runtime still to do ran
    # ap setup later: the finished runtime is skipped
    third = installer.Installer(models_cmd=fake_download(tmp_path, [EVENTS[-1]]), run=run)
    calls.clear()
    third.start()
    p = wait(third)
    assert {s["id"]: s["status"] for s in p["steps"]}["runtime:trellis2"] == "skipped" and not calls


def test_cancel_pauses_and_install_resumes(tmp_path):
    slow = fake_download(tmp_path, EVENTS[:2] + [EVENTS[2]] * 400, sleep=0.05)
    inst = installer.Installer(models_cmd=slow, run=lambda cmd, log, env=None: 0)
    inst.update({"values": {"comfy_backend": "httpx"}, "components": ["flux1-dev-fp8"]})
    inst.start()
    while (inst.progress().get("current") or {}).get("model") != "flux1-dev-fp8":
        time.sleep(0.02)
    assert inst.cancel()["status"] == "paused"
    inst.models_cmd = fake_download(tmp_path, EVENTS)
    inst.start()
    assert wait(inst)["status"] == "done"


# --- the server -------------------------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    inst = installer.Installer(models_cmd=fake_download(tmp_path, EVENTS), run=lambda cmd, log, env=None: 0)
    state = {"profile": "rocm-1gpu"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), installer.make_handler(inst, state))
    state["httpd"] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], inst
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"localhost:{port}"} | ({"Content-Type": "application/json"} if body is not None else {}) | (headers or {})
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_server_serves_the_pages_assets_and_state(server):
    port, _ = server
    code, html, _ = req(port, "GET", "/")
    assert code == 200 and b"wizard-core.js" in html
    code, js, _ = req(port, "GET", "/web/wizard-core.js")
    assert code == 200 and b"createWizard" in js
    code, data, _ = req(port, "GET", "/api/state")
    st = json.loads(data)
    assert "sequence.json" in st["assets"] and "01-opening-door-vintage.mp3" in st["assets"]
    assert st["profile_hint"] == "rocm-1gpu" and "MIT License" in st["license_text"]
    assert req(port, "GET", "/assets/../LICENSE")[0] == 404
    code, part, h = req(port, "GET", "/assets/sequence.json", headers={"Range": "bytes=0-9"})
    assert code == 206 and len(part) == 10 and h["Content-Range"].startswith("bytes 0-9/")
    code, data, _ = req(port, "POST", "/api/state", {"page": "components", "license": True})
    assert code == 200 and json.loads(installer.state_path().read_text())["page"] == "components"


def test_server_refuses_other_sites(server):
    port, _ = server
    assert req(port, "GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403     # DNS rebinding
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/install", "{}", {"Host": f"localhost:{port}", "Content-Type": "text/plain"})
    assert c.getresponse().status == 403                                                      # a simple cross-site POST


def test_server_installs_and_reports_progress(server):
    port, inst = server
    req(port, "POST", "/api/state", {"values": {"comfy_backend": "httpx"}, "components": ["flux1-dev-fp8", "sam3.1"]})
    code, data, _ = req(port, "POST", "/api/install", {"secrets": {}})
    assert code == 200 and json.loads(data)["status"] == "running"
    wait(inst)
    p = json.loads(req(port, "GET", "/api/progress")[1])
    assert p["status"] == "done" and p["download"] == {"done": 30 * GB, "total": 30 * GB}


def test_server_test_button_uses_setup_tests(server, tmp_path):
    port, _ = server
    r = json.loads(req(port, "POST", "/api/test", {"id": "storage", "value": str(tmp_path / "s"), "values": {}})[1])
    assert r["ok"] and r["id"] == "storage"


def test_ap_setup_opens_the_wizard(monkeypatch):
    called = {}
    monkeypatch.setattr(installer, "serve", lambda **kw: called.update(kw))
    cli.main(["setup", "--no-open"])
    assert called == {"open_page": False}

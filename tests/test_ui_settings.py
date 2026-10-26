"""The app's Settings (File › Settings…, the toolbar gear): Keys & connections lists every key and
connection of the setup wizard's Connections page, saves keys to secrets.toml and settings to the
user config; the Status box links there when a feature needs a missing key. The app window's icon."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from asset_pipeline import config, setup
from asset_pipeline.jobs import JobQueue
from asset_pipeline.ui import app as ui_app

STATIC = Path(ui_app.STATIC)


def _client():
    return TestClient(ui_app.create_app(JobQueue()))


def test_settings_is_in_the_file_menu_and_the_toolbar():
    html = _client().get("/").text
    assert 'id="tb-settings"' in html and 'aria-label="Settings"' in html
    assert re.search(r'src="/static/settings\.js\?v=', html)
    js = (STATIC / "settings.js").read_text()
    assert '["Settings…", "appSettings"]' in js and 'MENUS.find(m => m[0] === "File")' in js
    assert '$("#tb-settings").onclick = () => appSettingsDialog()' in js
    assert '"Project settings…"' in (STATIC / "versions.js").read_text()      # the per-project one, renamed apart
    assert 'it.wizard === "connections"' in js                    # the wizard's Connections page, nothing hand-listed
    assert "st-show" in js and 'type="password"' in js            # keys masked, with Show
    assert "127.0.0.1 is this computer" in js


def test_keys_and_connections_come_from_setup_and_save(monkeypatch, tmp_path):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    monkeypatch.setattr(setup, "detect", lambda: {})
    c = _client()
    items = [i for i in c.get("/api/setup").json()["items"] if i["wizard"] == "connections"]
    ids = {i["id"] for i in items}
    assert {"comfy_backend", "comfy_url", "sdk_url", "comfy_api_key", "hf_token", "blender", "unreal"} <= ids
    r = c.post("/api/setup/save", json={"values": {"comfy_backend": "httpx"}, "secrets": {"HF_TOKEN": "hf_" + "x" * 20}})
    assert r.status_code == 200
    assert config.get("comfyui.backend") == "httpx" and config.secrets()["HF_TOKEN"].startswith("hf_")
    assert "hf_xxx" not in c.get("/api/setup").text                # key values never come back


def test_status_box_links_a_missing_key_to_settings():
    js = (STATIC / "doctor.js").read_text()
    assert "keys_missing" in js and "Setup: missing key — Settings" in js and 'data-open="settings"' in js
    assert 'b.dataset.open === "settings"' in js


def test_the_app_has_the_terraformer_icon():
    c = _client()
    assert '<link rel="icon" href="/app-icon.png">' in c.get("/").text
    r = c.get("/app-icon.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"

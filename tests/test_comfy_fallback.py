"""Native Windows: the sdk backend falls back to direct HTTP when the proxy or SDK can't run
(comfy/fallback.py). Linux and an explicit AP_COMFY_BACKEND never fall back."""
import pytest

from asset_pipeline import doctor, oscompat
from asset_pipeline.comfy import client as comfy_client, fallback, service
from asset_pipeline.comfy.client import ComfyClient
from asset_pipeline.comfy.sdk_client import SdkComfyClient


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    monkeypatch.delenv("AP_COMFY_BACKEND", raising=False)
    monkeypatch.setattr(oscompat, "IS_WINDOWS", True)
    return tmp_path


def _probe(monkeypatch, why):
    monkeypatch.setattr(fallback, "probe", lambda *a, **k: why)


def test_windows_falls_back_to_httpx_and_records_why(win, monkeypatch):
    _probe(monkeypatch, "comfy-api-proxy not answering at http://127.0.0.1:8190")
    c = comfy_client.connect()
    assert type(c) is ComfyClient
    assert fallback.notice() == "SDK path unavailable, using direct HTTP"
    assert "not answering" in fallback.active()["reason"]
    log = (win / "logs" / "comfy-backend.log").read_text()
    assert "SDK path unavailable" in log and "not answering" in log
    comfy_client.connect()
    assert (win / "logs" / "comfy-backend.log").read_text() == log      # logged once per reason


def test_windows_uses_sdk_and_clears_the_notice_when_it_works(win, monkeypatch):
    fallback.record("old failure")
    _probe(monkeypatch, None)
    monkeypatch.setattr("asset_pipeline.comfy.sdk_client._sdk_client", lambda url, timeout: object())
    assert isinstance(comfy_client.connect(), SdkComfyClient)
    assert fallback.notice() is None
    assert "available again" in (win / "logs" / "comfy-backend.log").read_text()


def test_explicit_backend_env_is_honored(win, monkeypatch):
    monkeypatch.setenv("AP_COMFY_BACKEND", "sdk")
    monkeypatch.setattr("asset_pipeline.comfy.sdk_client._sdk_client", lambda url, timeout: object())
    _probe(monkeypatch, "down")
    assert isinstance(comfy_client.connect(), SdkComfyClient)
    assert fallback.notice() is None


def test_linux_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    monkeypatch.setattr(oscompat, "IS_WINDOWS", False)
    monkeypatch.setattr("asset_pipeline.comfy.sdk_client._sdk_client", lambda url, timeout: object())
    _probe(monkeypatch, "down")
    assert isinstance(comfy_client.connect(), SdkComfyClient)
    assert fallback.notice() is None and not (tmp_path / "logs").exists()


def test_probe_reports_an_unreachable_proxy(win):
    why = fallback.probe("http://127.0.0.1:9", timeout=0.5)
    assert why and "comfy-api-proxy not answering" in why


def test_start_falls_back_when_the_proxy_wont_start(win, monkeypatch):
    s = service.settings()
    monkeypatch.setattr(service, "healthy", lambda *a, **k: True)
    monkeypatch.setattr(service, "proxy_up", lambda *a, **k: False)
    monkeypatch.setattr(service, "listener_pid", lambda port: None)

    def boom(*a, **k):
        raise RuntimeError("comfy-api-proxy is not installed (x)")
    monkeypatch.setattr(service, "start_proxy", boom)
    lines = []
    out = service.start(log=lines.append, s=s)
    assert out["notice"] == fallback.NOTICE
    assert any(fallback.NOTICE in ln for ln in lines)
    assert service.ready(s)                       # the Gate no longer waits for a proxy


def test_start_still_raises_on_linux(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    monkeypatch.setattr(oscompat, "IS_WINDOWS", False)
    monkeypatch.setattr(service, "healthy", lambda *a, **k: True)
    monkeypatch.setattr(service, "proxy_up", lambda *a, **k: False)
    monkeypatch.setattr(service, "start_proxy", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    with pytest.raises(RuntimeError):
        service.start(log=lambda m: None)


def test_doctor_shows_the_notice(win, monkeypatch):
    from asset_pipeline import setup
    monkeypatch.setattr(setup, "proxy_installed", lambda: None)
    monkeypatch.setattr(setup, "_probe_comfy", lambda *a, **k: {"system": {"comfyui_version": "0.24"}})
    monkeypatch.setattr(doctor, "check_comfy_models", lambda url: doctor._c("comfy_models", "ComfyUI models", True, "ok"))
    checks = doctor.check_comfy()
    note = next(c for c in checks if c.id == "comfy_sdk_fallback")
    assert note.level == "warn" and fallback.NOTICE in note.detail
    assert all(c.level != "fail" for c in checks)

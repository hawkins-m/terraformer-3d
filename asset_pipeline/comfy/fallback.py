"""Native Windows: the sdk backend is the default, and falls back to direct HTTP when it can't run.

The Comfy SDK path needs comfy-api-proxy (installed, started, answering) and the comfy-sdk
package. When either is missing or fails, `connect()` hands out the httpx client instead and
the reason is recorded here: one line in $AP_ROOT/logs/comfy-backend.log, and a small state file
the Status box and `ap doctor` read for NOTICE. The next probe that succeeds clears it, so the
sdk path comes back by itself once the proxy runs.

Applies only on native Windows, and only when the backend comes from the config: an explicit
AP_COMFY_BACKEND=sdk is honored as it is (the live parity test relies on that). Linux never
falls back."""
import json
import os
import time

import httpx

from .. import config, oscompat

NOTICE = "SDK path unavailable, using direct HTTP"


def applies() -> bool:
    """Native Windows with the sdk backend chosen by the config (not by AP_COMFY_BACKEND)."""
    return (oscompat.IS_WINDOWS and not os.environ.get("AP_COMFY_BACKEND")
            and config.backends()["comfyui"].get("backend", "sdk") == "sdk")


def _state_file():
    return config.ap_root() / "logs" / "comfy-sdk-fallback.json"


def _log(line: str) -> None:
    try:
        p = config.ap_root() / "logs" / "comfy-backend.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def active() -> dict | None:
    """{"reason", "since"} while the fallback is in effect, else None."""
    if not applies():
        return None
    try:
        return json.loads(_state_file().read_text(encoding="utf-8")) or None
    except (OSError, ValueError):
        return None


def notice() -> str | None:
    a = active()
    return f"{NOTICE}" if a else None


def record(reason: str) -> None:
    """Fall back to httpx for `reason`. Logged when it is new or has changed, not on every probe."""
    reason = " ".join(str(reason).split())
    cur = active()
    if cur and cur.get("reason") == reason:
        return
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        oscompat.atomic_write_text(f, json.dumps({"reason": reason, "since": time.time()}))
    except OSError:
        pass
    _log(f"{NOTICE}: {reason}")


def clear() -> None:
    if active():
        _log("SDK path available again")
    try:
        _state_file().unlink()
    except OSError:
        pass


def probe(sdk_url: str | None, comfy_url: str | None = None, timeout: float = 2.0) -> str | None:
    """Why the sdk path can't be used right now, or None when it works. With comfy_url (ComfyUI
    known to be up) the SDK client must also reach ComfyUI through the proxy."""
    if not sdk_url:
        return "no proxy address ([comfyui] sdk_url)"
    try:
        httpx.get(f"{sdk_url.rstrip('/')}/api/v2/health", timeout=timeout).raise_for_status()
    except httpx.HTTPError as e:
        return f"comfy-api-proxy not answering at {sdk_url} ({e})"
    try:
        from .sdk_client import SdkComfyClient
        c = SdkComfyClient(comfy_url or "http://127.0.0.1", sdk_url, timeout_s=timeout)
        if comfy_url:
            c.http.timeout = httpx.Timeout(timeout)
            c.health()
    except Exception as e:                              # noqa: BLE001 - any failure means "use httpx", with the reason
        return f"the Comfy SDK failed: {e}"
    return None


def check(sdk_url: str | None, comfy_url: str | None = None) -> bool:
    """Probe and record: True when the sdk path works (and any earlier fallback is cleared)."""
    why = probe(sdk_url, comfy_url)
    if why:
        record(why)
        return False
    clear()
    return True

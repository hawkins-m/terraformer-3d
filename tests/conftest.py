"""Every test runs with its own empty user config folder: the machine's
~/.config/terraformer (storage folder, GPUs, API keys) never leaks into a test. WSL is off unless a
test turns it on (tests/test_wsl.py)."""
import pytest

from asset_pipeline import config, wsl

_BACKENDS = config.backends          # the cached original (some tests swap in a plain function)


@pytest.fixture(autouse=True)
def _own_user_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("AP_CONFIG_DIR", str(tmp_path_factory.mktemp("user-config")))
    for env in config.ENV_OVERRIDES:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(wsl, "is_wsl", lambda proc=None: False)   # a WSL machine runs the suite as plain Linux
    _BACKENDS.cache_clear()
    yield
    _BACKENDS.cache_clear()

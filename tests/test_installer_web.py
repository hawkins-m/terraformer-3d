"""The setup wizard's page flow, sounds and progress text (installer/web/wizard-core.js), tested in
node: tests/js/wizard_core.test.js. Skipped without node."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_wizard_core_in_node():
    r = subprocess.run(["node", "--test", str(REPO / "tests" / "js" / "wizard_core.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_wizard_scripts_parse_in_one_scope(tmp_path):
    """Like the app's static files: the scripts share one global scope."""
    web = REPO / "installer" / "web"
    (tmp_path / "all.js").write_text("".join((web / f).read_text() for f in ("wizard-core.js", "wizard.js")))
    r = subprocess.run(["node", "--check", str(tmp_path / "all.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_wizard_uses_only_assets_that_exist_or_fall_back():
    """Sounds and the sequence ship with the repo; images and the video fall back when missing."""
    assets = REPO / "installer" / "assets"
    core = (REPO / "installer" / "web" / "wizard-core.js").read_text()
    for mp3 in ("01-opening-door-vintage.mp3", "02-selection-tick-soft.mp3", "03-next-glass-chime.mp3",
                "04-install-complete-shimmer.mp3"):
        assert (assets / mp3).is_file() and mp3 in core
    assert (assets / "sequence.json").is_file()
    html = (REPO / "installer" / "web" / "index.html").read_text()
    assert html.count("data-fallback=") == 2               # title bar icon and header icon

"""The UI always serves the repo's current code: index.html is never cached, every static URL in
it carries a content hash, and /api/version tells an open page that the code changed."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from asset_pipeline.jobs import JobQueue
from asset_pipeline.ui import app as ui_app


def _client():
    return TestClient(ui_app.create_app(JobQueue()))


def test_index_is_no_store_and_versions_every_static_url():
    r = _client().get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
    assert refs and all(re.search(r"\?v=[0-9a-f]{8}$", u) for u in refs), refs
    assert f'<meta name="ap-build" content="{ui_app.build_id()}">' in r.text


def test_versioned_assets_are_immutable_and_plain_ones_revalidate():
    c = _client()
    js = re.search(r'src="(/static/shell\.js\?v=[0-9a-f]+)"', c.get("/").text).group(1)
    assert "immutable" in c.get(js).headers["cache-control"]
    plain = c.get("/static/shell.js")
    assert plain.headers["cache-control"] == "no-cache" and plain.headers.get("etag")


def test_a_changed_static_file_gets_a_new_url_and_build(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text('<head></head><script src="/static/a.js"></script>')
    (static / "a.js").write_text("one")
    monkeypatch.setattr(ui_app, "STATIC", static)
    first, b1 = ui_app.index_html(), ui_app.build_id()
    (static / "a.js").write_text("two")
    assert ui_app.index_html() != first and ui_app.build_id() != b1   # no server restart needed


def test_version_endpoint_names_the_repo_code():
    v = _client().get("/api/version").json()
    assert v["build"] == ui_app.build_id()
    assert (Path(v["code"]) / "asset_pipeline" / "ui" / "app.py").resolve() == Path(ui_app.__file__).resolve()


def test_input_patterns_are_valid_in_unicode_sets_mode():
    """Browsers compile pattern="" with the v flag: a bare "-" in a character class is a syntax
    error there and silently disables the field's validation (found in a headless check)."""
    static = Path(ui_app.STATIC)
    pats = [m for f in static.glob("*.*") if f.suffix in (".js", ".html")
            for m in re.findall(r'pattern="([^"]*)"', f.read_text())]
    assert pats
    for p in pats:
        assert not re.search(r"\[[^\]]*[^\\]-\]", p), p


def test_the_old_product_name_is_gone():
    """The product is "Terraformer" (2026-10-17); the old long name crept back once, in the launcher."""
    root = Path(ui_app.__file__).resolve().parents[2]
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html", ".css", ".md", ".sh", ".toml")
             and not any(part in (".git", ".claude", "build", "__pycache__") for part in p.parts)]
    hits = [str(p.relative_to(root)) for p in files if p != Path(__file__).resolve() and "Terraformer Pipeline" in p.read_text(errors="ignore")]
    assert not hits, hits


def test_every_script_is_served_by_the_app_no_cdn():
    """The UI works offline: three.js and model-viewer are vendored under static/vendor."""
    html = (ui_app.STATIC / "index.html").read_text()
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    assert srcs and all(s.startswith("/static/") for s in srcs), srcs
    assert '<script type="module" src="/static/vendor/model-viewer/model-viewer.min.js"></script>' in html
    mv = ui_app.STATIC / "vendor" / "model-viewer"
    assert (mv / "model-viewer.min.js").stat().st_size > 500_000 and "Apache License" in (mv / "LICENSE").read_text()
    assert "model-viewer" in (ui_app.STATIC.parents[2] / "THIRD_PARTY_LICENSES.md").read_text()

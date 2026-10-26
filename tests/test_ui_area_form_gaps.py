"""Library › Areas › Form gaps: a fix says what it did (with Next and Undo) and points at the row it changed."""
import json

import pytest

from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import sw_site

from test_ui_layout import BROWSER_PY, _browse, _entry


def _seed(store: ProjectStore):
    """Two undecided procedural buildings in harbour-area with no form: one whose words match a form,
    one that matches none (its first fix is Build with TRELLIS)."""
    lib = sw_site.site_dir(store) / "library.json"
    d = json.loads(lib.read_text())
    b = {"kind": "building", "tier": "B", "route_auto": "procedural"}
    d["unseen"] += [_entry("hall-a", "harbour") | b | {"description_auto": "A long hall with a colonnade."},
                    _entry("blob-x", "harbour") | b | {"description_auto": "A lumpy shape."}]
    write_json(lib, d)


SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {}
ROW = '#area-table tr[data-asset="{}"]'
BRIEF = """a => { const tr = document.querySelector(`#area-table tr[data-asset="${a}"]`);
  const i = [...document.querySelectorAll('#area-table th')].findIndex(th => th.textContent === 'Brief');
  return {brief: tr.children[i].textContent.trim(), rowFlash: tr.classList.contains('flash'), cellFlash: tr.children[i].classList.contains('flash'),
          inView: (r => r.top >= 0 && r.bottom <= innerHeight)(tr.getBoundingClientRect())}; }"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 700})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_selector("#lanes .lane", state="attached", timeout=20000)
    pg.click("button[data-tab=library]"); pg.click("button[data-sub=areas]")
    pg.wait_for_selector(".area-gap", timeout=10000)
    out["gaps0"] = pg.eval_on_selector_all(".area-gap", "els => els.map(e => e.dataset.asset)")
    out["fixes"] = {a: pg.locator(f'.area-gap[data-asset="{a}"] .area-fix').first.inner_text() for a in out["gaps0"]}
    out["before"] = pg.evaluate(BRIEF, "hall-a")
    pg.locator('.area-gap[data-asset="hall-a"] .area-fix').first.click()
    pg.wait_for_selector(".area-fixed", timeout=10000); pg.wait_for_timeout(300)
    out["note"] = pg.inner_text(".area-fixed")
    out["gaps1"] = pg.eval_on_selector_all(".area-gap", "els => els.map(e => e.dataset.asset)")
    out["after"] = pg.evaluate(BRIEF, "hall-a")
    pg.click("#area-fix-undo"); pg.wait_for_selector('.area-gap[data-asset="hall-a"]', timeout=10000)
    out["undone"] = {"note": pg.locator(".area-fixed").count(), "gaps": pg.eval_on_selector_all(".area-gap", "els => els.map(e => e.dataset.asset)"),
                     "brief": pg.evaluate(BRIEF, "hall-a")["brief"]}
    pg.locator('.area-gap[data-asset="blob-x"] .area-fix').first.click()
    pg.wait_for_selector(".area-fixed", timeout=10000); pg.wait_for_timeout(300)
    out["trellis"] = {"note": pg.inner_text(".area-fixed"), "route": pg.inner_text(ROW.format("blob-x") + " td:nth-child(3)"),
                      "flash": pg.evaluate(BRIEF, "blob-x")["rowFlash"]}
    # the line's "Next: Decide" is a link: Asset review, scrolled to the card, highlighted
    out["next_link"] = pg.inner_text("#area-fix-next")
    pg.click("#area-fix-next"); pg.wait_for_timeout(400)
    out["next"] = pg.evaluate("""() => { const c = document.querySelector('#assets-list [data-entry="blob-x"]'), r = c && c.getBoundingClientRect();
      const l = document.querySelector('#assets-list').getBoundingClientRect();
      return {tab: currentTab, sub: localStorage.getItem('ap.sub'), card: !!c, flash: !!c && c.classList.contains('jump-hit'),
              inView: !!c && r.top >= l.top && r.bottom <= l.bottom}; }""")
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_a_form_gap_fix_says_what_it_did_and_can_be_undone(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, SCRIPT, seed=_seed)
    assert sorted(out["gaps0"]) == ["blob-x", "hall-a"]
    assert out["fixes"]["blob-x"] == "Build with TRELLIS" and out["fixes"]["hall-a"].startswith("Form: One-storey colonnade range")
    assert out["before"]["brief"] == "no form"
    assert out["note"].startswith("✓ hall a: form set to One-storey colonnade range") and "Next: Decide" in out["note"]
    assert out["note"].endswith("Undo") and out["gaps1"] == ["blob-x"]
    assert out["after"] == {"brief": "–", "rowFlash": True, "cellFlash": True, "inView": True}
    u = out["undone"]                                                           # Undo: the gap is back, the line gone
    assert u["note"] == 0 and sorted(u["gaps"]) == ["blob-x", "hall-a"] and u["brief"] == "no form"
    assert out["trellis"]["note"].startswith("✓ blob x: build method set to TRELLIS.") and "Next: Decide" in out["trellis"]["note"]
    assert out["trellis"]["route"] == "trellis" and out["trellis"]["flash"]
    assert out["next_link"] == "Decide"
    assert out["next"] == {"tab": "library", "sub": "assets", "card": True, "flash": True, "inView": True}
    assert out["errors"] == []

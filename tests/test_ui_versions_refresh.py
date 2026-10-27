"""Version actions re-render the asset's row from the server (no reload needed)."""
import pytest

from asset_pipeline import filestore, versions
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline import kit_proc

from test_ui_layout import BROWSER_PY, _browse


def _trellis(store, asset, seed):
    stem = f"sheet_000_v0_1024_cascade_s{seed}"
    out = kit_proc.out_dir(store, asset)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.glb").write_bytes(b"trellis-glb" * (100 + seed))
    write_json(out / f"{stem}.json", {"seed": seed, "glb_faces": 1000 + seed})
    (out / f"{stem}_input.png").write_bytes(b"png")
    filestore.intern(store, out / f"{stem}.glb")
    return versions.record_trellis(store, "library", asset, {"glb": f"3d/library/{asset}/{stem}.glb",
                                                             "input": f"3d/library/{asset}/{stem}_input.png",
                                                             "seed": seed, "mode": "1024_cascade"})


DELETE_SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {}
ROW = '#review-list .asset-review[data-asset="harbour-prop-00"]'
STATE = """sel => { const r = document.querySelector(sel); return {
  vers: [...r.querySelectorAll('.version-strip .ver')].map(e => +e.dataset.n),
  cards: [...r.querySelectorAll('.ar-models .result')].map(e => +e.dataset.n),
  total: r.querySelector('.version-strip legend').textContent.trim().split(' · ')[0],
  head: (r.querySelector('.cur-version b') || {}).textContent || ''}; }"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_selector("#assets-list .asset-card", state="attached", timeout=20000)
    pg.click("button[data-tab=views]")
    pg.wait_for_selector(ROW + " .version-strip .ver", timeout=10000); pg.wait_for_timeout(500)
    out["before"] = pg.evaluate(STATE, ROW)
    # Use this version / Delete live on the 3D model cards
    pg.click(ROW + ' .ar-models .result[data-n="1"] .ver-delete')
    pg.click("#version-delete-dialog [data-role=confirm]"); pg.wait_for_timeout(1500)
    out["deleted"] = pg.evaluate(STATE, ROW)
    # Use this version: the Active badge in the header, the strip and the first card follow
    pg.click(ROW + ' .ar-models .result[data-n="2"] .ver-restore')
    pg.click("#version-restore-dialog [data-role=confirm]")
    pg.wait_for_selector("#version-used-dialog", timeout=15000)
    pg.click("#version-used-dialog .wbuttons button"); pg.wait_for_timeout(800)
    out["used"] = pg.evaluate(STATE, ROW) | {"active": pg.evaluate(f"sel => +document.querySelector(sel + ' .ver.cur').dataset.n", ROW)}
    out["active_card"] = pg.evaluate("""sel => { const c = document.querySelector(sel + ' .ar-models .result.current');
      return [!c.querySelector('.ver-restore'), c.querySelector('.ver-delete').disabled]; }""", ROW)
    # a version job finishing (Reroll, Make 3D) refreshes its asset's strip on the next load, also
    # when /review didn't change (v3 deleted behind the page's back)
    out["job"] = pg.evaluate("""async ([sel, a]) => {
      await fetch(`/api/projects/${slug}/library/asset/${a}/versions/3`, {method: "DELETE"});
      data.jobs.push({id: "jx", kind: "library.reroll", status: "done", tag: {asset: a}, finished: Date.now() / 1000});
      await versionsAfterLoad(); renderReview();
      return !document.querySelector(`${sel} .ver[data-n="3"]`);
    }""", [ROW, "harbour-prop-00"])
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_deleting_a_version_updates_the_row_without_a_reload(tmp_path, monkeypatch):
    def seed(store: ProjectStore):
        for s in (1, 2, 3):
            _trellis(store, "harbour-prop-00", s)
    out = _browse(tmp_path, monkeypatch, DELETE_SCRIPT, seed)
    assert sorted(out["before"]["vers"]) == [1, 2, 3], out
    assert 1 in out["before"]["cards"] and out["before"]["total"] == "Versions 3"
    assert 1 not in out["deleted"]["vers"] and 1 not in out["deleted"]["cards"], out
    assert out["deleted"]["total"] == "Versions 2", out
    assert out["used"]["head"] == "v2" and out["used"]["active"] == 2 and out["used"]["cards"][0] == 2, out
    assert out["active_card"] == [True, True], out
    assert out["job"], out
    assert out["errors"] == []

"""Library › Reference sheets: the "guidance for the next sheets" box keeps exactly what was typed,
cleared included, across re-renders, a finished batch and a reload, and "+2 more" sends what is in
it. The bug: a cleared box refilled with the older guidance once the new sheets loaded."""
import pytest

from test_ui_layout import BROWSER_PY, _browse

SCRIPT = r'''
import json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = {"sent": []}
E = "academy-prop-00"
D = Path(os.environ["AP_ROOT"]) / "projects" / "p" / "refs" / "library" / E

def generate(route):                 # stubbed: the batch's sheets land as a real job would leave them
    body = json.loads(route.request.post_data)
    if route.request.headers.get("x-ap-estimate"):      # the long-job estimate: the real server answers it
        return route.continue_()
    out["sent"].append(body.get("guidance"))
    meta = json.loads((D / "meta.json").read_text())
    for _ in range(body.get("n") or 2):
        name = f"sheet_{len(list(D.glob('sheet_*.png'))):03d}.png"
        (D / name).write_bytes(b"")
        meta["sheets"].append({"file": name, "view": "turnaround", "description": "A thing."}
                              | ({"guidance": body["guidance"]} if body.get("guidance") else {}))
    (D / "meta.json").write_text(json.dumps(meta))
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"id": "stub", "kind": "refs.generate", "status": "done", "tag": {}}))

BOX = f'#refs-list .unit[data-unit="{E}"] input.guidance'
CAPS = f"() => [...document.querySelectorAll('#refs-list .unit[data-unit=\"{E}\"] .sheet-cap')].map(c => c.innerText)"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 800})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/refs/generate", generate)
    pg.goto(url); pg.wait_for_selector("#assets-list .asset-card", state="attached", timeout=20000)
    pg.click("button[data-tab=library]"); pg.click("button[data-sub=refs]"); pg.wait_for_timeout(300)
    more = f'#refs-list .unit[data-unit="{E}"] button.more'
    pg.fill(BOX, "oak staves"); pg.click(more); pg.wait_for_timeout(800)
    out["after_first"] = [pg.input_value(BOX), pg.evaluate(CAPS)]
    pg.fill(BOX, ""); pg.click(more); pg.wait_for_timeout(800)
    out["after_second"] = [pg.input_value(BOX), pg.evaluate(CAPS)]
    pg.evaluate("load()"); pg.wait_for_timeout(500)
    pg.evaluate("renderRefs()")
    out["rerender"] = pg.input_value(BOX)
    pg.reload(); pg.wait_for_selector("#assets-list .asset-card", state="attached", timeout=20000)
    pg.click("button[data-tab=library]"); pg.click("button[data-sub=refs]"); pg.wait_for_timeout(300)
    out["reload"] = pg.input_value(BOX)
    out["server"] = pg.evaluate(f"() => refs.find(u => u.key === '{E}').guidance")
    out["errors"] = errs
    b.close()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not BROWSER_PY.exists(), reason="needs the browser-test env (Playwright + system Chrome)")
def test_cleared_guidance_stays_cleared_and_sends_none(tmp_path, monkeypatch):
    out = _browse(tmp_path, monkeypatch, SCRIPT)
    assert out["sent"] == ["oak staves", None]                 # the second request has no guidance
    box, caps = out["after_first"]
    assert box == "oak staves" and sum("Guidance:" in c for c in caps) == 2
    box, caps = out["after_second"]
    assert box == "" and sum("Guidance:" in c for c in caps) == 2   # the two new cards have no Guidance line
    assert len(caps) == 5
    assert out["rerender"] == "" and out["reload"] == "" and out["server"] == ""
    assert out["errors"] == []

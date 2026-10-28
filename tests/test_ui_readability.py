"""Readability: each tab and sub-tab opens with one short line (the full text behind a "?"), the
Library step strip counts what waits at each step, and the guide stays optional."""
import re
import shutil
from html.parser import HTMLParser

import pytest

from tests.test_ui_workflow import STATIC, _html, _wf

LINE = 80          # characters that fit on one line of the narrowest tab at 12px


class _Hints(HTMLParser):
    """Every element with class "hint": its visible text (a "?" button's text left out) and the
    full text of its "?" (the title)."""
    VOID = {"input", "br", "img", "meta", "link"}

    def __init__(self):
        super().__init__()
        self.stack, self.out = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.VOID:
            return
        cls = (a.get("class") or "").split()
        if self.stack and "help-q" in cls:
            self.stack[-1]["q"] = a.get("title")
        self.stack.append({"tag": tag, "hint": "hint" in cls, "tab_help": "tab-help" in cls, "id": a.get("id"),
                           "text": "", "q": None, "skip": "help-q" in cls})

    def handle_endtag(self, tag):
        while self.stack:
            el = self.stack.pop()
            if self.stack and not el["skip"]:
                self.stack[-1]["text"] += el["text"]
                self.stack[-1]["q"] = self.stack[-1]["q"] or el["q"]
            if el["hint"]:
                self.out.append(el | {"text": " ".join(el["text"].split())})
            if el["tag"] == tag:
                break

    def handle_data(self, d):
        if self.stack and not self.stack[-1]["skip"]:
            self.stack[-1]["text"] += d


def _hints(html):
    p = _Hints()
    p.feed(html)
    return p.out


def _section(html, tab):
    return re.search(rf'<section class="tabpanel" data-tab="{tab}".*?\n      </section>', html, re.S).group(0)


def test_every_help_text_on_the_page_is_one_line_and_long_text_sits_behind_a_question_mark():
    hints = _hints(_html())
    long = [h["text"] for h in hints if len(h["text"]) > LINE]
    assert not long, long
    for h in hints:
        if h["tab_help"]:
            assert h["q"] and len(h["q"]) > len(h["text"]), h          # the full text moved into the "?"


def test_each_tab_and_library_sub_tab_starts_with_a_one_line_help():
    html = _html()
    lib = _section(html, "library")
    for sub in ("plan", "assets", "refs"):                             # Areas is generated: areas.js
        body = re.search(rf'<div data-sub="{sub}".*?(?=\n          <div data-sub=|\n        </div>\n      </section>)', lib, re.S).group(0)
        first = _hints(body)[0]
        assert first["tab_help"] and first["q"] and len(first["text"]) <= LINE, (sub, first)
    for tab in ("style", "site", "shots", "views"):
        first = _hints(_section(html, tab))[0]
        assert first["tab_help"] and first["q"], (tab, first)
    areas = (STATIC / "areas.js").read_text()
    help_ = re.search(r'id="area-help">(.*?)\$\{helpQ\("([^"]+)"', areas)
    assert help_ and len(re.sub(r"<[^>]+>", "", help_.group(1)).strip()) <= LINE and len(help_.group(2)) > LINE
    # the header line every tab opens with: a short line, the full description in its "?"
    wf = (STATIC / "workflow.js").read_text()
    steps = re.findall(r'name: "([^"]+)", short: "([^"]+)", desc: "([^"]+)"', wf)
    assert len(steps) == 8 and all(len(sh) <= 50 < len(sh) + len(d) for _, sh, d in steps), steps
    head = wf[wf.index("function renderStepHeads"):]
    assert "esc(s.short)} ${helpQ(" in head and "sh-main tab-help" in head


def test_question_mark_shows_the_full_text_and_notes_keep_theirs_as_tooltips():
    shell = (STATIC / "shell.js").read_text()
    assert 'class="help-q" title="${esc(full)}"' in shell and 'closest(".help-q")' in shell
    lib = (STATIC / "library.js").read_text()
    assert "${shortNote(f)}" in lib                                     # card flags: a few words, the rest as tooltip
    frames = (STATIC / "frames.js").read_text()
    assert 'helpQ("Starring only marks frames you like; locking picks the one frame this shot uses."' in frames


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_short_note_keeps_the_whole_note_in_its_tooltip():
    import json
    import subprocess
    js = (STATIC / "app.js").read_text().split("\n")
    esc = next(ln for ln in js if ln.startswith("const esc"))
    shell = (STATIC / "shell.js").read_text()
    fn = shell[shell.index("function shortNote"):shell.index('document.addEventListener("click", e => {\n  const q')]
    notes = ["approximate crop: the id pass shows the whole slot holding it", "trees cover 40% of the asset area",
             "only 30% of the area is drawn as the asset (something else fills the rest)"]
    out = subprocess.run(["node", "-e", f"{esc}\n{fn}\nconsole.log(JSON.stringify({json.dumps(notes)}.map(n => shortNote(n))))"],
                         capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got[0] == '<span title="approximate crop: the id pass shows the whole slot holding it">approximate crop</span>'
    assert got[1] == "trees cover 40% of the asset area"
    assert got[2].startswith('<span title="only 30% of the area is drawn as the asset (something else fills the rest)">only 30%')


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_library_step_strip_counts_what_waits_at_each_step():
    ents = [{"id": i, "kind": "prop"} for i in ("a", "b", "c", "d", "e", "f", "g")] + [{"id": "tree", "kind": "vegetation"}]
    lib = {"plan": {"entries": ents, "unseen": []}, "decisions": {
        "b": {"decision": "description"}, "c": {"decision": "description"}, "d": {"decision": "reference"},
        "e": {"decision": "description"}, "f": {"decision": "exclude"},
        "g": {"decision": "kit", "pieces": {"bay": {}, "corner": {}}}},
        "status": {"steps": {"a": "decide", "b": "sheets", "c": "star", "d": "build", "e": "approve", "g-bay": "star"}},
        "sheets": {"needed": ["b"], "skip": {"c": "sheets", "d": "sheets", "g-bay": "sheets", "e": "built", "g-corner": "approved"}}}
    refs = [{"plan": "library", "key": k, "sheets": [f"refs/library/{k}/sheet_000.png"]} for k in ("c", "d", "g-bay")]
    review = {"pending_sheets": [], "starred_sheets": 1, "assets": [
        {"plan": "library", "asset": {"id": "e"}, "results": [{}], "current": {"n": 1}, "review": None},
        {"plan": "library", "asset": {"id": "g-corner"}, "results": [{}], "current": {"n": 1}, "review": "approved"}]}
    data = {"project": {"anchor": None}, "explore": [], "moodboard": {}, "jobs": [], "stars": {"refs/library/d/sheet_000.png": True}}
    got = _wf(libraryData=lib, refs=refs, reviewData=review, data=data, expr="libSteps().map(s => [s.id, s.waiting])")
    # a: undecided; b: no sheet; c: a sheet, none starred; d: starred, no model; e: built, not approved;
    # f: excluded; g: a kit (bay: sheet not starred; corner: approved); trees never wait
    assert dict(got) == {"decide": 1, "sheets": 1, "star": 2, "build": 1, "approve": 1}
    wf = (STATIC / "workflow.js").read_text()
    assert '"1 Decide' not in wf and 'name: "Decide", go: ["library", "assets"]' in wf and 'f.value = "todo"' in wf
    assert '<nav class="lib-steps" id="lib-steps"' in _html()


def test_guide_is_a_short_overlay_off_by_default():
    html, wf, css = _html(), (STATIC / "workflow.js").read_text(), (STATIC / "style.css").read_text()
    assert '<aside class="side guide" id="guide" aria-label="Guide" hidden>' in html
    assert re.search(r"\.side\.guide \{ position: fixed;", css) and "with-guide" not in css + wf
    guide = wf[wf.index("function renderGuide"):wf.index("// --- Library step strip")]
    assert "s.detail || s.desc" not in guide and 'title="${esc(`${s.short}' in guide      # detail in the tooltip


def test_library_sub_tabs_follow_the_workflow_order():
    # Scene plans sits between Areas and Asset review; Asset review and Reference sheets are neighbours.
    # Tab buttons, panels and showSub's list all share the order (arrow keys follow the buttons).
    lib = _section(_html(), "library")
    order = ["areas", "plan", "assets", "refs"]
    assert re.findall(r'<button role="tab" data-sub="(\w+)"', lib) == order
    assert re.findall(r'<div data-sub="(\w+)" id=', lib) == order
    assert '["areas", "plan", "assets", "refs"]' in (STATIC / "shell.js").read_text()


def test_views_row_order_and_version_actions():
    # Views and 3D, per asset: header right side = tier toggle, New seed, Make 3D; then the review row
    # (status, Approve, Re-cut views…, Needs changes); the Cut Sheets box; Versions beside the cards.
    js = (STATIC / "review.js").read_text()
    head = js[js.index('el.innerHTML = `<div class="head">'):js.index('<div class="review-row">')]
    assert head.index('class="usage"') < head.index('class="new-seed"') < head.index('class="make3d"')
    assert "recut" not in head and "approve" not in head and "cleanup" not in head
    row2 = js[js.index('<div class="review-row">'):js.index("reroll-form")]
    assert row2.index("a.review ||") < row2.index("secondary approve") < row2.index("secondary recut") < row2.index("secondary changes")
    assert "<legend>Cut Sheets</legend>" in js and 'class="small ver-restore"' in js and 'class="small ver-delete"' in js
    ver = (STATIC / "versions.js").read_text()
    assert "ver-pin" not in ver and "/pin`" not in ver and "Stop keeping" not in ver
    assert "Versions ${d.versions.length} " in ver and "ver-cmp-row" in ver
    assert ver.index('<div class="ver-list">') < ver.index('class="row ver-cmp-row"')
    for step in ("Clean up", "Smooth shading", "Remove ground", "✓ cleaned", "✓ smoothed", "✓ ground removed"):
        assert step in ver, step


def test_ready_for_unreal_sits_above_the_build_and_links_to_views():
    html = (STATIC / "index.html").read_text()
    assert html.index('id="ue-ready"') < html.index('id="ue-build-box"')
    ue = (STATIC / "unreal.js").read_text()
    assert 'data-jump="views"' in ue and "renderReadiness()" in ue and '$("#ueb-active").onchange' in ue
    for col in ("Asset", "Version used", "Approved", "Cleanup", "Stand-in reason", "Placement issues"):
        assert f"<th>{col}</th>" in ue, col
    assert 'if (k === "views") showTab("views")' in (STATIC / "jump.js").read_text()


def test_renamed_labels_keep_their_ids_and_values():
    html = _html()
    assert '<button role="tab" data-tab="views">Generate 3D Model &amp; Review</button>' in html          # id "views" stays
    assert "Views and 3D" not in html + "".join((STATIC / f).read_text() for f in ("shell.js", "workflow.js", "review.js", "unreal.js"))
    assert 'views: "Generate 3D Model & Review"' in (STATIC / "shell.js").read_text()
    wf = (STATIC / "workflow.js").read_text()
    assert 'name: "Generate 3D Model & Review"' in wf and '{id: "star", name: "Choose Reference"' in wf
    # 3D mode: friendly names, the TRELLIS mode still the value (and in the tooltip)
    assert '<option value="1024_cascade" title="TRELLIS mode 1024_cascade: detailed, about 2.5 min">Detailed</option>' in html
    assert '<option value="512" title="TRELLIS mode 512: quicker and coarser">Fast Preview</option>' in html
    # quality: the button says cinematic, the stored value stays cine
    rv = (STATIC / "review.js").read_text()
    assert 'const USAGES = ["game", "cine", "hero"];' in rv and "${usageLabel(u)}</button>" in rv and 'data-u="${u}"' in rv
    assert 'const usageLabel = u => u === "cine" ? "cinematic" : u;' in (STATIC / "app.js").read_text()
    assert '<option value="cine" ${a.usage === "cine" ? "selected" : ""}>cinematic</option>' in (STATIC / "plan.js").read_text()


def test_scene_plans_is_an_optional_experimental_side_path():
    html = _html()
    tab = re.search(r'<button role="tab" data-sub="plan"[^>]*>Scene plans</button>', html).group(0)
    assert 'class="tab-optional"' in tab and "disabled" not in tab and "tabindex" not in tab      # still a working tab
    assert re.findall(r'<button role="tab" data-sub="(\w+)"', html) == ["areas", "plan", "assets", "refs"]  # order kept
    body = html[html.index('<div data-sub="plan" id="plan-tab"'):]
    note = ("Experimental: build assets from a single concept image instead of from your city&#x27;s locked shots. "
            "Uses the vision model (local by default; API backends optional).")
    assert body.index(f'<p class="experimental-note" id="plan-experimental" role="note">{note}</p>') < body.index("Pick a scene; the vision model")
    css = (STATIC / "style.css").read_text()
    assert ".experimental-note { color: var(--danger);" in css and "#lib-tabs [role=tab].tab-optional {" in css
    shell = (STATIC / "shell.js").read_text()      # a concept-images project shows it normally
    assert 'classList.toggle("tab-optional", startOf(data.project) !== "assets")' in shell

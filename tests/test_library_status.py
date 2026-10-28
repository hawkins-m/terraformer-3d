"""One definition of where a library asset waits (areas.library_status): the Library step strip, its banner,
Generate 3D Model & Review (list, Show filter, status line, Extract button) and `ap library status` agree.
Synthetic assets, reusing test_sheets_needed's library."""
import json
import shutil
import subprocess

import pytest

from asset_pipeline import areas, cli, kit_proc, review, versions
from asset_pipeline.project import write_json
from asset_pipeline.stages import s1_plan, sw_library
from tests.test_sheets_needed import STATIC, client, store  # noqa: F401 (fixture)


def star(store, asset):
    k = f"refs/library/{asset}/sheet_000.png"
    review.set_star(store, k, True)
    return k


def test_each_asset_waits_at_one_step_and_the_lists_match(store):
    st = areas.library_status(store)
    s = st["steps"]
    assert s["undecided"] == "decide" and s["desc-only"] == "sheets" and s["has-sheets"] == "star"
    assert s["brief-only"] == "build"                       # a brief stands in for the sheet: next is the build
    assert s["built"] == "approve" and s["approved"] is None and s["excluded"] is None
    assert "tree" not in s                                  # Unreal foliage
    assert st["needs_model"] == sorted(a for a, v in s.items() if v == "build")
    assert st["awaiting_approval"] == ["built"]
    assert st["counts"]["needs_model"] == len(st["needs_model"]) and st["counts"]["awaiting_approval"] == 1
    star(store, "has-sheets")
    st = areas.library_status(store)
    assert st["steps"]["has-sheets"] == "build" and "has-sheets" in st["needs_model"]
    assert "has-sheets" in st["needs_views"]                # a starred sheet nothing was cut from


def test_assets_the_next_city_build_does_not_place_are_left_out(store, monkeypatch):
    star(store, "has-sheets")
    gone = {"reason": "building type with no share or slots", "appears": False}
    monkeypatch.setattr(areas, "not_in_layout", lambda s: dict.fromkeys(("has-sheets", "built", "brief-only"), gone))
    st = areas.library_status(store)
    assert st["steps"]["has-sheets"] is None and st["steps"]["built"] is None and st["steps"]["brief-only"] is None
    assert st["outside"]["built"] == "building type with no share or slots"
    assert not set(st["outside"]) & set(st["needs_model"] + st["awaiting_approval"] + st["needs_views"])
    assert st["steps"]["desc-only"] == "sheets" and st["steps"]["undecided"] == "decide"    # earlier steps are unaffected
    assert "has-sheets" not in {a for a in st["needs_views"]}


def test_strip_banner_and_tab_read_the_same_payloads(store, monkeypatch):
    c = client(store)
    star(store, "has-sheets")
    lib = c.get("/api/projects/demo/library").json()["status"]
    assert lib == areas.library_status(store)
    rev = c.get("/api/projects/demo/review").json()
    assert rev["pending_sheets"] == ["refs/library/has-sheets/sheet_000.png"]     # Extract views from N: the needs_views assets' sheets
    monkeypatch.setattr(areas, "not_in_layout", lambda s: {"has-sheets": {"reason": "x", "appears": False}})
    assert c.get("/api/projects/demo/review").json()["pending_sheets"] == []       # not placed: not counted anywhere
    assert c.get("/api/projects/demo/library").json()["status"]["counts"]["needs_views"] == 0


def test_ensure_puts_the_counted_assets_in_the_plan_so_the_tab_lists_them(store):
    c = client(store)
    star(store, "has-sheets")
    ids = lambda: {x["asset"]["id"] for x in c.get("/api/projects/demo/review").json()["assets"]}  # noqa: E731
    assert "has-sheets" not in ids() and "has-sheets" in areas.library_status(store)["needs_model"]
    assert c.post("/api/projects/demo/library/ensure", json={"assets": ["has-sheets", "excluded", "nope"]}).json()["added"] == ["has-sheets"]
    assert "has-sheets" in ids() and "excluded" not in ids()


def test_cut_starred_only_cuts_what_it_is_given(store, monkeypatch):
    from asset_pipeline.stages import s3_views
    a, b = star(store, "has-sheets"), star(store, "orig")
    cut = []
    monkeypatch.setattr(s3_views, "cut", lambda st, key, **kw: cut.append(key) or [])
    monkeypatch.setattr(s3_views, "_unit_for_sheet", lambda st, key, ensure=True: type("U", (), {"kind": "object", "plan": "library", "key": key.split("/")[2]})())
    s3_views.cut_starred(store, log=lambda m: None, only=[b])
    assert cut == [b] and a not in cut


def test_cli_status_prints_each_count_with_names(store, capsys):
    star(store, "has-sheets")
    cli.main(["library", "status", "demo"])
    out = capsys.readouterr().out
    assert "1 Decide" in out and "undecided" in out
    assert "4 Build: need a 3D model: 2\n   brief only (brief-only), has sheets (has-sheets)" in out
    assert "5 Approve: awaiting approval: 1\n   built" in out
    assert "Needs views" in out and "has-sheets" in out


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the tab's own code")
def test_chips_and_show_filter_pick_exactly_the_counted_assets(store, tmp_path, monkeypatch):
    c = client(store)
    star(store, "has-sheets")
    c.post("/api/projects/demo/library/ensure", json={"assets": ["has-sheets", "built"]})
    d = tmp_path / "api"
    d.mkdir()
    for k in ("library", "refs", "review"):
        (d / f"{k}.json").write_text(json.dumps(c.get(f"/api/projects/demo/{k}").json()))
    harness = r'''
const fs = require("fs"), vm = require("vm");
const L = n => JSON.parse(fs.readFileSync(`${process.argv[2]}/${n}.json`, "utf8"));
const rd = fs.readFileSync(process.argv[3] + "/review.js", "utf8"), wf = fs.readFileSync(process.argv[3] + "/workflow.js", "utf8");
const grab = (s, a, b) => { const i = s.indexOf(a); return s.slice(i, s.indexOf(b, i)); };
const ctx = {libraryData: L("library"), reviewData: L("review"), assetName: x => x};
vm.createContext(ctx);
vm.runInContext(grab(wf, "function libStepOf()", "function libSteps()") + "\n" + grab(rd, "let viewsStepMap", "let viewsFilter"), ctx);
const f = k => vm.runInContext(`reviewData.assets.filter(VIEWS_FILTERS.${k}[1]).map(a => a.asset.id).sort()`, ctx);
console.log(JSON.stringify({build: f("build"), approve: f("approve"), labels: vm.runInContext("Object.values(VIEWS_FILTERS).map(v => v[0])", ctx)}));
'''
    (tmp_path / "f.js").write_text(harness)
    out = json.loads(subprocess.run(["node", str(tmp_path / "f.js"), str(d), str(STATIC)], capture_output=True, text=True, check=True).stdout)
    st = areas.library_status(store)
    in_plan = {x["asset"]["id"] for x in json.loads((d / "review.json").read_text())["assets"]}
    assert out["build"] == sorted(set(st["needs_model"]) & in_plan) and "has-sheets" in out["build"]
    assert out["approve"] == ["built"] == st["awaiting_approval"]
    assert "Awaiting approval" in out["labels"]


def test_each_reason_for_not_in_the_layout_and_whether_it_appears(store, monkeypatch, capsys):
    from asset_pipeline import next_build
    d = sw_library.decisions(store)
    d["built"] = d["built"] | {"children": [{"entry": "approved"}]}                 # approved is attached to built
    write_json(sw_library.decisions_path(store), d)
    rel = next_build._relations(store, sw_library.decisions(store))
    dec = sw_library.decisions(store)
    ent = {"kind": "building"}
    assert next_build.outside_reason("approved", ent, dec["approved"], rel) == ("attached to built", True)
    assert next_build.outside_reason("orig-copy", ent, dec["orig-copy"], rel) == ("variant of orig", True)
    assert next_build.outside_reason("set-member", ent, dec["set-member"], rel) == ("variant of set-base", True)
    assert next_build.outside_reason("kit-entry", ent, dec["kit-entry"], rel)[1] is True
    assert next_build.outside_reason("desc-only", ent, dec["desc-only"], rel) == ("building type with no share or slots", False)
    assert next_build.outside_reason("desc-prop", {"kind": "prop"}, dec["desc-prop"], rel) == ("prop with no share or slots", False)
    assert next_build.outside_reason("excluded", ent, dec["excluded"], rel) == ("excluded", False)


def test_appearing_assets_are_listed_but_still_counted(store, monkeypatch, capsys):
    star(store, "has-sheets")
    monkeypatch.setattr(areas, "not_in_layout", lambda s: {
        "has-sheets": {"reason": "variant of orig", "appears": True},
        "built": {"reason": "building type with no share or slots", "appears": False},
        "kit-entry": {"reason": "a facade kit: its modules stand on buildings", "appears": True}})
    st = areas.library_status(store)
    assert st["steps"]["has-sheets"] == "build" and st["steps"]["built"] is None           # appears: counted; never: left out
    assert set(st["outside"]) == {"built"}
    nl = st["not_in_layout"]
    assert nl["has-sheets"] == {"reason": "variant of orig", "appears": True}
    assert nl["kit-entry-bay"]["reason"] == "kit module of kit-entry" and nl["kit-entry-bay"]["appears"]
    cli.main(["library", "status", "demo"])
    out = capsys.readouterr().out
    assert "has sheets (has-sheets): variant of orig (appears in the city)" in out
    assert "built: building type with no share or slots (never appears)" in out
    assert "kit entry bay (kit-entry-bay): kit module of kit-entry (appears in the city)" in out


def test_explain_gives_the_ready_for_unreal_panel_the_reason(store):
    from asset_pipeline import next_build
    from asset_pipeline.stages import sw_site
    write_json(sw_site.site_dir(store) / "greybox.json", {"slots": []})
    rows = {r["entry"]: r for r in next_build.explain(store)["assets"]}
    r = rows["desc-only"]
    assert r["role"] == "none" and r["appears"] is False and r["outside_reason"] == "building type with no share or slots"
    assert "building type with no share or slots" in r["stand_in"] and r["line"].endswith(")")
    assert rows["orig-copy"]["appears"] is True and rows["orig-copy"]["outside_reason"] == "variant of orig"

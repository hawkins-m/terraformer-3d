"""One definition of "needs a reference sheet" (areas.sheet_skip): the Library step strip's 2 Sheets,
the sheets batch's Make sheets for all, the Reference sheets tab's No sheets yet and Generate missing
all agree. Synthetic assets, one per case."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import areas, kit_proc, library_review
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, read_json, write_json
from asset_pipeline.stages import s1_plan, s2_refs, sw_library, sw_site
from asset_pipeline.ui import app as ui_app

BRIEF = {"kind": "building", "form": "cube", "dims": [20.0, 20.0, 12.0], "params": {}, "features": {}, "measured": {}}
STATIC = Path(ui_app.STATIC)


def ent(eid, kind="building", route="procedural", best=None, **kw):
    return {"id": eid, "tier": "B", "kind": kind, "part": eid.replace("-", "_"), "instances": 1, "slot_ids": [],
            "districts": {}, "district": "north", "route_auto": route, "description_auto": f"A {eid.replace('-', ' ')}.",
            "best": best, "share": 0.1 if best else 0.0, "sources": [], "flags": [], "mood": [], "size_m": [10, 10, 10], **kw}


# asset -> why it needs no sheet (None: it needs one). One per case.
EXPECTED = {
    "desc-only": None,                    # Description only, no shot frame, nothing else: needs one (drawn from the description)
    "desc-prop": None,                    # ... a TRELLIS prop too
    "src-framed": None,                   # a good-source decision a frame shows
    "trellis-brief": None,                # a leftover brief means nothing on a TRELLIS asset
    "kit-entry-corner": None,             # a kit's module without a sheet (counted per module, not per entry)
    "set-base": None, "set-member": None,    # a variant set's members each have their own sheets
    "excl-copy": None,                    # a copy whose original is excluded stands alone
    "ref-unplanned": None,                # decided, never included in the plan: still counted (and listed)
    "kit-entry-bay": "sheets",            # the module that has sheets
    "has-sheets": "sheets",               # decided before the plan included it, sheets on disk
    "brief-only": "brief",                # a procedural brief stands in for a sheet
    "built": "built",                     # already built
    "approved": "approved",
    "orig-copy": "copy",                  # a copy rides on its decided original's sheet
    "src-noframe": "no frame",            # good source, no frame shows it
    "excluded": "excluded",
    "undecided": "undecided",
    "orig": "sheets", "excl-orig": "excluded",        # the originals the two copies point at
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    s = ProjectStore.create("demo")
    ents = [ent("desc-only"), ent("desc-prop", kind="prop", route="trellis"), ent("src-framed", best="frames/a.png"),
            ent("src-noframe"), ent("has-sheets"), ent("brief-only"), ent("trellis-brief", route="trellis"),
            ent("built"), ent("approved"), ent("excluded"), ent("undecided"), ent("kit-entry"), ent("orig"),
            ent("set-base"), ent("set-member"), ent("excl-orig"), ent("ref-unplanned"), ent("tree", kind="vegetation")]
    write_json(sw_library.library_path(s), {"min_share": 0.0005, "sources": {}, "mood": {}, "candidates": [], "checked": True,
                                            "entries": [e for e in ents if e["best"]], "unseen": [e for e in ents if not e["best"]],
                                            "surfaces": {}})
    sw_library.save_custom(s, {  # copies: duplicate_of an original, a variant
        "orig-copy": ent("orig-copy", custom=True, duplicate_of="orig", variant=True),
        "excl-copy": ent("excl-copy", custom=True, duplicate_of="excl-orig", variant=True)})
    ref = {"decision": "reference", "frame": "frames/r.png"}
    d = {"desc-only": {"decision": "description"}, "desc-prop": {"decision": "description"},
         "src-framed": {"decision": "source"}, "src-noframe": {"decision": "source"},
         "has-sheets": ref, "brief-only": ref, "trellis-brief": ref | {"route": "trellis"}, "built": ref,
         "approved": {"decision": "description"}, "excluded": {"decision": "exclude"},
         "kit-entry": {"decision": "kit", "route": "procedural", "pieces": {
             "bay": {"size": [4, 4, 6], "description": "A bay."}, "corner": {"size": [4, 4, 6], "description": "A corner."}}},
         "orig": ref, "orig-copy": {"decision": "description"}, "excl-orig": {"decision": "exclude"},
         "excl-copy": {"decision": "description"}, "ref-unplanned": ref,
         "set-base": {"decision": "description"}, "set-member": {"decision": "description"},
         "tree": {"decision": "description"},
         "_variants": {"set-base": {"members": {"set-base": 1.0, "set-member": 1.0}, "areas": [], "seed": 0}}}
    write_json(sw_library.decisions_path(s), d)
    for a in ("kit-entry-bay", "has-sheets", "orig"):                      # sheets on disk
        (s.root / "refs" / "library" / a).mkdir(parents=True)
        (s.root / "refs" / "library" / a / "sheet_000.png").write_bytes(b"x")
    write_json(kit_proc.spec_path(s, "brief-only"), BRIEF)
    write_json(kit_proc.spec_path(s, "trellis-brief"), BRIEF)
    (kit_proc.out_dir(s, "built") / "procedural.glb").parent.mkdir(parents=True, exist_ok=True)
    (kit_proc.out_dir(s, "built") / "procedural.glb").write_bytes(b"x")
    monkeypatch.setattr(library_review, "entry_of", lambda store, asset: None)
    library_review.set_status(s, "approved", "approved", "")
    # the plan: some assets included, the rest decided but never included (ref-unplanned, has-sheets, ...)
    sw_library.asset_plan(s, only=["desc-prop", "src-framed", "kit-entry", "orig"])
    return s


def test_every_case_has_one_verdict(store):
    states = areas.sheet_states(store)
    assert states == EXPECTED
    assert "tree" not in states                                         # Unreal foliage: no sheets
    assert set(areas.SHEET_SKIP) >= {v for v in EXPECTED.values() if v}


def test_a_description_only_asset_with_no_frame_needs_a_sheet_drawn_from_its_description(store):
    r = next(r for r in areas.status(store, everywhere=True) if r["asset"] == "desc-only")
    assert r["decision"] == "description" and areas.sheet_skip(r) is None and "make sheets" in r["next"]
    u = next(u for u in s2_refs.units(store, pending=True) if u.key == "desc-only")
    assert u.pending and u.assets[0].refs == []
    prompt = s2_refs.request(s2_refs.current(store, u), None)[0]
    assert "A desc only." in prompt                                       # its description, nothing else to go on


def client(store):
    return TestClient(ui_app.create_app(JobQueue()))


def need_set():
    return sorted(a for a, why in EXPECTED.items() if why is None)


def test_banner_tab_and_library_payload_count_the_same_assets(store):
    c = client(store)
    plan = c.post("/api/projects/demo/areas/batch", json={"action": "sheets", "everywhere": True, "dry_run": True}).json()
    assert plan["items"] == need_set() and plan["count"] == len(need_set())    # Make sheets for all (N): per asset
    refs = c.get("/api/projects/demo/refs").json()
    assert sorted(u["key"] for u in refs if u["needs_sheet"]) == need_set()    # No sheets yet (N) / Generate missing (N)
    lib = c.get("/api/projects/demo/library").json()["sheets"]
    assert lib["needed"] == need_set()                                         # what the step strip counts
    assert {a: w for a, w in lib["skip"].items()} == {a: w for a, w in EXPECTED.items() if w}
    by = {u["key"]: u for u in refs}
    assert by["desc-only"]["pending"] and by["ref-unplanned"]["pending"]       # listed though not in the plan yet
    assert by["has-sheets"]["pending"] and not by["has-sheets"]["needs_sheet"]  # its sheets are listed, none missing
    assert not by["desc-prop"]["pending"] and by["desc-prop"]["needs_sheet"]
    for gone in ("excluded", "undecided", "src-noframe", "orig-copy", "brief-only", "built", "approved"):
        assert not by.get(gone, {}).get("needs_sheet")                         # never counted
    notes = " | ".join(plan["notes"])
    assert "no shot frame shows it" in notes and "a copy shares its original's sheet" in notes


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the strip's own code")
def test_the_step_strip_counts_the_same_assets(store, tmp_path):
    c = client(store)
    d = tmp_path / "api"
    d.mkdir()
    for k in ("library", "refs", "review"):
        (d / f"{k}.json").write_text(json.dumps(c.get(f"/api/projects/demo/{k}").json()))
    harness = r'''
const fs = require("fs"), vm = require("vm");
const L = n => JSON.parse(fs.readFileSync(`${process.argv[2]}/${n}.json`, "utf8"));
const src = fs.readFileSync(process.argv[3] + "/workflow.js", "utf8"), lib = fs.readFileSync(process.argv[3] + "/library.js", "utf8");
const grab = (s, a, b) => { const i = s.indexOf(a); return s.slice(i, s.indexOf(b, i)); };
const ctx = {libraryData: L("library"), refs: L("refs"), reviewData: L("review"), data: {stars: {}}};
vm.createContext(ctx);
vm.runInContext(grab(lib, "function libEntries()", "// a library asset's label") + "\n" + grab(lib, "const libDecision", "const LIB_FILTERS") +
  "\n" + grab(src, "function libStepOf()", "function libSteps()"), ctx);
console.log(JSON.stringify(vm.runInContext("libStepOf()", ctx)));
'''
    (tmp_path / "strip.js").write_text(harness)
    out = subprocess.run(["node", str(tmp_path / "strip.js"), str(d), str(STATIC)], capture_output=True, text=True, check=True).stdout
    step = json.loads(out)
    assert sorted(a for a, s in step.items() if s == "sheets") == need_set()
    assert step["has-sheets"] == "star" and step["brief-only"] == "build"          # the others carry on to their own step
    assert step["orig-copy"] is None and step["src-noframe"] is None and step["undecided"] == "decide"
    assert step["excluded"] is None and "tree" not in step


def test_generate_missing_makes_sheets_for_exactly_those_and_includes_them_in_the_plan(store, monkeypatch):
    made = []

    def fake(store_, unit, n=2, **kw):
        made.append((unit.key, unit.pending))
        d = unit.dir(store_)
        d.mkdir(parents=True, exist_ok=True)
        (d / "sheet_000.png").write_bytes(b"x")
        return [d / "sheet_000.png"]
    monkeypatch.setattr(s2_refs, "generate", fake)
    out = s2_refs.generate_missing(store, n=2, log=lambda *a: None)
    assert sorted(k for k, _ in made) == need_set() and len(out) == len(need_set())
    assert dict(made)["desc-only"] is True and dict(made)["desc-prop"] is False       # the pending ones, and an included one
    plan = s1_plan.load(store, sw_library.PLAN_NAME)
    included = {a.id for a in plan.assets if a.include}
    assert set(need_set()) <= included and "excluded" not in included and "undecided" not in included
    assert areas.sheet_states(store)["desc-only"] == "sheets"                          # no longer needs one
    assert s2_refs.missing_units(store) == []
    assert s2_refs.generate_missing(store, n=2, log=lambda *a: None) == []              # nothing left: the three counts are 0


def test_a_pending_unit_can_be_generated_on_its_own_through_the_api(store, monkeypatch):
    made = []
    monkeypatch.setattr(s2_refs, "generate", lambda st, unit, n=2, **kw: made.append(unit.key) or [])
    monkeypatch.setattr(ui_app, "_on_comfy", lambda *a, **k: False, raising=False)
    c = client(store)
    r = c.post("/api/projects/demo/refs/generate", json={"plan": "library", "unit": "desc-only", "n": 2})
    assert r.status_code == 200, r.text
    import time
    for _ in range(200):
        if c.get(f"/api/jobs/{r.json()['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    job = c.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "done", job
    assert made == ["desc-only"]
    included = {a.id for a in s1_plan.load(store, sw_library.PLAN_NAME).assets if a.include}
    assert "desc-only" in included                                  # it joined the library plan
    assert c.post("/api/projects/demo/refs/generate", json={"plan": "library", "unit": "excluded", "n": 2}).status_code == 404


def test_the_batch_skips_a_copy_and_an_asset_with_a_brief_but_not_a_decided_description_only_one(store):
    c = client(store)
    plan = c.post("/api/projects/demo/areas/batch", json={"action": "sheets", "everywhere": True, "dry_run": True,
                                                         "assets": ["desc-only", "orig-copy", "brief-only"]}).json()
    assert plan["items"] == ["desc-only"]

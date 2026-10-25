"""Duplicated library entries: shown as copies of their original, deleted to the trash, and used as
variants that share the original's placements (variants.py, city_library). Synthetic project: one
prop entry, two duplicates, 20 placements in two market slots."""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from asset_pipeline import areas, city_library as cl, kit_proc, library_entries as le, variants, versions
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import read_json, write_json
from asset_pipeline.stages import s1_plan, sw_library, sw_shots, sw_site
from asset_pipeline.ui import app as ui_app

from tests.test_assembly import glb_box
from tests.test_library_entries import _entry, proj  # noqa: F401  (fixture)


def _canopy(i):
    return {"name": f"market.canopy.{i:03d}", "piece": "market:canopy", "mesh": "unit",
            "matrix_local": [[1, 0, 0, 4.0 * i], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}


def _trellis(store, eid):
    out = kit_proc.out_dir(store, eid)
    glb_box(out / "a_1024_cascade_s1.glb", [-2, -1.5, 0], [2, 1.5, 2.7])
    write_json(out / "a_1024_cascade_s1.json", {"seed": 1})
    versions.record_trellis(store, "library", eid, {"glb": f"3d/library/{eid}/a_1024_cascade_s1.glb", "input": "x",
                                                    "seed": 1, "mode": "1024_cascade"})


@pytest.fixture
def market(proj):            # noqa: F811
    slots = [{"id": f"market-{k}", "type": "market", "district": "core", "typology": None, "bbox": [[0, 0, 0], [80, 10, 5]],
              "matrix": [[1, 0, 0, 200.0 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
              "pieces": [_canopy(i) for i in range(n)]} for k, n in ((0, 12), (1, 8))]
    write_json(sw_site.site_dir(proj) / "greybox.json", {"slots": slots, "shots": [{"id": "med-a"}]})
    write_json(sw_shots.shot_dir(proj, "med-a") / "ids.json", {"slots": {"market-0": {"frac": 0.3}}})
    write_json(sw_library.library_path(proj), {
        "min_share": 0.005, "sources": {"med-a": ["frames/med-a/f1.png"]}, "mood": {}, "candidates": [], "checked": True,
        "entries": [_entry("canopy-table", instances=20, slot_ids=["market-0", "market-1"], best="frames/med-a/f1.png",
                           share=0.02, flags=["approximate: whole slot"], sources=[{"shot": "med-a", "share": 0.3}])],
        "unseen": [], "surfaces": {}})
    sw_site._greybox_memo.d = None
    crop = sw_site.site_dir(proj) / "library" / "refs" / "canopy-table.jpg"
    crop.parent.mkdir(parents=True, exist_ok=True)
    crop.write_bytes(b"jpg")
    sw_library.decide(proj, "canopy-table", "source")
    areas.define(proj, "west", shots=["med-a"])
    le.duplicate(proj, "canopy-table", "canopy-table-02")
    le.duplicate(proj, "canopy-table", "canopy-table-03")
    return proj


def test_duplicates_are_copies_with_no_shot_frame_in_their_originals_area(market):
    data = sw_library.load(market)
    dup = next(u for u in data["unseen"] if u["id"] == "canopy-table-02")
    assert dup["duplicate_of"] == "canopy-table" and dup["best"] is None and dup["flags"] == [] and dup["variant"]
    d = sw_library.decisions(market)["canopy-table-02"]
    # a frame-crop decision becomes "description + chosen image" with the original's crop
    assert d["decision"] == "reference" and d["frame"] == "site/library/refs/canopy-table-02.jpg"
    m = areas.members(market, "west")
    assert m["canopy-table-02"] == m["canopy-table-03"] == m["canopy-table"]
    a = next(x for x in s1_plan.load(market, "library").assets if x.id == "canopy-table-02")
    assert a.refs == ["site/library/refs/canopy-table-02.jpg"]


def test_variants_split_the_placements_evenly_stably_and_only_once_built(market):
    gb = sw_site.load_greybox(market)
    assert len(variants.placement_keys(market, "canopy-table", gb)) == 20
    assert variants.splits(market)["canopy-table"]["counts"] == {"canopy-table": 20}    # no builds yet
    _trellis(market, "canopy-table")
    _trellis(market, "canopy-table-02")                       # 03 has no Active version: not placed
    s = variants.splits(market)["canopy-table"]
    assert s["placements"] == 20 and s["counts"] == {"canopy-table": 10, "canopy-table-02": 10}
    assert s["waiting"] == ["canopy-table-03"]
    a1 = variants.assignments(market, gb)
    assert a1 == variants.assignments(market, gb)             # the same on every build
    props, _, missing = cl.prop_placements(market, gb, a1)
    assert not missing and len(props) == 20
    assert Counter(p["entry"] for p in props) == {"canopy-table": 10, "canopy-table-02": 10}
    v = next(p for p in props if p["entry"] == "canopy-table-02")
    assert v["variant_of"] == "canopy-table" and v["mesh"] == "prop_canopy-table-02" and "canopy-table-02" in v["glb"]
    assert {p["key"]: p["entry"] for p in props} == a1["canopy-table"]
    # placements of both slots get both entries (not one slot each)
    assert {p["entry"] for p in props if p["slot"] == "market-0"} == {"canopy-table", "canopy-table-02"}
    _trellis(market, "canopy-table-03")
    s = variants.splits(market)["canopy-table"]
    assert sorted(s["counts"].values()) == [6, 7, 7] and not s["waiting"]
    # turned off: the original takes its places back
    variants.set_variant(market, "canopy-table-03", False)
    assert variants.splits(market)["canopy-table"]["counts"] == {"canopy-table": 10, "canopy-table-02": 10}
    assert variants.assignments(market, gb) == a1
    with pytest.raises(ValueError, match="not a duplicate"):
        variants.set_variant(market, "canopy-table", True)


def test_kit_slot_placements_take_the_variants_procedural_build(market):
    for e in ("canopy-table", "canopy-table-02"):
        glb_box(kit_proc.out_dir(market, e) / "procedural.glb", [-2, -1, 0], [2, 1, 3])
        write_json(kit_proc.out_dir(market, e) / "procedural" / "job.json", {"materials": {}})
        write_json(kit_proc.spec_path(market, e), {"kind": "building", "dims": [4 + (e[-1] == "2"), 2, 3]})
    assign = {"canopy-table": {"canopymtable_market-0": "canopy-table", "canopymtable_market-1": "canopy-table-02"}}
    ps = [{"key": f"canopymtable_market-{k}", "entry": "canopy-table", "glb": "3d/library/canopy-table/procedural.glb",
           "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "w": 4, "d": 2, "slot": f"market-{k}"} for k in (0, 1)]
    entries = {}
    cl.apply_variants(market, ps, assign, entries)
    assert ps[0]["entry"] == "canopy-table" and "variant_of" not in ps[0]
    assert ps[1]["entry"] == "canopy-table-02" and ps[1]["variant_of"] == "canopy-table"
    assert ps[1]["glb"] == "3d/library/canopy-table-02/procedural.glb" and ps[1]["w"] == 5
    assert entries == {"canopy-table-02": "3d/library/canopy-table-02/procedural/job.json"}


def test_delete_moves_a_user_entrys_files_to_the_trash(market):
    _trellis(market, "canopy-table-03")
    sheet = market.root / "refs/library/canopy-table-03/sheet_000.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_bytes(b"png")
    le.duplicate(market, "canopy-table-03", "canopy-table-04")          # a duplicate of it keeps its own files
    info = le.delete_info(market, "canopy-table-03")
    a = info["assets"][0]
    assert a["sheets"] == 1 and a["versions"] == 1 and a["trellis"] == 1
    assert info["used_by"]["duplicates"] == ["canopy-table-04"]
    r = le.delete(market, "canopy-table-03")
    assert not (market.root / "3d/library/canopy-table-03").exists() and not sheet.exists()
    bin_ = market.root / r["trash"]
    assert bin_.parent.name == "trash" and (bin_ / "refs/library/canopy-table-03/sheet_000.png").is_file()
    assert (bin_ / "3d/library/canopy-table-03/a_1024_cascade_s1.glb").is_file()
    assert read_json(bin_ / "entry.json")["entry"]["duplicate_of"] == "canopy-table"
    assert "canopy-table-03" not in sw_library.custom(market) and "canopy-table-03" not in sw_library.decisions(market)
    assert "canopy-table-03" not in {x.id for x in s1_plan.load(market, "library").assets}
    four = sw_library.custom(market)["canopy-table-04"]
    assert four["duplicate_of"] == "canopy-table"                       # re-linked to the original
    assert (market.root / "3d/library/canopy-table-04/a_1024_cascade_s1.glb").is_file()
    with pytest.raises(ValueError, match="exclude it instead"):
        le.delete_info(market, "canopy-table")


def test_api_carries_splits_variants_and_delete(market):
    _trellis(market, "canopy-table")
    _trellis(market, "canopy-table-02")
    c = TestClient(ui_app.create_app(JobQueue()))
    d = c.get("/api/projects/p/library").json()
    assert d["variant_of"] == {"canopy-table-02": "canopy-table", "canopy-table-03": "canopy-table"}
    assert d["splits"]["canopy-table"]["counts"] == {"canopy-table": 10, "canopy-table-02": 10}
    assert c.get("/api/projects/p/library/entries/canopy-table-02/variant").json()["original"] == "canopy-table"
    r = c.post("/api/projects/p/library/entries/canopy-table-02/variant", json={"on": False})
    assert r.status_code == 200 and r.json()["variant"] is False
    assert c.get("/api/projects/p/library/entries/canopy-table/delete").status_code == 400
    assert c.get("/api/projects/p/library/entries/canopy-table-03/delete").json()["trash"] == "trash/"
    assert c.post("/api/projects/p/library/entries/canopy-table-03/delete").status_code == 200
    assert "canopy-table-03" not in c.get("/api/projects/p/library").json()["variant_of"]


def test_deal_is_even_whatever_the_input_order():
    keys = [f"k{i}" for i in range(21)]
    a = variants.deal("o", keys, ["o", "v1", "v2"])
    assert Counter(a.values()) == {"o": 7, "v1": 7, "v2": 7}
    assert a == variants.deal("o", list(reversed(keys)), ["o", "v1", "v2"])

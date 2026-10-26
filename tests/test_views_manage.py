"""Views and 3D: the row follows the entry's build method; cut views can be deleted (to the trash)
or re-cut from the asset's starred sheet, whole or in views; odd cuts get a note. Synthetic only."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from asset_pipeline import review, segment
from asset_pipeline.jobs import JobQueue
from asset_pipeline.project import ProjectStore, write_json
from asset_pipeline.stages import s3_views, sw_library, sw_site
from asset_pipeline.ui import app as ui_app

from test_stage345 import NAME, env  # noqa: F401  (the stage tests' kit sheet)
from test_ui_layout import _entry

A = "long-quay"
SHEET = f"refs/library/{A}/sheet_000.png"
W, H = 300, 100


def _det(x0, x1, y0=40, y1=90):
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return segment.Detection(mask=m, bbox=segment._bbox(m), area_frac=float(m.mean()), touches_border=False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """One library entry (procedural, no form) with one starred sheet: three dark blocks on white."""
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    st = ProjectStore.create("p")
    e = _entry(A, "harbour") | {"kind": "building", "tier": "B", "route_auto": "procedural"}
    write_json(sw_site.site_dir(st) / "library.json", {"min_share": 0.005, "sources": {}, "mood": {}, "candidates": [],
                                                       "checked": True, "entries": [], "unseen": [e], "surfaces": {}})
    sw_library.decide(st, A, "description")
    px = np.full((H, W, 3), 255, np.uint8)
    for x0, x1 in ((10, 60), (120, 170), (230, 290)):
        px[40:90, x0:x1] = (90, 80, 70)
    (st.root / SHEET).parent.mkdir(parents=True)
    Image.fromarray(px).save(st.root / SHEET)
    write_json(st.root / f"refs/library/{A}/meta.json", {"plan": "library", "unit": A, "kind": "object", "assets": [A], "sheets": []})
    sw_library.asset_plan(st)
    review.set_star(st, SHEET)
    monkeypatch.setattr(segment, "detect", lambda c, img, noun, **kw: [_det(10, 60), _det(120, 170), _det(230, 290)])
    return st


def _row(client):
    return next(a for a in client.get("/api/projects/p/review").json()["assets"] if a["asset"]["id"] == A)


def _wait(client, job):
    end = time.time() + 10
    while client.get(f"/api/jobs/{job['id']}").json()["status"] not in ("done", "error") and time.time() < end:
        time.sleep(0.05)
    return client.get(f"/api/jobs/{job['id']}").json()


def test_row_follows_the_build_method(store):
    c = TestClient(ui_app.create_app(JobQueue()))
    a = _row(c)
    assert a["procedural"] and a["next"]["route"] == "procedural" and a["next"]["form"]["form"] is None   # Choose form…
    assert a["starred"] == [{"sheet": SHEET, "kind": "object"}] and a["views"] == []
    assert c.post("/api/projects/p/library/route", json={"entry": A, "route": "trellis"}).status_code == 200
    a = _row(c)                                     # switched in Manage entry: the next poll has it
    assert not a["procedural"] and a["next"]["route"] == "trellis"
    assert c.post("/api/projects/p/library/route", json={"entry": A, "route": None}).status_code == 200
    assert _row(c)["procedural"]


def test_delete_view_goes_to_trash_and_never_the_chosen_one(store):
    views = s3_views.cut(store, SHEET, client=object())
    keys = [f"views/library/{A}/{v['file']}" for v in views]
    review.choose(store, "library", A, keys[0])
    c = TestClient(ui_app.create_app(JobQueue()))
    r = c.post("/api/projects/p/views/delete", json={"key": keys[0]})
    assert r.status_code == 400 and "pick another view first" in r.json()["detail"]
    r = c.post("/api/projects/p/views/delete", json={"key": keys[1]}).json()
    assert r["deleted"] == keys[1] and r["trash"].startswith("trash/")
    assert not (store.root / keys[1]).exists() and (store.root / r["trash"] / keys[1]).is_file()
    assert [v["key"] for v in _row(c)["views"]] == [keys[0], keys[2]]
    assert c.post("/api/projects/p/views/delete", json={"key": SHEET}).status_code == 400     # not a view
    assert c.post("/api/projects/p/views/delete", json={"key": "../x.png"}).status_code == 400


def test_recut_whole_sheet_replaces_the_views_and_trashes_the_old(store):
    old = s3_views.cut(store, SHEET, client=object())
    review.choose(store, "library", A, f"views/library/{A}/{old[0]['file']}")
    c = TestClient(ui_app.create_app(JobQueue()))
    job = c.post("/api/projects/p/views/recut", json={"plan": "library", "asset": A, "whole": True}).json()
    assert job["lane"] == "cpu" and job["tag"] == {"sheet": SHEET, "asset": A, "recut": True}     # no SAM: no ComfyUI
    assert _wait(c, job)["status"] == "done"
    a = _row(c)
    assert [(v["position"], v["method"], v["asked"], v["odd"]) for v in a["views"]] == [(1, "whole", None, None)]
    assert a["chosen"] is None                      # the chosen view was replaced: pick again
    im = Image.open(store.root / a["views"][0]["key"])
    assert im.width == im.height and im.width > 280     # all three blocks in one padded square
    trashed = sorted(p.name for p in (store.root / "trash").rglob("*.png"))
    assert trashed == [f"sheet_000_v{i}.png" for i in (1, 2, 3)]
    # and back to three views (SAM on a ComfyUI lane)
    job = c.post("/api/projects/p/views/recut", json={"plan": "library", "asset": A}).json()
    assert job["lane"] == "comfy"


def test_recut_needs_a_starred_sheet(store):
    review.set_star(store, SHEET, False)
    c = TestClient(ui_app.create_app(JobQueue()))
    r = c.post("/api/projects/p/views/recut", json={"plan": "library", "asset": A})
    assert r.status_code == 400 and "no starred sheet" in r.json()["detail"]
    with pytest.raises(ValueError):
        s3_views.recut(store, "library", A)


def test_odd_cuts_get_a_note(store, monkeypatch):
    # a sliced view (a strip of the middle block) among two square-ish ones
    monkeypatch.setattr(segment, "detect", lambda c, img, noun, **kw: [_det(10, 60), _det(120, 135), _det(230, 290)])
    s3_views.cut(store, SHEET, client=object())
    notes = [v["odd"] for v in s3_views.views(store, "library", A)]
    assert notes[0] is None and notes[2] is None and "sliced" in notes[1]
    # a sliver, or one much wider than its siblings (two merged)
    sib = [{"bbox": [0, 0, 50, 50]}, {"bbox": [0, 0, 60, 50]}]
    assert "thin" in s3_views.odd_note({"bbox": [0, 0, 8, 60], "method": "white"}, []).lower()
    assert "sliced" in s3_views.odd_note({"bbox": [0, 0, 200, 50], "method": "sam"}, sib)
    assert s3_views.odd_note({"bbox": [0, 0, 280, 40], "method": "whole"}, []) is None   # cut whole on purpose
    assert s3_views.odd_note(sib[0], [sib[1]]) is None


def test_kit_sheet_cannot_be_cut_whole(env):
    with pytest.raises(ValueError, match="object sheet"):
        s3_views.cut(env, f"refs/{NAME}/kit-wall-kit/sheet_000.png", whole=True)

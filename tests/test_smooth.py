"""Smooth shading and ground removal for TRELLIS models (asset_pipeline/smooth.py): an existing version
fixed as a new version that takes over as Active, "no ground found" recorded without a version, the
batch over every TRELLIS model in use, the post-process switches, and (with Blender) the weld and
smooth-by-angle and the slab cut on real GLBs (a slab goes, a podium stays)."""
import json
import shutil
import struct

import numpy as np
import pytest

from asset_pipeline import blender, config, smooth, versions
from asset_pipeline.project import ProjectStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "root"))
    st = ProjectStore.create("smooth-demo")
    d = st.root / "3d" / "library" / "dome"
    d.mkdir(parents=True)
    for stem in ("view_1024_cascade_s7",):
        (d / f"{stem}.glb").write_bytes(b"glTF-fake")
        (d / f"{stem}.json").write_text(json.dumps({"glb_faces": 10, "seed": 7}))
        (d / f"{stem}_input.png").write_bytes(b"png")
    versions.record_trellis(st, versions.PLAN, "dome", {"view": None, "mode": "1024_cascade", "seed": 7,
                                                        "glb": "3d/library/dome/view_1024_cascade_s7.glb",
                                                        "input": "3d/library/dome/view_1024_cascade_s7_input.png"})
    return st


def fake_blender(monkeypatch, calls, removed=1234):
    def run(src, dst, angle_deg=None, ground=None, log_path=None):
        calls.append((src.name, angle_deg, ground is not None))
        shutil.copyfile(src, dst)
        rep = {"angle": angle_deg, "welded": 5, "sharp_edges": 3, "seconds": 0.1}
        if ground is not None:
            rep["ground"] = {"removed_faces": removed} if removed else {"removed_faces": 0, "reason": "none"}
        return rep
    monkeypatch.setattr(smooth, "fix_glb", run)


def test_a_version_is_smoothed_as_a_new_active_version(store, monkeypatch):
    calls = []
    fake_blender(monkeypatch, calls)
    assert versions.current(store, "dome") == 1 and smooth.candidates(store) == ["dome"]
    r = smooth.smooth_version(store, "dome")
    assert (r["from"], r["n"]) == (1, 2) and calls == [("view_1024_cascade_s7.glb", 30.0, False)]
    assert versions.get(store, "dome", 2)["files"][0].endswith("view_1024_cascade_s7_smooth.glb")
    assert versions.current(store, "dome") == 2                        # took over as Active
    row = versions.get(store, "dome", 2)
    assert row["kind"] == "trellis" and "smooth shading" in row["note"] and row["source"]["seed"] == 7
    assert all((store.root / f).is_file() for f in row["files"]) and len(row["files"]) == 3
    stats = json.loads((store.root / "3d/library/dome/view_1024_cascade_s7_smooth.json").read_text())
    assert stats["smoothed"]["from_version"] == 1 and stats["glb_faces"] == 10
    assert versions.get(store, "dome", 1)["files"]                     # the original stays to go back to
    assert smooth.candidates(store) == []
    with pytest.raises(ValueError, match="smoothed already"):
        smooth.smooth_version(store, "dome", 2)
    listing = {v["n"]: v["smoothable"] for v in versions.listing(store, "dome")["versions"]}
    assert listing == {1: True, 2: False}
    rep = smooth.smooth_all(store, log=lambda *a: None)                 # nothing left to do
    assert rep == {"done": [], "failed": {}}


def test_smoothing_an_older_version_leaves_the_active_one(store, monkeypatch):
    fake_blender(monkeypatch, [])
    d = store.root / "3d/library/dome"
    for ext in (".glb", ".json", "_input.png"):
        shutil.copyfile(d / f"view_1024_cascade_s7{ext}", d / f"view_1024_cascade_s8{ext}")
    versions.record_trellis(store, versions.PLAN, "dome", {"view": None, "mode": "1024_cascade", "seed": 8,
                                                           "glb": "3d/library/dome/view_1024_cascade_s8.glb",
                                                           "input": "3d/library/dome/view_1024_cascade_s8_input.png"})
    versions.set_current(store, "dome", 2)
    r = smooth.smooth_version(store, "dome", 1)
    assert r["n"] == 3 and versions.current(store, "dome") == 2


def test_postprocess_follows_the_setting(store, monkeypatch, tmp_path):
    calls = []
    fake_blender(monkeypatch, calls)
    glb, stats = tmp_path / "x.glb", tmp_path / "x.json"
    glb.write_bytes(b"g")
    stats.write_text("{}")
    cfg = {"trellis.smooth_angle": 0, "trellis.remove_ground": False}
    monkeypatch.setattr(config, "get", lambda k, d=None: cfg.get(k, d))
    assert smooth.postprocess(glb, stats) is None and calls == []
    cfg["trellis.smooth_angle"] = 25
    assert smooth.postprocess(glb, stats)["smoothed"]["angle"] == 25 and calls == [("x.glb", 25, False)]
    assert json.loads(stats.read_text())["smoothed"]["welded"] == 5
    cfg["trellis.remove_ground"] = True                                 # the default: one pass does both
    out = smooth.postprocess(glb, stats)
    assert calls[-1] == ("x.glb", 25, True) and out["ground"]["removed_faces"] == 1234
    assert smooth.ground_line(json.loads(stats.read_text())["ground"]) == "Ground removed: 1,234 faces"


def test_ground_removed_as_a_new_active_version(store, monkeypatch):
    calls = []
    fake_blender(monkeypatch, calls)
    assert smooth.candidates(store, op="ground") == ["dome"]
    r = smooth.ground_version(store, "dome")
    assert (r["from"], r["n"]) == (1, 2) and calls == [("view_1024_cascade_s7.glb", 0, True)]   # shading kept
    row = versions.get(store, "dome", 2)
    assert row["files"][0].endswith("view_1024_cascade_s7_noground.glb") and "ground removed (1,234 faces)" in row["note"]
    assert versions.current(store, "dome") == 2 and smooth.candidates(store, op="ground") == []
    listing = {v["n"]: v for v in versions.listing(store, "dome")["versions"]}
    assert listing[2]["ground"] == "Ground removed: 1,234 faces" and not listing[2]["groundable"]
    assert listing[1]["groundable"] and listing[1]["ground"] == ""
    with pytest.raises(ValueError, match="looked for already"):
        smooth.ground_version(store, "dome", 2)


def test_no_ground_found_makes_no_version_and_says_so(store, monkeypatch):
    fake_blender(monkeypatch, [], removed=0)
    f = store.root / "3d/library/dome/view_1024_cascade_s7.json"
    t = f.stat().st_mtime - 100
    import os
    os.utime(f, (t, t))
    r = smooth.ground_version(store, "dome")
    assert r["n"] is None and len(versions.load(store, "dome")) == 1
    assert f.stat().st_mtime == t                                       # not made to look like a newer result
    v = versions.listing(store, "dome")["versions"][0]
    assert v["ground"] == "No ground found" and not v["groundable"] and v["smoothable"]


def test_batch_smooths_and_removes_ground_in_one_pass(store, monkeypatch):
    calls = []
    fake_blender(monkeypatch, calls)
    rep = smooth.fix_all(store, log=lambda *a: None, smooth=True, ground=True)
    assert [r["n"] for r in rep["done"]] == [2] and calls == [("view_1024_cascade_s7.glb", 30.0, True)]
    row = versions.get(store, "dome", 2)
    assert row["files"][0].endswith("_s7_smooth_noground.glb") and row["note"] == \
        "v1 with smooth shading (30 deg), the ground removed (1,234 faces)"
    assert smooth.candidates(store) == [] and smooth.candidates(store, op="ground") == []


def _glb(path, pos, idx):
    pos, idx = np.asarray(pos, np.float32), np.asarray(idx, np.uint32)
    bin_ = pos.tobytes() + idx.tobytes()
    j = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}], "buffers": [{"byteLength": len(bin_)}],
         "bufferViews": [{"buffer": 0, "byteLength": pos.nbytes}, {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": idx.nbytes}],
         "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                        "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                       {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]}
    js = json.dumps(j).encode()
    js += b" " * (-len(js) % 4)
    bin_ += b"\0" * (-len(bin_) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(bin_)) + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(bin_), 0x004E4942) + bin_)


def _read(path):
    b = path.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + n])
    binary = b[28 + n:]
    prim = j["meshes"][0]["primitives"][0]

    def acc(i, comps):
        a = j["accessors"][i]
        v = j["bufferViews"][a["bufferView"]]
        dt = np.float32 if a["componentType"] == 5126 else {5125: np.uint32, 5123: np.uint16}[a["componentType"]]
        return np.frombuffer(binary, dt, a["count"] * comps, v.get("byteOffset", 0) + a.get("byteOffset", 0)).reshape(-1, comps)
    return acc(prim["attributes"]["POSITION"], 3), acc(prim["attributes"]["NORMAL"], 3)


@pytest.mark.blender
@pytest.mark.skipif(shutil.which(str(config.get("tools.blender") or "blender")) is None, reason="needs Blender")
def test_weld_and_smooth_by_angle_in_blender(tmp_path, monkeypatch):
    """A shallow roof ridge (10 deg: smooth) on a box wall (90 deg: stays sharp), every face with its
    own vertices as TRELLIS leaves them: welded, the ridge one normal, the wall edge two."""
    monkeypatch.setattr(blender, "blender_bin", lambda: config.get("tools.blender") or "blender")
    t = np.tan(np.radians(10))
    quads = [[(0, 0, 1), (1, 0, 1 + t), (1, 1, 1 + t), (0, 1, 1)], [(1, 0, 1 + t), (2, 0, 1), (2, 1, 1), (1, 1, 1 + t)],
             [(0, 0, 0), (1, 0, 0), (1, 0, 1 + t), (0, 0, 1)], [(1, 0, 0), (2, 0, 0), (2, 0, 1), (1, 0, 1 + t)]]
    pos, idx = [], []
    for q in quads:                                                      # split: 4 own vertices per face
        k = len(pos)
        pos += q
        idx += [k, k + 1, k + 2, k, k + 2, k + 3]
    src, dst = tmp_path / "in.glb", tmp_path / "out.glb"
    _glb(src, [(x, z, -y) for x, y, z in pos], idx)                      # glTF is y-up
    rep = smooth.smooth_glb(src, dst, 30)
    assert rep["welded"] >= 3 and rep["sharp_edges"] >= 1
    p, n = _read(dst)
    ridge = np.isclose(p[:, 0], 1) & np.isclose(p[:, 1], 1 + t, atol=1e-4) & np.isclose(p[:, 2], -1)   # away from the wall
    assert ridge.any() and np.allclose(n[ridge], n[ridge][0], atol=1e-3)            # smooth across 10 deg
    corner = np.isclose(p[:, 0], 0) & np.isclose(p[:, 1], 1) & np.isclose(p[:, 2], 0)
    assert len({tuple(np.round(v, 2)) for v in n[corner]}) >= 2                     # crisp at 90 deg


def _boxes(boxes):
    """Axis-aligned boxes (x0, y0, z0, x1, y1, z1), z up, as one split-vertex mesh in glTF axes."""
    pos, idx = [], []
    for x0, y0, z0, x1, y1, z1 in boxes:
        c = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
        k = len(pos)
        pos += [(x, z, -y) for x, y, z in c]
        for a, b, cc, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            idx += [k + a, k + b, k + cc, k + a, k + cc, k + d]
    return pos, idx


@pytest.mark.blender
@pytest.mark.skipif(shutil.which(str(config.get("tools.blender") or "blender")) is None, reason="needs Blender")
def test_a_slab_is_cut_off_and_a_podium_stays_in_blender(tmp_path, monkeypatch):
    """Two piers on a thin plate three times wider (a TRELLIS shadow slab): the plate goes, the piers
    stand at z=0. A temple-like podium (as wide as what stands on it, a fifth of the height) stays."""
    monkeypatch.setattr(blender, "blender_bin", lambda: config.get("tools.blender") or "blender")
    g = smooth.ground_settings()
    src, dst = tmp_path / "slab.glb", tmp_path / "slab_out.glb"
    _glb(src, *_boxes([(-3, -3, 0, 3, 3, 0.1), (-1, -0.3, 0.1, -0.5, 0.3, 4), (0.5, -0.3, 0.1, 1, 0.3, 4)]))
    rep = smooth.fix_glb(src, dst, 0, g)
    assert rep["ground"]["removed_faces"] > 0
    b = dst.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    acc = json.loads(b[20:20 + n])["accessors"][0]
    lo, hi = acc["min"], acc["max"]                                      # glTF: y up, z = -plan y
    assert abs(lo[1]) < 1e-3 and 3.8 < hi[1] < 4.0                      # re-seated, the plate's thickness gone
    assert -1.01 < lo[0] and hi[0] < 1.01 and -0.31 < lo[2] and hi[2] < 0.31   # only the piers are left
    src, dst = tmp_path / "podium.glb", tmp_path / "podium_out.glb"
    _glb(src, *_boxes([(-2, -2, 0, 2, 2, 0.8), (-1.5, -1.5, 0.8, 1.5, 1.5, 4)]))
    rep = smooth.fix_glb(src, dst, 0, g)
    assert rep["ground"]["removed_faces"] == 0 and smooth.ground_line(rep["ground"]) == "No ground found"


def test_cleanups_say_which_version_they_came_from(store, monkeypatch):
    """Views and 3D labels each Clean up output with its source version and marks the Active one's."""
    fake_blender(monkeypatch, [])
    smooth.smooth_version(store, "dome")                                  # v2, Active
    v1, v2 = (versions.get(store, "dome", n)["files"][0] for n in (1, 2))
    reps = [{"glb": "cleanup/a.glb", "source": v1}, {"glb": "cleanup/b.glb", "source": v2},
            {"glb": "cleanup/c.glb", "source": "3d/library/dome/gone.glb"}]
    out = versions.cleanup_sources(store, "dome", reps)
    assert [(c["version"], c["active"]) for c in out] == [(1, False), (2, True), (None, False)]

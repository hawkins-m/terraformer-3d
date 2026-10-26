"""Smooth shading for TRELLIS models (asset_pipeline/smooth.py): an existing version smoothed as a new
version that takes over as Active, the batch over every unsmoothed TRELLIS model in use, the
post-process switch, and (with Blender) the weld and smooth-by-angle on a real GLB."""
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


def fake_blender(monkeypatch, calls):
    def run(src, dst, angle_deg=None, log_path=None):
        calls.append((src.name, dst.name, angle_deg))
        shutil.copyfile(src, dst)
        return {"angle": angle_deg or 30.0, "welded": 5, "sharp_edges": 3, "seconds": 0.1}
    monkeypatch.setattr(smooth, "smooth_glb", run)


def test_a_version_is_smoothed_as_a_new_active_version(store, monkeypatch):
    calls = []
    fake_blender(monkeypatch, calls)
    assert versions.current(store, "dome") == 1 and smooth.candidates(store) == ["dome"]
    r = smooth.smooth_version(store, "dome")
    assert (r["from"], r["n"]) == (1, 2) and calls == [("view_1024_cascade_s7.glb", "view_1024_cascade_s7_smooth.glb", None)]
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
    monkeypatch.setattr(config, "get", lambda k, d=None: 0 if k == "trellis.smooth_angle" else d)
    assert smooth.postprocess(glb, stats) is None and calls == []
    monkeypatch.setattr(config, "get", lambda k, d=None: 25 if k == "trellis.smooth_angle" else d)
    assert smooth.postprocess(glb, stats)["angle"] == 25 and json.loads(stats.read_text())["smoothed"]["welded"] == 5


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

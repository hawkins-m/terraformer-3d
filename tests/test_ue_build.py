"""`ap ue build` pieces that run without Unreal: the prune count, the render GPU rule, the
report, and texture stripping (which keeps re-imports skippable: the same source gives the
same bytes, so the same content hash)."""
import json
import struct

import numpy as np

from asset_pipeline import city_library as cl, ue_build


def test_expected_prune_counts_pipeline_actors_the_manifest_no_longer_has():
    man = {"slots": [{"id": "a"}, {"id": "b"}], "shots": [{"id": "wide"}]}
    assert ue_build.expected_prune({"a": {}, "b": {}, "old": {}}, {"wide", "gone"}, man) == 2
    assert ue_build.expected_prune({}, set(), man) == 0


def test_renders_use_gpu_1_only_while_comfyui_is_down():
    gpu, why = ue_build.render_gpu(True, {0: 0.5, 1: 0.5})
    assert gpu is None and "ComfyUI is running on GPU 1" in why and "GPU 0" in why
    assert ue_build.render_gpu(False, {0: 0.5, 1: 9.0})[0] is None
    gpu, why = ue_build.render_gpu(False, {0: 3.0, 1: 0.5})
    assert gpu == 1 and why.startswith("rendering on GPU 1")


def test_report_is_short_and_lists_warnings():
    r = {"ok": True, "placed": 555, "attached": 0, "slots_replaced": 38, "greybox_slots": 470, "held": 121,
         "imported": 3, "skipped": 900, "reset": 2, "pruned": 1, "terrain_outer": True, "shots": None,
         "steps": {"export": 20.0, "import": 300.0}, "seconds": 330.0, "warnings": ["no Landscape in the level"]}
    lines = ue_build.report_lines(r)
    assert lines[0] == "OK: 555 library actors placed in 38 slots; 470 slots still greybox, 121 builds held back (slot not fully built or not approved)"
    assert "3 imported, 900 unchanged" in lines[1] and lines[2].startswith("time: 5.5 min")
    assert lines[-1] == "warning: no Landscape in the level" and len(lines) == 4


def _glb(with_image=True):
    """A one-triangle GLB with a base colour texture embedded (bufferView 1)."""
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()
    png = b"\x89PNG fake image bytes" + b"\0" * 7
    js = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(pos) + len(png)}],
          "bufferViews": [{"buffer": 0, "byteOffset": len(png), "byteLength": len(pos)}] if not with_image else
          [{"buffer": 0, "byteOffset": 0, "byteLength": len(png)}, {"buffer": 0, "byteOffset": len(png), "byteLength": len(pos)}],
          "accessors": [{"bufferView": 1 if with_image else 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
          "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
          "materials": [{"name": "wall", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                                                 **({"baseColorTexture": {"index": 0}} if with_image else {})}}]}
    if with_image:
        js |= {"images": [{"bufferView": 0, "mimeType": "image/png"}], "textures": [{"source": 0, "sampler": 0}],
               "samplers": [{}], "extensionsUsed": ["KHR_texture_transform"]}
    binb = (png if with_image else b"") + pos
    jb = json.dumps(js).encode()
    jb += b" " * (-len(jb) % 4)
    binb += b"\0" * (-len(binb) % 4)
    total = 12 + 8 + len(jb) + 8 + len(binb)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(jb), 0x4E4F534A) + jb
            + struct.pack("<II", len(binb), 0x004E4942) + binb)


def _parts(glb):
    n = struct.unpack_from("<I", glb, 12)[0]
    js = json.loads(glb[20:20 + n])
    off = 20 + n
    return js, glb[off + 8:off + 8 + struct.unpack_from("<I", glb, off)[0]]


def test_stripping_drops_the_images_and_keeps_geometry_and_material_names():
    out = cl.strip_textures(_glb())
    js, binb = _parts(out)
    assert not {"images", "textures", "samplers", "extensionsUsed"} & set(js)
    assert js["materials"] == [{"name": "wall", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}]
    v = js["bufferViews"][js["accessors"][0]["bufferView"]]
    pts = np.frombuffer(binb[v["byteOffset"]:v["byteOffset"] + v["byteLength"]], np.float32).reshape(3, 3)
    assert pts.tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0]] and len(binb) == 36
    assert struct.unpack_from("<I", out, 8)[0] == len(out)


def test_stripping_is_deterministic_so_an_unchanged_build_is_skipped_on_import():
    assert cl.strip_textures(_glb()) == cl.strip_textures(_glb())
    assert cl.strip_textures(cl.strip_textures(_glb())) == cl.strip_textures(_glb())    # nothing left to strip


def _png(rgb, size=4):
    import io

    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (size, size), rgb).save(b, "PNG")
    return b.getvalue()


def _textured_glb(base_rgb, mr_rgb, metallic=1.0, roughness=0.8):
    """A GLB with a baked base colour and metallic-roughness map (as TRELLIS writes them)."""
    imgs = [_png(base_rgb), _png(mr_rgb)]
    binb, views = b"", []
    for im in imgs:
        views.append({"buffer": 0, "byteOffset": len(binb), "byteLength": len(im)})
        binb += im + b"\0" * (-len(im) % 4)
    js = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binb)}], "bufferViews": views,
          "images": [{"bufferView": 0, "mimeType": "image/png"}, {"bufferView": 1, "mimeType": "image/png"}],
          "textures": [{"source": 0}, {"source": 1}],
          "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicRoughnessTexture": {"index": 1},
                                                  "metallicFactor": metallic, "roughnessFactor": roughness}}]}
    jb = json.dumps(js).encode()
    jb += b" " * (-len(jb) % 4)
    total = 12 + 8 + len(jb) + 8 + len(binb)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(jb), 0x4E4F534A) + jb
            + struct.pack("<II", len(binb), 0x004E4942) + binb)


def test_baked_materials_read_the_maps_metallic_roughness_and_base_colour():
    out = cl.baked_materials(_textured_glb((255, 204, 102), (0, 200, 51), metallic=1.0, roughness=0.8))
    assert out == [{"maps": ["baseColorTexture", "metallicRoughnessTexture"], "metallic": 0.2, "roughness": 0.8,
                    "base_mean": [1.0, 0.8, 0.4]}]                     # metallic = factor x the map's blue (51/255)


def test_a_glb_without_maps_reports_its_factors():
    out = cl.baked_materials(_glb(with_image=False))
    assert out == [{"maps": [], "metallic": 1.0, "roughness": 1.0, "base_mean": [1.0, 1.0, 1.0]}]


# --- the Unreal tab's "Build city in Unreal": `ap ue build` as a UI job, Unreal stubbed ----------

import time  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def stub_build(tmp_path, monkeypatch):
    """A synthetic project whose Unreal, Blender and export runs are stubs: build() runs its real
    step order, warnings, placement check call and report on canned results."""
    from asset_pipeline import city_library, ground, look, placement_check, ue
    from asset_pipeline.project import ProjectStore
    from asset_pipeline.stages import s7_export, sw_site
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("AP_UE_PROJECTS", str(tmp_path / "ue"))
    monkeypatch.setenv("AP_UE_BACKUPS", str(tmp_path / "backups"))
    store = ProjectStore.create("demo-build")
    (ue.project_dir(store)).mkdir(parents=True)
    calls = []
    man = {"map": "/Game/AP/Maps/City", "slots": [{"id": "a", "replaced": True}, {"id": "b"}], "shots": [],
           "library": {"placed": 3, "parents": 3, "props": 0, "slots_replaced": 1, "held": 1}}
    monkeypatch.setattr(ue, "editor_running", lambda up: False)
    monkeypatch.setattr(ue, "backup", lambda st: calls.append("backup") or {})
    monkeypatch.setattr(s7_export, "export", lambda st, **kw: calls.append(("export", kw)) or {"instances": 7})
    monkeypatch.setattr(s7_export, "load_manifest", lambda st: man)
    monkeypatch.setattr(ue_build, "_level_actors", lambda st, m: calls.append("level read") or ({}, set()))
    monkeypatch.setattr(look, "city_lighting", lambda st: {"sun_pitch": -40})
    monkeypatch.setattr(ue, "import_", lambda st, **kw: calls.append(("import", kw)) or {
        "ok": True, "library": {"placed": 3}, "assets": {"imported": [1], "skipped": [2, 3]},
        "actors": {"reset": []}, "landscape": True, "warnings": [], "library_actors": [{"id": "x"}]})
    monkeypatch.setattr(sw_site, "load_greybox", lambda st: {"slots": [{"id": "a", "bbox": [[0] * 3, [1] * 3]}]})
    monkeypatch.setattr(ground.Ground, "from_project", classmethod(lambda cls, st: None))
    monkeypatch.setattr(city_library, "settings", lambda st: {})
    monkeypatch.setattr(placement_check, "check", lambda *a: {"ok": False, "checked": 3, "issues": {"floating": ["x"]},
                                                                "per_entry": {"tower": {"floating": 1}}})
    return SimpleNamespace(store=store, calls=calls, ue=ue)


def _client():
    from fastapi.testclient import TestClient
    from asset_pipeline.jobs import JobQueue
    from asset_pipeline.ui import app as ui_app
    return TestClient(ui_app.create_app(JobQueue()))


def _wait(client, job):
    for _ in range(500):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_build_steps_run_in_order_and_report_their_stages(stub_build):
    seen = []
    r = ue_build.build(stub_build.store, log=lambda *a: None, on_step=seen.append)
    assert seen == ["backup", "export", "dressing", "lighting", "level read", "import", "check"]   # no outer ring: no terrain step
    idx = [ue_build.STAGES.index(ue_build.STAGE_OF[s]) for s in seen]
    assert idx == sorted(idx)                                       # the Jobs panel's stages never go back
    imp = next(c for c in stub_build.calls if c[0] == "import")[1]
    assert imp["lighting"] == {"sun_pitch": -40} and imp["prune"] and not imp["reset_layout"]
    assert r["placed"] == 3 and r["greybox_slots"] == 1 and not r["placement"]["ok"]
    assert any("no outer ring" in w for w in r["warnings"])


def test_ui_build_is_a_staged_cpu_job_with_the_report_on_the_tab(stub_build, monkeypatch):
    client = _client()
    U = "/api/projects/demo-build"
    est = client.post(f"{U}/ue/build", json={}, headers={"X-AP-Estimate": "1"}).json()["estimate"]
    assert est["kind"] == "ue.build" and [x["stage"] for x in est["stages_est"]] == ue_build.STAGES
    assert est["breakdown"] and est["seconds"] == sum(x["seconds"] for x in est["stages_est"])
    assert not stub_build.calls                                     # an estimate starts nothing
    j = _wait(client, client.post(f"{U}/ue/build", json={"use_active": True, "keep_hand_moves": False}).json())
    assert j["status"] == "done" and j["lane"] == "cpu" and j["kind"] == "ue.build", j
    assert j["tag"]["stages"] == ue_build.STAGES and j["tag"]["stage"] == ue_build.STAGES.index("check")
    assert ("export", {"assets": True, "use_active": True, "log": stub_build.calls[1][1]["log"]}) == stub_build.calls[1]
    assert next(c for c in stub_build.calls if c[0] == "import")[1]["reset_layout"] is True     # hand moves off: reset
    b = client.get(f"{U}/unreal").json()["build"]
    assert b["placed"] == 3 and b["greybox_slots"] == 1 and b["use_active"] and b["keep_hand_moves"] is False
    assert b["placement_lines"][0].startswith("placement: 1 floating of 3") and "tower" in b["placement_lines"][1]
    assert b["lines"][0].startswith("OK: 3 library actors placed") and b["seconds"] >= 0
    j = _wait(client, client.post(f"{U}/ue/build", json={"render": True}).json())
    assert j["tag"]["stages"] == ue_build.STAGES + ["render"]


def test_ui_build_refuses_while_the_editor_is_open_and_stop_ends_it_between_steps(stub_build, monkeypatch):
    from asset_pipeline.jobs import current_job
    client = _client()
    U = "/api/projects/demo-build"
    ue = stub_build.ue
    monkeypatch.setattr(ue, "uproject", lambda st: Path(__file__))      # an existing .uproject
    monkeypatch.setattr(ue, "editor_running", lambda up: True)
    r = client.post(f"{U}/ue/build", json={})
    assert r.status_code == 409 and "close it" in r.json()["detail"] and not stub_build.calls
    monkeypatch.setattr(ue, "editor_running", lambda up: False)

    def stop_during_backup(st):                                      # Stop pressed while the backup runs
        job = current_job()
        job._queue.cancel(job.id)
        stub_build.calls.append("backup")
    monkeypatch.setattr(ue, "backup", stop_during_backup)
    j = _wait(client, client.post(f"{U}/ue/build", json={}).json())
    assert j["status"] == "canceled" and stub_build.calls == ["backup"]          # export never started
    assert client.get(f"{U}/unreal").json()["build"] is None


def test_progress_shows_build_stages_and_counts_the_export_meshes(stub_build):
    from asset_pipeline.stages import s7_export
    from asset_pipeline.ui import app as ui_app
    t0 = time.time()
    job = SimpleNamespace(kind="ue.build", tag={"stages": ue_build.STAGES, "stage": 1}, created=t0, note="export…")
    out = s7_export.export_dir(stub_build.store)
    (out / "assets" / "a").mkdir(parents=True)
    (out / "assets" / "a" / "SM_a.glb").write_bytes(b"x")
    (out / "blender.log").write_text("\n=== blender\nEXPORT_TOTAL 4\n")
    p = ui_app._progress(stub_build.store, job, t0, t0 + 5)
    assert p["stages"][:2] == ["Backup", "Export"] and p["stage"] == 1 and p["stage_label"] == "Export"
    assert (p["done"], p["total"]) == (1, 4) and p["line"] == "export…"
    job.tag["stage"] = ue_build.STAGES.index("import")
    p = ui_app._progress(stub_build.store, job, t0, t0 + 5)
    assert p["stage_label"] == "Import" and p["done"] is None


# --- the build's time estimate: per stage, from the work queued before it starts ------------

def _synthetic_plan(store, glbs, missing):
    """city_library.plan's stand-in: placements of `glbs` (relative paths); the dry fitter
    learns the `missing` ones are height variants still to build."""
    def plan(st, gb, use_active=False, index=None, fitter=None, log=print):
        assert fitter.dry
        fitter.missing += [g for g in missing if g not in fitter.missing]
        return {"placed": [{"key": f"k{i}", "glb": g} for i, g in enumerate(glbs)]}
    return plan


def test_queued_work_counts_missing_variants_and_changed_imports_only(stub_build, monkeypatch):
    from asset_pipeline import city_library, ue
    from asset_pipeline.project import write_json
    from asset_pipeline.stages import s7_export
    st = stub_build.store
    have = [f"3d/library/b{i}/procedural.glb" for i in range(5)]
    for i, g in enumerate(have):
        (st.root / g).parent.mkdir(parents=True, exist_ok=True)
        (st.root / g).write_bytes(b"glb%d" % i)
    variants = [f"3d/library/b0/heights/middle-{n}s-abc.glb" for n in range(3)]
    monkeypatch.setattr(city_library, "plan", _synthetic_plan(st, have + variants, variants))
    write_json(s7_export.export_dir(st) / "mesh_index.json", {"m1": {"id": "SM_a"}, "m2": {"id": "SM_a"}, "m3": {"id": "SM_b"}})
    w = ue_build.queued_work(st)
    assert w["height_variants"] == 3 and w["first_import"] and "error" not in w
    assert w["imports"] == 5 + 3 + 2                     # first import: every build and stand-in mesh
    ue.record_imported(st, {"library_assets": [{"id": city_library.asset_id(f"k{i}"), "source": g} for i, g in enumerate(have)],
                            "assets": [{"id": "SM_a"}, {"id": "SM_b"}]},
                       {"assets": {"imported": [city_library.asset_id(f"k{i}") for i in range(5)], "skipped": []}})
    (st.root / have[1]).write_bytes(b"changed")          # one build changed since the last import
    w = ue_build.queued_work(st)
    assert (w["height_variants"], w["imports"], w["first_import"]) == (3, 1 + 3, False)
    monkeypatch.setattr(city_library, "plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("no greybox")))
    assert ue_build.queued_work(st)["error"] == "no greybox"           # never fails the build


def test_estimate_is_per_stage_from_the_queue_with_defaults_then_recorded_times(tmp_path, monkeypatch):
    from asset_pipeline import estimates
    monkeypatch.setenv("AP_ROOT", str(tmp_path))
    estimates._cache.clear()
    tag = {"stages": ue_build.STAGES, "work": {"backup": True, "terrain": False, "height_variants": 120, "imports": 40}}
    e = estimates.estimate("ue.build", "cpu", tag)
    D = estimates.UE_BUILD_DEFAULTS
    by = {x["stage"]: x for x in e["stages_est"]}
    assert by["export"]["seconds"] == D["ue.build.export"] + 120 * D["ue.height_variant"]
    assert by["import"]["seconds"] == D["ue.build.import"] + 40 * D["ue.import_asset"]
    assert by["terrain"]["seconds"] == 0 and e["long"] and "default" in e["source"]
    assert e["breakdown"] == "~120 height variants to build, ~40 imports"
    assert estimates.job_seconds(SimpleNamespace(kind="ue.build", tag=tag)) == e["seconds"]
    small = estimates.estimate("ue.build", "cpu", tag | {"work": {"height_variants": 0, "imports": 0}})
    assert not small["long"] and small["breakdown"] == "no height variants to build, nothing changed to import"
    for _ in range(3):                                   # three builds recorded: 30 s per variant, 2 s per import
        estimates.record_time("ue.height_variant", 10, 300)
        estimates.record_time("ue.import_asset", 5, 10)
        estimates.record_time("ue.build.export", 1, 50)
    e = estimates.estimate("ue.build", "cpu", tag)
    by = {x["stage"]: x for x in e["stages_est"]}
    assert by["export"]["seconds"] == 50 + 120 * 30 and by["import"]["seconds"] == D["ue.build.import"] + 40 * 2
    assert e["source"] == "recorded builds"


def test_a_build_records_its_parts_for_the_next_estimate(stub_build, monkeypatch):
    from asset_pipeline import estimates, ue
    from asset_pipeline.stages import s7_export
    man = s7_export.load_manifest(stub_build.store)
    man["library"] |= {"height_variants_built": ["a/x", "a/y"], "height_variant_s": 0.0}
    monkeypatch.setattr(ue, "import_", lambda st, **kw: {
        "ok": True, "assets": {"imported": [1, 2], "skipped": []}, "actors": {"reset": []}, "landscape": True,
        "warnings": [], "import_seconds": {"a": 1.5, "b": 2.5}})
    ue_build.build(stub_build.store, log=lambda *a: None)
    rows = [json.loads(x) for x in estimates.log_path().read_text().splitlines()]
    kinds = {r["kind"]: r for r in rows}
    assert kinds["ue.import_asset"]["items"] == 2 and kinds["ue.import_asset"]["seconds"] == 4.0
    assert {"ue.build.backup", "ue.build.export", "ue.build.lighting", "ue.build.import"} <= set(kinds)
    assert "ue.height_variant" not in kinds              # no Blender seconds measured: nothing to learn from
    assert all(r["seconds"] >= 0 for r in rows)


def test_progress_time_left_follows_the_stage_not_the_old_median(stub_build):
    from asset_pipeline import estimates
    from asset_pipeline.ui import app as ui_app
    t0 = time.time()
    work = {"backup": True, "terrain": False, "height_variants": 100, "imports": 40}
    tag = {"stages": ue_build.STAGES, "stage": 1, "work": work, "stage_t0": t0 - 600,
           "hv_t0": t0 - 500, "hv_started": 11}                # 10 variants done in 500 s: 50 s each
    job = SimpleNamespace(kind="ue.build", tag=tag, created=t0 - 700, note="b0: height variant …", est_s=999)
    p = ui_app._progress(stub_build.store, job, t0 - 700, t0)
    later = sum(x["seconds"] for x in estimates.ue_build_stages(tag)[2:])
    assert (p["done"], p["total"]) == (10, 100)
    assert p["eta_s"] == round(90 * 50 + later) and not p["over"]
    tag["stage"] = ue_build.STAGES.index("import")           # import: its own counted log line
    (ue_build.ue.work_dir(stub_build.store)).mkdir(parents=True, exist_ok=True)
    (ue_build.ue.work_dir(stub_build.store) / "ap_import.log").write_text("LogPython: library: imported 30 of 40 (3.0 s each)\n")
    p = ui_app._progress(stub_build.store, job, t0 - 700, t0 + 1)
    check = estimates.ue_build_stages(tag)[ue_build.STAGES.index("check")]["seconds"]
    assert (p["done"], p["total"]) == (30, 40) and p["eta_s"] == round(10 * 3.0 + check)

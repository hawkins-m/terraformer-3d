"""What you still need (asset_pipeline/needs.py): the setup wizard's page and `ap doctor`'s last section,
from faked detection results (Linux + AMD, Windows + NVIDIA) and from ComfyUI models a user already has
(sparse files of the manifest's sizes). Nothing here downloads or touches a real machine."""
import json
import os
import re
from pathlib import Path

import pytest

from asset_pipeline import cli, config, models, needs, setup

OFFICIAL = ("https://www.nvidia.com/", "https://rocm.docs.amd.com/", "https://learn.microsoft.com/", "https://www.blender.org/",
            "https://github.com/Comfy-Org/ComfyUI", "https://huggingface.co/", "https://www.unrealengine.com/")


def _facts(**kw):
    f = {"os": "linux", "wsl2": None, "vendor": "amd", "profile": {"id": "rocm-2gpu", "title": "Ubuntu + ROCm, 2 GPUs", "how": "detected"},
         "gpus": [{"index": 0, "name": "Radeon AI PRO R9700", "vram_gb": 32.0}, {"index": 1, "name": "Radeon AI PRO R9700", "vram_gb": 32.0}],
         "blender": True, "comfyui": True, "vlm_env": True, "trellis_env": True, "hf_token": True, "unreal": True,
         "comfy_models_dir": "/data/comfy/models", "folders": [],
         "models": {"comfy": {"missing": [], "gated": [], "page": "", "bytes": 0},
                    "vlm": {"missing": [], "gated": [], "page": "https://huggingface.co/x/vlm", "bytes": 0},
                    "trellis": {"missing": [], "gated": [], "page": "https://huggingface.co/microsoft/TRELLIS.2-4B", "bytes": 0}}}
    for k, v in kw.items():
        if k in ("comfy", "vlm", "trellis"):
            f["models"][k] |= v
        else:
            f[k] = v
    return f


def _shown(rep):
    return {r["id"]: r for r in rep["shown"]}


def test_a_ready_machine_shows_nothing_but_still_lists_every_component():
    rep = needs.report(f=_facts())
    assert rep["shown"] == [] and rep["missing"] == 0
    assert [r["id"] for r in rep["items"]] == [c for c in needs.COMPONENTS if c != "wsl2"]
    assert rep["os_label"] == "Linux" and rep["vram_gb"] == 32 and rep["profile"]["title"].startswith("Ubuntu + ROCm")
    assert "Nothing: every component is ready." in "\n".join(needs.lines(rep))


def test_linux_amd_without_rocm_blender_or_models():
    rep = needs.report(f=_facts(gpus=[], vendor=None, profile=None, blender=False, unreal=False, trellis_env=False, hf_token=False,
                                comfy={"missing": ["flux1-dev-fp8", "flux1-redux-dev"], "gated": ["flux1-redux-dev"]},
                                vlm={"missing": ["vlm-32b"], "bytes": 24e9}, trellis={"missing": ["trellis2-4b"]}))
    s = _shown(rep)
    assert s["gpu_driver"]["link"] == needs.LINKS["rocm"] and s["gpu_driver"]["state"] == "missing"
    assert "wsl2" not in s and s["blender"]["link"] == "https://www.blender.org/download/"
    assert s["flux"]["link"] == "https://huggingface.co/black-forest-labs/FLUX.1-dev" and "license" in s["flux"]["sentence"]
    assert s["hf_token"]["link"] == "https://huggingface.co/settings/tokens" and "flux1-redux-dev" in s["hf_token"]["sentence"]
    assert "(24 GB)" in s["vlm"]["sentence"]
    assert s["trellis"]["state"] == "optional" and s["unreal"]["state"] == "optional"
    assert s["unreal"]["link"] == "https://www.unrealengine.com/en-US/download"
    assert rep["missing"] == 5 and "comfyui" not in s                   # ComfyUI is there: not shown
    for r in rep["shown"]:                                               # one plain sentence, one official link
        assert r["sentence"].count(". ") == 0 and r["sentence"].endswith(".") and r["link"].startswith(OFFICIAL), r
    text = "\n".join(needs.lines(rep))
    assert "hardware profile: none" in text and "✗ GPU driver" in text and "- Unreal Engine (optional)" in text


def test_windows_nvidia_is_beta_and_points_at_wsl_and_the_nvidia_driver():
    nv = [{"index": 0, "name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0}]
    rep = needs.report(f=_facts(os="windows", wsl2=False, vendor="nvidia", gpus=[], profile=None, comfyui=False))
    s = _shown(rep)
    assert "beta" in rep["os_label"] and "Windows" in rep["os_label"]
    assert s["wsl2"]["link"] == "https://learn.microsoft.com/en-us/windows/wsl/install" and "untested" in s["wsl2"]["sentence"]
    assert s["gpu_driver"]["link"] == "https://www.nvidia.com/en-us/drivers/" and "Windows" in s["gpu_driver"]["sentence"]
    assert s["comfyui"]["state"] == "setup" and "Setup installs a local ComfyUI" in s["comfyui"]["sentence"]
    assert rep["missing"] == 2 and s["comfyui"]["link"] == "https://github.com/Comfy-Org/ComfyUI"      # not counted as missing
    rep = needs.report(f=_facts(os="windows", wsl2=True, vendor="nvidia", gpus=nv,
                                profile={"id": "nvidia", "title": "Windows (WSL2) + NVIDIA, beta", "how": "detected"}))
    assert rep["shown"] == [] and rep["gpu"] == "NVIDIA" and rep["gpus"] == ["NVIDIA GeForce RTX 4090 (24 GB)"]
    assert [r["id"] for r in rep["items"]][:2] == ["gpu_driver", "wsl2"]


def _have(folder: Path, ids):
    """The manifest's files of these models in a ComfyUI-style folder: sparse files of the right size."""
    for m in models.load():
        if m.id in ids:
            for f in m.source().files:
                p = folder / f.dest
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "wb") as fh:
                    fh.truncate(f.size)


@pytest.mark.posix
def test_models_already_present_are_found_and_reused_where_they_are(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("AP_CONFIG_DIR", str(tmp_path / "cfg"))
    config.backends.cache_clear()
    theirs = tmp_path / "ComfyUI" / "models"                             # the default location: <ComfyUI folder>/models
    _have(theirs, ["flux1-dev-fp8", "sigclip-vision-384"])
    other = tmp_path / "elsewhere" / "models"                           # one the user points to
    _have(other, ["flux1-dev-fp8", "flux1-redux-dev", "sigclip-vision-384", "controlnet-union-pro-2", "sam3.1"])
    (other / "checkpoints" / "unrelated.safetensors").write_bytes(b"x")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    config.save_user({"comfyui": {"service": {"cwd": str(tmp_path / "ComfyUI")}}})
    found = {Path(f["path"]).parent.name: f for f in needs.comfy_folders(str(other))}
    assert found["ComfyUI"]["models"] == ["flux1-dev-fp8", "sigclip-vision-384"] and found["ComfyUI"]["found"] == 2
    assert set(found["elsewhere"]["missing"]) == set() and found["elsewhere"]["found"] == 5 and not found["elsewhere"]["configured"]
    assert needs.scan_folder(tmp_path / "nope")["exists"] is False
    # a wrong-sized file is not a match
    with open(theirs / "clip_vision" / "sigclip_vision_patch14_384.safetensors", "wb") as fh:
        fh.truncate(10)
    assert needs.scan_folder(theirs)["models"] == ["flux1-dev-fp8"]
    # using the folder: Setup's own models check now counts them installed, so nothing is downloaded again
    setup.save({"comfy_models_dir": str(other)})
    miss = [m.id for m in models.missing("comfy")]
    assert miss == []
    assert not any(m.root == "comfyui" for m in models.select(required_only=True) if models.status(m)["state"] != "installed")
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file() and "cfg" not in p.parts
                   and "storage" not in p.parts)
    assert after == [p for p in before if "storage" not in p]           # nothing moved, copied or deleted
    assert next(f for f in needs.comfy_folders() if Path(f["path"]) == other)["configured"]
    msg = setup.test("comfy_models_dir", str(other))["message"]
    assert "won't be downloaded again" in msg


def test_doctor_ends_with_the_same_section_and_the_wizard_has_the_page(monkeypatch, capsys):
    from asset_pipeline import doctor
    rep = needs.report(f=_facts(blender=False))
    monkeypatch.setattr(doctor, "report", lambda: {"checks": [], "features": {}, "missing": 0, "optional_missing": 0, "needs": rep})
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "What you still need" in out and "✗ Blender:" in out and "https://www.blender.org/download/" in out
    src = (config.REPO_ROOT / "asset_pipeline" / "doctor.py").read_text()
    assert '"needs": needs.report()' in src                             # one function for both
    web = config.REPO_ROOT / "installer" / "web"
    core, page = (web / "wizard-core.js").read_text(), (web / "wizard.js").read_text()
    assert core.index('id: "precheck"') < core.index('id: "ready"') < core.index('id: "installing"') < core.index('id: "needs"') < core.index('id: "finish"')
    assert "precheck() { return PAGE.needs(); }" in page and page.count("needs() {") == 1     # one page, drawn twice
    assert 'id="needs-again">Check again</button>' in page and 'api.get("api/needs"' in page and 'api/needs/use' in page
    assert 'target="_blank" rel="noopener noreferrer"' in page
    for btn in ('"needs-again") { W.select();', '"needs-look") { W.select();'):      # the shared tick (mute respected)
        assert btn in page, btn
    assert "W.select();" in page[page.index("e.target.dataset.use"):page.index("api/needs/use")]

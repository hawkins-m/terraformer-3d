"""What this machine still needs: the setup wizard's "What you still need" page and the section at
the end of `ap doctor` (one function, `report`).

It reuses the existing checks (the GPU tools, the hardware profiles, the model manifest's status,
setup's Blender / ComfyUI / Unreal tests, WSL's status) and turns them into one row per component:
GPU driver, WSL2 (Windows), Blender, ComfyUI, FLUX.1-dev, the vision model, TRELLIS, the Hugging
Face token and Unreal Engine (optional), each ready, missing, optional or handled by Setup (ComfyUI is installed
locally by the wizard). Only the rows that aren't ready are shown, each with one plain sentence and one link to the official page (LINKS).

ComfyUI models the user already has: the configured ComfyUI models folder, the default one (the
ComfyUI folder's models/) and any folder the user points to are scanned for the manifest's files
(same path and size); a folder holding them can be used as the ComfyUI models folder, so Setup and
`ap models download` skip those files instead of downloading them again. Nothing is moved, copied or
deleted. Stdlib only at import time: the wizard runs this before the app's packages are installed.
"""
import os
import sys
from pathlib import Path

from . import config

# official pages only
LINKS = {
    "nvidia": "https://www.nvidia.com/en-us/drivers/",
    "rocm": "https://rocm.docs.amd.com/projects/install-on-linux/en/latest/",
    "wsl": "https://learn.microsoft.com/en-us/windows/wsl/install",
    "blender": "https://www.blender.org/download/",
    "comfyui": "https://github.com/Comfy-Org/ComfyUI",
    "flux": "https://huggingface.co/black-forest-labs/FLUX.1-dev",
    "hf_token": "https://huggingface.co/settings/tokens",
    "unreal": "https://www.unrealengine.com/en-US/download",
}
COMPONENTS = ("gpu_driver", "wsl2", "blender", "comfyui", "flux", "vlm", "trellis", "hf_token", "unreal")


# --- detection (the existing checks) ------------------------------------------------------------

def scan_folder(folder: str | Path) -> dict:
    """The manifest's ComfyUI model files found in this folder (same path under it, same size):
    {path, exists, files, found, models (ids complete here), missing (required ids not complete)}."""
    from . import models
    p = Path(os.path.expanduser(str(folder)))
    comfy = [m for m in models.load() if m.root == "comfyui"]
    out = {"path": str(p), "exists": p.is_dir(), "files": 0, "found": 0, "models": [], "missing": []}
    if not p.is_dir():
        out["missing"] = [m.id for m in comfy if models.required(m)]
        return out
    for m in comfy:
        if m.need == "experimental":
            continue
        here = [f for f in m.source().files if (p / f.dest).is_file() and (p / f.dest).stat().st_size == f.size]
        out["files"] += len(m.source().files)
        out["found"] += len(here)
        if len(here) == len(m.source().files):
            out["models"].append(m.id)
        elif models.required(m):
            out["missing"].append(m.id)
    return out


def comfy_folders(extra: str | None = None) -> list[dict]:
    """The configured ComfyUI models folder, the default (<ComfyUI folder>/models) and `extra`."""
    cands = [config.get("comfyui.models_dir") or "",
             str(Path(os.path.expanduser(str(config.get("comfyui.service.cwd") or ""))) / "models")
             if config.get("comfyui.service.cwd") else "", extra or ""]
    out, seen = [], set()
    for c in cands:
        if not c:
            continue
        key = os.path.realpath(os.path.expanduser(c))
        if key in seen:
            continue
        seen.add(key)
        s = scan_folder(c)
        s["configured"] = bool(config.get("comfyui.models_dir")) and key == os.path.realpath(
            os.path.expanduser(str(config.get("comfyui.models_dir"))))
        out.append(s)
    return out


def facts(extra_folder: str | None = None) -> dict:
    """Everything the rows are made from, read from this machine (tests pass their own)."""
    from . import gpu, models, profiles, setup, wsl
    st = wsl.status()
    gpus = gpu.list_gpus()
    try:
        sm = profiles.summary()
        prof = sm["profile"]
        profile = {"id": prof["id"], "title": prof["title"], "how": "set" if sm["configured"] else "detected"} if prof else None
    except Exception:   # noqa: BLE001  (no GPU tools: no profile; the row says so)
        profile = None
    feats = {}
    for feat, req in (("comfy", True), ("vlm", True), ("trellis", False)):
        ms = [m for m in models.load() if m.feature == feat and (models.required(m) if req else m.need != "experimental")]
        miss = [m for m in ms if models.status(m)["state"] != "installed"]
        feats[feat] = {"missing": [m.id for m in miss], "gated": [m.id for m in miss if m.gated and m.gated != "manual"],
                       "page": ms[0].page if ms else "", "bytes": sum(models.status(m)["missing_bytes"] for m in miss)}
    root = config.ap_root()
    local = config.backends().get("local", {})
    vlm_backend = (local.get("models", {}).get(str(local.get("model", "32b"))) or {}).get("backend")
    vlm_env = root / ("envs/llamacpp/llama-server" if vlm_backend == "llamacpp" else "envs/qwen-vl/bin/python")
    from . import blender as _blender
    try:
        bl = setup._test_blender(str(_blender.blender_bin()), {})["ok"]
    except Exception:   # noqa: BLE001
        bl = bool(setup._blender_candidates())
    url = config.get("comfyui.url") or "http://127.0.0.1:8188"
    comfy_up = bool(setup._probe_comfy(url))
    launch = setup._check_launch(str(config.get("comfyui.service.launch") or ""), {})
    ue = config.get("unreal.editor_cmd") or ""
    return {
        "os": "windows" if st["wsl"] else ("linux" if sys.platform.startswith("linux") else sys.platform),
        "wsl2": bool(st.get("wsl2")) if st["wsl"] else None,
        "vendor": gpu.vendor(), "gpus": gpus, "profile": profile,
        "blender": bl, "comfyui": comfy_up or bool(launch.get("ok")),
        "models": feats, "vlm_env": vlm_env.exists(),
        "trellis_env": (root / "envs/trellis2/bin/python").exists(),
        "hf_token": bool(models.hf_token()),
        "unreal": bool(ue) and bool(setup._test_unreal(ue, {}).get("ok")),
        "comfy_models_dir": config.get("comfyui.models_dir") or "",
        "folders": comfy_folders(extra_folder),
    }


# --- the rows ------------------------------------------------------------------------------------

def _size(n) -> str:
    return f" ({n / 1e9:.0f} GB)" if n else ""


def _row(id_, name, state, sentence="", link=""):
    return {"id": id_, "name": name, "state": state, "sentence": sentence, "link": link}


def items(f: dict) -> list[dict]:
    """One row per component (COMPONENTS order): ready, missing, optional or setup (Setup installs it), with a sentence and
    a link for the ones that aren't ready."""
    win = f["os"] == "windows"
    out = []
    nv = f.get("vendor") == "nvidia" or (win and not f.get("vendor"))
    if f["gpus"]:
        out.append(_row("gpu_driver", "GPU driver", "ready"))
    elif nv:
        out.append(_row("gpu_driver", "GPU driver", "missing",
                        "No NVIDIA GPU answered: install the NVIDIA driver" + (" in Windows (WSL2 uses it)" if win else "")
                        + ", then click Check again.", LINKS["nvidia"]))
    else:
        out.append(_row("gpu_driver", "GPU driver", "missing",
                        "No GPU answered: install ROCm for an AMD card (or the NVIDIA driver for an NVIDIA card), "
                        "then click Check again.", LINKS["rocm"]))
    if win:
        out.append(_row("wsl2", "WSL2", "ready") if f.get("wsl2") else
                   _row("wsl2", "WSL2", "missing", "Terraformer on Windows runs in WSL 2 (beta, untested): install or "
                        "upgrade it with Windows' own guide.", LINKS["wsl"]))
    out.append(_row("blender", "Blender", "ready") if f["blender"] else
               _row("blender", "Blender", "missing", "Install Blender 4.2 or newer; Setup finds it in its usual place.",
                    LINKS["blender"]))
    out.append(_row("comfyui", "ComfyUI", "ready") if f["comfyui"] else
               _row("comfyui", "ComfyUI", "setup", "Setup installs a local ComfyUI for you (ticked on the Components page; "
                    "nothing to download yourself). To use one you already have, set its folder on the Connections page.",
                    LINKS["comfyui"]))
    m = f["models"]
    reuse = [x for x in f.get("folders", []) if x["exists"] and x["models"] and not x.get("configured")]
    if not m["comfy"]["missing"]:
        out.append(_row("flux", "FLUX.1-dev (ComfyUI models)", "ready"))
    elif not f.get("comfy_models_dir") and not reuse:
        out.append(_row("flux", "FLUX.1-dev (ComfyUI models)", "missing",
                        "Set your ComfyUI models folder, accept the FLUX.1-dev license on its page, and Setup downloads "
                        "the models.", LINKS["flux"]))
    else:
        out.append(_row("flux", "FLUX.1-dev (ComfyUI models)", "missing",
                        f"{len(m['comfy']['missing'])} ComfyUI model(s) still to download: accept the FLUX.1-dev license "
                        "on its page (signed in to Hugging Face), then run Setup again.", LINKS["flux"]))
    out.append(_row("vlm", "Vision model", "ready") if not m["vlm"]["missing"] and f["vlm_env"] else
               _row("vlm", "Vision model", "missing", "The local vision model (Qwen3-VL) isn't installed yet: Setup "
                    f"downloads it{_size(m['vlm'].get('bytes'))}; no account needed.", m["vlm"]["page"]))
    out.append(_row("trellis", "TRELLIS (3D models)", "ready") if not m["trellis"]["missing"] and f["trellis_env"] else
               _row("trellis", "TRELLIS (3D models)", "optional", "Optional: TRELLIS.2 turns concept images into 3D "
                    "models; tick it on the Components page to install it.", m["trellis"]["page"]))
    gated = m["comfy"]["gated"] + m["trellis"]["gated"]
    if f["hf_token"] or not gated:
        out.append(_row("hf_token", "Hugging Face token", "ready"))
    else:
        out.append(_row("hf_token", "Hugging Face token", "missing",
                        f"Gated models ({', '.join(gated)}) need a Hugging Face read token: create one and paste it on "
                        "the Connections page.", LINKS["hf_token"]))
    out.append(_row("unreal", "Unreal Engine (optional)", "ready") if f["unreal"] else
               _row("unreal", "Unreal Engine (optional)", "optional", "Optional: Unreal Engine 5 for the Unreal tab; "
                    "install it from the Epic Games launcher, everything else works without it.", LINKS["unreal"]))
    return out


def report(extra_folder: str | None = None, f: dict | None = None) -> dict:
    """{os, os_label, gpu, vram_gb, profile, items (all), shown (missing and optional), missing,
    folders (ComfyUI models folders scanned: what each already holds)}."""
    f = f or facts(extra_folder)
    rows = items(f)
    from . import wsl
    win = f["os"] == "windows"
    g = f["gpus"]
    return {"os": f["os"], "os_label": wsl.LABEL if win else ("Linux" if f["os"] == "linux" else f["os"]),
            "gpu": (f["vendor"] or "none").upper() if f["vendor"] != "amd" else "AMD",
            "gpus": [f"{x['name']} ({x['vram_gb']:.0f} GB)" for x in g], "vram_gb": max((x["vram_gb"] for x in g), default=0),
            "profile": f["profile"], "items": rows, "shown": [r for r in rows if r["state"] != "ready"],
            "missing": sum(r["state"] == "missing" for r in rows), "folders": f.get("folders", [])}


def lines(rep: dict) -> list[str]:
    """`ap doctor`'s section."""
    p = rep["profile"]
    out = ["What you still need",
           f"  This machine: {rep['os_label']}; GPU: {', '.join(rep['gpus']) or 'none found'}; "
           f"hardware profile: {p['title'] + ' (' + p['how'] + ')' if p else 'none'}"]
    for r in rep["shown"]:
        out.append(f"  {'✗' if r['state'] == 'missing' else '-'} {r['name']}: {r['sentence']}")
        if r["link"]:
            out.append(f"      {r['link']}")
    for fo in rep["folders"]:
        if fo["exists"] and fo["found"] and not fo.get("configured"):
            out.append(f"  Found {fo['found']} of {fo['files']} ComfyUI model files in {fo['path']}: use it as the ComfyUI "
                       "models folder (ap setup) and they won't be downloaded again.")
    if not rep["shown"]:
        out.append("  Nothing: every component is ready.")
    return out

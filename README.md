# Terraformer

Terraformer is a local-first 3D environment pipeline, built around ComfyUI, that takes a world
layout to a lit Unreal Engine 5.8 scene. You lay out a place (roads, districts, landmarks,
terrain); the pipeline builds a Blender greybox, renders control passes for each camera shot,
paints concept frames from them with FLUX.1-dev, plans which assets the frames call for, builds
those assets, places the whole city in Unreal and lights a scene that matches the approved
frame. Everything runs on your own machine with open-weights models: no partner or API nodes,
no hosted services. It is an entry for the Comfy "Open Source Higgsfield" challenge, category
**Open Weights Only**.

The repo is [`hawkins-m/terraformer-3d`](https://github.com/hawkins-m/terraformer-3d) (renamed
from `asset-pipeline` on 2026-10-18; the old URL redirects). The command line is `ap`, the Python
package is `asset_pipeline`, and the app (`ap ui`) is called Terraformer.

## Demo video

▶ **[Watch the demo](https://example.com/terraformer-demo)** (placeholder: the link goes here
once the video is up).

## Features

- **Layout to greybox.** A layout (`site/layout.json`, or the City planner: districts,
  typologies, height fields, enclosures, terrain ranges) becomes a Blender greybox, built
  headless on the CPU (about 4 s for the starter layout).
- **Exact shot passes.** For each camera shot Blender renders depth, edges, object ids and
  normals (about 3.5 s per shot).
- **Concept frames through ComfyUI.** FLUX.1-dev with ControlNet Union Pro 2.0 (depth) and a Flux
  Redux style anchor paints frames that keep the greybox's layout and scale, plus an optional
  img2img realism pass. All workflows (`workflows/`) use core nodes only.
- **Asset library from the frames.** The id pass says which greybox object is where in an
  approved frame; SAM 3.1, OneFormer and Depth Anything V2 check the crops; FLUX.1-dev makes
  reference sheets.
- **Procedural architecture from a VLM brief.** Qwen3-VL (local, llama.cpp) reads proportions,
  palette and the material of each storey band off a sheet; a facade grammar builds the
  building in Blender with ambientCG CC0 scans. Irregular assets and props can go through
  TRELLIS.2 instead.
- **Reroll with guidance.** The vision model proposes brief edits for a new version, offers only
  the settings a builder uses, says what changed and skips builds identical to an earlier one.
- **Versions and variants.** Every build is a version (pin, restore, compare, prune, "use this
  version"); variant sets, duplicated entries and attached pieces; files stored once by hash.
- **Review gate.** Approve or send back each build, with silhouette and openings checks against
  the locked frames. Only approved builds reach look scenes.
- **The whole city in Unreal, headless.** `ap ue build` exports the library and places every
  build at its real spot (ground-aware, single-building slots and props, baked textures), with a
  check at the end of every build.
- **Look match.** Tints, exposure and the far ridge are read from the approved frame (OneFormer,
  Depth Anything V2) and rendered as an Unreal look scene.
- **No terminal needed.** Every step is a button in the app, with a guide panel, honest step
  states, job lanes with queue positions, VRAM checks and real progress.
- **Setup that checks itself.** `ap setup`, `ap doctor` and `ap models` (pinned, checksummed
  model manifest), with hardware profiles for ROCm (one or two GPUs) and NVIDIA.

## Built during the challenge

The challenge started on 2026-10-08. The last commit before it is tagged
[`pre-challenge`](https://github.com/hawkins-m/terraformer-3d/tree/pre-challenge) (`0cef52e`,
2026-10-07); everything since is in the
[pre-challenge...main compare](https://github.com/hawkins-m/terraformer-3d/compare/pre-challenge...main)
(about 150 commits, 245 files). Before the challenge the repo had the greybox, shot passes,
FLUX frame generation, the city planner and the first browser UI. Added during it:

- **Unreal city build**: `ap export --assets` and `ap ue build` place the whole city's library
  builds in the Unreal level headless, at their real places; `ap ue stale`, `--only-stale`
  re-imports; look scenes from presets (`ap ue look`) with frame-read tints, exposure and ridge.
- **Library and asset loop**: library planning from locked shots, SAM 3.1 / OneFormer / Depth
  Anything crop checks, procedural facade grammar, material library (ambientCG), TRELLIS.2 route,
  review gate with silhouette and openings checks, areas and batch commands.
- **Reroll guidance**: the vision model proposes brief edits to accept before a build; reroll
  says what changed, offers only the settings a builder uses and skips identical builds.
- **Versions and variants**: asset version bank (pin, restore, compare, prune), variant sets,
  duplicated entries, attached pieces, a shared file store by hash, Storage and Properties
  dialogs.
- **Comfy SDK backend**: every ComfyUI call behind the `ComfyClient` interface, `sdk` (Comfy SDK
  through `comfy-api-proxy`) or `httpx`, with a live parity test; ComfyUI start/stop from the app.
- **Setup and doctor**: one user config outside the repo, `ap setup` and the first-run Setup
  screen, `ap doctor` and the Status box, features switched off with the reason; a clean-install
  test from a fresh `HOME`.
- **Models manager**: `config/models.toml` with pinned revisions, sizes and checksums; `ap models
  list | download | verify` with resume, disk check and gated-repo help.
- **Hardware profiles**: ROCm 2 GPUs, ROCm 1 GPU and NVIDIA (untested), with GPU lanes and memory
  management.
- **The app**: an XP-style classic shell, job dialogs with real progress and Cancel, a UX pass
  (step headers, guide panel, empty states that name the prerequisite), a desktop launcher, and
  a UI for every runbook step.
- **Open-weights scope**: MIT license, paid SDKs moved to an optional extra that refuses to run
  by default, experimental paths marked and kept out of the defaults; the RUNBOOK and this README.

## Requirements

- Linux (verified on Ubuntu 26.04). Windows through WSL2 with an NVIDIA GPU is **beta**
  ([below](#windows-wsl2--nvidia-beta)); macOS is not supported.
- Python 3.12 (`python3.12`; the default `python3` may be newer, and every venv here uses 3.12).
- ComfyUI 0.24 or newer (the tested version). The workflows use only core nodes (SAM 3.1
  included).
- Blender 4.2 or newer (verified with 5.2.2; the snap works).
- Unreal Engine 5.8 (free with an Epic account) for the last steps only: import, city build and
  look scenes. The starter demo does not need it.
- GPU memory:
  - Concept frames (Flux fp8, ControlNet, Redux): about 24 GB.
  - Vision LLM: Qwen3-VL-32B Q5_K_M takes about 27 GB on its own GPU; with 24 GB or less, use
    the 8B (`ap config set local.model 8b`).
  - Two GPUs are recommended (ComfyUI on one; the VLM, TRELLIS and Unreal renders on the other).
    One 32 GB GPU works with jobs run one after another.
- Disk: about 50 GB of required models (below), 18 GB more for TRELLIS.2; several hundred GB is
  comfortable with Unreal projects. All of it goes under one storage folder (default
  `~/.local/share/terraformer`).

**Tested on** one machine: Ubuntu 26.04, 2x AMD Radeon AI PRO R9700 (32 GB, gfx1201), ROCm 7.2.4,
ComfyUI 0.24.0, Blender 5.2.2, Unreal Engine 5.8.3.

- Full test suite: 635 tests (633 offline; 2 more need a live ComfyUI), all passing.
- Clean install (2026-10-23) from a fresh clone with an empty `HOME`: `ap setup`, the first-run
  Setup screen, `ap doctor`, a new project and its greybox (the model downloads and GPU jobs were
  left out).
- The two ComfyUI backends give identical SAM 3.1 results (`pytest -m comfy`); the SDK backend's
  cancel and error handling were checked against a live ComfyUI.
- Starter path (`scripts/demo.sh starter`) through the SDK backend, on a fresh storage folder:
  greybox and two shots' passes in about 16 s (CPU), two concept frames in 67 s.
- Area path (`scripts/demo.sh area`) on one shot of a larger private test city (about 2.3k
  buildings, 381 exported assets, 29k instances): two frames about 1 min each, 11 procedural
  builds in about 1 min, about 620 per-building instances near the shot in 25 min (CPU), export
  about 3 min, Unreal import about 2 min, look scene 5-11 min (GPU).

**NVIDIA / CUDA is untested.** The code has a CUDA path, but only ROCm has been run.

## Install

### Hardware profiles

`ap setup` detects the GPU vendor, count and VRAM (`rocm-smi` / `nvidia-smi`) and preselects a
profile; you can override it (`ap config set hardware.profile ...`). The profile sets the PyTorch
build the model environments install, which GPU each job lane uses, and the scheduling.

| Profile | PyTorch | GPU lanes | Status |
|---|---|---|---|
| Ubuntu + ROCm, 2 GPUs (`rocm-2gpu`) | ROCm 7.2 | TRELLIS and the vision model on GPU 0, ComfyUI on GPU 1 | the tested setup |
| Ubuntu + ROCm, 1 GPU (`rocm-1gpu`) | ROCm 7.2 | everything on GPU 0, one job at a time | scheduling tested by simulation only |
| Ubuntu + NVIDIA (`nvidia`) | CUDA 12.8 | as above, by GPU count | **untested**: the CUDA paths have never run |
| Windows (WSL2) + NVIDIA, beta (`wsl-nvidia`) | CUDA 12.8 | as above, by GPU count | **beta, untested**: the `nvidia` profile inside WSL2 |

What differs per profile (the steps below are otherwise the same):

- **ROCm, 2 GPUs**: the steps as written.
- **ROCm, 1 GPU**: in `ap setup` (step 3) pick `Ubuntu + ROCm, 1 GPU` (both GPU roles on GPU 0).
  With less than ~28 GB on the card, take the 8B vision model before downloading:
  `ap config set local.model 8b` (the required download drops to 43.7 GB). ComfyUI, TRELLIS and
  the vision model share one slot and run in submit order; ComfyUI is asked to unload before
  TRELLIS or the vision model starts, and the vision model is unloaded before a ComfyUI job.
- **NVIDIA (untested)**: pick `Ubuntu + NVIDIA (untested)` in `ap setup` before installing the
  model environments, so they get CUDA wheels (`cu128`; or `export AP_TORCH=cu128` for the install
  scripts). Skip `scripts/setup_rocm_toolchain.sh`; TRELLIS.2 needs the CUDA toolkit for its
  extensions. The llama.cpp build is the Vulkan one, which should run on NVIDIA too. Expect to
  debug.

Each feature has a VRAM floor from the manifest (ComfyUI image models ~24 GB, the 32B vision model
~28 GB, the 8B ~19 GB, TRELLIS.2 ~16 GB); a feature the card can't run is switched off in the app
with the reason. Memory held by programs the app didn't start is never touched.

### Steps

Your settings live in one user config outside the repo, `~/.config/terraformer/config.toml` (API
keys in `secrets.toml` beside it, mode 0600); environment variables win over it. To put the
storage folder on a big drive, set it before downloading:
`ap config set paths.ap_root /path/to/storage/terraformer` (or `export AP_ROOT=...`).

1. **The pipeline.**
   ```bash
   git clone https://github.com/hawkins-m/terraformer-3d.git && cd terraformer-3d
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -e '.[dev]'
   mkdir -p ~/.local/bin && ln -sf "$PWD/scripts/ap" ~/.local/bin/ap   # `ap` in any shell, venv or not
   ```
   The `[paid]` extra (Gemini and Claude SDKs) is not needed; the paid backends refuse to run
   unless `AP_ALLOW_PAID_APIS=1` is set.

2. **ComfyUI.** Install ComfyUI yourself (its own venv; this repo only talks to it over HTTP and
   never modifies it) and tell the pipeline where its models folder is and how to launch it
   (`{port}` is filled in):
   ```bash
   ap config set comfyui.models_dir ~/ComfyUI/models
   ap config set comfyui.service.launch "~/ComfyUI/venv/bin/python main.py --listen 127.0.0.1 --port {port}"
   ap config set comfyui.service.cwd ~/ComfyUI
   ```
   `ap setup` (step 3) can find these from a running ComfyUI. `ap comfy start|stop|restart|status`
   (or **Start ComfyUI** in the app) only launches and stops it.

   **Run ComfyUI through the Comfy SDK backend** (the default): the official Comfy SDK
   (comfy-sdk 0.5.0, Comfy API v2) through `comfy-api-proxy` 0.1.6, next to your local ComfyUI.
   ```bash
   scripts/comfy_api_proxy.sh install      # its own venv, port 8190
   ap config set comfyui.backend sdk       # the repo default; "httpx" uses ComfyUI's own HTTP API, no proxy
   ap comfy start                          # starts ComfyUI if needed, then the proxy
   ```
   `AP_COMFY_BACKEND=sdk|httpx` overrides the config for one shell. ComfyUI 0.24 and older lack
   the proxy's cancel route, so Stop falls back to `/queue` delete plus `/interrupt`. The proxy
   does not return 3D outputs; only the experimental Hunyuan path needs `httpx`.

3. **Setup.** `ap setup` asks for the storage folder, the hardware profile, the ComfyUI backend
   and address, Blender, Unreal (optional) and the GPU for each role; what it finds is pre-filled
   and each answer is tested. `ap setup --check` only tests, `ap setup --yes` takes what it
   found. The app opens the same Setup screen on first run (later: Tools › Setup…).
   `ap config show` prints what is in effect; `ap config set SECTION.KEY VALUE` changes one value.

4. **Models (about 50 GB).** Everything is listed in [config/models.toml](config/models.toml)
   with pinned revisions, sizes and checksums:
   ```bash
   ap models list                                # every model, installed or missing, and its size
   ap models download --required --dry-run       # the plan and the disk check
   ap models download --required                 # 50.6 GB: ComfyUI 24.3, vision model 24.4 (32B), segmentation/depth 1.9
   ap models verify
   ```
   With the 8B vision model (`ap config set local.model 8b` first) it is 43.7 GB. Files already
   there are skipped, partial files resume, checksums are verified. FLUX.1 Redux dev is gated:
   the download stops and says what to do (accept the license on the model page,
   then a Hugging Face read token via `hf auth login` or `ap setup`). `--json` prints progress
   events for scripts.

5. **Model runtimes.**
   ```bash
   scripts/install_llamacpp_vlm.sh llamacpp 32b   # llama.cpp Vulkan build for Qwen3-VL (or 8b)
   scripts/install_segdepth.sh                    # OneFormer and Depth Anything V2 Small
   ```
   The vision server starts on demand and unloads when idle (`ap vlm`).

6. **TRELLIS.2** (optional, for irregular assets; not on the demo path). On ROCm/gfx1201:
   ```bash
   scripts/setup_rocm_toolchain.sh      # one time, no sudo (skip on NVIDIA)
   scripts/install_trellis2.sh venv deps trellis nvdiffrast cumesh flexgemm ovoxel nvdiffrec weights verify
   ```
   The `weights` stage is `ap models download --feature trellis --all` (18.0 GB). DINOv3 is a
   gated Meta repo; the download explains and names an ungated mirror
   (`ap config set models.dinov3_repo ...`).

7. **Doctor.** `ap doctor` checks storage, GPUs, ComfyUI and the proxy, that ComfyUI sees each
   model file the workflows load, Blender, Unreal, the vision model, segmentation and TRELLIS,
   and prints what is missing with the command that fixes it. The app shows the same in its
   Status box (Tools › Doctor…) and switches off only what a missing piece needs.

8. **Desktop launcher** (optional): `ap launcher install` adds **Terraformer** to the Linux
   applications menu, so day-to-day work needs no terminal.

### Windows (WSL2) + NVIDIA, beta

The pipeline runs inside WSL2 (Ubuntu) as the normal Linux install with the `wsl-nvidia` profile;
a Windows launcher starts it and opens your browser. **Beta: none of this has run on a Windows
machine yet** (the tests mock `wslpath` and the Windows programs).

Needs Windows 11, or Windows 10 21H2 (build 19044) or newer, and a current NVIDIA driver for
Windows (Game Ready or Studio). Do not install a Linux NVIDIA driver inside Ubuntu: WSL uses the
Windows one.

1. Download the repo (Code › Download ZIP, or `git clone`) anywhere on Windows and double-click
   `windows\install.bat` (or run `windows\install.bat -NoDesktopShortcut`, `-Distro NAME`, `-Yes`).
   It
   - checks the Windows version and WSL; if WSL is missing it asks, installs it (admin prompt)
     and asks you to restart Windows and run `install.bat` again;
   - installs `Ubuntu-24.04` in WSL if it is missing (it asks first; Ubuntu then asks for a Linux
     user name and password), and converts it to WSL 2 if needed;
   - checks that `nvidia-smi` works inside WSL;
   - clones the repo inside WSL at `~/terraformer-3d` and runs the Linux install there:
     `scripts/install.sh --profile wsl-nvidia` (venv, `ap`, the profile, then `ap setup`);
   - writes the launcher `%LOCALAPPDATA%\Terraformer\Terraformer.bat` and a **Terraformer**
     shortcut in the Start Menu and on the desktop.
2. Inside WSL (Start Menu › Ubuntu), do the rest of the [Steps](#steps) as on Linux: ComfyUI
   (installed inside WSL), models, model runtimes, `ap doctor`. Skip
   `scripts/setup_rocm_toolchain.sh` and `ap launcher install`.
3. Start **Terraformer** from the Start Menu. It runs `ap ui` inside WSL and opens your default
   browser at <http://localhost:8700/>. Closing its window (or Ctrl+C in it) stops `ap ui`; an
   `ap ui` that was already running is left alone.

In Setup (`ap setup` or the app's Setup screen):

- **Storage** stays on the WSL filesystem (the default `~/.local/share/terraformer`). The Windows
  drives (`C:\` is `/mnt/c` in WSL) are slow from WSL and break the hard links the file store and
  backups use; Setup and `ap doctor` warn when storage is there.
- **Blender**: a Blender installed inside WSL (e.g. from blender.org's Linux build) is preferred
  and found first. A Windows Blender works too: paste its Windows path
  (`C:\Program Files\Blender Foundation\Blender 4.2\blender.exe`).
- **Unreal** stays on Windows: paste
  `C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe` (or the
  `UE_5.8` folder). Put the Unreal projects folder on a Windows drive (e.g.
  `C:\Users\you\Documents\Unreal Projects`); Setup warns otherwise.

Windows paths are stored as WSL sees them (`wslpath -u`). A Windows `.exe` runs through WSL
interop: the pipeline gives it Windows paths (`wslpath -w`, including the paths inside its JSON
job files and `AP_UE_ARGS` through `WSLENV`) and turns the Windows paths in the JSON files it
writes back into Linux paths. `ap doctor` shows the WSL check with the beta label and what is
untested.

Known limits (beta):

- Untested end to end on Windows: the CUDA wheels, TRELLIS.2's extensions and the llama.cpp
  Vulkan build on WSL's NVIDIA driver; Windows Blender and Unreal through interop.
- Stop on a job running a Windows program ends the Linux side; the Windows process may keep
  running (end it in Task Manager).
- The "Unreal Editor has this project open" check can't see an editor window on Windows: close
  it yourself before an import or backup.
- Only paths in arguments, in the JSON files named on the command line and in `AP_UE_ARGS` are
  translated; Unreal reading projects on `\\wsl.localhost` is slow, hence the Windows-drive
  projects folder.
- WSL 2 must reach `localhost` (the default `localhostForwarding`, or mirrored networking) for the
  browser to open the app. GPU memory in WSL is shared with Windows: close GPU-heavy Windows apps.

### ROCm notes

Details in [docs/notes/machine-and-gpu.md](docs/notes/machine-and-gpu.md).

- Tall GEMMs silently return corrupt results on gfx1201; `scripts/gfx1201_guard.py` chunks
  `F.linear` in any torch process that installs it.
- `hipMemcpy2D` device-to-device copies only the first 2^20 rows and still reports success; the
  CuMesh build for TRELLIS.2 is patched to chunk them.
- Never set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` on ROCm: silent NaNs. (The NVIDIA
  profile sets it for GPU jobs.)
- Build HIP extensions with `scripts/setup_rocm_toolchain.sh` once, then
  `source scripts/rocm_build_env.sh` in the shell that builds. No flash-attn; sdpa is used.

## Quick start

```bash
ap setup                           # first run: check and save your settings
ap models download --required      # ~50 GB
ap doctor                          # what is installed, what is missing, how to fix it
ap comfy start                     # ComfyUI and the SDK proxy
ap ui                              # the app, at http://127.0.0.1:8700 (or the desktop launcher)
scripts/demo.sh starter            # neutral starter layout, no Unreal needed
```

Closing the browser doesn't stop jobs; closing the Terraformer window does.

`demo.sh starter [SLUG]` creates a project (default `my-project`), a starter layout, the greybox,
the shot passes and a style text, and generates two concept frames for the `street` shot through
ComfyUI. Frames land under `<storage>/projects/my-project/`. In the app it is Help › Run demo….

The full path on one area of a project, brief to Unreal render:

```bash
scripts/demo.sh area my-project SHOT ENTRY [ENTRY ...]
```

This generates two new frames for the shot, builds the listed buildings procedurally, places
them near the shot's camera, exports GLBs, imports into Unreal headless and renders a look scene.
It needs an existing project with a shot and library entries; see `scripts/demo.sh`,
[USAGE.md](USAGE.md) and [RUNBOOK.md](RUNBOOK.md).

## Pipeline overview

**Style → Layout → Greybox → Shots → Frames → Library → Views and 3D → Review → Unreal**

Each step is a tab or button in the app (the [RUNBOOK](RUNBOOK.md) describes the clicks) and a
command. **Artist** marks a decision point; everything else runs unattended.

1. **Style.** `ap new`, `ap style text`: the brief and a style text (optional anchor images). **Artist:** writes the brief.
2. **Layout.** `ap site init` (or `--city`): roads, districts, landmarks, terrain. **Artist:** edits the layout.
3. **Greybox.** `ap site build`: terrain and greybox in headless Blender (CPU).
4. **Shots.** `ap shots render`: depth, edge, object-id and normal passes per camera shot (Blender Cycles, CPU).
5. **Frames.** `ap frames generate SHOT -n 4`: FLUX.1-dev, ControlNet Union Pro 2.0 (depth) and Redux through ComfyUI. **Artist:** picks and locks frames (`ap frames lock`).
6. **Library.** `ap library plan`, `ap library check` (SAM 3.1), `ap library sheets` (FLUX.1-dev reference sheets). **Artist:** decides each entry's source and picks the sheet view.
7. **Views and 3D.** `ap library brief` (Qwen3-VL reads the sheet), then `ap library build` (procedural Blender, ambientCG materials) or `ap library trellis` (TRELLIS.2); Reroll with guidance. **Artist:** edits the brief and picks versions.
8. **Review.** Library › Asset review, `ap library review apply`, silhouette and openings checks. **Artist:** approves or sends back with a note.
9. **Unreal.** `ap export`, `ap ue build` (the whole city, headless), `ap ue look PROJECT SHOT` (OneFormer and Depth Anything V2 read the frame's look). **Artist:** chooses and tunes the look preset.

## License

The code is **MIT**, see [LICENSE](LICENSE). The models and tools carry their own licenses.

### Models and licenses

All open weights, all run locally. The full list with sources, pinned revisions, sizes and
checksums is [config/models.toml](config/models.toml) (`ap models list --all`). **Commercial use requires a commercial license from each model's owner wherever that model's license calls for one; check every row below before using any output commercially.** Several are non-commercial, so the pipeline as configured
is for non-commercial use unless you swap those models.

| Model | Used for | License |
|---|---|---|
| FLUX.1-dev (fp8) | Concept frames | FLUX.1 [dev] Non-Commercial License |
| FLUX.1 Redux dev (gated) | Style anchor | FLUX.1 [dev] Non-Commercial License |
| SigLIP so400m patch14-384 (vision) | Redux image encoder | Apache-2.0 |
| ControlNet Union Pro 2.0 (Shakker-Labs) | Depth control of frames | FLUX.1-dev non-commercial (derivative) |
| SAM 3.1 (gated) | Text-prompted masks | Meta SAM License |
| Qwen3-VL 32B / 8B Instruct | Plans, material reading | Apache-2.0 |
| OneFormer Swin-L ADE20K | Frame classes | MIT |
| Depth Anything V2 Small | Frame depth | Apache-2.0 (Base, Large, Giant are CC-BY-NC-4.0 and not used) |
| TRELLIS.2-4B (optional) | Irregular assets | MIT |
| BiRefNet (TRELLIS.2) | Background removal | MIT |
| DINOv3 ViT-L/16 (TRELLIS.2, gated) | Image features | Meta DINOv3 License |
| ambientCG 2K sets | Materials | CC0 |

Code dependencies with notable licenses: nvdiffrast and nvdiffrec (NVIDIA Source Code License,
non-commercial), used by TRELLIS.2.

### Tools beyond ComfyUI

- **Blender 5.x** (GPL): greybox, data passes, procedural buildings, GLB export.
- **Unreal Engine 5.8** (free with an Epic account, Epic EULA): headless import and offscreen
  renders. Optional for the early stages.
- **TRELLIS.2**: irregular assets (optional).
- **llama.cpp**: serves the Qwen3-VL GGUFs.

### Experimental, not part of the entry

Wan 2.2 hero orbits, Hunyuan3D-2mv (Tencent Hunyuan Community License, not licensed in the EU,
UK or South Korea) and a Mask2Former A/B exist as prototypes and are not on any demo path.
Paid backends (Gemini, Claude) exist but are off by default and refuse to run without
`AP_ALLOW_PAID_APIS=1`; the entry does not use them. The SDK can also target Comfy Cloud; that
is untested and not used.

## Design note

![Terraformer's main window: the starter city's Shots tab, with a shot-pass render running in its job window](docs/images/terraformer-main.png)

Terraformer's interface is deliberately styled after Windows XP/Me-era desktop software: menu
bar, toolbar, group boxes, tabbed panels, status bar and Tahoma / MS Sans Serif-style type. Its
muted grey palette avoids near-white glare during long sessions. Each pipeline stage has its own
small animated job icon (frame generation, greybox building, 3D conversion, Unreal export and
more), so long GPU jobs stay readable at a glance. The intent is a calm, dense, tool-like
workspace for artists rather than a web-app look.

## Docs

- [USAGE.md](USAGE.md): running the tools and the UI.
- [RUNBOOK.md](RUNBOOK.md): the daily asset loop for one area, common failures.
- [PLAN.md](PLAN.md): design, stages and milestones.
- [CLAUDE.md](CLAUDE.md): working rules; [docs/notes/](docs/notes/): machine facts, measured findings and pitfalls.

## Credits

- TRELLIS.2 ROCm recipe: [bioritmovideo/trellis2-rocm-gfx1201](https://github.com/bioritmovideo/trellis2-rocm-gfx1201).
- nvdiffrast ROCm port: Painter3000 ([amd-nvdiffrast-rocm72-gfx1201](https://github.com/Painter3000/amd-nvdiffrast-rocm72-gfx1201)).
- Thanks to [egore/comfyui-trellis2-gguf-rocm](https://github.com/egore/comfyui-trellis2-gguf-rocm),
  whose notes first got CuMesh building on ROCm. The CuMesh fixes used here are our own.
- ambientCG for the CC0 materials; the authors of the models above.

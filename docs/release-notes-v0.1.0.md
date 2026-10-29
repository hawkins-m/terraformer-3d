# Terraformer v0.1.0

The first release of Terraformer, a local 3D environment pipeline that takes a city layout to a lit
Unreal Engine 5.8 scene, with ComfyUI and open-weights models on your own GPU. It is the entry for
Comfy's "Open Source Higgsfield" challenge, category **Open Weights Only**.

**Demo video:** [watch on YouTube](https://www.youtube.com/watch?v=R8xaOrSxGe4)

## Highlights

- **Layout to greybox to concept frames.** A city layout (or one of 20 city presets and 8 generated
  terrains) becomes a Blender greybox; each camera shot gets depth, edge, id and normal passes; FLUX.1-dev
  with ControlNet Union Pro 2.0 and a Flux Redux style anchor paints concept frames that keep the
  greybox's layout and scale.
- **An asset library from the frames.** The vision model (Qwen3-VL, local) plans what the approved frames
  call for; SAM 3.1, OneFormer and Depth Anything V2 check the crops; FLUX.1-dev makes reference sheets;
  a procedural facade grammar builds architecture in Blender with CC0 materials, and TRELLIS.2 handles
  irregular assets. Every build is a version, with a review gate against the locked frames.
- **The whole city in Unreal, headless.** `ap ue build` places every approved build at its real spot,
  with optional CC0 ground, paving, rocks, vegetation and water, a generated terrain as an Unreal
  Landscape, and a placement check at the end. Look scenes match the tints, exposure and far ridge of
  the approved frame.
- **ComfyUI through the official Comfy SDK.** Every ComfyUI call goes through one `ComfyClient`
  interface; the default `sdk` backend uses `comfy-sdk` through `comfy-api-proxy`, and `httpx` is the
  fallback (`AP_COMFY_BACKEND`). Workflows use core nodes only: no API or partner nodes.
- **Setup that checks itself.** A browser setup wizard (`scripts/install.sh`), `ap setup`, `ap doctor`
  and `ap models` (a pinned, checksummed model manifest with resumable downloads), with hardware
  profiles for ROCm (one or two GPUs) and NVIDIA (untested).
- **No terminal needed.** Every step is a button in the app (`ap ui`), with a guide panel, honest step
  states, job lanes with queue positions and real progress.

## Requirements

Linux (Windows through WSL2 with NVIDIA is beta), Python 3.12, ComfyUI 0.24+, Blender 4.2+, Unreal
Engine 5.8 for the last steps. About 24 GB of GPU memory for concept frames; two GPUs recommended.
About 50 GB of required models. See the README's Requirements section.

Tested on one machine: Ubuntu 26.04, 2x AMD Radeon AI PRO R9700 (gfx1201), ROCm 7.2.4. The CUDA path
exists but has not been run.

## Licenses

Code: MIT. Models and assets download from their sources and keep their own licenses
([THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md)). FLUX.1-dev and the models derived from it
are non-commercial, so the pipeline as configured is for non-commercial use. CC0 assets come from
ambientCG and Poly Haven; Megascans assets are never downloaded or redistributed.

## Known issues

- Clean up can produce wrong scale or broken texture bakes; it is optional.
- The generated-terrain Landscape (APLandscape plugin) builds for Linux engines only.
- The Water plugin's ocean crashes the headless build in UE 5.8: the build uses the Simple sea instead.
- Describe the terrain falls back to keyword matching when the local vision model isn't running.
- The utopia layout preset keeps its old overlaps.

## Since the last review

- Describe the terrain: a negated word ("flat plain with no river", "without mountains", "no coast")
  now wins over the keyword.
- Unreal stage: "Render verified" only when every required shot and a look run are newer than the
  current build; otherwise "Render available".
- Ready for Unreal: the placement-issue column says its results were checked on the previous build.

The full history since the challenge began is in the
[pre-challenge...v0.1.0 compare](https://github.com/hawkins-m/terraformer-3d/compare/pre-challenge...v0.1.0).

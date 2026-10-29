# Third-party licenses

Terraformer's own code is MIT ([LICENSE](LICENSE)). This repository contains **no model weights, textures,
meshes or Unreal assets**: setup downloads each one from its original source, pinned to a revision with a
checksum ([config/models.toml](config/models.toml), `ap models list --all`). What you download stays under
its own license, listed here. Read each license before using outputs commercially: several below are
non-commercial, so the pipeline as configured is for non-commercial use unless you swap those models.

## Models

| Model | Source | Needed | License |
|---|---|---|---|
| FLUX.1-dev (fp8) | [Comfy-Org/flux1-dev](https://huggingface.co/Comfy-Org/flux1-dev) | required | [FLUX.1 [dev] Non-Commercial License](https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md) |
| FLUX.1 Redux dev | [black-forest-labs/FLUX.1-Redux-dev](https://huggingface.co/black-forest-labs/FLUX.1-Redux-dev) (gated) | required | FLUX.1 [dev] Non-Commercial License |
| SigLIP so400m patch14-384 | [Comfy-Org/sigclip_vision_384](https://huggingface.co/Comfy-Org/sigclip_vision_384) | required | Apache-2.0 |
| ControlNet Union Pro 2.0 | [Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro-2.0](https://huggingface.co/Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro-2.0) | required | FLUX.1 [dev] Non-Commercial License (derivative) |
| SAM 3.1 | [Comfy-Org/sam3.1](https://huggingface.co/Comfy-Org/sam3.1) (gated) | required | Meta SAM License |
| OneFormer Swin-L ADE20K | [shi-labs/oneformer_ade20k_swin_large](https://huggingface.co/shi-labs/oneformer_ade20k_swin_large) | required | MIT |
| Depth Anything V2 Small | [depth-anything/Depth-Anything-V2-Small-hf](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) | required | Apache-2.0 (the Base, Large and Giant checkpoints are CC-BY-NC-4.0 and not used) |
| Qwen3-VL 32B Instruct (GGUF) | [unsloth/Qwen3-VL-32B-Instruct-GGUF](https://huggingface.co/unsloth/Qwen3-VL-32B-Instruct-GGUF) | one of the two | Apache-2.0 |
| Qwen3-VL 8B Instruct | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) | one of the two | Apache-2.0 |
| TRELLIS.2-4B | [microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) | optional | MIT |
| TRELLIS image large | [microsoft/TRELLIS-image-large](https://huggingface.co/microsoft/TRELLIS-image-large) | optional | MIT |
| BiRefNet | [ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) | optional | MIT |
| DINOv3 ViT-L/16 | [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) (gated) | optional | Meta DINOv3 License |

Experimental, off every default path and not part of the challenge entry:

| Model | Source | License |
|---|---|---|
| FLUX.1 Depth dev LoRA | [black-forest-labs/FLUX.1-Depth-dev-lora](https://huggingface.co/black-forest-labs/FLUX.1-Depth-dev-lora) | FLUX.1 [dev] Non-Commercial License |
| Hunyuan3D-2mv (turbo) | [tencent/Hunyuan3D-2mv](https://huggingface.co/tencent/Hunyuan3D-2mv) | Tencent Hunyuan Community License (not licensed in the EU, UK and South Korea) |
| Wan 2.2 I2V, Wan 2.1 encoders | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged), [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | Apache-2.0 |
| Qwen3-VL 8B (GGUF), Qwen3-VL 30B-A3B (GGUF) | [Qwen/Qwen3-VL-8B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF), [unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF](https://huggingface.co/unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF) | Apache-2.0 |
| Mask2Former Swin-L ADE20K | [facebook/mask2former-swin-large-ade-semantic](https://huggingface.co/facebook/mask2former-swin-large-ade-semantic) | see the model card before use |

## Assets (CC0)

Fetched from their sites on first use (or ahead with `ap models download --feature dressing --all`), never bundled.

| Asset | Source | Used for | License |
|---|---|---|---|
| ambientCG 2K material sets | [ambientCG](https://ambientcg.com) | Procedural building materials | CC0 1.0 |
| Grass004, Ground106, Rock030, Ground093C (2K) | [ambientCG](https://ambientcg.com) | World dressing: ground textures | CC0 1.0 |
| PavingStones054, PavingStones119, PavingStones128 (2K) | [ambientCG](https://ambientcg.com) | World dressing: street and plaza paving | CC0 1.0 |
| rock_07, boulder_01, shrub_02 (1k glTF) | [Poly Haven](https://polyhaven.com) | World dressing: rocks, shrubs | CC0 1.0 |

**Megascans (Fab)** assets are licensed for use in Unreal Engine only. Terraformer never downloads or
redistributes them, and nothing from Fab is in this repository. If you own them, you add them to your own
Unreal project and map them there (`ap ue scan-assets`).

## Tools

| Tool | Used for | License |
|---|---|---|
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | Every image-model step | GPL-3.0 |
| [comfy-sdk](https://pypi.org/project/comfy-sdk/) 0.5.0 | The default ComfyUI backend | MIT |
| comfy-api-proxy 0.1.6 | Comfy API v2 in front of a local ComfyUI | MIT |
| [Blender](https://www.blender.org) | Greybox, shot passes, procedural buildings, GLB export | GNU GPL (v3 for the program as a whole) |
| [Unreal Engine 5.8](https://www.unrealengine.com) | Import, city build, look scenes | Epic Games EULA |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Serves the Qwen3-VL GGUFs | MIT |
| [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) code | Irregular assets (optional) | MIT |
| [nvdiffrast](https://github.com/NVlabs/nvdiffrast), nvdiffrec | Used by TRELLIS.2 (optional) | NVIDIA Source Code License (non-commercial) |

ComfyUI, Blender and Unreal Engine run as separate programs that Terraformer talks to (over HTTP, or by
running them headless with a script); none of their code is copied into this repository.

## Python dependencies

Installed from PyPI by `pip install`, not vendored: fastapi (MIT), uvicorn (BSD-3-Clause), httpx
(BSD-3-Clause), pydantic (MIT), pillow (MIT-CMU), numpy (BSD-3-Clause and others), shapely (BSD-3-Clause),
scipy (BSD-3-Clause), python-multipart (Apache-2.0), pyyaml (MIT), comfy-sdk (MIT). The optional `paid`
extra (google-genai, anthropic) is off by default and not part of the entry.

## Code credits

- TRELLIS.2 ROCm recipe: [bioritmovideo/trellis2-rocm-gfx1201](https://github.com/bioritmovideo/trellis2-rocm-gfx1201).
- nvdiffrast ROCm port: Painter3000 ([amd-nvdiffrast-rocm72-gfx1201](https://github.com/Painter3000/amd-nvdiffrast-rocm72-gfx1201)).
- [egore/comfyui-trellis2-gguf-rocm](https://github.com/egore/comfyui-trellis2-gguf-rocm), whose notes first got
  CuMesh building on ROCm.

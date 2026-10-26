#!/usr/bin/env bash
# scripts/install_comfyui.sh [DIR]  -- a local ComfyUI for Terraformer: the setup wizard's "Install ComfyUI
# (local)" component, when no ComfyUI was found. Clones [installer] comfyui_repo at comfyui_ref (the tested
# release) into DIR (default [comfyui.service] cwd), with its own venv (python3.12, the hardware profile's
# PyTorch build: scripts/torch_index.sh) and its requirements. Re-running it resumes. A ComfyUI this script
# didn't make (no .terraformer-installed in DIR) is never touched. `ap comfy start` starts it afterwards.
set -euo pipefail
here="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
source "$here/ap_root.sh"
source "$here/torch_index.sh"
cfg() { python3 "$here/ap_config.py" "$1"; }
dir="${1:-$(cfg comfyui.service.cwd)}"
dir="${dir/#\~/$HOME}"
repo="$(cfg installer.comfyui_repo)"
ref="$(cfg installer.comfyui_ref)"
if [ -f "$dir/main.py" ] && [ ! -f "$dir/.terraformer-installed" ]; then
  echo "install_comfyui.sh: $dir already holds a ComfyUI that Terraformer didn't install; leaving it as it is."
  exit 0
fi
echo "=== ComfyUI $ref into $dir"
mkdir -p "$dir"
cd "$dir"
touch .terraformer-installed
if [ ! -d .git ]; then
  git init -q
  git remote add origin "$repo"
fi
git fetch -q --depth 1 origin "$ref"
git checkout -q -f FETCH_HEAD          # models/ may already hold downloads: untracked files stay
echo "=== venv ($TORCH_KIND, $TORCH_INDEX)"
[ -x venv/bin/python ] || python3.12 -m venv venv
venv/bin/python -m pip install -q -U pip
venv/bin/python -m pip install -q torch torchvision torchaudio --index-url "$TORCH_INDEX"
venv/bin/python -m pip install -q -r requirements.txt
echo "=== ComfyUI installed: $dir (start it with: ap comfy start)"

#!/usr/bin/env bash
# scripts/install.sh [--profile ID] [--yes] [--no-setup]
# The README's Install step 1 and 3 in one go, from a checkout: the orchestrator venv (.venv, Python 3.12),
# `ap` on PATH (~/.local/bin/ap), the hardware profile, then `ap setup`. Models, ComfyUI and the model
# runtimes stay separate steps (README steps 2, 4-6): nothing large is downloaded here.
#   --profile   rocm-2gpu | rocm-1gpu | nvidia | wsl-nvidia (default: what `ap setup` detects)
#   --yes       `ap setup --yes`: take the detected values without asking
#   --no-setup  stop after the venv and the profile
# windows/install.ps1 runs this inside WSL with --profile wsl-nvidia (Windows (WSL2) + NVIDIA, beta).
set -euo pipefail
repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
profile="" yes="" setup=1
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile="${2:?--profile needs a value}"; shift 2 ;;
    --yes) yes=1; shift ;;
    --no-setup) setup=""; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$profile" in ""|rocm-2gpu|rocm-1gpu|nvidia|wsl-nvidia) ;;
  *) echo "install.sh: --profile must be rocm-2gpu, rocm-1gpu, nvidia or wsl-nvidia" >&2; exit 2 ;; esac

ask() {   # ask "question": yes on y/Y; no without a terminal
  [ -t 0 ] || return 1
  local a; read -r -p "$1 [y/N] " a; [ "$a" = y ] || [ "$a" = Y ]
}

# WSL: a checkout on a Windows drive (9p / drvfs) is slow and breaks hard links
if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  fs="$(findmnt -n -o FSTYPE --target "$repo" 2>/dev/null || true)"
  case "$fs" in 9p|v9fs|drvfs)
    echo "install.sh: $repo is on a Windows drive ($fs). Clone it inside WSL (e.g. ~/terraformer-3d): the" >&2
    echo "  Windows drives are slow from WSL and break the hard links the file store uses." >&2
    exit 1 ;;
  esac
fi

if ! command -v python3.12 >/dev/null 2>&1 || ! python3.12 -c "import venv, ensurepip" >/dev/null 2>&1; then
  echo "Python 3.12 with venv is needed (Ubuntu: sudo apt-get install -y python3.12 python3.12-venv)."
  if command -v apt-get >/dev/null 2>&1 && ask "Install it now with sudo apt-get?"; then
    sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv
  else
    exit 1
  fi
fi
for tool in git curl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "install.sh: $tool is needed (sudo apt-get install -y $tool)" >&2; exit 1; }
done

cd "$repo"
[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e '.[dev]'
mkdir -p "$HOME/.local/bin"
ln -sf "$repo/scripts/ap" "$HOME/.local/bin/ap"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: add ~/.local/bin to PATH (Ubuntu's ~/.profile does on the next login)." ;; esac
ap="$repo/scripts/ap"
"$ap" --help >/dev/null && echo "Installed: ap ($repo/.venv), linked as ~/.local/bin/ap"

if [ -n "$profile" ]; then
  "$ap" config set hardware.profile "$profile"
  echo "Hardware profile: $profile"
fi
if [ "$profile" = wsl-nvidia ]; then
  echo "Windows (WSL2) + NVIDIA is beta: the CUDA paths and the Windows Blender/Unreal calls are untested."
fi
[ -n "$setup" ] || exit 0
args=()
[ -n "$profile" ] && args+=(--set "hardware_profile=$profile")
[ -n "$yes" ] && args+=(--yes)
"$ap" setup "${args[@]}" || echo "Setup found something to fix (above); run ap setup again after fixing it."
echo
echo "Next: ComfyUI (README step 2), ap models download --required, the model runtimes, then ap doctor."

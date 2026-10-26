#!/usr/bin/env bash
# scripts/install.sh [--profile ID] [--port N] [--no-open]
# Bootstrap for the setup wizard: checks only what the wizard's own server needs (Python 3.12 with
# venv, git, curl), then starts it (python3.12 -m asset_pipeline.installer, standard library only)
# and opens it in the default browser (inside WSL: the Windows one). Everything else happens in the
# wizard: settings, the app's venv, the `ap` command, models, model runtimes. Keep this terminal
# open while it installs; running this again (or `ap setup`) resumes.
#   --profile   preselect rocm-2gpu | rocm-1gpu | nvidia | wsl-nvidia (default: what the wizard detects)
#   --port      the wizard's port (default [installer] port in the config, 8705)
#   --no-open   don't open the browser; open the printed address yourself
# windows/install.ps1 runs this inside WSL with --profile wsl-nvidia (Windows (WSL2) + NVIDIA, beta).
set -euo pipefail
repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) args+=(--profile "${2:?--profile needs a value}"); shift 2 ;;
    --port) args+=(--port "${2:?--port needs a value}"); shift 2 ;;
    --no-open) args+=(--no-open); shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
done

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

# the wizard creates the app's venv with this Python, so venv/ensurepip are checked now (they need sudo)
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
exec python3.12 -m asset_pipeline.installer "${args[@]}"

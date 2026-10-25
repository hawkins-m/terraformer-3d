#!/usr/bin/env bash
# scripts/wsl_ui.sh [PORT]  -- the WSL side of windows/Terraformer.bat (Windows (WSL2) + NVIDIA, beta).
# Starts `ap ui` on PORT (default 8700), opens the Windows default browser at http://localhost:PORT/ once
# it answers, and stops it (SIGTERM: uvicorn shuts down cleanly) when the launcher window closes (stdin
# ends, or SIGHUP) or on Ctrl+C. An `ap ui` already answering on PORT is left alone: only the browser opens.
set -uo pipefail
here="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
source "$here/ap_root.sh"
port="${1:-8700}"
url="http://localhost:$port/"
up() { curl -fsS -m 2 "http://127.0.0.1:$port/api/version" >/dev/null 2>&1; }
open_browser() {   # WSL interop: the Windows default browser
  powershell.exe -NoProfile -NonInteractive -Command "Start-Process '$url'" >/dev/null 2>&1 ||
    cmd.exe /c start "" "$url" >/dev/null 2>&1 ||
    echo "Open $url in your browser (WSL interop couldn't start it)."
}

if up; then
  echo "Terraformer is already running at $url (not started by this window; it keeps running)."
  open_browser
  exit 0
fi
mkdir -p "$AP_ROOT/logs"
echo "Starting Terraformer (ap ui, port $port); log: $AP_ROOT/logs/ui.log"
"$here/ap" ui --port "$port" >>"$AP_ROOT/logs/ui.log" 2>&1 &
pid=$!

stopping=""
stop() {
  [ -n "$stopping" ] && return
  stopping=1
  trap '' INT TERM HUP
  echo "Stopping Terraformer..."
  kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  exit 0
}
trap stop INT TERM HUP

for _ in $(seq 1 60); do
  up && break
  kill -0 "$pid" 2>/dev/null || { echo "ap ui exited; see $AP_ROOT/logs/ui.log" >&2; tail -n 15 "$AP_ROOT/logs/ui.log" >&2; exit 1; }
  sleep 1
done
up || { echo "ap ui did not answer within 60 s; see $AP_ROOT/logs/ui.log" >&2; stop; }
echo "Terraformer is running at $url"
echo "Close this window (or press Ctrl+C) to stop it."
open_browser

# Wait for ap ui to exit or the window to close: read times out (status > 128) every 2 s while the
# console is open; any other failure is the end of stdin.
while kill -0 "$pid" 2>/dev/null; do
  read -r -t 2 _
  rc=$?
  [ "$rc" -ne 0 ] && [ "$rc" -le 128 ] && stop
done
wait "$pid" 2>/dev/null
echo "ap ui stopped."

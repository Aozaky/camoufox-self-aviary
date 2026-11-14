#!/bin/bash
set -euo pipefail
/usr/bin/desktop_ready
# First boot on a fresh volume: the main profile must exist before the gateway starts.
STATE_ROOT=/home/kasm-user/camoufox-profile/service
if [ ! -d "$STATE_ROOT/profiles/legacy-main/native" ]; then
  mkdir -p "$STATE_ROOT/profiles/legacy-main/native"
  chmod 700 "$STATE_ROOT" "$STATE_ROOT/profiles" "$STATE_ROOT/profiles/legacy-main" "$STATE_ROOT/profiles/legacy-main/native"
fi
cd /opt
python3 -m http.server 8888 --bind 127.0.0.1 >/dev/null 2>&1 &
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 0' TERM INT
/opt/camoufox-venv/bin/python /home/kasm-user/scripts/gateway.py &
worker_pid=$!
wait "$worker_pid" || true
# Restart the whole container on owner exit so no orphan browser can survive.
kill -TERM 1

#!/bin/bash
set -euo pipefail
/usr/bin/desktop_ready
cd /opt
python3 -m http.server 8888 --bind 127.0.0.1 >/dev/null 2>&1 &
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" || true; exit 0' TERM INT
/opt/camoufox-venv/bin/python /home/kasm-user/scripts/gateway.py &
worker_pid=$!
wait "$worker_pid" || true
# Restart the whole container on owner exit so no orphan browser can survive.
kill -TERM 1

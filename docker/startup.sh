#!/usr/bin/env bash
# Startup script for the Camoufox + KasmVNC container
# Runs inside the container as the kasm user
set -euo pipefail

PROFILE_DIR="/home/kasm-user/camoufox-profile"
TEST_PAGE="/home/kasm-user/test_page.html"
DISPLAY_NUM="${DISPLAY_NUM:-0}"
export DISPLAY=":${DISPLAY_NUM}"

# Ensure profile directory exists
mkdir -p "$PROFILE_DIR"

echo "[startup] Starting Camoufox in headful mode with persistent profile..."
echo "[startup] Profile: $PROFILE_DIR"
echo "[startup] Test page: $TEST_PAGE"

# Launch camoufox via the python wrapper in headful mode
# Using --persist flag to use persistent profile directory
cd /home/kasm-user

# The camoufox browser will be launched by the automation script via playwright
# This script just ensures the environment is ready and KasmVNC can see it

echo "[startup] Environment ready. Browser will be launched by automation."
echo "[startup] Keeping container alive..."

# Keep container running - KasmVNC's own processes handle the desktop
exec sleep infinity

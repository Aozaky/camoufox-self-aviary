#!/usr/bin/env bash
# One-command install: builds the image, generates credentials, starts the service.
set -euo pipefail
cd "$(dirname "$0")"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 1; }; }
need docker
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required" >&2; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  pw="$(openssl rand -hex 16 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -d '=+/')"
  sed -i "s|^CAMOUFOX_VIEWER_PASSWORD=.*|CAMOUFOX_VIEWER_PASSWORD=${pw}|" .env
  chmod 600 .env
  echo "generated .env with a random viewer password"
fi

docker compose up -d --build

echo -n "waiting for health"
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${GATEWAY_PORT:-19280}/health" >/dev/null 2>&1; then
    echo " ok"; break
  fi
  echo -n "."; sleep 2
done
curl -fsS "http://127.0.0.1:${GATEWAY_PORT:-19280}/health" >/dev/null || {
  echo; echo "service did not become healthy; check: docker compose logs -f" >&2; exit 1; }

cat <<EOF

Ready.
  viewer : http://127.0.0.1:${VIEWER_PORT:-6901}  (KasmVNC desktop, login = password in .env)
  api    : http://127.0.0.1:${GATEWAY_PORT:-19280}
  token  : docker compose exec camoufox cat /home/kasm-user/camoufox-profile/service/api.token

Register with an MCP client (Hermes example):

  mcp_servers:
    camoufox:
      command: sudo
      args: [-n, docker, exec, -i, ${CONTAINER_NAME:-aviary}, /opt/camoufox-venv/bin/python, /home/kasm-user/scripts/mcp_stdio.py]
      connect_timeout: 60.0
      enabled: true

EOF

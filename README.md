# camoufox-self-aviary

A self-hosted aviary for agent browsers: one persistent, stealth Camoufox where an
agent works and a human can take the same page over at any time. One command to
bring up, one path to migrate.

## What you get

- Camoufox (Firefox-based, anti-fingerprinting) in a KasmVNC desktop, so a human can
  watch and take over the same page the agent is using.
- A gateway exposing MCP over stdio plus HTTP endpoints for sessions, tabs, snapshots,
  clicks, scrolling, uploads/downloads and login-state snapshots.
- Persistent profile volume: logins survive restarts and image rebuilds.
- Encrypted backup/restore of the browser profile.

## Quick start

```bash
git clone <this-repo> camoufox-self-aviary && cd camoufox-self-aviary
./install.sh
```

`install.sh` copies `.env.example` to `.env`, generates a random viewer password,
builds the image, starts the container and waits for `/health`. First build pulls the
Kasm base image and the pinned Camoufox browser, so allow a few minutes.

Then register the MCP server in your agent's config:

```yaml
mcp_servers:
  camoufox:
    command: sudo
    args: [-n, docker, exec, -i, aviary, /opt/camoufox-venv/bin/python, /home/kasm-user/scripts/mcp_stdio.py]
    connect_timeout: 60.0
    enabled: true
```

## Layout

```
docker/         image definition, pinned requirements, container startup
scripts/        gateway (sessions/tabs/tools), service layer, MCP stdio bridge
tests/          integration and contract tests
install.sh      build + start + health check
```

## Security notes

- Ports bind to `127.0.0.1` on purpose. Publish through your own TLS proxy and keep
  authentication in front of it; the API token is a bearer credential.
- Never commit `.env`, `*.token`, profile exports or backups. `.gitignore` covers them;
  the install script sets `.env` to `600`.
- Browser profiles contain cookies and session tokens. Treat the volume as a credential.

## Migrating to another host

1. Clone the repo on the new machine and run `./install.sh`.
2. Copy the profile volume over if you want to keep logins:
   `docker run --rm -v <old-volume>:/from -v <new-volume>:/to alpine sh -c 'cd /from && tar cf - .' | docker run -i --rm -v <new-volume>:/to alpine tar xf - -C /to`
3. Point your MCP client at the new container name.

## Requirements

Docker with Compose v2, ~4 GB RAM headroom, x86_64 or arm64.

# Docker

Docker is an opted-in runtime target. It keeps Codex home, gateway
configuration, gateway state, workspace data, uv cache, and the project
environment in Docker volumes.

Stop the Windows `CodexGateway` service and any WSL gateway before starting the
Docker gateway with the same Telegram bot token.

## Get The Repository

Clone the project and enter the repo root before running Docker commands. The
examples below use PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
Set-Location "$HOME\src"
git clone <repo-url> codex-gateway
Set-Location codex-gateway
```

From Bash, use the same checkout path with `mkdir -p`, `cd`, `git clone`, and
`cd codex-gateway`. If you already have a checkout, just run the commands below
from that repo root.

## First Run

Run these steps in order.

1. Build the image:

   ```powershell
   docker compose -f testing\docker\compose.linux.yaml build
   ```

2. Configure the gateway:

   ```powershell
   docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-setup
   ```

3. Confirm the Docker-local status:

   ```powershell
   docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli status
   ```

4. Start the gateway:

   ```powershell
   docker compose -f testing\docker\compose.linux.yaml up -d codex-gateway
   ```

5. Watch logs:

   ```powershell
   docker compose -f testing\docker\compose.linux.yaml logs -f codex-gateway
   ```

The setup service runs `telegram setup` in `/work`, writes the Docker-local
`.env` into the `gateway-config` volume, prompts for Codex login reuse or
login, prompts for workspace roots, writes the default initial model
preference, and prompts for the default permission profile. Use Telegram
`/model` after pairing to switch models from the live app-server list.
If the `codex-gateway` container is already running, restart it after rerunning
setup so the updated Docker-local `.env` is applied:

```powershell
docker compose -f testing\docker\compose.linux.yaml restart codex-gateway
```

## Pairing

In Telegram, send `/start` to the bot from the configured user account. The
Docker runtime sets `CODEX_GATEWAY_TELEGRAM_PAIR_COMMAND`, so the bot should
print a host command like this:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli pair CODE-HERE
```

Run the command with the generated code.

## Access Token Auth

Docker setup uses the same interactive Codex login check as local setup. If the
persistent `codex-home` volume is already signed in, the default choice is to
reuse it. If it is not signed in, the default choice is ChatGPT device auth,
and access-token auth is the secondary choice.

To use an access token from `https://developers.openai.com/codex/auth`, set it
before setup and select the access-token option when prompted:

```powershell
$env:CODEX_ACCESS_TOKEN = "<codex-access-token>"
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-setup
Remove-Item Env:CODEX_ACCESS_TOKEN
```

Refresh Codex auth later with:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-login
```

## Status And Validation

Run status checks as needed:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli status
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli access status
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli workspace list
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli login-status
```

Run Linux compatibility checks when validating the Docker target:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli test
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli smoke
```

## Troubleshooting

### Missing Configuration Or Bot Token

If the gateway container reports that it is not configured, rerun Docker setup
and then check Docker-local status:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-setup
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli status
```

### Pairing Or Unauthorized User

Only the configured Telegram user can request a pairing code. Send `/start` to
the bot from that account, then run the Docker-local pair command:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli access status
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli pair CODE-HERE
```

### Codex Authentication

Codex auth is stored in the persistent `codex-home` volume. Check or refresh it
inside the Docker runtime:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli login-status
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-login
```

### Workspace Rejected

Docker runs with workspace roots inside the `gateway-workspace` volume. Inspect
the Docker-local workspace state, then rerun setup if the configured roots need
to change:

```powershell
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli workspace list
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-setup
```

### App-Server Readiness Or Gateway Exit

Default startup uses a loopback WebSocket app-server inside the gateway
container. Check gateway logs, then restart the container:

```powershell
docker compose -f testing\docker\compose.linux.yaml logs --tail 120 codex-gateway
docker compose -f testing\docker\compose.linux.yaml restart codex-gateway
```

### Telegram API Connectivity

If command-menu sync or polling reports connection failures, verify host
network access to Telegram and then restart the gateway container:

```powershell
Invoke-WebRequest https://api.telegram.org -UseBasicParsing |
  Select-Object StatusCode
docker compose -f testing\docker\compose.linux.yaml restart codex-gateway
```

## Cleanup And Uninstall

Stop the Docker gateway:

```powershell
docker compose -f testing\docker\compose.linux.yaml down
```

For a gateway-only Docker reset, use one of the full uninstall scripts and opt
in to Docker gateway volume cleanup. This runs Compose `down` and removes only
the Docker `gateway-config` and `gateway-state` volumes. It preserves
`codex-home`, `gateway-workspace`, `linux-venv`, and `uv-cache`:

```powershell
.\scripts\uninstall-gateway.ps1 -DockerGatewayVolumes
```

```bash
bash scripts/uninstall-gateway.sh --docker-gateway-volumes
```

Run the corresponding dry-run first when checking local paths:

```powershell
.\scripts\uninstall-gateway.ps1 -WhatIf -DockerGatewayVolumes
```

```bash
bash scripts/uninstall-gateway.sh --dry-run --docker-gateway-volumes
```

Remove all Docker volumes only when you intentionally want to discard Codex
auth, gateway config, Telegram state, workspace data, uv cache, and the Linux
project environment:

```powershell
docker compose -f testing\docker\compose.linux.yaml down -v
```

Image removal is optional:

```powershell
docker rmi codex-gateway-linux:0.133.0
```

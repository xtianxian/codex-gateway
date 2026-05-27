# Windows

Windows is the primary supported local setup path for Codex Gateway.

## Requirements

- Python 3.11 or newer
- `uv`
- Git for fresh checkouts
- Codex CLI installed and authenticated with a ChatGPT/Codex account
- Telegram bot token from `@BotFather`
- Numeric Telegram user ID, for example from `@userinfobot`

## Get The Repository

Clone the project and enter the repo root before running setup commands:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
Set-Location "$HOME\src"
git clone <repo-url> codex-gateway
Set-Location codex-gateway
```

If you already have a checkout, just run the commands below from that repo
root.

Check Codex before setup:

```powershell
codex --version
codex login status
```

Codex authentication comes from the local `codex` CLI/app-server session. Setup
can reuse an existing login, start ChatGPT device auth, or use a Codex access
token through `codex login --with-access-token`; it does not use
`OPENAI_API_KEY` directly.

If you want to sign in before setup prompts, use:

```powershell
codex login --device-auth
```

## Setup

Run these commands from the repo root.

Choose one setup command.

For the normal Windows setup, run:

```powershell
.\scripts\setup.ps1
```

That command syncs dependencies, runs tests, and offers to configure Telegram.
Run it from an elevated PowerShell only if you want setup to install or start
the optional Windows Service.

If you want foreground-only testing without installing the service, run this
instead:

```powershell
.\scripts\setup.ps1 -SkipStartup
```

If you only want dependency and test verification, run this instead:

```powershell
.\scripts\setup.ps1 -SkipTelegramSetup -SkipStartup
```

If `uv` is not installed and you want the setup script to install it first, run
this instead:

```powershell
.\scripts\setup.ps1 -InstallUv
```

## Manual Setup

Use this sequence only if you do not want to use `scripts\setup.ps1`:

```powershell
uv sync --extra dev
uv run pytest
uv run codex-gateway telegram setup
uv run codex-gateway telegram status
uv run codex-gateway telegram run
```

`telegram setup` writes a local `.env` file, creates the default workspace, and
sets the one Telegram user ID allowed to request pairing. It checks that the
configured Codex CLI is installed and reports whether `codex login status` is
already signed in. You can rerun setup later; pressing Enter keeps existing
token, user ID, workspace roots, default workspace, and default permission
profile values. Setup silently keeps or writes the initial default model; use
Telegram `/model` after pairing to switch models. If the `CodexGateway`
Windows Service already exists, setup asks whether to update and restart it so
the current `.env` is applied.

## Service Install, Start, Restart

Use this section only if you want the gateway to run as the optional
`CodexGateway` Windows Service. These commands require an elevated PowerShell.

Install or update the service:

```powershell
.\scripts\install-gateway-service.ps1 -Start
```

Restart the service after rerunning `telegram setup` or changing `.env`:

```powershell
Restart-Service CodexGateway
```

Stop and start the service:

```powershell
sc.exe stop CodexGateway
sc.exe start CodexGateway
```

The service starts the Telegram gateway without a foreground console window and
writes logs under `.codex-gateway\logs\service`.

## Pairing And Basic Usage

Start the gateway if it is not already running as a service:

```powershell
uv run codex-gateway telegram run
```

In Telegram, send `/start` to the bot from the configured user account, then
run the local pairing command the bot replies with:

```powershell
uv run codex-gateway telegram access pair <code>
```

Useful status commands:

```powershell
uv run codex-gateway telegram status
uv run codex-gateway telegram access status
uv run codex-gateway telegram workspace list
```

Other Telegram users are rejected and are not given pairing codes.

## Troubleshooting

### Missing Bot Token

If `telegram run` reports that `CODEX_GATEWAY_TELEGRAM_BOT_TOKEN` is required,
rerun setup or check the sanitized status output:

```powershell
uv run codex-gateway telegram setup
uv run codex-gateway telegram status
```

### Pairing Or Unauthorized User

Only the configured Telegram user can request a pairing code. Send `/start` to
the bot from that account, then run the local command the bot replies with:

```powershell
uv run codex-gateway telegram access status
uv run codex-gateway telegram access pair <code>
```

### Missing Codex CLI

If PowerShell reports that `codex` is not recognized, install or update Codex
CLI, then open a new PowerShell and confirm the command is on `Path`:

```powershell
Get-Command codex
codex --version
```

### Codex Authentication

The gateway uses your local Codex CLI/app-server session, not
`OPENAI_API_KEY`. Confirm Codex CLI is installed and authenticated before
starting the gateway:

```powershell
codex --version
codex login status
codex login --device-auth
uv run codex-gateway telegram status
```

### Workspace Rejected

The active workspace must be inside `CODEX_GATEWAY_ALLOWED_ROOTS`. Inspect the
current workspace state, then rerun setup if the configured roots need to
change:

```powershell
uv run codex-gateway telegram workspace list
uv run codex-gateway telegram setup
```

### App-Server Readiness Timeout

Default startup uses a loopback WebSocket app-server. If readiness times out,
check whether another process owns the configured port and then restart the
gateway or service:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess
Restart-Service CodexGateway
```

### Logs

Foreground runs write logs to the current terminal. The Windows Service writes
logs under `.codex-gateway\logs\service`:

```powershell
Get-ChildItem .codex-gateway\logs\service |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5
Get-Content .codex-gateway\logs\service\*.log -Tail 80
```

### Telegram API Connectivity

If command-menu sync or polling reports connection failures, verify basic
network access to Telegram and then restart the gateway:

```powershell
Invoke-WebRequest https://api.telegram.org -UseBasicParsing |
  Select-Object StatusCode
Restart-Service CodexGateway
```

## Cleanup And Uninstall

Remove startup integration only:

```powershell
.\scripts\setup.ps1 -RemoveStartup
```

Remove the service directly:

```powershell
.\scripts\uninstall-gateway-service.ps1
```

Run a full gateway-only uninstall/reset when you want to remove local gateway
configuration, state, logs, startup integration, and matching gateway processes
while preserving workspaces and Codex CLI login/auth:

```powershell
.\scripts\uninstall-gateway.ps1 -WhatIf
.\scripts\uninstall-gateway.ps1
```

If the `CodexGateway` service exists, run the full uninstall from an elevated
PowerShell. The script removes only gateway-owned paths, refuses dangerous
targets such as workspace roots or profile directories, does not kill an
arbitrary process just because it owns port `8765`, and does not revoke the
Telegram bot token at BotFather. Windows setup and uninstall manage only the
supported `CodexGateway` Windows Service; they do not remove startup artifacts
they did not create.

To also stop the Docker Compose gateway and remove only the Docker
`gateway-config` and `gateway-state` volumes, opt in explicitly:

```powershell
.\scripts\uninstall-gateway.ps1 -DockerGatewayVolumes
```

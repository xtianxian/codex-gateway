# Linux

This page covers Linux setup, foreground runtime, and systemd-style operation.
The validated Ubuntu 24.04 WSL2 flow is included because it uses the same Linux
CLI and service surface.

## Get The Repository

On a normal Linux host, clone the project and enter the repo root before
running setup commands:

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"
git clone <repo-url> codex-gateway
cd codex-gateway
```

If you already have a checkout, just run the commands below from that repo
root. The WSL2 setup below copies an existing Windows checkout into WSL instead
of cloning because that was the validated Windows-to-WSL flow.

## WSL2 Ubuntu Setup

Run these steps in order when setting up WSL2 Ubuntu from a Windows host.

1. Install or open the WSL distro:

   ```powershell
   New-Item -ItemType Directory -Force E:\WSL\ubuntu
   wsl --install Ubuntu-24.04 --name Ubuntu --location E:\WSL\ubuntu --version 2
   wsl -d Ubuntu
   ```

2. Install the Linux toolchain inside Ubuntu:

   ```bash
   sudo apt-get update
   sudo apt-get install -y ca-certificates curl git nodejs npm rsync
   curl -LsSf https://astral.sh/uv/install.sh | sh
   sudo npm install -g @openai/codex@0.133.0
   export PATH="$HOME/.local/bin:$PATH"
   codex --version
   uv --version
   ```

3. Copy the Windows working tree into WSL ext4 without local secrets or runtime
   state:

   ```bash
   mkdir -p ~/src ~/codex-gateway-workspace ~/.local/state/codex-gateway/telegram
   rsync -a --delete --include='.env.example' --exclude='.env' --exclude='.env.*' \
     --exclude='.*venv/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
     --exclude='.codex-gateway/' --exclude='workspace/' --exclude='testing/artifacts/' \
     /mnt/e/Projects/codex-gateway/ ~/src/codex-gateway/
   ```

4. Sync dependencies:

   ```bash
   cd ~/src/codex-gateway
   uv sync --extra dev
   ```

5. Check or refresh Codex auth:

   ```bash
   codex login status
   codex login --device-auth
   ```

6. Configure the gateway with WSL-local workspace and state paths:

   ```bash
   uv run codex-gateway telegram setup \
     --allowed-root ~/codex-gateway-workspace \
     --default-cwd ~/codex-gateway-workspace \
     --state-dir ~/.local/state/codex-gateway/telegram
   uv run codex-gateway telegram status
   ```

If an existing Linux or WSL `codex-gateway.service` is already running, restart
it after rerunning setup so the updated `.env` is applied:

```bash
sudo systemctl restart codex-gateway.service
```

Stop the Windows `CodexGateway` service before running a WSL foreground or
system-service gateway with the same Telegram bot token, then restart the
Windows service when the WSL run is stopped.

## Foreground Run

Run this after setup when you want the gateway in the current terminal:

```bash
uv run codex-gateway telegram run
```

## Persistent Run

For background WSL operation, use a system service with
`User=<your-linux-user>` rather than a user service. An enabled Linux service
starts when the WSL distro starts, but WSL may stop the whole distro when there
are no Windows-side WSL clients.

For continuous WSL operation from a Windows host, start a host-side WSL
keepalive after enabling the system service:

```powershell
wsl.exe -d Ubuntu -u root --exec bash -lc "systemctl start codex-gateway.service; exec sleep infinity"
```

Run that from a persistent terminal or wrap it in a hidden `Start-Process`
startup entry. This keepalive is WSL-specific and is not needed on normal
Linux, where the system service keeps running while the OS is running.

## Validation

Run validation commands as needed:

```bash
uv run pytest -p no:cacheprovider --basetemp ~/.local/state/codex-gateway/pytest-wsl-full
uv run --script testing/probes/mock_bot_real_app_server_smoke.py
uv run codex-gateway telegram status
```

## Troubleshooting

### Missing Bot Token

If `telegram run` reports that `CODEX_GATEWAY_TELEGRAM_BOT_TOKEN` is required,
rerun setup or check the sanitized status output:

```bash
uv run codex-gateway telegram setup
uv run codex-gateway telegram status
```

### Pairing Or Unauthorized User

Only the configured Telegram user can request a pairing code. Send `/start` to
the bot from that account, then run the local command the bot replies with:

```bash
uv run codex-gateway telegram access status
uv run codex-gateway telegram access pair <code>
```

### Missing Codex CLI

If the shell cannot find `codex`, install or update Codex CLI, then confirm the
command is on `PATH`:

```bash
command -v codex
codex --version
```

### Codex Authentication

The gateway uses your local Codex CLI/app-server session, not
`OPENAI_API_KEY`. Confirm Codex CLI is installed and authenticated before
starting the gateway:

```bash
codex --version
codex login status
codex login --device-auth
uv run codex-gateway telegram status
```

### Workspace Rejected

The active workspace must be inside `CODEX_GATEWAY_ALLOWED_ROOTS`. Inspect the
current workspace state, then rerun setup if the configured roots need to
change:

```bash
uv run codex-gateway telegram workspace list
uv run codex-gateway telegram setup
```

### App-Server Readiness Timeout

Default startup uses a loopback WebSocket app-server. If readiness times out,
check whether another process owns the configured port and then restart the
gateway or service:

```bash
ss -ltnp 'sport = :8765'
sudo systemctl restart codex-gateway.service
```

### Logs

Foreground runs write logs to the current terminal. A Linux system service
writes to the system journal:

```bash
sudo journalctl -u codex-gateway.service -n 100 --no-pager
```

### Telegram API Connectivity

If command-menu sync or polling reports connection failures, verify basic
network access to Telegram and then restart the gateway service:

```bash
curl -I https://api.telegram.org
sudo systemctl restart codex-gateway.service
```

## Cleanup And Uninstall

Stop the service only:

```bash
sudo systemctl stop codex-gateway.service
```

Disable the service before returning to the Windows service for the same bot
token:

```bash
sudo systemctl disable --now codex-gateway.service
```

Run a full gateway-only uninstall/reset when you want to remove local gateway
configuration, state, logs, startup integration, and matching gateway processes
while preserving workspaces and Codex CLI login/auth:

```bash
bash scripts/uninstall-gateway.sh --dry-run
bash scripts/uninstall-gateway.sh
```

On Linux and WSL, the script disables/removes `codex-gateway.service` when it
is present. It removes only gateway-owned paths, refuses dangerous targets such
as workspace roots or profile directories, does not kill an arbitrary process
just because it owns port `8765`, and does not revoke the Telegram bot token at
BotFather.

To also stop the Docker Compose gateway and remove only the Docker
`gateway-config` and `gateway-state` volumes, opt in explicitly:

```bash
bash scripts/uninstall-gateway.sh --docker-gateway-volumes
```

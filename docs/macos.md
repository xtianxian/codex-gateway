# macOS

macOS uses the same gateway CLI flow as Linux. The repo includes helper
scripts for dependency/test setup, foreground runs, and optional background
startup through a launchd user service.

## Requirements

- Python 3.11 or newer
- `uv`
- Node.js and npm for Codex CLI installation
- Codex CLI installed and authenticated with a ChatGPT/Codex account
- Telegram bot token from `@BotFather`
- Numeric Telegram user ID, for example from `@userinfobot`

Install common prerequisites with Homebrew when needed:

```bash
brew install python node git
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g @openai/codex@0.133.0
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
```

## Get The Repository

Clone the project and enter the repo root before running setup commands:

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"
git clone <repo-url> codex-gateway
cd codex-gateway
```

If you already have a checkout, just run the commands below from that repo
root.

Check Codex before setup:

```bash
codex --version
codex login status
codex login --device-auth
```

Codex authentication comes from the local `codex` CLI/app-server session. Setup
can reuse an existing login, start ChatGPT device auth, or use a Codex access
token through `codex login --with-access-token`; it does not use
`OPENAI_API_KEY` directly.

## Setup

Run these commands from the repo root.

Choose one setup command.

For the normal macOS setup, run:

```bash
bash scripts/setup-macos.sh
```

That command syncs dependencies, runs tests, and offers to configure Telegram.
It also offers to install and start the optional launchd user service. If the
launchd service already exists, setup asks whether to update and restart it so
the current `.env` is applied.

If you want foreground-only testing without installing launchd, run this
instead:

```bash
bash scripts/setup-macos.sh --skip-startup
```

If you only want dependency and test verification, run this instead:

```bash
bash scripts/setup-macos.sh --skip-telegram-setup --skip-startup
```

If `uv` is not installed and you want the setup script to install it first, run
this instead:

```bash
bash scripts/setup-macos.sh --install-uv
```

## Manual Setup

Use this sequence only if you do not want to use `scripts/setup-macos.sh`:

```bash
uv sync --extra dev
uv run pytest
uv run codex-gateway telegram setup
uv run codex-gateway telegram status
uv run codex-gateway telegram run
```

For home-directory workspace and state paths, pass them explicitly during
setup:

```bash
mkdir -p "$HOME/codex-gateway-workspace" "$HOME/.local/state/codex-gateway/telegram"
uv run codex-gateway telegram setup \
  --allowed-root "$HOME/codex-gateway-workspace" \
  --default-cwd "$HOME/codex-gateway-workspace" \
  --state-dir "$HOME/.local/state/codex-gateway/telegram"
```

## Launchd Install, Start, Restart

Use this section only if you want the gateway to run as a launchd user service.
These commands do not require `sudo`.

Install and start the service:

```bash
bash scripts/install-macos-launchd.sh --start
```

Restart the service after rerunning `telegram setup` or changing `.env`:

```bash
launchctl kickstart -k "gui/$(id -u)/com.codex.gateway.telegram"
```

Inspect the service:

```bash
launchctl print "gui/$(id -u)/com.codex.gateway.telegram"
```

The service starts the Telegram gateway without a foreground terminal and
writes logs under `.codex-gateway/logs` and `.codex-gateway/logs/launchd`.

## Pairing And Basic Usage

Start the gateway if it is not already running through launchd:

```bash
uv run codex-gateway telegram run
```

In Telegram, send `/start` to the bot from the configured user account, then
run the local pairing command the bot replies with:

```bash
uv run codex-gateway telegram access pair <code>
```

Useful status commands:

```bash
uv run codex-gateway telegram status
uv run codex-gateway telegram access status
uv run codex-gateway telegram workspace list
```

Other Telegram users are rejected and are not given pairing codes.

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
gateway or launchd service:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
launchctl kickstart -k "gui/$(id -u)/com.codex.gateway.telegram"
```

### Logs

Foreground runs write logs to the current terminal. The macOS launchd helper
writes gateway and launchd logs under `.codex-gateway/logs`:

```bash
find .codex-gateway/logs -type f -print
tail -n 80 .codex-gateway/logs/*.log
tail -n 80 .codex-gateway/logs/launchd/*.log
```

### Telegram API Connectivity

If command-menu sync or polling reports connection failures, verify basic
network access to Telegram and then restart the launchd service:

```bash
curl -I https://api.telegram.org
launchctl kickstart -k "gui/$(id -u)/com.codex.gateway.telegram"
```

## Cleanup And Uninstall

Remove startup integration only:

```bash
bash scripts/setup-macos.sh --remove-startup
```

Remove the launchd service directly:

```bash
bash scripts/uninstall-macos-launchd.sh
```

Run a full gateway-only uninstall/reset when you want to remove local gateway
configuration, state, logs, startup integration, and matching gateway processes
while preserving workspaces and Codex CLI login/auth:

```bash
bash scripts/uninstall-gateway.sh --dry-run
bash scripts/uninstall-gateway.sh
```

The script removes only gateway-owned paths, refuses dangerous targets such as
workspace roots or profile directories, does not kill an arbitrary process just
because it owns port `8765`, and does not revoke the Telegram bot token at
BotFather.

To also stop the Docker Compose gateway and remove only the Docker
`gateway-config` and `gateway-state` volumes, opt in explicitly:

```bash
bash scripts/uninstall-gateway.sh --docker-gateway-volumes
```

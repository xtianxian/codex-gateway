<p align="center">
  <img src="assets/cover.png" alt="Codex Gateway cover">
</p>

<h1 align="center">Codex Gateway</h1>

<p align="center">
  Operate a local Codex app-server from Telegram while your credentials,
  workspace files, and gateway state stay on your machine.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>
  &middot;
  <a href="#setup-guides">Setup Guides</a>
  &middot;
  <a href="#pair-telegram">Pair Telegram</a>
  &middot;
  <a href="#commands">Commands</a>
  &middot;
  <a href="#configuration">Configuration</a>
  &middot;
  <a href="#troubleshooting">Troubleshooting</a>
  &middot;
  <a href="#copy-paste-commands">Copy-Paste Commands</a>
  &middot;
  <a href="#roadmap">Roadmap</a>
  &middot;
  <a href="#development">Development</a>
</p>

## Overview

Codex Gateway is a local bridge between the Codex app-server and chat-style
channels. The current bridge is a Telegram bot for one authorized user, scoped
to configured local workspaces.

| Area | Current support |
| --- | --- |
| Gateway | Telegram |
| Backend | Local `codex app-server` |
| Transport | Loopback WebSocket by default, stdio fallback available |
| Default URL | `ws://127.0.0.1:8765` |
| Intended use | Personal local automation |
| Hosted operation | Out of scope |

## Features

- Single-user Telegram pairing with local CLI confirmation.
- Workspace allow-listing for all Telegram-started work.
- Codex thread start, resume, fork, archive, rollback, compact, review, diff,
  and status workflows.
- Inline Telegram controls for model, reasoning effort, permissions, modes,
  skills, approvals, and app-server-backed choices.
- App-server request handling for command approval, file approval, permissions,
  MCP elicitation, tool user input, and Telegram dynamic tools.
- Attachment download support with configurable size limits for native Telegram
  file payloads including documents, photos, videos, audio, voice, stickers,
  live photos, and paid media.
- Structured Telegram payload summaries for contacts, locations, venues, polls,
  dice, checklists, stories, payments, gifts, sharing, web app data, and common
  service messages.
- Generated images plus workspace files and structured responses sent back
  through native Telegram media, contact, location, venue, poll, checklist,
  dice, copy, and forward methods where Bot API allows them.
- Telegram command-menu sync based on the locally generated app-server schema.
- Optional Windows background startup through the `CodexGateway` service.

<p align="center">
  <a href="https://ko-fi.com/xtianjamoner">
    <img
      src="assets/kofi.png"
      alt="Support this project on Ko-fi"
      title="Support this project on Ko-fi"
      width="180">
  </a>
  <br>
  <sub><a href="https://ko-fi.com/xtianjamoner">Support this project on Ko-fi</a></sub>
</p>

## Roadmap

Telegram is the current supported bridge. Future bridge candidates include:

- Discord: bot-based access for personal servers and private workflows.
- Slack: workspace app support for team channels and direct messages.

All bridges should keep the same local-first model: credentials, workspace
files, and gateway state stay on the user's machine.

## Requirements

- Python 3.11 or newer
- `uv`
- Codex CLI installed and authenticated with a ChatGPT/Codex account
- Telegram bot token from `@BotFather`
- Numeric Telegram user ID, for example from `@userinfobot`

```powershell
codex --version
codex login status
```

Codex authentication comes from your local `codex` CLI/app-server session. This
gateway is for a Codex-capable ChatGPT account or subscription; it does not ask
for or use an OpenAI API key such as `OPENAI_API_KEY` to run Codex. If
`codex login status` shows you are not signed in, run
`codex login --device-auth` before starting the gateway, or let
`telegram setup` prompt for device auth or access-token auth. Docker setup can
also seed the container's Codex CLI session from `CODEX_ACCESS_TOKEN`; that
token is read only by `codex login --with-access-token`.

## Compatibility

This alpha targets the Codex app-server protocol schema generated with
`codex-cli 0.133.0`.

Validated local targets are Windows foreground/service operation, WSL2 Ubuntu
24.04 foreground/system-service operation, and macOS 15.6 arm64 foreground and
launchd user-service operation. The WSL validation used distro name `Ubuntu`,
Codex CLI `0.133.0`, uv `0.11.16`, repo path `~/src/codex-gateway`, workspace
`~/codex-gateway-workspace`, and state `~/.local/state/codex-gateway/telegram`.

The gateway talks to local Codex app-server over loopback WebSocket by default.
If Codex app-server changes its generated schema, regenerate the checked-in
protocol files and rerun the test suite before tagging a new snapshot.

Regenerate the protocol bundle after upgrading Codex CLI when app-server request
or response shapes may have changed:

```powershell
codex app-server generate-json-schema --out src\codex_gateway\backends\codex_app_server\protocol --experimental
uv run pytest -p no:cacheprovider tests\backends\codex_app_server tests\gateways\telegram\test_bridge.py tests\gateways\telegram\test_command_menu.py
uv run pytest
```

Keep this README compatibility target and any annotated tag message aligned with
the Codex CLI version used to generate the checked-in schema.

## Release And Tagging

Use ordinary commits for regular development. Use annotated Git tags only for
public snapshots that someone may want to install, compare, or return to later.

The current public snapshot is `v0.1.0-alpha.1`. Alpha tags should use the
format `v0.1.0-alpha.N` while the project is still moving toward a first
`v0.1.0` release. Compatibility notes belong in this README and in the
annotated tag message. GitHub Releases and package publishing are not required
unless the project intentionally adds a package release workflow.

## Quick Start

Start with the environment guide that matches where you want the local gateway
to run. Windows is the primary supported path. Docker, Linux, and macOS are
documented runtime targets.

## Setup Guides

| Environment | Best for | Guide |
| --- | --- | --- |
| Windows | Recommended local setup with PowerShell and optional background service. | [Open Windows guide](docs/windows.md) |
| Docker | Containerized runtime with persistent Codex, gateway, and workspace volumes. | [Open Docker guide](docs/docker.md) |
| Linux | Linux setup, foreground runtime, and systemd-style service notes. | [Open Linux guide](docs/linux.md) |
| macOS | Local setup with Bash helpers and optional launchd user service. | [Open macOS guide](docs/macos.md) |

After setup, send `/start` to the Telegram bot from the configured user and
run the local pairing command the bot returns.

## Pair Telegram

1. Start the gateway with this command.

   ```powershell
   uv run codex-gateway telegram run
   ```

2. In Telegram, send `/start` to your bot from the configured user account.

3. Run the pairing command the bot replies with. Replace `<code>` with the
   code from Telegram.

   ```powershell
   uv run codex-gateway telegram access pair <code>
   ```

4. Wait for the pairing confirmation in Telegram, then send a normal message.

Other Telegram users are rejected and are not given pairing codes.

## Copy-Paste Commands

Use these blocks when you already know which operation you need.

| Task | Command |
| --- | --- |
| Check Codex CLI | `codex --version` |
| Sign in to Codex CLI | `codex login --device-auth` |
| Install dependencies | `uv sync --extra dev` |
| Run tests | `uv run pytest` |
| Configure Telegram | `uv run codex-gateway telegram setup` |
| Show gateway status | `uv run codex-gateway telegram status` |
| Run the gateway | `uv run codex-gateway telegram run` |
| Pair a Telegram code | `uv run codex-gateway telegram access pair <code>` |
| List configured workspaces | `uv run codex-gateway telegram workspace list` |
| Show access state | `uv run codex-gateway telegram access status` |
| Update default permissions | `uv run codex-gateway telegram setup --permission-profile default` |
| Install or update Windows Service | `.\scripts\install-gateway-service.ps1 -Start` from elevated PowerShell |
| Restart Windows Service | `Restart-Service CodexGateway` from elevated PowerShell |
| Remove Windows Service | `.\scripts\setup.ps1 -RemoveStartup` from elevated PowerShell |
| Run macOS setup | `bash scripts/setup-macos.sh` |
| Install or update macOS launchd service | `bash scripts/install-macos-launchd.sh --start` |
| Remove macOS launchd service | `bash scripts/setup-macos.sh --remove-startup` |

Full first-run sequence:

```powershell
uv sync --extra dev
uv run pytest
uv run codex-gateway telegram setup
uv run codex-gateway telegram status
uv run codex-gateway telegram run
```

Pair after `/start` replies in Telegram:

```powershell
uv run codex-gateway telegram access pair <code>
```

Run without setup prompts:

```powershell
.\scripts\setup.ps1 -SkipTelegramSetup -SkipStartup
```

Run setup without elevation by skipping the Windows Service:

```powershell
.\scripts\setup.ps1 -SkipStartup
```

Update the default permission profile from PowerShell:

```powershell
uv run codex-gateway telegram setup --permission-profile read-only
uv run codex-gateway telegram setup --permission-profile default
uv run codex-gateway telegram setup --permission-profile auto-review
uv run codex-gateway telegram setup --permission-profile full-access
```

## Commands

| Command | Purpose |
| --- | --- |
| `/status` | Show workspace, thread, account, context usage, token usage, and rate limits. |
| `/projects`, `/setcwd <path>`, `/getcwd` | Inspect or change the active workspace. |
| `/new`, `/resume`, `/threads` | Manage Codex threads. |
| `/reset`, `/clear` | Reset chat state or clear local thread mappings. `/clear` preserves the active workspace and preferences. |
| `/model`, `/permissions`, `/mode`, `/personality` | Change persisted thread settings with inline selectors where available; model and effort are mode-scoped. |
| `/plan [text]` | Switch to Plan mode, show plan updates in Telegram, then offer CLI-style implement, fresh-thread implement, or stay-in-plan choices. |
| `/diff`, `/review`, `/compact`, `/mention <path>` | Run common Codex workflows. |
| `/steer <text>`, `/cancel` | Control an active turn. |
| `/plugins`, `/skills`, `/mcp`, `/features`, `/config`, `/debug-config` | Inspect local Codex app-server capabilities. |
| `/commands` | Sync Telegram's slash-command menu. |

Some commands depend on app-server features exposed by the current Codex account
or build. For example, `/apps` is hidden unless the app catalog is visible.
Commands unsupported by the generated local schema are hidden or reported as
unavailable.

## Configuration

The interactive setup writes these values to `.env`. Real environment variables
override `.env` values.

| Variable | Purpose |
| --- | --- |
| `CODEX_GATEWAY_TELEGRAM_BOT_TOKEN` | Telegram bot token used by `telegram run`. |
| `CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID` | Numeric Telegram user allowed to pair. |
| `CODEX_GATEWAY_TELEGRAM_STATE_DIR` | Local pairing, chat, thread, approval, and download state. |
| `CODEX_GATEWAY_ALLOWED_ROOTS` | Workspace roots the gateway may use; separate multiple roots with semicolon or comma. |
| `CODEX_GATEWAY_DEFAULT_CWD` | Default workspace, required to be inside an allowed root. |
| `CODEX_GATEWAY_CODEX_BIN` | Codex executable, default `codex`. |
| `CODEX_GATEWAY_APP_SERVER_URL` | Loopback WebSocket URL, default `ws://127.0.0.1:8765`. |
| `CODEX_GATEWAY_APP_SERVER_TRANSPORT` | `websocket` or `stdio`, default `websocket`. |
| `CODEX_GATEWAY_TELEGRAM_MODEL` | Initial model for new threads before a chat chooses one with `/model`; setup defaults to `gpt-5.4-mini`. |
| `CODEX_GATEWAY_TELEGRAM_MODEL_REASONING_EFFORT` | Initial reasoning effort for that model; setup defaults to `medium`. |
| `CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE` | Default permissions for new Telegram threads: `:read-only`, `:workspace`, `:auto-review`, or `:danger-full-access`. |
| `CODEX_GATEWAY_TELEGRAM_SANDBOX` | Sandbox setting for app-server threads. |
| `CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY` | Approval policy for app-server requests. |
| `CODEX_GATEWAY_TELEGRAM_PAIR_COMMAND` | Pairing command template shown by `/start`; use `{code}` where the generated code belongs. |
| `CODEX_GATEWAY_ENABLE_EXEC` | Enables `/exec` when set to `1`. |
| `CODEX_GATEWAY_ADVERTISE_EXEC` | Shows `/exec` in Telegram when set to `1`. |

See `.env.example` for the full set of supported environment variables and
defaults.

Legacy `CODEX_TELEGRAM_*` variables are accepted as migration fallbacks, but
`CODEX_GATEWAY_*` names are canonical.

## Security Notes

- Keep `.env`, Telegram bot tokens, and `.codex-gateway/` state local.
- Telegram bots are reachable by anyone who knows the bot username or adds the
  bot to a chat. Treat Telegram delivery as untrusted input.
- Keep `CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID` set to your own numeric
  Telegram user ID; users are denied by default, and only that configured user
  can pair and use the gateway.
- Avoid adding the bot to groups unless you explicitly want group-chat access
  control behavior.
- Use narrow workspace roots for `CODEX_GATEWAY_ALLOWED_ROOTS`.
- Leave `/exec` disabled unless you explicitly want Telegram messages to start
  local command-running Codex turns.
- Do not expose this gateway as a public or multi-user hosted service.

## Windows Service

The optional `CodexGateway` Windows Service starts the Telegram gateway without
a foreground console window and writes logs under `.codex-gateway\logs\service`.
Install, restart, and removal commands live in the
[Windows setup guide](docs/windows.md#service-install-start-restart).

## Troubleshooting

Environment-specific troubleshooting lives with each setup guide:

| Environment | Troubleshooting |
| --- | --- |
| Windows | [Windows troubleshooting](docs/windows.md#troubleshooting) |
| Docker | [Docker troubleshooting](docs/docker.md#troubleshooting) |
| Linux | [Linux troubleshooting](docs/linux.md#troubleshooting) |
| macOS | [macOS troubleshooting](docs/macos.md#troubleshooting) |

## Development

Install dependencies and run tests:

```powershell
uv sync --extra dev
uv run pytest
```

Optional smoke probe against a real local app-server:

```powershell
uv run --script testing\probes\mock_bot_real_app_server_smoke.py --include-turns --exhaustive
```

The smoke probe requires an authenticated Codex CLI and may start a real local
app-server process.

Linux container verification:

```powershell
docker compose -f testing\docker\compose.linux.yaml build
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli test
```

WSL2 Ubuntu verification:

```powershell
wsl -d Ubuntu -e bash -lc 'cd ~/src/codex-gateway && uv run pytest -p no:cacheprovider --basetemp ~/.local/state/codex-gateway/pytest-wsl-full'
```

## Repository Layout

| Path | Purpose |
| --- | --- |
| `src/codex_gateway` | Package source. |
| `src/codex_gateway/backends/codex_app_server` | App-server client, transport, lifecycle, and generated protocol metadata. |
| `src/codex_gateway/gateways/telegram` | Telegram bridge, Bot API client, commands, setup, access control, and local state. |
| `tests` | Unit and async behavior tests. |
| `testing/probes` | Optional smoke probes. |
| `testing/docker` | Docker runtime and Linux test harness with Codex CLI installed. |
| `scripts` | Windows and macOS setup and service helpers. |

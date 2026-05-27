# Testing

This page collects local test and smoke-probe commands for development and
release validation.

## Unit And Integration Tests

Run the default test suite from the repo root:

```powershell
uv run pytest
```

On Windows, stale elevated temp ACLs can block pytest's default temp root. Use
a repo-local or profile-local base temp when that happens:

```powershell
uv run pytest -p no:cacheprovider --basetemp .pytest-tmp
```

## Native Telegram Payload Smoke

Run deterministic native Telegram payload and dynamic-tool coverage without a
live Telegram bot, Docker, or a real model turn:

```powershell
uv run --script testing\probes\mock_bot_real_app_server_smoke.py --native-payloads
```

This probe uses `TelegramBridge` with a mock Telegram bot and
response-recording app-server. It verifies representative inbound media and
structured payload summaries, native Telegram dynamic send tools, compact
sent-message results, and current-message reuse.

Poll validation is snapshot-only; it does not track ongoing `poll_answer`
updates. Telegram paid media, checklist, and business-account-only methods can
return Bot API tool-result errors when the bot account lacks those
capabilities. Those are expected smoke outcomes when they stay inside the tool
result instead of stopping the bridge.

## Hybrid App-Server Smoke

Run the hybrid mock-bot/real-app-server probe when command parity or
app-server response shapes change:

```powershell
uv run --script testing\probes\mock_bot_real_app_server_smoke.py --include-turns --exhaustive
```

This probe requires an authenticated Codex CLI and may start a real local
app-server process.

## Docker And Linux Targets

Validate the Docker target from Windows:

```powershell
docker compose -f testing\docker\compose.linux.yaml build
docker compose -f testing\docker\compose.linux.yaml run --rm codex-gateway-cli test
```

Validate a WSL2 Ubuntu checkout:

```powershell
wsl -d Ubuntu -e bash -lc 'cd ~/src/codex-gateway && uv run pytest -p no:cacheprovider --basetemp ~/.local/state/codex-gateway/pytest-wsl-full'
```

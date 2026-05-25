from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from codex_gateway.gateways.telegram.access import AccessManager
from codex_gateway.gateways.telegram.bot_api import TelegramAPIError, TelegramBotAPI
from codex_gateway.gateways.telegram.bridge import TelegramBridge, get_updates_with_retry
from codex_gateway.gateways.telegram.config import TelegramSettings
from codex_gateway.gateways.telegram.state import TelegramStateStore


@pytest.mark.asyncio
async def test_set_my_commands_sends_telegram_command_menu_payload() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.url.path == "/bottest-token/setMyCommands"
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert await api.set_my_commands([{"command": "start", "description": "Start"}]) is True
    assert requests == [{"commands": [{"command": "start", "description": "Start"}]}]

    await client.aclose()


class MenuFakeBot:
    def __init__(self) -> None:
        self.commands: list[dict[str, str]] = []
        self.messages: list[dict[str, Any]] = []

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        return True

    async def send_message(self, chat_id: str | int, text: str, **kwargs: Any) -> list[dict[str, Any]]:
        message = {"message_id": len(self.messages) + 1, "chat": {"id": chat_id}, "text": text, **kwargs}
        self.messages.append(message)
        return [message]


class FailingMenuFakeBot(MenuFakeBot):
    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        raise TelegramAPIError("Telegram API setMyCommands failed: All connection attempts failed")


class RetryUpdatesBot:
    def __init__(self) -> None:
        self.calls = 0

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            raise TelegramAPIError("Telegram API getUpdates failed: All connection attempts failed")
        return [{"update_id": offset or 1, "timeout": timeout}]


class MenuFakeAppServer:
    pass


def menu_settings(tmp_path: Path) -> TelegramSettings:
    root = tmp_path / "projects"
    cwd = root / "codex-gateway"
    cwd.mkdir(parents=True)
    return TelegramSettings(
        bot_token="token",
        state_dir=tmp_path / "state",
        allowed_roots=(root.resolve(strict=False),),
        default_cwd=cwd.resolve(strict=False),
        app_server_command=("codex", "app-server", "--listen", "stdio://"),
        app_server_transport="websocket",
        app_server_url="ws://127.0.0.1:8765",
        codex_bin="codex",
        model=None,
        sandbox="workspace-write",
        approval_policy="unlessTrusted",
        approval_timeout_seconds=900,
        max_attachment_bytes=25_000_000,
        poll_timeout_seconds=30,
        enable_exec=False,
        advertise_exec=False,
    )


@pytest.mark.asyncio
async def test_commands_command_syncs_menu_and_returns_short_confirmation(tmp_path: Path) -> None:
    settings = menu_settings(tmp_path)
    store = TelegramStateStore(settings.state_dir)
    access = AccessManager(store)
    access.allow_user("123", username="xtian", source="cli")
    bot = MenuFakeBot()
    bridge = TelegramBridge(settings, store, access, bot, MenuFakeAppServer())

    await bridge.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 123, "username": "xtian"},
                "text": "/commands",
            },
        }
    )

    assert bot.commands
    assert bot.commands[0]["command"] == "start"
    assert all(not command["command"].startswith("/") for command in bot.commands)
    names = {command["command"] for command in bot.commands}
    assert {"models", "modes"}.isdisjoint(names)
    assert "permissions" in names
    assert "effort" not in names
    assert bot.messages[-1]["text"] == "Telegram command menu synced."


@pytest.mark.asyncio
async def test_command_menu_sync_failure_is_reported_without_crashing(tmp_path: Path) -> None:
    settings = menu_settings(tmp_path)
    store = TelegramStateStore(settings.state_dir)
    access = AccessManager(store)
    access.allow_user("123", username="xtian", source="cli")
    bot = FailingMenuFakeBot()
    bridge = TelegramBridge(settings, store, access, bot, MenuFakeAppServer())

    error = await bridge.sync_telegram_command_menu()

    assert "setMyCommands failed" in str(error)

    await bridge.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 123, "username": "xtian"},
                "text": "/commands",
            },
        }
    )

    assert bot.messages[-1]["text"].startswith("Telegram command menu sync failed:")


@pytest.mark.asyncio
async def test_get_updates_retries_transient_telegram_api_errors() -> None:
    bot = RetryUpdatesBot()
    warnings: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    updates = await get_updates_with_retry(
        bot,
        offset=42,
        timeout=30,
        retry_delay_seconds=0.25,
        sleep=fake_sleep,
        warn=warnings.append,
    )

    assert updates == [{"update_id": 42, "timeout": 30}]
    assert bot.calls == 2
    assert sleeps == [0.25]
    assert "getUpdates failed" in warnings[0]


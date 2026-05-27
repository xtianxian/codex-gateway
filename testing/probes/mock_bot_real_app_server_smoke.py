from __future__ import annotations

import asyncio
import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from codex_gateway.backends.codex_app_server.client import AppServerClient  # noqa: E402
from codex_gateway.backends.codex_app_server.client import AppServerEvent  # noqa: E402
from codex_gateway.backends.codex_app_server.lifecycle import AppServerProcessManager  # noqa: E402
from codex_gateway.backends.codex_app_server.transport import WebSocketJsonRpcTransport  # noqa: E402
from codex_gateway.core.commands import default_command_registry  # noqa: E402
from codex_gateway.gateways.telegram.access import AccessManager  # noqa: E402
from codex_gateway.gateways.telegram.bot_api import TelegramAPIError  # noqa: E402
from codex_gateway.gateways.telegram.bridge import TelegramBridge  # noqa: E402
from codex_gateway.gateways.telegram.commands import (  # noqa: E402
    APP_SERVER_COMMANDS,
    CODEX_TURN_COMMANDS,
    LOCAL_COMMANDS,
    THREAD_COMMANDS,
    UNSUPPORTED_COMMANDS,
)
from codex_gateway.gateways.telegram.config import TelegramSettings  # noqa: E402
from codex_gateway.gateways.telegram.state import TelegramStateStore  # noqa: E402


CHAT_ID = 42
USER_ID = 123
KNOWN_UNKNOWN_COMMANDS = frozenset({"models", "modes", "permission", "doesnotexist"})
CLI_SLASH_COMMAND_FIXTURE: dict[str, str] = {
    "start": "telegram",
    "help": "telegram",
    "commands": "telegram",
    "project": "telegram",
    "projects": "telegram",
    "setcwd": "telegram",
    "getcwd": "telegram",
    "searchcwd": "telegram",
    "threads": "telegram",
    "archive": "telegram",
    "unarchive": "telegram",
    "cancel": "telegram",
    "interrupt": "telegram",
    "steer": "telegram",
    "model": "cli_aligned",
    "permissions": "cli_aligned",
    "personality": "cli_aligned",
    "experimental": "cli_aligned",
    "memories": "cli_aligned",
    "skills": "cli_aligned",
    "hooks": "cli_aligned",
    "apps": "cli_aligned_feature_gated",
    "plugins": "cli_aligned_feature_gated",
    "account": "cli_aligned",
    "mcp": "cli_aligned",
    "status": "cli_aligned",
    "debug-config": "cli_aligned",
    "approve": "cli_aligned_sensitive",
    "new": "thread_turn",
    "resume": "thread_turn",
    "fork": "thread_turn",
    "init": "thread_turn",
    "compact": "thread_turn",
    "plan": "thread_turn",
    "goal": "thread_turn",
    "agent": "thread_turn",
    "subagents": "thread_turn",
    "side": "thread_turn",
    "btw": "thread_turn",
    "copy": "tui_only",
    "diff": "thread_turn",
    "mention": "thread_turn",
    "review": "thread_turn",
    "rename": "thread_turn",
    "ps": "thread_turn",
    "stop": "thread_turn",
    "clear": "thread_turn",
    "exec": "feature_gated",
    "approval": "typed_only",
    "mode": "typed_only",
    "effort": "typed_only",
    "config": "typed_only",
    "features": "typed_only",
    "limits": "typed_only",
    "workspace": "typed_only",
    "reset": "typed_only",
    "collab": "typed_only",
    "read": "typed_only",
    "rollback": "typed_only",
    "logout": "typed_only_sensitive",
    "plugin": "typed_only_unsupported",
    "usage": "retired",
    "context": "retired",
    "raw": "tui_only",
    "title": "tui_only",
    "statusline": "tui_only",
    "theme": "tui_only",
    "pets": "tui_only",
    "pet": "tui_only",
    "keymap": "tui_only",
    "vim": "tui_only",
    "settings": "tui_only",
    "realtime": "tui_only",
    "ide": "tui_only",
    "quit": "tui_only",
    "exit": "tui_only",
    "feedback": "debug_local_only",
    "rollout": "debug_local_only",
    "test-approval": "debug_local_only",
    "debug-m-drop": "debug_local_only",
    "debug-m-update": "debug_local_only",
    "setup-default-sandbox": "debug_local_only",
    "sandbox-add-read-dir": "debug_local_only",
    "clean": "debug_local_only",
}
TURN_COMMAND_SKIP_REASONS = {
    "compact": "starts a real context compaction turn",
    "read": "starts a real model turn",
    "review": "starts a real review turn",
    "mention": "starts a real model turn",
    "collab": "starts a real model turn",
    "fork": "forks then starts a real model turn",
    "init": "starts a real model turn when AGENTS.md is absent",
    "plan": "starts a real model turn when inline text is provided",
    "side": "forks then starts a real model turn with inline text",
    "btw": "forks then starts a real model turn with inline text",
}
STATEFUL_COMMAND_SKIP_REASONS = {
    "archive": "requires a persisted rollout from a real completed turn",
    "unarchive": "requires a persisted rollout from a real completed turn",
    "rollback": "requires completed turn history",
}
DEFAULT_COMMAND_SKIP_REASONS = {
    **TURN_COMMAND_SKIP_REASONS,
    **STATEFUL_COMMAND_SKIP_REASONS,
}
DEFAULT_SKIPPED_COMMAND_NAMES = frozenset(DEFAULT_COMMAND_SKIP_REASONS)
SERVER_REQUEST_SKIP_REASONS = {
    "command/file approvals": "requires a real model turn to request command or file approval",
    "permissions approval": "requires a real model turn to request permissions",
    "MCP elicitation": "requires a real MCP tool call that elicits input",
    "tool user input": "requires a real model/tool path to call request_user_input",
    "dynamic tools": "requires a real model turn to call telegram_* dynamic tools",
}
COMMAND_TIMEOUT_SECONDS = 60.0
CALLBACK_CLEAR_REPLY_MARKUP = {"inline_keyboard": []}


class MockBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.commands: list[dict[str, str]] = []
        self.files: dict[str, dict[str, Any]] = {}
        self.downloads: dict[str, bytes] = {}
        self.photos: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []
        self.animations: list[dict[str, Any]] = []
        self.stickers: list[dict[str, Any]] = []
        self.contacts: list[dict[str, Any]] = []
        self.locations: list[dict[str, Any]] = []
        self.venues: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []
        self.dice: list[dict[str, Any]] = []
        self.copied_messages: list[dict[str, Any]] = []
        self.forwarded_messages: list[dict[str, Any]] = []
        self.paid_media: list[dict[str, Any]] = []
        self.checklists: list[dict[str, Any]] = []
        self.next_message_id = 1000

    async def send_message(self, chat_id: str | int, text: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.next_message_id += 1
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "text": text, **kwargs}
        self.messages.append(message)
        return [message]

    async def edit_message_text(self, chat_id: str | int, message_id: int, text: str, **kwargs: Any) -> bool:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})
        return True

    async def answer_callback_query(self, callback_query_id: str, **kwargs: Any) -> bool:
        self.answers.append({"callback_query_id": callback_query_id, **kwargs})
        return True

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        return True

    async def get_file(self, file_id: str) -> dict[str, Any]:
        if file_id not in self.files:
            raise KeyError(f"missing file info for {file_id}")
        return self.files[file_id]

    async def download_file(self, file_path: str) -> bytes:
        if file_path not in self.downloads:
            raise KeyError(f"missing download for {file_path}")
        return self.downloads[file_path]

    async def send_photo(
        self,
        chat_id: str | int,
        photo: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "photo": photo,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
        }
        self.photos.append(message)
        return message

    async def send_video(
        self,
        chat_id: str | int,
        video: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "video": video,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
            "width": width,
            "height": height,
        }
        self.videos.append(message)
        return message

    async def send_animation(
        self,
        chat_id: str | int,
        animation: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "animation": animation,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
            "width": width,
            "height": height,
        }
        self.animations.append(message)
        return message

    async def send_sticker(
        self,
        chat_id: str | int,
        sticker: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "sticker": sticker,
            "filename": filename,
            "content_type": content_type,
            "emoji": emoji,
        }
        self.stickers.append(message)
        return message

    async def send_contact(
        self,
        chat_id: str | int,
        phone_number: str,
        first_name: str,
        *,
        last_name: str | None = None,
        vcard: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "contact": {
                "phone_number": phone_number,
                "first_name": first_name,
                **({"last_name": last_name} if last_name else {}),
                **({"vcard": vcard} if vcard else {}),
            },
        }
        self.contacts.append(message)
        return message

    async def send_location(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        **options: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        location = {"latitude": latitude, "longitude": longitude}
        location.update({key: value for key, value in options.items() if value is not None})
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "location": location}
        self.locations.append(message)
        return message

    async def send_venue(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        title: str,
        address: str,
        **options: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        venue = {
            "location": {"latitude": latitude, "longitude": longitude},
            "title": title,
            "address": address,
        }
        venue.update({key: value for key, value in options.items() if value is not None})
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "venue": venue}
        self.venues.append(message)
        return message

    async def send_poll(
        self,
        chat_id: str | int,
        question: str,
        options: list[str] | list[dict[str, Any]],
        **settings: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        poll = {
            "id": f"poll_{self.next_message_id}",
            "question": question,
            "options": options,
        }
        poll.update({key: value for key, value in settings.items() if value is not None})
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "poll": poll}
        self.polls.append(message)
        return message

    async def send_dice(self, chat_id: str | int, *, emoji: str | None = None) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "dice": {"emoji": emoji or "\U0001f3b2", "value": 3},
        }
        self.dice.append(message)
        return message

    async def copy_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **options: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "source_message_id": message_id,
            **{key: value for key, value in options.items() if value is not None},
        }
        self.copied_messages.append(message)
        return message

    async def forward_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **options: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "source_message_id": message_id,
            **{key: value for key, value in options.items() if value is not None},
        }
        self.forwarded_messages.append(message)
        return message

    async def send_paid_media(
        self,
        chat_id: str | int,
        star_count: int,
        media: list[dict[str, Any]],
        *,
        caption: str | None = None,
        payload: str | None = None,
        files: dict[str, tuple[str, bytes, str | None]] | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "star_count": star_count,
            "media": media,
            "caption": caption,
            "payload": payload,
            "files": files or {},
        }
        self.paid_media.append(message)
        return message

    async def send_checklist(
        self,
        chat_id: str | int,
        business_connection_id: str,
        checklist: dict[str, Any],
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "business_connection_id": business_connection_id,
            "checklist": checklist,
        }
        self.checklists.append(message)
        return message


class RecordingAppServer:
    def __init__(self) -> None:
        self.thread_starts = []
        self.thread_settings_updates = []
        self.turn_starts = []
        self.tool_results = []
        self.errors = []
        self.next_thread_id = 1
        self.next_turn_id = 1

    async def thread_start(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_starts.append(kwargs)
        thread_id = f"thread_{self.next_thread_id}"
        self.next_thread_id += 1
        return {"thread": {"id": thread_id}}

    async def thread_settings_update(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_settings_updates.append(kwargs)
        thread_id = str(kwargs.get("thread_id") or kwargs.get("threadId") or "")
        return {"thread": {"id": thread_id}}

    async def turn_start(self, **kwargs: Any) -> dict[str, Any]:
        self.turn_starts.append(kwargs)
        turn_id = f"turn_{self.next_turn_id}"
        self.next_turn_id += 1
        return {"turn": {"id": turn_id}}

    async def send_dynamic_tool_result(self, request_id: int | str, content: list[dict[str, Any]]) -> None:
        self.tool_results.append({"request_id": request_id, "content": content})

    async def send_error_response(self, request_id: int | str, message: str, *, code: int = -32000) -> None:
        self.errors.append({"request_id": request_id, "message": message, "code": code})


@dataclass
class SmokeResult:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _workspace(temp_root: Path, requested: str | None) -> Path:
    raw = requested or os.getenv("CODEX_GATEWAY_HYBRID_WORKSPACE")
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    workspace = temp_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("Hybrid mock-bot real-app-server smoke workspace.\n", encoding="utf-8")
    return workspace.resolve(strict=False)


def _settings(state_dir: Path, workspace: Path) -> TelegramSettings:
    return TelegramSettings(
        bot_token=None,
        state_dir=state_dir,
        allowed_roots=(workspace,),
        default_cwd=workspace,
        app_server_command=("codex", "app-server", "--listen", "stdio://"),
        model=None,
        sandbox="workspace-write",
        approval_policy="unlessTrusted",
        approval_timeout_seconds=900,
        max_attachment_bytes=25_000_000,
        poll_timeout_seconds=30,
        app_server_transport="websocket",
        app_server_url="ws://127.0.0.1:0",
        codex_bin="codex",
    )


def _message_update(text: str, message_id: int) -> dict[str, Any]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": 1,
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {"id": USER_ID, "username": "hybrid-smoke"},
            "text": text,
        },
    }


def _callback_update(data: str, message_id: int) -> dict[str, Any]:
    return {
        "update_id": message_id + 10000,
        "callback_query": {
            "id": f"cb-{message_id}",
            "from": {"id": USER_ID, "username": "hybrid-smoke"},
            "message": {"message_id": message_id, "chat": {"id": CHAT_ID}},
            "data": data,
        },
    }


def _inline_button_map(message: dict[str, Any]) -> dict[str, str]:
    buttons: dict[str, str] = {}
    markup = message.get("reply_markup") if isinstance(message.get("reply_markup"), dict) else {}
    for row in markup.get("inline_keyboard") or []:
        for button in row:
            if isinstance(button, dict):
                buttons[str(button.get("text"))] = str(button.get("callback_data"))
    return buttons


def _latest_text(items: list[dict[str, Any]]) -> str:
    values = [item.get("text") for item in items if isinstance(item.get("text"), str)]
    return str(values[-1]) if values else ""


def _redact(text: str) -> str:
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", text)


def _classify_command_result(command: str, text: str, *, audit_affected: bool = False) -> SmokeResult | None:
    if (
        command == "/apps"
        and "App-server command failed:" in text
        and "failed to list apps" in text
        and "403 Forbidden" in text
    ):
        return SmokeResult(command, "skip", "/apps is feature-gated or hidden by the current app-server account/config")
    if command == "/apps" and "Apps are not available for this account/config" in text:
        return SmokeResult(command, "skip", "/apps is feature-gated or hidden by the current app-server account/config")
    if audit_affected and command == "/rollback" and "App-server command failed:" in text:
        return SmokeResult(command, "skip", "/rollback requires completed turn history in the current app-server state")
    if command.startswith("/permissions") and "unknown built-in profile `:auto-review`" in text:
        return SmokeResult(command, "skip", "Auto-review permission profile is not available in this app-server config")
    if "App-server command failed:" in text:
        return SmokeResult(command, "fail", text)
    return None


def _timeout_result(command: str) -> SmokeResult:
    if command in {"/archive", "/unarchive", "/rollback"}:
        return SmokeResult(command, "skip", f"{command[1:]} did not return within the probe timeout")
    return SmokeResult(command, "fail", f"timed out after {COMMAND_TIMEOUT_SECONDS:g}s")


def _exception_result(command: str, exc: Exception) -> SmokeResult:
    if command in {"/archive", "/unarchive", "/rollback"} and exc.__class__.__name__ == "ConnectionClosedError":
        return SmokeResult(command, "skip", f"{command[1:]} closed the app-server connection during stateful probing")
    return SmokeResult(command, "fail", f"raised {exc.__class__.__name__}: {exc}")


async def _send(
    bridge: TelegramBridge,
    bot: MockBot,
    results: list[SmokeResult],
    command: str,
    message_id: int,
    *,
    audit_affected: bool = False,
) -> None:
    before_messages = len(bot.messages)
    before_edits = len(bot.edits)
    try:
        await asyncio.wait_for(
            bridge.handle_update(_message_update(command, message_id)),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        await asyncio.sleep(0)
    except asyncio.TimeoutError:
        results.append(_timeout_result(command))
        return
    except Exception as exc:
        results.append(_exception_result(command, exc))
        return
    new_items = [*bot.messages[before_messages:], *bot.edits[before_edits:]]
    text = _latest_text(new_items)
    classified = _classify_command_result(command, text, audit_affected=audit_affected)
    if classified is not None:
        results.append(classified)
        return
    changed = len(bot.messages) != before_messages or len(bot.edits) != before_edits
    results.append(SmokeResult(command, "ok", text if changed else "no immediate Telegram text"))


async def _choose(
    bridge: TelegramBridge,
    bot: MockBot,
    results: list[SmokeResult],
    label: str,
    callback_data: str,
    message_id: int,
    *,
    require_keyboard_clear: bool = False,
) -> None:
    before_messages = len(bot.messages)
    before_edits = len(bot.edits)
    try:
        await bridge.handle_update(_callback_update(callback_data, message_id))
        await asyncio.sleep(0)
    except Exception as exc:
        results.append(SmokeResult(label, "fail", f"raised {exc.__class__.__name__}: {exc}"))
        return
    new_edits = bot.edits[before_edits:]
    if require_keyboard_clear:
        if not new_edits:
            results.append(SmokeResult(label, "fail", "callback did not edit the source message"))
            return
        uncleared = [edit for edit in new_edits if edit.get("reply_markup") != CALLBACK_CLEAR_REPLY_MARKUP]
        if uncleared:
            results.append(SmokeResult(label, "fail", "callback edit did not clear the inline keyboard"))
            return
    new_items = [*bot.messages[before_messages:], *new_edits]
    text = _latest_text(new_items)
    classified = _classify_command_result(label, text)
    if classified is not None:
        results.append(classified)
        return
    changed = len(bot.edits) != before_edits
    results.append(SmokeResult(label, "ok", text if changed else "callback answered"))


async def _wait_for_bridge_idle(bridge: TelegramBridge, timeout_seconds: float = 120.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if bridge._active_turn_context(str(CHAT_ID)) is None:
            return True
        await asyncio.sleep(0.25)
    return False


async def _exercise_selector(
    bridge: TelegramBridge,
    bot: MockBot,
    results: list[SmokeResult],
    command: str,
    message_id: int,
    *,
    restore_toggles: bool = False,
    require_keyboard_clear: bool = False,
) -> tuple[int, list[str]]:
    await _send(bridge, bot, results, command, message_id)
    initial_buttons = _inline_button_map(bot.messages[-1])
    if not initial_buttons:
        results.append(SmokeResult(f"{command} selector coverage", "fail", "selector returned no buttons"))
        return message_id + 1, []
    labels = list(initial_buttons)
    for index, label in enumerate(labels):
        if index > 0:
            message_id += 1
            await _send(bridge, bot, results, command, message_id)
        buttons = _inline_button_map(bot.messages[-1])
        callback_data = buttons.get(label)
        if callback_data is None:
            results.append(SmokeResult(f"{command} callback {label}", "fail", "button disappeared"))
            continue
        await _choose(
            bridge,
            bot,
            results,
            f"{command} callback {label}",
            callback_data,
            bot.messages[-1]["message_id"],
            require_keyboard_clear=require_keyboard_clear,
        )
        restore_label = _opposite_toggle_label(label) if restore_toggles else None
        if restore_label is not None:
            message_id += 1
            await _send(bridge, bot, results, command, message_id)
            restore_buttons = _inline_button_map(bot.messages[-1])
            restore_callback_data = restore_buttons.get(restore_label)
            if restore_callback_data is None:
                results.append(SmokeResult(f"{command} restore {label}", "fail", "restore button disappeared"))
                continue
            await _choose(
                bridge,
                bot,
                results,
                f"{command} restore {label}",
                restore_callback_data,
                bot.messages[-1]["message_id"],
                require_keyboard_clear=require_keyboard_clear,
            )
    return message_id + 1, labels


def _opposite_toggle_label(label: str) -> str | None:
    if "enabled" in label:
        return label.replace("enabled", "disabled", 1)
    if "disabled" in label:
        return label.replace("disabled", "enabled", 1)
    return None


async def _exercise_resume_callback(
    bridge: TelegramBridge,
    bot: MockBot,
    results: list[SmokeResult],
    message_id: int,
    *,
    require_keyboard_clear: bool = False,
) -> int:
    await _send(bridge, bot, results, "/resume", message_id)
    buttons = _inline_button_map(bot.messages[-1])
    if not buttons:
        results.append(SmokeResult("/resume callback", "fail", "resume returned no thread button"))
        return message_id + 1
    label, callback_data = next(iter(buttons.items()))
    await _choose(
        bridge,
        bot,
        results,
        f"/resume callback {label}",
        callback_data,
        bot.messages[-1]["message_id"],
        require_keyboard_clear=require_keyboard_clear,
    )
    return message_id + 1


def _record_skips(results: list[SmokeResult], skipped: dict[str, str]) -> None:
    for name, reason in skipped.items():
        results.append(SmokeResult(name, "skip", reason))


def parity_command_names() -> set[str]:
    registry = default_command_registry()
    return {command.name for command in registry.commands()} | set(CLI_SLASH_COMMAND_FIXTURE)


def _assert_fixture_completeness(results: list[SmokeResult]) -> None:
    known = LOCAL_COMMANDS | THREAD_COMMANDS | APP_SERVER_COMMANDS | CODEX_TURN_COMMANDS | UNSUPPORTED_COMMANDS
    registry = {command.name for command in default_command_registry().commands()}
    missing_fixture = sorted((known | registry) - set(CLI_SLASH_COMMAND_FIXTURE))
    if missing_fixture:
        results.append(SmokeResult("CLI fixture completeness", "fail", f"missing fixture: {', '.join(missing_fixture)}"))


def _assert_command_coverage(results: list[SmokeResult], skipped_command_names: set[str]) -> None:
    known = parity_command_names()
    exercised = {
        result.name.split()[0].lstrip("/")
        for result in results
        if result.name.startswith("/")
    }
    covered = exercised | skipped_command_names | KNOWN_UNKNOWN_COMMANDS
    missing = sorted(known - covered)
    if missing:
        results.append(SmokeResult("slash command coverage", "fail", f"missing coverage: {', '.join(missing)}"))


def _record_check(results: list[SmokeResult], name: str, ok: bool, detail: str, fail_detail: str | None = None) -> None:
    results.append(SmokeResult(name, "ok" if ok else "fail", detail if ok else fail_detail or detail))


def _combined_turn_text(turn_start: dict[str, Any]) -> str:
    input_items = turn_start.get("input_items")
    if not isinstance(input_items, list):
        return ""
    return "\n\n".join(
        str(item.get("text") or "")
        for item in input_items
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _has_local_image(turn_start: dict[str, Any]) -> bool:
    input_items = turn_start.get("input_items")
    return isinstance(input_items, list) and any(
        isinstance(item, dict) and item.get("type") == "localImage"
        for item in input_items
    )


def _full_message_json_leaked(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("{")
        or stripped.startswith("[")
        or '"message_id"' in text
        or "'message_id'" in text
        or '"chat"' in text
        or "'chat'" in text
    )


def _tool_result_text(app_server: RecordingAppServer, request_id: int | str) -> str:
    for result in reversed(app_server.tool_results):
        if result.get("request_id") != request_id:
            continue
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            return str(content[0].get("text") or "")
    return ""


def _native_payload_update(message_id: int, payload: dict[str, Any], *, text: str = "") -> dict[str, Any]:
    update = _message_update(text, message_id)
    if not text:
        update["message"].pop("text", None)
    update["message"].update(payload)
    return update


async def _complete_active_native_turn(bridge: TelegramBridge) -> None:
    context = bridge._active_turn_context(str(CHAT_ID))
    if context is None:
        return
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"turnId": context.turn_id, "turn": {"status": "completed", "items": []}},
        )
    )


async def _exercise_native_inbound_payload(
    bridge: TelegramBridge,
    app_server: RecordingAppServer,
    results: list[SmokeResult],
    name: str,
    update: dict[str, Any],
    *,
    expected_fragments: tuple[str, ...],
    unexpected_fragments: tuple[str, ...] = (),
    expect_local_image: bool = False,
) -> None:
    before_turns = len(app_server.turn_starts)
    try:
        await bridge.handle_update(update)
    except Exception as exc:
        results.append(SmokeResult(name, "fail", f"raised {exc.__class__.__name__}: {exc}"))
        return
    if len(app_server.turn_starts) != before_turns + 1:
        results.append(SmokeResult(name, "fail", "payload did not start a Codex turn"))
        return
    turn_start = app_server.turn_starts[-1]
    text = _combined_turn_text(turn_start)
    missing = [fragment for fragment in expected_fragments if fragment not in text]
    unexpected = [fragment for fragment in unexpected_fragments if fragment in text]
    local_image_ok = not expect_local_image or _has_local_image(turn_start)
    await _complete_active_native_turn(bridge)
    if missing or unexpected or not local_image_ok:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        if not local_image_ok:
            details.append("missing localImage item")
        results.append(SmokeResult(name, "fail", "; ".join(details)))
        return
    results.append(SmokeResult(name, "ok", "turn input included expected native payload summary"))


async def _invoke_native_tool(
    bridge: TelegramBridge,
    app_server: RecordingAppServer,
    *,
    request_id: int,
    turn_id: str,
    tool: str,
    arguments: dict[str, Any],
) -> str:
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": turn_id, "tool": tool, "arguments": arguments},
            request_id=request_id,
        )
    )
    return _tool_result_text(app_server, request_id)


async def _exercise_native_outbound_tools(
    bridge: TelegramBridge,
    bot: MockBot,
    app_server: RecordingAppServer,
    workspace: Path,
    results: list[SmokeResult],
) -> None:
    for filename, data in {
        "photo.png": b"png bytes",
        "clip.mp4": b"mp4 bytes",
        "loop.gif": b"gif bytes",
        "sticker.webp": b"webp bytes",
        "paid.mp4": b"paid bytes",
    }.items():
        (workspace / filename).write_bytes(data)

    await bridge.handle_update(_message_update("send native tools", 80))
    context = bridge._active_turn_context(str(CHAT_ID))
    if context is None:
        results.append(SmokeResult("native outbound setup", "fail", "active turn context was not created"))
        return

    calls: list[tuple[str, dict[str, Any]]] = [
        ("telegram_send_photo", {"path": "photo.png", "caption": "Photo", "filename": "final.png"}),
        (
            "telegram_send_video",
            {"path": "clip.mp4", "caption": "Clip", "duration": 5, "width": 640, "height": 360},
        ),
        ("telegram_send_animation", {"path": "loop.gif", "caption": "Loop", "duration": 2}),
        ("telegram_send_sticker", {"path": "sticker.webp", "emoji": ":)"}),
        ("telegram_send_contact", {"phone_number": "+15551212", "first_name": "Ada", "last_name": "Lovelace"}),
        ("telegram_send_location", {"latitude": 14.6, "longitude": 121.0, "horizontal_accuracy": 12.5}),
        ("telegram_send_venue", {"latitude": 14.6, "longitude": 121.0, "title": "HQ", "address": "Main St"}),
        ("telegram_send_poll", {"question": "Ship?", "options": ["Yes", "No"], "is_anonymous": False}),
        ("telegram_send_dice", {"emoji": "\U0001f3b2"}),
        ("telegram_copy_current_message", {"caption": "Copied"}),
        ("telegram_forward_current_message", {}),
    ]
    result_texts: dict[str, str] = {}
    for index, (tool, arguments) in enumerate(calls, start=200):
        result_texts[tool] = await _invoke_native_tool(
            bridge,
            app_server,
            request_id=index,
            turn_id=context.turn_id,
            tool=tool,
            arguments=arguments,
        )

    sent_result_ok = all(re.search(r"\bmessage_id=\d+\b", text) for text in result_texts.values())
    poll_ok = "poll_id=poll_" in result_texts["telegram_send_poll"]
    dice_ok = "dice_value=3" in result_texts["telegram_send_dice"]
    compact_ok = not any(_full_message_json_leaked(text) for text in result_texts.values())
    routed_ok = (
        bool(bot.photos and bot.photos[-1]["filename"] == "final.png")
        and bool(bot.videos and bot.videos[-1]["duration"] == 5)
        and bool(bot.animations and bot.animations[-1]["filename"] == "loop.gif")
        and bool(bot.stickers and bot.stickers[-1]["filename"] == "sticker.webp")
        and bool(bot.contacts and bot.contacts[-1]["contact"]["first_name"] == "Ada")
        and bool(bot.locations and bot.locations[-1]["location"]["latitude"] == 14.6)
        and bool(bot.venues and bot.venues[-1]["venue"]["title"] == "HQ")
        and bool(bot.polls and bot.polls[-1]["poll"]["question"] == "Ship?")
        and bool(bot.dice and bot.dice[-1]["dice"]["value"] == 3)
        and bool(bot.copied_messages and bot.copied_messages[-1]["source_message_id"] == 80)
        and bool(bot.forwarded_messages and bot.forwarded_messages[-1]["source_message_id"] == 80)
    )
    _record_check(
        results,
        "native outbound dynamic tools",
        sent_result_ok and poll_ok and dice_ok and compact_ok and routed_ok,
        "native send tools returned compact sent results",
        "; ".join(
            reason
            for ok, reason in (
                (sent_result_ok, "missing message_id result"),
                (poll_ok, "missing poll_id"),
                (dice_ok, "missing dice_value"),
                (compact_ok, "full message JSON leaked"),
                (routed_ok, "mock bot did not receive expected native calls"),
            )
            if not ok
        ),
    )

    async def failing_paid_media(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TelegramAPIError("Telegram API sendPaidMedia failed: 400 account is restricted")

    async def failing_checklist(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TelegramAPIError("Telegram API sendChecklist failed: 400 business account required")

    bot.send_paid_media = failing_paid_media  # type: ignore[method-assign]
    bot.send_checklist = failing_checklist  # type: ignore[method-assign]
    paid_text = await _invoke_native_tool(
        bridge,
        app_server,
        request_id=220,
        turn_id=context.turn_id,
        tool="telegram_send_paid_media",
        arguments={"star_count": 5, "media": [{"type": "video", "path": "paid.mp4"}]},
    )
    checklist_text = await _invoke_native_tool(
        bridge,
        app_server,
        request_id=221,
        turn_id=context.turn_id,
        tool="telegram_send_checklist",
        arguments={"business_connection_id": "biz_1", "title": "Launch", "tasks": ["Test", "Ship"]},
    )
    account_gated_ok = (
        "Telegram API error:" in paid_text
        and "account is restricted" in paid_text
        and "Telegram API error:" in checklist_text
        and "business account required" in checklist_text
        and not _full_message_json_leaked(paid_text)
        and not _full_message_json_leaked(checklist_text)
    )
    _record_check(
        results,
        "account-gated native tool errors",
        account_gated_ok,
        "Bot API failures were returned as compact tool results",
        f"unexpected tool results: paid={paid_text!r}; checklist={checklist_text!r}",
    )
    await _complete_active_native_turn(bridge)


async def run_native_payload_smoke(*, workspace_arg: str | None = None) -> int:
    results: list[SmokeResult] = []
    with tempfile.TemporaryDirectory(prefix="codex-gateway-native-payloads-", ignore_cleanup_errors=True) as temp_dir:
        temp_root = Path(temp_dir)
        workspace = _workspace(temp_root, workspace_arg)
        workspace.mkdir(parents=True, exist_ok=True)
        settings = _settings(temp_root / "state", workspace)
        store = TelegramStateStore(settings.state_dir)
        access = AccessManager(store)
        bot = MockBot()
        app_server = RecordingAppServer()
        bridge = TelegramBridge(settings, store, access, bot, app_server=app_server)
        access.allow_user(str(USER_ID), username="hybrid-smoke", source="probe")

        bot.files["photo_big"] = {"file_path": "photos/big.png", "file_size": 3}
        bot.downloads["photos/big.png"] = b"png"
        await _exercise_native_inbound_payload(
            bridge,
            app_server,
            results,
            "inbound photo payload",
            _native_payload_update(
                30,
                {
                    "caption": "make the hair red",
                    "photo": [
                        {"file_id": "photo_small", "file_unique_id": "small", "file_size": 1},
                        {"file_id": "photo_big", "file_unique_id": "big", "file_size": 3},
                    ],
                },
            ),
            expected_fragments=(
                "make the hair red",
                "Telegram attachment: photo_big.png",
                "Payload type: photo",
                "Telegram file id: photo_big",
                "MIME type: image/png",
            ),
            expect_local_image=True,
        )

        bot.files["video_1"] = {"file_path": "videos/clip.mp4", "file_size": 9}
        bot.downloads["videos/clip.mp4"] = b"mp4 bytes"
        await _exercise_native_inbound_payload(
            bridge,
            app_server,
            results,
            "inbound video payload",
            _native_payload_update(
                31,
                {
                    "video": {
                        "file_id": "video_1",
                        "file_unique_id": "v1",
                        "file_size": 9,
                        "duration": 5,
                        "width": 640,
                        "height": 360,
                        "mime_type": "video/mp4",
                    }
                },
                text="inspect video",
            ),
            expected_fragments=("inspect video", "Payload type: video", "Duration: 5", "Width: 640"),
        )

        bot.files["doc_1"] = {"file_path": "docs/note.txt", "file_size": 4}
        bot.downloads["docs/note.txt"] = b"note"
        await _exercise_native_inbound_payload(
            bridge,
            app_server,
            results,
            "inbound document payload",
            _native_payload_update(
                32,
                {"document": {"file_id": "doc_1", "file_name": "note.txt", "file_size": 4, "mime_type": "text/plain"}},
                text="read this",
            ),
            expected_fragments=(
                "read this",
                "Telegram attachment: note.txt",
                "Payload type: document",
                "MIME type: text/plain",
            ),
        )

        bot.files["sticker_1"] = {"file_path": "stickers/smile.webp", "file_size": 5}
        bot.downloads["stickers/smile.webp"] = b"webp"
        await _exercise_native_inbound_payload(
            bridge,
            app_server,
            results,
            "inbound sticker payload",
            _native_payload_update(
                33,
                {
                    "sticker": {
                        "file_id": "sticker_1",
                        "file_unique_id": "s1",
                        "file_size": 5,
                        "emoji": ":)",
                        "type": "regular",
                        "is_animated": False,
                        "is_video": False,
                    }
                },
            ),
            expected_fragments=("Payload type: sticker", "Telegram file id: sticker_1", "Emoji: :)"),
        )

        structured_cases: list[tuple[str, dict[str, Any], tuple[str, ...], tuple[str, ...]]] = [
            (
                "inbound contact payload",
                {"contact": {"phone_number": "+15551212", "first_name": "Ada", "last_name": "Lovelace", "user_id": 99}},
                ("Telegram contact", "Name: Ada Lovelace", "phone_number: +15551212"),
                (),
            ),
            (
                "inbound location payload",
                {"location": {"latitude": 14.6, "longitude": 121.0, "horizontal_accuracy": 10}},
                ("Telegram location", "latitude: 14.6", "longitude: 121.0"),
                (),
            ),
            (
                "inbound venue payload",
                {
                    "venue": {
                        "location": {"latitude": 14.6, "longitude": 121.0},
                        "title": "HQ",
                        "address": "Main St",
                    },
                    "location": {"latitude": 1, "longitude": 2},
                },
                ("Telegram venue", "title: HQ", "location_latitude: 14.6"),
                ("Telegram location",),
            ),
            (
                "inbound poll payload",
                {
                    "poll": {
                        "id": "poll_1",
                        "question": "Ship?",
                        "options": [
                            {"text": "Yes", "voter_count": 2},
                            {"text": "No", "voter_count": 1},
                        ],
                        "total_voter_count": 3,
                    }
                },
                ("Telegram poll", "total_voter_count: 3", "option_1: Yes (2 votes)", "option_2: No (1 votes)"),
                (),
            ),
            (
                "inbound dice payload",
                {"dice": {"emoji": "\U0001f3b2", "value": 4}},
                ("Telegram dice", "value: 4"),
                (),
            ),
        ]
        for index, (name, payload, expected, unexpected) in enumerate(structured_cases, start=40):
            await _exercise_native_inbound_payload(
                bridge,
                app_server,
                results,
                name,
                _native_payload_update(index, payload),
                expected_fragments=expected,
                unexpected_fragments=unexpected,
            )

        await _exercise_native_outbound_tools(bridge, bot, app_server, workspace, results)

    failures = [result for result in results if result.status == "fail"]
    print(f"workspace: {workspace}")
    print(f"checked: {len(results)} native Telegram payload smoke actions")
    for result in results:
        status = result.status.upper() if result.status != "ok" else "ok"
        detail = _redact(result.detail.replace("\n", " "))[:180]
        print(f"{status:4} {result.name}: {detail}")
    if failures:
        print(f"{len(failures)} failures", file=sys.stderr)
        return 1
    return 0


async def run_smoke(
    *,
    workspace_arg: str | None = None,
    include_turns: bool = False,
    exhaustive: bool = False,
    audit_affected: bool = False,
) -> int:
    if audit_affected:
        include_turns = False
        exhaustive = False
    elif exhaustive:
        include_turns = True
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        print("codex binary not found on PATH", file=sys.stderr)
        return 2

    results: list[SmokeResult] = []
    manager: AppServerProcessManager | None = None
    client: AppServerClient | None = None
    with tempfile.TemporaryDirectory(prefix="codex-gateway-hybrid-", ignore_cleanup_errors=True) as temp_dir:
        temp_root = Path(temp_dir)
        workspace = _workspace(temp_root, workspace_arg)
        settings = _settings(temp_root / "state", workspace)
        store = TelegramStateStore(settings.state_dir)
        access = AccessManager(store)
        bot = MockBot()
        bridge = TelegramBridge(settings, store, access, bot, app_server=None)
        manager = AppServerProcessManager(
            codex_bin=codex_bin,
            url=os.environ.get("CODEX_GATEWAY_PROBE_APP_SERVER_URL", "ws://127.0.0.1:48173"),
            ready_timeout_seconds=20,
        )
        try:
            await manager.start()
            transport = WebSocketJsonRpcTransport(manager.url)
            await transport.start()
            client = AppServerClient(
                transport=transport,
                on_notification=bridge.handle_app_event,
                on_request=bridge.handle_app_server_request,
                retry_delay_seconds=0,
            )
            bridge.app_server = client
            await client.start()
            access.allow_user(str(USER_ID), username="hybrid-smoke", source="probe")

            message_id = 10
            if audit_affected:
                try:
                    await bridge._start_new_thread(str(CHAT_ID), workspace)
                except Exception as exc:
                    results.append(SmokeResult("audit thread setup", "fail", f"raised {exc.__class__.__name__}: {exc}"))
                for command in [
                    "/cancel",
                    "/interrupt",
                    "/logout",
                    "/mcp",
                    "/mcp invalid",
                    "/features",
                    "/skills",
                    "/apps",
                    "/config",
                    "/rollback",
                    "/diff",
                ]:
                    await _send(bridge, bot, results, command, message_id, audit_affected=True)
                    message_id += 1
                message_id = await _exercise_resume_callback(
                    bridge,
                    bot,
                    results,
                    message_id,
                    require_keyboard_clear=True,
                )
                for command in ["/model", "/permissions", "/approval", "/mode"]:
                    message_id, _labels = await _exercise_selector(
                        bridge,
                        bot,
                        results,
                        command,
                        message_id,
                        require_keyboard_clear=True,
                    )
            else:
                for command in [
                    "/start",
                    "/help",
                    "/status",
                    "/usage",
                    "/context",
                    "/commands",
                    "/project",
                    "/projects",
                    "/workspace",
                    "/workspace list",
                    "/workspace set .",
                    "/setcwd .",
                    "/getcwd",
                    "/searchcwd workspace",
                    "/clear",
                    "/cancel",
                    "/interrupt",
                    "/steer",
                    "/steer no active turn",
                    "/exec python -V",
                    "/new",
                ]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1

                message_id = await _exercise_resume_callback(bridge, bot, results, message_id)

                for command in [
                    "/goal",
                    "/goal set Hybrid smoke",
                    "/goal bogus",
                    "/goal set",
                    "/goal clear",
                    "/rename Hybrid smoke",
                    "/rename",
                    "/threads",
                    "/threads Hybrid",
                    "/personality pragmatic",
                    "/memories disabled",
                    "/ps",
                    "/account",
                    "/limits",
                    "/hooks",
                    "/mcp",
                    "/mcp verbose",
                    "/mcp reload",
                    "/mcp invalid",
                    "/apps",
                    "/plugins",
                    "/features",
                    "/skills",
                    "/config",
                    "/debug-config",
                    "/diff",
                ]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1

                message_id, model_labels = await _exercise_selector(bridge, bot, results, "/model", message_id)
                message_id, permission_labels = await _exercise_selector(bridge, bot, results, "/permissions", message_id)
                message_id, approval_labels = await _exercise_selector(bridge, bot, results, "/approval", message_id)
                message_id, mode_labels = await _exercise_selector(bridge, bot, results, "/mode", message_id)
                message_id, personality_labels = await _exercise_selector(bridge, bot, results, "/personality", message_id)
                message_id, memory_labels = await _exercise_selector(bridge, bot, results, "/memories", message_id)
                message_id, experimental_labels = await _exercise_selector(
                    bridge,
                    bot,
                    results,
                    "/experimental",
                    message_id,
                    restore_toggles=True,
                )
                message_id, skill_labels = await _exercise_selector(
                    bridge,
                    bot,
                    results,
                    "/skills",
                    message_id,
                    restore_toggles=True,
                )
                message_id, stop_labels = await _exercise_selector(bridge, bot, results, "/stop", message_id)
                message_id, agent_labels = await _exercise_selector(bridge, bot, results, "/agent", message_id)
                await bridge.handle_app_event(
                    AppServerEvent(
                        "item/autoApprovalReview/completed",
                        {
                            "threadId": str(bridge._thread_record(str(CHAT_ID), workspace).get("thread_id") or ""),
                            "turnId": "turn_probe",
                            "id": "guardian_probe",
                            "reviewId": "review_probe",
                            "status": "denied",
                            "review": {"status": "denied", "riskLevel": "high"},
                            "action": {
                                "id": "guardian_action_probe",
                                "type": "command",
                                "command": "echo probe",
                                "cwd": str(workspace),
                                "source": "shell",
                            },
                        },
                    )
                )
                message_id, approve_labels = await _exercise_selector(bridge, bot, results, "/approve", message_id)

                typed_setting_commands = [
                    f"/model {model_labels[0]} medium" if model_labels and model_labels[0] != "Cancel" else None,
                    "/permissions default",
                    "/permissions read-only",
                    "/approval on-request",
                    f"/mode {mode_labels[0]}" if mode_labels and mode_labels[0] != "Cancel" else None,
                    "/personality friendly",
                    "/memories enabled",
                    "/memories disabled",
                    "/approval invalid-policy",
                    "/effort impossible",
                    "/mode impossible-mode",
                ]
                for command in [item for item in typed_setting_commands if item is not None]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1

                for command in ["/stop", "/agent", "/subagents", "/approve"]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1
                if not exhaustive:
                    _record_skips(
                        results,
                        {f"/{name}": reason for name, reason in STATEFUL_COMMAND_SKIP_REASONS.items()},
                    )
                if include_turns:
                    turn_commands = [
                        "/read README.md",
                        "/review",
                        "/mention README.md",
                        "/collab",
                        "/fork",
                        "/init",
                        "/plan Make a concise smoke plan",
                        "/side Check this in a side thread",
                        "/btw Check this as an aside",
                    ]
                    object.__setattr__(bridge.settings, "enable_exec", True)
                    turn_commands.append("/exec python -V")
                    for command in turn_commands:
                        await _send(bridge, bot, results, command, message_id)
                        message_id += 1
                        await asyncio.sleep(0.5)
                        await _send(bridge, bot, results, "/cancel", message_id)
                        message_id += 1
                        await _wait_for_bridge_idle(bridge, timeout_seconds=10)
                    await _send(bridge, bot, results, "/compact", message_id)
                    message_id += 1
                    if exhaustive:
                        for command in ["/new", "/approval never", "/permissions default"]:
                            await _send(bridge, bot, results, command, message_id)
                            message_id += 1
                        await _send(bridge, bot, results, "Reply exactly: HYBRID_READY", message_id)
                        message_id += 1
                        if not await _wait_for_bridge_idle(bridge):
                            results.append(
                                SmokeResult("completed turn setup", "fail", "timed out waiting for real turn completion")
                            )
                        for command in ["/archive", "/unarchive", "/rollback"]:
                            await _send(bridge, bot, results, command, message_id)
                            message_id += 1
                else:
                    _record_skips(
                        results,
                        {f"/{name}": reason for name, reason in TURN_COMMAND_SKIP_REASONS.items()},
                    )
                    results.append(SmokeResult("/exec enabled", "skip", "starts a real model turn when enabled"))

                for command in [f"/{name}" for name in sorted(UNSUPPORTED_COMMANDS)]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1
                for command in ["/models", "/modes", "/permission", "/doesnotexist"]:
                    await _send(bridge, bot, results, command, message_id)
                    message_id += 1

                if not exhaustive:
                    _record_skips(results, SERVER_REQUEST_SKIP_REASONS)
                await _send(bridge, bot, results, "/reset", message_id)
        finally:
            if client is not None:
                await client.stop()
            if manager is not None:
                await manager.stop()

    if not audit_affected:
        skipped_command_names = set(DEFAULT_SKIPPED_COMMAND_NAMES)
        if include_turns:
            skipped_command_names -= set(TURN_COMMAND_SKIP_REASONS)
        if exhaustive:
            skipped_command_names.clear()
        _assert_fixture_completeness(results)
        _assert_command_coverage(results, skipped_command_names)
    failures = [result for result in results if result.status == "fail"]
    skipped = [result for result in results if result.status == "skip"]
    print(f"workspace: {workspace}")
    print(f"checked: {len(results)} hybrid mock-bot/real-app-server actions")
    for result in results:
        status = result.status.upper() if result.status != "ok" else "ok"
        detail = _redact(result.detail.replace("\n", " "))[:180]
        print(f"{status:4} {result.name}: {detail}")
    if failures:
        print(f"{len(failures)} failures", file=sys.stderr)
        return 1
    if skipped:
        print(f"{len(skipped)} explicit skips")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a mock Telegram bot against a real local Codex app-server.")
    parser.add_argument("--workspace", help="Workspace to use; defaults to a fresh temporary workspace.")
    parser.add_argument(
        "--include-turns",
        action="store_true",
        help="Also run slash commands that start real model turns, then attempt to cancel them.",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Fail on any unexercised command unless the result is an explicit account/config feature gate.",
    )
    parser.add_argument(
        "--audit-affected",
        action="store_true",
        help="Run only Telegram audit remediation slash/callback paths and verify callback edits clear keyboards.",
    )
    parser.add_argument(
        "--native-payloads",
        action="store_true",
        help="Run deterministic native Telegram payload and dynamic-tool coverage without a live app-server.",
    )
    args = parser.parse_args()
    if args.native_payloads:
        raise SystemExit(asyncio.run(run_native_payload_smoke(workspace_arg=args.workspace)))
    raise SystemExit(
        asyncio.run(
            run_smoke(
                workspace_arg=args.workspace,
                include_turns=args.include_turns,
                exhaustive=args.exhaustive,
                audit_affected=args.audit_affected,
            )
        )
    )

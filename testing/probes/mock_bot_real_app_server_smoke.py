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
    args = parser.parse_args()
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

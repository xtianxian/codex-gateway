from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...backends.codex_app_server.client import AppServerClient, AppServerEvent, JsonRpcError
from ...backends.codex_app_server.lifecycle import AppServerProcessManager
from ...backends.codex_app_server.protocol import generated_protocol_methods, generated_server_request_methods
from ...backends.codex_app_server.transport import WebSocketJsonRpcTransport
from ...core.commands import default_command_registry
from .access import AccessManager, _format_iso, _parse_iso
from .bot_api import TelegramAPIError, TelegramBotAPI
from .commands import (
    TelegramCommand,
    TelegramCommandKind,
    command_turn_prompt,
    parse_telegram_command,
    unsupported_command_message,
)
from .bridge_helpers import (
    sanitize_text,
    _input_items,
    _assistant_text,
    _format_turn_plan,
    _plan_item_text,
    _output_attachment,
    _output_attachment_caption,
    _decode_image_result,
    _image_content_type,
    _generated_image_filename,
    _extract_id,
    _tool_arguments,
    _tool_file_path,
    _tool_name,
    _message_id,
    _pairing_guidance_text,
    _start_pairing_text,
    _unauthorized_user_text,
    _turn_id,
    _thread_id,
    _item,
    _command_summary,
    _file_change_summary,
    _approval_text,
    _permissions_approval_text,
    _current_user_input_question,
    _question_options,
    _tool_user_input_text,
    _mcp_elicitation_text,
    _mcp_elicitation_field_labels,
    _action_past_tense,
    _params_shape,
    _skill_names,
    _iter_skill_groups,
    _skill_path,
    _result_items,
    _result_items_or_scalars,
    _find_named_item,
    _find_model,
    _find_permission_profile,
    _resolve_permission_profile,
    _cli_permission_choice,
    _find_skill,
    _model_config_value,
    _model_selection_options,
    _split_model_effort_args,
    _reasoning_effort_aliases,
    _reasoning_effort_value,
    _personality_value,
    _memory_mode_value,
    _reasoning_effort_label,
    _model_supported_reasoning_efforts,
    _model_default_reasoning_effort,
    _model_supports_reasoning_effort,
    _model_reasoning_effort_options,
    _unsupported_model_effort_text,
    _permission_profile_value,
    _permission_profile_label,
    _permission_lookup_key,
    _permission_profile_approval_policy,
    _mode_selection_values,
    _mode_display_name,
    _format_models,
    _format_features,
    _feature_name,
    _feature_label,
    _format_skills,
    _format_apps,
    _format_plugins,
    _format_loaded_threads,
    _loaded_thread_line,
    _loaded_thread_label,
    _thread_status_text,
    _thread_is_subagent,
    _format_guardian_denials,
    _guardian_denial_label,
    _live_process_lines,
    _thread_process_lines,
    _iter_thread_items,
    _process_item_line,
    _dedupe_lines,
    _apps_unavailable_error,
    _format_account,
    _format_rate_limits,
    _format_rate_limit_snapshot,
    _format_gateway_status,
    _status_config,
    _first_text,
    _format_permission_status,
    _format_agents_status,
    _find_agents_file,
    _format_status_account,
    _format_context_window_status,
    _format_token_usage_status,
    _format_rate_limit_status,
    _format_rate_limit_window,
    _rate_limit_window_label,
    _format_reset_time,
    _format_thread_token_usage,
    _format_token_breakdown,
    _int_or_none,
    _format_int,
    _percent,
    _format_hooks,
    _format_mcp_servers,
    _format_config,
    _thread_id_from_thread_item,
    _thread_title_from_item,
    _thread_title_from_text,
    _thread_title_from_read_result,
    _first_thread_message_text,
    _thread_item_text,
    _resume_button_text,
    _format_threads,
    _format_goal,
    _format_lines,
    _git_diff_with_untracked,
    _run_git,
    _untracked_file_diff,
    _safe_filename,
    _attachment_filename,
    _attachment_mime_type,
    _is_image_attachment,
    _message_text_with_payload_summary,
    _bot_chat_id,
    _command_disabled_during_active_turn,
    _thread_sandbox_value,
    _approval_policy_value,
)
from .constants import (
    _AUTH_HEADER_PATTERN,
    _SECRET_PATTERNS,
    TYPING_ACTION_INTERVAL_SECONDS,
    TELEGRAM_UPDATE_HANDLE_TIMEOUT_SECONDS,
    AUTO_THREAD_TITLE_MAX_CHARS,
    APPROVAL_POLICY_CHOICES,
    EFFORT_CHOICES,
    PERSONALITY_CHOICES,
    MEMORY_MODE_CHOICES,
    CLI_PERMISSION_CHOICES,
    ACTIVE_TURN_DISABLED_COMMANDS,
    SETTABLE_EXPERIMENTAL_FEATURES,
    TELEGRAM_SERVER_REQUEST_SUPPORT,
    TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY,
    TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
    TELEGRAM_HELP_TEXT,
)
from .dynamic_tools import _dynamic_tools_fingerprint, telegram_dynamic_tools
from .bridge_commands import TelegramBridgeCommandMixin
from .bridge_io import TelegramBridgeIOMixin
from .bridge_requests import TelegramBridgeRequestMixin
from .bridge_threads import TelegramBridgeThreadMixin
from .config import (
    TelegramSettings,
    TelegramSettingsError,
    get_telegram_settings,
    is_path_within_any_root,
    resolve_workspace,
)
from .state import TelegramStateStore
from .types import OutputAttachment, TurnContext


LOGGER = logging.getLogger(__name__)
TELEGRAM_POLL_RETRY_DELAY_SECONDS = 5.0
TELEGRAM_POLL_RECREATE_FAILURES = 3
TELEGRAM_POLL_WATCHDOG_GRACE_SECONDS = 15.0
TELEGRAM_POLL_HEARTBEAT_SECONDS = 300.0
PLAN_CHOICE_TEXT = (
    "Implement this plan?\n\n"
    "1. Yes, implement this plan\n"
    "2. Yes, clear context and implement\n"
    "3. No, stay in Plan mode"
)


@dataclass
class TelegramPollingState:
    latest_offset: int | None = None
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    client_recreated_during_failure_streak: bool = False


@dataclass
class AppServerRuntime:
    client: AppServerClient
    process_manager: AppServerProcessManager | None = None


def _plan_implementation_prompt(plan_text: str) -> str:
    return (
        "Implement this plan now. Re-read files as needed, and carry the work through "
        "implementation and verification.\n\n"
        f"{plan_text}"
    )


def _fresh_plan_implementation_prompt(plan_text: str) -> str:
    return (
        "A previous agent produced the plan below to accomplish the user's task. "
        "Implement the plan in a fresh context. Treat the plan as the source of user intent, "
        "re-read files as needed, and carry the work through implementation and verification.\n\n"
        f"{plan_text}"
    )


class TelegramBridge(
    TelegramBridgeCommandMixin,
    TelegramBridgeThreadMixin,
    TelegramBridgeRequestMixin,
    TelegramBridgeIOMixin,
):
    def __init__(
        self,
        settings: TelegramSettings,
        store: TelegramStateStore,
        access: AccessManager,
        bot: Any,
        app_server: Any,
    ) -> None:
        self.settings = settings
        self.store = store
        self.access = access
        self.bot = bot
        self.app_server = app_server
        self.turns: dict[str, TurnContext] = {}
        self.latest_turn_by_thread: dict[str, str] = {}
        self.bridge_messages: set[tuple[str, int]] = set()
        self.resumed_thread_ids: set[str] = set()
        self.workspace_reset_chats: set[str] = set()
        self.typing_tasks: dict[str, asyncio.Task[None]] = {}
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self.guardian_denials_by_thread: dict[str, list[dict[str, Any]]] = {}
        self.user_notice_times: dict[tuple[str, str], datetime] = {}
        self.app_server_state = "ready"

    async def sync_telegram_command_menu(self) -> str | None:
        if not hasattr(self.bot, "set_my_commands"):
            return None
        registry = default_command_registry()
        commands = registry.telegram_menu_payload(
            supported_methods=set(generated_protocol_methods()),
            enable_exec=self.settings.enable_exec,
            advertise_exec=self.settings.advertise_exec,
        )
        try:
            await self.bot.set_my_commands(commands)
        except TelegramAPIError as exc:
            return str(exc)
        return None

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat_id = str((message.get("chat") or {}).get("id"))
        user = message.get("from") or {}
        user_id = str(user.get("id"))
        username = str(user.get("username") or "")
        text = str(message.get("text") or message.get("caption") or "")
        command = parse_telegram_command(text)

        self._record_last_update(chat_id, update.get("update_id"))

        if not self.access.can_receive_message(chat_id=chat_id, user_id=user_id):
            await self._handle_unpaired_or_unauthorized_user(chat_id, user_id, username, command)
            return

        if self.app_server_state != "ready" and _command_requires_ready_app_server(command):
            await self._send_user_notice(
                chat_id,
                "app_server_reconnecting",
                "Codex is reconnecting. I'll keep this chat active.",
            )
            return

        if command.kind == TelegramCommandKind.MESSAGE and await self._handle_pending_user_input_message(
            chat_id,
            user_id,
            text,
        ):
            return

        active_context = await self._active_turn_context_or_recover(chat_id)
        if active_context is not None and _command_disabled_during_active_turn(command):
            await self._send_active_task_disabled_message(chat_id, command.name or "")
            return

        if command.kind == TelegramCommandKind.LOCAL:
            await self._handle_local_command(chat_id, command)
            return
        if command.kind == TelegramCommandKind.THREAD:
            try:
                await self._handle_thread_command(chat_id, user_id, command)
            except JsonRpcError as exc:
                await self._send(chat_id, f"App-server command failed: {exc}")
            return
        if command.kind == TelegramCommandKind.APP_SERVER:
            await self._handle_app_server_command(chat_id, user_id, command)
            return
        if command.kind == TelegramCommandKind.CODEX_TURN:
            try:
                if command.name == "exec":
                    if not self.settings.enable_exec:
                        await self._send(
                            chat_id,
                            "/exec is disabled. Set CODEX_GATEWAY_ENABLE_EXEC=1 to enable it.",
                        )
                        return
                    if not command.args:
                        await self._send(chat_id, "Use /exec <command>.")
                        return
                if command.name == "review" and await self._review_current_thread(chat_id):
                    return
                if command.name == "compact" and await self._compact_current_thread(chat_id):
                    return
                if command.name == "init":
                    if await self._init_project_instructions(chat_id, user_id, _message_id(message)):
                        return
                if command.name == "plan":
                    if await self._handle_plan_turn_command(chat_id, user_id, command, _message_id(message)):
                        return
                if active_context is not None:
                    await self._send_active_turn_wait_message(chat_id)
                    return
                await self._start_turn(
                    chat_id=chat_id,
                    user_id=user_id,
                    message_id=_message_id(message),
                    text=command_turn_prompt(command),
                    attachments=[],
                )
            except JsonRpcError as exc:
                await self._send(chat_id, f"App-server command failed: {exc}")
            return
        if command.kind == TelegramCommandKind.UNSUPPORTED:
            await self._send(chat_id, unsupported_command_message(command))
            return
        if command.kind == TelegramCommandKind.UNKNOWN:
            await self._send(chat_id, f"Unknown command: /{command.name}")
            return

        if active_context is not None:
            await self._send_active_turn_wait_message(chat_id)
            return
        attachments = await self._download_message_attachments(chat_id, message)
        if attachments is None:
            return
        turn_text = _message_text_with_payload_summary(text, message)
        try:
            await self._start_turn(
                chat_id=chat_id,
                user_id=user_id,
                message_id=_message_id(message),
                text=turn_text,
                attachments=attachments,
            )
        except JsonRpcError as exc:
            await self._send(chat_id, f"App-server command failed: {exc}")

    async def handle_app_event(self, event: AppServerEvent) -> None:
        if event.method == "thread/tokenUsage/updated":
            self._save_thread_token_usage(event.params)
            return
        if event.method == "item/autoApprovalReview/completed":
            self._record_guardian_denial(event.params)
            return

        turn_id = _turn_id(event.params)
        context = self.turns.get(turn_id or "")
        if context is None:
            return
        context.last_event_at = self.access.now_fn()

        if event.method == "item/agentMessage/delta":
            delta = str(event.params.get("delta") or "")
            if delta:
                self._record_turn_progress(context, "assistant_delta")
                context.final_text += delta
            return
        if event.method == "item/started":
            self._record_turn_progress(context, "item_started")
            return
        if event.method == "turn/plan/updated":
            text = _format_turn_plan(event.params)
            if text:
                self._record_turn_progress(context, "turn_plan_updated")
                await self._send_or_update_plan(context, text)
            return
        if event.method == "item/completed":
            self._record_turn_progress(context, "item_completed")
            item = _item(event.params)
            await self._send_output_attachment(context, item)
            plan_text = _plan_item_text(item)
            if plan_text:
                await self._send_or_update_plan(context, plan_text)
                return
            text = _assistant_text(item)
            if text:
                context.final_text = text
            return
        if "commandExecution/started" in event.method:
            self._record_turn_progress(context, "command_started", background_activity=True)
            return
        if "commandExecution/completed" in event.method:
            self._record_turn_progress(context, "command_completed", background_activity=True)
            return
        if "fileChange" in event.method:
            self._record_turn_progress(context, "file_change")
            return
        if event.method == "turn/completed":
            self._stop_typing_indicator(context.turn_id)
            context.completed = True
            turn = event.params.get("turn") if isinstance(event.params.get("turn"), dict) else {}
            status = str(turn.get("status") or event.params.get("status") or "completed")
            self._record_turn_progress(
                context,
                "turn_completed",
                terminal=True,
                interrupted=status == "interrupted",
                waiting_on_user=False,
            )
            if status == "failed":
                error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                message = error.get("message") if isinstance(error, dict) else None
                await self._send(context.chat_id, f"Turn failed: {sanitize_text(str(message or 'Turn failed.'))}")
                return
            if status == "interrupted":
                await self._send(context.chat_id, "Turn cancelled.")
                return
            for item in turn.get("items") or []:
                if isinstance(item, dict):
                    await self._send_output_attachment(context, item)
            self._schedule_auto_name_thread(context)
            if (
                context.final_text
                and not context.plan_text
                and not context.tool_replied
                and not context.auto_replied
            ):
                await self._send(context.chat_id, context.final_text)
                context.auto_replied = True
            if context.plan_text:
                await self._send_or_update_plan_choices(context)

    async def _send_or_update_plan(self, context: TurnContext, text: str) -> None:
        text = text.strip()
        if not text or text == context.plan_text:
            return
        context.plan_text = text
        sanitized = sanitize_text(text)
        if context.plan_message_id is not None and hasattr(self.bot, "edit_message_text") and len(sanitized) <= 3500:
            try:
                await self.bot.edit_message_text(
                    _bot_chat_id(context.chat_id),
                    context.plan_message_id,
                    sanitized,
                )
                return
            except Exception as exc:
                LOGGER.debug("Failed to edit Telegram plan message: %s", exc)
                context.plan_message_id = None
        sent = await self._send(context.chat_id, text)
        if len(sent) != 1 or len(sanitized) > 3500:
            return
        message_id = sent[0].get("message_id")
        if message_id is not None:
            context.plan_message_id = int(message_id)

    async def _send_or_update_plan_choices(self, context: TurnContext) -> None:
        keyboard = self._plan_choice_keyboard(context)
        if context.plan_choice_message_id is not None and hasattr(self.bot, "edit_message_text"):
            try:
                await self.bot.edit_message_text(
                    _bot_chat_id(context.chat_id),
                    context.plan_choice_message_id,
                    PLAN_CHOICE_TEXT,
                    reply_markup={"inline_keyboard": keyboard},
                )
                self._save_plan_choice_message_id(context)
                return
            except Exception as exc:
                LOGGER.debug("Failed to edit Telegram plan choice message: %s", exc)
                context.plan_choice_message_id = None
        sent = await self._send(context.chat_id, PLAN_CHOICE_TEXT, reply_markup={"inline_keyboard": keyboard})
        if sent:
            message_id = sent[0].get("message_id")
            if message_id is not None:
                context.plan_choice_message_id = int(message_id)
                self._save_plan_choice_message_id(context)

    def _plan_choice_keyboard(self, context: TurnContext) -> list[list[dict[str, str]]]:
        pending = self.store.load_pending_selections()
        if context.plan_selection_group_id:
            for token, record in list(pending.items()):
                if isinstance(record, dict) and record.get("group_id") == context.plan_selection_group_id:
                    pending.pop(token, None)
        group_id = secrets.token_urlsafe(8)
        context.plan_selection_group_id = group_id
        expires_at = _format_iso(self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds))
        value = {
            "thread_id": context.thread_id,
            "turn_id": context.turn_id,
            "plan_text": context.plan_text,
        }
        rows: list[list[dict[str, str]]] = []
        for label, action in (
            ("Yes, implement this plan", "plan_implement"),
            ("Yes, clear context and implement", "plan_fresh"),
            ("No, stay in Plan mode", "plan_stay"),
        ):
            token = secrets.token_urlsafe(8)
            pending[token] = {
                "chat_id": str(context.chat_id),
                "user_id": str(context.user_id),
                "action": action,
                "value": value,
                "group_id": group_id,
                "expires_at": expires_at,
            }
            rows.append([{"text": label, "callback_data": f"select:{token}"}])
        self.store.save_pending_selections(pending)
        return rows

    def _save_plan_choice_message_id(self, context: TurnContext) -> None:
        if context.plan_choice_message_id is None or not context.plan_selection_group_id:
            return
        pending = self.store.load_pending_selections()
        changed = False
        for record in pending.values():
            if isinstance(record, dict) and record.get("group_id") == context.plan_selection_group_id:
                record["message_id"] = context.plan_choice_message_id
                changed = True
        if changed:
            self.store.save_pending_selections(pending)

    async def _apply_plan_choice(
        self,
        chat_id: str,
        user_id: str,
        workspace: Path,
        action: str,
        value: dict[str, Any],
    ) -> str:
        plan_text = _first_text(value.get("plan_text"))
        if not plan_text:
            return "Plan is no longer available."
        if action == "plan_stay":
            return "Staying in Plan mode. Send feedback or a revised request."
        if await self._active_turn_context_or_recover(chat_id) is not None:
            return "The planning turn is still running. Wait for it to finish, then choose again."
        if action == "plan_implement":
            mode_error = await self._switch_to_default_mode(chat_id, workspace)
            if mode_error:
                return mode_error
            await self._start_turn(
                chat_id=chat_id,
                user_id=user_id,
                message_id=None,
                text=_plan_implementation_prompt(plan_text),
                attachments=[],
            )
            return "Implementing this plan in Default mode."
        if action == "plan_fresh":
            self._save_thread_active_mode(chat_id, workspace, "default")
            await self._start_turn(
                chat_id=chat_id,
                user_id=user_id,
                message_id=None,
                text=_fresh_plan_implementation_prompt(plan_text),
                attachments=[],
                force_new_thread=True,
            )
            return "Starting a fresh Default-mode thread to implement this plan."
        return "Plan choice is no longer available."

    async def _switch_to_default_mode(self, chat_id: str, workspace: Path) -> str | None:
        payload = await self._collaboration_mode_payload_for_mode(chat_id, workspace, "default")
        if payload is None:
            model = self._thread_mode_setting(chat_id, workspace, "model", mode_name="default")
            if model is None:
                model = await self._default_model_setting()
            if model is None:
                return "Collaboration mode cannot be applied: default"
            payload = {
                "mode": "default",
                "settings": {
                    "developer_instructions": None,
                    "model": model,
                    "reasoning_effort": self._thread_mode_setting(
                        chat_id,
                        workspace,
                        "effort",
                        mode_name="default",
                    )
                    or self.settings.model_reasoning_effort,
                },
            }
        existing_thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        self._save_thread_active_mode(chat_id, workspace, "default")
        thread_id = await self._ensure_thread(chat_id, workspace, active_mode_payload=payload)
        if existing_thread_id and existing_thread_id == thread_id:
            await self.app_server.thread_settings_update(thread_id=thread_id, collaboration_mode=payload)
        return None

    async def handle_app_server_request(self, event: AppServerEvent) -> None:
        LOGGER.debug(
            "app-server request method=%s request_id=%s params_shape=%s",
            event.method,
            event.request_id,
            _params_shape(event.params),
        )
        handlers = {
            "item/commandExecution/requestApproval": self._handle_approval_request,
            "item/fileChange/requestApproval": self._handle_approval_request,
            "item/permissions/requestApproval": self._handle_permissions_approval_request,
            "mcpServer/elicitation/request": self._handle_mcp_elicitation_request,
            "item/tool/requestUserInput": self._handle_tool_user_input_request,
            "item/tool/call": self._handle_tool_call,
        }
        handler = handlers.get(event.method)
        if handler is not None:
            await handler(event)
            return
        if event.method == "account/chatgptAuthTokens/refresh":
            if event.request_id is not None:
                await self.app_server.send_error_response(
                    event.request_id,
                    "Server request account/chatgptAuthTokens/refresh is unsupported by the Telegram gateway.",
                    code=-32601,
                )
            return
        if event.method in {"attestation/generate", "applyPatchApproval", "execCommandApproval"}:
            if event.request_id is not None:
                await self.app_server.send_error_response(
                    event.request_id,
                    f"Server request {event.method} was not negotiated by the Telegram gateway.",
                    code=-32601,
                )
            return
        if event.request_id is not None:
            await self.app_server.send_error_response(event.request_id, f"Unsupported app-server request: {event.method}")

    def track_bridge_message(self, chat_id: str | int, message_id: int) -> None:
        self.bridge_messages.add((str(chat_id), int(message_id)))



















































































































async def run_telegram_bridge() -> None:
    settings = get_telegram_settings()
    if not settings.bot_token:
        raise SystemExit("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN is required for `telegram run`.")
    store = TelegramStateStore(settings.state_dir)
    access = AccessManager(store, allowed_user_id=settings.allowed_user_id)
    bot = TelegramBotAPI(settings.bot_token)
    runtime = await _create_app_server_runtime(settings)
    bridge = TelegramBridge(settings, store, access, bot, runtime.client)
    _wire_app_server(runtime.client, bridge)
    await runtime.client.start()
    try:
        sync_error = await bridge.sync_telegram_command_menu()
        if sync_error:
            print(f"Warning: Telegram command menu sync failed: {sync_error}", file=sys.stderr)
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(_telegram_polling_service(settings, store, bot, bridge))
            task_group.create_task(_app_server_supervisor(settings, bridge, runtime))
    finally:
        bridge.stop_typing_indicators()
        bridge.stop_background_tasks()
        await _stop_app_server_runtime(runtime)
        await bot.aclose()


async def _create_app_server_runtime(settings: TelegramSettings) -> AppServerRuntime:
    if settings.app_server_transport == "websocket":
        process_manager = AppServerProcessManager(
            codex_bin=settings.codex_bin,
            url=settings.app_server_url,
            ready_timeout_seconds=30,
        )
        try:
            await process_manager.start()
            transport = WebSocketJsonRpcTransport(process_manager.url, open_timeout_seconds=10)
            await transport.start()
        except Exception:
            await process_manager.stop()
            raise
        return AppServerRuntime(client=AppServerClient(transport=transport), process_manager=process_manager)
    if settings.app_server_transport == "stdio":
        return AppServerRuntime(client=AppServerClient(command=settings.app_server_command))
    raise SystemExit("CODEX_GATEWAY_APP_SERVER_TRANSPORT must be websocket or stdio.")


def _wire_app_server(app_server: AppServerClient, bridge: TelegramBridge) -> None:
    app_server.on_notification = bridge.handle_app_event
    app_server.on_request = bridge.handle_app_server_request


async def _telegram_polling_service(
    settings: TelegramSettings,
    store: TelegramStateStore,
    bot: Any,
    bridge: TelegramBridge,
) -> None:
    offset: int | None = initial_poll_offset(store)
    polling_state = TelegramPollingState(latest_offset=offset)
    while True:
        updates = await get_updates_with_retry(
            bot,
            offset=offset,
            timeout=settings.poll_timeout_seconds,
            state=polling_state,
        )
        for update in updates:
            update_id = update.get("update_id")
            await handle_update_with_recovery(
                bridge,
                update,
                timeout_seconds=TELEGRAM_UPDATE_HANDLE_TIMEOUT_SECONDS,
            )
            if isinstance(update_id, int):
                offset = update_id + 1
                polling_state.latest_offset = offset


async def _app_server_supervisor(
    settings: TelegramSettings,
    bridge: TelegramBridge,
    runtime: AppServerRuntime,
) -> None:
    while True:
        await runtime.client.wait_reader_stopped()
        bridge.app_server_state = "reconnecting"
        await _notify_app_server_state(
            bridge,
            "app_server_reconnecting",
            "Codex is reconnecting. I'll keep this chat active.",
        )
        await _stop_app_server_runtime(runtime)
        for delay in _supervisor_backoff_seconds():
            replacement: AppServerRuntime | None = None
            try:
                replacement = await _create_app_server_runtime(settings)
                _wire_app_server(replacement.client, bridge)
                await replacement.client.start()
            except asyncio.CancelledError:
                if replacement is not None:
                    await _stop_app_server_runtime(replacement)
                raise
            except Exception:
                LOGGER.exception("Failed to restart Codex app-server")
                if replacement is not None:
                    await _stop_app_server_runtime(replacement)
                await asyncio.sleep(delay)
                continue
            runtime.client = replacement.client
            runtime.process_manager = replacement.process_manager
            bridge.app_server = replacement.client
            bridge.resumed_thread_ids.clear()
            bridge.app_server_state = "ready"
            await _notify_app_server_state(
                bridge,
                "app_server_reconnected",
                "Reconnected to Codex. Checking the current turn.",
            )
            await _reconcile_active_turns_after_reconnect(bridge)
            break


async def _stop_app_server_runtime(runtime: AppServerRuntime) -> None:
    await runtime.client.stop()
    if runtime.process_manager is not None:
        await runtime.process_manager.stop()
        runtime.process_manager = None


async def _notify_app_server_state(
    bridge: TelegramBridge,
    notice_type: str,
    message: str,
) -> None:
    for chat_id in _affected_chat_ids(bridge):
        await bridge._send_user_notice(chat_id, notice_type, message)


def _affected_chat_ids(bridge: TelegramBridge) -> list[str]:
    chat_ids = {context.chat_id for context in bridge.turns.values() if not context.completed}
    for key in bridge.store.load_chats():
        if key.startswith("chat_id:"):
            chat_ids.add(key.removeprefix("chat_id:"))
    return sorted(chat_ids)


async def _reconcile_active_turns_after_reconnect(bridge: TelegramBridge) -> None:
    for context in list(bridge.turns.values()):
        if context.completed:
            continue
        try:
            await bridge._recover_completed_active_turn_context(context, notify=True)
        except Exception:
            LOGGER.exception(
                "Failed to reconcile active turn after app-server reconnect",
                extra={"chat_id": context.chat_id, "thread_id": context.thread_id, "turn_id": context.turn_id},
            )


def _supervisor_backoff_seconds() -> tuple[float, ...]:
    return (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


async def handle_update_with_recovery(
    bridge: TelegramBridge,
    update: dict[str, Any],
    *,
    timeout_seconds: float = TELEGRAM_UPDATE_HANDLE_TIMEOUT_SECONDS,
) -> bool:
    chat_id = _chat_id_from_update(update)
    update_id = update.get("update_id")
    try:
        async with asyncio.timeout(timeout_seconds):
            await bridge.handle_update(update)
        return True
    except TimeoutError:
        LOGGER.warning(
            "Telegram update handling timed out",
            extra={"chat_id": chat_id, "update_id": update_id, "timeout_kind": "telegram_update"},
        )
        if chat_id is not None:
            await bridge._send_user_notice(
                chat_id,
                "action_timeout",
                "Codex did not respond in time. Please try again in a moment.",
            )
            bridge._record_last_update(chat_id, update_id)
    except TelegramAPIError as exc:
        LOGGER.warning(
            "Telegram API error while handling update",
            extra={"chat_id": chat_id, "update_id": update_id, "dead_letter_reason": str(exc)},
        )
        if chat_id is not None:
            if exc.ambiguous_delivery:
                await bridge._send_user_notice(
                    chat_id,
                    "telegram_delivery_uncertain",
                    "Telegram delivery is uncertain, so I won't resend automatically.",
                )
            else:
                await bridge._send_user_notice(
                    chat_id,
                    "action_failed",
                    "Codex did not respond in time. Please try again in a moment.",
                )
            bridge._record_last_update(chat_id, update_id)
    except JsonRpcError as exc:
        LOGGER.warning(
            "App-server error while handling Telegram update",
            extra={"chat_id": chat_id, "update_id": update_id, "dead_letter_reason": str(exc)},
        )
        if chat_id is not None:
            await bridge._send_user_notice(
                chat_id,
                "action_failed",
                "Codex did not respond in time. Please try again in a moment.",
            )
            bridge._record_last_update(chat_id, update_id)
    except Exception:
        LOGGER.exception(
            "Unexpected error while handling Telegram update",
            extra={"chat_id": chat_id, "update_id": update_id},
        )
        if chat_id is not None:
            await bridge._send_user_notice(
                chat_id,
                "action_failed",
                "Codex did not respond in time. Please try again in a moment.",
            )
            bridge._record_last_update(chat_id, update_id)
    return False


def _chat_id_from_update(update: dict[str, Any]) -> str | None:
    message = update.get("message")
    if isinstance(message, dict):
        chat_id = (message.get("chat") or {}).get("id")
        return str(chat_id) if chat_id is not None else None
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        callback_message = callback.get("message")
        if isinstance(callback_message, dict):
            chat_id = (callback_message.get("chat") or {}).get("id")
            return str(chat_id) if chat_id is not None else None
    return None


def _command_requires_ready_app_server(command: TelegramCommand) -> bool:
    return command.kind in {
        TelegramCommandKind.MESSAGE,
        TelegramCommandKind.THREAD,
        TelegramCommandKind.APP_SERVER,
        TelegramCommandKind.CODEX_TURN,
    }


async def get_updates_with_retry(
    bot: Any,
    *,
    offset: int | None,
    timeout: int,
    retry_delay_seconds: float = TELEGRAM_POLL_RETRY_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    warn: Callable[[str], Any] | None = None,
    state: TelegramPollingState | None = None,
    watchdog_timeout_seconds: float | None = None,
    heartbeat_interval_seconds: float = TELEGRAM_POLL_HEARTBEAT_SECONDS,
    recreate_after_failures: int = TELEGRAM_POLL_RECREATE_FAILURES,
    now_fn: Callable[[], datetime] | None = None,
) -> list[dict[str, Any]]:
    if now_fn is None:
        now_fn = _polling_utc_now
    polling_state = state or TelegramPollingState()
    polling_state.latest_offset = offset
    watchdog = watchdog_timeout_seconds
    if watchdog is None:
        watchdog = timeout + TELEGRAM_POLL_WATCHDOG_GRACE_SECONDS
    while True:
        now = now_fn()
        await _maybe_emit_polling_heartbeat(
            polling_state,
            retry_delay_seconds=retry_delay_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            now=now,
            warn=warn,
        )
        try:
            updates = await asyncio.wait_for(
                bot.get_updates(offset=offset, timeout=timeout),
                timeout=watchdog,
            )
        except TelegramAPIError as exc:
            polling_state.consecutive_failures += 1
            client_recreated = await _maybe_recreate_polling_client(
                bot,
                polling_state,
                recreate_after_failures=recreate_after_failures,
            )
            await _emit_polling_warning(
                _polling_failure_message(
                    polling_state,
                    retry_delay_seconds=retry_delay_seconds,
                    client_recreated=client_recreated,
                    now=now_fn(),
                    error=str(exc),
                ),
                warn,
            )
            await sleep(retry_delay_seconds)
        except asyncio.TimeoutError:
            polling_state.consecutive_failures += 1
            client_recreated = await _maybe_recreate_polling_client(
                bot,
                polling_state,
                recreate_after_failures=recreate_after_failures,
            )
            await _emit_polling_warning(
                _polling_failure_message(
                    polling_state,
                    retry_delay_seconds=retry_delay_seconds,
                    client_recreated=client_recreated,
                    now=now_fn(),
                    error=f"getUpdates watchdog timed out after {watchdog:g}s",
                ),
                warn,
            )
            await sleep(retry_delay_seconds)
        else:
            previous_failures = polling_state.consecutive_failures
            client_recreated = polling_state.client_recreated_during_failure_streak
            polling_state.last_success_at = now_fn()
            polling_state.consecutive_failures = 0
            polling_state.client_recreated_during_failure_streak = False
            if previous_failures:
                await _emit_polling_warning(
                    _polling_recovery_message(
                        polling_state,
                        previous_failures=previous_failures,
                        retry_delay_seconds=retry_delay_seconds,
                        client_recreated=client_recreated,
                        now=polling_state.last_success_at,
                    ),
                    warn,
                )
            return updates


def _polling_utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _maybe_recreate_polling_client(
    bot: Any,
    state: TelegramPollingState,
    *,
    recreate_after_failures: int,
) -> str:
    if recreate_after_failures <= 0 or state.consecutive_failures % recreate_after_failures != 0:
        return "no"
    recreate_client = getattr(bot, "recreate_client", None)
    if recreate_client is None:
        return "no"
    try:
        result = recreate_client()
        if isawaitable(result):
            result = await result
    except Exception:
        return "failed"
    if bool(result):
        state.client_recreated_during_failure_streak = True
        return "yes"
    return "no"


async def _maybe_emit_polling_heartbeat(
    state: TelegramPollingState,
    *,
    retry_delay_seconds: float,
    heartbeat_interval_seconds: float,
    now: datetime,
    warn: Callable[[str], Any] | None,
) -> None:
    if state.last_heartbeat_at is None:
        if heartbeat_interval_seconds > 0:
            state.last_heartbeat_at = now
            return
    elif (
        heartbeat_interval_seconds > 0
        and (now - state.last_heartbeat_at).total_seconds() < heartbeat_interval_seconds
    ):
        return
    state.last_heartbeat_at = now
    await _emit_polling_warning(
        "Telegram polling heartbeat: "
        + _polling_diagnostic_fields(
            state,
            retry_delay_seconds=retry_delay_seconds,
            now=now,
        ),
        warn,
    )


def _polling_failure_message(
    state: TelegramPollingState,
    *,
    retry_delay_seconds: float,
    client_recreated: str,
    now: datetime,
    error: str,
) -> str:
    return (
        "Telegram polling failed: "
        + _polling_diagnostic_fields(
            state,
            retry_delay_seconds=retry_delay_seconds,
            now=now,
        )
        + f" client_recreated={client_recreated} error={error}"
    )


def _polling_recovery_message(
    state: TelegramPollingState,
    *,
    previous_failures: int,
    retry_delay_seconds: float,
    client_recreated: bool,
    now: datetime,
) -> str:
    return (
        f"Telegram polling recovered: previous_failures={previous_failures} "
        + _polling_diagnostic_fields(
            state,
            retry_delay_seconds=retry_delay_seconds,
            now=now,
        )
        + f" client_recreated={'yes' if client_recreated else 'no'}"
    )


def _polling_diagnostic_fields(
    state: TelegramPollingState,
    *,
    retry_delay_seconds: float,
    now: datetime,
) -> str:
    last_success = _format_polling_time(state.last_success_at)
    age = _format_polling_age(state.last_success_at, now)
    offset = state.latest_offset if state.latest_offset is not None else "none"
    return (
        f"failure_count={state.consecutive_failures} "
        f"consecutive_failures={state.consecutive_failures} "
        f"retry_delay={retry_delay_seconds:g}s "
        f"last_success_utc={last_success} "
        f"last_success_age={age} "
        f"latest_offset={offset}"
    )


def _format_polling_time(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_polling_age(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "unknown"
    age_seconds = max(0, int((now - value).total_seconds()))
    return f"{age_seconds}s"


async def _emit_polling_warning(message: str, warn: Callable[[str], Any] | None) -> None:
    if warn is None:
        print(f"Warning: {message}", file=sys.stderr)
        return
    result = warn(message)
    if isawaitable(result):
        await result


def telegram_status_summary(settings: TelegramSettings, store: TelegramStateStore) -> dict[str, Any]:
    return {
        "state_dir": str(settings.state_dir),
        "allowed_roots": [str(root) for root in settings.allowed_roots],
        "default_cwd": str(settings.default_cwd),
        "allowed_users": len(store.load_access().get("allowed_users", {})),
        "thread_mappings": len(store.load_threads()),
        "bot_token_configured": bool(settings.bot_token),
        "allowed_user_configured": bool(settings.allowed_user_id),
        "permission_profile": settings.permission_profile,
        "sandbox": settings.sandbox,
        "approval_policy": _approval_policy_value(settings.approval_policy),
    }


def initial_poll_offset(store: TelegramStateStore) -> int | None:
    update_ids = [
        int(entry.get("last_update_id"))
        for entry in store.load_chats().values()
        if isinstance(entry, dict) and isinstance(entry.get("last_update_id"), int)
    ]
    if not update_ids:
        return None
    return max(update_ids) + 1

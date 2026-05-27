from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import secrets
import sys
from datetime import timedelta
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
    _bot_chat_id,
    _command_disabled_during_active_turn,
    _thread_sandbox_value,
    _approval_policy_value,
)
from .constants import (
    _AUTH_HEADER_PATTERN,
    _SECRET_PATTERNS,
    TYPING_ACTION_INTERVAL_SECONDS,
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
    TELEGRAM_HELP_TEXT,
)
from .dynamic_tools import _dynamic_tools_fingerprint, telegram_dynamic_tools
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

_FILE_SEND_TOOLS: dict[str, dict[str, Any]] = {
    "telegram_send_photo": {"label": "Photo", "content_type_prefixes": ("image/",)},
    "telegram_send_video": {"label": "Video", "content_type_prefixes": ("video/",)},
    "telegram_send_document": {"label": "Document"},
    "telegram_send_animation": {"label": "Animation", "suffixes": {".gif", ".mp4"}},
    "telegram_send_audio": {"label": "Audio", "content_type_prefixes": ("audio/",)},
    "telegram_send_voice": {"label": "Voice", "content_type_prefixes": ("audio/",)},
    "telegram_send_video_note": {"label": "Video note", "content_type_prefixes": ("video/",)},
    "telegram_send_sticker": {"label": "Sticker", "suffixes": {".webp", ".tgs", ".webm"}},
}

_STRUCTURED_SEND_TOOLS = {
    "telegram_send_contact",
    "telegram_send_location",
    "telegram_send_venue",
    "telegram_send_poll",
    "telegram_send_checklist",
    "telegram_send_dice",
}
_CURRENT_MESSAGE_REUSE_TOOLS = {"telegram_copy_current_message", "telegram_forward_current_message"}
_OUTBOUND_SEND_TIMEOUT_TEXT = (
    "Telegram send request timed out after it was submitted. Delivery status is unknown, and Telegram may "
    "already have delivered it. Do not retry this same send automatically; ask the user before retrying."
)
_DUPLICATE_OUTBOUND_SEND_TEXT = (
    "Duplicate Telegram send suppressed because an earlier identical send timed out after submission. "
    "Delivery status is unknown, and Telegram may already have delivered it. Ask the user before retrying."
)


def _outbound_send_fingerprint(tool: str, arguments: dict[str, Any]) -> str | None:
    if (
        tool != "telegram_reply"
        and tool not in _FILE_SEND_TOOLS
        and tool != "telegram_send_live_photo"
        and tool != "telegram_send_media_group"
        and tool != "telegram_send_paid_media"
        and tool not in _STRUCTURED_SEND_TOOLS
        and tool not in _CURRENT_MESSAGE_REUSE_TOOLS
    ):
        return None
    return json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, default=str, separators=(",", ":"))


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _input_checklist(
    title: str | None,
    tasks: Any,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    if not title:
        return {}, "Checklist title is required."
    if not isinstance(tasks, list) or not tasks:
        return {}, "Checklist tasks must be a non-empty array."
    normalized_tasks: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        if isinstance(task, str):
            text = task
            task_id = index
        elif isinstance(task, dict):
            text = _first_text(task.get("text"))
            task_id = _int_or_none(task.get("id")) or index
        else:
            return {}, f"Checklist task {index} must be a string or object."
        if not text:
            return {}, f"Checklist task {index} text is required."
        normalized_tasks.append({"id": task_id, "text": text})
    checklist: dict[str, Any] = {"title": title, "tasks": normalized_tasks}
    for key in ("others_can_add_tasks", "others_can_mark_tasks_as_done"):
        value = arguments.get(key)
        if isinstance(value, bool):
            checklist[key] = value
    return checklist, None


class TelegramBridgeRequestMixin:
    async def _send_context_error_response(self, event: AppServerEvent) -> bool:
        if event.request_id is None:
            return True
        context = self._context_for_event(event)
        if context is not None:
            return False
        await self.app_server.send_error_response(
            event.request_id,
            f"No active Telegram turn context is available for {event.method}.",
        )
        return True


    async def _handle_approval_request(self, event: AppServerEvent) -> None:
        if await self._send_context_error_response(event):
            return
        context = self._context_for_event(event)
        if context is None or event.request_id is None:
            return
        self._stop_typing_indicator(context.turn_id)
        token = secrets.token_urlsafe(18)
        expires_at = self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds)
        pending = self.store.load_pending_approvals()
        pending[token] = {
            "thread_id": context.thread_id,
            "turn_id": context.turn_id,
            "request_id": event.request_id,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "kind": event.method,
            "expires_at": _format_iso(expires_at),
        }
        self.store.save_pending_approvals(pending)
        text = _approval_text(event.params, context.workspace)
        sent = await self._send(
            context.chat_id,
            text,
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Accept once", "callback_data": f"approval:{token}:accept"},
                        {"text": "Decline", "callback_data": f"approval:{token}:decline"},
                        {"text": "Cancel", "callback_data": f"approval:{token}:cancel"},
                    ]
                ]
            },
        )
        if sent:
            pending = self.store.load_pending_approvals()
            if token in pending:
                pending[token]["message_id"] = sent[0].get("message_id")
                self.store.save_pending_approvals(pending)


    async def _handle_permissions_approval_request(self, event: AppServerEvent) -> None:
        if await self._send_context_error_response(event):
            return
        context = self._context_for_event(event)
        if context is None or event.request_id is None:
            return
        self._stop_typing_indicator(context.turn_id)
        token = secrets.token_urlsafe(18)
        expires_at = self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds)
        permissions = event.params.get("permissions")
        pending = self.store.load_pending_approvals()
        pending[token] = {
            "thread_id": context.thread_id,
            "turn_id": context.turn_id,
            "request_id": event.request_id,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "kind": event.method,
            "permissions": permissions if isinstance(permissions, dict) else {},
            "expires_at": _format_iso(expires_at),
        }
        self.store.save_pending_approvals(pending)
        sent = await self._send(
            context.chat_id,
            _permissions_approval_text(event.params, context.workspace),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Accept once", "callback_data": f"approval:{token}:accept"},
                        {"text": "Decline", "callback_data": f"approval:{token}:decline"},
                        {"text": "Cancel", "callback_data": f"approval:{token}:cancel"},
                    ]
                ]
            },
        )
        if sent:
            pending = self.store.load_pending_approvals()
            if token in pending:
                pending[token]["message_id"] = sent[0].get("message_id")
                self.store.save_pending_approvals(pending)


    async def _handle_mcp_elicitation_request(self, event: AppServerEvent) -> None:
        if await self._send_context_error_response(event):
            return
        context = self._context_for_event(event)
        if context is None or event.request_id is None:
            return
        self._stop_typing_indicator(context.turn_id)
        token = secrets.token_urlsafe(18)
        expires_at = self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds)
        pending = self.store.load_pending_elicitations()
        pending[token] = {
            "thread_id": context.thread_id,
            "turn_id": context.turn_id,
            "request_id": event.request_id,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "server_name": str(event.params.get("serverName") or ""),
            "expires_at": _format_iso(expires_at),
        }
        self.store.save_pending_elicitations(pending)
        sent = await self._send(
            context.chat_id,
            _mcp_elicitation_text(event.params),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Accept", "callback_data": f"elicitation:{token}:accept"},
                        {"text": "Decline", "callback_data": f"elicitation:{token}:decline"},
                        {"text": "Cancel", "callback_data": f"elicitation:{token}:cancel"},
                    ]
                ]
            },
        )
        if sent:
            pending = self.store.load_pending_elicitations()
            if token in pending:
                pending[token]["message_id"] = sent[0].get("message_id")
                self.store.save_pending_elicitations(pending)


    async def _handle_tool_user_input_request(self, event: AppServerEvent) -> None:
        if await self._send_context_error_response(event):
            return
        context = self._context_for_event(event)
        if context is None or event.request_id is None:
            return
        questions = [item for item in event.params.get("questions") or [] if isinstance(item, dict)]
        if not questions:
            await self.app_server.send_error_response(event.request_id, "User input request did not include questions.")
            return
        if any(question.get("isSecret") for question in questions):
            self._stop_typing_indicator(context.turn_id)
            await self.app_server.send_error_response(
                event.request_id,
                "Secret user input is not supported over Telegram.",
            )
            return
        self._stop_typing_indicator(context.turn_id)
        token = secrets.token_urlsafe(18)
        expires_at = self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds)
        pending = self.store.load_pending_user_inputs()
        pending[token] = {
            "thread_id": context.thread_id,
            "turn_id": context.turn_id,
            "request_id": event.request_id,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "questions": questions,
            "answers": {},
            "question_index": 0,
            "waiting_for_text": not _question_options(questions[0]),
            "expires_at": _format_iso(expires_at),
        }
        self.store.save_pending_user_inputs(pending)
        await self._send_user_input_question(token, pending[token])


    async def _send_user_input_question(self, token: str, record: dict[str, Any]) -> None:
        question = _current_user_input_question(record)
        if question is None:
            return
        options = _question_options(question)
        text = _tool_user_input_text(question, waiting_for_text=not options)
        keyboard: list[list[dict[str, str]]] = []
        if options:
            row: list[dict[str, str]] = []
            for index, option in enumerate(options):
                row.append({"text": str(option["label"]), "callback_data": f"userinput:{token}:option:{index}"})
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([{"text": "Other", "callback_data": f"userinput:{token}:other"}])
        keyboard.append([{"text": "Cancel", "callback_data": f"userinput:{token}:cancel"}])
        sent = await self._send(
            str(record.get("chat_id") or ""),
            text,
            reply_markup={"inline_keyboard": keyboard},
        )
        if sent:
            pending = self.store.load_pending_user_inputs()
            current = pending.get(token)
            if isinstance(current, dict):
                current["message_id"] = sent[0].get("message_id")
                self.store.save_pending_user_inputs(pending)


    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        data = str(callback.get("data") or "")
        if data.startswith("select:"):
            await self._handle_selection_callback(callback, data)
            return
        if data.startswith("userinput:"):
            await self._handle_user_input_callback(callback, data)
            return
        if data.startswith("elicitation:"):
            await self._handle_mcp_elicitation_callback(callback, data)
            return
        if data.startswith("approval:"):
            await self._handle_approval_callback(callback, data)
            return
        if data.startswith("resume:"):
            await self._handle_resume_callback(callback, data)
            return
        await self.bot.answer_callback_query(str(callback.get("id") or ""), text="Unsupported callback.")


    async def _handle_approval_callback(self, callback: dict[str, Any], data: str) -> None:
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, token, action = parts
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        user_id = str((callback.get("from") or {}).get("id"))
        callback_id = str(callback.get("id") or "")
        pending_all = self.store.load_pending_approvals()
        pending = pending_all.get(token)
        if not isinstance(pending, dict):
            await self.bot.answer_callback_query(callback_id, text="Approval expired.")
            return
        if not self.access.can_answer_callback(chat_id, user_id, token):
            if str(pending.get("chat_id")) == chat_id and str(pending.get("user_id")) == user_id:
                await self.app_server.send_error_response(pending["request_id"], "Approval expired.")
                pending_all.pop(token, None)
                self.store.save_pending_approvals(pending_all)
                await self._edit_callback_message(callback, "Approval expired.")
                await self.bot.answer_callback_query(callback_id, text="Approval expired.")
            else:
                await self.bot.answer_callback_query(callback_id, text="You are not allowed to answer this approval.")
            return
        if action not in {"accept", "decline", "cancel"}:
            await self.bot.answer_callback_query(callback_id, text="Unsupported approval action.")
            return
        if pending.get("kind") == "item/permissions/requestApproval":
            if action == "accept":
                permissions = pending.get("permissions")
                await self.app_server.send_permissions_approval_response(
                    pending["request_id"],
                    permissions if isinstance(permissions, dict) else {},
                )
                self._resume_typing_for_pending_record(pending)
            else:
                await self.app_server.send_error_response(
                    pending["request_id"],
                    f"Permission approval {_action_past_tense(action)}.",
                )
        else:
            await self.app_server.send_approval_decision(pending["request_id"], action)
            if action == "accept":
                self._resume_typing_for_pending_record(pending)
        pending_all.pop(token, None)
        self.store.save_pending_approvals(pending_all)
        message = f"Approval {_action_past_tense(action)}."
        await self._edit_callback_message(callback, message)
        await self.bot.answer_callback_query(callback_id, text=message)


    async def _handle_mcp_elicitation_callback(self, callback: dict[str, Any], data: str) -> None:
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, token, action = parts
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        user_id = str((callback.get("from") or {}).get("id"))
        callback_id = str(callback.get("id") or "")
        pending_all = self.store.load_pending_elicitations()
        pending = pending_all.get(token)
        if not isinstance(pending, dict):
            await self.bot.answer_callback_query(callback_id, text="Elicitation expired.")
            return
        if str(pending.get("chat_id")) != chat_id or str(pending.get("user_id")) != user_id:
            await self.bot.answer_callback_query(
                callback_id,
                text="You are not allowed to answer this elicitation.",
            )
            return
        expires_at = _parse_iso(str(pending.get("expires_at") or ""))
        if expires_at <= self.access.now_fn():
            await self.app_server.send_error_response(pending["request_id"], "Elicitation expired.")
            pending_all.pop(token, None)
            self.store.save_pending_elicitations(pending_all)
            await self._edit_callback_message(callback, "Elicitation expired.")
            await self.bot.answer_callback_query(callback_id, text="Elicitation expired.")
            return
        if action not in {"accept", "decline", "cancel"}:
            await self.bot.answer_callback_query(callback_id, text="Unsupported elicitation action.")
            return
        await self.app_server.send_mcp_elicitation_response(pending["request_id"], action)
        if action == "accept":
            self._resume_typing_for_pending_record(pending)
        pending_all.pop(token, None)
        self.store.save_pending_elicitations(pending_all)
        message = f"Elicitation {_action_past_tense(action)}."
        await self._edit_callback_message(callback, message)
        await self.bot.answer_callback_query(callback_id, text=message)


    async def _handle_user_input_callback(self, callback: dict[str, Any], data: str) -> None:
        parts = data.split(":")
        if len(parts) < 3:
            return
        _, token, action = parts[:3]
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        user_id = str((callback.get("from") or {}).get("id"))
        callback_id = str(callback.get("id") or "")
        pending_all = self.store.load_pending_user_inputs()
        pending = pending_all.get(token)
        if not isinstance(pending, dict):
            await self.bot.answer_callback_query(callback_id, text="User input expired.")
            return
        if str(pending.get("chat_id")) != chat_id or str(pending.get("user_id")) != user_id:
            await self.bot.answer_callback_query(callback_id, text="You are not allowed to answer this prompt.")
            return
        expires_at = _parse_iso(str(pending.get("expires_at") or ""))
        if expires_at <= self.access.now_fn():
            await self.app_server.send_error_response(pending["request_id"], "User input expired.")
            pending_all.pop(token, None)
            self.store.save_pending_user_inputs(pending_all)
            await self._edit_callback_message(callback, "User input expired.")
            await self.bot.answer_callback_query(callback_id, text="User input expired.")
            return
        if action == "cancel":
            await self.app_server.send_error_response(pending["request_id"], "User input cancelled.")
            pending_all.pop(token, None)
            self.store.save_pending_user_inputs(pending_all)
            await self._edit_callback_message(callback, "User input cancelled.")
            await self.bot.answer_callback_query(callback_id, text="User input cancelled.")
            return
        if action == "other":
            pending["waiting_for_text"] = True
            pending_all[token] = pending
            self.store.save_pending_user_inputs(pending_all)
            question = _current_user_input_question(pending)
            await self._edit_callback_message(
                callback,
                _tool_user_input_text(question or {}, waiting_for_text=True),
            )
            await self.bot.answer_callback_query(callback_id, text="Send your answer as a message.")
            return
        if action == "option":
            try:
                index = int(parts[3])
            except (IndexError, ValueError):
                await self.bot.answer_callback_query(callback_id, text="Unsupported prompt option.")
                return
            question = _current_user_input_question(pending)
            options = _question_options(question or {})
            if index < 0 or index >= len(options):
                await self.bot.answer_callback_query(callback_id, text="Unsupported prompt option.")
                return
            await self._finish_user_input_answer(token, pending, str(options[index].get("label") or ""), callback)
            await self.bot.answer_callback_query(callback_id, text="Answer sent.")
            return
        await self.bot.answer_callback_query(callback_id, text="Unsupported prompt action.")


    async def _handle_pending_user_input_message(self, chat_id: str, user_id: str, text: str) -> bool:
        if not text:
            return False
        pending_all = self.store.load_pending_user_inputs()
        for token, pending in list(pending_all.items()):
            if not isinstance(pending, dict):
                continue
            if str(pending.get("chat_id")) != str(chat_id) or str(pending.get("user_id")) != str(user_id):
                continue
            if not pending.get("waiting_for_text"):
                continue
            expires_at = _parse_iso(str(pending.get("expires_at") or ""))
            if expires_at <= self.access.now_fn():
                await self.app_server.send_error_response(pending["request_id"], "User input expired.")
                pending_all.pop(token, None)
                self.store.save_pending_user_inputs(pending_all)
                await self._send(chat_id, "User input expired.")
                return True
            await self._finish_user_input_answer(token, pending, text)
            return True
        return False


    async def _finish_user_input_answer(
        self,
        token: str,
        record: dict[str, Any],
        answer: str,
        callback: dict[str, Any] | None = None,
    ) -> None:
        question = _current_user_input_question(record)
        if question is None:
            return
        question_id = str(question.get("id") or "")
        answers = dict(record.get("answers") or {})
        answers[question_id] = {"answers": [answer]}
        record["answers"] = answers
        record["waiting_for_text"] = False
        record["question_index"] = int(record.get("question_index") or 0) + 1
        pending_all = self.store.load_pending_user_inputs()
        questions = [item for item in record.get("questions") or [] if isinstance(item, dict)]
        if int(record.get("question_index") or 0) < len(questions):
            next_question = questions[int(record["question_index"])]
            record["waiting_for_text"] = not _question_options(next_question)
            pending_all[token] = record
            self.store.save_pending_user_inputs(pending_all)
            await self._send_user_input_question(token, record)
            return
        pending_all.pop(token, None)
        self.store.save_pending_user_inputs(pending_all)
        response_answers = {
            str(key): list(value.get("answers") or [])
            for key, value in answers.items()
            if isinstance(value, dict)
        }
        await self.app_server.send_tool_user_input_response(record["request_id"], response_answers)
        if callback is not None:
            await self._edit_callback_message(callback, "Answer sent.")
        else:
            await self._send(str(record.get("chat_id") or ""), "Answer sent.")
        self._resume_typing_for_pending_record(record)


    async def _handle_selection_callback(self, callback: dict[str, Any], data: str) -> None:
        _, _, token = data.partition(":")
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        user_id = str((callback.get("from") or {}).get("id"))
        callback_id = str(callback.get("id") or "")
        pending_all = self.store.load_pending_selections()
        pending = pending_all.get(token)
        if not isinstance(pending, dict):
            await self.bot.answer_callback_query(callback_id, text="Selection expired.")
            return
        if str(pending.get("chat_id")) != chat_id or str(pending.get("user_id")) != user_id:
            await self.bot.answer_callback_query(callback_id, text="You are not allowed to use this selection.")
            return
        expires_at = _parse_iso(str(pending.get("expires_at") or ""))
        if expires_at <= self.access.now_fn():
            self._remove_pending_selection_group(pending_all, token, pending)
            self.store.save_pending_selections(pending_all)
            await self._edit_callback_message(callback, "Selection expired.")
            await self.bot.answer_callback_query(callback_id, text="Selection expired.")
            return
        action = str(pending.get("action") or "")
        if action == "cancel":
            self._remove_pending_selection_group(pending_all, token, pending)
            self.store.save_pending_selections(pending_all)
            await self._edit_callback_message(callback, "Selection cancelled.")
            await self.bot.answer_callback_query(callback_id, text="Selection cancelled.")
            return
        workspace = self._active_workspace(chat_id)
        value = pending.get("value")
        self._remove_pending_selection_group(pending_all, token, pending)
        self.store.save_pending_selections(pending_all)
        try:
            message = await self._apply_selection(chat_id, user_id, workspace, action, value)
        except JsonRpcError as exc:
            message = f"App-server command failed: {exc}"
        await self._edit_callback_message(callback, message)
        await self.bot.answer_callback_query(callback_id, text="Selection applied.")


    async def _apply_selection(self, chat_id: str, user_id: str, workspace: Path, action: str, value: Any) -> str:
        if action == "model" and isinstance(value, dict):
            model_name = _model_config_value(value)
            if not model_name:
                return "Selection is no longer available."
            if await self._send_model_effort_selection(chat_id, user_id, workspace, model_name, value):
                return f"Model selected: {model_name}. Select reasoning level below."
            return await self._apply_model_setting(chat_id, workspace, model_name)
        if action == "model_effort" and isinstance(value, dict):
            model_name = _first_text(value.get("model"))
            effort = _reasoning_effort_value(_first_text(value.get("effort")))
            if model_name and effort:
                return await self._apply_model_effort_setting(chat_id, workspace, model_name, effort)
        if action == "permission":
            permission = str(value) if value is not None else None
            return await self._apply_permission_setting(chat_id, workspace, permission)
        if action == "approval" and isinstance(value, str):
            return await self._apply_approval_policy_setting(chat_id, workspace, value)
        if action == "mode" and isinstance(value, str):
            return await self._apply_mode_setting(chat_id, workspace, value)
        if action == "effort" and isinstance(value, str):
            return await self._apply_effort_setting(chat_id, workspace, value)
        if action == "personality" and isinstance(value, str):
            return await self._apply_personality_setting(chat_id, workspace, value)
        if action == "memory" and isinstance(value, str):
            return await self._apply_memory_mode_setting(chat_id, workspace, value)
        if action in {"plan_implement", "plan_fresh", "plan_stay"} and isinstance(value, dict):
            return await self._apply_plan_choice(chat_id, user_id, workspace, action, value)
        if action == "experimental" and isinstance(value, dict):
            name = str(value.get("name") or "")
            enabled = bool(value.get("enabled"))
            if not name:
                return "Feature is no longer available."
            await self.app_server.experimental_feature_enablement_set(enablement={name: enabled})
            return f"Experimental feature {'enabled' if enabled else 'disabled'}: {name}"
        if action == "skill" and isinstance(value, dict):
            name = str(value.get("name") or "")
            path = _first_text(value.get("path"))
            enabled = bool(value.get("enabled"))
            if not name and not path:
                return "Skill is no longer available."
            await self.app_server.skills_config_write(
                enabled=enabled,
                name=None if path else name or None,
                path=path,
            )
            return f"Skill {'enabled' if enabled else 'disabled'}: {name or path}"
        if action == "stop" and isinstance(value, str):
            await self.app_server.thread_background_terminals_clean(thread_id=value)
            return "Background terminals stopped."
        if action == "thread_select" and isinstance(value, dict):
            thread_id = str(value.get("thread_id") or "")
            cwd = Path(str(value.get("cwd") or workspace)).expanduser().resolve(strict=False)
            if not thread_id:
                return "Thread is no longer available."
            if not is_path_within_any_root(cwd, self.settings.allowed_roots):
                cwd = workspace
            self._set_active_workspace(chat_id, cwd)
            self._save_thread_record(chat_id, cwd, thread_id, auto_name_pending=False)
            return f"Selected thread: {thread_id}"
        if action == "guardian_approve" and isinstance(value, dict):
            thread_id = str(value.get("thread_id") or "")
            event = value.get("event")
            if not thread_id or event is None:
                return "Denied action is no longer available."
            await self.app_server.thread_approve_guardian_denied_action(thread_id=thread_id, event=event)
            return "Denied action approved."
        return "Selection is no longer available."


    def _remove_pending_selection_group(
        self,
        pending: dict[str, Any],
        token: str,
        record: dict[str, Any],
    ) -> None:
        group_id = record.get("group_id")
        if group_id:
            for key, value in list(pending.items()):
                if isinstance(value, dict) and value.get("group_id") == group_id:
                    pending.pop(key, None)
            return
        pending.pop(token, None)


    async def _handle_resume_callback(self, callback: dict[str, Any], data: str) -> None:
        _, _, thread_id = data.partition(":")
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        user_id = str((callback.get("from") or {}).get("id"))
        callback_id = str(callback.get("id") or "")
        if not self.access.is_user_allowed(user_id):
            await self.bot.answer_callback_query(callback_id, text="You are not allowed to select this thread.")
            return
        workspace = self._active_workspace(chat_id)
        title = await self._thread_title_for_id(chat_id, thread_id)
        self._save_thread_record(chat_id, workspace, thread_id, auto_name_pending=False, title=title)
        await self._edit_callback_message(callback, f"Resumed thread: {thread_id}")
        await self.bot.answer_callback_query(callback_id, text="Thread selected.")


    async def _handle_tool_call(self, event: AppServerEvent) -> None:
        context = self._context_for_event(event)
        if event.request_id is None:
            return
        if context is None:
            await self.app_server.send_error_response(
                event.request_id,
                f"No active Telegram turn context is available for {event.method}.",
            )
            return
        tool = _tool_name(str(event.params.get("tool") or event.params.get("name") or ""))
        arguments = _tool_arguments(event.params.get("arguments"))
        send_fingerprint = _outbound_send_fingerprint(tool, arguments)
        if send_fingerprint and send_fingerprint in context.ambiguous_tool_sends:
            context.tool_replied = True
            await self._send_tool_text(event.request_id, _DUPLICATE_OUTBOUND_SEND_TEXT)
            return
        try:
            handled = await self._handle_telegram_tool(event.request_id, context, tool, arguments)
        except TelegramAPIError as exc:
            if send_fingerprint and exc.ambiguous_delivery:
                context.ambiguous_tool_sends.add(send_fingerprint)
                context.tool_replied = True
                await self._send_tool_text(event.request_id, _OUTBOUND_SEND_TIMEOUT_TEXT)
                return
            await self._send_tool_text(event.request_id, f"Telegram API error: {exc}")
            return
        except Exception as exc:
            LOGGER.exception("Telegram dynamic tool failed")
            await self._send_tool_text(event.request_id, f"Telegram tool failed: {exc}")
            return
        if not handled:
            await self._send_tool_text(event.request_id, f"Unsupported Telegram tool: {tool}")


    async def _handle_telegram_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        tool: str,
        arguments: dict[str, Any],
    ) -> bool:
        if tool == "telegram_reply":
            text = str(arguments.get("text") or "")
            sent = await self._send(
                context.chat_id,
                text,
                parse_mode=arguments.get("parse_mode"),
                reply_to_message_id=arguments.get("reply_to_message_id"),
            )
            context.tool_replied = True
            self._track_sent_message(context.chat_id, sent)
            await self._send_tool_text(request_id, self._sent_result_text(sent))
            return True
        if tool == "telegram_react":
            message_id = int(arguments.get("message_id") or context.message_id or 0)
            emoji = str(arguments.get("emoji") or "")
            try:
                await self.bot.set_message_reaction(_bot_chat_id(context.chat_id), message_id, emoji)
                text = "reacted"
            except Exception as exc:  # pragma: no cover - concrete Bot API support varies
                text = f"reaction unavailable: {exc}"
            await self._send_tool_text(request_id, text)
            return True
        if tool == "telegram_edit_message":
            message_id = int(arguments.get("message_id") or 0)
            if (context.chat_id, message_id) not in self.bridge_messages:
                await self._send_tool_text(request_id, "Message is not bridge-owned and cannot be edited.")
                return True
            await self.bot.edit_message_text(
                _bot_chat_id(context.chat_id),
                message_id,
                str(arguments.get("text") or ""),
                parse_mode=arguments.get("parse_mode"),
            )
            await self._send_tool_text(request_id, "edited")
            return True
        if tool in _FILE_SEND_TOOLS:
            await self._handle_file_send_tool(request_id, context, tool, arguments)
            return True
        if tool == "telegram_send_live_photo":
            await self._handle_live_photo_tool(request_id, context, arguments)
            return True
        if tool == "telegram_send_media_group":
            await self._handle_media_group_tool(request_id, context, arguments)
            return True
        if tool == "telegram_send_paid_media":
            await self._handle_paid_media_tool(request_id, context, arguments)
            return True
        if tool in _STRUCTURED_SEND_TOOLS:
            await self._handle_structured_send_tool(request_id, context, tool, arguments)
            return True
        if tool in _CURRENT_MESSAGE_REUSE_TOOLS:
            await self._handle_current_message_reuse_tool(request_id, context, tool, arguments)
            return True
        if tool == "telegram_download_attachment":
            file_id = str(arguments.get("file_id") or "")
            attachment = context.attachments.get(file_id)
            if not attachment:
                await self._send_tool_text(request_id, "Attachment is not available in the current turn.")
                return True
            await self._send_tool_text(request_id, json.dumps(attachment, sort_keys=True))
            return True
        return False

    async def _send_tool_text(self, request_id: int | str, text: str) -> None:
        await self.app_server.send_dynamic_tool_result(request_id, [{"type": "text", "text": text}])


    def _sent_result_text(self, sent: Any) -> str:
        if isinstance(sent, list):
            ids = [str(item.get("message_id")) for item in sent if isinstance(item, dict) and item.get("message_id")]
            return "sent" + (f" message_ids={','.join(ids)}" if ids else "")
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        result = "sent" + (f" message_id={message_id}" if message_id is not None else "")
        details = self._sent_result_details(sent)
        return result + (f" {details}" if details else "")


    def _sent_result_details(self, sent: Any) -> str:
        if not isinstance(sent, dict):
            return ""
        dice = sent.get("dice")
        if isinstance(dice, dict):
            parts = []
            emoji = _first_text(dice.get("emoji"))
            if emoji:
                parts.append(f"dice_emoji={emoji}")
            if dice.get("value") is not None:
                parts.append(f"dice_value={dice['value']}")
            return " ".join(parts)
        poll = sent.get("poll")
        if isinstance(poll, dict):
            poll_id = _first_text(poll.get("id"))
            if poll_id:
                return f"poll_id={poll_id}"
        return ""


    async def _handle_file_send_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        spec = _FILE_SEND_TOOLS[tool]
        label = str(spec["label"])
        path, error = self._tool_upload_path(arguments, context, label=label)
        if error:
            await self._send_tool_text(request_id, error)
            return
        assert path is not None
        filename = _first_text(arguments.get("filename")) or path.name
        content_type = (
            _first_text(arguments.get("content_type"))
            or mimetypes.guess_type(filename)[0]
            or mimetypes.guess_type(path.name)[0]
        )
        allowed_prefixes = spec.get("content_type_prefixes")
        if allowed_prefixes and (not content_type or not str(content_type).startswith(tuple(allowed_prefixes))):
            await self._send_tool_text(request_id, f"{label} type is unsupported; use telegram_send_document.")
            return
        allowed_suffixes = spec.get("suffixes")
        if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
            await self._send_tool_text(request_id, f"{label} format is unsupported; use telegram_send_document.")
            return
        data = path.read_bytes()
        caption = _first_text(arguments.get("caption"))
        if tool == "telegram_send_photo":
            sent = await self._send_photo_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
            )
        elif tool == "telegram_send_video":
            sent = await self._send_video_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
                duration=_int_or_none(arguments.get("duration")),
                width=_int_or_none(arguments.get("width")),
                height=_int_or_none(arguments.get("height")),
            )
        elif tool == "telegram_send_document":
            sent = await self._send_document_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
            )
        elif tool == "telegram_send_animation":
            sent = await self._send_animation_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
                duration=_int_or_none(arguments.get("duration")),
                width=_int_or_none(arguments.get("width")),
                height=_int_or_none(arguments.get("height")),
            )
        elif tool == "telegram_send_audio":
            sent = await self._send_audio_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
                duration=_int_or_none(arguments.get("duration")),
                performer=_first_text(arguments.get("performer")),
                title=_first_text(arguments.get("title")),
            )
        elif tool == "telegram_send_voice":
            sent = await self._send_voice_bytes(
                context.chat_id,
                data,
                filename=filename,
                caption=caption,
                content_type=content_type,
                duration=_int_or_none(arguments.get("duration")),
            )
        elif tool == "telegram_send_video_note":
            sent = await self._send_video_note_bytes(
                context.chat_id,
                data,
                filename=filename,
                content_type=content_type,
                duration=_int_or_none(arguments.get("duration")),
                length=_int_or_none(arguments.get("length")),
            )
        else:
            sent = await self._send_sticker_bytes(
                context.chat_id,
                data,
                filename=filename,
                content_type=content_type,
                emoji=_first_text(arguments.get("emoji")),
            )
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    async def _handle_live_photo_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        arguments: dict[str, Any],
    ) -> None:
        live_path, error = self._tool_upload_path(arguments, context, label="Live photo", key="live_photo_path")
        if error:
            await self._send_tool_text(request_id, error)
            return
        photo_path, error = self._tool_upload_path(arguments, context, label="Live photo still image", key="photo_path")
        if error:
            await self._send_tool_text(request_id, error)
            return
        assert live_path is not None and photo_path is not None
        sent = await self._send_live_photo_bytes(
            context.chat_id,
            live_path.read_bytes(),
            photo_path.read_bytes(),
            live_photo_filename=_first_text(arguments.get("live_photo_filename")) or live_path.name,
            photo_filename=_first_text(arguments.get("photo_filename")) or photo_path.name,
            caption=_first_text(arguments.get("caption")),
            live_photo_content_type=(
                _first_text(arguments.get("live_photo_content_type"))
                or mimetypes.guess_type(live_path.name)[0]
            ),
            photo_content_type=(
                _first_text(arguments.get("photo_content_type"))
                or mimetypes.guess_type(photo_path.name)[0]
            ),
        )
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    async def _handle_media_group_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        arguments: dict[str, Any],
    ) -> None:
        media, files, error = self._media_group_payload(context, arguments.get("media"), paid=False)
        if error:
            await self._send_tool_text(request_id, error)
            return
        sent = await self.bot.send_media_group(_bot_chat_id(context.chat_id), media, files=files)
        self._track_sent_message(context.chat_id, sent)
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    async def _handle_paid_media_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        arguments: dict[str, Any],
    ) -> None:
        star_count = _int_or_none(arguments.get("star_count"))
        if star_count is None or star_count <= 0:
            await self._send_tool_text(request_id, "Paid media star_count must be a positive integer.")
            return
        media, files, error = self._media_group_payload(context, arguments.get("media"), paid=True)
        if error:
            await self._send_tool_text(request_id, error)
            return
        sent = await self.bot.send_paid_media(
            _bot_chat_id(context.chat_id),
            star_count,
            media,
            caption=_first_text(arguments.get("caption")),
            payload=_first_text(arguments.get("payload")),
            files=files,
        )
        self._track_sent_message(context.chat_id, sent)
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    async def _handle_structured_send_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        chat_id = _bot_chat_id(context.chat_id)
        if tool == "telegram_send_contact":
            phone = _first_text(arguments.get("phone_number"))
            first_name = _first_text(arguments.get("first_name"))
            if not phone or not first_name:
                await self._send_tool_text(request_id, "Contact phone_number and first_name are required.")
                return
            sent = await self.bot.send_contact(
                chat_id,
                phone,
                first_name,
                last_name=_first_text(arguments.get("last_name")),
                vcard=_first_text(arguments.get("vcard")),
            )
        elif tool == "telegram_send_location":
            latitude = _float_or_none(arguments.get("latitude"))
            longitude = _float_or_none(arguments.get("longitude"))
            if latitude is None or longitude is None:
                await self._send_tool_text(request_id, "Location latitude and longitude are required.")
                return
            sent = await self.bot.send_location(
                chat_id,
                latitude,
                longitude,
                horizontal_accuracy=_float_or_none(arguments.get("horizontal_accuracy")),
                live_period=_int_or_none(arguments.get("live_period")),
                heading=_int_or_none(arguments.get("heading")),
                proximity_alert_radius=_int_or_none(arguments.get("proximity_alert_radius")),
            )
        elif tool == "telegram_send_venue":
            latitude = _float_or_none(arguments.get("latitude"))
            longitude = _float_or_none(arguments.get("longitude"))
            title = _first_text(arguments.get("title"))
            address = _first_text(arguments.get("address"))
            if latitude is None or longitude is None or not title or not address:
                await self._send_tool_text(request_id, "Venue latitude, longitude, title, and address are required.")
                return
            sent = await self.bot.send_venue(
                chat_id,
                latitude,
                longitude,
                title,
                address,
                foursquare_id=_first_text(arguments.get("foursquare_id")),
                foursquare_type=_first_text(arguments.get("foursquare_type")),
                google_place_id=_first_text(arguments.get("google_place_id")),
                google_place_type=_first_text(arguments.get("google_place_type")),
            )
        elif tool == "telegram_send_poll":
            question = _first_text(arguments.get("question"))
            options = arguments.get("options")
            if not question or not isinstance(options, list) or len(options) < 2:
                await self._send_tool_text(request_id, "Poll question and at least two options are required.")
                return
            sent = await self.bot.send_poll(
                chat_id,
                question,
                options,
                is_anonymous=arguments.get("is_anonymous"),
                type=_first_text(arguments.get("type")),
                allows_multiple_answers=arguments.get("allows_multiple_answers"),
                correct_option_id=_int_or_none(arguments.get("correct_option_id")),
                explanation=_first_text(arguments.get("explanation")),
                open_period=_int_or_none(arguments.get("open_period")),
                close_date=_int_or_none(arguments.get("close_date")),
                is_closed=arguments.get("is_closed"),
            )
        elif tool == "telegram_send_checklist":
            business_connection_id = _first_text(arguments.get("business_connection_id"))
            title = _first_text(arguments.get("title"))
            tasks = arguments.get("tasks")
            if not business_connection_id:
                await self._send_tool_text(
                    request_id,
                    "Checklist sends require business_connection_id for a connected Telegram business account.",
                )
                return
            checklist, error = _input_checklist(title, tasks, arguments)
            if error:
                await self._send_tool_text(request_id, error)
                return
            sent = await self.bot.send_checklist(chat_id, business_connection_id, checklist)
        else:
            sent = await self.bot.send_dice(chat_id, emoji=_first_text(arguments.get("emoji")))
        self._track_sent_message(context.chat_id, sent)
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    async def _handle_current_message_reuse_tool(
        self,
        request_id: int | str,
        context: TurnContext,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        if context.message_id is None:
            await self._send_tool_text(request_id, "No current inbound Telegram message is available.")
            return
        if tool == "telegram_copy_current_message":
            sent = await self.bot.copy_message(
                _bot_chat_id(context.chat_id),
                _bot_chat_id(context.chat_id),
                context.message_id,
                caption=_first_text(arguments.get("caption")),
                parse_mode=_first_text(arguments.get("parse_mode")),
            )
        else:
            sent = await self.bot.forward_message(
                _bot_chat_id(context.chat_id),
                _bot_chat_id(context.chat_id),
                context.message_id,
            )
        self._track_sent_message(context.chat_id, sent)
        context.tool_replied = True
        await self._send_tool_text(request_id, self._sent_result_text(sent))


    def _tool_upload_path(
        self,
        arguments: dict[str, Any],
        context: TurnContext,
        *,
        label: str,
        key: str = "path",
    ) -> tuple[Path | None, str | None]:
        raw_path = _first_text(arguments.get(key))
        if raw_path is None:
            return None, f"{label} path is required."
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = context.workspace / path
        path = path.resolve(strict=False)
        if not self._tool_upload_path_allowed(path, context):
            return None, f"{label} path is outside the active workspace and current-turn attachments."
        if not path.is_file():
            return None, f"{label} was not found: {path}"
        if path.stat().st_size > self.settings.max_attachment_bytes:
            return None, f"{label} is too large for this bridge."
        return path, None


    def _tool_upload_path_allowed(self, path: Path, context: TurnContext) -> bool:
        if is_path_within_any_root(path, (context.workspace,)):
            return True
        for attachment in context.attachments.values():
            attachment_path = _first_text(attachment.get("path")) if isinstance(attachment, dict) else None
            if not attachment_path:
                continue
            if Path(attachment_path).expanduser().resolve(strict=False) == path:
                return True
        return False


    def _media_group_payload(
        self,
        context: TurnContext,
        raw_media: Any,
        *,
        paid: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, bytes, str | None]], str | None]:
        if not isinstance(raw_media, list):
            return [], {}, "Media must be an array."
        if not raw_media:
            return [], {}, "Media must include at least one item."
        if not paid and len(raw_media) < 2:
            return [], {}, "Media group must include at least two items."
        files: dict[str, tuple[str, bytes, str | None]] = {}
        media: list[dict[str, Any]] = []
        allowed_types = {"photo", "video", "live_photo"} if paid else {"photo", "video", "document", "audio", "live_photo"}
        for index, item in enumerate(raw_media):
            if not isinstance(item, dict):
                return [], {}, f"Media item {index + 1} must be an object."
            media_type = _first_text(item.get("type")) or ""
            if media_type not in allowed_types:
                return [], {}, f"Media item {index + 1} type is unsupported: {media_type or 'missing'}"
            path_key = "live_photo_path" if media_type == "live_photo" and item.get("live_photo_path") else "path"
            path, error = self._tool_upload_path(item, context, label=f"Media item {index + 1}", key=path_key)
            if error:
                return [], {}, error
            assert path is not None
            attach_name = f"media{index}"
            filename = _first_text(item.get("filename")) or path.name
            content_type = (
                _first_text(item.get("content_type"))
                or mimetypes.guess_type(filename)[0]
                or mimetypes.guess_type(path.name)[0]
            )
            files[attach_name] = (_safe_filename(filename), path.read_bytes(), content_type)
            media_item = {"type": media_type, "media": f"attach://{attach_name}"}
            if media_type == "live_photo":
                photo_path, photo_error = self._tool_upload_path(
                    item,
                    context,
                    label=f"Media item {index + 1} photo",
                    key="photo_path",
                )
                if photo_error:
                    return [], {}, photo_error
                assert photo_path is not None
                photo_attach_name = f"photo{index}"
                photo_filename = _first_text(item.get("photo_filename")) or photo_path.name
                photo_content_type = (
                    _first_text(item.get("photo_content_type"))
                    or mimetypes.guess_type(photo_filename)[0]
                    or mimetypes.guess_type(photo_path.name)[0]
                )
                files[photo_attach_name] = (_safe_filename(photo_filename), photo_path.read_bytes(), photo_content_type)
                media_item["photo"] = f"attach://{photo_attach_name}"
            for key in (
                "caption",
                "parse_mode",
                "duration",
                "width",
                "height",
                "performer",
                "title",
                "supports_streaming",
                "has_spoiler",
                "show_caption_above_media",
            ):
                value = item.get(key)
                if value is not None:
                    media_item[key] = value
            media.append(media_item)
        return media, files, None

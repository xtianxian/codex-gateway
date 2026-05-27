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
    USER_NOTICE_RATE_LIMIT_SECONDS,
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


class TelegramBridgeIOMixin:
    def _record_turn_progress(
        self,
        context: TurnContext,
        kind: str,
        *,
        waiting_on_user: bool | None = None,
        waiting_prompt_type: str | None = None,
        background_activity: bool = False,
        terminal: bool = False,
        interrupted: bool = False,
    ) -> None:
        now = self.access.now_fn()
        context.last_event_at = now
        context.last_progress_at = now
        context.last_progress_kind = kind
        if waiting_on_user is not None:
            context.waiting_on_user = waiting_on_user
            context.waiting_prompt_type = waiting_prompt_type if waiting_on_user else None
        if background_activity:
            context.background_activity_seen = True
        if terminal:
            context.terminal_seen = True
            context.completed_at = now
        if interrupted:
            context.interrupted_at = now


    def _context_for_event(self, event: AppServerEvent) -> TurnContext | None:
        turn_id = _turn_id(event.params)
        if turn_id and turn_id in self.turns:
            return self.turns[turn_id]
        thread_id = _thread_id(event.params)
        latest = self.latest_turn_by_thread.get(thread_id)
        if latest:
            return self.turns.get(latest)
        return None


    async def _input_items(
        self,
        text: str,
        attachments: list[dict[str, Any]],
        workspace: Path,
    ) -> list[dict[str, Any]]:
        items = _input_items(text, attachments)
        for skill_name in _skill_names(text):
            skill_item = await self._skill_input_item(skill_name, workspace)
            if skill_item is not None:
                items.append(skill_item)
        return items


    async def _skill_input_item(self, skill_name: str, workspace: Path) -> dict[str, Any] | None:
        if not hasattr(self.app_server, "skills_list"):
            return None
        try:
            result = await self.app_server.skills_list(cwds=[str(workspace)], force_reload=False)
        except Exception:
            return None
        for group in _iter_skill_groups(result):
            for skill in group.get("skills", []):
                if not isinstance(skill, dict):
                    continue
                if str(skill.get("name") or "") != skill_name:
                    continue
                path = _skill_path(skill)
                if path:
                    return {"type": "skill", "name": skill_name, "path": path}
        return None


    async def _send(self, chat_id: str | int, text: str, **kwargs: Any) -> list[dict[str, Any]]:
        sent = await self.bot.send_message(_bot_chat_id(chat_id), sanitize_text(text), **kwargs)
        for message in sent:
            message_id = message.get("message_id")
            if message_id is not None:
                self.track_bridge_message(chat_id, int(message_id))
        return sent


    async def _send_user_notice(
        self,
        chat_id: str | int,
        notice_type: str,
        text: str,
        *,
        min_interval_seconds: float = USER_NOTICE_RATE_LIMIT_SECONDS,
    ) -> bool:
        now = self.access.now_fn()
        key = (str(chat_id), notice_type)
        last_sent = self.user_notice_times.get(key)
        if last_sent is not None and (now - last_sent).total_seconds() < min_interval_seconds:
            return False
        self.user_notice_times[key] = now
        try:
            await self._send(chat_id, text)
        except Exception:
            LOGGER.exception("Failed to send Telegram user notice", extra={"chat_id": str(chat_id), "notice_type": notice_type})
            return False
        return True


    async def _send_document(
        self,
        chat_id: str | int,
        path: Path,
        *,
        caption: str | None = None,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            await self._send(chat_id, f"Generated attachment was not found: {path}")
            return None
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return await self._send_document_bytes(
            chat_id,
            data,
            filename=_safe_filename(path.name),
            caption=caption,
            content_type=content_type,
        )


    async def _send_document_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_document(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
        )
        if isinstance(sent, dict):
            message_id = sent.get("message_id")
            if message_id is not None:
                self.track_bridge_message(chat_id, int(message_id))
        return sent if isinstance(sent, dict) else None


    async def _send_photo_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_photo(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
        )
        if isinstance(sent, dict):
            message_id = sent.get("message_id")
            if message_id is not None:
                self.track_bridge_message(chat_id, int(message_id))
        return sent if isinstance(sent, dict) else None


    async def _send_video_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_video(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
            duration=duration,
            width=width,
            height=height,
        )
        if isinstance(sent, dict):
            message_id = sent.get("message_id")
            if message_id is not None:
                self.track_bridge_message(chat_id, int(message_id))
        return sent if isinstance(sent, dict) else None


    def _track_sent_message(self, chat_id: str | int, sent: Any) -> None:
        messages = sent if isinstance(sent, list) else [sent]
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_id = message.get("message_id")
            if message_id is not None:
                self.track_bridge_message(chat_id, int(message_id))


    async def _send_animation_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_animation(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
            duration=duration,
            width=width,
            height=height,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_audio_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        performer: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_audio(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
            duration=duration,
            performer=performer,
            title=title,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_voice_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_voice(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            caption=caption,
            content_type=content_type,
            duration=duration,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_video_note_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        duration: int | None = None,
        length: int | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_video_note(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            content_type=content_type,
            duration=duration,
            length=length,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_sticker_bytes(
        self,
        chat_id: str | int,
        data: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        emoji: str | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_sticker(
            _bot_chat_id(chat_id),
            data,
            filename=_safe_filename(filename),
            content_type=content_type,
            emoji=emoji,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_live_photo_bytes(
        self,
        chat_id: str | int,
        live_photo: bytes,
        photo: bytes,
        *,
        live_photo_filename: str,
        photo_filename: str,
        caption: str | None = None,
        live_photo_content_type: str | None = None,
        photo_content_type: str | None = None,
    ) -> dict[str, Any] | None:
        sent = await self.bot.send_live_photo(
            _bot_chat_id(chat_id),
            live_photo,
            photo,
            live_photo_filename=_safe_filename(live_photo_filename),
            photo_filename=_safe_filename(photo_filename),
            caption=caption,
            live_photo_content_type=live_photo_content_type,
            photo_content_type=photo_content_type,
        )
        self._track_sent_message(chat_id, sent)
        return sent if isinstance(sent, dict) else None


    async def _send_output_attachment(self, context: TurnContext, item: dict[str, Any]) -> None:
        attachment = _output_attachment(item)
        if attachment is None:
            return
        if attachment.key in context.output_attachments_sent:
            return
        if attachment.path is not None:
            if not attachment.path.is_file():
                await self._send(context.chat_id, f"Generated attachment was not found: {attachment.path}")
                sent = None
            else:
                sent = await self._send_photo_bytes(
                    context.chat_id,
                    attachment.path.read_bytes(),
                    filename=attachment.filename,
                    caption=attachment.caption,
                    content_type=attachment.content_type,
                )
        elif attachment.data is not None:
            sent = await self._send_photo_bytes(
                context.chat_id,
                attachment.data,
                filename=attachment.filename,
                caption=attachment.caption,
                content_type=attachment.content_type,
            )
        else:
            sent = None
        if sent is not None:
            context.output_attachments_sent.add(attachment.key)


    async def _send_active_turn_wait_message(self, chat_id: str) -> None:
        await self._send(chat_id, "A Codex turn is already active. Please wait, use /steer <text>, or /cancel.")


    async def _send_active_task_disabled_message(self, chat_id: str, command_name: str) -> None:
        await self._send(
            chat_id,
            f"/{command_name} is disabled while a task is in progress. Use /steer <text> or /cancel.",
        )


    def _start_typing_indicator(self, context: TurnContext) -> None:
        if not hasattr(self.bot, "send_chat_action"):
            return
        if context.turn_id in self.typing_tasks:
            return
        self.typing_tasks[context.turn_id] = asyncio.create_task(self._typing_indicator_loop(context.chat_id))


    def _resume_typing_for_pending_record(self, record: dict[str, Any]) -> None:
        self._mark_pending_record_answered(record, resume_typing=True)


    def _mark_pending_record_answered(
        self,
        record: dict[str, Any],
        *,
        resume_typing: bool = False,
    ) -> None:
        context = self.turns.get(str(record.get("turn_id") or ""))
        if context is None or context.completed:
            return
        self._record_turn_progress(context, "user_prompt_answered", waiting_on_user=False)
        if resume_typing:
            self._start_typing_indicator(context)


    def _stop_typing_indicator(self, turn_id: str) -> None:
        task = self.typing_tasks.pop(turn_id, None)
        if task is not None:
            task.cancel()


    def stop_typing_indicators(self) -> None:
        for turn_id in list(self.typing_tasks):
            self._stop_typing_indicator(turn_id)


    def stop_background_tasks(self) -> None:
        for task in list(self.background_tasks):
            task.cancel()


    async def _typing_indicator_loop(self, chat_id: str) -> None:
        while True:
            try:
                await self.bot.send_chat_action(_bot_chat_id(chat_id), "typing")
                await asyncio.sleep(TYPING_ACTION_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                return


    async def _edit_callback_message(self, callback: dict[str, Any], text: str) -> None:
        message = callback.get("message") or {}
        chat_id = ((message.get("chat") or {}).get("id"))
        message_id = message.get("message_id")
        if chat_id is None or message_id is None:
            return
        await self.bot.edit_message_text(chat_id, int(message_id), text, reply_markup={"inline_keyboard": []})


    def _record_last_update(self, chat_id: str, update_id: Any) -> None:
        if update_id is None:
            return
        chats = self.store.load_chats()
        key = TelegramStateStore.chat_key(chat_id)
        chat_state = dict(chats.get(key) or {})
        chat_state["last_update_id"] = update_id
        chats[key] = chat_state
        self.store.save_chats(chats)

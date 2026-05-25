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
    TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
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


class TelegramBridgeThreadMixin:
    async def _start_turn(
        self,
        *,
        chat_id: str,
        user_id: str,
        message_id: int | None,
        text: str,
        attachments: list[dict[str, Any]],
        force_new_thread: bool = False,
        thread_id_override: str | None = None,
    ) -> None:
        workspace = self._active_workspace(chat_id)
        if chat_id in self.workspace_reset_chats:
            self.workspace_reset_chats.discard(chat_id)
            await self._send(chat_id, "Active workspace was outside allowed roots and was reset to the default workspace.")
        thread_id = thread_id_override or await self._ensure_thread(chat_id, workspace, force_new=force_new_thread)
        input_items = await self._input_items(text, attachments, workspace)
        permissions = self._thread_permissions(chat_id, workspace)
        turn_kwargs: dict[str, Any] = {
            "thread_id": thread_id,
            "input_items": input_items,
            "cwd": str(workspace),
            "approval_policy": self._thread_approval_policy(chat_id, workspace),
        }
        if permissions:
            turn_kwargs["permissions"] = permissions
        else:
            turn_kwargs["sandbox_policy"] = {
                "type": "workspaceWrite",
                "writableRoots": [str(workspace)],
            }
        result = await self.app_server.turn_start(**turn_kwargs)
        turn_id = _extract_id(result, "turn") or f"turn_local_{secrets.token_hex(8)}"
        context = TurnContext(
            chat_id=chat_id,
            user_id=user_id,
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=workspace,
            message_id=message_id,
            auto_name_text=text if thread_id_override is None else None,
            attachments={str(item["file_id"]): item for item in attachments if item.get("file_id")},
        )
        self.turns[turn_id] = context
        self.latest_turn_by_thread[thread_id] = turn_id
        self._start_typing_indicator(context)


    async def _ensure_thread(
        self,
        chat_id: str,
        workspace: Path,
        *,
        force_new: bool = False,
        active_mode_payload: dict[str, Any] | None = None,
    ) -> str:
        if force_new:
            return await self._start_new_thread(chat_id, workspace, active_mode_payload=active_mode_payload)
        record = self._thread_record(chat_id, workspace)
        thread_id = record.get("thread_id")
        if thread_id:
            thread_id = str(thread_id)
            if record.get(TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY) != _dynamic_tools_fingerprint():
                return await self._start_new_thread(chat_id, workspace, active_mode_payload=active_mode_payload)
            if thread_id not in self.resumed_thread_ids and hasattr(self.app_server, "thread_resume"):
                permissions = self._thread_permissions(chat_id, workspace)
                try:
                    await self.app_server.thread_resume(
                        thread_id=thread_id,
                        cwd=str(workspace),
                        approval_policy=self._thread_approval_policy(chat_id, workspace),
                        sandbox=None if permissions else _thread_sandbox_value(self.settings.sandbox),
                        model=self._thread_setting(chat_id, workspace, "model"),
                        permissions=permissions,
                        developer_instructions=TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
                    )
                    self.resumed_thread_ids.add(thread_id)
                except JsonRpcError:
                    return await self._start_new_thread(chat_id, workspace, active_mode_payload=active_mode_payload)
            return str(thread_id)
        return await self._start_new_thread(chat_id, workspace, active_mode_payload=active_mode_payload)


    async def _start_new_thread(
        self,
        chat_id: str,
        workspace: Path,
        *,
        active_mode_payload: dict[str, Any] | None = None,
    ) -> str:
        permissions = self._thread_permissions(chat_id, workspace)
        active_mode = self._thread_active_mode(chat_id, workspace)
        stored_model = self._thread_mode_setting(chat_id, workspace, "model", mode_name=active_mode)
        stored_effort = self._thread_mode_setting(chat_id, workspace, "effort", mode_name=active_mode)
        mode_payload = active_mode_payload
        if mode_payload is None and active_mode != "default":
            mode_payload = await self._collaboration_mode_payload_for_mode(chat_id, workspace, active_mode)
        mode_model = self._collaboration_mode_model(mode_payload)
        start_model = stored_model or mode_model or self.settings.model
        thread_kwargs: dict[str, Any] = {
            "cwd": str(workspace),
            "model": start_model,
            "approval_policy": self._thread_approval_policy(chat_id, workspace),
            "dynamic_tools": telegram_dynamic_tools(),
            "developer_instructions": TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
        }
        if permissions:
            thread_kwargs["permissions"] = permissions
        else:
            thread_kwargs["sandbox"] = _thread_sandbox_value(self.settings.sandbox)
        result = await self.app_server.thread_start(**thread_kwargs)
        thread_id = _extract_id(result, "thread") or str(result.get("threadId") or "")
        self._save_thread_record(
            chat_id,
            workspace,
            thread_id,
            auto_name_pending=True,
            dynamic_tools_fingerprint=_dynamic_tools_fingerprint(),
        )
        update_kwargs: dict[str, Any] = {"thread_id": thread_id}
        if stored_effort:
            update_kwargs["effort"] = stored_effort
            if start_model:
                update_kwargs["model"] = start_model
        personality = self._thread_setting(chat_id, workspace, "personality")
        if personality:
            update_kwargs["personality"] = personality
        if mode_payload is not None:
            update_kwargs["collaboration_mode"] = mode_payload
        if len(update_kwargs) > 1:
            await self.app_server.thread_settings_update(**update_kwargs)
        memory_mode = self._thread_setting(chat_id, workspace, "memory_mode")
        if memory_mode and hasattr(self.app_server, "thread_memory_mode_set"):
            await self.app_server.thread_memory_mode_set(thread_id=thread_id, mode=memory_mode)
        return thread_id


    async def _fork_current_thread(
        self,
        chat_id: str,
        workspace: Path,
        *,
        ephemeral: bool = False,
        save_mapping: bool = True,
    ) -> str | None:
        current_thread_id = await self._ensure_thread(chat_id, workspace)
        if not hasattr(self.app_server, "thread_fork"):
            return None
        fork_kwargs: dict[str, Any] = {
            "thread_id": current_thread_id,
            "exclude_turns": True,
            "developer_instructions": TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
        }
        if ephemeral:
            permissions = self._thread_permissions(chat_id, workspace)
            fork_kwargs.update(
                {
                    "ephemeral": True,
                    "cwd": str(workspace),
                    "approval_policy": self._thread_approval_policy(chat_id, workspace),
                    "sandbox": None if permissions else _thread_sandbox_value(self.settings.sandbox),
                    "permissions": permissions,
                }
            )
        try:
            result = await self.app_server.thread_fork(**fork_kwargs)
        except JsonRpcError as exc:
            if "no rollout found for thread id" not in str(exc):
                raise
            current_thread_id = await self._start_new_thread(chat_id, workspace)
            fork_kwargs["thread_id"] = current_thread_id
            result = await self.app_server.thread_fork(**fork_kwargs)
        forked_thread_id = _extract_id(result, "thread") or str(result.get("threadId") or "")
        if forked_thread_id:
            if save_mapping:
                self._save_thread_record(chat_id, workspace, forked_thread_id, auto_name_pending=False)
            return forked_thread_id
        return None


    async def _compact_current_thread(self, chat_id: str) -> bool:
        workspace = self._active_workspace(chat_id)
        record = self._thread_record(chat_id, workspace)
        thread_id = str(record.get("thread_id") or "")
        if not thread_id or not hasattr(self.app_server, "thread_compact_start"):
            return False
        await self.app_server.thread_compact_start(thread_id=thread_id)
        await self._send(chat_id, "Compaction requested.")
        return True


    async def _rollback_current_thread(self, chat_id: str, workspace: Path, args: str) -> bool:
        record = self._thread_record(chat_id, workspace)
        thread_id = str(record.get("thread_id") or "")
        if not thread_id or not hasattr(self.app_server, "thread_rollback"):
            return False
        num_turns = 1
        if args.strip():
            try:
                num_turns = max(1, int(args.strip()))
            except ValueError:
                await self._send(chat_id, "Use /rollback or /rollback <turn-count>.")
                return False
        await self.app_server.thread_rollback(thread_id=thread_id, num_turns=num_turns)
        return True


    async def _archive_current_thread(self, chat_id: str, workspace: Path) -> bool:
        record = self._thread_record(chat_id, workspace)
        thread_id = str(record.get("thread_id") or "")
        if not thread_id or not hasattr(self.app_server, "thread_archive"):
            return False
        await self.app_server.thread_archive(thread_id=thread_id)
        return True


    async def _unarchive_current_thread(self, chat_id: str, workspace: Path) -> bool:
        record = self._thread_record(chat_id, workspace)
        thread_id = str(record.get("thread_id") or "")
        if not thread_id or not hasattr(self.app_server, "thread_unarchive"):
            return False
        await self.app_server.thread_unarchive(thread_id=thread_id)
        if hasattr(self.app_server, "thread_resume"):
            permissions = self._thread_permissions(chat_id, workspace)
            try:
                await self.app_server.thread_resume(
                    thread_id=thread_id,
                    cwd=str(workspace),
                    approval_policy=self._thread_approval_policy(chat_id, workspace),
                    sandbox=None if permissions else _thread_sandbox_value(self.settings.sandbox),
                    model=self._thread_setting(chat_id, workspace, "model"),
                    permissions=permissions,
                    developer_instructions=TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
                )
                self.resumed_thread_ids.add(thread_id)
            except JsonRpcError:
                self.resumed_thread_ids.discard(thread_id)
        return True


    async def _review_current_thread(self, chat_id: str) -> bool:
        workspace = self._active_workspace(chat_id)
        thread_id = await self._ensure_thread(chat_id, workspace)
        if not thread_id or not hasattr(self.app_server, "review_start"):
            return False
        result = await self.app_server.review_start(thread_id=thread_id, target={"type": "uncommittedChanges"})
        turn_id = _extract_id(result, "turn") or f"turn_review_{secrets.token_hex(8)}"
        context = TurnContext(
            chat_id=chat_id,
            user_id="",
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=workspace,
        )
        self.turns[turn_id] = context
        self.latest_turn_by_thread[thread_id] = turn_id
        self._start_typing_indicator(context)
        return True


    async def _init_project_instructions(self, chat_id: str, user_id: str, message_id: int | None) -> bool:
        workspace = self._active_workspace(chat_id)
        agents = _find_agents_file(workspace)
        if agents is not None:
            await self._send(chat_id, f"Project instructions already exist: {agents}")
            return True
        if self._active_turn_context(chat_id) is not None:
            await self._send_active_turn_wait_message(chat_id)
            return True
        await self._start_turn(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            text="Create an AGENTS.md file with concise project instructions for this workspace.",
            attachments=[],
        )
        return True


    async def _handle_plan_turn_command(
        self,
        chat_id: str,
        user_id: str,
        command: TelegramCommand,
        message_id: int | None,
    ) -> bool:
        workspace = self._active_workspace(chat_id)
        message = await self._apply_mode_setting(chat_id, workspace, "plan")
        if not command.args:
            await self._send(chat_id, message)
            return True
        if self._active_turn_context(chat_id) is not None:
            await self._send_active_turn_wait_message(chat_id)
            return True
        await self._send(chat_id, message)
        await self._start_turn(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            text=command.args,
            attachments=[],
        )
        return True


    def _save_thread_record(
        self,
        chat_id: str,
        workspace: Path,
        thread_id: str,
        *,
        auto_name_pending: bool | None = None,
        title: str | None = None,
        dynamic_tools_fingerprint: str | None = None,
    ) -> None:
        threads = self.store.load_threads()
        key = TelegramStateStore.thread_key(chat_id, workspace)
        existing = dict(threads.get(key) or {})
        same_thread = str(existing.get("thread_id") or "") == thread_id
        now = _format_iso(self.access.now_fn())
        record = {
            "thread_id": thread_id,
            "workspace": str(workspace),
            "created_at": existing.get("created_at") if same_thread and existing.get("created_at") else now,
            "updated_at": now,
        }
        settings = self._normalized_thread_settings(existing.get("settings"))
        if settings:
            record["settings"] = settings
        stored_title = title or (existing.get("title") if same_thread else None)
        if isinstance(stored_title, str) and stored_title.strip():
            record["title"] = stored_title.strip()
        if auto_name_pending is not None:
            record["auto_name_pending"] = auto_name_pending
        elif same_thread and "auto_name_pending" in existing:
            record["auto_name_pending"] = bool(existing.get("auto_name_pending"))
        if same_thread and existing.get("auto_generated_title"):
            record["auto_generated_title"] = bool(existing.get("auto_generated_title"))
        if dynamic_tools_fingerprint:
            record[TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY] = dynamic_tools_fingerprint
        elif same_thread and existing.get(TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY):
            record[TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY] = existing[TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY]
        threads[key] = record
        self.store.save_threads(threads)


    def _save_thread_title(
        self,
        chat_id: str,
        workspace: Path,
        thread_id: str,
        title: str,
        *,
        auto_generated: bool,
    ) -> None:
        threads = self.store.load_threads()
        key = TelegramStateStore.thread_key(chat_id, workspace)
        record = dict(threads.get(key) or {})
        if str(record.get("thread_id") or "") != thread_id:
            return
        record["title"] = title
        record["auto_name_pending"] = False
        record["auto_generated_title"] = auto_generated
        record["updated_at"] = _format_iso(self.access.now_fn())
        threads[key] = record
        self.store.save_threads(threads)


    async def _maybe_auto_name_thread(self, context: TurnContext) -> None:
        if not context.auto_name_text:
            return
        record = self._thread_record(context.chat_id, context.workspace)
        if str(record.get("thread_id") or "") != context.thread_id:
            return
        if record.get("auto_name_pending") is not True:
            return
        if _first_text(record.get("title")):
            return
        title = _thread_title_from_text(context.auto_name_text)
        if not title or not hasattr(self.app_server, "thread_set_name"):
            return
        try:
            await self.app_server.thread_set_name(thread_id=context.thread_id, name=title)
        except (AttributeError, JsonRpcError):
            return
        self._save_thread_title(context.chat_id, context.workspace, context.thread_id, title, auto_generated=True)


    def _schedule_auto_name_thread(self, context: TurnContext) -> None:
        task = asyncio.create_task(self._maybe_auto_name_thread(context))
        self.background_tasks.add(task)
        task.add_done_callback(self._discard_background_task)


    def _discard_background_task(self, task: asyncio.Task[Any]) -> None:
        self.background_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            LOGGER.exception("Telegram bridge background task failed")


    def _save_thread_token_usage(self, params: dict[str, Any]) -> None:
        thread_id = _thread_id(params)
        usage = params.get("tokenUsage")
        if not thread_id or not isinstance(usage, dict):
            return
        threads = self.store.load_threads()
        changed = False
        for key, raw_record in list(threads.items()):
            if not isinstance(raw_record, dict):
                continue
            if str(raw_record.get("thread_id") or "") != thread_id:
                continue
            record = dict(raw_record)
            record["token_usage"] = usage
            turn_id = params.get("turnId") or params.get("turn_id")
            if turn_id:
                record["token_usage_turn_id"] = str(turn_id)
            record["token_usage_updated_at"] = _format_iso(self.access.now_fn())
            threads[key] = record
            changed = True
        if changed:
            self.store.save_threads(threads)


    def _record_guardian_denial(self, params: dict[str, Any]) -> None:
        thread_id = _thread_id(params)
        review = params.get("review") if isinstance(params.get("review"), dict) else {}
        if not thread_id or str(review.get("status") or "") != "denied":
            return
        denials = self.guardian_denials_by_thread.setdefault(thread_id, [])
        denials.append(dict(params))
        del denials[:-10]


    async def _send_usage_status(self, chat_id: str) -> None:
        workspace = self._active_workspace(chat_id)
        record = self._thread_record(chat_id, workspace)
        await self._send(chat_id, _format_thread_token_usage(record))


    async def _send_status(self, chat_id: str) -> None:
        workspace = self._active_workspace(chat_id)
        record = self._thread_record(chat_id, workspace)
        config_result, account_result, limits_result = await asyncio.gather(
            self._status_call(lambda: self.app_server.config_read(cwd=str(workspace), include_layers=False)),
            self._status_call(lambda: self.app_server.account_read(refresh_token=False)),
            self._status_call(self.app_server.account_rate_limits_read),
        )
        await self._send(
            chat_id,
            _format_gateway_status(
                workspace=workspace,
                record=record,
                default_sandbox=self.settings.sandbox,
                default_approval_policy=self.settings.approval_policy,
                default_permission_profile=self.settings.permission_profile,
                config_result=config_result,
                account_result=account_result,
                limits_result=limits_result,
            ),
        )


    async def _status_call(self, call: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await call()
        except Exception as exc:  # Status should degrade instead of breaking command handling.
            return {"_error": sanitize_text(str(exc))}


    def _mode_storage_key(self, mode_name: Any) -> str:
        text = str(mode_name or "").strip().casefold()
        return text or "default"


    def _normalized_thread_settings(self, raw_settings: Any) -> dict[str, Any]:
        settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
        modes: dict[str, dict[str, str]] = {}
        raw_modes = settings.get("modes")
        if isinstance(raw_modes, dict):
            for raw_mode, raw_mode_settings in raw_modes.items():
                if not isinstance(raw_mode_settings, dict):
                    continue
                mode_key = self._mode_storage_key(raw_mode)
                mode_settings: dict[str, str] = {}
                for setting_name in ("model", "effort"):
                    value = raw_mode_settings.get(setting_name)
                    if value:
                        mode_settings[setting_name] = str(value)
                if mode_settings:
                    modes[mode_key] = mode_settings
        default_mode = modes.setdefault("default", {})
        for setting_name in ("model", "effort"):
            value = settings.pop(setting_name, None)
            if value and setting_name not in default_mode:
                default_mode[setting_name] = str(value)
        if not default_mode:
            modes.pop("default", None)
        if modes:
            settings["modes"] = modes
        else:
            settings.pop("modes", None)
        active_mode = _first_text(settings.pop("active_mode", None), settings.pop("collaboration_mode", None))
        if active_mode:
            settings["active_mode"] = self._mode_storage_key(active_mode)
        return settings


    def _prune_thread_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        raw_modes = settings.get("modes")
        if isinstance(raw_modes, dict):
            modes = {
                self._mode_storage_key(mode_name): dict(mode_settings)
                for mode_name, mode_settings in raw_modes.items()
                if isinstance(mode_settings, dict) and mode_settings
            }
            if modes:
                settings["modes"] = modes
            else:
                settings.pop("modes", None)
        elif "modes" in settings:
            settings.pop("modes", None)
        for key_name in list(settings):
            value = settings[key_name]
            if value is None or value == {}:
                settings.pop(key_name, None)
        return settings


    def _update_thread_settings_record(
        self,
        chat_id: str,
        workspace: Path,
        update: Callable[[dict[str, Any]], None],
    ) -> None:
        threads = self.store.load_threads()
        key = TelegramStateStore.thread_key(chat_id, workspace)
        record = dict(threads.get(key) or {})
        settings = self._normalized_thread_settings(record.get("settings"))
        update(settings)
        settings = self._prune_thread_settings(settings)
        if settings:
            record["settings"] = settings
            record["workspace"] = str(workspace)
            record["updated_at"] = _format_iso(self.access.now_fn())
            threads[key] = record
        else:
            record.pop("settings", None)
            if record.get("thread_id"):
                record["updated_at"] = _format_iso(self.access.now_fn())
                threads[key] = record
            else:
                threads.pop(key, None)
        self.store.save_threads(threads)


    def _save_thread_setting(self, chat_id: str, workspace: Path, key_name: str, value: str | None) -> None:
        if key_name in {"model", "effort"}:
            self._save_thread_mode_setting(chat_id, workspace, key_name, value)
            return
        if key_name == "collaboration_mode":
            self._save_thread_active_mode(chat_id, workspace, value or "default")
            return

        def update(settings: dict[str, Any]) -> None:
            if value is None:
                settings.pop(key_name, None)
            else:
                settings[key_name] = value

        self._update_thread_settings_record(chat_id, workspace, update)


    def _save_thread_mode_setting(self, chat_id: str, workspace: Path, key_name: str, value: str | None) -> None:
        active_mode = self._thread_active_mode(chat_id, workspace)

        def update(settings: dict[str, Any]) -> None:
            settings["active_mode"] = active_mode
            raw_modes = settings.get("modes")
            modes = raw_modes if isinstance(raw_modes, dict) else {}
            mode_settings = dict(modes.get(active_mode) or {})
            if value is None:
                mode_settings.pop(key_name, None)
            else:
                mode_settings[key_name] = value
            if mode_settings:
                modes[active_mode] = mode_settings
            else:
                modes.pop(active_mode, None)
            if modes:
                settings["modes"] = modes
            else:
                settings.pop("modes", None)

        self._update_thread_settings_record(chat_id, workspace, update)


    def _save_thread_active_mode(self, chat_id: str, workspace: Path, mode_name: str) -> None:
        mode_key = self._mode_storage_key(mode_name)

        def update(settings: dict[str, Any]) -> None:
            settings["active_mode"] = mode_key

        self._update_thread_settings_record(chat_id, workspace, update)


    def _thread_settings(self, chat_id: str, workspace: Path) -> dict[str, Any]:
        raw_settings = self._thread_record(chat_id, workspace).get("settings")
        return raw_settings if isinstance(raw_settings, dict) else {}


    def _thread_active_mode(self, chat_id: str, workspace: Path) -> str:
        settings = self._thread_settings(chat_id, workspace)
        active_mode = _first_text(settings.get("active_mode"), settings.get("collaboration_mode"))
        return self._mode_storage_key(active_mode)


    def _mode_settings(self, settings: dict[str, Any], mode_name: str) -> dict[str, Any]:
        modes = settings.get("modes")
        if not isinstance(modes, dict):
            return {}
        mode_key = self._mode_storage_key(mode_name)
        direct = modes.get(mode_key)
        if isinstance(direct, dict):
            return direct
        for raw_mode, raw_mode_settings in modes.items():
            if self._mode_storage_key(raw_mode) == mode_key and isinstance(raw_mode_settings, dict):
                return raw_mode_settings
        return {}


    def _thread_mode_setting(
        self,
        chat_id: str,
        workspace: Path,
        key_name: str,
        *,
        mode_name: str | None = None,
    ) -> str | None:
        settings = self._thread_settings(chat_id, workspace)
        active_mode = self._mode_storage_key(mode_name or self._thread_active_mode(chat_id, workspace))
        value = self._mode_settings(settings, active_mode).get(key_name)
        if value is None:
            value = settings.get(key_name) if active_mode == "default" else None
        return str(value) if value else None


    def _thread_setting(self, chat_id: str, workspace: Path, key_name: str) -> str | None:
        if key_name in {"model", "effort"}:
            return self._thread_mode_setting(chat_id, workspace, key_name)
        if key_name == "collaboration_mode":
            return self._thread_active_mode(chat_id, workspace)
        settings = self._thread_settings(chat_id, workspace)
        value = settings.get(key_name)
        return str(value) if value else None


    def _thread_permissions(self, chat_id: str, workspace: Path) -> str | None:
        return self._thread_setting(chat_id, workspace, "permissions") or self.settings.permission_profile


    def _thread_approval_policy(self, chat_id: str, workspace: Path) -> str:
        return self._thread_setting(chat_id, workspace, "approval_policy") or _approval_policy_value(self.settings.approval_policy)


    def _active_turn_context(self, chat_id: str) -> TurnContext | None:
        workspace = self._active_workspace(chat_id)
        thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        if not thread_id:
            return None
        turn_id = self.latest_turn_by_thread.get(thread_id)
        context = self.turns.get(turn_id or "")
        if context is None or context.completed:
            return None
        if context.chat_id != str(chat_id):
            return None
        return context


    async def _default_model_setting(self) -> str | None:
        if self.settings.model:
            return self.settings.model
        if not hasattr(self.app_server, "model_list"):
            return None
        result = await self.app_server.model_list()
        items = _result_items(result)
        for item in items:
            if item.get("isDefault"):
                value = _model_config_value(item)
                if value:
                    return value
        for item in items:
            value = _model_config_value(item)
            if value:
                return value
        return None


    async def _collaboration_mode_payload_for_mode(
        self,
        chat_id: str,
        workspace: Path,
        mode_name: str,
    ) -> dict[str, Any] | None:
        result = await self.app_server.collaboration_mode_list()
        mode = _find_named_item(_result_items(result), mode_name)
        if mode is None:
            return None
        model = self._thread_mode_setting(chat_id, workspace, "model", mode_name=mode_name)
        effort = self._thread_mode_setting(chat_id, workspace, "effort", mode_name=mode_name)
        payload = self._collaboration_mode_payload(mode, model=model, effort=effort)
        if payload is None:
            payload = self._collaboration_mode_payload(mode, model=await self._default_model_setting(), effort=effort)
        return payload


    def _collaboration_mode_model(self, payload: dict[str, Any] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return None
        return _first_text(settings.get("model"))


    def _collaboration_mode_payload(
        self,
        mode: dict[str, Any],
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any] | None:
        raw_settings = mode.get("settings")
        raw_mode = mode.get("mode") or mode.get("name")
        if isinstance(raw_settings, dict) and raw_mode:
            settings = dict(raw_settings)
            if not settings.get("model") and model:
                settings["model"] = model
            if effort:
                settings["reasoning_effort"] = effort
            if not settings.get("model"):
                return None
            return {"mode": str(raw_mode), "settings": settings}
        selected_model = mode.get("model") or model or self.settings.model
        if not raw_mode or not selected_model:
            return None
        return {
            "mode": str(raw_mode),
            "settings": {
                "developer_instructions": None,
                "model": str(selected_model),
                "reasoning_effort": effort or mode.get("reasoning_effort"),
            },
        }


    def _active_workspace(self, chat_id: str) -> Path:
        chats = self.store.load_chats()
        key = TelegramStateStore.chat_key(chat_id)
        chat_state = dict(chats.get(key) or {})
        workspace = Path(str(chat_state.get("active_workspace") or self.settings.default_cwd)).resolve(strict=False)
        if not is_path_within_any_root(workspace, self.settings.allowed_roots):
            workspace = self.settings.default_cwd
            chat_state["active_workspace"] = str(workspace)
            chats[key] = chat_state
            self.store.save_chats(chats)
            self.workspace_reset_chats.add(chat_id)
        elif key not in chats:
            chat_state["active_workspace"] = str(workspace)
            chats[key] = chat_state
            self.store.save_chats(chats)
        return workspace


    def _set_active_workspace(self, chat_id: str, workspace: Path) -> None:
        chats = self.store.load_chats()
        key = TelegramStateStore.chat_key(chat_id)
        chat_state = dict(chats.get(key) or {})
        chat_state["active_workspace"] = str(workspace)
        chats[key] = chat_state
        self.store.save_chats(chats)


    def _thread_record(self, chat_id: str, workspace: Path) -> dict[str, Any]:
        record = self.store.load_threads().get(TelegramStateStore.thread_key(chat_id, workspace), {})
        return record if isinstance(record, dict) else {}


    async def _send_resume_options(self, chat_id: str, workspace: Path) -> None:
        try:
            result = await self.app_server.thread_list(cwd=str(workspace), limit=10)
        except (AttributeError, JsonRpcError):
            result = None
        if result is not None:
            items = _result_items(result)
            if items:
                fallbacks = await self._thread_title_fallbacks(chat_id, result)
                rows = []
                for item in items:
                    thread_id = _thread_id_from_thread_item(item)
                    if not thread_id:
                        continue
                    title = _thread_title_from_item(item) or fallbacks.get(thread_id)
                    rows.append(
                        [
                            {
                                "text": _resume_button_text(thread_id, title),
                                "callback_data": f"resume:{thread_id}",
                            }
                        ]
                    )
                if rows:
                    await self._send(
                        chat_id,
                        f"Select a thread for {workspace}:",
                        reply_markup={"inline_keyboard": rows},
                    )
                    return
        threads = self.store.load_threads()
        key = TelegramStateStore.thread_key(chat_id, workspace)
        if key not in threads:
            await self._send(chat_id, f"No local thread record for {workspace}.")
            return
        record = threads[key] or {}
        thread_id = str(record.get("thread_id") or "")
        title = _first_text(record.get("title"))
        await self._send(
            chat_id,
            f"Select a thread for {workspace}:",
            reply_markup={
                "inline_keyboard": [
                    [{"text": _resume_button_text(thread_id, title), "callback_data": f"resume:{thread_id}"}]
                ]
            },
        )


    async def _thread_title_for_id(self, chat_id: str, thread_id: str) -> str | None:
        local_titles = self._local_thread_titles(chat_id)
        if thread_id in local_titles:
            return local_titles[thread_id]
        if not hasattr(self.app_server, "thread_read"):
            return None
        try:
            return _thread_title_from_read_result(await self.app_server.thread_read(thread_id=thread_id, include_turns=True))
        except (AttributeError, JsonRpcError):
            return None


    async def _thread_title_fallbacks(self, chat_id: str, result: Any) -> dict[str, str]:
        fallbacks = self._local_thread_titles(chat_id)
        missing = []
        for item in _result_items(result):
            thread_id = _thread_id_from_thread_item(item)
            if not thread_id:
                continue
            if _thread_title_from_item(item) or thread_id in fallbacks:
                continue
            missing.append(thread_id)
        if not missing or not hasattr(self.app_server, "thread_read"):
            return fallbacks
        reads = await asyncio.gather(
            *[self._safe_thread_read_title(thread_id) for thread_id in missing],
            return_exceptions=True,
        )
        for thread_id, title in zip(missing, reads):
            if isinstance(title, str) and title:
                fallbacks[thread_id] = title
        return fallbacks


    async def _safe_thread_read_title(self, thread_id: str) -> str | None:
        try:
            return _thread_title_from_read_result(await self.app_server.thread_read(thread_id=thread_id, include_turns=True))
        except (AttributeError, JsonRpcError):
            return None


    def _local_thread_titles(self, chat_id: str) -> dict[str, str]:
        prefix = f"chat_id:{chat_id}|cwd:"
        titles: dict[str, str] = {}
        for key, value in self.store.load_threads().items():
            if not key.startswith(prefix) or not isinstance(value, dict):
                continue
            thread_id = str(value.get("thread_id") or "")
            title = _first_text(value.get("title"))
            if thread_id and title:
                titles[thread_id] = title
        return titles


    async def _download_message_attachments(
        self,
        chat_id: str,
        message: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        attachments: list[dict[str, Any]] = []
        document = message.get("document")
        if isinstance(document, dict):
            downloaded = await self._download_attachment(chat_id, _message_id(message), document)
            if downloaded is None:
                return None
            attachments.append(downloaded)
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            photo = dict(photos[-1])
            photo.setdefault("_default_file_stem", f"photo_{photo.get('file_unique_id') or photo.get('file_id')}")
            downloaded = await self._download_attachment(chat_id, _message_id(message), photo)
            if downloaded is None:
                return None
            attachments.append(downloaded)
        return attachments


    async def _download_attachment(
        self,
        chat_id: str,
        message_id: int | None,
        attachment: dict[str, Any],
    ) -> dict[str, Any] | None:
        file_id = str(attachment.get("file_id") or "")
        size = int(attachment.get("file_size") or 0)
        filename = _attachment_filename(attachment, "", file_id)
        if size > self.settings.max_attachment_bytes:
            await self._send(chat_id, f"Attachment {filename} is too large for this bridge.")
            return None
        file_info = await self.bot.get_file(file_id)
        file_path = str(file_info.get("file_path") or "")
        filename = _attachment_filename(attachment, file_path, file_id)
        file_size = int(file_info.get("file_size") or size or 0)
        if file_size > self.settings.max_attachment_bytes:
            await self._send(chat_id, f"Attachment {filename} is too large for this bridge.")
            return None
        data = await self.bot.download_file(file_path)
        if len(data) > self.settings.max_attachment_bytes:
            await self._send(chat_id, f"Attachment {filename} is too large for this bridge.")
            return None
        target_dir = self.store.downloads_dir(chat_id, message_id or "unknown")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / filename).resolve(strict=False)
        try:
            target.relative_to(target_dir.resolve(strict=False))
        except ValueError:
            await self._send(chat_id, "Attachment filename was rejected.")
            return None
        target.write_bytes(data)
        return {
            "file_id": file_id,
            "filename": filename,
            "path": str(target),
            "mime_type": _attachment_mime_type(attachment, filename, file_path),
            "size_bytes": len(data),
        }

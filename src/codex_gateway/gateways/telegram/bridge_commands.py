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


class TelegramBridgeCommandMixin:
    async def _handle_local_command(self, chat_id: str, command: TelegramCommand) -> None:
        if command.name == "help" or command.name == "start":
            await self._send(chat_id, TELEGRAM_HELP_TEXT)
            return
        if command.name == "commands":
            error = await self.sync_telegram_command_menu()
            if error:
                await self._send(chat_id, f"Telegram command menu sync failed: {error}")
                return
            await self._send(chat_id, "Telegram command menu synced.")
            return
        if command.name == "status":
            await self._send_status(chat_id)
            return
        if command.name == "diff":
            await self._handle_diff_command(chat_id)
            return
        if command.name in {"project", "getcwd"}:
            await self._send(chat_id, str(self._active_workspace(chat_id)))
            return
        if command.name == "projects":
            await self._handle_workspace_command(chat_id, "list")
            return
        if command.name == "setcwd":
            await self._handle_workspace_command(chat_id, f"set {command.args}".strip())
            return
        if command.name == "searchcwd":
            await self._handle_searchcwd_command(chat_id, command.args)
            return
        if command.name == "reset":
            self._reset_chat_state(chat_id)
            await self._send(chat_id, "Chat gateway state reset.")
            return
        if command.name == "clear":
            self._clear_chat_mapping(chat_id)
            await self._send(chat_id, "Chat thread mapping cleared.")
            return
        if command.name == "threads":
            await self._handle_threads_command(chat_id)
            return
        if command.name == "workspace":
            await self._handle_workspace_command(chat_id, command.args)
            return


    async def _handle_unpaired_or_unauthorized_user(
        self,
        chat_id: str,
        user_id: str,
        username: str,
        command: TelegramCommand,
    ) -> None:
        if not self.access.can_request_pairing(user_id):
            await self._send(chat_id, _unauthorized_user_text())
            return
        if command.kind == TelegramCommandKind.LOCAL and command.name == "start":
            code = self.access.create_pairing_code(user_id, username=username, chat_id=chat_id)
            if code is None:
                await self._send(chat_id, _unauthorized_user_text())
                return
            await self._send(chat_id, _pairing_guidance_text(code))
            return
        await self._send(chat_id, _start_pairing_text())


    async def _handle_app_server_command(
        self,
        chat_id: str,
        user_id: str,
        command: TelegramCommand,
    ) -> None:
        name = command.name or ""
        workspace = self._active_workspace(chat_id)
        args = command.args.strip()
        try:
            if name == "model":
                await self._handle_model_command(chat_id, user_id, workspace, args)
                return
            if name == "permissions":
                await self._handle_permissions_command(chat_id, user_id, workspace, args)
                return
            if name == "approval":
                await self._handle_approval_policy_command(chat_id, user_id, workspace, args)
                return
            if name == "mode":
                await self._handle_mode_command(chat_id, user_id, workspace, args)
                return
            if name == "effort":
                await self._handle_effort_command(chat_id, user_id, workspace, args)
                return
            if name == "personality":
                await self._handle_personality_command(chat_id, user_id, workspace, args)
                return
            if name == "goal":
                await self._handle_goal_command(chat_id, workspace, args)
                return
            if name == "approve":
                await self._handle_approve_command(chat_id, user_id, workspace)
                return
            if name in {"agent", "subagents"}:
                await self._handle_loaded_threads_command(chat_id, user_id, workspace, subagents_only=name == "subagents")
                return
            if name == "rename":
                if not args:
                    await self._send(chat_id, "Use /rename <title>.")
                    return
                thread_id = await self._ensure_thread(chat_id, workspace)
                await self.app_server.thread_set_name(thread_id=thread_id, name=args)
                self._save_thread_title(chat_id, workspace, thread_id, args, auto_generated=False)
                await self._send(chat_id, f"Thread renamed: {args}")
                return
            if name in {"cancel", "interrupt"}:
                await self._interrupt_active_turn(chat_id)
                return
            if name == "steer":
                await self._steer_active_turn(chat_id, args)
                return
            if name == "threads":
                await self._handle_threads_command(chat_id, args)
                return
            if name == "ps":
                await self._handle_ps_command(chat_id, workspace)
                return
            if name == "stop":
                await self._handle_stop_command(chat_id, user_id, workspace)
                return
            if name == "account":
                await self._send(chat_id, _format_account(await self.app_server.account_read(refresh_token=False)))
                return
            if name == "limits":
                await self._send(chat_id, _format_rate_limits(await self.app_server.account_rate_limits_read()))
                return
            if name == "hooks":
                await self._send(chat_id, _format_hooks(await self.app_server.hooks_list(cwds=[str(workspace)])))
                return
            if name == "mcp":
                if args.lower() == "reload":
                    await self.app_server.config_mcp_server_reload()
                    await self._send(chat_id, "MCP server configuration reloaded.")
                    return
                detail = "toolsAndAuthOnly"
                if args.lower() == "verbose":
                    detail = "full"
                elif args:
                    await self._send(chat_id, "Use /mcp, /mcp verbose, or /mcp reload.")
                    return
                result = await self.app_server.mcp_server_status_list(detail=detail)
                await self._send(chat_id, _format_mcp_servers(result))
                return
            if name == "apps":
                thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "") or None
                await self._send(chat_id, _format_apps(await self.app_server.app_list(thread_id=thread_id)))
                return
            if name in {"features", "experimental"}:
                thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "") or None
                result = await self.app_server.experimental_feature_list(thread_id=thread_id)
                if name == "experimental":
                    await self._send_experimental_selection(chat_id, user_id, result)
                else:
                    await self._send(chat_id, _format_features(result))
                return
            if name == "memories":
                await self._handle_memories_command(chat_id, user_id, workspace, args)
                return
            if name == "skills":
                await self._handle_skills_command(chat_id, user_id, workspace, args)
                return
            if name == "plugins":
                result = await self.app_server.plugin_list(cwds=[str(workspace)])
                await self._send(chat_id, _format_plugins(result))
                return
            if name in {"config", "debug-config"}:
                result = await self.app_server.config_read(cwd=str(workspace), include_layers=name == "debug-config")
                await self._send(chat_id, _format_config(result))
                return
        except JsonRpcError as exc:
            if name == "apps" and _apps_unavailable_error(exc):
                await self._send(chat_id, "Apps are not available for this account/config.")
                return
            await self._send(chat_id, f"App-server command failed: {exc}")


    async def _handle_model_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            await self._send_model_selection(chat_id, user_id, workspace)
            return
        model_args, effort = _split_model_effort_args(args)
        models_result = await self.app_server.model_list()
        model = _find_model(_result_items(models_result), model_args)
        if model is None:
            await self._send(chat_id, f"Unknown model: {model_args}\n\n{_format_models(models_result)}")
            return
        model_name = _model_config_value(model)
        if not model_name:
            await self._send(chat_id, f"Unknown model: {model_args}\n\n{_format_models(models_result)}")
            return
        if effort is not None:
            if not _model_supports_reasoning_effort(model, effort):
                await self._send(chat_id, _unsupported_model_effort_text(model_name, model))
                return
            await self._send(chat_id, await self._apply_model_effort_setting(chat_id, workspace, model_name, effort))
            return
        if await self._send_model_effort_selection(chat_id, user_id, workspace, model_name, model):
            return
        await self._send(chat_id, await self._apply_model_setting(chat_id, workspace, model_name))


    async def _handle_permissions_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            await self._send_permissions_selection(chat_id, user_id, workspace)
            return
        result = await self.app_server.permission_profile_list(cwd=str(workspace))
        profiles = _result_items(result)
        mapped = _cli_permission_choice(args)
        if mapped is not None:
            aliases, fallback = mapped
            permission = _resolve_permission_profile(profiles, aliases, fallback)
        else:
            permission = _find_permission_profile(profiles, args)
        if permission is None:
            await self._send(chat_id, f"Permission profile not found: {args}")
            return
        await self._send(chat_id, await self._apply_permission_setting(chat_id, workspace, permission))


    async def _handle_approval_policy_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            await self._send_fixed_selection(
                chat_id,
                user_id,
                "Select an approval policy:",
                "approval",
                APPROVAL_POLICY_CHOICES,
            )
            return
        policy = _approval_policy_value(args.lower())
        if policy not in {value for _, value in APPROVAL_POLICY_CHOICES}:
            await self._send(chat_id, "Use /approval <untrusted|on-failure|on-request|never>.")
            return
        await self._send(chat_id, await self._apply_approval_policy_setting(chat_id, workspace, policy))


    async def _handle_mode_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            await self._send_mode_selection(chat_id, user_id)
            return
        await self._send(chat_id, await self._apply_mode_setting(chat_id, workspace, args))


    async def _handle_effort_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        effort = _reasoning_effort_value(args)
        if not effort:
            await self._send(
                chat_id,
                "Use /model to select a model and reasoning effort together. "
                "Legacy /effort accepts <none|minimal|low|medium|high|xhigh>.",
            )
            return
        await self._send(chat_id, await self._apply_effort_setting(chat_id, workspace, effort))


    async def _handle_personality_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            await self._send_fixed_selection(
                chat_id,
                user_id,
                "Select a personality:",
                "personality",
                PERSONALITY_CHOICES,
            )
            return
        personality = _personality_value(args)
        if personality is None:
            await self._send(chat_id, "Use /personality <none|friendly|pragmatic>.")
            return
        await self._send(chat_id, await self._apply_personality_setting(chat_id, workspace, personality))


    async def _apply_model_setting(self, chat_id: str, workspace: Path, model_name: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_settings_update(thread_id=thread_id, model=model_name)
        self._save_thread_setting(chat_id, workspace, "model", model_name)
        return f"Model set to {model_name} for subsequent turns."


    async def _apply_permission_setting(self, chat_id: str, workspace: Path, permission: str | None) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        approval_policy = _permission_profile_approval_policy(permission)
        if approval_policy:
            await self.app_server.thread_settings_update(
                thread_id=thread_id,
                permissions=permission,
                approval_policy=approval_policy,
            )
        else:
            await self.app_server.thread_settings_update(thread_id=thread_id, permissions=permission)
        self._save_thread_setting(chat_id, workspace, "permissions", permission)
        if approval_policy:
            self._save_thread_setting(chat_id, workspace, "approval_policy", approval_policy)
        if permission is None:
            return "Permission profile cleared for subsequent turns."
        if approval_policy:
            return f"Permission profile set to {permission}; approval policy set to {approval_policy} for subsequent turns."
        return f"Permission profile set to {permission} for subsequent turns."


    async def _apply_approval_policy_setting(self, chat_id: str, workspace: Path, policy: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_settings_update(thread_id=thread_id, approval_policy=policy)
        self._save_thread_setting(chat_id, workspace, "approval_policy", policy)
        return f"Approval policy set to {policy} for subsequent turns."


    async def _apply_mode_setting(self, chat_id: str, workspace: Path, args: str) -> str:
        result = await self.app_server.collaboration_mode_list()
        mode = _find_named_item(_result_items(result), args)
        if mode is None:
            return f"Collaboration mode not found: {args}"
        mode_key = self._mode_storage_key(mode.get("mode") or mode.get("name") or args)
        payload = self._collaboration_mode_payload(
            mode,
            model=self._thread_mode_setting(chat_id, workspace, "model", mode_name=mode_key),
            effort=self._thread_mode_setting(chat_id, workspace, "effort", mode_name=mode_key),
        )
        if payload is None:
            payload = self._collaboration_mode_payload(
                mode,
                model=await self._default_model_setting(),
                effort=self._thread_mode_setting(chat_id, workspace, "effort", mode_name=mode_key),
            )
        if payload is None:
            return f"Collaboration mode cannot be applied: {args}"
        existing_thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        self._save_thread_active_mode(chat_id, workspace, mode_key)
        thread_id = await self._ensure_thread(chat_id, workspace, active_mode_payload=payload)
        if existing_thread_id and existing_thread_id == thread_id:
            await self.app_server.thread_settings_update(thread_id=thread_id, collaboration_mode=payload)
        return f"Collaboration mode set to {args} for subsequent turns."


    async def _apply_effort_setting(self, chat_id: str, workspace: Path, effort: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_settings_update(thread_id=thread_id, effort=effort)
        self._save_thread_setting(chat_id, workspace, "effort", effort)
        return f"Reasoning effort set to {effort} for subsequent turns."


    async def _apply_personality_setting(self, chat_id: str, workspace: Path, personality: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_settings_update(thread_id=thread_id, personality=personality)
        self._save_thread_setting(chat_id, workspace, "personality", personality)
        return f"Personality set to {personality} for subsequent turns."


    async def _apply_memory_mode_setting(self, chat_id: str, workspace: Path, mode: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_memory_mode_set(thread_id=thread_id, mode=mode)
        self._save_thread_setting(chat_id, workspace, "memory_mode", mode)
        return f"Memory mode set to {mode}."


    async def _apply_model_effort_setting(self, chat_id: str, workspace: Path, model_name: str, effort: str) -> str:
        thread_id = await self._ensure_thread(chat_id, workspace)
        await self.app_server.thread_settings_update(thread_id=thread_id, model=model_name, effort=effort)
        self._save_thread_setting(chat_id, workspace, "model", model_name)
        self._save_thread_setting(chat_id, workspace, "effort", effort)
        return f"Model set to {model_name}; reasoning effort set to {effort} for subsequent turns."


    async def _send_model_selection(self, chat_id: str, user_id: str, workspace: Path) -> None:
        result = await self.app_server.model_list()
        current_model = self._thread_setting(chat_id, workspace, "model")
        options = _model_selection_options(result, current_model=current_model)
        if not options:
            await self._send(chat_id, "No models found.")
            return
        await self._send_selection(chat_id, user_id, "Select Model and Effort:", options)


    async def _send_model_effort_selection(
        self,
        chat_id: str,
        user_id: str,
        workspace: Path,
        model_name: str,
        model: dict[str, Any],
    ) -> bool:
        current_model = self._thread_setting(chat_id, workspace, "model")
        current_effort = self._thread_setting(chat_id, workspace, "effort") if current_model == model_name else None
        options = _model_reasoning_effort_options(model_name, model, current_effort=current_effort)
        if not options:
            return False
        await self._send_selection(chat_id, user_id, f"Select reasoning level for {model_name}:", options)
        return True


    async def _send_permissions_selection(self, chat_id: str, user_id: str, workspace: Path) -> None:
        result = await self.app_server.permission_profile_list(cwd=str(workspace))
        profiles = _result_items(result)
        options: list[tuple[str, str, Any]] = []
        for label, aliases, fallback in CLI_PERMISSION_CHOICES:
            options.append((label, "permission", _resolve_permission_profile(profiles, aliases, fallback)))
        await self._send_selection(chat_id, user_id, "Select a permission profile:", options)


    async def _send_mode_selection(self, chat_id: str, user_id: str) -> None:
        result = await self.app_server.collaboration_mode_list()
        options = [
            (name, "mode", name)
            for name in _mode_selection_values(result)
        ]
        if not options:
            await self._send(chat_id, "No collaboration modes found.")
            return
        await self._send_selection(chat_id, user_id, "Select a collaboration mode:", options)


    async def _send_fixed_selection(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        action: str,
        choices: list[tuple[str, str]],
    ) -> None:
        await self._send_selection(
            chat_id,
            user_id,
            text,
            [(label, action, value) for label, value in choices],
        )


    async def _send_selection(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        options: list[tuple[str, str, Any]],
    ) -> None:
        group_id = secrets.token_urlsafe(8)
        expires_at = _format_iso(self.access.now_fn() + timedelta(seconds=self.settings.approval_timeout_seconds))
        pending = self.store.load_pending_selections()
        keyboard: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for label, action, value in [*options, ("Cancel", "cancel", None)]:
            token = secrets.token_urlsafe(8)
            pending[token] = {
                "chat_id": str(chat_id),
                "user_id": str(user_id),
                "action": action,
                "value": value,
                "group_id": group_id,
                "expires_at": expires_at,
            }
            button = {"text": label, "callback_data": f"select:{token}"}
            if action == "cancel":
                if row:
                    keyboard.append(row)
                keyboard.append([button])
                row = []
                continue
            row.append(button)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        self.store.save_pending_selections(pending)
        sent = await self._send(chat_id, text, reply_markup={"inline_keyboard": keyboard})
        if sent:
            pending = self.store.load_pending_selections()
            message_id = sent[0].get("message_id")
            for record in pending.values():
                if isinstance(record, dict) and record.get("group_id") == group_id:
                    record["message_id"] = message_id
            self.store.save_pending_selections(pending)


    async def _handle_goal_command(self, chat_id: str, workspace: Path, args: str) -> None:
        thread_id = await self._ensure_thread(chat_id, workspace)
        subcommand, _, rest = args.partition(" ")
        if not args:
            await self._send(chat_id, _format_goal(await self.app_server.thread_goal_get(thread_id=thread_id)))
            return
        if subcommand == "set":
            objective = rest.strip()
            if not objective:
                await self._send(chat_id, "Use /goal set <text>.")
                return
            result = await self.app_server.thread_goal_set(
                thread_id=thread_id,
                objective=objective,
                status="active",
            )
            await self._send(chat_id, _format_goal(result))
            return
        if subcommand == "clear":
            await self.app_server.thread_goal_clear(thread_id=thread_id)
            await self._send(chat_id, "Goal cleared.")
            return
        await self._send(chat_id, "Use /goal, /goal set <text>, or /goal clear.")


    async def _handle_memories_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        if not args:
            current = self._thread_setting(chat_id, workspace, "memory_mode") or "enabled"
            choices = [(label, value) for label, value in MEMORY_MODE_CHOICES]
            await self._send_fixed_selection(chat_id, user_id, f"Memory mode: {current}", "memory", choices)
            return
        mode = _memory_mode_value(args)
        if mode is None:
            await self._send(chat_id, "Use /memories <enabled|disabled>.")
            return
        await self._send(chat_id, await self._apply_memory_mode_setting(chat_id, workspace, mode))


    async def _handle_skills_command(self, chat_id: str, user_id: str, workspace: Path, args: str) -> None:
        words = args.split()
        if len(words) >= 2 and words[0].lower() in {"enable", "disable"}:
            enabled = words[0].lower() == "enable"
            selector = " ".join(words[1:]).strip()
            result = await self.app_server.skills_list(cwds=[str(workspace)], force_reload=False)
            skill = _find_skill(result, selector)
            if skill is None:
                await self._send(chat_id, f"Skill not found: {selector}")
                return
            path = _skill_path(skill)
            await self.app_server.skills_config_write(
                enabled=enabled,
                name=None if path else str(skill.get("name") or selector),
                path=path,
            )
            await self._send(chat_id, f"Skill {'enabled' if enabled else 'disabled'}: {skill.get('name') or selector}")
            return
        result = await self.app_server.skills_list(cwds=[str(workspace)], force_reload=False)
        options: list[tuple[str, str, Any]] = []
        for group in _iter_skill_groups(result):
            for skill in group.get("skills", []):
                if not isinstance(skill, dict):
                    continue
                name = str(skill.get("name") or "")
                if not name:
                    continue
                enabled = bool(skill.get("enabled", True))
                action = "disable" if enabled else "enable"
                options.append(
                    (
                        f"{name}: {'enabled' if enabled else 'disabled'}",
                        "skill",
                        {"name": name, "path": _skill_path(skill), "enabled": not enabled},
                    )
                )
        text = _format_skills(result)
        if options:
            await self._send_selection(chat_id, user_id, text, options[:20])
        else:
            await self._send(chat_id, text)


    async def _send_experimental_selection(self, chat_id: str, user_id: str, result: Any) -> None:
        options: list[tuple[str, str, Any]] = []
        for item in _result_items(result):
            name = _feature_name(item)
            if not name:
                continue
            if name not in SETTABLE_EXPERIMENTAL_FEATURES:
                continue
            enabled = bool(item.get("enabled"))
            options.append(
                (
                    f"{_feature_label(item)}: {'enabled' if enabled else 'disabled'}",
                    "experimental",
                    {"name": name, "enabled": not enabled},
                )
            )
        text = _format_features(result)
        if options:
            await self._send_selection(chat_id, user_id, text, options[:20])
        else:
            await self._send(chat_id, text)


    async def _handle_approve_command(self, chat_id: str, user_id: str, workspace: Path) -> None:
        thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        denials = list(self.guardian_denials_by_thread.get(thread_id, [])) if thread_id else []
        if not denials:
            await self._send(chat_id, "No recent denied auto-review actions for the active thread.")
            return
        options = [
            (
                _guardian_denial_label(event),
                "guardian_approve",
                {"thread_id": thread_id, "event": event},
            )
            for event in denials[-10:]
        ]
        await self._send_selection(chat_id, user_id, _format_guardian_denials(denials), options)


    async def _handle_loaded_threads_command(
        self,
        chat_id: str,
        user_id: str,
        workspace: Path,
        *,
        subagents_only: bool,
    ) -> None:
        result = await self.app_server.thread_loaded_list(limit=25)
        thread_ids = [str(item) for item in _result_items_or_scalars(result) if str(item)]
        records: list[dict[str, Any]] = []
        for thread_id in thread_ids[:25]:
            try:
                read_result = await self.app_server.thread_read(thread_id=thread_id, include_turns=False)
            except JsonRpcError:
                continue
            thread = read_result.get("thread") if isinstance(read_result, dict) else None
            if isinstance(thread, dict):
                if not subagents_only or _thread_is_subagent(thread):
                    records.append(thread)
        active_thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        options = [
            (
                _loaded_thread_label(thread, active_thread_id=active_thread_id),
                "thread_select",
                {"thread_id": str(thread.get("id") or ""), "cwd": str(thread.get("cwd") or workspace)},
            )
            for thread in records
            if thread.get("id")
        ]
        text = _format_loaded_threads(records, active_thread_id=active_thread_id, subagents_only=subagents_only)
        if options:
            await self._send_selection(chat_id, user_id, text, options)
        else:
            await self._send(chat_id, text)


    async def _handle_ps_command(self, chat_id: str, workspace: Path) -> None:
        record = self._thread_record(chat_id, workspace)
        thread_id = str(record.get("thread_id") or "")
        lines = _live_process_lines(self.turns.values(), chat_id=chat_id, thread_id=thread_id)
        if thread_id:
            try:
                result = await self.app_server.thread_read(thread_id=thread_id, include_turns=True)
                lines.extend(_thread_process_lines(result))
            except (AttributeError, JsonRpcError):
                pass
        await self._send(chat_id, _format_lines("Processes", _dedupe_lines(lines), "No active commands or processes."))


    async def _handle_stop_command(self, chat_id: str, user_id: str, workspace: Path) -> None:
        thread_id = str(self._thread_record(chat_id, workspace).get("thread_id") or "")
        if not thread_id:
            await self._send(chat_id, "No active thread for background terminal cleanup.")
            return
        await self._send_selection(
            chat_id,
            user_id,
            f"Stop background terminals for thread {thread_id}?",
            [("Stop background terminals", "stop", thread_id)],
        )


    async def _interrupt_active_turn(self, chat_id: str) -> None:
        context = self._active_turn_context(chat_id)
        if context is None:
            await self._send(chat_id, "No active turn to cancel.")
            return
        try:
            await self.app_server.turn_interrupt(thread_id=context.thread_id, turn_id=context.turn_id)
        except JsonRpcError as exc:
            message = str(exc)
            if "no active turn to interrupt" in message or "expected active turn id" in message:
                context.completed = True
                self._stop_typing_indicator(context.turn_id)
                await self._send(chat_id, "No active turn to cancel.")
                return
            raise
        await self._send(chat_id, "Cancel requested.")


    async def _steer_active_turn(self, chat_id: str, args: str) -> None:
        if not args:
            await self._send(chat_id, "Use /steer <text>.")
            return
        context = self._active_turn_context(chat_id)
        if context is None:
            await self._send(chat_id, "No active turn to steer.")
            return
        await self.app_server.turn_steer(
            thread_id=context.thread_id,
            expected_turn_id=context.turn_id,
            input_items=_input_items(args, []),
        )
        await self._send(chat_id, "Steer request sent.")


    async def _handle_diff_command(self, chat_id: str) -> None:
        workspace = self._active_workspace(chat_id)
        await self._send(chat_id, await _git_diff_with_untracked(workspace))


    async def _handle_workspace_command(self, chat_id: str, args: str) -> None:
        subcommand, _, rest = args.partition(" ")
        if subcommand == "list":
            roots = "\n".join(str(root) for root in self.settings.allowed_roots)
            await self._send(chat_id, f"Allowed roots:\n{roots}")
            return
        if subcommand == "set":
            try:
                workspace = resolve_workspace(self.settings, rest)
            except TelegramSettingsError as exc:
                await self._send(chat_id, str(exc))
                return
            chats = self.store.load_chats()
            key = TelegramStateStore.chat_key(chat_id)
            chat_state = dict(chats.get(key) or {})
            chat_state["active_workspace"] = str(workspace)
            chats[key] = chat_state
            self.store.save_chats(chats)
            await self._send(chat_id, f"Workspace set: {workspace}")
            return
        await self._send(chat_id, "Use /workspace list or /workspace set <path>.")


    async def _handle_searchcwd_command(self, chat_id: str, args: str) -> None:
        needle = args.strip().lower()
        matches: list[str] = []
        for root in self.settings.allowed_roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_dir() and (not needle or needle in child.name.lower()):
                    matches.append(str(child))
                    if len(matches) >= 10:
                        break
            if len(matches) >= 10:
                break
        await self._send(chat_id, "\n".join(matches) if matches else "No matching workspaces found.")


    async def _handle_threads_command(self, chat_id: str, args: str = "") -> None:
        workspace = self._active_workspace(chat_id)
        try:
            result = await self.app_server.thread_list(
            cwd=str(workspace),
            search_term=args.strip() or None,
            limit=10,
        )
        except (AttributeError, JsonRpcError):
            await self._send_local_thread_records(chat_id)
            return
        fallbacks = await self._thread_title_fallbacks(chat_id, result)
        await self._send(chat_id, _format_threads(result, fallback_titles=fallbacks))


    async def _send_local_thread_records(self, chat_id: str) -> None:
        prefix = f"chat_id:{chat_id}|cwd:"
        rows = [
            f"{value.get('thread_id')} - {value.get('workspace')}"
            for key, value in self.store.load_threads().items()
            if key.startswith(prefix) and isinstance(value, dict)
        ]
        await self._send(chat_id, "\n".join(rows) if rows else "No local thread records.")


    def _reset_chat_state(self, chat_id: str) -> None:
        chats = self.store.load_chats()
        chats.pop(TelegramStateStore.chat_key(chat_id), None)
        self.store.save_chats(chats)
        threads = self.store.load_threads()
        prefix = f"chat_id:{chat_id}|cwd:"
        for key in list(threads):
            if key.startswith(prefix):
                threads.pop(key, None)
        self.store.save_threads(threads)


    def _clear_chat_mapping(self, chat_id: str) -> None:
        threads = self.store.load_threads()
        prefix = f"chat_id:{chat_id}|cwd:"
        now = _format_iso(self.access.now_fn())
        for key in list(threads):
            if key.startswith(prefix):
                raw_record = threads.get(key)
                record = dict(raw_record) if isinstance(raw_record, dict) else {}
                settings = self._normalized_thread_settings(record.get("settings"))
                if settings:
                    workspace = str(record.get("workspace") or key.rsplit("|cwd:", 1)[-1])
                    threads[key] = {
                        "workspace": workspace,
                        "updated_at": now,
                        "settings": settings,
                    }
                else:
                    threads.pop(key, None)
        self.store.save_threads(threads)


    async def _handle_thread_command(
        self,
        chat_id: str,
        user_id: str,
        command: TelegramCommand,
    ) -> None:
        workspace = self._active_workspace(chat_id)
        if command.name == "new":
            await self._start_new_thread(chat_id, workspace)
            await self._send(chat_id, f"Started a new Codex thread for {workspace}.")
            return
        if command.name == "resume":
            await self._send_resume_options(chat_id, workspace)
            return
        if command.name == "fork":
            forked_thread_id = await self._fork_current_thread(chat_id, workspace)
            await self._start_turn(
                chat_id=chat_id,
                user_id=user_id,
                message_id=None,
                text="Fork this current task into a fresh thread and continue from the current goal.",
                attachments=[],
                thread_id_override=forked_thread_id,
            )
            return
        if command.name in {"side", "btw"}:
            save_mapping = not bool(command.args)
            forked_thread_id = await self._fork_current_thread(
                chat_id,
                workspace,
                ephemeral=True,
                save_mapping=save_mapping,
            )
            if not forked_thread_id:
                await self._send(chat_id, "Unable to create ephemeral fork.")
                return
            if command.args:
                await self._start_turn(
                    chat_id=chat_id,
                    user_id=user_id,
                    message_id=None,
                    text=command.args,
                    attachments=[],
                    thread_id_override=forked_thread_id,
                )
                return
            await self._send(
                chat_id,
                f"Switched to ephemeral thread: {forked_thread_id}\nUse /threads or /resume to return to another thread.",
            )
            return
        if command.name == "rollback":
            if await self._rollback_current_thread(chat_id, workspace, command.args):
                await self._send(chat_id, "Thread history rolled back.")
            return
        if command.name == "archive":
            if await self._archive_current_thread(chat_id, workspace):
                await self._send(chat_id, "Thread archived.")
            return
        if command.name == "unarchive":
            if await self._unarchive_current_thread(chat_id, workspace):
                await self._send(chat_id, "Thread unarchived.")
            return

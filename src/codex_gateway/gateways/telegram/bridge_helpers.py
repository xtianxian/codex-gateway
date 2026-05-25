from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ...backends.codex_app_server.client import JsonRpcError
from .commands import TelegramCommand
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
from .types import OutputAttachment, TurnContext


def sanitize_text(text: str) -> str:
    sanitized = text
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _input_items(text: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if text:
        items.append({"type": "text", "text": text})
    for attachment in attachments:
        if _is_image_attachment(attachment):
            items.append({"type": "localImage", "path": attachment["path"], "detail": "original"})
        items.append(
            {
                "type": "text",
                "text": (
                    f"Telegram attachment: {attachment['filename']}\n"
                    f"Local path: {attachment['path']}\n"
                    f"MIME type: {attachment['mime_type']}\n"
                    f"Size: {attachment['size_bytes']} bytes"
                ),
            }
        )
    return items or [{"type": "text", "text": ""}]


def _assistant_text(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type not in {"agentMessage", "agent_message", "assistant_message", "message"}:
        return ""
    if isinstance(item.get("text"), str):
        return str(item["text"])
    content = item.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _output_attachment(item: dict[str, Any]) -> OutputAttachment | None:
    item_type = str(item.get("type") or "")
    if item_type not in {"imageGeneration", "image_generation_call", "image_generation_end"}:
        return None
    path = _first_text(item.get("savedPath"), item.get("saved_path"))
    identity = _first_text(item.get("id"), item.get("call_id"))
    content_type = "image/png"
    if path:
        resolved = Path(path).expanduser().resolve(strict=False)
        return OutputAttachment(
            key=identity or str(resolved),
            filename=_safe_filename(resolved.name or "generated-image.png"),
            caption=_output_attachment_caption(item),
            content_type=mimetypes.guess_type(resolved.name)[0] or content_type,
            path=resolved,
        )

    decoded = _decode_image_result(item.get("result"))
    if decoded is None:
        return None
    data, decoded_content_type = decoded
    content_type = decoded_content_type or content_type
    digest = hashlib.sha256(data).hexdigest()
    filename = _first_text(item.get("filename"), item.get("fileName")) or _generated_image_filename(digest, content_type)
    return OutputAttachment(
        key=identity or f"image:{digest}",
        filename=_safe_filename(filename),
        caption=_output_attachment_caption(item),
        content_type=content_type,
        data=data,
    )


def _output_attachment_caption(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type in {"imageGeneration", "image_generation_call", "image_generation_end"}:
        return "Generated image"
    return "Attachment"


def _decode_image_result(value: Any) -> tuple[bytes, str | None] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    content_type: str | None = None
    if text.startswith("data:"):
        header, separator, payload = text.partition(",")
        if separator != "," or ";base64" not in header:
            return None
        content_type = header.removeprefix("data:").split(";", 1)[0] or None
        text = payload
    try:
        data = base64.b64decode(text, validate=True)
    except binascii.Error:
        try:
            data = base64.b64decode(text)
        except binascii.Error:
            return None
    if not data:
        return None
    return data, content_type or _image_content_type(data)


def _image_content_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _generated_image_filename(digest: str, content_type: str) -> str:
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".png")
    return f"generated-image-{digest[:12]}{extension}"


def _extract_id(result: dict[str, Any], name: str) -> str | None:
    nested = result.get(name)
    if isinstance(nested, dict) and nested.get("id"):
        return str(nested["id"])
    if result.get(f"{name}Id"):
        return str(result[f"{name}Id"])
    return None


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _tool_file_path(arguments: dict[str, Any], workspace: Path) -> Path | None:
    raw_path = _first_text(arguments.get("path"))
    if raw_path is None:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve(strict=False)


def _tool_name(value: str) -> str:
    aliases = {
        "telegram.reply": "telegram_reply",
        "telegram.react": "telegram_react",
        "telegram.edit_message": "telegram_edit_message",
        "telegram.download_attachment": "telegram_download_attachment",
        "telegram.send_photo": "telegram_send_photo",
        "telegram.send_video": "telegram_send_video",
        "telegram.send_document": "telegram_send_document",
    }
    return aliases.get(value, value)


def _message_id(message: dict[str, Any]) -> int | None:
    value = message.get("message_id")
    return int(value) if isinstance(value, int) else None


def _pairing_guidance_text(code: str) -> str:
    return (
        "This Telegram user is not paired.\n\n"
        "Run this in your project terminal:\n"
        f"uv run codex-gateway telegram access pair {code}\n\n"
        "Then send another message here."
    )


def _start_pairing_text() -> str:
    return "This Telegram user is not paired.\n\nSend /start to get the local pairing command."


def _unauthorized_user_text() -> str:
    return "This Telegram user is not authorized for this gateway."


def _turn_id(params: dict[str, Any]) -> str | None:
    turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
    value = params.get("turnId") or params.get("turn_id") or turn.get("id")
    return str(value) if value else None


def _thread_id(params: dict[str, Any]) -> str:
    return str(params.get("threadId") or params.get("thread_id") or "")


def _item(params: dict[str, Any]) -> dict[str, Any]:
    item = params.get("item")
    return item if isinstance(item, dict) else {}


def _command_summary(item: dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, str) and command:
        return command
    actions = item.get("commandActions")
    if isinstance(actions, list) and actions:
        return str(actions[0])
    return "command"


def _file_change_summary(item: dict[str, Any]) -> str:
    paths: list[str] = []
    for change in item.get("changes") or []:
        if isinstance(change, dict):
            path = change.get("path") or change.get("displayPath") or change.get("file")
            if path:
                paths.append(str(path))
    if item.get("path"):
        paths.append(str(item["path"]))
    return ", ".join(paths) if paths else "file"


def _approval_text(params: dict[str, Any], workspace: Path) -> str:
    item = _item(params)
    lines = ["Approval requested"]
    cwd = params.get("cwd") or item.get("cwd") or str(workspace)
    lines.append(f"cwd: {cwd}")
    command = params.get("command") or item.get("command")
    if command:
        lines.append(f"command: {_command_summary({'command': command})}")
    else:
        lines.append("action: requested action")
    if params.get("reason"):
        lines.append(f"reason: {params['reason']}")
    if params.get("grantRoot"):
        lines.append(f"grant root: {params['grantRoot']}")
    if params.get("networkApprovalContext"):
        lines.append(f"network: {json.dumps(params['networkApprovalContext'], sort_keys=True)}")
    additional = params.get("additionalPermissions")
    if additional:
        lines.append(f"additional permissions: {json.dumps(additional, sort_keys=True)}")
    decisions = params.get("availableDecisions")
    if decisions:
        safe_decisions = [decision for decision in decisions if decision != "acceptForSession"]
        lines.append(f"available decisions: {', '.join(str(decision) for decision in safe_decisions)}")
    return "\n".join(lines)


def _permissions_approval_text(params: dict[str, Any], workspace: Path) -> str:
    lines = ["Permission approval requested"]
    lines.append(f"cwd: {params.get('cwd') or str(workspace)}")
    if params.get("reason"):
        lines.append(f"reason: {params['reason']}")
    permissions = params.get("permissions")
    if isinstance(permissions, dict):
        file_system = permissions.get("fileSystem")
        if file_system is not None:
            lines.append(f"file system: {json.dumps(file_system, sort_keys=True)}")
        network = permissions.get("network")
        if network is not None:
            lines.append(f"network: {json.dumps(network, sort_keys=True)}")
        if file_system is None and network is None:
            lines.append(f"permissions: {json.dumps(permissions, sort_keys=True)}")
    return "\n".join(lines)


def _current_user_input_question(record: dict[str, Any]) -> dict[str, Any] | None:
    questions = [item for item in record.get("questions") or [] if isinstance(item, dict)]
    try:
        index = int(record.get("question_index") or 0)
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= len(questions):
        return None
    return questions[index]


def _question_options(question: dict[str, Any]) -> list[dict[str, str]]:
    options = question.get("options")
    if not isinstance(options, list):
        return []
    normalized: list[dict[str, str]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "")
        if not label:
            continue
        normalized.append(
            {
                "label": label,
                "description": str(option.get("description") or ""),
            }
        )
    return normalized


def _tool_user_input_text(question: dict[str, Any], *, waiting_for_text: bool) -> str:
    lines = ["Input requested"]
    header = str(question.get("header") or "")
    if header:
        lines.append(header)
    prompt = str(question.get("question") or "")
    if prompt:
        lines.append(prompt)
    for option in _question_options(question):
        detail = f"- {option['label']}"
        if option.get("description"):
            detail += f": {option['description']}"
        lines.append(detail)
    if waiting_for_text:
        lines.append("Send your answer as a message.")
    return "\n".join(lines)


def _mcp_elicitation_text(params: dict[str, Any]) -> str:
    lines = ["MCP input requested"]
    server_name = params.get("serverName")
    if server_name:
        lines.append(f"server: {server_name}")
    message = params.get("message")
    if message:
        lines.append(str(message))
    mode = params.get("mode")
    if mode:
        lines.append(f"mode: {mode}")
    url = params.get("url")
    if url:
        lines.append(f"url: {url}")
    fields = _mcp_elicitation_field_labels(params.get("requestedSchema"))
    if fields:
        lines.append(f"fields: {', '.join(fields)}")
    return "\n".join(lines)


def _mcp_elicitation_field_labels(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    labels: list[str] = []
    for name, spec in properties.items():
        if isinstance(spec, dict):
            title = spec.get("title")
            if isinstance(title, str) and title:
                labels.append(title)
                continue
        labels.append(str(name))
    return labels


def _action_past_tense(action: str) -> str:
    if action == "accept":
        return "accepted"
    if action == "decline":
        return "declined"
    if action == "cancel":
        return "cancelled"
    return f"{action}ed"


def _params_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _params_shape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [f"{len(value)} items"]
    if value is None:
        return "null"
    return type(value).__name__


def _skill_names(text: str) -> list[str]:
    return sorted(set(re.findall(r"\$([A-Za-z0-9_-]+)", text)))


def _iter_skill_groups(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(result.get("skills"), list):
            return [result]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _skill_path(skill: dict[str, Any]) -> str | None:
    for key in ("path", "skillPath", "sourcePath", "filePath"):
        value = skill.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _result_items(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _result_items_or_scalars(result: Any) -> list[Any]:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return data
    if isinstance(result, list):
        return result
    return []


def _find_named_item(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    needle = name.casefold()
    for item in items:
        for key in ("name", "id", "mode", "model", "displayName"):
            value = item.get(key)
            if isinstance(value, str) and value.casefold() == needle:
                return item
    return None


def _find_model(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return _find_named_item(items, name)


def _find_permission_profile(items: list[dict[str, Any]], name: str) -> str | None:
    needle = name.casefold()
    normalized_needle = _permission_lookup_key(name)
    for item in items:
        value = _permission_profile_value(item)
        if not value:
            continue
        candidates = {
            value.casefold(),
            _permission_lookup_key(value),
            _permission_profile_label(value).casefold(),
            _permission_lookup_key(_permission_profile_label(value)),
        }
        if needle in candidates or normalized_needle in candidates:
            return value
    return None


def _resolve_permission_profile(items: list[dict[str, Any]], aliases: Iterable[str], fallback: str) -> str:
    for alias in aliases:
        resolved = _find_permission_profile(items, alias)
        if resolved is not None:
            return resolved
    return fallback


def _cli_permission_choice(name: str) -> tuple[tuple[str, ...], str] | None:
    needle = _permission_lookup_key(name)
    for label, aliases, fallback in CLI_PERMISSION_CHOICES:
        candidates = {_permission_lookup_key(label), _permission_lookup_key(fallback)}
        candidates.update(_permission_lookup_key(alias) for alias in aliases)
        if needle in candidates:
            return aliases, fallback
    return None


def _find_skill(result: Any, selector: str) -> dict[str, Any] | None:
    needle = selector.casefold()
    for group in _iter_skill_groups(result):
        for skill in group.get("skills", []):
            if not isinstance(skill, dict):
                continue
            candidates = [
                _first_text(skill.get("name")),
                _skill_path(skill),
                _first_text(skill.get("displayName")),
            ]
            if any(value and value.casefold() == needle for value in candidates):
                return skill
    return None


def _model_config_value(item: dict[str, Any]) -> str | None:
    for key in ("model", "id", "displayName"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _model_selection_options(result: Any, *, current_model: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    options: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for item in _result_items(result):
        value = _model_config_value(item)
        if not value or value in seen:
            continue
        label = value
        if current_model == value:
            label += " (current)"
        payload = dict(item)
        payload["model"] = value
        options.append((label, "model", payload))
        seen.add(value)
    return options


def _split_model_effort_args(args: str) -> tuple[str, str | None]:
    stripped = args.strip()
    lowered = stripped.casefold()
    for alias, effort in sorted(_reasoning_effort_aliases().items(), key=lambda item: len(item[0]), reverse=True):
        if lowered == alias:
            break
        suffix = f" {alias}"
        if lowered.endswith(suffix):
            model = stripped[: -len(alias)].strip()
            if model:
                return model, effort
    return stripped, None


def _reasoning_effort_aliases() -> dict[str, str]:
    aliases = {
        "extra high": "xhigh",
        "extra-high": "xhigh",
        "extra_high": "xhigh",
    }
    for label, value in EFFORT_CHOICES:
        aliases[label.casefold()] = value
        aliases[value.casefold()] = value
    return aliases


def _reasoning_effort_value(value: str | None) -> str | None:
    if value is None:
        return None
    return _reasoning_effort_aliases().get(value.strip().casefold())


def _personality_value(value: str | None) -> str | None:
    if value is None:
        return None
    needle = value.strip().casefold()
    for label, personality in PERSONALITY_CHOICES:
        if needle in {label.casefold(), personality.casefold()}:
            return personality
    return None


def _memory_mode_value(value: str | None) -> str | None:
    if value is None:
        return None
    needle = value.strip().casefold()
    aliases = {
        "on": "enabled",
        "enable": "enabled",
        "enabled": "enabled",
        "off": "disabled",
        "disable": "disabled",
        "disabled": "disabled",
    }
    return aliases.get(needle)


def _reasoning_effort_label(value: str) -> str:
    for label, effort in EFFORT_CHOICES:
        if effort == value:
            return label
    return value


def _model_supported_reasoning_efforts(model: dict[str, Any]) -> list[str]:
    supported = model.get("supportedReasoningEfforts")
    if not isinstance(supported, list):
        return [value for _, value in EFFORT_CHOICES]
    values: list[str] = []
    seen: set[str] = set()
    for item in supported:
        if not isinstance(item, dict):
            continue
        effort = _reasoning_effort_value(_first_text(item.get("reasoningEffort")))
        if effort and effort not in seen:
            values.append(effort)
            seen.add(effort)
    return values


def _model_default_reasoning_effort(model: dict[str, Any]) -> str | None:
    return _reasoning_effort_value(_first_text(model.get("defaultReasoningEffort")))


def _model_supports_reasoning_effort(model: dict[str, Any], effort: str) -> bool:
    return effort in _model_supported_reasoning_efforts(model)


def _model_reasoning_effort_options(
    model_name: str,
    model: dict[str, Any],
    *,
    current_effort: str | None = None,
) -> list[tuple[str, str, dict[str, str]]]:
    supported = _model_supported_reasoning_efforts(model)
    default_effort = _model_default_reasoning_effort(model)
    options: list[tuple[str, str, dict[str, str]]] = []
    for effort in supported:
        label = _reasoning_effort_label(effort)
        if current_effort == effort:
            label += " (current)"
        elif current_effort is None and default_effort == effort:
            label += " (default)"
        options.append((label, "model_effort", {"model": model_name, "effort": effort}))
    return options


def _unsupported_model_effort_text(model_name: str, model: dict[str, Any]) -> str:
    supported = _model_supported_reasoning_efforts(model)
    if not supported:
        return f"{model_name} does not expose selectable reasoning efforts."
    values = "|".join(supported)
    return f"Use /model {model_name} <{values}>."


def _permission_profile_value(item: dict[str, Any]) -> str | None:
    for key in ("id", "name", "profile"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _permission_profile_label(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).lower()).strip("-")
    common = {
        "read-only": "Read Only",
        "auto-review": "Auto-review",
        "full-access": "Full Access",
    }
    if normalized in common:
        return common[normalized]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("-", " ").replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:].lower() for word in words) or value


def _permission_lookup_key(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "-",
        re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).lower(),
    ).strip("-")


def _permission_profile_approval_policy(value: str | None) -> str | None:
    if value is None:
        return None
    key = _permission_lookup_key(value)
    if key in {"danger-full-access", "full-access"}:
        return "never"
    if key in {"read-only", "workspace", "workspace-write", "default", "auto-review"}:
        return "on-request"
    return None


def _mode_selection_values(result: Any) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in _result_items(result):
        value = item.get("name") or item.get("mode")
        if isinstance(value, str) and value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _mode_display_name(mode: dict[str, Any], fallback: str) -> str:
    for key in ("name", "mode", "displayName"):
        value = mode.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _format_models(result: Any) -> str:
    lines: list[str] = []
    for item in _result_items(result):
        display = str(item.get("displayName") or item.get("model") or item.get("id") or "model")
        model_id = str(item.get("model") or item.get("id") or "")
        suffix = " default" if item.get("isDefault") else ""
        line = f"- {display}"
        if model_id and model_id != display:
            line += f" ({model_id})"
        line += suffix
        description = item.get("description")
        if description:
            line += f"\n  {description}"
        lines.append(line)
    return _format_lines("Models", lines, "No models found.")


def _format_features(result: Any) -> str:
    lines = []
    for item in _result_items(result):
        name = _feature_label(item)
        state = "enabled" if item.get("enabled") else "disabled"
        stage = f" {item['stage']}" if item.get("stage") else ""
        lines.append(f"- {name}: {state}{stage}")
    return _format_lines("Features", lines, "No features found.")


def _feature_name(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("name"), item.get("id"), item.get("key"))


def _feature_label(item: dict[str, Any]) -> str:
    return _first_text(item.get("displayName"), item.get("name"), item.get("id")) or "feature"


def _format_skills(result: Any) -> str:
    lines = []
    for group in _iter_skill_groups(result):
        for skill in group.get("skills", []):
            if not isinstance(skill, dict):
                continue
            name = skill.get("name") or "skill"
            state = "enabled" if skill.get("enabled", True) else "disabled"
            path = _skill_path(skill)
            lines.append(f"- {name}: {state}" + (f" - {path}" if path else ""))
    return _format_lines("Skills", lines, "No skills found.")


def _format_apps(result: Any) -> str:
    lines = []
    for item in _result_items(result):
        name = item.get("name") or item.get("id") or "app"
        state = "enabled" if item.get("isEnabled", True) else "disabled"
        line = f"- {name}: {state}"
        if item.get("description"):
            line += f" - {item['description']}"
        lines.append(line)
    return _format_lines("Apps", lines, "No apps found.")


def _format_plugins(result: Any) -> str:
    if not isinstance(result, dict):
        return "Plugins unavailable."
    lines: list[str] = []
    for marketplace in result.get("marketplaces") or []:
        if not isinstance(marketplace, dict):
            continue
        marketplace_name = marketplace.get("name") or "marketplace"
        for plugin in marketplace.get("plugins") or []:
            if not isinstance(plugin, dict):
                continue
            state = "installed" if plugin.get("installed") else "available"
            if not plugin.get("enabled", True):
                state += ", disabled"
            interface = plugin.get("interface") if isinstance(plugin.get("interface"), dict) else {}
            description = interface.get("shortDescription") or interface.get("displayName") or ""
            line = f"- {plugin.get('name') or plugin.get('id') or 'plugin'}: {state} ({marketplace_name})"
            if description:
                line += f" - {description}"
            lines.append(line)
    for error in result.get("marketplaceLoadErrors") or []:
        if isinstance(error, dict):
            lines.append(f"- error: {error.get('message') or error}")
    return _format_lines("Plugins", lines, "No plugins found.")


def _format_loaded_threads(
    records: list[dict[str, Any]],
    *,
    active_thread_id: str,
    subagents_only: bool,
) -> str:
    lines = [_loaded_thread_line(thread, active_thread_id=active_thread_id) for thread in records]
    title = "Subagents" if subagents_only else "Loaded agents"
    return _format_lines(title, lines, "No loaded subagents found." if subagents_only else "No loaded agents found.")


def _loaded_thread_line(thread: dict[str, Any], *, active_thread_id: str) -> str:
    marker = "* " if str(thread.get("id") or "") == active_thread_id else ""
    parts = [f"{marker}{thread.get('id') or 'thread'}"]
    status = _thread_status_text(thread.get("status"))
    if status:
        parts.append(status)
    nickname = _first_text(thread.get("agentNickname"), thread.get("agentRole"))
    if nickname:
        parts.append(nickname)
    cwd = _first_text(thread.get("cwd"))
    if cwd:
        parts.append(cwd)
    return " - ".join(parts)


def _loaded_thread_label(thread: dict[str, Any], *, active_thread_id: str) -> str:
    label = _first_text(thread.get("agentNickname"), thread.get("agentRole"), thread.get("preview"), thread.get("id"))
    return label or "thread"


def _thread_status_text(status: Any) -> str:
    if isinstance(status, dict):
        return _first_text(status.get("type")) or json.dumps(status, sort_keys=True)
    return _first_text(status)


def _thread_is_subagent(thread: dict[str, Any]) -> bool:
    if thread.get("agentNickname") or thread.get("agentRole"):
        return True
    source = thread.get("source")
    if isinstance(source, dict):
        return "subAgent" in source or "sub_agent" in source
    return str(source or "").lower() == "subagent"


def _format_guardian_denials(denials: list[dict[str, Any]]) -> str:
    lines = [_guardian_denial_label(event) for event in denials[-10:]]
    return _format_lines("Denied actions", lines, "No recent denied actions.")


def _guardian_denial_label(event: dict[str, Any]) -> str:
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    action_type = _first_text(action.get("type")) or "action"
    if action_type == "command":
        return _first_text(action.get("command")) or "command"
    if action_type == "execve":
        argv = action.get("argv")
        if isinstance(argv, list) and argv:
            return " ".join(str(part) for part in argv)
        return _first_text(action.get("program")) or "execve"
    if action_type == "applyPatch":
        files = action.get("files")
        if isinstance(files, list) and files:
            return "patch " + ", ".join(str(item) for item in files[:3])
        return "apply patch"
    if action_type == "networkAccess":
        return f"network {action.get('target') or action.get('host') or ''}".strip()
    if action_type == "mcpToolCall":
        return f"{action.get('server') or 'mcp'}:{action.get('toolName') or action.get('toolTitle') or 'tool'}"
    return action_type


def _live_process_lines(contexts: Iterable[TurnContext], *, chat_id: str, thread_id: str) -> list[str]:
    lines = []
    for context in contexts:
        if context.completed:
            continue
        if context.chat_id != str(chat_id):
            continue
        if thread_id and context.thread_id != thread_id:
            continue
        lines.append(f"- turn {context.turn_id}: inProgress")
    return lines


def _thread_process_lines(result: Any) -> list[str]:
    thread = result.get("thread") if isinstance(result, dict) else None
    if not isinstance(thread, dict):
        return []
    lines: list[str] = []
    for item in _iter_thread_items(thread):
        line = _process_item_line(item)
        if line:
            lines.append(line)
    return lines


def _iter_thread_items(thread: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for turn in thread.get("turns") or []:
        if isinstance(turn, dict):
            for item in turn.get("items") or []:
                if isinstance(item, dict):
                    items.append(item)
    for item in thread.get("items") or []:
        if isinstance(item, dict):
            items.append(item)
    return items


def _process_item_line(item: dict[str, Any]) -> str | None:
    item_type = str(item.get("type") or "")
    status = _first_text(item.get("status"))
    if status and status not in {"inProgress", "running", "pending"}:
        return None
    if item_type in {"commandExecution", "command_execution"} or item.get("command") or item.get("commandActions"):
        command = _command_summary(item)
        pid = _first_text(item.get("pid"), item.get("processId"), item.get("process_id"))
        suffix = f", pid {pid}" if pid else ""
        return f"- {command}: {status or 'inProgress'}{suffix}"
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
        tool = _first_text(item.get("tool"), item.get("name")) or item_type
        return f"- {tool}: {status or 'inProgress'}"
    return None


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)
    return unique


def _apps_unavailable_error(exc: JsonRpcError) -> bool:
    message = str(exc)
    return "failed to list apps" in message and "403 Forbidden" in message


def _format_account(result: Any) -> str:
    if not isinstance(result, dict):
        return "Account information unavailable."
    account = result.get("account")
    if not isinstance(account, dict):
        if result.get("requiresOpenaiAuth"):
            return "Account: OpenAI authentication required."
        return "Account: not signed in."
    lines = [f"type: {account.get('type') or 'unknown'}"]
    if account.get("email"):
        lines.append(f"email: {account['email']}")
    if account.get("planType"):
        lines.append(f"plan: {account['planType']}")
    return "Account\n" + "\n".join(lines)


def _format_rate_limits(result: Any) -> str:
    if not isinstance(result, dict):
        return "Rate limits unavailable."
    snapshots: list[dict[str, Any]] = []
    if isinstance(result.get("rateLimits"), dict):
        snapshots.append(result["rateLimits"])
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        snapshots.extend(item for item in by_id.values() if isinstance(item, dict))
    lines = [_format_rate_limit_snapshot(item) for item in snapshots]
    lines = [line for line in lines if line]
    return _format_lines("Rate limits", lines, "No rate limits found.")


def _format_rate_limit_snapshot(item: dict[str, Any]) -> str:
    name = str(item.get("limitName") or item.get("limitId") or "default")
    parts = [name]
    for key in ("primary", "secondary"):
        window = item.get(key)
        if isinstance(window, dict) and window.get("usedPercent") is not None:
            parts.append(f"{key} {window['usedPercent']}%")
    if item.get("planType"):
        parts.append(f"plan {item['planType']}")
    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def _format_gateway_status(
    *,
    workspace: Path,
    record: dict[str, Any],
    default_sandbox: str,
    default_approval_policy: str,
    default_permission_profile: str | None,
    config_result: Any,
    account_result: Any,
    limits_result: Any,
) -> str:
    config = _status_config(config_result)
    settings = record.get("settings") if isinstance(record.get("settings"), dict) else {}
    thread_id = str(record.get("thread_id") or "")
    active_mode = (_first_text(settings.get("active_mode"), settings.get("collaboration_mode")) or "default").casefold()
    modes = settings.get("modes") if isinstance(settings.get("modes"), dict) else {}
    mode_settings = modes.get(active_mode) if isinstance(modes.get(active_mode), dict) else {}
    legacy_model = settings.get("model") if active_mode == "default" else None
    legacy_effort = settings.get("effort") if active_mode == "default" else None

    model = _first_text(mode_settings.get("model"), legacy_model, config.get("model")) or "unknown"
    effort = _first_text(mode_settings.get("effort"), legacy_effort, config.get("model_reasoning_effort"))
    summary = _first_text(config.get("model_reasoning_summary")) or "auto"
    model_parts = []
    if effort:
        model_parts.append(f"reasoning {effort}")
    if summary:
        model_parts.append(f"summaries {summary}")

    permission = _first_text(
        settings.get("permissions"),
        config.get("permission_profile"),
        config.get("permissionProfile"),
        default_permission_profile,
    )
    sandbox = _first_text(config.get("sandbox_mode"), default_sandbox)
    approval = _first_text(settings.get("approval_policy"), config.get("approval_policy"), _approval_policy_value(default_approval_policy))
    collaboration_mode = active_mode or "default"

    lines = ["Codex status"]
    lines.append(f"Model: {model}" + (f" ({', '.join(model_parts)})" if model_parts else ""))
    lines.append(f"Directory: {workspace}")
    lines.append(f"Permissions: {_format_permission_status(permission=permission, sandbox=sandbox, approval=approval)}")
    lines.append(f"Agents.md: {_format_agents_status(workspace)}")
    lines.append(f"Account: {_format_status_account(account_result)}")
    lines.append(f"Collaboration mode: {collaboration_mode}")
    lines.append(f"Session: {thread_id or 'none'}")
    lines.append(_format_context_window_status(record))
    token_line = _format_token_usage_status(record)
    if token_line:
        lines.append(token_line)
    limit_lines = _format_rate_limit_status(limits_result)
    if limit_lines:
        lines.extend(limit_lines)
    return "\n".join(lines)


def _status_config(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and isinstance(result.get("config"), dict):
        return result["config"]
    return {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_permission_status(*, permission: str | None, sandbox: str | None, approval: str | None) -> str:
    base = _permission_profile_label(permission) if permission else _permission_profile_label(_thread_sandbox_value(sandbox or ""))
    if not base:
        base = "Default"
    if approval:
        return f"{base} (approval {approval})"
    return base


def _format_agents_status(workspace: Path) -> str:
    agents = _find_agents_file(workspace)
    if agents is None:
        return "none"
    try:
        relative = agents.relative_to(workspace)
    except ValueError:
        return str(agents)
    return str(relative) or agents.name


def _find_agents_file(workspace: Path) -> Path | None:
    for candidate in (workspace, *workspace.parents):
        path = candidate / "AGENTS.md"
        if path.is_file():
            return path
    return None


def _format_status_account(result: Any) -> str:
    if not isinstance(result, dict):
        return "unavailable"
    account = result.get("account")
    if not isinstance(account, dict):
        return "authentication required" if result.get("requiresOpenaiAuth") else "not signed in"
    email = _first_text(account.get("email"))
    plan = _first_text(account.get("planType"), account.get("type"))
    if email and plan:
        return f"{email} ({_permission_profile_label(plan)})"
    if email:
        return email
    return _first_text(account.get("type")) or "signed in"


def _format_context_window_status(record: dict[str, Any]) -> str:
    thread_id = str(record.get("thread_id") or "")
    if not thread_id:
        return "Context window: no active thread yet"
    usage = record.get("token_usage")
    if not isinstance(usage, dict):
        return "Context window: no usage recorded yet"
    total = usage.get("total")
    window = _int_or_none(usage.get("modelContextWindow"))
    used = _int_or_none(total.get("totalTokens") if isinstance(total, dict) else None)
    if window and used is not None:
        left = max(window - used, 0)
        return f"Context window: {_percent(left, window)} left ({_format_int(used)} used / {_format_int(window)})"
    if used is not None:
        return f"Context window: {_format_int(used)} used"
    if window:
        return f"Context window: {_format_int(window)}"
    return "Context window: no usage recorded yet"


def _format_token_usage_status(record: dict[str, Any]) -> str | None:
    usage = record.get("token_usage")
    if not isinstance(usage, dict):
        return None
    total = usage.get("total")
    if not isinstance(total, dict):
        return None
    parts = []
    for output_label, key in (
        ("input", "inputTokens"),
        ("cached", "cachedInputTokens"),
        ("output", "outputTokens"),
        ("reasoning", "reasoningOutputTokens"),
    ):
        amount = _int_or_none(total.get(key))
        if amount is not None:
            parts.append(f"{output_label} {_format_int(amount)}")
    if not parts:
        return None
    return "Token usage: " + ", ".join(parts)


def _format_rate_limit_status(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    snapshot = result.get("rateLimits")
    if not isinstance(snapshot, dict):
        return []
    lines: list[str] = []
    for key, fallback_label in (("primary", "Primary limit"), ("secondary", "Secondary limit")):
        window = snapshot.get(key)
        if isinstance(window, dict):
            line = _format_rate_limit_window(window, fallback_label)
            if line:
                lines.append(line)
    return lines


def _format_rate_limit_window(window: dict[str, Any], fallback_label: str) -> str | None:
    used_percent = _int_or_none(window.get("usedPercent"))
    if used_percent is None:
        return None
    left = max(0, min(100, 100 - used_percent))
    label = _rate_limit_window_label(window, fallback_label)
    reset = _format_reset_time(window.get("resetsAt"), weekly=label == "Weekly limit")
    return f"{label}: {left}% left" + (f" (resets {reset})" if reset else "")


def _rate_limit_window_label(window: dict[str, Any], fallback_label: str) -> str:
    duration = _int_or_none(window.get("windowDurationMins"))
    if duration == 300:
        return "5h limit"
    if duration == 10080:
        return "Weekly limit"
    return fallback_label


def _format_reset_time(value: Any, *, weekly: bool) -> str | None:
    timestamp = _int_or_none(value)
    if timestamp is None:
        return None
    reset_at = datetime.fromtimestamp(timestamp)
    if weekly:
        return reset_at.strftime("%H:%M on %d %b")
    return reset_at.strftime("%H:%M")


def _format_thread_token_usage(record: dict[str, Any]) -> str:
    thread_id = str(record.get("thread_id") or "")
    if not thread_id:
        return "No active thread yet. Send a message first."
    usage = record.get("token_usage")
    if not isinstance(usage, dict):
        return "No token usage recorded for this thread yet. Send a message and wait for a response first."

    lines = ["Usage"]
    if record.get("token_usage_updated_at"):
        lines.append(f"updated: {record['token_usage_updated_at']}")
    window = _int_or_none(usage.get("modelContextWindow"))
    total = usage.get("total")
    last = usage.get("last")
    if isinstance(total, dict):
        total_tokens = _int_or_none(total.get("totalTokens"))
        if total_tokens is not None:
            if window:
                lines.append(f"context: {_format_int(total_tokens)} / {_format_int(window)} ({_percent(total_tokens, window)})")
            else:
                lines.append(f"total tokens: {_format_int(total_tokens)}")
        lines.extend(_format_token_breakdown("total", total))
    if isinstance(last, dict):
        lines.extend(_format_token_breakdown("last turn", last))
    if window and not any(line.startswith("context:") for line in lines):
        lines.append(f"context window: {_format_int(window)}")
    return "\n".join(lines)


def _format_token_breakdown(label: str, value: dict[str, Any]) -> list[str]:
    parts = []
    for output_label, key in (
        ("input", "inputTokens"),
        ("cached", "cachedInputTokens"),
        ("output", "outputTokens"),
        ("reasoning", "reasoningOutputTokens"),
    ):
        amount = _int_or_none(value.get(key))
        if amount is not None:
            parts.append(f"{output_label} {_format_int(amount)}")
    return [f"{label}: " + ", ".join(parts)] if parts else []


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _format_int(value: int) -> str:
    return f"{value:,}"


def _percent(value: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{(value / total) * 100:.1f}%"


def _format_hooks(result: Any) -> str:
    lines = []
    for group in _result_items(result):
        cwd = group.get("cwd") or "cwd"
        for hook in group.get("hooks", []):
            if not isinstance(hook, dict):
                continue
            state = "enabled" if hook.get("enabled") else "disabled"
            lines.append(f"- {hook.get('key') or 'hook'}: {hook.get('eventName') or 'event'} {state} ({cwd})")
        for error in group.get("errors", []):
            if isinstance(error, dict):
                lines.append(f"- error: {error.get('message') or error}")
    return _format_lines("Hooks", lines, "No hooks found.")


def _format_mcp_servers(result: Any) -> str:
    lines = []
    for item in _result_items(result):
        tools = item.get("tools") if isinstance(item.get("tools"), dict) else {}
        lines.append(f"- {item.get('name') or 'server'}: {item.get('authStatus') or 'auth unknown'}, tools {len(tools)}")
    return _format_lines("MCP servers", lines, "No MCP servers found.")


def _format_config(result: Any) -> str:
    if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
        return "Config unavailable."
    config = result["config"]
    keys = [
        "model",
        "approval_policy",
        "sandbox_mode",
        "model_reasoning_effort",
        "model_reasoning_summary",
        "profile",
    ]
    lines = []
    for key in keys:
        if key in config and config[key] is not None:
            value = config[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            lines.append(f"{key}: {value}")
    if not lines:
        lines = [json.dumps(config, sort_keys=True)[:1000]]
    return "Config\n" + "\n".join(lines)


def _thread_id_from_thread_item(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("threadId") or _extract_id(item, "thread") or "")


def _thread_title_from_item(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("title"), item.get("summary"), item.get("name"))


def _thread_title_from_text(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", sanitize_text(text)).strip()
    if not normalized:
        return None
    if len(normalized) <= AUTO_THREAD_TITLE_MAX_CHARS:
        return normalized
    return normalized[: AUTO_THREAD_TITLE_MAX_CHARS - 3].rstrip() + "..."


def _thread_title_from_read_result(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else result
    if not isinstance(thread, dict):
        return None
    title = _thread_title_from_item(thread)
    if title:
        return title
    return _thread_title_from_text(_first_thread_message_text(thread) or "")


def _first_thread_message_text(thread: dict[str, Any]) -> str | None:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return None
    first_text: str | None = None
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        items = turn.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            text = _thread_item_text(item)
            if not text:
                continue
            if first_text is None:
                first_text = text
            item_type = str(item.get("type") or "").lower()
            if "user" in item_type:
                return text
    return first_text


def _thread_item_text(item: dict[str, Any]) -> str | None:
    for key in ("text", "message", "summary", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, list):
        parts = [str(block.get("text")) for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
        if parts:
            return "".join(parts).strip() or None
    input_items = item.get("input") or item.get("inputItems")
    if isinstance(input_items, list):
        parts = [str(block.get("text")) for block in input_items if isinstance(block, dict) and isinstance(block.get("text"), str)]
        if parts:
            return " ".join(parts).strip() or None
    return None


def _resume_button_text(thread_id: str, title: str | None) -> str:
    label = title or thread_id
    if len(label) <= 60:
        return label
    return label[:57].rstrip() + "..."


def _format_threads(result: Any, *, fallback_titles: dict[str, str] | None = None) -> str:
    lines = []
    fallback_titles = fallback_titles or {}
    for item in _result_items(result):
        thread_id = _thread_id_from_thread_item(item) or "thread"
        title = _thread_title_from_item(item) or fallback_titles.get(thread_id) or "Untitled"
        cwd = item.get("cwd") or item.get("workspace") or ""
        updated = item.get("updatedAt") or item.get("updated_at") or item.get("updated") or ""
        parts = [str(thread_id), str(title)]
        if cwd:
            parts.append(str(cwd))
        if updated:
            parts.append(str(updated))
        lines.append(" - ".join(parts))
    return _format_lines("Threads", lines, "No app-server threads found.")


def _format_goal(result: Any) -> str:
    goal = result.get("goal") if isinstance(result, dict) else None
    if not isinstance(goal, dict):
        goal = result if isinstance(result, dict) else {}
    objective = goal.get("objective")
    if not objective:
        return "No goal set."
    status = goal.get("status")
    return "Goal: " + str(objective) + (f"\nStatus: {status}" if status else "")


def _format_lines(title: str, lines: list[str], empty: str) -> str:
    if not lines:
        return empty
    return f"{title}:\n" + "\n".join(lines[:20])


async def _git_diff_with_untracked(workspace: Path) -> str:
    inside_code, inside_out, _inside_err = await _run_git(workspace, "rev-parse", "--is-inside-work-tree")
    if inside_code != 0 or inside_out.strip() != "true":
        return f"Not a git repository: {workspace}"
    parts: list[str] = []
    diff_code, diff_out, diff_err = await _run_git(workspace, "diff", "--no-ext-diff", "--")
    if diff_code not in {0, 1}:
        return f"git diff failed: {diff_err.strip() or diff_out.strip() or diff_code}"
    if diff_out.strip():
        parts.append(diff_out.rstrip())
    untracked_code, untracked_out, untracked_err = await _run_git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    if untracked_code not in {0, 1}:
        return f"git ls-files failed: {untracked_err.strip() or untracked_out.strip() or untracked_code}"
    untracked = [item for item in untracked_out.split("\0") if item]
    for rel_path in untracked[:20]:
        parts.append(_untracked_file_diff(workspace, rel_path))
    if len(untracked) > 20:
        parts.append(f"... {len(untracked) - 20} more untracked files omitted")
    return "\n\n".join(parts) if parts else "No git diff."


async def _run_git(workspace: Path, *args: str) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return 127, "", str(exc)
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _untracked_file_diff(workspace: Path, rel_path: str) -> str:
    safe_rel = rel_path.replace("\\", "/")
    path = (workspace / rel_path).resolve(strict=False)
    header = f"diff --git a/{safe_rel} b/{safe_rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{safe_rel}"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"{header}\n@@\n+<unable to read: {exc}>"
    if b"\0" in data:
        return f"{header}\n@@\n+<binary file, {len(data)} bytes>"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines and text:
        lines = [text]
    limited = lines[:200]
    body = "\n".join("+" + line for line in limited)
    if len(lines) > len(limited):
        body += f"\n+... {len(lines) - len(limited)} more lines omitted"
    return f"{header}\n@@\n{body}"


def _safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip().strip(".")
    if not name:
        name = "attachment"
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)


def _attachment_filename(attachment: dict[str, Any], file_path: str, file_id: str) -> str:
    explicit_name = str(attachment.get("file_name") or "").strip()
    if explicit_name:
        return _safe_filename(explicit_name)
    default_stem = str(attachment.get("_default_file_stem") or "").strip()
    if default_stem:
        extension = Path(file_path).suffix or ".jpg"
        return _safe_filename(f"{default_stem}{extension}")
    path_name = Path(file_path.replace("\\", "/")).name
    return _safe_filename(path_name or file_id or "attachment")


def _attachment_mime_type(attachment: dict[str, Any], filename: str, file_path: str) -> str:
    explicit_mime_type = str(attachment.get("mime_type") or "").strip()
    if explicit_mime_type:
        return explicit_mime_type
    guessed = mimetypes.guess_type(filename)[0] or mimetypes.guess_type(file_path)[0]
    return guessed or "application/octet-stream"


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    mime_type = str(attachment.get("mime_type") or "")
    if mime_type.startswith("image/"):
        return True
    guessed = mimetypes.guess_type(str(attachment.get("filename") or ""))[0]
    return bool(guessed and guessed.startswith("image/"))


def _bot_chat_id(chat_id: str | int) -> str | int:
    if isinstance(chat_id, int):
        return chat_id
    if re.fullmatch(r"-?\d+", chat_id):
        return int(chat_id)
    return chat_id


def _command_disabled_during_active_turn(command: TelegramCommand) -> bool:
    return (command.name or "") in ACTIVE_TURN_DISABLED_COMMANDS


def _thread_sandbox_value(sandbox: str) -> str:
    aliases = {
        "workspace-write": "workspace-write",
        "workspace_write": "workspace-write",
        "workspaceWrite": "workspace-write",
        "read-only": "read-only",
        "read_only": "read-only",
        "readOnly": "read-only",
        "danger-full-access": "danger-full-access",
        "danger_full_access": "danger-full-access",
        "dangerFullAccess": "danger-full-access",
    }
    return aliases.get(sandbox, sandbox)


def _approval_policy_value(policy: str) -> str:
    aliases = {
        "unlessTrusted": "on-request",
        "unless_trusted": "on-request",
        "onRequest": "on-request",
        "on_request": "on-request",
        "onFailure": "on-failure",
        "on_failure": "on-failure",
        "untrusted": "untrusted",
        "granular": "granular",
        "never": "never",
    }
    return aliases.get(policy, policy)

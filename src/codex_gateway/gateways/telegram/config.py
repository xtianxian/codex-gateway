from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ALLOWED_ROOT = Path("workspace")
DEFAULT_PROJECT_CWD = Path("workspace")
DEFAULT_STATE_DIR = Path(".codex-gateway") / "telegram"
DEFAULT_APP_SERVER_URL = "ws://127.0.0.1:8765"
DEFAULT_APP_SERVER_TRANSPORT = "websocket"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_APP_SERVER_COMMAND = "codex app-server --listen stdio://"


class TelegramSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str | None
    state_dir: Path
    allowed_roots: tuple[Path, ...]
    default_cwd: Path
    app_server_command: tuple[str, ...]
    model: str | None
    sandbox: str
    approval_policy: str
    approval_timeout_seconds: int
    max_attachment_bytes: int
    poll_timeout_seconds: int
    permission_profile: str | None = None
    model_reasoning_effort: str | None = None
    app_server_transport: str = DEFAULT_APP_SERVER_TRANSPORT
    app_server_url: str = DEFAULT_APP_SERVER_URL
    codex_bin: str = DEFAULT_CODEX_BIN
    enable_exec: bool = False
    advertise_exec: bool = False
    allowed_user_id: str | None = None
    pair_command_template: str | None = None


def get_telegram_settings() -> TelegramSettings:
    allowed_roots = tuple(
        _parse_path_list("CODEX_GATEWAY_ALLOWED_ROOTS", "CODEX_TELEGRAM_ALLOWED_ROOTS") or [DEFAULT_ALLOWED_ROOT]
    )
    allowed_roots = tuple(path.expanduser().resolve(strict=False) for path in allowed_roots)
    default_cwd = Path(
        _env("CODEX_GATEWAY_DEFAULT_CWD", "CODEX_TELEGRAM_DEFAULT_CWD")
        or str(DEFAULT_PROJECT_CWD)
    ).expanduser().resolve(strict=False)
    if not is_path_within_any_root(default_cwd, allowed_roots):
        raise TelegramSettingsError(
            f"CODEX_GATEWAY_DEFAULT_CWD must be inside CODEX_GATEWAY_ALLOWED_ROOTS: {default_cwd}"
        )

    codex_bin = _env("CODEX_GATEWAY_CODEX_BIN") or DEFAULT_CODEX_BIN
    app_server_url = _env("CODEX_GATEWAY_APP_SERVER_URL") or DEFAULT_APP_SERVER_URL
    command = _env("CODEX_GATEWAY_APP_SERVER_COMMAND", "CODEX_TELEGRAM_APP_SERVER_COMMAND")
    if command is None:
        command = DEFAULT_APP_SERVER_COMMAND
    return TelegramSettings(
        bot_token=_env_optional("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN", "CODEX_TELEGRAM_BOT_TOKEN"),
        state_dir=Path(
            _env("CODEX_GATEWAY_TELEGRAM_STATE_DIR", "CODEX_TELEGRAM_STATE_DIR") or _default_state_dir()
        ).expanduser().resolve(strict=False),
        allowed_roots=allowed_roots,
        default_cwd=default_cwd,
        app_server_command=tuple(shlex.split(command, posix=os.name != "nt")),
        model=_env_optional("CODEX_GATEWAY_TELEGRAM_MODEL", "CODEX_TELEGRAM_MODEL"),
        model_reasoning_effort=_env_optional(
            "CODEX_GATEWAY_TELEGRAM_MODEL_REASONING_EFFORT",
            "CODEX_TELEGRAM_MODEL_REASONING_EFFORT",
        ),
        permission_profile=_env_optional(
            "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE",
            "CODEX_TELEGRAM_PERMISSION_PROFILE",
        ),
        sandbox=_env("CODEX_GATEWAY_TELEGRAM_SANDBOX", "CODEX_TELEGRAM_SANDBOX") or "workspace-write",
        approval_policy=_env("CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY", "CODEX_TELEGRAM_APPROVAL_POLICY")
        or "unlessTrusted",
        approval_timeout_seconds=_env_int("CODEX_GATEWAY_TELEGRAM_APPROVAL_TIMEOUT_SECONDS", 900, "CODEX_TELEGRAM_APPROVAL_TIMEOUT_SECONDS"),
        max_attachment_bytes=_env_int("CODEX_GATEWAY_TELEGRAM_MAX_ATTACHMENT_BYTES", 25_000_000, "CODEX_TELEGRAM_MAX_ATTACHMENT_BYTES"),
        poll_timeout_seconds=_env_int("CODEX_GATEWAY_TELEGRAM_POLL_TIMEOUT_SECONDS", 30, "CODEX_TELEGRAM_POLL_TIMEOUT_SECONDS"),
        app_server_transport=(
            _env("CODEX_GATEWAY_APP_SERVER_TRANSPORT", "CODEX_TELEGRAM_APP_SERVER_TRANSPORT")
            or DEFAULT_APP_SERVER_TRANSPORT
        ).lower(),
        app_server_url=app_server_url,
        codex_bin=codex_bin,
        enable_exec=_env_bool("CODEX_GATEWAY_ENABLE_EXEC", False),
        advertise_exec=_env_bool("CODEX_GATEWAY_ADVERTISE_EXEC", False),
        allowed_user_id=_telegram_allowed_user_id(),
        pair_command_template=_env_optional("CODEX_GATEWAY_TELEGRAM_PAIR_COMMAND"),
    )


def resolve_workspace(settings: TelegramSettings, workspace: str) -> Path:
    raw = workspace.strip()
    if not raw:
        return settings.default_cwd
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        if is_path_within_any_root(resolved, settings.allowed_roots):
            return resolved
        raise TelegramSettingsError(f"Workspace is outside allowed roots: {workspace}")

    for root in settings.allowed_roots:
        resolved = (root / candidate).resolve(strict=False)
        if is_path_within_any_root(resolved, [root]):
            return resolved
    raise TelegramSettingsError(f"Workspace is outside allowed roots: {workspace}")


def is_path_within_any_root(path: str | Path, roots: list[Path] | tuple[Path, ...]) -> bool:
    resolved_path = Path(path).expanduser().resolve(strict=False)
    for root in roots:
        resolved_root = Path(root).expanduser().resolve(strict=False)
        try:
            resolved_path.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def _default_state_dir() -> str:
    return str(DEFAULT_STATE_DIR)


def _parse_path_list(name: str, legacy_name: str | None = None) -> list[Path]:
    raw = (_env(name, legacy_name) or "").strip()
    if not raw:
        return []
    separators = [os.pathsep]
    if os.pathsep != ",":
        separators.append(",")
    parts = [raw]
    for separator in separators:
        next_parts: list[str] = []
        for part in parts:
            next_parts.extend(part.split(separator))
        parts = next_parts
    return [Path(part.strip()) for part in parts if part.strip()]


def _env(name: str, legacy_name: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None and legacy_name is not None:
        raw = os.getenv(legacy_name)
    if raw is None:
        raw = _dotenv_values().get(name)
    if raw is None and legacy_name is not None:
        raw = _dotenv_values().get(legacy_name)
    return raw


def _env_optional(name: str, legacy_name: str | None = None) -> str | None:
    raw = _env(name, legacy_name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _env_int(name: str, default: int, legacy_name: str | None = None) -> int:
    raw = _env(name, legacy_name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _telegram_allowed_user_id() -> str | None:
    raw = _env_optional("CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID", "CODEX_TELEGRAM_ALLOWED_USER_ID")
    if raw is None:
        raw = _env_optional("CODEX_GATEWAY_TELEGRAM_USER_ID", "CODEX_TELEGRAM_USER_ID")
    if raw is None:
        return None
    if not raw.isdecimal():
        raise TelegramSettingsError("CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID must be a numeric Telegram user ID.")
    return raw


def _dotenv_values(path: Path | None = None) -> dict[str, str]:
    dotenv_path = path or (Path.cwd() / ".env")
    if not dotenv_path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values

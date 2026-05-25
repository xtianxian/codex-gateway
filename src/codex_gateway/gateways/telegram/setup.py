from __future__ import annotations

import getpass
import os
import textwrap
from pathlib import Path
from typing import Any, NamedTuple

from .config import TelegramSettingsError, _dotenv_values, is_path_within_any_root


SETUP_ENV_DEFAULT = ".env"
SETUP_STATE_DIR_DEFAULT = ".codex-gateway/telegram"
SETUP_ALLOWED_ROOT_DEFAULT = "workspace"
SETUP_DEFAULT_CWD_DEFAULT = "workspace"
SETUP_PERMISSION_PROFILE_DEFAULT = ":workspace"
BOT_TOKEN_HELP = "In Telegram, open @BotFather, run /newbot, then paste the token it gives you."
USER_ID_HELP = "In Telegram, open @userinfobot and copy your numeric ID."
WORKSPACE_HELP = (
    "Workspace root(s) are directories Codex may use.\n"
    "Use one directory, or multiple directories separated by semicolon or comma.\n"
    "Example: C:\\codex-workspace"
)


class PermissionProfileChoice(NamedTuple):
    key: str
    label: str
    profile: str
    sandbox: str
    approval_policy: str
    description: str
    aliases: tuple[str, ...]


PERMISSION_PROFILE_CHOICES = [
    PermissionProfileChoice(
        "1",
        "Read Only",
        ":read-only",
        "read-only",
        "on-request",
        "Codex can read files in the current workspace. Approval is required to edit files or access the internet.",
        ("read-only", "read only", ":read-only"),
    ),
    PermissionProfileChoice(
        "2",
        "Default",
        SETUP_PERMISSION_PROFILE_DEFAULT,
        "workspace-write",
        "on-request",
        "Codex can read and edit files in the current workspace, and run commands. Approval is required to access the internet or edit other files.",
        ("default", "workspace", "workspace-write", ":workspace"),
    ),
    PermissionProfileChoice(
        "3",
        "Auto-review",
        ":auto-review",
        "workspace-write",
        "on-request",
        "Same workspace-write permissions as Default, but eligible on-request approvals are routed through the auto-reviewer.",
        ("auto-review", "auto review", ":auto-review"),
    ),
    PermissionProfileChoice(
        "4",
        "Full Access",
        ":danger-full-access",
        "danger-full-access",
        "never",
        "Codex can edit files outside this workspace and access the internet without asking for approval. Exercise caution when using.",
        ("full-access", "full access", "danger-full-access", ":danger-full-access", ":full-access"),
    ),
]

GATEWAY_ENV_KEYS = {
    "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN": "",
    "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID": "",
    "CODEX_GATEWAY_TELEGRAM_STATE_DIR": SETUP_STATE_DIR_DEFAULT,
    "CODEX_GATEWAY_ALLOWED_ROOTS": SETUP_ALLOWED_ROOT_DEFAULT,
    "CODEX_GATEWAY_DEFAULT_CWD": SETUP_DEFAULT_CWD_DEFAULT,
    "CODEX_GATEWAY_CODEX_BIN": "codex",
    "CODEX_GATEWAY_APP_SERVER_URL": "ws://127.0.0.1:8765",
    "CODEX_GATEWAY_APP_SERVER_TRANSPORT": "websocket",
    "CODEX_GATEWAY_TELEGRAM_MODEL": "",
    "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE": SETUP_PERMISSION_PROFILE_DEFAULT,
    "CODEX_GATEWAY_TELEGRAM_SANDBOX": "workspace-write",
    "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY": "on-request",
    "CODEX_GATEWAY_TELEGRAM_APPROVAL_TIMEOUT_SECONDS": "900",
    "CODEX_GATEWAY_TELEGRAM_MAX_ATTACHMENT_BYTES": "25000000",
    "CODEX_GATEWAY_TELEGRAM_POLL_TIMEOUT_SECONDS": "30",
    "CODEX_GATEWAY_ENABLE_EXEC": "0",
    "CODEX_GATEWAY_ADVERTISE_EXEC": "0",
}


def run_telegram_setup(args: Any) -> None:
    env_file = Path(str(args.env_file or SETUP_ENV_DEFAULT)).expanduser()
    existing = _dotenv_values(env_file)
    env_display = _display_path(env_file)
    targeted_update = _has_targeted_update_args(args)
    print("Telegram Gateway Setup")
    existing_token = _existing_value(
        existing,
        "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN",
        "CODEX_TELEGRAM_BOT_TOKEN",
    )
    existing_user_id = _existing_value(
        existing,
        "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID",
        "CODEX_TELEGRAM_ALLOWED_USER_ID",
        "CODEX_GATEWAY_TELEGRAM_USER_ID",
        "CODEX_TELEGRAM_USER_ID",
    )
    existing_allowed_root = _existing_value(existing, "CODEX_GATEWAY_ALLOWED_ROOTS", "CODEX_TELEGRAM_ALLOWED_ROOTS")
    existing_default_cwd = _existing_value(existing, "CODEX_GATEWAY_DEFAULT_CWD", "CODEX_TELEGRAM_DEFAULT_CWD")
    existing_state_dir = _existing_value(
        existing,
        "CODEX_GATEWAY_TELEGRAM_STATE_DIR",
        "CODEX_TELEGRAM_STATE_DIR",
    )
    existing_permission_choice = _permission_choice_from_existing(existing)
    _print_existing_setup_defaults(
        env_display=env_display,
        token_detected=bool(existing_token),
        user_id=existing_user_id,
        workspace_roots=existing_allowed_root,
        default_workspace=existing_default_cwd,
        permission_choice=existing_permission_choice if _existing_permission_configured(existing) else None,
    )

    bot_token = _secret_prompt(
        "Telegram bot token",
        args.bot_token,
        default=existing_token,
        use_default_without_prompt=targeted_update,
        help_text=BOT_TOKEN_HELP,
        help_title="Telegram Bot Token",
    )
    allowed_user_id = _telegram_user_id_prompt(
        args.user_id,
        default=existing_user_id,
        use_default_without_prompt=targeted_update,
    )
    allowed_roots, default_cwd = _workspace_values(
        args.allowed_root,
        args.default_cwd,
        default_allowed_root=existing_allowed_root,
        default_default_cwd=existing_default_cwd,
        use_existing_without_prompt=targeted_update,
    )
    state_dir = str(args.state_dir or existing_state_dir or SETUP_STATE_DIR_DEFAULT).strip()
    permission_choice = _permission_profile_prompt(
        getattr(args, "permission_profile", None),
        default_choice=existing_permission_choice,
        default_is_existing=_existing_permission_configured(existing),
        use_default_without_prompt=targeted_update,
    )

    if not bot_token:
        raise SystemExit("Telegram bot token is required.")
    if not allowed_user_id:
        raise SystemExit("Telegram user ID is required.")
    if not state_dir:
        state_dir = SETUP_STATE_DIR_DEFAULT
    _validate_default_cwd(default_cwd, allowed_roots)
    Path(default_cwd).expanduser().resolve(strict=False).mkdir(parents=True, exist_ok=True)

    values = dict(GATEWAY_ENV_KEYS)
    values.update(
        {
            "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN": bot_token,
            "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID": allowed_user_id,
            "CODEX_GATEWAY_TELEGRAM_STATE_DIR": state_dir,
            "CODEX_GATEWAY_ALLOWED_ROOTS": allowed_roots,
            "CODEX_GATEWAY_DEFAULT_CWD": default_cwd,
            "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE": permission_choice.profile,
            "CODEX_GATEWAY_TELEGRAM_SANDBOX": permission_choice.sandbox,
            "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY": permission_choice.approval_policy,
        }
    )
    write_gateway_env_file(env_file, values)

    print()
    print("Setup Complete")
    print(f"  Telegram gateway setup written to {env_display}.")
    print(f"  Default permission profile: {permission_choice.label}.")
    print("  Workspace roots can contain multiple directories separated by semicolon or comma.")
    print("  Telegram /setcwd persists per chat until /setcwd, /workspace set, or /reset changes it.")
    print("  Only the configured Telegram user can send /start to get the pairing command.")
    print()
    print("Next Commands")
    print("  uv run codex-gateway telegram status")
    print("  uv run codex-gateway telegram run")


def write_gateway_env_file(env_file: Path, values: dict[str, str]) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    updated: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _value = stripped.split("=", 1)
            key = key.strip()
            if key in values:
                updated.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    env_file.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _prompt(
    label: str,
    value: str | None,
    default: str | None = None,
    *,
    default_label: str | None = None,
    use_default_without_prompt: bool = False,
    help_text: str | None = None,
    help_title: str | None = None,
) -> str:
    if value is not None:
        return value.strip()
    if default is not None and use_default_without_prompt:
        return default
    if help_text:
        _print_setup_section(help_title or label, help_text)
    suffix = f" [{default_label or default}]" if default is not None else ""
    entered = input(f"{label}{suffix}: ").strip()
    if entered:
        return entered
    return default or ""


def _secret_prompt(
    label: str,
    value: str | None,
    *,
    default: str | None = None,
    use_default_without_prompt: bool = False,
    help_text: str | None = None,
    help_title: str | None = None,
) -> str:
    if value is not None:
        return value.strip()
    if default and use_default_without_prompt:
        return default
    if help_text:
        _print_setup_section(help_title or label, help_text)
    suffix = " [existing token found; Enter keeps it]" if default else ""
    entered = getpass.getpass(f"{label}{suffix}: ").strip()
    return entered or default or ""


def _telegram_user_id_prompt(
    value: str | None,
    *,
    default: str | None = None,
    use_default_without_prompt: bool = False,
) -> str:
    user_id = _prompt(
        "Telegram user ID",
        value,
        default,
        default_label=f"existing {default}; Enter keeps it" if default else None,
        use_default_without_prompt=use_default_without_prompt,
        help_text=USER_ID_HELP,
        help_title="Telegram User ID",
    )
    if user_id and not user_id.isdecimal():
        raise SystemExit("Telegram user ID must be numeric.")
    return user_id


def _workspace_values(
    allowed_root: str | None,
    default_cwd: str | None,
    *,
    default_allowed_root: str | None = None,
    default_default_cwd: str | None = None,
    use_existing_without_prompt: bool = False,
) -> tuple[str, str]:
    if allowed_root is None and default_cwd is None:
        if use_existing_without_prompt and default_allowed_root and default_default_cwd:
            return default_allowed_root, default_default_cwd
        if default_allowed_root and default_default_cwd and default_allowed_root != default_default_cwd:
            allowed = _prompt(
                "Workspace root(s)",
                None,
                default_allowed_root,
                default_label=f"existing {default_allowed_root}; Enter keeps it",
                help_text=WORKSPACE_HELP,
                help_title="Workspace",
            )
            default = _prompt(
                "Default workspace",
                None,
                default_default_cwd,
                default_label=f"existing {default_default_cwd}; Enter keeps it",
            )
            return allowed or SETUP_ALLOWED_ROOT_DEFAULT, default or SETUP_DEFAULT_CWD_DEFAULT
        workspace_default = default_default_cwd or default_allowed_root or SETUP_DEFAULT_CWD_DEFAULT
        workspace = _prompt(
            "Workspace root(s)",
            None,
            workspace_default,
            default_label=(
                f"existing {workspace_default}; Enter keeps it"
                if default_default_cwd or default_allowed_root
                else None
            ),
            help_text=WORKSPACE_HELP,
            help_title="Workspace",
        )
        workspace_parts = _split_path_list(workspace)
        if len(workspace_parts) > 1:
            default = _prompt("Default workspace", None, default_default_cwd or workspace_parts[0])
            return workspace, default or workspace_parts[0]
        return workspace, workspace

    allowed = (allowed_root or default_cwd or default_allowed_root or SETUP_ALLOWED_ROOT_DEFAULT).strip()
    default = (default_cwd or _default_workspace_for_roots(allowed) or allowed).strip()
    return allowed or SETUP_ALLOWED_ROOT_DEFAULT, default or SETUP_DEFAULT_CWD_DEFAULT


def _validate_default_cwd(default_cwd: str, allowed_roots: str) -> None:
    roots = [Path(part.strip()).expanduser().resolve(strict=False) for part in _split_path_list(allowed_roots)]
    if not roots:
        raise SystemExit("At least one allowed workspace root is required.")
    cwd = Path(default_cwd).expanduser().resolve(strict=False)
    if not is_path_within_any_root(cwd, roots):
        raise SystemExit(TelegramSettingsError(f"Default workspace is outside allowed roots: {default_cwd}"))


def _split_path_list(value: str) -> list[str]:
    parts = [value]
    separators = [os.pathsep]
    if os.pathsep != ",":
        separators.append(",")
    for separator in separators:
        next_parts: list[str] = []
        for part in parts:
            next_parts.extend(part.split(separator))
        parts = next_parts
    return [part.strip() for part in parts if part.strip()]


def _default_workspace_for_roots(allowed_roots: str) -> str | None:
    parts = _split_path_list(allowed_roots)
    return parts[0] if parts else None


def _print_existing_setup_defaults(
    *,
    env_display: str,
    token_detected: bool,
    user_id: str | None,
    workspace_roots: str | None,
    default_workspace: str | None,
    permission_choice: PermissionProfileChoice | None,
) -> None:
    rows: list[tuple[str, str]] = []
    if token_detected:
        rows.append(("Telegram bot token", "found; Enter keeps it"))
    if user_id:
        rows.append(("Telegram user ID", user_id))
    if workspace_roots:
        rows.append(("Workspace root(s)", workspace_roots))
    if default_workspace:
        rows.append(("Default workspace", default_workspace))
    if permission_choice:
        rows.append(("Permission profile", permission_choice.label))
    if rows:
        print()
        print(f"Existing Setup Detected ({env_display})")
        for label, value in rows:
            print(f"  {label:<20} {value}")


def _print_setup_section(title: str, body: str) -> None:
    print()
    print(title)
    for paragraph in body.splitlines():
        wrapped = textwrap.wrap(paragraph, width=88) or [""]
        for line in wrapped:
            print(f"  {line}")
    print()


def _existing_permission_configured(existing: dict[str, str]) -> bool:
    return any(
        _existing_value(existing, *names) is not None
        for names in (
            ("CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE", "CODEX_TELEGRAM_PERMISSION_PROFILE"),
            ("CODEX_GATEWAY_TELEGRAM_SANDBOX", "CODEX_TELEGRAM_SANDBOX"),
            ("CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY", "CODEX_TELEGRAM_APPROVAL_POLICY"),
        )
    )


def _permission_profile_prompt(
    value: str | None,
    *,
    default_choice: PermissionProfileChoice,
    default_is_existing: bool = False,
    use_default_without_prompt: bool = False,
) -> PermissionProfileChoice:
    if value is not None:
        choice = _permission_choice_from_value(value)
        if choice is None:
            raise SystemExit("Use --permission-profile <read-only|default|auto-review|full-access>.")
        return choice
    if use_default_without_prompt:
        return default_choice

    print()
    print("Default Permission Profile")
    print()
    for choice in PERMISSION_PROFILE_CHOICES:
        print(f"  {choice.key}. {choice.label}")
        for line in textwrap.wrap(choice.description, width=84):
            print(f"     {line}")
    default_label = (
        f"existing {default_choice.key} {default_choice.label}; Enter keeps it"
        if default_is_existing
        else f"{default_choice.key} {default_choice.label}"
    )
    print()
    selected = input(f"Select profile [{default_label}]: ").strip() or default_choice.key
    choice = _permission_choice_from_value(selected)
    if choice is None:
        raise SystemExit("Choose permission profile 1, 2, 3, 4, read-only, default, auto-review, or full-access.")
    return choice


def _permission_choice_from_existing(existing: dict[str, str]) -> PermissionProfileChoice:
    profile = _existing_value(
        existing,
        "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE",
        "CODEX_TELEGRAM_PERMISSION_PROFILE",
    )
    choice = _permission_choice_from_value(profile)
    if choice is not None:
        return choice

    sandbox = (_existing_value(existing, "CODEX_GATEWAY_TELEGRAM_SANDBOX", "CODEX_TELEGRAM_SANDBOX") or "").strip()
    approval_policy = (
        _existing_value(existing, "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY", "CODEX_TELEGRAM_APPROVAL_POLICY") or ""
    ).strip()
    if _normalize_permission_selector(approval_policy) == "never" or _normalize_permission_selector(
        sandbox
    ) == "danger-full-access":
        return _permission_choice_from_value("full-access") or PERMISSION_PROFILE_CHOICES[-1]
    if _normalize_permission_selector(sandbox) == "read-only":
        return _permission_choice_from_value("read-only") or PERMISSION_PROFILE_CHOICES[0]
    return _permission_choice_from_value(SETUP_PERMISSION_PROFILE_DEFAULT) or PERMISSION_PROFILE_CHOICES[1]


def _permission_choice_from_value(value: str | None) -> PermissionProfileChoice | None:
    if value is None:
        return None
    normalized = _normalize_permission_selector(value)
    for choice in PERMISSION_PROFILE_CHOICES:
        candidates = {
            choice.key,
            _normalize_permission_selector(choice.label),
            _normalize_permission_selector(choice.profile),
        }
        candidates.update(_normalize_permission_selector(alias) for alias in choice.aliases)
        if normalized in candidates:
            return choice
    return None


def _normalize_permission_selector(value: str) -> str:
    normalized = value.strip().casefold().replace("_", "-").replace(" ", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized


def _existing_value(existing: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = existing.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _has_targeted_update_args(args: Any) -> bool:
    return any(
        getattr(args, name, None) is not None
        for name in (
            "bot_token",
            "user_id",
            "allowed_root",
            "default_cwd",
            "state_dir",
            "permission_profile",
        )
    )


def _display_path(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=False)
    try:
        return resolved.relative_to(Path.cwd().resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...core.commands import default_command_registry


class TelegramCommandKind(str, Enum):
    MESSAGE = "message"
    LOCAL = "local"
    THREAD = "thread"
    APP_SERVER = "app_server"
    CODEX_TURN = "codex_turn"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TelegramCommand:
    kind: TelegramCommandKind
    name: str | None
    args: str
    raw: str


LOCAL_COMMANDS = {
    "start",
    "help",
    "status",
    "commands",
    "project",
    "projects",
    "workspace",
    "setcwd",
    "getcwd",
    "searchcwd",
    "reset",
    "clear",
    "diff",
}
THREAD_COMMANDS = {"new", "resume", "fork", "side", "btw", "archive", "unarchive", "rollback"}
APP_SERVER_COMMANDS = {
    "account",
    "approval",
    "apps",
    "approve",
    "agent",
    "cancel",
    "config",
    "debug-config",
    "effort",
    "experimental",
    "features",
    "goal",
    "hooks",
    "interrupt",
    "limits",
    "memories",
    "mcp",
    "mode",
    "model",
    "permissions",
    "personality",
    "plugins",
    "ps",
    "rename",
    "skills",
    "steer",
    "stop",
    "subagents",
    "threads",
}
CODEX_TURN_COMMANDS = {
    "review",
    "compact",
    "init",
    "mention",
    "plan",
    "read",
    "collab",
    "exec",
}
UNSUPPORTED_COMMANDS = {
    "usage",
    "context",
    "copy",
    "raw",
    "statusline",
    "title",
    "theme",
    "pets",
    "pet",
    "keymap",
    "vim",
    "settings",
    "realtime",
    "ide",
    "quit",
    "exit",
    "feedback",
    "rollout",
    "test-approval",
    "debug-m-drop",
    "debug-m-update",
    "setup-default-sandbox",
    "sandbox-add-read-dir",
    "clean",
    "logout",
    "plugin",
}


def parse_telegram_command(text: str | None) -> TelegramCommand:
    raw = text or ""
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return TelegramCommand(
            kind=TelegramCommandKind.MESSAGE,
            name=None,
            args=raw,
            raw=raw,
        )
    head, _, args = stripped.partition(" ")
    name = head[1:].split("@", 1)[0].lower()
    kind = TelegramCommandKind.UNKNOWN
    if name in LOCAL_COMMANDS:
        kind = TelegramCommandKind.LOCAL
    elif name in THREAD_COMMANDS:
        kind = TelegramCommandKind.THREAD
    elif name in APP_SERVER_COMMANDS:
        kind = TelegramCommandKind.APP_SERVER
    elif name in CODEX_TURN_COMMANDS:
        kind = TelegramCommandKind.CODEX_TURN
    elif name in UNSUPPORTED_COMMANDS:
        kind = TelegramCommandKind.UNSUPPORTED
    return TelegramCommand(kind=kind, name=name, args=args.strip(), raw=raw)


def command_turn_prompt(command: TelegramCommand) -> str:
    name = command.name or ""
    if name == "review":
        return "Review the current working tree."
    if name == "compact":
        return "Compact this conversation context."
    if name == "init":
        return "Create an AGENTS.md file with project instructions for this workspace."
    if name == "mention":
        return f"Use the referenced path in this task: {command.args}".strip()
    if name == "read":
        return f"Read and summarize this path: {command.args}".strip()
    if name == "collab":
        return "Show the current collaboration mode."
    if name == "exec":
        return f"Run this shell command in the active workspace: {command.args}".strip()
    if name == "plan":
        return command.args or "Switch this task into plan mode."
    return command.raw


def unsupported_command_message(command: TelegramCommand) -> str:
    name = command.name or ""
    if name in {"usage", "context"}:
        return "Use /status for context, token usage, and rate-limit status."
    if name == "plugin":
        return "Use /plugins to list Codex plugins. Install and uninstall are not available from Telegram Gateway."
    registry = default_command_registry()
    if registry.get(name) is not None:
        return registry.missing_method_message(name)
    return f"/{name} is not available from Telegram Gateway."

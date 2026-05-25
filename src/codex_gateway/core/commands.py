from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_TELEGRAM_MENU_COMMAND_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


@dataclass(frozen=True)
class GatewayCommand:
    name: str
    description: str
    kind: str
    app_server_method: str | None = None
    advertise: bool = True
    exec_only: bool = False
    typed_only: bool = False
    cli_hidden: bool = False
    feature_gated: bool = False
    sensitive_confirmation: bool = False
    required_app_server_method_status: str = "not_required"


class CommandRegistry:
    def __init__(self, commands: Iterable[GatewayCommand]) -> None:
        self._commands = {command.name: command for command in commands}

    def get(self, name: str) -> GatewayCommand | None:
        return self._commands.get(name)

    def commands(self) -> list[GatewayCommand]:
        return list(self._commands.values())

    def advertised_commands(
        self,
        *,
        supported_methods: set[str] | None = None,
        enable_exec: bool = False,
        advertise_exec: bool = False,
    ) -> list[GatewayCommand]:
        commands: list[GatewayCommand] = []
        for command in self._commands.values():
            if not command.advertise:
                continue
            if command.typed_only:
                continue
            if command.exec_only and not (enable_exec and advertise_exec):
                continue
            if (
                supported_methods is not None
                and command.app_server_method is not None
                and command.app_server_method not in supported_methods
            ):
                continue
            commands.append(command)
        return commands

    def telegram_menu_payload(
        self,
        *,
        supported_methods: set[str] | None = None,
        enable_exec: bool = False,
        advertise_exec: bool = False,
    ) -> list[dict[str, str]]:
        return [
            {"command": command.name, "description": command.description}
            for command in self.advertised_commands(
                supported_methods=supported_methods,
                enable_exec=enable_exec,
                advertise_exec=advertise_exec,
            )
            if _TELEGRAM_MENU_COMMAND_PATTERN.fullmatch(command.name)
        ]

    def missing_method_message(self, name: str) -> str:
        command = self.get(name)
        if command is None or command.app_server_method is None:
            return f"/{name} is not supported by this gateway."
        return (
            f"/{name} requires app-server method `{command.app_server_method}`, "
            "which is not available in the generated local Codex schema."
        )


def default_command_registry() -> CommandRegistry:
    return CommandRegistry(
        [
            GatewayCommand("start", "Start or pair this chat", "local"),
            GatewayCommand("help", "Show command reference", "local"),
            GatewayCommand("status", "Show Codex status", "local"),
            GatewayCommand("commands", "Sync the Telegram command menu", "local"),
            GatewayCommand("project", "Show the active project", "local"),
            GatewayCommand("projects", "List allowed project roots", "local"),
            GatewayCommand("setcwd", "Set the active workspace", "local"),
            GatewayCommand("getcwd", "Show the active workspace", "local"),
            GatewayCommand("searchcwd", "Search allowed workspaces", "local"),
            GatewayCommand("workspace", "Set or list workspaces", "local", advertise=False, typed_only=True),
            GatewayCommand("reset", "Reset local chat state", "local", advertise=False, typed_only=True),
            GatewayCommand("clear", "Clear local thread mapping", "local"),
            GatewayCommand("cancel", "Cancel the active turn", "app_server", "turn/interrupt", required_app_server_method_status="required"),
            GatewayCommand("new", "Start a new thread", "thread", "thread/start"),
            GatewayCommand("resume", "Resume a thread", "thread", "thread/resume"),
            GatewayCommand("fork", "Fork the current thread", "thread", "thread/fork"),
            GatewayCommand("side", "Start an ephemeral side thread", "thread", "thread/fork"),
            GatewayCommand("btw", "Start an ephemeral aside thread", "thread", "thread/fork"),
            GatewayCommand("threads", "List workspace threads", "app_server", "thread/list", required_app_server_method_status="required"),
            GatewayCommand("read", "Ask Codex to read a path", "turn", advertise=False, typed_only=True),
            GatewayCommand("archive", "Archive the current thread", "thread", "thread/archive"),
            GatewayCommand("unarchive", "Unarchive a thread", "thread", "thread/unarchive"),
            GatewayCommand("compact", "Compact context", "turn", "thread/compact/start"),
            GatewayCommand("rollback", "Roll back thread history", "thread", "thread/rollback", advertise=False, typed_only=True),
            GatewayCommand("interrupt", "Cancel the active turn", "app_server", "turn/interrupt", required_app_server_method_status="required"),
            GatewayCommand("steer", "Steer the active turn", "app_server", "turn/steer", required_app_server_method_status="required"),
            GatewayCommand("review", "Review the working tree", "turn", "review/start"),
            GatewayCommand("diff", "Show the current diff", "turn"),
            GatewayCommand("mention", "Mention a path or symbol", "turn"),
            GatewayCommand("init", "Create AGENTS.md instructions", "turn"),
            GatewayCommand("plan", "Enter plan mode", "turn", "thread/settings/update"),
            GatewayCommand("goal", "Show or update goal", "app_server", "thread/goal/get", required_app_server_method_status="required"),
            GatewayCommand("agent", "List loaded agents", "app_server", "thread/loaded/list", required_app_server_method_status="required"),
            GatewayCommand("subagents", "List loaded subagents", "app_server", "thread/loaded/list", required_app_server_method_status="required"),
            GatewayCommand("rename", "Rename current thread", "app_server", "thread/name/set", required_app_server_method_status="required"),
            GatewayCommand("ps", "List active processes", "app_server", "thread/read", required_app_server_method_status="required"),
            GatewayCommand(
                "stop",
                "Stop background terminals",
                "app_server",
                "thread/backgroundTerminals/clean",
                sensitive_confirmation=True,
                required_app_server_method_status="required",
            ),
            GatewayCommand(
                "approve",
                "Approve a denied action",
                "app_server",
                "thread/approveGuardianDeniedAction",
                sensitive_confirmation=True,
                required_app_server_method_status="required",
            ),
            GatewayCommand("model", "Set model and reasoning effort", "app_server", "thread/settings/update", required_app_server_method_status="required"),
            GatewayCommand("permissions", "Set permission profile", "app_server", "thread/settings/update", required_app_server_method_status="required"),
            GatewayCommand("approval", "Set approval policy", "app_server", "thread/settings/update", advertise=False, typed_only=True),
            GatewayCommand("mode", "Set collaboration mode", "app_server", "thread/settings/update", advertise=False, typed_only=True),
            GatewayCommand("effort", "Set reasoning effort", "app_server", "thread/settings/update", advertise=False, typed_only=True),
            GatewayCommand("personality", "Set assistant personality", "app_server", "thread/settings/update", required_app_server_method_status="required"),
            GatewayCommand(
                "experimental",
                "Manage experimental features",
                "app_server",
                "experimentalFeature/enablement/set",
                feature_gated=True,
                required_app_server_method_status="required",
            ),
            GatewayCommand("features", "List available features", "app_server", "experimentalFeature/list", advertise=False, typed_only=True),
            GatewayCommand("memories", "Set thread memory mode", "app_server", "thread/memoryMode/set", required_app_server_method_status="required"),
            GatewayCommand("collab", "Show collaboration mode", "turn", advertise=False, typed_only=True),
            GatewayCommand("skills", "List or toggle skills", "app_server", "skills/list", required_app_server_method_status="required"),
            GatewayCommand("apps", "List available apps", "app_server", "app/list", feature_gated=True, required_app_server_method_status="required"),
            GatewayCommand("plugins", "List available plugins", "app_server", "plugin/list", feature_gated=True, required_app_server_method_status="required"),
            GatewayCommand("account", "Show account status", "app_server", "account/read", required_app_server_method_status="required"),
            GatewayCommand("limits", "Show rate limits", "app_server", "account/rateLimits/read", advertise=False, typed_only=True),
            GatewayCommand("hooks", "List hooks", "app_server", "hooks/list", required_app_server_method_status="required"),
            GatewayCommand("mcp", "List MCP status", "app_server", "mcpServerStatus/list", required_app_server_method_status="required"),
            GatewayCommand("config", "Show Codex configuration", "app_server", "config/read", advertise=False, typed_only=True),
            GatewayCommand("debug-config", "Show debug configuration", "app_server", "config/read", required_app_server_method_status="required"),
            GatewayCommand("exec", "Run a local command", "turn", advertise=True, exec_only=True),
        ]
    )

from __future__ import annotations

from codex_gateway.gateways.telegram.commands import (
    TelegramCommand,
    TelegramCommandKind,
    command_turn_prompt,
    parse_telegram_command,
    unsupported_command_message,
)
from codex_gateway.core.commands import default_command_registry


def test_parse_normal_message() -> None:
    assert parse_telegram_command("hello") == TelegramCommand(
        kind=TelegramCommandKind.MESSAGE,
        name=None,
        args="hello",
        raw="hello",
    )


def test_parse_workspace_list_and_set() -> None:
    assert parse_telegram_command("/workspace list").kind == TelegramCommandKind.LOCAL
    command = parse_telegram_command("/workspace set repo-a")
    assert command.name == "workspace"
    assert command.args == "set repo-a"
    assert command.kind == TelegramCommandKind.LOCAL


def test_retired_usage_commands_point_to_status() -> None:
    for text in ["/usage", "/context"]:
        command = parse_telegram_command(text)
        assert command.kind == TelegramCommandKind.UNSUPPORTED
        assert "Use /status" in unsupported_command_message(command)


def test_parse_thread_commands() -> None:
    for text in ["/new", "/resume", "/fork", "/side", "/btw", "/archive", "/unarchive", "/rollback"]:
        command = parse_telegram_command(text)
        assert command.name == text[1:]
        assert command.kind == TelegramCommandKind.THREAD


def test_parse_codex_turn_commands() -> None:
    expected = {
        "/review": "Review the current working tree.",
        "/compact": "Compact this conversation context.",
        "/mention README.md": "Use the referenced path in this task: README.md",
        "/init": "Create an AGENTS.md file with project instructions for this workspace.",
        "/read README.md": "Read and summarize this path: README.md",
        "/collab": "Show the current collaboration mode.",
        "/exec python -V": "Run this shell command in the active workspace: python -V",
        "/plan Draft the implementation": "Draft the implementation",
    }

    for text, prompt in expected.items():
        command = parse_telegram_command(text)
        assert command.kind == TelegramCommandKind.CODEX_TURN
        assert command_turn_prompt(command) == prompt


def test_parse_local_direct_commands() -> None:
    for text in ["/diff", "/clear"]:
        command = parse_telegram_command(text)
        assert command.name == text[1:]
        assert command.kind == TelegramCommandKind.LOCAL


def test_non_turn_commands_do_not_have_dead_turn_prompt_branches() -> None:
    for text in ["/diff", "/mcp", "/rollback", "/features", "/skills", "/apps", "/config"]:
        command = parse_telegram_command(text)

        assert command.kind != TelegramCommandKind.CODEX_TURN
        assert command_turn_prompt(command) == text


def test_parse_app_server_commands_and_aliases() -> None:
    for text in [
        "/model",
        "/model gpt-5.1",
        "/permissions",
        "/permissions read-only",
        "/permissions default",
        "/permissions auto-review",
        "/permissions full-access",
        "/approval",
        "/approval never",
        "/mode",
        "/mode plan",
        "/effort",
        "/effort high",
        "/goal",
        "/goal set Ship this",
        "/goal clear",
        "/approve",
        "/agent",
        "/subagents",
        "/personality",
        "/experimental",
        "/memories",
        "/plugins",
        "/ps",
        "/stop",
        "/rename Gateway work",
        "/cancel",
        "/interrupt",
        "/steer keep going",
        "/threads gateway",
        "/account",
        "/limits",
        "/hooks",
        "/mcp",
        "/mcp reload",
        "/apps",
        "/features",
        "/skills",
        "/config",
        "/debug-config",
    ]:
        command = parse_telegram_command(text)
        assert command.kind == TelegramCommandKind.APP_SERVER


def test_retired_list_commands_are_not_supported() -> None:
    for text in ["/models", "/modes"]:
        command = parse_telegram_command(text)
        assert command.kind == TelegramCommandKind.UNKNOWN


def test_removed_permission_alias_is_unknown() -> None:
    command = parse_telegram_command("/permission read-only")

    assert command.kind == TelegramCommandKind.UNKNOWN


def test_unsupported_terminal_only_command() -> None:
    for text in ["/raw", "/copy", "/logout"]:
        command = parse_telegram_command(text)

        assert command.kind == TelegramCommandKind.UNSUPPORTED
        assert unsupported_command_message(command) == f"{text} is not available from Telegram Gateway."


def test_unknown_command_is_reported() -> None:
    command = parse_telegram_command("/doesnotexist")

    assert command.kind == TelegramCommandKind.UNKNOWN


def test_all_advertised_registry_commands_are_classified_for_telegram() -> None:
    registry = default_command_registry()

    unknown = [
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
        if parse_telegram_command(f"/{command.name}").kind == TelegramCommandKind.UNKNOWN
    ]

    assert unknown == []


from __future__ import annotations

from codex_gateway.core.commands import default_command_registry


def test_registry_filters_advertised_commands_by_supported_methods() -> None:
    registry = default_command_registry()

    commands = registry.advertised_commands(
        supported_methods={
            "thread/settings/update",
            "experimentalFeature/list",
            "experimentalFeature/enablement/set",
        },
        enable_exec=False,
        advertise_exec=False,
    )
    names = {command.name for command in commands}

    assert {"start", "help", "status", "commands", "model", "experimental"} <= names
    assert {"models", "modes"}.isdisjoint(names)
    assert "permissions" in names
    assert "effort" not in names
    assert "archive" not in names
    assert "exec" not in names


def test_registry_uses_generated_schema_method_names() -> None:
    registry = default_command_registry()

    assert registry.get("features").app_server_method == "experimentalFeature/list"
    assert registry.get("experimental").app_server_method == "experimentalFeature/enablement/set"
    assert registry.get("apps").app_server_method == "app/list"
    assert registry.get("plugins").app_server_method == "plugin/list"
    assert registry.get("config").app_server_method == "config/read"
    assert registry.get("debug-config").app_server_method == "config/read"
    assert registry.get("model").app_server_method == "thread/settings/update"
    assert registry.get("permissions").app_server_method == "thread/settings/update"
    assert registry.get("permission") is None
    assert registry.get("logout") is None
    assert registry.get("approval").app_server_method == "thread/settings/update"
    assert registry.get("mode").app_server_method == "thread/settings/update"
    assert registry.get("effort").app_server_method == "thread/settings/update"
    assert registry.get("personality").app_server_method == "thread/settings/update"
    assert registry.get("memories").app_server_method == "thread/memoryMode/set"
    assert registry.get("goal").app_server_method == "thread/goal/get"
    assert registry.get("approve").app_server_method == "thread/approveGuardianDeniedAction"
    assert registry.get("agent").app_server_method == "thread/loaded/list"
    assert registry.get("subagents").app_server_method == "thread/loaded/list"
    assert registry.get("ps").app_server_method == "thread/read"
    assert registry.get("stop").app_server_method == "thread/backgroundTerminals/clean"
    assert registry.get("rename").app_server_method == "thread/name/set"
    assert registry.get("cancel").app_server_method == "turn/interrupt"
    assert registry.get("steer").app_server_method == "turn/steer"
    assert registry.get("threads").app_server_method == "thread/list"
    assert registry.get("account").app_server_method == "account/read"
    assert registry.get("limits").app_server_method == "account/rateLimits/read"
    assert registry.get("hooks").app_server_method == "hooks/list"
    assert registry.get("mcp").app_server_method == "mcpServerStatus/list"


def test_feature_gated_apps_command_is_advertised_for_cli_parity() -> None:
    registry = default_command_registry()

    names = {
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
    }

    assert "apps" in names
    assert registry.get("apps").feature_gated is True


def test_telegram_menu_payload_excludes_bot_api_invalid_command_names() -> None:
    registry = default_command_registry()

    advertised_names = {
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
    }
    menu_names = {
        command["command"]
        for command in registry.telegram_menu_payload(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
    }

    assert "debug-config" in advertised_names
    assert "debug-config" not in menu_names
    assert "debug_config" not in menu_names
    assert all("-" not in name for name in menu_names)


def test_retired_list_commands_are_not_registered() -> None:
    registry = default_command_registry()

    assert registry.get("models") is None
    assert registry.get("modes") is None


def test_exec_is_hidden_unless_enabled_and_advertised() -> None:
    registry = default_command_registry()

    assert "exec" not in {
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=False,
        )
    }
    assert "exec" in {
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
    }


def test_missing_method_message_names_required_app_server_method() -> None:
    registry = default_command_registry()

    message = registry.missing_method_message("archive")

    assert "thread/archive" in message
    assert "/archive" in message


from __future__ import annotations

from pathlib import Path

from codex_gateway.gateways.telegram.config import get_telegram_settings


GATEWAY_ENV_NAMES = [
    "CODEX_GATEWAY_ALLOWED_ROOTS",
    "CODEX_GATEWAY_DEFAULT_CWD",
    "CODEX_GATEWAY_TELEGRAM_STATE_DIR",
    "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN",
    "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID",
    "CODEX_GATEWAY_TELEGRAM_USER_ID",
    "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE",
    "CODEX_GATEWAY_TELEGRAM_MODEL_REASONING_EFFORT",
    "CODEX_GATEWAY_TELEGRAM_PAIR_COMMAND",
    "CODEX_GATEWAY_APP_SERVER_TRANSPORT",
    "CODEX_GATEWAY_CODEX_BIN",
    "CODEX_TELEGRAM_ALLOWED_ROOTS",
    "CODEX_TELEGRAM_DEFAULT_CWD",
    "CODEX_TELEGRAM_STATE_DIR",
    "CODEX_TELEGRAM_BOT_TOKEN",
    "CODEX_TELEGRAM_ALLOWED_USER_ID",
    "CODEX_TELEGRAM_USER_ID",
    "CODEX_TELEGRAM_PERMISSION_PROFILE",
    "CODEX_TELEGRAM_MODEL_REASONING_EFFORT",
]


def clear_gateway_env(monkeypatch) -> None:
    for name in GATEWAY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_gateway_telegram_defaults_use_relative_paths(monkeypatch, tmp_path: Path) -> None:
    clear_gateway_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    settings = get_telegram_settings()

    workspace = tmp_path / "workspace"
    assert settings.allowed_roots == (workspace.resolve(strict=False),)
    assert settings.default_cwd == workspace.resolve(strict=False)
    assert settings.state_dir == (tmp_path / ".codex-gateway" / "telegram").resolve(strict=False)
    assert settings.app_server_transport == "websocket"
    assert settings.app_server_url == "ws://127.0.0.1:8765"


def test_gateway_env_vars_override_legacy_telegram_env_vars(monkeypatch, tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    gateway_root = tmp_path / "gateway"
    legacy_root.mkdir()
    gateway_root.mkdir()
    gateway_cwd = gateway_root / "repo"
    gateway_cwd.mkdir()

    monkeypatch.setenv("CODEX_TELEGRAM_ALLOWED_ROOTS", str(legacy_root))
    monkeypatch.setenv("CODEX_TELEGRAM_DEFAULT_CWD", str(legacy_root))
    monkeypatch.setenv("CODEX_GATEWAY_ALLOWED_ROOTS", str(gateway_root))
    monkeypatch.setenv("CODEX_GATEWAY_DEFAULT_CWD", str(gateway_cwd))
    monkeypatch.setenv("CODEX_GATEWAY_APP_SERVER_TRANSPORT", "stdio")
    monkeypatch.setenv("CODEX_GATEWAY_CODEX_BIN", "codex-test")
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID", "123456")
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_MODEL_REASONING_EFFORT", "medium")
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_PAIR_COMMAND", "docker pair {code}")

    settings = get_telegram_settings()

    assert settings.allowed_roots == (gateway_root.resolve(strict=False),)
    assert settings.default_cwd == gateway_cwd.resolve(strict=False)
    assert settings.app_server_transport == "stdio"
    assert settings.codex_bin == "codex-test"
    assert settings.allowed_user_id == "123456"
    assert settings.model == "gpt-5.4-mini"
    assert settings.model_reasoning_effort == "medium"
    assert settings.pair_command_template == "docker pair {code}"


def test_dotenv_values_are_used_when_environment_is_empty(monkeypatch, tmp_path: Path) -> None:
    clear_gateway_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=from-dotenv",
                "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram",
                "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123456",
                "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:auto-review",
                "CODEX_GATEWAY_TELEGRAM_MODEL=gpt-5.4-mini",
                "CODEX_GATEWAY_TELEGRAM_MODEL_REASONING_EFFORT=medium",
                "CODEX_GATEWAY_ALLOWED_ROOTS=.",
                "CODEX_GATEWAY_DEFAULT_CWD=.",
                "CODEX_GATEWAY_APP_SERVER_TRANSPORT=stdio",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = get_telegram_settings()

    assert settings.bot_token == "from-dotenv"
    assert settings.state_dir == (tmp_path / ".codex-gateway" / "telegram").resolve(strict=False)
    assert settings.allowed_roots == (tmp_path.resolve(strict=False),)
    assert settings.default_cwd == tmp_path.resolve(strict=False)
    assert settings.app_server_transport == "stdio"
    assert settings.allowed_user_id == "123456"
    assert settings.permission_profile == ":auto-review"
    assert settings.model == "gpt-5.4-mini"
    assert settings.model_reasoning_effort == "medium"


def test_environment_values_override_dotenv(monkeypatch, tmp_path: Path) -> None:
    clear_gateway_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    dotenv_root = tmp_path / "dotenv"
    env_root = tmp_path / "env"
    dotenv_root.mkdir()
    env_root.mkdir()
    env_cwd = env_root / "repo"
    env_cwd.mkdir()
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=from-dotenv",
                f"CODEX_GATEWAY_ALLOWED_ROOTS={dotenv_root}",
                f"CODEX_GATEWAY_DEFAULT_CWD={dotenv_root}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN", "from-env")
    monkeypatch.setenv("CODEX_GATEWAY_ALLOWED_ROOTS", str(env_root))
    monkeypatch.setenv("CODEX_GATEWAY_DEFAULT_CWD", str(env_cwd))

    settings = get_telegram_settings()

    assert settings.bot_token == "from-env"
    assert settings.allowed_roots == (env_root.resolve(strict=False),)
    assert settings.default_cwd == env_cwd.resolve(strict=False)


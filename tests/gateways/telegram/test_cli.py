from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from codex_gateway import __main__ as cli
from codex_gateway.gateways.telegram.access import AccessManager
from codex_gateway.gateways.telegram.config import get_telegram_settings
from codex_gateway.gateways.telegram.state import TelegramStateStore


class FakeTelegramBotAPI:
    sent_messages: list[dict[str, object]] = []
    closed_count = 0

    def __init__(self, token: str) -> None:
        self.token = token

    async def send_message(self, chat_id: str | int, text: str, **kwargs: object) -> list[dict[str, object]]:
        type(self).sent_messages.append({"token": self.token, "chat_id": chat_id, "text": text, **kwargs})
        return []

    async def aclose(self) -> None:
        type(self).closed_count += 1


def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    cwd = root / "codex-gateway"
    cwd.mkdir(parents=True)
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CODEX_GATEWAY_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("CODEX_GATEWAY_DEFAULT_CWD", str(cwd))
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID", "123")
    monkeypatch.delenv("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN", raising=False)


def test_telegram_status_prints_sanitized_summary(monkeypatch, tmp_path: Path, capsys) -> None:
    configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN", "local-secret-token")

    cli.main(["telegram", "status"])

    captured = capsys.readouterr().out
    output = json.loads(captured)
    assert output["bot_token_configured"] is True
    assert "local-secret-token" not in captured
    assert output["allowed_users"] == 0


def test_telegram_access_pair_allow_remove(monkeypatch, tmp_path: Path, capsys) -> None:
    configure_env(monkeypatch, tmp_path)
    settings = get_telegram_settings()
    code = AccessManager(TelegramStateStore(settings.state_dir)).create_pairing_code("123", username="xtian")

    cli.main(["telegram", "access", "pair", code])
    pair_output = json.loads(capsys.readouterr().out)
    assert pair_output["user_id"] == "123"
    assert pair_output["paired"] is True
    assert pair_output["telegram_notified"] is False

    cli.main(["telegram", "access", "allow", "--user-id", "123"])
    allow_output = json.loads(capsys.readouterr().out)
    assert allow_output["allowed"] is True

    cli.main(["telegram", "access", "status"])
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["allowed_users"] == ["123"]

    cli.main(["telegram", "access", "remove", "--user-id", "123"])
    remove_output = json.loads(capsys.readouterr().out)
    assert remove_output["removed"] is True


def test_telegram_access_pair_notifies_telegram_when_code_has_chat_id(monkeypatch, tmp_path: Path, capsys) -> None:
    configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CODEX_GATEWAY_TELEGRAM_BOT_TOKEN", "notify-token")
    FakeTelegramBotAPI.sent_messages = []
    FakeTelegramBotAPI.closed_count = 0
    monkeypatch.setattr(cli, "TelegramBotAPI", FakeTelegramBotAPI)
    settings = get_telegram_settings()
    code = AccessManager(TelegramStateStore(settings.state_dir)).create_pairing_code(
        "123",
        username="xtian",
        chat_id=42,
    )

    cli.main(["telegram", "access", "pair", code])

    pair_output = json.loads(capsys.readouterr().out)
    assert pair_output["user_id"] == "123"
    assert pair_output["paired"] is True
    assert pair_output["telegram_notified"] is True
    assert FakeTelegramBotAPI.sent_messages == [
        {
            "token": "notify-token",
            "chat_id": "42",
            "text": "Pairing complete. You can now send messages here to use Codex Gateway.",
        }
    ]
    assert FakeTelegramBotAPI.closed_count == 1


def test_telegram_workspace_list(monkeypatch, tmp_path: Path, capsys) -> None:
    configure_env(monkeypatch, tmp_path)

    cli.main(["telegram", "workspace", "list"])

    output = json.loads(capsys.readouterr().out)
    assert output["default_cwd"].endswith("codex-gateway")
    assert len(output["allowed_roots"]) == 1


def test_telegram_run_dispatches_runner(monkeypatch, tmp_path: Path) -> None:
    configure_env(monkeypatch, tmp_path)
    called = {"value": False}

    async def fake_runner() -> None:
        called["value"] = True

    monkeypatch.setattr(cli, "run_telegram_bridge", fake_runner)

    cli.main(["telegram", "run"])

    assert called["value"] is True


def test_telegram_setup_writes_relative_env_without_pairing_code(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    cli.main(
        [
            "telegram",
            "setup",
            "--bot-token",
            "setup-token",
            "--user-id",
            "123",
            "--allowed-root",
            ".",
            "--default-cwd",
            ".",
            "--state-dir",
            ".codex-gateway/telegram",
            "--env-file",
            ".env",
        ]
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=setup-token" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123" in env_text
    assert "CODEX_GATEWAY_ALLOWED_ROOTS=." in env_text
    assert "CODEX_GATEWAY_DEFAULT_CWD=." in env_text
    assert "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:workspace" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY=on-request" in env_text
    assert str(tmp_path) not in env_text

    output = capsys.readouterr().out
    assert "Default permission profile: Default." in output
    assert "Only the configured Telegram user can send /start to get the pairing command." in output
    assert re.search(r"/start [A-Z0-9]{4}-[A-Z0-9]{4}", output) is None

    cli.main(["telegram", "access", "status"])
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["pairing_codes"] == 0


def test_telegram_setup_defaults_to_workspace_directory(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        if prompt == "Telegram user ID: ":
            return "123"
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    cli.main(
        [
            "telegram",
            "setup",
            "--bot-token",
            "setup-token",
            "--env-file",
            ".env",
        ]
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_GATEWAY_ALLOWED_ROOTS=workspace" in env_text
    assert "CODEX_GATEWAY_DEFAULT_CWD=workspace" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:workspace" in env_text
    assert (tmp_path / "workspace").is_dir()
    assert prompts == ["Telegram user ID: ", "Workspace root(s) [workspace]: "]

    output = capsys.readouterr().out
    assert "Example: C:\\codex-workspace" in output
    assert "multiple directories separated by semicolon or comma" in output
    assert "uv run codex-gateway telegram status" in output
    assert "uv run codex-gateway telegram run" in output


def test_telegram_setup_prints_token_and_workspace_guidance(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    prompts: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return "setup-token"

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        if prompt == "Telegram user ID: ":
            return "123"
        return ""

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", fake_input)

    cli.main(["telegram", "setup", "--env-file", ".env"])

    output = capsys.readouterr().out
    assert "Telegram Bot Token" in output
    assert "In Telegram, open @BotFather, run /newbot, then paste the token it gives you." in output
    assert "Telegram User ID" in output
    assert "In Telegram, open @userinfobot and copy your numeric ID." in output
    assert "Example: C:\\codex-workspace" in output
    assert prompts == [
        "Telegram bot token: ",
        "Telegram user ID: ",
        "Workspace root(s) [workspace]: ",
        "Select profile [2 Default]: ",
    ]
    assert "Default Permission Profile" in output


def test_telegram_setup_prompts_for_default_workspace_when_roots_are_multiple(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        if prompt == "Telegram user ID: ":
            return "123"
        if prompt == "Workspace root(s) [workspace]: ":
            return "repo-a,repo-b"
        if prompt == "Default workspace [repo-a]: ":
            return "repo-b"
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    cli.main(["telegram", "setup", "--bot-token", "setup-token", "--env-file", ".env"])

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_GATEWAY_ALLOWED_ROOTS=repo-a,repo-b" in env_text
    assert "CODEX_GATEWAY_DEFAULT_CWD=repo-b" in env_text
    assert prompts == [
        "Telegram user ID: ",
        "Workspace root(s) [workspace]: ",
        "Default workspace [repo-a]: ",
    ]


def test_telegram_setup_accepts_absolute_default_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    cli.main(
        [
            "telegram",
            "setup",
            "--bot-token",
            "setup-token",
            "--user-id",
            "123",
            "--allowed-root",
            str(tmp_path),
            "--default-cwd",
            str(workspace),
            "--state-dir",
            ".codex-gateway/telegram",
            "--env-file",
            ".env",
        ]
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"CODEX_GATEWAY_ALLOWED_ROOTS={tmp_path}" in env_text
    assert f"CODEX_GATEWAY_DEFAULT_CWD={workspace}" in env_text


def test_telegram_setup_reuses_existing_env_for_targeted_permission_update(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=existing-token",
                "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123",
                "CODEX_GATEWAY_ALLOWED_ROOTS=.",
                "CODEX_GATEWAY_DEFAULT_CWD=.",
                "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram",
                "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:workspace",
                "CODEX_GATEWAY_TELEGRAM_SANDBOX=workspace-write",
                "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY=on-request",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_getpass(prompt: str) -> str:
        raise AssertionError(f"unexpected secret prompt: {prompt}")

    def fail_input(prompt: str) -> str:
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr("getpass.getpass", fail_getpass)
    monkeypatch.setattr("builtins.input", fail_input)

    cli.main(["telegram", "setup", "--env-file", ".env", "--permission-profile", "full-access"])

    output = capsys.readouterr().out
    assert "Existing Setup Detected (.env)" in output
    assert "Telegram bot token" in output
    assert "found; Enter keeps it" in output
    assert "Telegram user ID     123" in output
    assert "Workspace root(s)    ." in output
    assert "Default workspace    ." in output
    assert "Permission profile   Default" in output

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=existing-token" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123" in env_text
    assert "CODEX_GATEWAY_ALLOWED_ROOTS=." in env_text
    assert "CODEX_GATEWAY_DEFAULT_CWD=." in env_text
    assert "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:danger-full-access" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_SANDBOX=danger-full-access" in env_text
    assert "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY=never" in env_text


def test_telegram_setup_formats_existing_env_prompts(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODEX_GATEWAY_TELEGRAM_BOT_TOKEN=existing-token",
                "CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID=123",
                "CODEX_GATEWAY_ALLOWED_ROOTS=.",
                "CODEX_GATEWAY_DEFAULT_CWD=.",
                "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram",
                "CODEX_GATEWAY_TELEGRAM_PERMISSION_PROFILE=:danger-full-access",
                "CODEX_GATEWAY_TELEGRAM_SANDBOX=danger-full-access",
                "CODEX_GATEWAY_TELEGRAM_APPROVAL_POLICY=never",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    prompts: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", fake_input)

    cli.main(["telegram", "setup", "--env-file", ".env"])

    output = capsys.readouterr().out
    assert "Existing Setup Detected (.env)" in output
    assert "Permission profile   Full Access" in output
    assert prompts == [
        "Telegram bot token [existing token found; Enter keeps it]: ",
        "Telegram user ID [existing 123; Enter keeps it]: ",
        "Workspace root(s) [existing .; Enter keeps it]: ",
        "Select profile [existing 4 Full Access; Enter keeps it]: ",
    ]


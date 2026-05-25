from __future__ import annotations

from pathlib import Path

import pytest

from codex_gateway.gateways.telegram.config import (
    TelegramSettingsError,
    get_telegram_settings,
    is_path_within_any_root,
    resolve_workspace,
)


def test_defaults_use_relative_project_locations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CODEX_GATEWAY_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("CODEX_GATEWAY_DEFAULT_CWD", raising=False)
    monkeypatch.delenv("CODEX_GATEWAY_TELEGRAM_STATE_DIR", raising=False)
    monkeypatch.delenv("CODEX_GATEWAY_TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("CODEX_GATEWAY_TELEGRAM_USER_ID", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_DEFAULT_CWD", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_STATE_DIR", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("CODEX_TELEGRAM_USER_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    settings = get_telegram_settings()

    workspace = tmp_path / "workspace"
    assert settings.allowed_roots == (workspace.resolve(strict=False),)
    assert settings.default_cwd == workspace.resolve(strict=False)
    assert settings.state_dir == (tmp_path / ".codex-gateway" / "telegram").resolve(strict=False)


def test_resolve_relative_workspace_under_allowed_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("CODEX_TELEGRAM_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("CODEX_TELEGRAM_DEFAULT_CWD", str(root))

    settings = get_telegram_settings()

    assert resolve_workspace(settings, "repo-a") == (root / "repo-a").resolve(strict=False)


def test_resolve_absolute_workspace_under_allowed_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    repo = root / "repo-a"
    monkeypatch.setenv("CODEX_TELEGRAM_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("CODEX_TELEGRAM_DEFAULT_CWD", str(root))

    settings = get_telegram_settings()

    assert resolve_workspace(settings, str(repo)) == repo.resolve(strict=False)


def test_resolve_rejects_absolute_workspace_outside_allowed_roots(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    other = tmp_path / "other" / "repo-a"
    root.mkdir()
    monkeypatch.setenv("CODEX_TELEGRAM_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("CODEX_TELEGRAM_DEFAULT_CWD", str(root))

    settings = get_telegram_settings()

    with pytest.raises(TelegramSettingsError, match="allowed roots"):
        resolve_workspace(settings, str(other))


def test_resolve_rejects_relative_workspace_escape(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("CODEX_TELEGRAM_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("CODEX_TELEGRAM_DEFAULT_CWD", str(root))

    settings = get_telegram_settings()

    with pytest.raises(TelegramSettingsError, match="allowed roots"):
        resolve_workspace(settings, "..\\Windows")


def test_path_containment_uses_resolved_paths(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    inside = root / "nested" / ".." / "repo"
    outside = tmp_path / "root-other"

    assert is_path_within_any_root(inside, [root])
    assert not is_path_within_any_root(outside, [root])


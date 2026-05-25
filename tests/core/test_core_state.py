from __future__ import annotations

from pathlib import Path

import pytest

from codex_gateway.core.projects import WorkspaceScope, WorkspaceScopeError
from codex_gateway.core.results import CommandResult
from codex_gateway.core.threads import ThreadRecord, thread_key


def test_workspace_scope_resolves_relative_paths_under_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    scope = WorkspaceScope(default_cwd=root, allowed_roots=(root,))

    assert scope.resolve("repo") == (root / "repo").resolve(strict=False)


def test_workspace_scope_rejects_paths_outside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    other = tmp_path / "other"
    root.mkdir()
    scope = WorkspaceScope(default_cwd=root, allowed_roots=(root,))

    with pytest.raises(WorkspaceScopeError):
        scope.resolve(other)


def test_thread_record_key_is_channel_neutral(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    record = ThreadRecord(scope_id="telegram:42", workspace=workspace, thread_id="thr_1")

    assert record.key == thread_key("telegram:42", workspace)


def test_command_result_constructors_capture_user_visible_text() -> None:
    assert CommandResult.success("synced").ok is True
    assert CommandResult.unsupported("missing method").message == "missing method"

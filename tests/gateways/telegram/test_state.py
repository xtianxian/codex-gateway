from __future__ import annotations

from pathlib import Path

import pytest

from codex_gateway.gateways.telegram.state import TelegramStateError, TelegramStateStore


def test_state_store_creates_root_and_empty_default_objects(tmp_path: Path) -> None:
    root = tmp_path / "state"
    store = TelegramStateStore(root)

    assert store.load_access() == {"allowed_users": {}, "pairing_codes": {}}
    assert store.load_chats() == {}
    assert store.load_threads() == {}
    assert store.load_pending_approvals() == {}
    assert store.load_pending_user_inputs() == {}
    assert store.load_pending_selections() == {}
    assert root.is_dir()


def test_state_store_atomic_write_read_round_trip(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)
    data = {"allowed_users": {"123": {"username": "gatewayuser"}}, "pairing_codes": {}}

    store.save_access(data)

    assert store.load_access() == data
    assert not list(tmp_path.glob("*.tmp.*"))


def test_state_store_corrupt_json_raises_clear_error(tmp_path: Path) -> None:
    (tmp_path / "threads.json").write_text("{not json", encoding="utf-8")
    store = TelegramStateStore(tmp_path)

    with pytest.raises(TelegramStateError, match="corrupt"):
        store.load_threads()


def test_thread_key_uses_chat_id_and_normalized_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo" / ".." / "repo"

    key = TelegramStateStore.thread_key(-100123, workspace)

    assert key == f"chat_id:-100123|cwd:{workspace.resolve(strict=False)}"


def test_chat_key_is_stable() -> None:
    assert TelegramStateStore.chat_key("-100123") == "chat_id:-100123"


def test_pending_user_input_state_round_trips(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)

    store.save_pending_user_inputs({"tok": {"chat_id": "42", "request_id": 91}})

    assert store.load_pending_user_inputs()["tok"]["request_id"] == 91


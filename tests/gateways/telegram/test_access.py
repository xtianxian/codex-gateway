from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codex_gateway.gateways.telegram.access import AccessManager
from codex_gateway.gateways.telegram.state import TelegramStateStore


def test_access_is_deny_by_default(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path))

    assert not manager.is_user_allowed("123456")


def test_create_pairing_code_is_short_and_expiring(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path))

    code = manager.create_pairing_code("123456", username="gatewayuser", chat_id=42)

    assert len(code) == 9
    assert code[4] == "-"
    access = manager.store.load_access()
    assert access["pairing_codes"][code]["user_id"] == "123456"
    assert access["pairing_codes"][code]["username"] == "gatewayuser"
    assert access["pairing_codes"][code]["chat_id"] == "42"
    assert access["pairing_codes"][code]["expires_at"].endswith("Z")


def test_configured_pairing_code_is_limited_to_allowed_user(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path), allowed_user_id="123456")

    assert manager.create_pairing_code("999999", username="intruder") is None
    code = manager.create_pairing_code("123456", username="gatewayuser")

    assert code is not None
    assert manager.can_request_pairing("123456")
    assert not manager.can_request_pairing("999999")
    assert manager.store.load_access()["pairing_codes"][code]["user_id"] == "123456"


def test_bot_code_allows_intended_user_when_consumed_locally_before_expiry(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    manager = AccessManager(TelegramStateStore(tmp_path), now_fn=lambda: now)
    code = manager.create_pairing_code("123456", username="gatewayuser", chat_id=42)

    result = manager.consume_pairing_code(code)

    assert result == {"user_id": "123456", "username": "gatewayuser", "chat_id": "42"}
    assert manager.is_user_allowed("123456")
    assert manager.store.load_access()["allowed_users"]["123456"]["username"] == "gatewayuser"
    assert manager.consume_pairing_code(code) is None


def test_configured_pairing_rejects_code_for_different_user(tmp_path: Path) -> None:
    creator = AccessManager(TelegramStateStore(tmp_path))
    code = creator.create_pairing_code("999999")
    assert code is not None
    manager = AccessManager(TelegramStateStore(tmp_path), allowed_user_id="123456")

    assert manager.consume_pairing_code(code) is None
    assert not manager.is_user_allowed("999999")
    assert manager.store.load_access()["pairing_codes"] == {}


def test_expired_pairing_code_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    current = {"value": now}
    manager = AccessManager(
        TelegramStateStore(tmp_path),
        now_fn=lambda: current["value"],
        pairing_ttl_seconds=60,
    )
    code = manager.create_pairing_code("123456")

    current["value"] = now + timedelta(seconds=61)

    assert manager.consume_pairing_code(code) is None
    assert not manager.is_user_allowed("123456")


def test_cli_allow_and_remove_user(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path))

    manager.allow_user("123456", username="gatewayuser", source="cli")
    assert manager.is_user_allowed(123456)

    manager.remove_user("123456")
    assert not manager.is_user_allowed("123456")


def test_configured_allow_user_rejects_different_user(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path), allowed_user_id="123456")

    with pytest.raises(ValueError, match="configured allowlist"):
        manager.allow_user("999999", username="intruder", source="cli")


def test_group_message_requires_allowed_sender(tmp_path: Path) -> None:
    manager = AccessManager(TelegramStateStore(tmp_path))
    manager.allow_user("123456", username="gatewayuser", source="cli")

    assert manager.can_receive_message(chat_id="-100123", user_id="123456")
    assert not manager.can_receive_message(chat_id="-100123", user_id="999999")


def test_callback_answer_must_match_pending_approval_user(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    store = TelegramStateStore(tmp_path)
    store.save_pending_approvals(
        {
            "approval-token": {
                "chat_id": "-100123",
                "user_id": "123456",
                "expires_at": "2026-05-24T00:15:00Z",
            }
        }
    )
    manager = AccessManager(store, now_fn=lambda: now)

    assert manager.can_answer_callback("-100123", "123456", "approval-token")
    assert not manager.can_answer_callback("-100123", "999999", "approval-token")


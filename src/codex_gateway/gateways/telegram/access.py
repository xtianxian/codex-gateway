from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Callable

from .state import TelegramStateStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccessManager:
    def __init__(
        self,
        store: TelegramStateStore,
        *,
        now_fn: Callable[[], datetime] = utc_now,
        pairing_ttl_seconds: int = 600,
        allowed_user_id: str | int | None = None,
    ) -> None:
        self.store = store
        self.now_fn = now_fn
        self.pairing_ttl_seconds = pairing_ttl_seconds
        self.allowed_user_id = _normalize_user_id(allowed_user_id)

    def create_pairing_code(
        self,
        user_id: str | int,
        *,
        username: str | None = None,
        chat_id: str | int | None = None,
    ) -> str | None:
        if self.allowed_user_id is not None and not self._is_configured_user(user_id):
            return None
        code = self._new_code()
        entry = {
            "user_id": str(user_id),
            "username": username or "",
            "expires_at": _format_iso(self.now_fn() + timedelta(seconds=self.pairing_ttl_seconds)),
        }
        if chat_id is not None:
            entry["chat_id"] = str(chat_id)
        access = self.store.load_access()
        access["pairing_codes"][code] = entry
        self.store.save_access(access)
        return code

    def consume_pairing_code(self, code: str) -> dict[str, str] | None:
        access = self.store.load_access()
        entry = access.get("pairing_codes", {}).get(code)
        if not isinstance(entry, dict):
            return None
        normalized_user = str(entry.get("user_id") or "").strip()
        if not normalized_user:
            return None
        if self.allowed_user_id is not None and not self._is_configured_user(normalized_user):
            access["pairing_codes"].pop(code, None)
            self.store.save_access(access)
            return None
        expires_at = _parse_iso(str(entry.get("expires_at") or ""))
        if expires_at <= self.now_fn():
            access["pairing_codes"].pop(code, None)
            self.store.save_access(access)
            return None
        username = str(entry.get("username") or "")
        chat_id = str(entry.get("chat_id") or "")
        access["pairing_codes"].pop(code, None)
        access.setdefault("allowed_users", {})[normalized_user] = {
            "username": username or "",
            "allowed_at": _format_iso(self.now_fn()),
            "source": "pairing",
        }
        self.store.save_access(access)
        result = {"user_id": normalized_user, "username": username}
        if chat_id:
            result["chat_id"] = chat_id
        return result

    def allow_user(
        self,
        user_id: str | int,
        *,
        username: str | None = None,
        source: str = "cli",
    ) -> None:
        if self.allowed_user_id is not None and not self._is_configured_user(user_id):
            raise ValueError("Telegram user ID is not in the configured allowlist.")
        access = self.store.load_access()
        access.setdefault("allowed_users", {})[str(user_id)] = {
            "username": username or "",
            "allowed_at": _format_iso(self.now_fn()),
            "source": source,
        }
        self.store.save_access(access)

    def remove_user(self, user_id: str | int) -> bool:
        access = self.store.load_access()
        removed = access.setdefault("allowed_users", {}).pop(str(user_id), None) is not None
        self.store.save_access(access)
        return removed

    def is_user_allowed(self, user_id: str | int) -> bool:
        return self._is_configured_user(user_id) and str(user_id) in self.store.load_access().get("allowed_users", {})

    def can_request_pairing(self, user_id: str | int) -> bool:
        return self.allowed_user_id is not None and self._is_configured_user(user_id)

    def can_receive_message(self, *, chat_id: str | int, user_id: str | int) -> bool:
        del chat_id
        return self.is_user_allowed(user_id)

    def can_answer_callback(
        self,
        chat_id: str | int,
        user_id: str | int,
        approval_token: str,
    ) -> bool:
        pending = self.store.load_pending_approvals().get(approval_token)
        if not self._is_configured_user(user_id):
            return False
        if not isinstance(pending, dict):
            return False
        if str(pending.get("chat_id")) != str(chat_id):
            return False
        if str(pending.get("user_id")) != str(user_id):
            return False
        expires_at = _parse_iso(str(pending.get("expires_at") or ""))
        return expires_at > self.now_fn()

    def _new_code(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        left = "".join(secrets.choice(alphabet) for _ in range(4))
        right = "".join(secrets.choice(alphabet) for _ in range(4))
        return f"{left}-{right}"

    def _is_configured_user(self, user_id: str | int) -> bool:
        normalized = _normalize_user_id(user_id)
        return normalized is not None and (self.allowed_user_id is None or normalized == self.allowed_user_id)


def _format_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _normalize_user_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

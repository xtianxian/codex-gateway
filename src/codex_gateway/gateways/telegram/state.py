from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


class TelegramStateError(RuntimeError):
    pass


class TelegramStateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)

    def load_access(self) -> dict[str, Any]:
        data = self._read_json("access.json", {"allowed_users": {}, "pairing_codes": {}})
        data.setdefault("allowed_users", {})
        data.setdefault("pairing_codes", {})
        return data

    def save_access(self, data: dict[str, Any]) -> None:
        self._write_json("access.json", data)

    def load_chats(self) -> dict[str, Any]:
        return self._read_json("chats.json", {})

    def save_chats(self, data: dict[str, Any]) -> None:
        self._write_json("chats.json", data)

    def load_threads(self) -> dict[str, Any]:
        return self._read_json("threads.json", {})

    def save_threads(self, data: dict[str, Any]) -> None:
        self._write_json("threads.json", data)

    def load_pending_approvals(self) -> dict[str, Any]:
        return self._read_json("pending_approvals.json", {})

    def save_pending_approvals(self, data: dict[str, Any]) -> None:
        self._write_json("pending_approvals.json", data)

    def load_pending_user_inputs(self) -> dict[str, Any]:
        return self._read_json("pending_user_inputs.json", {})

    def save_pending_user_inputs(self, data: dict[str, Any]) -> None:
        self._write_json("pending_user_inputs.json", data)

    def load_pending_elicitations(self) -> dict[str, Any]:
        return self._read_json("pending_elicitations.json", {})

    def save_pending_elicitations(self, data: dict[str, Any]) -> None:
        self._write_json("pending_elicitations.json", data)

    def load_pending_selections(self) -> dict[str, Any]:
        return self._read_json("pending_selections.json", {})

    def save_pending_selections(self, data: dict[str, Any]) -> None:
        self._write_json("pending_selections.json", data)

    @staticmethod
    def chat_key(chat_id: str | int) -> str:
        return f"chat_id:{chat_id}"

    @staticmethod
    def thread_key(chat_id: str | int, workspace: str | Path) -> str:
        resolved = Path(workspace).expanduser().resolve(strict=False)
        return f"chat_id:{chat_id}|cwd:{resolved}"

    def downloads_dir(self, chat_id: str | int, message_id: str | int) -> Path:
        return self.root / "downloads" / str(chat_id) / str(message_id)

    def _read_json(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        if not path.is_file():
            return dict(default)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TelegramStateError(f"Telegram state file is corrupt: {path}") from exc
        if not isinstance(data, dict):
            raise TelegramStateError(f"Telegram state file is corrupt: {path}")
        return data

    def _write_json(self, name: str, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)

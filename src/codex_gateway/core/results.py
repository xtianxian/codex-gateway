from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    success_value: bool
    message: str
    data: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.success_value

    @classmethod
    def success(cls, message: str, data: dict[str, Any] | None = None) -> "CommandResult":
        return cls(True, message, data)

    @classmethod
    def unsupported(cls, message: str) -> "CommandResult":
        return cls(False, message)

    @classmethod
    def error(cls, message: str, data: dict[str, Any] | None = None) -> "CommandResult":
        return cls(False, message, data)

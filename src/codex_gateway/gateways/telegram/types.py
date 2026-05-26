from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class TurnContext:
    chat_id: str
    user_id: str
    thread_id: str
    turn_id: str
    workspace: Path
    message_id: int | None = None
    auto_name_text: str | None = None
    final_text: str = ""
    tool_replied: bool = False
    auto_replied: bool = False
    completed: bool = False
    plan_text: str = ""
    plan_message_id: int | None = None
    plan_choice_message_id: int | None = None
    plan_selection_group_id: str | None = None
    attachments: dict[str, dict[str, Any]] = field(default_factory=dict)
    output_attachments_sent: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class OutputAttachment:
    key: str
    filename: str
    caption: str
    content_type: str
    path: Path | None = None
    data: bytes | None = None

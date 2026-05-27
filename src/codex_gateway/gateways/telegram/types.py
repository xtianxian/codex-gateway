from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    ambiguous_tool_sends: set[str] = field(default_factory=set)
    started_at: datetime = field(default_factory=_utc_now)
    last_event_at: datetime = field(default_factory=_utc_now)
    last_progress_at: datetime = field(default_factory=_utc_now)
    last_progress_kind: str = "turn_started"
    waiting_on_user: bool = False
    waiting_prompt_type: str | None = None
    background_activity_seen: bool = False
    terminal_seen: bool = False
    completed_at: datetime | None = None
    interrupted_at: datetime | None = None
    reconcile_attempts: int = 0
    last_reconcile_at: datetime | None = None
    last_user_status_notice_at: datetime | None = None


@dataclass(frozen=True)
class OutputAttachment:
    key: str
    filename: str
    caption: str
    content_type: str
    path: Path | None = None
    data: bytes | None = None

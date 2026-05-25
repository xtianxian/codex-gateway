from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ThreadRecord:
    scope_id: str
    workspace: Path
    thread_id: str

    @property
    def key(self) -> str:
        return thread_key(self.scope_id, self.workspace)


def thread_key(scope_id: str, workspace: str | Path) -> str:
    resolved = Path(workspace).expanduser().resolve(strict=False)
    return f"scope:{scope_id}|cwd:{resolved}"

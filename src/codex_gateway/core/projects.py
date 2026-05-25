from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceScopeError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceScope:
    default_cwd: Path
    allowed_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_cwd", self.default_cwd.expanduser().resolve(strict=False))
        object.__setattr__(
            self,
            "allowed_roots",
            tuple(root.expanduser().resolve(strict=False) for root in self.allowed_roots),
        )
        if not self.contains(self.default_cwd):
            raise WorkspaceScopeError(f"Default cwd is outside allowed roots: {self.default_cwd}")

    def contains(self, path: str | Path) -> bool:
        resolved_path = Path(path).expanduser().resolve(strict=False)
        for root in self.allowed_roots:
            try:
                resolved_path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def resolve(self, workspace: str | Path | None = None) -> Path:
        if workspace is None or str(workspace).strip() == "":
            return self.default_cwd
        candidate = Path(workspace).expanduser()
        candidates = [candidate.resolve(strict=False)] if candidate.is_absolute() else [
            (root / candidate).resolve(strict=False) for root in self.allowed_roots
        ]
        for resolved in candidates:
            if self.contains(resolved):
                return resolved
        raise WorkspaceScopeError(f"Workspace is outside allowed roots: {workspace}")

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitStatusEntry:
    index_status: str
    worktree_status: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class ChangeSet:
    affected_paths: tuple[str, ...]
    tombstoned_paths: tuple[str, ...]
    snapshot_hash: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.affected_paths and not self.tombstoned_paths

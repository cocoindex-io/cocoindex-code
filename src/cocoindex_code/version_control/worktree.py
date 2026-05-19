from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .branch import Branch
from .change_set import ChangeSet, GitStatusEntry
from .repository import Repository


@dataclass(frozen=True)
class Worktree:
    id: str
    path: Path
    name: str
    repository: Repository
    branch: Branch
    dirty: ChangeSet
    status_entries: tuple[GitStatusEntry, ...]

    @property
    def worktree_id(self) -> str:
        return self.id

    @property
    def repo_id(self) -> str:
        return self.repository.id

    @property
    def repo_root(self) -> Path:
        return self.repository.root

    @property
    def git_common_dir(self) -> Path:
        return self.repository.git_common_dir

    @property
    def remote_url(self) -> str:
        return self.repository.remote_url

    @property
    def normalized_remote_url(self) -> str:
        return self.repository.normalized_remote_url

    @property
    def branch_name(self) -> str:
        return self.branch.name

    @property
    def head_commit(self) -> str:
        return self.branch.head_commit

    @property
    def base_ref(self) -> str:
        return self.branch.base_ref

    @property
    def base_commit(self) -> str:
        return self.branch.base_commit

    @property
    def merge_base(self) -> str:
        return self.branch.merge_base

    @property
    def dirty_snapshot_hash(self) -> str | None:
        return self.dirty.snapshot_hash

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .layer_kind import LayerKind
from .layer_manifest import LayerManifest
from .layer_paths import LayerPaths


@dataclass(frozen=True)
class Layer:
    id: str
    repo_id: str
    kind: LayerKind
    paths: LayerPaths
    manifest: LayerManifest | None
    ref_name: str | None
    commit_hash: str | None
    base_commit_hash: str | None
    merge_base_hash: str | None
    base_layer_id: str | None
    worktree_id: str | None
    config_hash: str | None
    status: str
    created_at: float
    last_accessed_at: float

    @property
    def layer_id(self) -> str:
        return self.id

    @property
    def source_dir(self) -> Path:
        return self.paths.source

    @property
    def db_dir(self) -> Path:
        return self.paths.db_dir

    @property
    def commit(self) -> str | None:
        return self.commit_hash

    @property
    def base_commit(self) -> str | None:
        return self.base_commit_hash

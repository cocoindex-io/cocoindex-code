from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerManifest:
    affected_paths: frozenset[str]
    tombstoned_paths: frozenset[str]
    created_at: float
    expires_at: float | None

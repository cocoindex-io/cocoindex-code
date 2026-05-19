from __future__ import annotations

from enum import StrEnum


class LayerKind(StrEnum):
    BASE = "base"
    BRANCH = "branch"
    DIRTY = "dirty"

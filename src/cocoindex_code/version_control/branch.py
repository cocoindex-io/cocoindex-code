from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Branch:
    name: str
    head_commit: str
    base_ref: str
    base_commit: str
    merge_base: str

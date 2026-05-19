from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Repository:
    id: str
    root: Path
    git_common_dir: Path
    remote_url: str
    normalized_remote_url: str
    repo_name: str
    repo_relative_root: str
    last_seen_root: Path

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LayerPaths:
    root: Path
    source: Path
    cocoindex_db: Path
    target_sqlite: Path

    @property
    def db_dir(self) -> Path:
        return self.cocoindex_db.parent

    @classmethod
    def for_layer(cls, state_dir: Path, repo_id: str, layer_id: str) -> LayerPaths:
        root = state_dir / "repos" / repo_id / "layers" / layer_id
        db_dir = root / "db"
        return cls(
            root=root,
            source=root / "src",
            cocoindex_db=db_dir / "cocoindex.db",
            target_sqlite=db_dir / "target_sqlite.db",
        )

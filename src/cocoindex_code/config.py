"""Configuration for CocoIndex Code MCP server."""

import os
from dataclasses import dataclass
from pathlib import Path


def _find_root_with_marker(start_dir: Path, marker: str) -> Path | None:
    """Find the nearest parent directory containing the given marker directory."""
    current = start_dir.resolve()
    while current != current.parent:
        if (current / marker).is_dir():
            return current
        current = current.parent
    # Check root directory too
    if (current / marker).is_dir():
        return current
    return None


def _discover_codebase_root() -> Path:
    """
    Discover the codebase root directory.

    Discovery order:
    1. Find nearest parent with `.cocoindex_code` directory
    2. Find nearest parent with `.git` directory
    3. Fall back to current working directory
    """
    cwd = Path.cwd()

    # First, look for existing .cocoindex_code directory
    root = _find_root_with_marker(cwd, ".cocoindex_code")
    if root is not None:
        return root

    # Then, look for .git directory
    root = _find_root_with_marker(cwd, ".git")
    if root is not None:
        return root

    # Fall back to current working directory
    return cwd


@dataclass
class Config:
    """Configuration loaded from environment variables."""

    codebase_root_path: Path
    embedding_model: str
    index_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        # Get root path from env or discover it
        root_path_str = os.environ.get("COCOINDEX_CODE_ROOT_PATH")
        if root_path_str:
            root = Path(root_path_str).resolve()
        else:
            root = _discover_codebase_root()

        # Get embedding model
        # Prefix "sbert/" for SentenceTransformers models, otherwise LiteLLM.
        embedding_model = os.environ.get(
            "COCOINDEX_CODE_EMBEDDING_MODEL",
            "sbert/sentence-transformers/all-MiniLM-L6-v2",
        )

        # Index directory is always under the root
        index_dir = root / ".cocoindex_code"

        return cls(
            codebase_root_path=root,
            embedding_model=embedding_model,
            index_dir=index_dir,
        )

    @property
    def target_sqlite_db_path(self) -> Path:
        """Path to the vector index SQLite database."""
        return self.index_dir / "target_sqlite.db"

    @property
    def cocoindex_db_path(self) -> Path:
        """Path to the CocoIndex state database."""
        return self.index_dir / "cocoindex.db"

"""Configuration management for cocoindex-code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_JINA_PREFIX = "jinaai/"  # kept for reference, not used in auto-detection = "jinaai/"
_SBERT_PREFIX = "sbert/"
_DEFAULT_MODEL = "sbert/jinaai/jina-embeddings-v2-base-code"


def _detect_device() -> str:
    """Return best available compute device, respecting env var override."""
    override = os.environ.get("COCOINDEX_CODE_DEVICE")
    if override:
        return override
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except (ImportError, ModuleNotFoundError):
        return "cpu"


def _find_root_with_marker(start: Path, markers: list[str]) -> Path | None:
    """Walk up from start, return first directory containing any marker."""
    current = start
    while True:
        if any((current / m).exists() for m in markers):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _discover_codebase_root() -> Path:
    """Discover the codebase root directory.

    Discovery order:
    1. Find nearest parent with `.cocoindex_code` directory (re-anchor to previously-indexed tree)
    2. Find nearest parent with any common project root marker
    3. Fall back to current working directory
    """
    cwd = Path.cwd()

    # First, look for existing .cocoindex_code directory
    root = _find_root_with_marker(cwd, [".cocoindex_code"])
    if root is not None:
        return root

    # Then, look for common project root markers
    markers = [".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"]
    root = _find_root_with_marker(cwd, markers)
    return root if root is not None else cwd


@dataclass
class Config:
    """Configuration loaded from environment variables."""

    codebase_root_path: Path
    embedding_model: str
    index_dir: Path
    device: str
    trust_remote_code: bool

    @classmethod
    def from_env(cls) -> Config:
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
            _DEFAULT_MODEL,
        )

        # Index directory is always under the root
        index_dir = root / ".cocoindex_code"

        # Device: auto-detect CUDA or use env override
        device = _detect_device()

        # trust_remote_code: opt-in via env var only.
        # sentence-transformers 5.x+ supports Jina models natively, so
        # auto-enabling this for jinaai/ models causes failures with
        # transformers 5.x (removed find_pruneable_heads_and_indices).
        trust_remote_code = os.environ.get("COCOINDEX_CODE_TRUST_REMOTE_CODE", "").lower() in (
            "1",
            "true",
            "yes",
        )

        return cls(
            codebase_root_path=root,
            embedding_model=embedding_model,
            index_dir=index_dir,
            device=device,
            trust_remote_code=trust_remote_code,
        )

    @property
    def target_sqlite_db_path(self) -> Path:
        """Path to the vector index SQLite database."""
        return self.index_dir / "target_sqlite.db"

    @property
    def cocoindex_db_path(self) -> Path:
        """Path to the CocoIndex state database."""
        return self.index_dir / "cocoindex.db"

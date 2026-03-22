"""Configuration management for cocoindex-code."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .settings import resolve_db_dir

_DEFAULT_MODEL = "sbert/sentence-transformers/all-MiniLM-L6-v2"


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


def _parse_json_string_list_env(var_name: str) -> list[str]:
    """Parse an environment variable as a JSON array of strings."""
    raw_value = os.environ.get(var_name, "")
    if not raw_value.strip():
        return []

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{var_name} must be a JSON array of strings, got invalid JSON") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"{var_name} must be a JSON array of strings")

    result: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise ValueError(f"{var_name} must be a JSON array of strings")
        item = item.strip()
        if item:
            result.append(item)

    return result


@dataclass
class Config:
    """Configuration loaded from environment variables."""

    codebase_root_path: Path
    embedding_model: str
    index_dir: Path
    device: str | None
    extra_extensions: dict[str, str | None]
    excluded_patterns: list[str]

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

        # Index directory: apply DB path mapping if configured
        index_dir = resolve_db_dir(root)

        # Device: auto-detect CUDA or use env override
        device = os.environ.get("COCOINDEX_CODE_DEVICE")

        # Extra file extensions (format: "inc:php,yaml,toml" — optional lang after colon)
        raw_extra = os.environ.get("COCOINDEX_CODE_EXTRA_EXTENSIONS", "")
        extra_extensions: dict[str, str | None] = {}
        for token in raw_extra.split(","):
            token = token.strip()
            if not token:
                continue
            if ":" in token:
                ext, lang = token.split(":", 1)
                extra_extensions[f".{ext.strip()}"] = lang.strip() or None
            else:
                extra_extensions[f".{token}"] = None

        # Excluded file glob patterns
        excluded_patterns = _parse_json_string_list_env("COCOINDEX_CODE_EXCLUDED_PATTERNS")

        return cls(
            codebase_root_path=root,
            embedding_model=embedding_model,
            index_dir=index_dir,
            device=device,
            extra_extensions=extra_extensions,
            excluded_patterns=excluded_patterns,
        )

    @property
    def target_sqlite_db_path(self) -> Path:
        """Path to the vector index SQLite database."""
        return self.index_dir / "target_sqlite.db"

    @property
    def cocoindex_db_path(self) -> Path:
        """Path to the CocoIndex state database."""
        return self.index_dir / "cocoindex.db"


# Module-level singleton — imported directly by all modules that need configuration
config: Config = Config.from_env()

"""YAML settings schema, loading, saving, and path helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cocoindex as _coco
import yaml as _yaml

# ---------------------------------------------------------------------------
# Default file patterns (moved from indexer.py)
# ---------------------------------------------------------------------------

DEFAULT_INCLUDED_PATTERNS: list[str] = [
    "**/*.py",  # Python
    "**/*.pyi",  # Python stubs
    "**/*.js",  # JavaScript
    "**/*.jsx",  # JavaScript React
    "**/*.ts",  # TypeScript
    "**/*.tsx",  # TypeScript React
    "**/*.mjs",  # JavaScript ES modules
    "**/*.cjs",  # JavaScript CommonJS
    "**/*.rs",  # Rust
    "**/*.go",  # Go
    "**/*.java",  # Java
    "**/*.c",  # C
    "**/*.h",  # C/C++ headers
    "**/*.cpp",  # C++
    "**/*.hpp",  # C++ headers
    "**/*.cc",  # C++
    "**/*.cxx",  # C++
    "**/*.hxx",  # C++ headers
    "**/*.hh",  # C++ headers
    "**/*.cs",  # C#
    "**/*.sql",  # SQL
    "**/*.sh",  # Shell
    "**/*.bash",  # Bash
    "**/*.zsh",  # Zsh
    "**/*.md",  # Markdown
    "**/*.mdx",  # MDX
    "**/*.txt",  # Plain text
    "**/*.rst",  # reStructuredText
    "**/*.php",  # PHP
    "**/*.lua",  # Lua
]

DEFAULT_EXCLUDED_PATTERNS: list[str] = [
    "**/.*",  # Hidden directories
    "**/__pycache__",  # Python cache
    "**/node_modules",  # Node.js dependencies
    "**/target",  # Rust/Maven build output
    "**/build/assets",  # Build assets directories
    "**/dist",  # Distribution directories
    "**/vendor/*.*/*",  # Go vendor directory (domain-based paths)
    "**/vendor/*",  # PHP vendor directory
    "**/.cocoindex_code",  # Our own index directory
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingSettings:
    provider: str = "sentence-transformers"
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str | None = None


@dataclass
class UserSettings:
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    envs: dict[str, str] = field(default_factory=dict)


@dataclass
class LanguageOverride:
    ext: str  # without dot, e.g. "inc"
    lang: str  # e.g. "php"


@dataclass
class ProjectSettings:
    include_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDED_PATTERNS))
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS))
    language_overrides: list[LanguageOverride] = field(default_factory=list)


# CocoIndex context key for project settings
PROJECT_SETTINGS = _coco.ContextKey[ProjectSettings]("project_settings")

# ---------------------------------------------------------------------------
# Default factories
# ---------------------------------------------------------------------------


def default_user_settings() -> UserSettings:
    return UserSettings()


def default_project_settings() -> ProjectSettings:
    return ProjectSettings()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SETTINGS_DIR_NAME = ".cocoindex_code"
_SETTINGS_FILE_NAME = "settings.yml"  # project-level
_USER_SETTINGS_FILE_NAME = "global_settings.yml"  # user-level


def user_settings_dir() -> Path:
    """Return ``~/.cocoindex_code/``.

    Respects ``COCOINDEX_CODE_DIR`` env var for overriding the base directory.
    """
    import os

    override = os.environ.get("COCOINDEX_CODE_DIR")
    if override:
        return Path(override)
    return Path.home() / _SETTINGS_DIR_NAME


def user_settings_path() -> Path:
    """Return ``~/.cocoindex_code/global_settings.yml``."""
    return user_settings_dir() / _USER_SETTINGS_FILE_NAME


def project_settings_path(project_root: Path) -> Path:
    """Return ``$PROJECT_ROOT/.cocoindex_code/settings.yml``."""
    return project_root / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME


def find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for ``.cocoindex_code/settings.yml``.

    Returns the directory containing it, or ``None``.
    """
    current = start.resolve()
    while True:
        if (current / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME).is_file():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def find_legacy_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a ``.cocoindex_code/`` dir that contains ``cocoindex.db``.

    Used by the backward-compat ``cocoindex-code`` entrypoint to re-anchor to a
    previously-indexed project tree.  Returns the first matching directory, or ``None``.
    """
    current = start.resolve()
    while True:
        if (current / _SETTINGS_DIR_NAME / "cocoindex.db").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def find_parent_with_marker(start: Path) -> Path | None:
    """Walk up from *start* looking for ``.cocoindex_code/`` or ``.git/``.

    Returns the first directory found, or ``None``.
    Does not consider the home directory or above, to avoid false positives
    on CI runners where ~/.git may exist.
    """
    home = Path.home().resolve()
    current = start.resolve()
    while True:
        # Stop before reaching the home directory (home itself is not a project root)
        if current == home:
            return None
        parent = current.parent
        if parent == current:
            return None
        if (current / _SETTINGS_DIR_NAME).is_dir() or (current / ".git").is_dir():
            return current
        current = parent


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _user_settings_to_dict(settings: UserSettings) -> dict[str, Any]:
    d: dict[str, Any] = {}
    emb: dict[str, Any] = {}
    if settings.embedding.provider != "sentence-transformers":
        emb["provider"] = settings.embedding.provider
    if settings.embedding.model != "sentence-transformers/all-MiniLM-L6-v2":
        emb["model"] = settings.embedding.model
    if settings.embedding.device is not None:
        emb["device"] = settings.embedding.device
    if emb:
        d["embedding"] = emb
    if settings.envs:
        d["envs"] = dict(settings.envs)
    return d


def _user_settings_from_dict(d: dict[str, Any]) -> UserSettings:
    emb_dict = d.get("embedding", {})
    embedding = EmbeddingSettings(
        provider=emb_dict.get("provider", "sentence-transformers"),
        model=emb_dict.get("model", "sentence-transformers/all-MiniLM-L6-v2"),
        device=emb_dict.get("device"),
    )
    envs = d.get("envs", {})
    return UserSettings(embedding=embedding, envs=envs)


def _project_settings_to_dict(settings: ProjectSettings) -> dict[str, Any]:
    d: dict[str, Any] = {
        "include_patterns": settings.include_patterns,
        "exclude_patterns": settings.exclude_patterns,
    }
    if settings.language_overrides:
        d["language_overrides"] = [
            {"ext": lo.ext, "lang": lo.lang} for lo in settings.language_overrides
        ]
    return d


def _project_settings_from_dict(d: dict[str, Any]) -> ProjectSettings:
    overrides = [
        LanguageOverride(ext=lo["ext"], lang=lo["lang"]) for lo in d.get("language_overrides", [])
    ]
    return ProjectSettings(
        include_patterns=d.get("include_patterns", list(DEFAULT_INCLUDED_PATTERNS)),
        exclude_patterns=d.get("exclude_patterns", list(DEFAULT_EXCLUDED_PATTERNS)),
        language_overrides=overrides,
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_user_settings() -> UserSettings:
    """Read ``~/.cocoindex_code/settings.yml``, return defaults if missing."""
    path = user_settings_path()
    if not path.is_file():
        return default_user_settings()
    with open(path) as f:
        data = _yaml.safe_load(f)
    if not data:
        return default_user_settings()
    return _user_settings_from_dict(data)


def save_user_settings(settings: UserSettings) -> Path:
    """Write user settings YAML. Returns path written."""
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        _yaml.safe_dump(_user_settings_to_dict(settings), f, default_flow_style=False)
    return path


def load_project_settings(project_root: Path) -> ProjectSettings:
    """Read ``$PROJECT_ROOT/.cocoindex_code/settings.yml``.

    Raises ``FileNotFoundError`` if the file does not exist.
    """
    path = project_settings_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(f"Project settings not found: {path}")
    with open(path) as f:
        data = _yaml.safe_load(f)
    if not data:
        return default_project_settings()
    return _project_settings_from_dict(data)


def save_project_settings(project_root: Path, settings: ProjectSettings) -> Path:
    """Write project settings YAML. Returns path written."""
    path = project_settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        _yaml.safe_dump(_project_settings_to_dict(settings), f, default_flow_style=False)
    return path

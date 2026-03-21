"""YAML settings schema, loading, saving, and path helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml as _yaml
from pathspec import GitIgnoreSpec

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
    model: str
    provider: str = "litellm"
    device: str | None = None


@dataclass
class UserSettings:
    embedding: EmbeddingSettings
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


# ---------------------------------------------------------------------------
# Default factories
# ---------------------------------------------------------------------------


def default_user_settings() -> UserSettings:
    return UserSettings(
        embedding=EmbeddingSettings(
            provider="sentence-transformers",
            model="sentence-transformers/all-MiniLM-L6-v2",
        )
    )


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


def global_settings_mtime_us() -> int | None:
    """Return the mtime of ``global_settings.yml`` as integer microseconds.

    Returns ``None`` if the file does not exist.  Used by the daemon to record
    the mtime at startup and by the client to detect staleness.
    """
    path = user_settings_path()
    try:
        return int(path.stat().st_mtime * 1_000_000)
    except FileNotFoundError:
        return None


def load_gitignore_spec(project_root: Path) -> GitIgnoreSpec | None:
    """Load a GitIgnoreSpec for the project's ``.gitignore`` if present."""
    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return None
    try:
        lines = gitignore.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines:
        return None
    return GitIgnoreSpec.from_lines(lines)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _user_settings_to_dict(settings: UserSettings) -> dict[str, Any]:
    d: dict[str, Any] = {}
    emb: dict[str, Any] = {
        "provider": settings.embedding.provider,
        "model": settings.embedding.model,
    }
    if settings.embedding.device is not None:
        emb["device"] = settings.embedding.device
    d["embedding"] = emb
    if settings.envs:
        d["envs"] = dict(settings.envs)
    return d


def _user_settings_from_dict(d: dict[str, Any]) -> UserSettings:
    emb_dict = d.get("embedding")
    if not emb_dict or "model" not in emb_dict:
        raise ValueError("Must contain 'embedding' with at least 'model' field")
    # Only pass keys that are present; provider uses dataclass default ("litellm") if omitted
    emb_kwargs: dict[str, Any] = {"model": emb_dict["model"]}
    if "provider" in emb_dict:
        emb_kwargs["provider"] = emb_dict["provider"]
    if "device" in emb_dict:
        emb_kwargs["device"] = emb_dict["device"]
    embedding = EmbeddingSettings(**emb_kwargs)
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
    """Read ``~/.cocoindex_code/global_settings.yml``.

    Raises ``FileNotFoundError`` if missing, ``ValueError`` if incomplete.
    """
    path = user_settings_path()
    if not path.is_file():
        raise FileNotFoundError(f"User settings not found: {path}")
    try:
        with open(path) as f:
            data = _yaml.safe_load(f)
        if not data:
            raise ValueError("File is empty")
        return _user_settings_from_dict(data)
    except Exception as e:
        raise type(e)(f"Error loading {path}: {e}") from e


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
    try:
        with open(path) as f:
            data = _yaml.safe_load(f)
        if not data:
            return default_project_settings()
        return _project_settings_from_dict(data)
    except Exception as e:
        raise type(e)(f"Error loading {path}: {e}") from e


def save_project_settings(project_root: Path, settings: ProjectSettings) -> Path:
    """Write project settings YAML. Returns path written."""
    path = project_settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        _yaml.safe_dump(_project_settings_to_dict(settings), f, default_flow_style=False)
    return path

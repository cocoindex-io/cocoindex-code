"""YAML settings schema, loading, saving, and path helpers."""

from __future__ import annotations

import os
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
    "**/*.rb",  # Ruby
    "**/*.swift",  # Swift
    "**/*.kt",  # Kotlin
    "**/*.kts",  # Kotlin script
    "**/*.scala",  # Scala
    "**/*.r",  # R
    "**/*.html",  # HTML
    "**/*.htm",  # HTML
    "**/*.css",  # CSS
    "**/*.scss",  # SCSS
    "**/*.json",  # JSON
    "**/*.xml",  # XML
    "**/*.yaml",  # YAML
    "**/*.yml",  # YAML
    "**/*.toml",  # TOML
    "**/*.sol",  # Solidity
    "**/*.pas",  # Pascal
    "**/*.dpr",  # Pascal/Delphi
    "**/*.dtd",  # DTD
    "**/*.f",  # Fortran
    "**/*.f90",  # Fortran
    "**/*.f95",  # Fortran
    "**/*.f03",  # Fortran
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
    min_interval_ms: int | None = None


@dataclass
class UserSettings:
    embedding: EmbeddingSettings
    envs: dict[str, str] = field(default_factory=dict)


@dataclass
class LanguageOverride:
    ext: str  # without dot, e.g. "inc"
    lang: str  # e.g. "php"


@dataclass
class ChunkerMapping:
    ext: str  # without dot, e.g. "toml"
    module: str  # "module.path:callable", e.g. "cocoindex_code.toml_chunker:toml_chunker"


@dataclass
class ProjectSettings:
    include_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDED_PATTERNS))
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS))
    language_overrides: list[LanguageOverride] = field(default_factory=list)
    chunkers: list[ChunkerMapping] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default factories
# ---------------------------------------------------------------------------


DEFAULT_ST_MODEL = "Snowflake/snowflake-arctic-embed-xs"


def default_user_settings() -> UserSettings:
    return UserSettings(
        embedding=EmbeddingSettings(
            provider="sentence-transformers",
            model=DEFAULT_ST_MODEL,
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

_ENV_DB_PATH_MAPPING = "COCOINDEX_CODE_DB_PATH_MAPPING"
_ENV_HOST_PATH_MAPPING = "COCOINDEX_CODE_HOST_PATH_MAPPING"


@dataclass
class PathMapping:
    source: Path
    target: Path


def _parse_path_mapping(env_var: str) -> list[PathMapping]:
    """Parse a ``source=target[,source=target...]`` env var.

    Both source and target must be absolute paths. Returns an empty list when
    the env var is unset or blank. Raises ``ValueError`` on malformed entries.
    """
    raw = os.environ.get(env_var, "")
    if not raw.strip():
        return []

    mappings: list[PathMapping] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("=", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"{env_var}: invalid entry {entry!r}, expected format 'source=target'")
        source = Path(parts[0])
        target = Path(parts[1])
        if not source.is_absolute():
            raise ValueError(f"{env_var}: source path must be absolute, got {source!r}")
        if not target.is_absolute():
            raise ValueError(f"{env_var}: target path must be absolute, got {target!r}")
        mappings.append(PathMapping(source=source.resolve(), target=target.resolve()))
    return mappings


def _apply_mapping(mappings: list[PathMapping], path: str | Path, reverse: bool = False) -> str:
    """Rewrite ``path`` through ``mappings``. First prefix match wins.

    ``reverse=False``: rewrites source-prefix → target-prefix (forward).
    ``reverse=True``: rewrites target-prefix → source-prefix (reverse).

    Relative paths and absolute paths with no matching prefix are returned
    unchanged (as ``str``).
    """
    p = Path(path)
    if not p.is_absolute():
        return str(path)
    resolved = p.resolve()
    for m in mappings:
        src, dst = (m.target, m.source) if reverse else (m.source, m.target)
        if resolved == src or resolved.is_relative_to(src):
            rel = resolved.relative_to(src)
            return str(dst / rel) if str(rel) != "." else str(dst)
    return str(path)


_db_path_mapping: list[PathMapping] | None = None
_host_path_mapping: list[PathMapping] | None = None


def resolve_db_dir(project_root: Path) -> Path:
    """Return the directory for database files given a project root.

    Applies ``COCOINDEX_CODE_DB_PATH_MAPPING`` if set, otherwise falls back
    to ``project_root / ".cocoindex_code"``.
    """
    global _db_path_mapping  # noqa: PLW0603
    if _db_path_mapping is None:
        _db_path_mapping = _parse_path_mapping(_ENV_DB_PATH_MAPPING)

    resolved = project_root.resolve()
    for mapping in _db_path_mapping:
        if resolved == mapping.source or resolved.is_relative_to(mapping.source):
            rel = resolved.relative_to(mapping.source)
            return mapping.target / rel
    return project_root / _SETTINGS_DIR_NAME


def get_db_path_mappings() -> list[PathMapping]:
    """Return the parsed DB path mappings from ``COCOINDEX_CODE_DB_PATH_MAPPING``."""
    global _db_path_mapping  # noqa: PLW0603
    if _db_path_mapping is None:
        _db_path_mapping = _parse_path_mapping(_ENV_DB_PATH_MAPPING)
    return list(_db_path_mapping)


def get_host_path_mappings() -> list[PathMapping]:
    """Return the parsed host path mappings from ``COCOINDEX_CODE_HOST_PATH_MAPPING``."""
    global _host_path_mapping  # noqa: PLW0603
    if _host_path_mapping is None:
        _host_path_mapping = _parse_path_mapping(_ENV_HOST_PATH_MAPPING)
    return list(_host_path_mapping)


def format_path_for_display(p: str | Path) -> str:
    """Translate a container path to its host equivalent for user-facing output.

    No-op when ``COCOINDEX_CODE_HOST_PATH_MAPPING`` is unset or when ``p`` is a
    relative path / unmatched absolute path.
    """
    return _apply_mapping(get_host_path_mappings(), p, reverse=False)


def normalize_input_path(p: str | Path) -> str:
    """Translate a host path back to its container form before using it internally.

    Inverse of :func:`format_path_for_display`. No-op when the env var is unset
    or when ``p`` is relative / unmatched.
    """
    return _apply_mapping(get_host_path_mappings(), p, reverse=True)


def _reset_db_path_mapping_cache() -> None:
    """Reset the cached mapping (for tests)."""
    global _db_path_mapping  # noqa: PLW0603
    _db_path_mapping = None


def _reset_host_path_mapping_cache() -> None:
    """Reset the cached mapping (for tests)."""
    global _host_path_mapping  # noqa: PLW0603
    _host_path_mapping = None


_TARGET_SQLITE_DB_NAME = "target_sqlite.db"
_COCOINDEX_DB_NAME = "cocoindex.db"


def target_sqlite_db_path(project_root: Path) -> Path:
    """Return the path to the vector index SQLite database for a project."""
    return resolve_db_dir(project_root) / _TARGET_SQLITE_DB_NAME


def cocoindex_db_path(project_root: Path) -> Path:
    """Return the path to the CocoIndex state database for a project."""
    return resolve_db_dir(project_root) / _COCOINDEX_DB_NAME


def user_settings_dir() -> Path:
    """Return ``~/.cocoindex_code/``.

    Respects ``COCOINDEX_CODE_DIR`` env var for overriding the base directory.
    """
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
        if (current / _SETTINGS_DIR_NAME / _COCOINDEX_DB_NAME).exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def find_parent_with_marker(start: Path) -> Path | None:
    """Walk up from *start* looking for an initialized project or a git repo.

    Match criteria: ``.cocoindex_code/settings.yml`` (a real project marker —
    distinct from a workspace-root ``.cocoindex_code/global_settings.yml``
    which should not trigger this check) or ``.git/``.

    Returns the first directory found, or ``None``. Does not consider the home
    directory or above, to avoid false positives on CI runners where ~/.git
    may exist.
    """
    home = Path.home().resolve()
    current = start.resolve()
    while True:
        if current == home:
            return None
        parent = current.parent
        if parent == current:
            return None
        if (current / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME).is_file() or (
            current / ".git"
        ).is_dir():
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


def _embedding_settings_to_dict(embedding: EmbeddingSettings) -> dict[str, Any]:
    d: dict[str, Any] = {
        "provider": embedding.provider,
        "model": embedding.model,
    }
    if embedding.device is not None:
        d["device"] = embedding.device
    if embedding.min_interval_ms is not None:
        d["min_interval_ms"] = embedding.min_interval_ms
    return d


def _user_settings_to_dict(settings: UserSettings) -> dict[str, Any]:
    d: dict[str, Any] = {"embedding": _embedding_settings_to_dict(settings.embedding)}
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
    if "min_interval_ms" in emb_dict:
        emb_kwargs["min_interval_ms"] = emb_dict["min_interval_ms"]
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
    if settings.chunkers:
        d["chunkers"] = [{"ext": cm.ext, "module": cm.module} for cm in settings.chunkers]
    return d


def _project_settings_from_dict(d: dict[str, Any]) -> ProjectSettings:
    overrides = [
        LanguageOverride(ext=lo["ext"], lang=lo["lang"]) for lo in d.get("language_overrides", [])
    ]
    chunkers = [ChunkerMapping(ext=cm["ext"], module=cm["module"]) for cm in d.get("chunkers", [])]
    return ProjectSettings(
        include_patterns=d.get("include_patterns", list(DEFAULT_INCLUDED_PATTERNS)),
        exclude_patterns=d.get("exclude_patterns", list(DEFAULT_EXCLUDED_PATTERNS)),
        language_overrides=overrides,
        chunkers=chunkers,
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


_INITIAL_HEADER = (
    "# CocoIndex Code — global settings.\n"
    "# After editing this file, run `ccc doctor` to verify your configuration.\n"
    "\n"
)

_INITIAL_ENVS_COMMENT = (
    "\n"
    "# Environment variables to inject into the daemon running in the background.\n"
    "# Uncomment and fill in keys for the LiteLLM providers you plan to use.\n"
    "#\n"
    "# envs:\n"
    "#   OPENAI_API_KEY: ...\n"
    "#   GEMINI_API_KEY: ...\n"
    "#   ANTHROPIC_API_KEY: ...\n"
    "#   VOYAGE_API_KEY: ...\n"
)


def save_initial_user_settings(embedding: EmbeddingSettings) -> Path:
    """Write the initial global_settings.yml with comment hints and env examples.

    Only used by `ccc init` on first-time setup. Emits only the `embedding:`
    block from the input; the `envs:` section is a commented-out template.
    Subsequent programmatic writes use `save_user_settings` and do not
    preserve comments.
    """
    emb_block = _yaml.safe_dump(
        {"embedding": _embedding_settings_to_dict(embedding)},
        default_flow_style=False,
        sort_keys=False,
    )
    content = _INITIAL_HEADER + emb_block + _INITIAL_ENVS_COMMENT

    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
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

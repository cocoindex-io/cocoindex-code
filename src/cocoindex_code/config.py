"""Configuration loading for multi-repository CocoIndex Code workspaces."""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
from yaml import safe_load

logger = logging.getLogger(__name__)


class RepoType(str, Enum):
    local = "local"
    github = "github"


class RepoConfig(BaseModel):
    """Per-repository configuration entry."""

    id: str
    type: RepoType
    path: str | None = None
    repo: str | None = None
    branch: str = "main"
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    refresh_interval_minutes: int = 120
    settings: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_type_fields(self) -> RepoConfig:
        if self.type == RepoType.local and not self.path:
            raise ValueError("Local repo entries must set 'path'")
        if self.type == RepoType.github and not self.repo:
            raise ValueError("GitHub repo entries must set 'repo' in owner/name format")
        if self.type == RepoType.github and self.path:
            raise ValueError("GitHub repo entries should not use 'path'")
        if self.type == RepoType.local and self.repo:
            raise ValueError("Local repo entries should not set 'repo'")
        return self


class GitHubConfig(BaseModel):
    token_env: str = "GITHUB_TOKEN"


class DeclarationsConfig(BaseModel):
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["typescript", "python"])


class ChunkerConfig(BaseModel):
    """Chunker paths for custom Python modules."""

    paths: list[str] = Field(default_factory=list)


class CodebaseConfig(BaseModel):
    """Top-level multi-repository workspace configuration file model."""

    repos: list[RepoConfig]
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    declarations: DeclarationsConfig = Field(default_factory=DeclarationsConfig)
    chunker: ChunkerConfig = Field(default_factory=ChunkerConfig)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)

    def repo_by_id(self, repo_id: str) -> RepoConfig | None:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        return None

    @model_validator(mode="after")
    def _validate_repo_ids(self) -> CodebaseConfig:
        seen: set[str] = set()
        for repo in self.repos:
            if repo.id in seen:
                raise ValueError(f"Duplicate repo id: {repo.id}")
            seen.add(repo.id)
        return self


class RepoConfigFileError(ValueError):
    pass


DEFAULT_CONFIG_FILENAME = "coco-config.yml"
CONFIG_ENV_VAR = "COCOINDEX_CODE_CONFIG"


def _config_path_candidates(config_path: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(config_path)

    if env_path := os.getenv(CONFIG_ENV_VAR):
        candidates.append(Path(env_path))

    cwd = Path.cwd() / DEFAULT_CONFIG_FILENAME
    candidates.append(cwd)

    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / DEFAULT_CONFIG_FILENAME)

    return candidates


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the config path from caller hints and cwd layout."""
    chosen = Path(config_path).expanduser() if config_path is not None else None

    for candidate in _config_path_candidates(chosen):
        if candidate is not None and candidate.is_file():
            resolved = candidate.resolve()
            logger.info(f"Resolved config path: {resolved}")
            return resolved

    if chosen is not None:
        logger.error(f"Config file not found at explicitly provided path: {chosen}")
        raise RepoConfigFileError(f"Config file not found: {chosen}")

    logger.error(f"Could not find {DEFAULT_CONFIG_FILENAME} in any candidate path")
    raise RepoConfigFileError(
        f"Could not find {DEFAULT_CONFIG_FILENAME}. Set --config or {CONFIG_ENV_VAR}."
    )


def _coerce_patterns(raw_patterns: Any) -> list[str]:
    if not isinstance(raw_patterns, list):
        return []
    return [str(item).strip() for item in raw_patterns if isinstance(item, str) and item.strip()]


def load_codebase_config(config_path: str | Path | None = None) -> tuple[CodebaseConfig, Path]:
    """Load configuration from YAML and return it with the resolved source path."""
    logger.debug(f"Loading configuration from: {config_path}")
    resolved = resolve_config_path(config_path)
    raw = safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        logger.error(f"Top-level config must be a YAML mapping, got {type(raw)}")
        raise RepoConfigFileError("Top-level config must be a YAML mapping")

    repos_payload = raw.get("repos")
    if not isinstance(repos_payload, list):
        raise RepoConfigFileError("Top-level config requires 'repos: []'")

    normalized_repos: list[dict[str, Any]] = []
    for repo_payload in repos_payload:
        if not isinstance(repo_payload, dict):
            continue
        payload = dict(repo_payload)
        payload["include_patterns"] = _coerce_patterns(payload.get("include_patterns"))
        payload["exclude_patterns"] = _coerce_patterns(payload.get("exclude_patterns"))
        normalized_repos.append(payload)

    normalized_raw = dict(raw)
    normalized_raw["repos"] = normalized_repos
    normalized_raw["include_patterns"] = _coerce_patterns(raw.get("include_patterns"))
    normalized_raw["exclude_patterns"] = _coerce_patterns(raw.get("exclude_patterns"))

    declarations = raw.get("declarations")
    if isinstance(declarations, dict):
        normalized_declarations = dict(declarations)
        langs = normalized_declarations.get("languages")
        if isinstance(langs, list):
            normalized_declarations["languages"] = [
                str(item).strip().lower()
                for item in langs
                if isinstance(item, str) and item.strip()
            ]
        normalized_raw["declarations"] = normalized_declarations

    config = CodebaseConfig.model_validate(normalized_raw)
    return config, resolved

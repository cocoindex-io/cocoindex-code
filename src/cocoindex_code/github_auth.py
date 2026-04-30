"""Resolve GitHub API credentials: env token first, then GitHub CLI."""

from __future__ import annotations

import os
import shutil
import subprocess

_GH_CLI_UNRESOLVED = object()
_gh_cli_token_cache: object | str | None = _GH_CLI_UNRESOLVED


def reset_gh_cli_token_cache() -> None:
    """Clear cached ``gh auth token`` (for tests or after ``gh auth login``)."""
    global _gh_cli_token_cache
    _gh_cli_token_cache = _GH_CLI_UNRESOLVED


def token_from_gh_cli(*, timeout_s: float = 12.0) -> str | None:
    """Return ``gh auth token`` stdout if ``gh`` is installed and authenticated.

    The result is cached for the process so multi-repo sync does not spawn
    ``gh`` once per GitHub mirror.
    """
    global _gh_cli_token_cache
    if _gh_cli_token_cache is not _GH_CLI_UNRESOLVED:
        return _gh_cli_token_cache if isinstance(_gh_cli_token_cache, str) else None

    gh = shutil.which("gh")
    if not gh:
        _gh_cli_token_cache = None
        return None
    try:
        proc = subprocess.run(
            [gh, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _gh_cli_token_cache = None
        return None
    if proc.returncode != 0:
        _gh_cli_token_cache = None
        return None
    token = (proc.stdout or "").strip()
    resolved: str | None = token or None
    _gh_cli_token_cache = resolved
    return resolved


def resolve_github_token(token_env: str = "GITHUB_TOKEN") -> str | None:
    """Prefer ``${token_env}``; if unset or empty, fall back to ``gh auth token``."""
    raw = os.getenv(token_env)
    if raw and raw.strip():
        return raw.strip()
    return token_from_gh_cli()

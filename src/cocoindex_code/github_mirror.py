"""GitHub repository mirror driven by the trees API."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from fnmatch import translate as fnmatch_translate
from pathlib import Path
from typing import Any, cast

_log = logging.getLogger(__name__)


def _normalize_repo(owner_repo: str) -> str:
    owner, repo = owner_repo.split("/", 1)
    return f"{owner}-{repo}"


@dataclasses.dataclass
class GitHubMirrorResult:
    """Result for one sync pass."""

    repo_id: str
    fetched: int
    skipped: int
    removed: int
    bytes_downloaded: int
    branch: str
    refreshed: bool
    rate_limit_remaining: int | None = None
    errors: list[str] = dataclasses.field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors


class GitHubMirror:
    """Mirror one GitHub repo at a branch and keep a blob-SHA manifest."""

    def __init__(
        self,
        owner_repo: str,
        branch: str,
        include_patterns: list[str],
        exclude_patterns: list[str],
        cache_root: Path,
        token: str | None = None,
    ) -> None:
        if "/" not in owner_repo:
            raise ValueError(f"Invalid owner/repo: {owner_repo}")
        owner, repo = owner_repo.split("/", 1)
        if not owner or not repo:
            raise ValueError(f"Invalid owner/repo: {owner_repo}")

        self.owner = owner
        self.repo = repo
        self.repo_id = _normalize_repo(owner_repo)
        self.branch = branch
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns
        self.token = token
        self.cache_root = cache_root
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: int | None = None

        # Pre-compile patterns for faster matching (O(1) instead of O(n) per file)
        self._exclude_patterns_compiled = [
            re.compile(fnmatch_translate(p)) for p in exclude_patterns
        ]
        self._include_patterns_compiled = [
            re.compile(fnmatch_translate(p)) for p in include_patterns
        ]

    @property
    def repo_path(self) -> Path:
        return self.cache_root / self.owner / self.repo

    @property
    def manifest_path(self) -> Path:
        return self.repo_path / ".manifest.json"

    @property
    def rate_limit_remaining(self) -> int | None:
        return self._rate_limit_remaining

    @property
    def rate_limit_reset(self) -> int | None:
        return self._rate_limit_reset

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "cocoindex-code/0.2.31",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    def _parse_rate_limit(self, headers: dict[str, str]) -> None:
        """Parse and store rate limit headers, safely handling missing/invalid values."""
        remain = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")

        # Consolidate int conversion logic
        def safe_int(val: str | None) -> int | None:
            if val is None:
                return None
            try:
                return int(val)
            except ValueError:
                return None

        self._rate_limit_remaining = safe_int(remain)
        self._rate_limit_reset = safe_int(reset)

    def _http_json(self, url: str) -> tuple[dict[str, Any], dict[str, str]]:
        """Fetch and parse JSON with retry logic for transient failures."""
        max_retries = 3
        retry_delays = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s

        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=self._build_headers())
                with urllib.request.urlopen(req, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    self._parse_rate_limit(dict(response.headers))
                    return payload, dict(response.headers)
            except urllib.error.HTTPError as exc:
                if attempt < max_retries and exc.code in (429, 503, 504):
                    delay = retry_delays[attempt]
                    _log.debug(
                        f"GitHub API rate limited/unavailable ({exc.code}), retrying in {delay}s"
                    )
                    time.sleep(delay)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub API request failed ({exc.code}) for {url}: {body[:500]}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < max_retries:
                    delay = retry_delays[attempt]
                    _log.debug(f"GitHub API network error, retrying in {delay}s: {exc}")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"GitHub API network error for {url}: {exc}") from exc

        raise RuntimeError(f"GitHub API request failed after {max_retries + 1} attempts: {url}")

    def _http_bytes(self, url: str) -> bytes:
        """Fetch raw bytes with retry logic for transient failures."""
        max_retries = 3
        retry_delays = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s

        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=self._build_headers())
                with urllib.request.urlopen(req, timeout=60) as response:
                    self._parse_rate_limit(dict(response.headers))
                    return cast(bytes, response.read())
            except urllib.error.HTTPError as exc:
                if attempt < max_retries and exc.code in (429, 503, 504):
                    delay = retry_delays[attempt]
                    _log.debug(f"GitHub raw file {exc.code}, retrying in {delay}s: {url}")
                    time.sleep(delay)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub raw file request failed ({exc.code}) for {url}: {body[:500]}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < max_retries:
                    delay = retry_delays[attempt]
                    _log.debug(f"GitHub raw file network error, retrying in {delay}s")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"GitHub raw file request failed for {url}: {exc}") from exc

        raise RuntimeError(
            f"GitHub raw file request failed after {max_retries + 1} attempts: {url}"
        )

    def _should_include(self, path: str) -> bool:
        """Check if path should be included using pre-compiled regex patterns."""
        normalized = path.replace(os.sep, "/")

        # Check excludes first (short-circuit if matched)
        for pattern_re in self._exclude_patterns_compiled:
            if pattern_re.fullmatch(normalized):
                return False

        # Check includes (if specified)
        if not self._include_patterns_compiled:
            return True

        return any(p.fullmatch(normalized) for p in self._include_patterns_compiled)

    def needs_refresh(self, refresh_interval_minutes: int) -> bool:
        manifest = self._load_manifest()
        if not manifest:
            return True
        if manifest.get("branch") != self.branch:
            return True
        synced = manifest.get("synced_at")
        if not isinstance(synced, int):
            return True
        age_seconds = time.time() - synced
        return age_seconds >= max(refresh_interval_minutes, 0) * 60

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return {}
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}
        return {}

    def _save_manifest(self, files: dict[str, str], branch_sha: str | None) -> None:
        self.repo_path.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo": f"{self.owner}/{self.repo}",
            "branch": self.branch,
            "branch_sha": branch_sha,
            "synced_at": int(time.time()),
            "files": files,
        }
        # Use compact format internally; separators for minimal size
        self.manifest_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    def _fetch_tree(self) -> tuple[dict[str, str], str | None]:
        encoded_branch = urllib.parse.quote(self.branch, safe="")
        url = (
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{encoded_branch}"
            "?recursive=1"
        )
        data, _headers = self._http_json(url)
        if data.get("truncated"):
            raise RuntimeError(
                f"GitHub tree response was truncated for {self.owner}/{self.repo}@{self.branch}"
            )

        tree = data.get("tree")
        if not isinstance(tree, list):
            raise RuntimeError(f"Unexpected tree payload for {self.owner}/{self.repo}")

        files: dict[str, str] = {}
        for entry in tree:
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = entry.get("path")
            sha = entry.get("sha")
            if not isinstance(path, str) or not isinstance(sha, str):
                continue
            if self._should_include(path):
                files[path] = sha
        branch_sha = str(data.get("sha")) if isinstance(data.get("sha"), str) else None
        return files, branch_sha

    def _file_url(self, path: str) -> str:
        encoded_branch = urllib.parse.quote(self.branch, safe="")
        encoded_path = urllib.parse.quote(path, safe="/")
        return (
            f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/"
            f"{encoded_branch}/{encoded_path}"
        )

    def _cleanup_removed_files(self, old_paths: set[str], current_paths: set[str]) -> int:
        removed = 0
        for old_path in old_paths - current_paths:
            old_file = self.repo_path / old_path
            if old_file.exists():
                old_file.unlink()
                removed += 1
            for parent in [old_file.parent, *reversed(list(old_file.parents))]:
                if parent == self.repo_path or parent == self.repo_path.parent:
                    break
                if not parent.is_dir():
                    continue
                if any(parent.iterdir()):
                    break
                parent.rmdir()
        return removed

    def sync(self, force: bool = False) -> GitHubMirrorResult:
        self.repo_path.mkdir(parents=True, exist_ok=True)
        previous_payload = self._load_manifest()
        previous_files = previous_payload.get("files", {})
        if not isinstance(previous_files, dict):
            previous_files = {}

        current_files, branch_sha = self._fetch_tree()
        previous: dict[str, str] = {}
        for path, sha in previous_files.items():
            if isinstance(path, str) and isinstance(sha, str):
                previous[path] = sha

        changed: list[str] = []
        skipped = 0
        if force:
            changed = list(current_files)
        else:
            for path, sha in current_files.items():
                if previous.get(path) != sha:
                    changed.append(path)
                else:
                    skipped += 1

        fetched = 0
        bytes_downloaded = 0
        errors: list[str] = []
        for path in changed:
            target = self.repo_path / path
            try:
                raw = self._http_bytes(self._file_url(path))
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            fetched += 1
            bytes_downloaded += len(raw)

        removed = self._cleanup_removed_files(set(previous), set(current_files))

        self._save_manifest(current_files, branch_sha=branch_sha)

        return GitHubMirrorResult(
            repo_id=self.repo_id,
            fetched=fetched,
            skipped=skipped,
            removed=removed,
            bytes_downloaded=bytes_downloaded,
            branch=self.branch,
            refreshed=True,
            rate_limit_remaining=self.rate_limit_remaining,
            errors=errors,
        )

    def status(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        return {
            "repo": f"{self.owner}/{self.repo}",
            "branch": self.branch,
            "synced_at": manifest.get("synced_at"),
            "file_count": len(manifest.get("files", {})),
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset": self.rate_limit_reset,
        }

"""Multi-repo orchestration for unified indexing root + GitHub mirrors."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import CodebaseConfig, RepoConfig, RepoType
from .github_auth import resolve_github_token
from .github_mirror import GitHubMirror, GitHubMirrorResult

logger = logging.getLogger(__name__)


DEFAULT_UNIFIED_ROOT = Path.home() / ".cocoindex_code" / "unified_root"
DEFAULT_GITHUB_CACHE = Path.home() / ".cocoindex_code" / "github_cache"


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _normalize_items(values: Any) -> list[str]:
    """Normalize a list of values into trimmed strings, removing empty entries."""
    if not isinstance(values, list):
        return []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    """Remove duplicates while preserving order (O(n) via dict)."""
    return list(dict.fromkeys(values))


def _dedupe_mappings(values: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove duplicate dictionaries based on sorted key-value tuples."""
    unique: dict[tuple[str, ...], dict[str, str]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        # Create key from sorted k-v pairs (only non-None values)
        normalized = tuple(sorted((str(k), str(v)) for k, v in item.items() if v is not None))
        if normalized not in unique:
            unique[normalized] = {str(k): str(v) for k, v in item.items() if v is not None}
    return list(unique.values())


def _run_command(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 3600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "unknown failure"


def _ccc_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "cocoindex_code.cli", *args]


def _parse_ccc_status_output(output: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "indexing": False,
        "total_chunks": 0,
        "total_files": 0,
        "languages": {},
        "index_exists": False,
    }
    parsing_languages = False
    for raw_line in output.splitlines():
        line = raw_line.strip()

        if line.startswith("Indexing in progress:"):
            match = re.search(
                r"Indexing in progress: (?P<listed>\d+) files listed \| (?P<added>\d+) added, "
                r"(?P<deleted>\d+) deleted, (?P<reprocessed>\d+) reprocessed, "
                r"(?P<unchanged>\d+) unchanged, error: (?P<error>\d+)",
                line,
            )
            if match:
                listed = int(match.group("listed"))
                status["indexing"] = listed > 0
                status["indexing_summary"] = {
                    "listed": listed,
                    "added": int(match.group("added")),
                    "deleted": int(match.group("deleted")),
                    "reprocessed": int(match.group("reprocessed")),
                    "unchanged": int(match.group("unchanged")),
                    "error": int(match.group("error")),
                }

            continue

        if line.startswith("Indexing:"):
            value = line.split(":", 1)[1].strip().lower()
            status["indexing"] = value in {"true", "1", "yes"}
            continue

        if line.startswith("Index DB:"):
            db_path = line.split(":", 1)[1].strip()
            status["index_exists"] = bool(db_path)
            status["index_db"] = db_path
            continue
        if line.startswith("Project:"):
            status["project"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Settings:"):
            status["settings"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Chunks:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    status["total_chunks"] = int(parts[1].strip())
                except ValueError:
                    status["total_chunks"] = 0
            continue
        if line.startswith("Files:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    status["total_files"] = int(parts[1].strip())
                except ValueError:
                    status["total_files"] = 0
            continue
        if line == "Languages:":
            parsing_languages = True
            continue
        if parsing_languages:
            if not line:
                continue
            language_match = re.search(r"^(?P<name>[^:]+):\s*(?P<count>\d+)", line)
            if language_match:
                status["languages"][language_match.group("name").strip()] = int(
                    language_match.group("count")
                )
                continue
            if not raw_line.startswith(" "):
                parsing_languages = False

    return status


def _prefixed_pattern(repo_id: str, pattern: str) -> str:
    normalized = pattern.replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    return f"{repo_id}/{normalized}"


def read_changed_paths_file(path: str | Path) -> list[str]:
    """Read newline-delimited changed paths, ignoring comments and blanks."""
    raw_path = Path(path)
    paths: list[str] = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        value = line.strip().replace("\\", "/")
        if not value or value.startswith("#"):
            continue
        paths.append(value.lstrip("/"))
    return _dedupe(paths)


class MultiRepoOrchestrator:
    """Coordinates mirroring, symlink orchestration, and indexing."""

    def __init__(
        self,
        config: CodebaseConfig,
        config_path: Path,
        unified_root: Path | None = None,
        github_cache: Path | None = None,
        repo_root_hint: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.unified_root = (unified_root or DEFAULT_UNIFIED_ROOT).resolve()
        self.github_cache = github_cache or DEFAULT_GITHUB_CACHE
        self.repo_root_hint = repo_root_hint or config_path.parent
        self._github_mirrors: dict[str, GitHubMirror] = {}

    def _token(self) -> str | None:
        return resolve_github_token(self.config.github.token_env)

    def _iter_repos(self, repo_ids: list[str] | None = None) -> list[RepoConfig]:
        if repo_ids:
            wanted = set(repo_ids)
            found = [repo for repo in self.config.repos if repo.id in wanted]
            missing = wanted.difference({repo.id for repo in found})
            if missing:
                raise ValueError("Unknown repo_id(s): " + ", ".join(sorted(missing)))
            return found
        return list(self.config.repos)

    def repo_ids_for_changed_paths(self, changed_paths: list[str]) -> list[str]:
        """Infer affected configured repo ids from unified-root-prefixed paths."""
        enabled = {repo.id for repo in self.config.repos if repo.enabled}
        affected: list[str] = []
        for path in changed_paths:
            first = path.replace("\\", "/").lstrip("/").split("/", 1)[0]
            if first in enabled:
                affected.append(first)
        return _dedupe(affected)

    def _resolve_local_repo_path(self, repo: RepoConfig) -> Path:
        base = Path(repo.path or ".")
        if not base.is_absolute():
            base = self.repo_root_hint / base
        return base.resolve()

    def _resolve_repo_settings_path(self, repo: RepoConfig) -> Path | None:
        if not repo.settings:
            return None
        base = Path(repo.settings)
        if not base.is_absolute():
            base = self.repo_root_hint / base
        return base.resolve()

    def _load_repo_settings(self, repo: RepoConfig) -> dict[str, Any]:
        settings_path = self._resolve_repo_settings_path(repo)
        if settings_path is not None and settings_path.is_file():
            return _read_yaml(settings_path)
        return {}

    def _coalesced_repo_settings(self, repo: RepoConfig) -> dict[str, Any]:
        settings = self._load_repo_settings(repo)
        # Keep base settings optional for repos with local settings declarations.
        return settings

    def _mirror(self, repo: RepoConfig) -> GitHubMirror:
        mirror = self._github_mirrors.get(repo.id)
        if mirror is not None:
            return mirror

        if repo.repo is None:
            raise ValueError(f"Repository repo field must not be None for repo id={repo.id}")
        mirror = GitHubMirror(
            owner_repo=repo.repo,
            branch=repo.branch,
            include_patterns=repo.include_patterns,
            exclude_patterns=repo.exclude_patterns,
            cache_root=self.github_cache,
            token=self._token(),
        )
        self._github_mirrors[repo.id] = mirror
        return mirror

    def sync_github(
        self, repo_ids: list[str] | None = None, force: bool = False
    ) -> list[GitHubMirrorResult]:
        results: list[GitHubMirrorResult] = []
        for repo in self._iter_repos(repo_ids):
            if not repo.enabled:
                continue
            if repo.type != RepoType.github or not repo.repo:
                continue
            mirror = self._mirror(repo)
            if not force and not mirror.needs_refresh(repo.refresh_interval_minutes):
                continue
            results.append(mirror.sync(force=force))
        return results

    def _link_path(self, link: Path, target: Path) -> None:
        """Create or update symlink efficiently, avoiding unnecessary resolution."""
        logger.debug(f"Creating/updating symlink: {link} -> {target}")

        # Fast path: if link doesn't exist, just create it
        if not link.exists() and not link.is_symlink():
            self.unified_root.mkdir(parents=True, exist_ok=True)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
            logger.debug(f"Created new symlink: {link} -> {target}")
            return

        # Link exists; check if it's already pointing to the right place
        if link.is_symlink():
            try:
                current = link.resolve()
                if current == target.resolve():
                    logger.debug(f"Symlink already correct: {link}")
                    return  # Already correct, skip
            except OSError as e:
                logger.debug(f"Broken symlink at {link}: {e}, will recreate")
            link.unlink()
        elif link.is_dir():
            raise RuntimeError(
                f"Refusing to clobber non-symlink directory at {link}. Remove it first."
            )
        else:
            raise RuntimeError(
                f"Refusing to clobber non-directory entry at {link}. Remove it first."
            )

        # Create the symlink
        self.unified_root.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        logger.debug(f"Recreated symlink: {link} -> {target}")

    def _link_local_repo(self, repo: RepoConfig) -> None:
        target = self._resolve_local_repo_path(repo)
        if not target.exists():
            logger.error(f"Local repo path does not exist: {target} (repo_id={repo.id})")
            raise FileNotFoundError(f"Local repo path does not exist: {target}")
        logger.info(f"Linking local repo {repo.id} from {target}")
        self._link_path(self.unified_root / repo.id, target)

    def _link_github_repo(self, repo: RepoConfig, force_sync: bool = False) -> None:
        mirror = self._mirror(repo)
        if not mirror.repo_path.is_dir():
            logger.info(f"Syncing GitHub repo {repo.id} ({repo.repo}) to {mirror.repo_path}")
            mirror.sync(force=force_sync)
        logger.info(f"Linking GitHub repo {repo.id} from {mirror.repo_path}")
        self._link_path(self.unified_root / repo.id, mirror.repo_path)

    def sync_and_link_repos(
        self,
        repo_ids: list[str] | None = None,
        force: bool = False,
    ) -> list[GitHubMirrorResult]:
        results = self.sync_github(repo_ids=repo_ids, force=force)
        for repo in self._iter_repos(repo_ids):
            if not repo.enabled:
                continue
            if repo.type == RepoType.local:
                self._link_local_repo(repo)
            elif repo.type == RepoType.github:
                self._link_github_repo(repo, force_sync=force)
        return results

    def link_repos(self, repo_ids: list[str] | None = None) -> None:
        for repo in self._iter_repos(repo_ids):
            if not repo.enabled:
                continue
            if repo.type == RepoType.local:
                self._link_local_repo(repo)
            elif repo.type == RepoType.github:
                self._link_github_repo(repo, force_sync=False)

    def merged_settings(self, repo_ids: list[str] | None = None) -> dict[str, Any]:
        include_patterns: list[str] = []
        exclude_patterns: list[str] = []
        chunkers: list[dict[str, str]] = []
        language_overrides: list[dict[str, str]] = []

        for repo in self._iter_repos(repo_ids):
            if not repo.enabled:
                continue
            settings = self._coalesced_repo_settings(repo)

            repo_includes = _normalize_items(settings.get("include_patterns", []))
            repo_excludes = _normalize_items(settings.get("exclude_patterns", []))
            repo_includes.extend(self.config.include_patterns)
            repo_excludes.extend(self.config.exclude_patterns)
            repo_includes.extend(repo.include_patterns)
            repo_excludes.extend(repo.exclude_patterns)

            for pattern in repo_includes:
                prefixed = _prefixed_pattern(repo.id, pattern)
                if prefixed:
                    include_patterns.append(prefixed)
            for pattern in repo_excludes:
                prefixed = _prefixed_pattern(repo.id, pattern)
                if prefixed:
                    exclude_patterns.append(prefixed)

            repo_chunkers = settings.get("chunkers")
            if isinstance(repo_chunkers, list):
                for chunker in repo_chunkers:
                    if not isinstance(chunker, dict):
                        continue
                    ext = chunker.get("ext")
                    module = chunker.get("module")
                    if isinstance(ext, str) and isinstance(module, str):
                        chunkers.append({"ext": ext, "module": module})

            repo_overrides = settings.get("language_overrides")
            if isinstance(repo_overrides, list):
                for override in repo_overrides:
                    if not isinstance(override, dict):
                        continue
                    ext = override.get("ext")
                    lang = override.get("lang")
                    if isinstance(ext, str) and isinstance(lang, str):
                        language_overrides.append({"ext": ext, "lang": lang})

        return {
            "include_patterns": _dedupe(include_patterns),
            "exclude_patterns": _dedupe(exclude_patterns),
            "chunkers": _dedupe_mappings(chunkers),
            "language_overrides": _dedupe_mappings(language_overrides),
        }

    def write_unified_settings(self, settings: dict[str, Any]) -> tuple[Path, bool]:
        settings_file = self.unified_root / ".cocoindex_code" / "settings.yml"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.safe_dump(
            {
                "include_patterns": _dedupe(settings.get("include_patterns", [])),
                "exclude_patterns": _dedupe(settings.get("exclude_patterns", [])),
                "chunkers": _dedupe_mappings(settings.get("chunkers", [])),
                "language_overrides": _dedupe_mappings(settings.get("language_overrides", [])),
            },
            sort_keys=False,
        )
        changed = True
        if settings_file.is_file():
            try:
                changed = settings_file.read_text(encoding="utf-8") != payload
            except OSError:
                changed = True
        if changed:
            settings_file.write_text(payload, encoding="utf-8")
        return settings_file, changed

    def _environment(self) -> dict[str, str]:
        """Build environment with custom chunker paths from config."""
        env = os.environ.copy()

        chunker_paths = []

        # Add config-specified chunker paths
        for chunker_path_str in self.config.chunker.paths:
            path = Path(chunker_path_str).expanduser()
            if not path.is_absolute():
                path = self.repo_root_hint / path
            path = path.resolve()
            if path.exists():
                chunker_paths.append(str(path))

        # Add default repo-root hint as fallback if not disabled
        default_chunker = self.repo_root_hint / "scripts" / "cocoindex" / "chunkers"
        if default_chunker.exists() and str(default_chunker) not in chunker_paths:
            chunker_paths.append(str(default_chunker))

        if chunker_paths:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{os.pathsep.join(chunker_paths)}{os.pathsep}{existing}"
                if existing
                else os.pathsep.join(chunker_paths)
            )

        env["COCOINDEX_CODE_ROOT_PATH"] = str(self.unified_root)
        return env

    def _status_timeout_seconds(self) -> int:
        value = os.getenv("COCOINDEX_CODE_STATUS_TIMEOUT_SECONDS", "20")
        return max(5, int(value))

    def _index_timeout_seconds(self) -> int:
        value = os.getenv("COCOINDEX_CODE_INDEX_TIMEOUT_SECONDS", "3600")
        return max(60, int(value))

    def _index_retry_limit(self) -> int:
        value = os.getenv("COCOINDEX_CODE_INDEX_RETRY_LIMIT", "1")
        return max(0, int(value))

    def _run_ccc_index_with_retry(
        self,
        *,
        env: dict[str, str],
        restart_before_index: bool,
        stop_daemon_first: bool,
    ) -> str:
        if stop_daemon_first:
            try:
                stop = _run_command(
                    _ccc_command("daemon", "stop"),
                    cwd=self.unified_root,
                    env=env,
                    timeout_seconds=30,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"ccc daemon stop timed out after 30s: {exc}")

            if stop.returncode not in (0, 1):
                raise RuntimeError(f"Failed to stop cocoindex daemon: {_command_output(stop)}")

        if restart_before_index:
            restart = _run_command(
                _ccc_command("daemon", "restart"),
                cwd=self.unified_root,
                env=env,
                timeout_seconds=30,
            )
            if restart.returncode not in (0, 1):
                raise RuntimeError(
                    f"Failed to restart cocoindex daemon: {_command_output(restart)}"
                )

        timeout_seconds = self._index_timeout_seconds()
        retry_limit = self._index_retry_limit()
        attempt = 0
        last_error = "unknown failure"

        while attempt <= retry_limit:
            attempt += 1
            try:
                index = _run_command(
                    _ccc_command("index"),
                    cwd=self.unified_root,
                    env=env,
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                last_error = f"ccc index timed out after {timeout_seconds}s: {exc}"
            else:
                if index.returncode == 0:
                    return index.stdout
                last_error = f"ccc index failed: {_command_output(index)}"

            if attempt > retry_limit:
                break

            restart = _run_command(
                _ccc_command("daemon", "restart"),
                cwd=self.unified_root,
                env=env,
                timeout_seconds=30,
            )
            if restart.returncode not in (0, 1):
                raise RuntimeError(
                    f"{last_error}; daemon restart also failed: {_command_output(restart)}"
                )

        raise RuntimeError(last_error)

    def build_unified_index(
        self,
        repo_ids: list[str] | None = None,
        skip_sync: bool = False,
    ) -> str:
        if not skip_sync:
            self.sync_and_link_repos(repo_ids=repo_ids, force=False)
        settings = self.merged_settings(repo_ids=repo_ids)
        self.write_unified_settings(settings)

        env = self._environment()
        return self._run_ccc_index_with_retry(
            env=env,
            restart_before_index=False,
            stop_daemon_first=True,
        )

    def incremental_unified_index(
        self,
        repo_ids: list[str] | None = None,
        skip_sync: bool = False,
        changed_paths: list[str] | None = None,
    ) -> str:
        sync_repo_ids = repo_ids
        if changed_paths and sync_repo_ids is None:
            inferred = self.repo_ids_for_changed_paths(changed_paths)
            sync_repo_ids = inferred or None
        if not skip_sync:
            self.link_repos(repo_ids=sync_repo_ids)
        settings = self.merged_settings(repo_ids=repo_ids)
        _, settings_changed = self.write_unified_settings(settings)
        env = self._environment()
        output = self._run_ccc_index_with_retry(
            env=env,
            restart_before_index=settings_changed,
            stop_daemon_first=False,
        )
        if changed_paths:
            header = (
                f"Changed paths considered: {len(changed_paths)}; "
                f"repo sync scope: {', '.join(sync_repo_ids or ['all'])}"
            )
            return f"{header}\n{output}"
        return output

    def _run_ccc_status(self) -> dict[str, Any]:
        timeout = self._status_timeout_seconds()
        env = self._environment()
        try:
            result = _run_command(
                _ccc_command("status"),
                cwd=self.unified_root,
                env=env,
                timeout_seconds=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ccc status timed out after {timeout}s: {exc}")
        if result.returncode != 0 and "No module named" in _command_output(result):
            restart = _run_command(
                _ccc_command("daemon", "restart"),
                cwd=self.unified_root,
                env=env,
                timeout_seconds=30,
            )
            if restart.returncode == 0:
                result = _run_command(
                    _ccc_command("status"),
                    cwd=self.unified_root,
                    env=env,
                    timeout_seconds=timeout,
                )
        if result.returncode != 0:
            raise RuntimeError(
                f"ccc status failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        parsed = _parse_ccc_status_output(result.stdout)
        parsed["project"] = str(self.unified_root)
        return parsed

    def run_status(self) -> dict[str, Any]:
        repos_payload: list[dict[str, Any]] = []
        for repo in self.config.repos:
            if repo.type == RepoType.local:
                path = self._resolve_local_repo_path(repo)
                reindex_head = path / ".cocoindex_code" / ".reindex.head"
                synced_at = int(reindex_head.stat().st_mtime) if reindex_head.exists() else None
                repos_payload.append(
                    {
                        "id": repo.id,
                        "type": repo.type.value,
                        "enabled": repo.enabled,
                        "path": str(path),
                        "exists": path.exists(),
                        "synced_at": synced_at,
                        "file_count": None,
                        "branch": None,
                    }
                )
            else:
                mirror = self._mirror(repo)
                status = mirror.status()
                repos_payload.append(
                    {
                        "id": repo.id,
                        "type": repo.type.value,
                        "enabled": repo.enabled,
                        "path": str(mirror.repo_path),
                        "branch": status.get("branch"),
                        "synced_at": status.get("synced_at"),
                        "file_count": status.get("file_count"),
                        "rate_limit_remaining": status.get("rate_limit_remaining"),
                        "rate_limit_reset": status.get("rate_limit_reset"),
                    }
                )

        try:
            from cocoindex_code import client

            project_status = client.project_status(str(self.unified_root))
            ccc_status = {
                "indexing": project_status.indexing,
                "total_chunks": project_status.total_chunks,
                "total_files": project_status.total_files,
                "languages": project_status.languages,
                "index_exists": project_status.index_exists,
            }
        except Exception as exc:
            try:
                ccc_status = self._run_ccc_status()
                ccc_status["error"] = None
                ccc_status["fallback"] = "ccc_cli"
            except Exception as cli_exc:
                ccc_status = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "fallback_error": f"{type(cli_exc).__name__}: {cli_exc}",
                }

        return {
            "unified_root": str(self.unified_root),
            "repos": repos_payload,
            "ccc": ccc_status,
        }

    def run_full_index(
        self, repo_ids: list[str] | None = None, force: bool = False
    ) -> tuple[list[GitHubMirrorResult], str]:
        results = self.sync_and_link_repos(repo_ids=repo_ids, force=force)
        index_output = self.build_unified_index(repo_ids=repo_ids, skip_sync=True)
        return results, index_output

from __future__ import annotations

import hashlib
import os
import tarfile
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pygit2

from .branch import Branch
from .change_set import ChangeSet, GitStatusEntry
from .repository import Repository
from .worktree import Worktree


class GitContextError(RuntimeError):
    """Raised when a directory cannot be resolved as a usable Git worktree."""


def _sha_short(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _open_repo(cwd: Path) -> pygit2.Repository:
    try:
        discovered = pygit2.discover_repository(str(cwd))
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise GitContextError(f"No Git repository found from {cwd}") from e
    if discovered is None:
        raise GitContextError(f"No Git repository found from {cwd}")
    try:
        return pygit2.Repository(discovered)
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise GitContextError(f"Cannot open Git repository at {discovered}") from e


def normalize_remote_url(url: str) -> str:
    """Normalize common Git remote URL forms into a stable lowercase identity."""
    raw = url.strip()
    if raw.endswith(".git"):
        raw = raw[:-4]
    if raw.startswith("git@") and ":" in raw:
        host, path = raw[4:].split(":", 1)
        return f"{host.lower()}/{path.strip('/').lower()}"
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/")
        return f"{parsed.netloc.lower()}/{path.lower()}"
    return raw.strip("/").lower()


def _repo_name(normalized_remote_url: str, repo_root: Path) -> str:
    remote_name = normalized_remote_url.rstrip("/").rsplit("/", 1)[-1]
    return remote_name or repo_root.name


def _worktree_name(repo_root: Path) -> str:
    return repo_root.name


def _status_char(flags: int, *, staged: bool) -> str:
    if staged:
        if flags & pygit2.enums.FileStatus.INDEX_NEW:
            return "A"
        if flags & pygit2.enums.FileStatus.INDEX_MODIFIED:
            return "M"
        if flags & pygit2.enums.FileStatus.INDEX_DELETED:
            return "D"
        if flags & pygit2.enums.FileStatus.INDEX_RENAMED:
            return "R"
        if flags & pygit2.enums.FileStatus.INDEX_TYPECHANGE:
            return "T"
    else:
        if flags & pygit2.enums.FileStatus.WT_NEW:
            return "?"
        if flags & pygit2.enums.FileStatus.WT_MODIFIED:
            return "M"
        if flags & pygit2.enums.FileStatus.WT_DELETED:
            return "D"
        if flags & pygit2.enums.FileStatus.WT_RENAMED:
            return "R"
        if flags & pygit2.enums.FileStatus.WT_TYPECHANGE:
            return "T"
        if flags & pygit2.enums.FileStatus.WT_UNREADABLE:
            return "U"
    return " "


def _status_entries(repo: pygit2.Repository) -> tuple[GitStatusEntry, ...]:
    status = repo.status(untracked_files="all", ignored=False)
    if not status:
        return ()
    entries: list[GitStatusEntry] = []
    for path, flags in sorted(status.items()):
        entries.append(
            GitStatusEntry(
                index_status=_status_char(flags, staged=True),
                worktree_status=_status_char(flags, staged=False),
                path=path,
            )
        )
    return tuple(entries)


def _dirty_snapshot_hash(repo_root: Path, entries: tuple[GitStatusEntry, ...]) -> str | None:
    if not entries:
        return None
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: (e.path, e.original_path or "")):
        digest.update(entry.index_status.encode())
        digest.update(entry.worktree_status.encode())
        digest.update(entry.path.encode())
        if entry.original_path is not None:
            digest.update(entry.original_path.encode())
        path = repo_root / entry.path
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:24]


def _resolve_base_ref(repo: pygit2.Repository, requested: str | None) -> str:
    candidates = [requested] if requested else _default_base_ref_candidates(repo)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            repo.revparse_single(candidate)
            return candidate
        except (KeyError, ValueError, pygit2.GitError):
            continue
    if requested:
        raise GitContextError(f"No usable base ref found for {requested}")
    raise GitContextError(
        "No usable default base ref found. Configure an upstream branch or run "
        "`ccc init --base <ref>`."
    )


def _shorten_ref_name(ref_name: str) -> str:
    for prefix in ("refs/remotes/", "refs/heads/"):
        if ref_name.startswith(prefix):
            return ref_name.removeprefix(prefix)
    return ref_name


def _branch_upstream_ref(repo: pygit2.Repository, branch_name: str) -> str | None:
    try:
        branch = repo.branches.local.get(branch_name)
    except (KeyError, ValueError, pygit2.GitError):
        return None
    if branch is None:
        return None
    try:
        upstream = branch.upstream
    except (KeyError, ValueError, pygit2.GitError):
        return None
    if upstream is None:
        return None
    return _shorten_ref_name(upstream.name)


def _current_branch_upstream_ref(repo: pygit2.Repository) -> str | None:
    try:
        return _branch_upstream_ref(repo, repo.head.shorthand)
    except (KeyError, ValueError, pygit2.GitError):
        return None


def _remote_head_refs(repo: pygit2.Repository) -> list[str]:
    refs: list[str] = []
    for ref_name in sorted(repo.references):
        if not ref_name.startswith("refs/remotes/") or not ref_name.endswith("/HEAD"):
            continue
        try:
            ref = repo.lookup_reference(ref_name)
            refs.append(_shorten_ref_name(ref.resolve().name))
        except (KeyError, ValueError, pygit2.GitError):
            continue
    return refs


def _default_base_ref_candidates(repo: pygit2.Repository) -> list[str]:
    candidates: list[str] = []
    upstream = _current_branch_upstream_ref(repo)
    if upstream is not None:
        candidates.append(upstream)
    candidates.extend(_remote_head_refs(repo))
    return list(dict.fromkeys(candidates))


def remote_tracking_ref_for_local_branch(
    cwd: str | os.PathLike[str] | Path,
    branch_name: str,
) -> str | None:
    """Return the configured upstream ref for a local branch, if any."""
    repo = _open_repo(Path(cwd).resolve())
    return _branch_upstream_ref(repo, branch_name)


def _git_common_dir(repo: pygit2.Repository) -> Path:
    git_dir = Path(repo.path).resolve()
    if git_dir.parent.name == "worktrees":
        return git_dir.parent.parent.resolve()
    return git_dir


def _paths_from_status(
    entries: tuple[GitStatusEntry, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    affected: list[str] = []
    tombstoned: list[str] = []
    for entry in entries:
        if entry.index_status == "D" or entry.worktree_status == "D":
            tombstoned.append(entry.path)
        else:
            affected.append(entry.path)
        if entry.original_path is not None:
            tombstoned.append(entry.original_path)
    return tuple(sorted(set(affected))), tuple(sorted(set(tombstoned)))


def resolve_worktree(
    cwd: str | os.PathLike[str] | Path,
    *,
    base_ref: str | None = None,
    index_config_hash: str,
) -> Worktree:
    """Resolve Git identity and dirty state for *cwd*."""
    start = Path(cwd).resolve()
    repo = _open_repo(start)
    if repo.workdir is None:
        raise GitContextError(f"Repository at {repo.path} has no worktree")
    repo_root = Path(repo.workdir).resolve()
    git_common_dir = _git_common_dir(repo)
    try:
        remote_url = repo.remotes["origin"].url
    except (KeyError, IndexError) as e:
        raise GitContextError("Git repository has no origin remote") from e
    if remote_url is None:
        raise GitContextError("Git origin remote has no URL")
    normalized_remote = normalize_remote_url(remote_url)
    try:
        branch_name = repo.head.shorthand or "HEAD"
        head_obj = repo.revparse_single("HEAD")
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise GitContextError("Git repository has no HEAD commit") from e
    head_commit = str(head_obj.id)
    resolved_base_ref = _resolve_base_ref(repo, base_ref)
    try:
        base_obj = repo.revparse_single(resolved_base_ref)
        merge_base_oid = repo.merge_base(head_obj.id, base_obj.id)
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise GitContextError(f"Cannot resolve base ref {resolved_base_ref}") from e
    if merge_base_oid is None:
        raise GitContextError(f"No merge base between HEAD and {resolved_base_ref}")
    base_commit = str(base_obj.id)
    merge_base = str(merge_base_oid)
    status_entries = _status_entries(repo)
    dirty_hash = _dirty_snapshot_hash(repo_root, status_entries)
    affected, tombstoned = _paths_from_status(status_entries)

    repo_relative_root = "."
    repo_id = _sha_short(f"{normalized_remote}\0{repo_relative_root}\0{index_config_hash}")
    worktree_name = _worktree_name(repo_root)
    worktree_id = _sha_short(f"{repo_id}\0{worktree_name}\0{branch_name}")
    repository = Repository(
        id=repo_id,
        root=repo_root,
        git_common_dir=git_common_dir,
        remote_url=remote_url,
        normalized_remote_url=normalized_remote,
        repo_name=_repo_name(normalized_remote, repo_root),
        repo_relative_root=repo_relative_root,
        last_seen_root=repo_root,
    )
    branch = Branch(
        name=branch_name,
        head_commit=head_commit,
        base_ref=resolved_base_ref,
        base_commit=base_commit,
        merge_base=merge_base,
    )
    dirty = ChangeSet(
        affected_paths=affected,
        tombstoned_paths=tombstoned,
        snapshot_hash=dirty_hash,
    )
    return Worktree(
        id=worktree_id,
        path=repo_root,
        name=worktree_name,
        repository=repository,
        branch=branch,
        dirty=dirty,
        status_entries=status_entries,
    )


def branch_changes(repo_root: Path, base: str, head: str) -> ChangeSet:
    repo = _open_repo(repo_root)
    try:
        diff = repo.diff(base, head)
        diff.find_similar()
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise GitContextError(f"Cannot diff {base}..{head}") from e
    if len(diff) == 0:
        return ChangeSet(affected_paths=(), tombstoned_paths=())
    affected: list[str] = []
    tombstoned: list[str] = []
    for patch in diff:
        if patch is None:
            continue
        delta = patch.delta
        status = delta.status_char()
        old_path = delta.old_file.path
        new_path = delta.new_file.path
        if status in {"R", "C"}:
            affected.append(new_path)
            if status == "R":
                tombstoned.append(old_path)
        elif status == "D":
            tombstoned.append(old_path)
        else:
            affected.append(new_path)
    return ChangeSet(
        affected_paths=tuple(sorted(set(affected))),
        tombstoned_paths=tuple(sorted(set(tombstoned))),
    )


def materialize_commit(repo_root: Path, commit: str, source_dir: Path) -> None:
    repo = _open_repo(repo_root)
    obj = repo.revparse_single(commit)
    with tarfile.open(source_dir / ".archive.tar", mode="w") as archive:
        repo.write_archive(obj, archive)
    with tarfile.open(source_dir / ".archive.tar", mode="r:") as archive:
        archive.extractall(source_dir)
    (source_dir / ".archive.tar").unlink(missing_ok=True)


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def materialize_paths_from_commit(
    repo_root: Path, commit: str, paths: tuple[str, ...], source_dir: Path
) -> None:
    repo = _open_repo(repo_root)
    commit_obj = repo.revparse_single(commit)
    for path in paths:
        try:
            entry = commit_obj.tree[path]
            blob = repo[entry.id]
            data = cast(Any, blob).data
        except (KeyError, ValueError, pygit2.GitError):
            continue
        _write_file(source_dir / path, data)


def materialize_paths_from_worktree(
    repo_root: Path, paths: tuple[str, ...], source_dir: Path
) -> None:
    for path in paths:
        source = repo_root / path
        if source.is_file():
            _write_file(source_dir / path, source.read_bytes())

"""Shared source-file walking: pattern + .gitignore matching, reused by the
indexer, the daemon's doctor file-walk, and ``ccc grep``.

The matcher (include/exclude globs + nested ``.gitignore`` awareness) is the
single source of truth for "which files count as part of the project". The
indexer feeds it to CocoIndex's incremental file source; the daemon and ``ccc
grep`` drive a plain :func:`os.walk` over it via :func:`iter_included_files`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePath

from cocoindex.resources.file import FilePathMatcher, PatternFilePathMatcher
from pathspec import GitIgnoreSpec

from .settings import load_gitignore_spec


def _normalize_gitignore_lines(lines: Iterable[str], directory: PurePath) -> list[str]:
    """Normalize .gitignore lines to root-relative gitignore patterns."""
    if directory in (PurePath("."), PurePath("")):
        prefix = ""
    else:
        prefix = f"{directory.as_posix().rstrip('/')}/"

    normalized: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("\\#") or line.startswith("\\!"):
            line = line[1:]
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        body = line.strip()
        if not body:
            continue
        anchor = body.startswith("/")
        if anchor:
            body = body.lstrip("/")
            pattern = f"{prefix}{body}" if prefix else body
        else:
            contains_slash = "/" in body
            base = prefix
            if contains_slash:
                pattern = f"{base}{body}"
            else:
                if base:
                    pattern = f"{base}**/{body}"
                else:
                    pattern = f"**/{body}"
        if negated:
            pattern = f"!{pattern}"
        normalized.append(pattern)
    return normalized


class GitignoreAwareMatcher(FilePathMatcher):
    """Wraps another matcher and applies .gitignore filtering."""

    def __init__(
        self,
        delegate: FilePathMatcher,
        root_spec: GitIgnoreSpec | None,
        project_root: Path,
    ) -> None:
        self._delegate = delegate
        self._root = project_root
        self._spec_cache: dict[PurePath, GitIgnoreSpec | None] = {PurePath("."): root_spec}

    def _spec_for(self, directory: PurePath) -> GitIgnoreSpec | None:
        if directory in self._spec_cache:
            return self._spec_cache[directory]

        parent_dir = directory.parent if directory != PurePath(".") else PurePath(".")
        parent_spec = self._spec_for(parent_dir)
        spec = parent_spec

        gitignore_path = (self._root / directory) / ".gitignore"
        if gitignore_path.is_file():
            try:
                lines = gitignore_path.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                lines = []
            normalized = _normalize_gitignore_lines(lines, directory)
            if normalized:
                new_spec = GitIgnoreSpec.from_lines(normalized)
                spec = new_spec if spec is None else spec + new_spec

        self._spec_cache[directory] = spec
        return spec

    def _is_ignored(self, path: PurePath, is_dir: bool) -> bool:
        directory = path if is_dir else path.parent
        if directory == PurePath(""):
            directory = PurePath(".")
        spec = self._spec_for(directory)
        if spec is None:
            return False
        match_path = path.as_posix()
        if is_dir and not match_path.endswith("/"):
            match_path = f"{match_path}/"
        return spec.match_file(match_path)

    def is_dir_included(self, path: PurePath) -> bool:
        if self._is_ignored(path, True):
            return False
        return self._delegate.is_dir_included(path)

    def is_file_included(self, path: PurePath) -> bool:
        if self._is_ignored(path, False):
            return False
        return self._delegate.is_file_included(path)


def find_git_root(start: Path) -> Path | None:
    """Walk up from ``start`` to the nearest directory holding a ``.git`` entry — a
    directory for a normal repo, or a *file* for a submodule or linked worktree.
    Returns that directory, or ``None`` if ``start`` is not inside a git repo.

    Used to anchor ``.gitignore`` resolution at the real repo root when grepping a
    subdirectory that isn't inside an initialized cocoindex project."""
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def build_matcher(
    project_root: Path,
    included_patterns: list[str],
    excluded_patterns: list[str],
) -> FilePathMatcher:
    """Build the project's file matcher: include/exclude globs plus nested
    ``.gitignore`` awareness anchored at ``project_root``."""
    base_matcher = PatternFilePathMatcher(
        included_patterns=included_patterns,
        excluded_patterns=excluded_patterns,
    )
    return GitignoreAwareMatcher(base_matcher, load_gitignore_spec(project_root), project_root)


def iter_included_files(
    start: Path,
    base: Path,
    matcher: FilePathMatcher,
) -> Iterator[tuple[Path, PurePath]]:
    """Walk ``start`` recursively, yielding ``(absolute_path, path_relative_to_base)``
    for every file ``matcher`` includes, pruning excluded directories.

    ``base`` anchors the relative paths the matcher sees (the project root, so
    its patterns line up); ``start`` is where traversal begins and may be a
    subdirectory of ``base``. Both must be absolute. Traversal is deterministic
    (directories and files are visited in sorted order).
    """
    for dirpath_str, dirnames, filenames in os.walk(start):
        dirpath = Path(dirpath_str)
        rel_dir = PurePath(dirpath.relative_to(base))
        if rel_dir != PurePath(".") and not matcher.is_dir_included(rel_dir):
            dirnames.clear()
            continue
        dirnames.sort()
        for fname in sorted(filenames):
            rel_path = rel_dir / fname if rel_dir != PurePath(".") else PurePath(fname)
            if matcher.is_file_included(rel_path):
                yield dirpath / fname, rel_path

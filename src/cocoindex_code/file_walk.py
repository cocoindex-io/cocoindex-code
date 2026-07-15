"""Shared source-file walking: pattern + .gitignore matching, reused by the
indexer, the daemon's doctor file-walk, ``ccc grep``, and ``ccc search --text``.

The matcher (include/exclude globs + nested ``.gitignore`` awareness) is the
single source of truth for "which files count as part of the project". The
indexer feeds it to CocoIndex's incremental file source; the daemon drives a
plain :func:`os.walk` over it via :func:`iter_included_files`. The local searches
(``grep`` / ``--text``) share the higher-level :func:`iter_project_files`, which
resolves a root to its included files and yields display paths — leaving per-file
language gating to each caller.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePath
from typing import NamedTuple

from cocoindex.resources.file import FilePathMatcher, PatternFilePathMatcher
from pathspec import GitIgnoreSpec

from .settings import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_INCLUDED_PATTERNS,
    find_project_root,
    load_gitignore_spec,
    load_project_settings,
)


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


class ProjectFiles(NamedTuple):
    """A search root resolved to the files the project includes, plus the
    extension→language overrides callers need for their own per-file language
    decisions.

    Shared by ``ccc grep`` and ``ccc search --text``: the *walk* is common, while
    per-file gating differs (grep skips files with no matchable code language;
    text search keeps every included file). So this yields language-agnostic files
    and hands the overrides back for the caller to decide.
    """

    files: Iterator[tuple[Path, str]]
    """``(absolute_path, display_path)`` for every included file, in sorted walk
    order — code, docs, and config alike."""

    ext_overrides: dict[str, str]
    """Project extension→language overrides (e.g. ``{".inc": "php"}``); ``{}``
    outside an initialized project."""


def _language_overrides(project_root: Path | None) -> dict[str, str]:
    """Extension→language overrides from project settings; ``{}`` if no project."""
    if project_root is None:
        return {}
    ps = load_project_settings(project_root)
    return {f".{lo.ext}": lo.lang for lo in ps.language_overrides}


def iter_project_files(root: Path, path_glob: str | None = None) -> ProjectFiles:
    """Resolve ``root`` to the project's included files — the single source of
    truth shared by ``ccc grep`` and ``ccc search --text``.

    Honors the project's include/exclude + nested ``.gitignore`` (or the default
    source-file patterns outside a project) and an optional ``path_glob``. ``root``
    may be a file (searched on its own) or a directory (walked recursively).
    ``files`` yields display paths mirroring ``root`` (e.g. ``src/a.py``); language
    gating is left to the caller.
    """
    resolved = root.resolve()

    if resolved.is_file():
        # A single file: just it, anchored to its enclosing project (if any).
        overrides = _language_overrides(find_project_root(resolved.parent))
        return ProjectFiles(files=iter([(resolved, root.as_posix())]), ext_overrides=overrides)

    project_root = find_project_root(resolved)
    if project_root is not None:
        ps = load_project_settings(project_root)
        included, excluded = ps.include_patterns, ps.exclude_patterns
        ext_overrides = {f".{lo.ext}": lo.lang for lo in ps.language_overrides}
        base = project_root
    else:
        included = list(DEFAULT_INCLUDED_PATTERNS)
        excluded = list(DEFAULT_EXCLUDED_PATTERNS)
        ext_overrides = {}
        # Anchor .gitignore at the enclosing git repo so a subdirectory search
        # still honors the repo-root rules; fall back to the target itself.
        base = find_git_root(resolved) or resolved

    matcher = build_matcher(base, included, excluded)
    path_filter = PatternFilePathMatcher(included_patterns=[path_glob]) if path_glob else None

    def _walk() -> Iterator[tuple[Path, str]]:
        for abs_path, rel in iter_included_files(resolved, base, matcher):
            if path_filter is not None and not path_filter.is_file_included(rel):
                continue
            # Display paths mirror the root the user gave (e.g. "src/a.py").
            yield abs_path, (root / abs_path.relative_to(resolved)).as_posix()

    return ProjectFiles(files=_walk(), ext_overrides=ext_overrides)

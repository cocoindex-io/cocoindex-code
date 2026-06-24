r"""``ccc grep`` — by-example structural code search over files.

Unlike ``ccc search`` (semantic, needs the index + daemon + embeddings), ``grep``
runs entirely locally: it compiles a structural pattern (cocoindex ``code_match``)
once per language, walks the matching source files, and matches them in parallel.
No index or daemon is required.

Patterns use the ``\`` sigil for metavariables, e.g. ``def \NAME(\(ARGS*\)):`` or
``foo(\(ARGS*\))``. See the cocoindex code_match design for the full syntax.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import click
from cocoindex.ops.code import CodeMatch, CodePattern
from cocoindex.ops.text import detect_code_language
from cocoindex.resources.file import PatternFilePathMatcher

from .file_walk import build_matcher, find_git_root, iter_included_files
from .settings import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_INCLUDED_PATTERNS,
    find_project_root,
    load_project_settings,
)

# A trivial, always-valid pattern (a bare identifier) used to probe whether a
# language is structurally matchable, independent of the user's pattern.
_PROBE_PATTERN = "x"


@dataclass(frozen=True, slots=True)
class GrepWarning:
    """A non-fatal problem surfaced during a grep — a file that couldn't be read,
    or a supported language the pattern failed to compile for. grep keeps going;
    the CLI prints these to stderr."""

    message: str

    language: str | None = None
    """Set when this is a per-language pattern-compile failure (so :class:`Grep` can
    tell whether the pattern is unusable for *every* language it tried)."""


@dataclass(frozen=True, slots=True)
class FileMatches:
    """Every match found in one file."""

    path: str
    """Display path, mirroring the root passed to grep (e.g. ``src/a.py`` or
    ``/tmp/x/a.py``)."""

    source: str
    """Full file content — kept so the renderer can show context around each match."""

    matches: list[CodeMatch]
    """Matches in source order (at least one)."""


@dataclass(frozen=True, slots=True)
class GrepRequest:
    """A grep invocation."""

    pattern: str
    root: Path
    """File or directory to search."""

    languages: frozenset[str] | None = None
    """Restrict to these languages (lowercased canonical names); ``None`` = all."""

    path_glob: str | None = None
    """Extra include glob (globset syntax) on the project-relative path; ``None`` = all."""


@dataclass(frozen=True, slots=True)
class _Target:
    path: Path
    display: str
    pattern: CodePattern


# ---------------------------------------------------------------------------
# Per-language pattern compilation
# ---------------------------------------------------------------------------


@functools.cache
def _is_match_supported(language: str) -> bool:
    """Whether code_match can structurally match ``language``.

    Probes with a trivial always-valid pattern so the answer doesn't depend on
    the user's pattern. Cached across the process.
    """
    try:
        CodePattern(_PROBE_PATTERN, language=language)
        return True
    except ValueError:
        return False


class _PatternCompiler:
    r"""Compiles one pattern per language on demand, caching the result.

    code_match patterns are language-bound (compiled against a grammar's token
    table), so we keep one compiled :class:`CodePattern` per language and reuse it
    across every file of that language. A language maps to ``None`` (its files are
    skipped) when either code_match can't match it at all, or the pattern won't
    compile for it — the latter records a :class:`GrepWarning` once for that
    language, so the user learns why those files were skipped instead of the whole
    run aborting.
    """

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self._cache: dict[str, CodePattern | None] = {}
        self.warnings: list[GrepWarning] = []

    def for_language(self, language: str) -> CodePattern | None:
        if language not in self._cache:
            self._cache[language] = self._compile(language)
        return self._cache[language]

    def _compile(self, language: str) -> CodePattern | None:
        try:
            return CodePattern(self._pattern, language=language)
        except ValueError as e:
            # A *supported* language that won't compile the pattern is a real
            # problem to surface (once per language); an *unsupported* language is
            # an expected silent skip. `_is_match_supported` tells them apart,
            # independent of the user's pattern.
            if _is_match_supported(language):
                self.warnings.append(
                    GrepWarning(f"pattern invalid for {language}: {e}", language=language)
                )
            return None


# ---------------------------------------------------------------------------
# Target collection (which files to match, with which compiled pattern)
# ---------------------------------------------------------------------------


def _detect_language(path: Path, ext_overrides: dict[str, str]) -> str | None:
    """Language for ``path``: project extension override first, then auto-detect."""
    return ext_overrides.get(path.suffix) or detect_code_language(filename=path.name)


def _ext_overrides(project_root: Path | None) -> dict[str, str]:
    if project_root is None:
        return {}
    ps = load_project_settings(project_root)
    return {f".{lo.ext}": lo.lang for lo in ps.language_overrides}


def _target_for_file(
    abs_path: Path,
    display: str,
    ext_overrides: dict[str, str],
    req: GrepRequest,
    compiler: _PatternCompiler,
) -> _Target | None:
    language = _detect_language(abs_path, ext_overrides)
    if language is None:
        return None
    if req.languages is not None and language.lower() not in req.languages:
        return None
    cp = compiler.for_language(language)
    if cp is None:
        return None
    return _Target(path=abs_path, display=display, pattern=cp)


def _resolve_file(
    abs_path: Path,
    display: str,
    ext_overrides: dict[str, str],
    req: GrepRequest,
    compiler: _PatternCompiler,
) -> Iterator[_Target | GrepWarning]:
    """Yield any compile warning newly raised for this file's language, then the
    file's target if the pattern compiled for that language."""
    before = len(compiler.warnings)
    target = _target_for_file(abs_path, display, ext_overrides, req, compiler)
    yield from compiler.warnings[before:]
    if target is not None:
        yield target


def _iter_targets(req: GrepRequest, compiler: _PatternCompiler) -> Iterator[_Target | GrepWarning]:
    """Lazily resolve a request into the files to match, yielded as they're
    discovered (so matching can begin before the walk finishes), interleaved with
    each new per-language compile warning.

    Single source of truth with the indexer: the same include/exclude patterns and
    ``.gitignore`` rules decide which files belong to the project. Outside a project
    we fall back to the default source-file patterns.
    """
    root = req.root.resolve()

    if root.is_file():
        project_root = find_project_root(root.parent)
        yield from _resolve_file(
            root, req.root.as_posix(), _ext_overrides(project_root), req, compiler
        )
        return

    project_root = find_project_root(root)
    if project_root is not None:
        ps = load_project_settings(project_root)
        included, excluded = ps.include_patterns, ps.exclude_patterns
        ext_overrides = {f".{lo.ext}": lo.lang for lo in ps.language_overrides}
        base = project_root
    else:
        included = list(DEFAULT_INCLUDED_PATTERNS)
        excluded = list(DEFAULT_EXCLUDED_PATTERNS)
        ext_overrides = {}
        # Anchor at the enclosing git repo so grepping a subdirectory still honors the
        # repo-root (and intervening) .gitignore; fall back to the target dir itself.
        base = find_git_root(root) or root

    matcher = build_matcher(base, included, excluded)
    path_filter = (
        PatternFilePathMatcher(included_patterns=[req.path_glob]) if req.path_glob else None
    )

    for abs_path, rel in iter_included_files(root, base, matcher):
        if path_filter is not None and not path_filter.is_file_included(rel):
            continue
        # Display paths mirror the root the user gave (e.g. "src/a.py", "/tmp/x/a.py"),
        # rather than always being cwd-relative.
        display = (req.root / abs_path.relative_to(root)).as_posix()
        yield from _resolve_file(abs_path, display, ext_overrides, req, compiler)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _match_file(target: _Target) -> FileMatches | GrepWarning | None:
    """Match one file via code_match's ``match_file``: it reads the file and runs
    the prefilter in Rust — skipping the parse (and the Python-side read) for files
    that can't match — all with the GIL released, so a worker-thread pool scans many
    files truly in parallel.

    Returns ``None`` for a file that's binary, prefilter-rejected, or has no matches
    (a silent skip, like ``grep -I``); a :class:`GrepWarning` for an unreadable file.
    """
    try:
        fm = target.pattern.match_file(str(target.path))
    except OSError as e:
        return GrepWarning(f"cannot read {target.display}: {e}")
    if fm is None:
        return None  # binary, prefilter-rejected, or no matches
    matches = sorted(fm.matches, key=lambda m: m.chunks[0].start.byte_offset if m.chunks else 0)
    return FileMatches(path=target.display, source=fm.ast.source, matches=matches)


class Grep:
    """A single grep run. :meth:`run` matches each file as it's listed and streams
    results as they complete. After ``run`` is exhausted, :attr:`unusable` /
    :attr:`failed_languages` report the compile verdict."""

    def __init__(self, req: GrepRequest) -> None:
        self._req = req
        self._compiler = _PatternCompiler(req.pattern)
        self._target_count = 0

    def run(self, emit: Callable[[FileMatches | GrepWarning], object]) -> None:
        """Match the request, calling ``emit`` with each file's matches and each
        compile warning the moment it's ready — *while the walk is still running*.

        Fully synchronous, no event loop: the walk runs on the calling thread and
        submits each file's match to a thread pool. ``match_file`` reads + prefilters
        + parses + matches in Rust with the GIL released, so the pool threads run
        truly in parallel — with the walk and with each other — and hand each result
        straight to ``emit`` as it finishes. ``run`` returns once the walk and every
        match are done; afterwards :attr:`unusable` / :attr:`failed_languages` hold the
        compile verdict.

        ``emit`` is called concurrently from the pool's worker threads (plus this
        thread, for warnings), so a consumer that does I/O must serialize it itself.
        """

        def _match(item: _Target) -> None:
            result = _match_file(item)
            if result is not None:  # None = binary / prefiltered / no match (skip)
                emit(result)

        with ThreadPoolExecutor() as pool:
            for item in _iter_targets(self._req, self._compiler):
                if isinstance(item, GrepWarning):
                    emit(item)
                else:
                    self._target_count += 1
                    pool.submit(_match, item)
            # ThreadPoolExecutor.__exit__ waits for every submitted match to finish.

    @property
    def failed_languages(self) -> list[str]:
        """Supported languages the pattern would not compile for (valid once
        :meth:`run` is exhausted)."""
        return [w.language for w in self._compiler.warnings if w.language is not None]

    @property
    def unusable(self) -> bool:
        """The pattern compiled for *none* of the languages actually encountered — a
        supported language was found but every one rejected the pattern, so there was
        nothing to match. A target exists only for a file whose language compiled, so
        zero targets plus ≥1 failed language means unusable everywhere it was tried —
        distinct from "no matchable files found" (no failures). Valid once
        :meth:`run` is exhausted."""
        return bool(self.failed_languages) and self._target_count == 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _line_char_offsets(source: str) -> list[int]:
    """Char offset of the start of each line (index ``line - 1``)."""
    offsets = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _paint(text: str, color: bool, **style: object) -> str:
    if not color or not text:
        return text
    return click.style(text, **style)  # type: ignore[arg-type]


def _render_code_line(
    line_no: int,
    text: str,
    dim_pre_end: int,
    dim_post_start: int,
    width: int,
    color: bool,
) -> str:
    """One code line: dimmed ``line_no| `` gutter, then the text with the leading
    (before ``dim_pre_end``) and trailing (from ``dim_post_start``) context dimmed
    and the matched span shown normally."""
    pre = max(0, min(dim_pre_end, len(text)))
    post = max(pre, min(dim_post_start, len(text)))
    gutter = _paint(f"{line_no:>{width}}| ", color, fg="bright_black")
    if not color:
        return f"{gutter}{text}"
    before = _paint(text[:pre], color, dim=True)
    matched = text[pre:post]
    after = _paint(text[post:], color, dim=True)
    return f"{gutter}{before}{matched}{after}"


def _render_match(
    src_lines: list[str],
    offsets: list[int],
    match: CodeMatch,
    width: int,
    color: bool,
) -> list[str]:
    chunk = match.chunks[0]
    s_off, e_off = chunk.start.char_offset, chunk.end.char_offset
    s_line, e_line = chunk.start.line, chunk.end.line
    out: list[str] = []
    for line_no in range(s_line, e_line + 1):
        idx = line_no - 1
        text = src_lines[idx] if 0 <= idx < len(src_lines) else ""
        line_start = offsets[idx] if 0 <= idx < len(offsets) else 0
        dim_pre_end = (s_off - line_start) if line_no == s_line else 0
        dim_post_start = (e_off - line_start) if line_no == e_line else len(text)
        out.append(_render_code_line(line_no, text, dim_pre_end, dim_post_start, width, color))
    return out


def render_file(fm: FileMatches, *, color: bool) -> str:
    """Render one file's matches: the path, then each match's line range, with
    matches separated by a ``---`` line."""
    # Split on "\n" to keep line numbers aligned with the offsets below (which
    # count "\n"), then drop the trailing "\r" that CRLF files leave on each line.
    src_lines = [line.rstrip("\r") for line in fm.source.split("\n")]
    offsets = _line_char_offsets(fm.source)
    max_line = max((m.chunks[0].end.line for m in fm.matches if m.chunks), default=1)
    width = len(str(max_line))

    parts = [_paint(fm.path, color, fg="magenta", bold=True)]
    emitted = False
    for match in fm.matches:
        if not match.chunks:
            continue
        if emitted:
            parts.append(_paint("---", color, fg="bright_black"))
        parts.extend(_render_match(src_lines, offsets, match, width, color))
        emitted = True
    return "\n".join(parts)


def render_results(results: list[FileMatches], *, color: bool) -> str:
    """Render a list of per-file matches in the ``ccc grep`` output format, files
    separated by a blank line. The CLI streams with :func:`render_file` instead;
    this is the batch form (used in tests)."""
    return "\n\n".join(render_file(fm, color=color) for fm in results)

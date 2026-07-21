r"""``ccc search --text`` — literal / regex full-text search over files.

Unlike ``ccc search`` (semantic: needs the index + daemon + embeddings), text
search runs entirely locally, like ``ccc grep``: it walks the project's source
files — the *same* include/exclude + ``.gitignore`` rules the indexer uses — and
matches each file's *content* against literal terms and/or regexes. No index or
daemon is required, and results are always fresh.

Query model (a small GitHub-code-search subset):

* One or more **terms**, AND-combined: a file matches only if *every* term
  appears in it (on some line).
* A term wrapped in slashes — ``/expr/`` — is a **regex**, *unless* its body has
  an unescaped ``/`` (then it stays a **literal**, e.g. ``/blobs/v1/``) — matching
  GitHub code search. Any other term is a literal substring. To regex-match a
  literal slash, escape it inside the body: ``/\/foo\//``.
* **Smart-case:** matching is case-insensitive unless the term contains an
  uppercase letter (decided per term), matching ripgrep's default. ``-i`` / ``-s``
  force the choice.

Structure mirrors ``grep.py``: walk → per-file match → stream results as each
file completes.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cocoindex.ops.text import detect_code_language

from .file_walk import iter_project_files
from .render import gutter, gutter_width, paint, path_header


class TermSyntaxError(ValueError):
    """A term could not be parsed / compiled (e.g. an invalid regex)."""


@dataclass(frozen=True, slots=True)
class TextSearchWarning:
    """A non-fatal problem surfaced during a run — e.g. a file that couldn't be
    read. The search keeps going; the CLI prints these to stderr."""

    message: str


@dataclass(frozen=True, slots=True)
class LineMatch:
    """One matching line and the character spans matched within it."""

    line_no: int
    """1-based line number."""

    text: str
    """The line's text, without the trailing newline."""

    spans: list[tuple[int, int]]
    """``(start, end)`` char offsets of matched substrings — sorted and merged."""


@dataclass(frozen=True, slots=True)
class FileMatches:
    """Every matching line found in one file (at least one)."""

    path: str
    """Display path, mirroring the search root (e.g. ``src/a.py``)."""

    matches: list[LineMatch]


@dataclass(frozen=True, slots=True)
class Term:
    """One compiled search term.

    Keeps the metadata a future index needs to decide *per term* whether it can
    use an inverted-index lookup (literal) or must fall back to a scan (regex) —
    the raw text, whether it's a regex, and its case sensitivity — alongside the
    compiled ``pattern`` used for matching today.
    """

    raw: str
    """The term as typed (e.g. ``password`` or ``/def \\w+\\(/``)."""

    is_regex: bool
    """True for a ``/…/`` regex term; False for a literal substring."""

    case_sensitive: bool
    """The resolved case mode (after smart-case / ``-i`` / ``-s``)."""

    pattern: re.Pattern[str]
    """Compiled matcher — the case flag is already baked in."""


@dataclass(frozen=True, slots=True)
class TextSearchRequest:
    """A text-search invocation."""

    terms: tuple[Term, ...]
    """Search terms, AND-combined — a file matches only if every term does."""

    root: Path
    """Directory to search."""

    languages: frozenset[str] | None = None
    """Restrict to files of these languages (lowercased canonical names);
    ``None`` = every included file, regardless of language."""

    path_glob: str | None = None
    """Extra include glob (globset syntax) on the project-relative path."""


# ---------------------------------------------------------------------------
# Term parsing
# ---------------------------------------------------------------------------


def _looks_like_regex(raw: str) -> bool:
    r"""Whether ``raw`` is a ``/…/`` regex term (GitHub-code-search rule).

    A term is a regex only if it is wrapped in slashes *and* its body has no
    *free* (unescaped) ``/``. So ``/def \w+\(/`` is a regex, but ``/blobs/v1/``
    stays a literal (the middle ``/`` is free) — matching GitHub. To regex-match
    a literal slash, escape it inside the body: ``/\/foo\//``.
    """
    if len(raw) < 3 or raw[0] != "/" or raw[-1] != "/":
        return False
    # Drop escaped ``\\`` then ``\/``; any ``/`` still left is a free slash.
    stripped = raw[1:-1].replace("\\\\", "").replace("\\/", "")
    return "/" not in stripped


def compile_terms(raw_terms: list[str], *, case_sensitive: bool | None) -> list[Term]:
    """Compile each raw term into a :class:`Term`.

    ``case_sensitive`` forces the case handling for every term; ``None`` selects
    smart-case per term (case-sensitive iff the term contains an uppercase
    letter). Raises :class:`TermSyntaxError` on an invalid regex.
    """
    terms: list[Term] = []
    for raw in raw_terms:
        is_regex = _looks_like_regex(raw)
        source = raw[1:-1] if is_regex else re.escape(raw)
        for_case = raw[1:-1] if is_regex else raw
        cs = case_sensitive if case_sensitive is not None else any(c.isupper() for c in for_case)
        flags = 0 if cs else re.IGNORECASE
        try:
            pattern = re.compile(source, flags)
        except re.error as e:
            raise TermSyntaxError(f"invalid regex {raw!r}: {e}") from e
        terms.append(Term(raw=raw, is_regex=is_regex, case_sensitive=cs, pattern=pattern))
    return terms


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping/adjacent spans."""
    if not spans:
        return spans
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _match_file(
    path: Path, display: str, terms: tuple[Term, ...]
) -> FileMatches | TextSearchWarning | None:
    """Match one file.

    File-level AND, line-oriented: the file qualifies only if *every* term
    matches on at least one line; the returned lines are those matching *any*
    term. Returns ``None`` for a binary/undecodable file or one that doesn't
    satisfy the AND, and a :class:`TextSearchWarning` for an unreadable file.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # binary / non-UTF-8 → silent skip, like `grep -I`
    except OSError as e:
        return TextSearchWarning(f"cannot read {display}: {e}")

    seen_terms: set[int] = set()
    line_matches: list[LineMatch] = []
    for line_no, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.rstrip("\r")
        spans: list[tuple[int, int]] = []
        for i, term in enumerate(terms):
            matched_here = False
            for m in term.pattern.finditer(line):
                if m.end() > m.start():  # ignore zero-width matches
                    spans.append((m.start(), m.end()))
                    matched_here = True
            if matched_here:
                seen_terms.add(i)
        if spans:
            line_matches.append(LineMatch(line_no, line, _merge_spans(spans)))

    if len(seen_terms) != len(terms):
        return None  # AND not satisfied — some term never appeared
    return FileMatches(path=display, matches=line_matches) if line_matches else None


# ---------------------------------------------------------------------------
# File selection (single source of truth with the indexer, via file_walk)
# ---------------------------------------------------------------------------


def _iter_files(req: TextSearchRequest) -> Iterator[tuple[Path, str]]:
    """Yield ``(absolute_path, display_path)`` for every file to search, using the
    same include/exclude + ``.gitignore`` rules as the indexer and ``ccc grep``.

    Unlike ``grep``, a file is *not* skipped for lacking a matchable code
    language — text search covers every included file (source, docs, config).
    ``--lang`` optionally restricts by detected language.
    """
    walk = iter_project_files(req.root, req.path_glob)
    for abs_path, display in walk.files:
        if req.languages is not None:
            language = walk.ext_overrides.get(abs_path.suffix) or detect_code_language(
                filename=abs_path.name
            )
            if language is None or language.lower() not in req.languages:
                continue
        yield abs_path, display


class TextSearch:
    """A single text-search run. :meth:`run` matches each file as it's listed and
    streams results as they complete."""

    def __init__(self, req: TextSearchRequest) -> None:
        self._req = req

    def run(
        self,
        emit: Callable[[FileMatches | TextSearchWarning], object],
        *,
        limit: int | None = None,
    ) -> bool:
        """Match the request, calling ``emit`` with each file's matches (and any
        read warning) the moment it's ready — while the walk is still running.

        The walk runs on the calling thread and submits each file to a pool.
        Python regex matching holds the GIL, so the pool mainly overlaps file
        I/O rather than the matching itself; still, results stream as they
        finish. ``emit`` is called from worker threads, so a consumer doing I/O
        must serialize itself.

        With ``limit`` set, at most ``limit`` matching files are emitted and the
        walk stops as soon as that many are found (warnings are never capped).
        Returns ``True`` if the walk was cut short by ``limit`` — i.e. more files
        may match.
        """
        terms = self._req.terms
        matched = 0
        hit_limit = False
        lock = threading.Lock()
        stop = threading.Event()

        def _match(item: tuple[Path, str]) -> None:
            nonlocal matched, hit_limit
            if stop.is_set():
                return
            result = _match_file(item[0], item[1], terms)
            if result is None:  # binary / undecodable / AND not met
                return
            if isinstance(result, TextSearchWarning):
                emit(result)
                return
            with lock:
                if limit is not None and matched >= limit:
                    return
                matched += 1
                if limit is not None and matched >= limit:
                    hit_limit = True
                    stop.set()  # enough matches — stop feeding the walk
            emit(result)

        with ThreadPoolExecutor() as pool:
            for abs_path, display in _iter_files(self._req):
                if stop.is_set():
                    break
                pool.submit(_match, (abs_path, display))
            # ThreadPoolExecutor.__exit__ waits for every submitted match.

        return hit_limit


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _highlight_line(text: str, spans: list[tuple[int, int]], color: bool) -> str:
    """Render a line with its matched spans emphasized."""
    if not color or not spans:
        return text
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(paint(text[start:end], color, fg="red", bold=True))
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def render_file(fm: FileMatches, *, color: bool) -> str:
    """Render one file's matches: the path header, then ``line| text`` per
    matching line with matched spans emphasized — mirroring ``ccc grep``."""
    max_line = max((lm.line_no for lm in fm.matches), default=1)
    width = gutter_width(max_line)
    parts = [path_header(fm.path, color=color)]
    for lm in fm.matches:
        prefix = gutter(lm.line_no, width, color=color)
        parts.append(f"{prefix}{_highlight_line(lm.text, lm.spans, color)}")
    return "\n".join(parts)


def render_results(results: list[FileMatches], *, color: bool) -> str:
    """Render a list of per-file matches, files separated by a blank line. The
    CLI streams with :func:`render_file` instead; this is the batch form (tests)."""
    return "\n\n".join(render_file(fm, color=color) for fm in results)

"""`cgrep`: mgrep-like local search CLI backed by cocoindex-code."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from click.core import ParameterSource

from . import client as _client
from ._matchers import SKIP_DIRS
from ._version import __version__
from .cli import add_to_gitignore
from .client import print_warning
from .settings import (
    default_project_settings,
    default_user_settings,
    find_project_root,
    format_path_for_display,
    project_settings_path,
    save_project_settings,
    save_user_settings,
    target_sqlite_db_path,
    user_settings_path,
)
from .shared import is_sentence_transformers_installed


@dataclass(frozen=True)
class SearchScope:
    """Resolved project root and optional search filters."""

    project_root: Path
    path_globs: list[str] | None
    path_prefix: str | None


@dataclass(frozen=True)
class ResultRow:
    """Normalized search result for rendering."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str | None
    score: float


def _display_file_path(file_path: str) -> str:
    """Render relative paths consistently without duplicating `./`."""
    if file_path.startswith(("./", "/")):
        return file_path
    return f"./{file_path}"


_KNOWN_COMMANDS = {"search", "watch"}


def _normalize_argv(argv: list[str]) -> list[str]:
    """Rewrite bare positional invocations to `search ...`."""
    if not argv:
        return argv
    first = argv[0]
    if first in _KNOWN_COMMANDS:
        return argv
    if first in {"-h", "--help", "--version"}:
        return argv
    if first.startswith("-") and first not in {"-a", "-c", "-d", "-i", "-m", "-r", "-s", "-w"}:
        return argv
    return ["search", *argv]


def _bootstrap_project(start: Path) -> Path:
    """Create default settings on demand and return the project root."""
    project_root = find_project_root(start) or _find_git_root(start) or start
    settings_file = project_settings_path(project_root)
    if not settings_file.is_file():
        save_project_settings(project_root, default_project_settings())
        add_to_gitignore(project_root)

    user_file = user_settings_path()
    if not user_file.is_file():
        if not is_sentence_transformers_installed():
            raise click.ClickException(
                "Global settings are missing and local embeddings are unavailable.\n"
                "Install `cocoindex-code[embeddings-local]` or run "
                "`ccc init --litellm-model MODEL` first."
            )
        save_user_settings(default_user_settings())
    return project_root


def _find_git_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a Git worktree root."""
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _normalize_search_path(project_root: Path, raw_path: str | None, cwd: Path) -> SearchScope:
    """Resolve path filters for vector and keyword backends."""
    if raw_path is None:
        target = cwd.resolve()
    else:
        candidate = Path(raw_path)
        target = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    try:
        rel = target.relative_to(project_root)
    except ValueError as exc:
        raise click.ClickException(
            f"Search path escapes project root: {format_path_for_display(target)}"
        ) from exc

    if rel == Path("."):
        return SearchScope(project_root=project_root, path_globs=None, path_prefix=None)

    rel_posix = rel.as_posix()
    if target.is_dir():
        return SearchScope(
            project_root=project_root,
            path_globs=[f"{rel_posix}/*"],
            path_prefix=f"{rel_posix}/",
        )
    return SearchScope(project_root=project_root, path_globs=[rel_posix], path_prefix=rel_posix)


def _ensure_search_scope(raw_path: str | None, *, watch_root: bool = False) -> SearchScope:
    if watch_root:
        start = Path(raw_path).resolve() if raw_path else Path.cwd().resolve()
    else:
        start = Path.cwd()
    project_root = _bootstrap_project(start.resolve())
    cwd = start if watch_root else Path.cwd().resolve()
    return _normalize_search_path(project_root, None if watch_root else raw_path, cwd)


def _index_needs_refresh(project_root: Path) -> bool:
    """Return True when the local index is missing or effectively empty."""
    try:
        status = _client.project_status(str(project_root))
    except Exception:
        return True
    return (not status.index_exists) or status.total_chunks <= 0


def _maybe_refresh_index(project_root: Path, *, sync: bool) -> None:
    db_path = target_sqlite_db_path(project_root)
    if sync or not db_path.exists() or _index_needs_refresh(project_root):
        from .cli import _run_index_with_progress

        _run_index_with_progress(str(project_root))


def _warn_unsupported_flags(
    ctx: click.Context,
    *,
    answer: bool,
    web: bool,
    agentic: bool,
    dry_run: bool,
    rerank: bool,
) -> None:
    if answer:
        print_warning("`--answer` is not implemented in cgrep yet; showing raw matches.")
    if web:
        print_warning("`--web` is not supported; searching the local codebase only.")
    if agentic:
        print_warning("`--agentic` is not supported; running a single local search.")
    if dry_run:
        print_warning("`--dry-run` is ignored by cgrep.")
    if (
        not rerank
        and ctx.get_parameter_source("rerank") == ParameterSource.COMMANDLINE
    ):
        print_warning("`--no-rerank` is ignored; cgrep uses fixed local ranking.")


def _keyword_rows_for_languages(
    db_path: Path,
    query: str,
    *,
    limit: int,
    path_prefix: str | None,
    languages: list[str] | None,
) -> list[Any]:
    from .hybrid_search import keyword_search

    fetch_limit = max(limit * 3, limit)
    if not languages:
        return keyword_search(
            db_path,
            query,
            limit=fetch_limit,
            path_prefix=path_prefix,
        )[:limit]

    deduped: dict[tuple[str, int, int], Any] = {}
    for language in languages:
        for row in keyword_search(
            db_path,
            query,
            limit=fetch_limit,
            path_prefix=path_prefix,
            language=language,
        ):
            key = (row.file_path, row.start_line, row.end_line)
            existing = deduped.get(key)
            if existing is None or row.score > existing.score:
                deduped[key] = row
    return sorted(deduped.values(), key=lambda row: row.score, reverse=True)[:limit]


def _vector_rows(
    project_root: Path,
    query: str,
    *,
    limit: int,
    offset: int,
    languages: list[str] | None,
    path_globs: list[str] | None,
) -> list[ResultRow]:
    resp = _client.search(
        project_root=str(project_root),
        query=query,
        languages=languages,
        paths=path_globs,
        limit=limit,
        offset=offset,
    )
    if not resp.success:
        raise click.ClickException(resp.message or "Search failed.")
    return [
        ResultRow(
            file_path=row.file_path,
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            language=row.language,
            score=row.score,
        )
        for row in resp.results
    ]


def _hybrid_rows(
    project_root: Path,
    query: str,
    *,
    limit: int,
    offset: int,
    languages: list[str] | None,
    path_globs: list[str] | None,
    path_prefix: str | None,
) -> list[ResultRow]:
    from .hybrid_search import ensure_fts_index, reciprocal_rank_fusion

    db_path = target_sqlite_db_path(project_root)
    ensure_fts_index(db_path)
    vector_rows = _vector_rows(
        project_root,
        query,
        limit=limit + offset,
        offset=0,
        languages=languages,
        path_globs=path_globs,
    )
    keyword_rows = _keyword_rows_for_languages(
        db_path,
        query,
        limit=limit + offset,
        path_prefix=path_prefix,
        languages=languages,
    )
    fused = reciprocal_rank_fusion(
        vector_results=[
            {
                "file_path": row.file_path,
                "language": row.language,
                "content": row.content,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "score": row.score,
            }
            for row in vector_rows
        ],
        keyword_results=keyword_rows,
        limit=limit + offset,
    )
    return [
        ResultRow(
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            content=row["content"],
            language=row.get("language"),
            score=float(row["hybrid_score"]),
        )
        for row in fused[offset : offset + limit]
    ]


def _keyword_only_rows(
    project_root: Path,
    query: str,
    *,
    limit: int,
    offset: int,
    languages: list[str] | None,
    path_prefix: str | None,
) -> list[ResultRow]:
    from .hybrid_search import ensure_fts_index

    db_path = target_sqlite_db_path(project_root)
    ensure_fts_index(db_path)
    rows = _keyword_rows_for_languages(
        db_path,
        query,
        limit=limit + offset,
        path_prefix=path_prefix,
        languages=languages,
    )
    return [
        ResultRow(
            file_path=row.file_path,
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            language=row.language,
            score=row.score,
        )
        for row in rows[offset : offset + limit]
    ]


def _grep_rows(
    project_root: Path,
    query: str,
    *,
    limit: int,
    path_prefix: str | None,
) -> list[ResultRow]:
    from .mcp_handlers import ripgrep_bounded_tool

    payload = ripgrep_bounded_tool(
        str(project_root),
        query,
        path_prefixes=[path_prefix] if path_prefix else None,
        max_matches=limit,
    )
    if not payload.get("success", False):
        raise click.ClickException(str(payload.get("error") or "ripgrep search failed"))
    matches = payload.get("matches", [])
    if not isinstance(matches, list):
        return []
    return [
        ResultRow(
            file_path=str(match["file_path"]),
            start_line=int(match["start_line"]),
            end_line=int(match["end_line"]),
            content=str(match["content"]),
            language=None,
            score=float(match.get("score", 1.0)),
        )
        for match in matches
    ]


def _run_search(
    scope: SearchScope,
    query: str,
    *,
    mode: str,
    limit: int,
    offset: int,
    languages: list[str] | None,
) -> list[ResultRow]:
    mode_value = mode.lower()
    if mode_value == "grep":
        return _grep_rows(scope.project_root, query, limit=limit, path_prefix=scope.path_prefix)

    if _index_needs_refresh(scope.project_root):
        print_warning("Index is missing or empty; falling back to grep for this search.")
        return _grep_rows(scope.project_root, query, limit=limit, path_prefix=scope.path_prefix)

    if mode_value == "vector":
        try:
            return _vector_rows(
                scope.project_root,
                query,
                limit=limit,
                offset=offset,
                languages=languages,
                path_globs=scope.path_globs,
            )
        except TimeoutError:
            print_warning("Vector search timed out; falling back to keyword search.")
            return _keyword_only_rows(
                scope.project_root,
                query,
                limit=limit,
                offset=offset,
                languages=languages,
                path_prefix=scope.path_prefix,
            )
    if mode_value == "keyword":
        return _keyword_only_rows(
            scope.project_root,
            query,
            limit=limit,
            offset=offset,
            languages=languages,
            path_prefix=scope.path_prefix,
        )
    if mode_value == "hybrid":
        try:
            return _hybrid_rows(
                scope.project_root,
                query,
                limit=limit,
                offset=offset,
                languages=languages,
                path_globs=scope.path_globs,
                path_prefix=scope.path_prefix,
            )
        except TimeoutError:
            print_warning("Hybrid vector phase timed out; falling back to keyword search.")
            return _keyword_only_rows(
                scope.project_root,
                query,
                limit=limit,
                offset=offset,
                languages=languages,
                path_prefix=scope.path_prefix,
            )
    raise click.ClickException(f"Invalid mode {mode!r}; expected hybrid, vector, keyword, or grep.")


def _render_results(rows: list[ResultRow], *, show_content: bool) -> None:
    if not rows:
        click.echo("No results found.")
        return
    for row in rows:
        language = f" [{row.language}]" if row.language else ""
        click.echo(
            f"{_display_file_path(row.file_path)}:{row.start_line}-{row.end_line}{language} "
            f"(score {row.score:.3f})"
        )
        if show_content:
            click.echo(row.content)


def _watch_filter(change: Any, path: str) -> bool:
    del change
    parts = Path(path).parts
    return not any(part in SKIP_DIRS for part in parts)


@click.group(help="cgrep: local semantic grep powered by CocoIndex Code.")
@click.version_option(version=__version__, prog_name="cgrep")
def cli() -> None:
    """Entry point for cgrep."""


@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("query", required=True)
@click.argument("path", required=False)
@click.option("-m", "--max-count", "limit", default=10, type=int, show_default=True)
@click.option("-c", "--content", is_flag=True, help="Show matching chunk content.")
@click.option("-a", "--answer", is_flag=True, help="Accepted for compatibility; ignored.")
@click.option("-s", "--sync", is_flag=True, help="Refresh the local index before searching.")
@click.option("-d", "--dry-run", is_flag=True, help="Accepted for compatibility; ignored.")
@click.option(
    "--no-rerank",
    "rerank",
    flag_value=False,
    default=True,
    help="Accepted for compatibility; ignored.",
)
@click.option("-w", "--web", is_flag=True, help="Accepted for compatibility; ignored.")
@click.option("--agentic", is_flag=True, help="Accepted for compatibility; ignored.")
@click.option(
    "--mode",
    type=click.Choice(["hybrid", "vector", "keyword", "grep"]),
    default="hybrid",
    show_default=True,
)
@click.option(
    "--lang",
    "languages",
    multiple=True,
    help="Restrict search to one or more languages.",
)
@click.option("--offset", default=0, type=int, show_default=True)
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    path: str | None,
    limit: int,
    content: bool,
    answer: bool,
    sync: bool,
    dry_run: bool,
    rerank: bool,
    web: bool,
    agentic: bool,
    mode: str,
    languages: tuple[str, ...],
    offset: int,
) -> None:
    """Search the current codebase."""
    _warn_unsupported_flags(
        ctx,
        answer=answer,
        web=web,
        agentic=agentic,
        dry_run=dry_run,
        rerank=rerank,
    )
    scope = _ensure_search_scope(path)
    if sync:
        _maybe_refresh_index(scope.project_root, sync=sync)
    rows = _run_search(
        scope,
        query,
        mode=mode,
        limit=limit,
        offset=offset,
        languages=list(languages) or None,
    )
    _render_results(rows, show_content=content)


@cli.command()
@click.argument("path", required=False)
@click.option(
    "--interval",
    default=1.0,
    type=float,
    show_default=True,
    help="Polling interval in seconds.",
)
@click.option("-d", "--dry-run", is_flag=True, help="Print the project root and exit.")
def watch(path: str | None, interval: float, dry_run: bool) -> None:
    """Index the current project and keep it in sync."""
    try:
        from watchfiles import watch as watch_files
    except ModuleNotFoundError as exc:
        raise click.ClickException(
            "`watchfiles` is not installed in this environment; `cgrep watch` is unavailable."
        ) from exc

    scope = _ensure_search_scope(path, watch_root=True)
    project_root = scope.project_root
    if dry_run:
        click.echo(f"Would watch {format_path_for_display(project_root)}")
        return

    _maybe_refresh_index(project_root, sync=True)
    click.echo(f"Watching {format_path_for_display(project_root)}. Press Ctrl-C to stop.")
    last_index_at = 0.0
    try:
        for _changes in watch_files(
            project_root, watch_filter=_watch_filter, yield_on_timeout=False
        ):
            now = time.monotonic()
            if now - last_index_at < interval:
                continue
            try:
                _maybe_refresh_index(project_root, sync=True)
            except Exception as exc:
                print_warning(f"`cgrep watch` reindex failed: {exc}")
                continue
            last_index_at = now
    except KeyboardInterrupt:
        click.echo("Stopped watcher.")


def main(argv: list[str] | None = None) -> None:
    """Script entry point with default-command rewriting."""
    args = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    cli.main(args=args, prog_name="cgrep", standalone_mode=True)


if __name__ == "__main__":
    main()

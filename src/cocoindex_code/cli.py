"""CLI entry point for cocoindex-code (ccc command)."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer as _typer

from .client import DaemonStartError
from .protocol import DoctorCheckResult, IndexingProgress, ProjectStatusResponse, SearchResponse
from .settings import (
    default_project_settings,
    default_user_settings,
    find_parent_with_marker,
    find_project_root,
    resolve_db_dir,
    save_project_settings,
    save_user_settings,
    user_settings_path,
)

app = _typer.Typer(
    name="ccc",
    help="CocoIndex Code — index and search codebases.",
    no_args_is_help=True,
)

daemon_app = _typer.Typer(name="daemon", help="Manage the daemon process.")
app.add_typer(daemon_app, name="daemon")


# ---------------------------------------------------------------------------
# Shared CLI helpers
# ---------------------------------------------------------------------------


def require_project_root() -> Path:
    """Find the project root by walking up from CWD.

    Checks global settings first (more fundamental), then project settings.
    Exits with code 1 if either check fails.
    """
    gs_path = user_settings_path()
    if not gs_path.is_file():
        _typer.echo(
            f"Error: Global settings not found: {gs_path}\n"
            "Run `ccc init` to create it with default settings.",
            err=True,
        )
        raise _typer.Exit(code=1)
    root = find_project_root(Path.cwd())
    if root is None:
        _typer.echo(
            "Error: Not in an initialized project directory.\n"
            "Run `ccc init` in your project root to get started.",
            err=True,
        )
        raise _typer.Exit(code=1)
    return root


_F = TypeVar("_F", bound=Callable[..., object])


def _catch_daemon_start_error(func: _F) -> _F:
    """Decorator that catches ``DaemonStartError`` and exits with a clean message.

    Apply to any CLI command that may trigger daemon auto-start.
    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return func(*args, **kwargs)
        except DaemonStartError as e:
            _typer.echo(f"Error: {e}", err=True)
            raise _typer.Exit(code=1)

    return wrapper  # type: ignore[return-value]


def resolve_default_path(project_root: Path) -> str | None:
    """Compute default ``--path`` filter from CWD relative to project root."""
    cwd = Path.cwd().resolve()
    try:
        rel = cwd.relative_to(project_root)
    except ValueError:
        return None
    if rel == Path("."):
        return None
    return f"{rel.as_posix()}/*"


def _format_progress(progress: IndexingProgress) -> str:
    """Format an IndexingProgress snapshot as a human-readable string."""
    return (
        f"{progress.num_execution_starts} files listed"
        f" | {progress.num_adds} added, {progress.num_deletes} deleted,"
        f" {progress.num_reprocesses} reprocessed,"
        f" {progress.num_unchanged} unchanged,"
        f" error: {progress.num_errors}"
    )


def print_project_header(project_root: str) -> None:
    """Print the project root directory."""
    _typer.echo(f"Project: {project_root}")


def print_index_stats(status: ProjectStatusResponse) -> None:
    """Print formatted index statistics."""
    if status.progress is not None:
        _typer.echo(f"Indexing in progress: {_format_progress(status.progress)}")
    if not status.index_exists:
        _typer.echo("\nIndex not created yet.")
        return
    _typer.echo("\nIndex stats:")
    _typer.echo(f"  Chunks: {status.total_chunks}")
    _typer.echo(f"  Files:  {status.total_files}")
    if status.languages:
        _typer.echo("  Languages:")
        for lang, count in sorted(status.languages.items(), key=lambda x: -x[1]):
            _typer.echo(f"    {lang}: {count} chunks")


def print_search_results(response: SearchResponse) -> None:
    """Print formatted search results."""
    if not response.success:
        _typer.echo(f"Search failed: {response.message}", err=True)
        return

    if not response.results:
        _typer.echo("No results found.")
        return

    for i, r in enumerate(response.results, 1):
        _typer.echo(f"\n--- Result {i} (score: {r.score:.3f}) ---")
        _typer.echo(f"File: {r.file_path}:{r.start_line}-{r.end_line} [{r.language}]")
        _typer.echo(r.content)


def _run_index_with_progress(project_root: str) -> None:
    """Run indexing with streaming progress display. Exits on failure."""
    from rich.console import Console as _Console
    from rich.live import Live as _Live
    from rich.spinner import Spinner as _Spinner

    from . import client as _client

    err_console = _Console(stderr=True)
    last_progress_line: str | None = None

    with _Live(_Spinner("dots", "Indexing..."), console=err_console, transient=True) as live:

        def _on_waiting() -> None:
            live.update(
                _Spinner(
                    "dots",
                    "Another indexing is ongoing, waiting for it to finish...",
                )
            )

        def _on_progress(progress: IndexingProgress) -> None:
            nonlocal last_progress_line
            last_progress_line = f"Indexing: {_format_progress(progress)}"
            live.update(_Spinner("dots", last_progress_line))

        try:
            resp = _client.index(project_root, on_progress=_on_progress, on_waiting=_on_waiting)
        except RuntimeError as e:
            live.stop()
            # Let DaemonStartError propagate to the decorator for consistent handling.
            if isinstance(e, DaemonStartError):
                raise
            _typer.echo(f"Indexing failed: {e}", err=True)
            raise _typer.Exit(code=1)

    # Print the final progress line so it remains visible after the spinner clears
    if last_progress_line is not None:
        _typer.echo(last_progress_line, err=True)

    if not resp.success:
        _typer.echo(f"Indexing failed: {resp.message}", err=True)
        raise _typer.Exit(code=1)


def _search_with_wait_spinner(
    project_root: str,
    query: str,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> SearchResponse:
    """Run search, showing a spinner if waiting for load-time indexing."""
    from rich.console import Console as _Console
    from rich.live import Live as _Live
    from rich.spinner import Spinner as _Spinner

    from . import client as _client

    err_console = _Console(stderr=True)

    with _Live(_Spinner("dots", "Searching..."), console=err_console, transient=True) as live:

        def _on_waiting() -> None:
            live.update(
                _Spinner("dots", "Waiting for indexing to complete..."),
                refresh=True,
            )

        resp = _client.search(
            project_root=project_root,
            query=query,
            languages=languages,
            paths=paths,
            limit=limit,
            offset=offset,
            on_waiting=_on_waiting,
        )

    return resp


_GITIGNORE_COMMENT = "# CocoIndex Code (ccc)"
_GITIGNORE_ENTRY = "/.cocoindex_code/"


def add_to_gitignore(project_root: Path) -> None:
    """Add ``/.cocoindex_code/`` to ``.gitignore`` if ``.git`` exists.

    Creates ``.gitignore`` if it doesn't exist.  Skips if the entry is already
    present.
    """
    if not (project_root / ".git").is_dir():
        return

    gitignore = project_root / ".gitignore"
    if gitignore.is_file():
        content = gitignore.read_text()
        if _GITIGNORE_ENTRY in content.splitlines():
            return  # already present
        # Ensure a trailing newline before appending
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"{_GITIGNORE_COMMENT}\n{_GITIGNORE_ENTRY}\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(f"{_GITIGNORE_COMMENT}\n{_GITIGNORE_ENTRY}\n")


def remove_from_gitignore(project_root: Path) -> None:
    """Remove ``/.cocoindex_code/`` entry and its comment from ``.gitignore``."""
    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return

    lines = gitignore.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip("\n\r")
        if stripped == _GITIGNORE_ENTRY:
            # Skip this line; also remove preceding comment if it matches
            if new_lines and new_lines[-1].rstrip("\n\r") == _GITIGNORE_COMMENT:
                new_lines.pop()
            i += 1
            continue
        new_lines.append(lines[i])
        i += 1
    gitignore.write_text("".join(new_lines))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    force: bool = _typer.Option(False, "-f", "--force", help="Skip parent directory warning"),
) -> None:
    """Initialize a project for cocoindex-code."""
    from .settings import project_settings_path as _project_settings_path

    cwd = Path.cwd().resolve()
    settings_file = _project_settings_path(cwd)

    # Always ensure user settings exist
    user_path = user_settings_path()
    if not user_path.is_file():
        save_user_settings(default_user_settings())
        _typer.echo(f"Created user settings: {user_path}")

    # Check if already initialized
    if settings_file.is_file():
        _typer.echo("Project already initialized.")
        return

    # Check parent directories for markers
    if not force:
        parent = find_parent_with_marker(cwd)
        if parent is not None and parent != cwd:
            _typer.echo(
                f"Warning: A parent directory has a project marker: {parent}\n"
                "You might want to run `ccc init` there instead.\n"
                "Use `ccc init -f` to initialize here anyway."
            )
            raise _typer.Exit(code=1)

    # Create project settings
    save_project_settings(cwd, default_project_settings())
    _typer.echo(f"Created project settings: {settings_file}")

    # Add to .gitignore
    add_to_gitignore(cwd)

    _typer.echo("You can edit the settings files to customize indexing behavior.")
    _typer.echo("Run `ccc index` to build the index.")


@app.command()
@_catch_daemon_start_error
def index() -> None:
    """Create/update index for the codebase."""
    from . import client as _client

    project_root = str(require_project_root())
    print_project_header(project_root)
    _run_index_with_progress(project_root)
    print_index_stats(_client.project_status(project_root))


@app.command()
@_catch_daemon_start_error
def search(
    query: list[str] = _typer.Argument(..., help="Search query"),
    lang: list[str] = _typer.Option([], "--lang", help="Filter by language"),
    path: str | None = _typer.Option(None, "--path", help="Filter by file path glob"),
    offset: int = _typer.Option(0, "--offset", help="Number of results to skip"),
    limit: int = _typer.Option(10, "--limit", help="Maximum results to return"),
    refresh: bool = _typer.Option(False, "--refresh", help="Refresh index before searching"),
) -> None:
    """Semantic search across the codebase."""
    project_root = str(require_project_root())
    query_str = " ".join(query)

    if refresh:
        _run_index_with_progress(project_root)

    # Default path filter from CWD
    paths: list[str] | None = None
    if path is not None:
        paths = [path]
    else:
        default = resolve_default_path(Path(project_root))
        if default is not None:
            paths = [default]

    resp = _search_with_wait_spinner(
        project_root=project_root,
        query=query_str,
        languages=lang or None,
        paths=paths,
        limit=limit,
        offset=offset,
    )
    print_search_results(resp)


@app.command()
@_catch_daemon_start_error
def status() -> None:
    """Show project status."""
    from . import client as _client

    project_root = str(require_project_root())
    print_project_header(project_root)
    print_index_stats(_client.project_status(project_root))


@app.command()
def reset(
    all_: bool = _typer.Option(False, "--all", help="Also remove settings and .gitignore entry"),
    force: bool = _typer.Option(False, "-f", "--force", help="Skip confirmation"),
) -> None:
    """Reset project databases and optionally remove settings."""
    project_root = require_project_root()
    cocoindex_dir = project_root / ".cocoindex_code"
    db_dir = resolve_db_dir(project_root)

    db_files = [
        db_dir / "cocoindex.db",
        db_dir / "target_sqlite.db",
    ]
    settings_file = cocoindex_dir / "settings.yml"

    # Determine what will be deleted
    to_delete = [f for f in db_files if f.exists()]
    if all_:
        if settings_file.exists():
            to_delete.append(settings_file)

    if not to_delete and not all_:
        _typer.echo("Nothing to reset.")
        return

    # Show what will be deleted
    if to_delete:
        _typer.echo("The following files will be deleted:")
        for f in to_delete:
            _typer.echo(f"  {f}")

    # Confirm
    if not force:
        if not _typer.confirm("Proceed?"):
            _typer.echo("Aborted.")
            raise _typer.Exit(code=0)

    # Remove project from daemon first so it releases file handles
    try:
        from . import client as _client

        _client.remove_project(str(project_root))
    except (ConnectionRefusedError, OSError, RuntimeError):
        pass  # Daemon not running — that's fine

    # Delete files/directories
    import shutil as _shutil

    for f in to_delete:
        if f.is_dir():
            _shutil.rmtree(f)
        else:
            f.unlink(missing_ok=True)

    if all_:
        # Remove db_dir if empty and different from cocoindex_dir
        if db_dir != cocoindex_dir:
            try:
                db_dir.rmdir()
            except OSError:
                pass  # Not empty or doesn't exist
        # Remove .cocoindex_code/ if empty
        try:
            cocoindex_dir.rmdir()
        except OSError:
            pass  # Not empty

        # Remove from .gitignore
        remove_from_gitignore(project_root)
        _typer.echo("Project fully reset.")
    else:
        _typer.echo("Databases deleted.")
        if settings_file.exists():
            _typer.echo(
                "Settings file still exists. Run `ccc reset --all` to remove it too,\n"
                "or edit it manually."
            )


def _print_section(name: str) -> None:
    import click as _click

    _typer.echo()
    _typer.echo(_click.style(f"  {name}", bold=True))
    _typer.echo(_click.style(f"  {'─' * 38}", fg="bright_black"))


def _print_error(msg: str) -> None:
    import click as _click

    _typer.echo(_click.style(f"  ERROR: {msg}", fg="red"), err=True)


def _print_doctor_result(result: DoctorCheckResult) -> None:
    import click as _click

    if result.name == "done":
        return
    if result.ok:
        tag = _click.style("[OK]", fg="green", bold=True)
    else:
        tag = _click.style("[FAIL]", fg="red", bold=True)
    _typer.echo(f"\n  {tag} {result.name}")
    for line in result.details:
        _typer.echo(f"    {line}")
    for err in result.errors:
        _typer.echo(_click.style(f"    ERROR: {err}", fg="red"), err=True)


@app.command()
@_catch_daemon_start_error
def doctor() -> None:
    """Check system health and report issues."""
    from . import client as _client
    from .settings import (
        load_project_settings as _load_project_settings,
    )
    from .settings import (
        load_user_settings as _load_user_settings,
    )
    from .settings import (
        project_settings_path as _project_settings_path,
    )
    from .settings import (
        user_settings_path as _user_settings_path,
    )

    # --- 1. Global settings (local, no daemon needed) ---
    _print_section("Global Settings")
    settings_path = _user_settings_path()
    _typer.echo(f"  Settings: {settings_path}")
    try:
        user_settings = _load_user_settings()
        emb = user_settings.embedding
        device_str = f", device={emb.device}" if emb.device else ""
        _typer.echo(f"  Embedding: provider={emb.provider}, model={emb.model}{device_str}")
        if user_settings.envs:
            _typer.echo(
                f"  Env vars (from settings): {', '.join(sorted(user_settings.envs.keys()))}"
            )
    except (FileNotFoundError, ValueError) as e:
        _print_error(str(e))

    # --- 2. Connect to daemon (handshake with auto-start/restart) ---
    _print_section("Daemon")
    daemon_ok = False
    try:
        status = _client.daemon_status()
        _typer.echo(f"  Version: {status.version}")
        _typer.echo(f"  Uptime: {status.uptime_seconds:.1f}s")
        _typer.echo(f"  Loaded projects: {len(status.projects)}")
        daemon_ok = True
    except Exception as e:
        _print_error(f"Cannot connect to daemon: {e}")
        _typer.echo("  Remaining daemon-side checks will be skipped.")

    # --- 3. Daemon environment (requires daemon) ---
    if daemon_ok:
        try:
            env_resp = _client.daemon_env()
            settings_keys = set(env_resp.settings_env_names)
            other_keys = [k for k in env_resp.env_names if k not in settings_keys]
            if other_keys:
                _typer.echo(f"  Other env vars in daemon: {', '.join(sorted(other_keys))}")
            if env_resp.db_path_mappings:
                _typer.echo("  DB path mappings:")
                for m in env_resp.db_path_mappings:
                    _typer.echo(f"    {m.source} \u2192 {m.target}")
        except Exception as e:
            _print_error(f"Failed to get daemon env: {e}")

    # --- 4. Model check (daemon-side, global — before project checks) ---
    if daemon_ok:
        try:
            _client.doctor(
                project_root=None,
                on_result=_print_doctor_result,
            )
        except Exception as e:
            _print_error(f"Model check failed: {e}")

    # --- 5. Detect project ---
    project_root = find_project_root(Path.cwd())

    # --- 6. Project settings (local, no daemon needed) ---
    if project_root is not None:
        _print_section("Project Settings")
        ps_path = _project_settings_path(project_root)
        _typer.echo(f"  Settings: {ps_path}")
        try:
            ps = _load_project_settings(project_root)
            _typer.echo(f"  Include patterns ({len(ps.include_patterns)}):")
            _typer.echo(f"    {', '.join(ps.include_patterns)}")
            _typer.echo(f"  Exclude patterns ({len(ps.exclude_patterns)}):")
            _typer.echo(f"    {', '.join(ps.exclude_patterns)}")
            if ps.language_overrides:
                _typer.echo("  Language overrides:")
                for lo in ps.language_overrides:
                    _typer.echo(f"    .{lo.ext} -> {lo.lang}")
        except (FileNotFoundError, ValueError) as e:
            _print_error(str(e))

    # --- 7. Project daemon-side checks (file walk + index status) ---
    if daemon_ok and project_root is not None:
        try:
            _client.doctor(
                project_root=str(project_root),
                on_result=_print_doctor_result,
            )
        except Exception as e:
            _print_error(f"Project checks failed: {e}")

    # --- 8. Log files ---
    _print_section("Log Files")
    from .daemon import daemon_dir as _daemon_dir

    log_dir = _daemon_dir()
    _typer.echo(f"  Daemon logs: {log_dir / 'daemon.log'}")
    _typer.echo("  Check logs above for further troubleshooting.")


@app.command()
@_catch_daemon_start_error
def mcp() -> None:
    """Run as MCP server (stdio mode)."""
    import asyncio

    project_root = str(require_project_root())

    async def _run_mcp() -> None:
        from .server import create_mcp_server

        mcp_server = create_mcp_server(project_root)
        asyncio.create_task(_bg_index(project_root))
        await mcp_server.run_stdio_async()

    asyncio.run(_run_mcp())


async def _bg_index(project_root: str) -> None:
    """Index in background. Each call opens its own daemon connection."""
    import asyncio

    from . import client as _client

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: _client.index(project_root))
    except Exception:
        pass


# --- Daemon subcommands ---


@daemon_app.command("status")
@_catch_daemon_start_error
def daemon_status() -> None:
    """Show daemon status."""
    from . import client as _client

    resp = _client.daemon_status()
    _typer.echo(f"Daemon version: {resp.version}")
    _typer.echo(f"Uptime: {resp.uptime_seconds:.1f}s")
    if resp.projects:
        _typer.echo("Projects:")
        for p in resp.projects:
            state = "indexing" if p.indexing else "idle"
            _typer.echo(f"  {p.project_root} [{state}]")
    else:
        _typer.echo("No projects loaded.")


@daemon_app.command("restart")
@_catch_daemon_start_error
def daemon_restart() -> None:
    """Restart the daemon."""
    from .client import _wait_for_daemon, start_daemon, stop_daemon

    _typer.echo("Stopping daemon...")
    stop_daemon()

    _typer.echo("Starting daemon...")
    proc = start_daemon()
    _wait_for_daemon(proc=proc)
    _typer.echo("Daemon restarted.")


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the daemon."""
    from .client import is_daemon_running, stop_daemon
    from .daemon import daemon_pid_path

    pid_path = daemon_pid_path()
    if not pid_path.exists() and not is_daemon_running():
        _typer.echo("Daemon is not running.")
        return

    stop_daemon()

    # Wait for process to exit (check both pid file and socket)
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not pid_path.exists() and not is_daemon_running():
            break
        time.sleep(0.1)

    if pid_path.exists() or is_daemon_running():
        _typer.echo("Warning: daemon may not have stopped cleanly.", err=True)
    else:
        _typer.echo("Daemon stopped.")


@app.command("run-daemon", hidden=True)
def run_daemon_cmd() -> None:
    """Internal: run the daemon process."""
    from .daemon import run_daemon

    run_daemon()


# Allow running as module: python -m cocoindex_code.cli
if __name__ == "__main__":
    app()

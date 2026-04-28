"""CLI entry point for cocoindex-code (ccc command)."""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer as _typer

from .client import DaemonStartError
from .protocol import DoctorCheckResult, IndexingProgress, ProjectStatusResponse, SearchResponse
from .settings import (
    DEFAULT_ST_MODEL,
    EmbeddingSettings,
    cocoindex_db_path,
    default_project_settings,
    find_parent_with_marker,
    find_project_root,
    format_path_for_display,
    normalize_input_path,
    project_settings_path,
    resolve_db_dir,
    save_initial_user_settings,
    save_project_settings,
    target_sqlite_db_path,
    user_settings_path,
)

app = _typer.Typer(
    name="ccc",
    help="CocoIndex Code — index and search codebases.",
    no_args_is_help=True,
)

daemon_app = _typer.Typer(name="daemon", help="Manage the daemon process.")
app.add_typer(daemon_app, name="daemon")
config_app = _typer.Typer(name="config", help="Validate and inspect coco-config.yml files.")
repos_app = _typer.Typer(name="repos", help="Sync and inspect configured repositories.")
workspace_app = _typer.Typer(name="workspace", help="Index and watch multi-repository workspaces.")
codebase_app = _typer.Typer(name="codebase", help="Codebase indexing and intelligence tools.")
codebase_graph_app = _typer.Typer(name="graph", help="Inspect dependency and symbol graph data.")
codebase_context_app = _typer.Typer(name="context", help="Inspect configured context artifacts.")
app.add_typer(config_app, name="config")
app.add_typer(repos_app, name="repos")
app.add_typer(workspace_app, name="workspace")
app.add_typer(codebase_app, name="codebase")
codebase_app.add_typer(codebase_graph_app, name="graph")
codebase_app.add_typer(codebase_context_app, name="context")


@app.callback()
def _apply_host_cwd() -> None:
    """Honor ``COCOINDEX_CODE_HOST_CWD`` when forwarded from a ``docker exec`` wrapper.

    The env var carries the host shell's pwd verbatim. We normalize it through
    the host path mapping to container form and ``chdir`` there so
    cwd-driven discovery (``find_project_root`` etc.) sees the user's real
    project subtree. Unset → no-op.
    """
    host_cwd = os.environ.get("COCOINDEX_CODE_HOST_CWD")
    if not host_cwd:
        return
    target = normalize_input_path(host_cwd)
    try:
        os.chdir(target)
    except OSError as e:
        _typer.echo(
            f"Warning: COCOINDEX_CODE_HOST_CWD={host_cwd!r} → {target!r} "
            f"is not accessible: {e}. Continuing with cwd={os.getcwd()!r}.",
            err=True,
        )


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
            f"Error: Global settings not found: {format_path_for_display(gs_path)}\n"
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
    _typer.echo(f"Project: {format_path_for_display(project_root)}")


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


_LITELLM_MODELS_URL = "https://docs.litellm.ai/docs/embedding/supported_embedding"


def _resolve_embedding_choice(
    litellm_model_flag: str | None,
    st_installed: bool,
    tty: bool,
) -> EmbeddingSettings:
    """Resolve the embedding settings per the init control-flow diagram."""
    if litellm_model_flag is not None:
        return EmbeddingSettings(provider="litellm", model=litellm_model_flag)

    if not tty:
        if st_installed:
            return EmbeddingSettings(provider="sentence-transformers", model=DEFAULT_ST_MODEL)
        _typer.echo(
            "Error: sentence-transformers is not installed and stdin is not a TTY.\n"
            "Either install the extra (`pip install 'cocoindex-code[embeddings-local]'`)\n"
            "or pass `--litellm-model MODEL` to select a LiteLLM model.",
            err=True,
        )
        raise _typer.Exit(code=1)

    # Interactive
    import questionary

    if st_installed:
        provider = questionary.select(
            "Embedding provider",
            choices=[
                questionary.Choice(
                    title="sentence-transformers (local, free)",
                    value="sentence-transformers",
                ),
                questionary.Choice(
                    title="litellm (cloud, 100+ providers)",
                    value="litellm",
                ),
            ],
        ).ask()
    else:
        _typer.echo(
            "sentence-transformers is not installed — only `litellm` is available.\n"
            "To enable local embeddings, install `cocoindex-code[embeddings-local]`."
        )
        provider = "litellm"

    if provider is None:  # user cancelled (Ctrl-C / Esc)
        raise _typer.Exit(code=1)

    if provider == "sentence-transformers":
        model = questionary.text("Model name", default=DEFAULT_ST_MODEL).ask()
    elif provider == "litellm":
        _typer.echo(f"See supported LiteLLM embedding models: {_LITELLM_MODELS_URL}")
        model = questionary.text("Model name").ask()
    else:
        _typer.echo(f"Error: unknown provider {provider!r}", err=True)
        raise _typer.Exit(code=1)

    if not model:  # None (cancelled) or empty string
        raise _typer.Exit(code=1)

    return EmbeddingSettings(provider=provider, model=model.strip())


def _ok_fail_tag(ok: bool) -> str:
    """Return a colored `[OK]` or `[FAIL]` tag string."""
    import click as _click

    if ok:
        return _click.style("[OK]", fg="green", bold=True)
    return _click.style("[FAIL]", fg="red", bold=True)


def _run_init_model_check(settings_path: Path) -> None:
    """Ask the daemon to test the embedding model; print results and a hint on failure.

    Drives the check via `DoctorRequest(project_root=None)`. The daemon loads
    the model once and stays running, so the user's next `ccc index` starts
    warm. Both DaemonStartError and generic exceptions are rendered as a
    synthetic failed DoctorCheckResult — uniform failure-output shape.
    """
    from rich.console import Console as _Console
    from rich.live import Live as _Live
    from rich.spinner import Spinner as _Spinner

    from . import client as _client

    err_console = _Console(stderr=True)
    results: list[DoctorCheckResult] = []
    try:
        with _Live(
            _Spinner("dots", "Testing embedding model..."),
            console=err_console,
            transient=True,
        ):
            results = _client.doctor(project_root=None)
    except Exception as e:
        results = [
            DoctorCheckResult(
                name="Model Check",
                ok=False,
                details=[],
                errors=[f"{type(e).__name__}: {e}"],
            )
        ]

    failed = False
    for r in results:
        if r.name == "done":
            continue
        _print_doctor_result(r)
        if not r.ok:
            failed = True

    if failed:
        display_path = format_path_for_display(settings_path)
        _typer.echo(
            f"You can edit {display_path} to change the model or add API keys\n"
            "under `envs:`. Then run `ccc doctor` to verify.",
            err=True,
        )


def _setup_user_settings_interactive(litellm_model_flag: str | None) -> None:
    """Interactive global-settings setup — only runs when settings are missing."""
    from .embedder_defaults import lookup_defaults
    from .shared import is_sentence_transformers_installed

    embedding = _resolve_embedding_choice(
        litellm_model_flag=litellm_model_flag,
        st_installed=is_sentence_transformers_installed(),
        tty=sys.stdin.isatty(),
    )

    # Apply curated defaults if the model is in our table.
    indexing_defaults, query_defaults = lookup_defaults(embedding.provider, embedding.model)
    defaults_applied = indexing_defaults is not None or query_defaults is not None
    if defaults_applied:
        embedding.indexing_params = indexing_defaults or {}
        embedding.query_params = query_defaults or {}

    path = save_initial_user_settings(embedding, defaults_applied=defaults_applied)
    _typer.echo()
    _typer.echo(f"Created user settings: {format_path_for_display(path)}")

    if defaults_applied:
        _typer.echo()
        _typer.echo(f"Applied recommended defaults for {embedding.model}:")
        _typer.echo(f"  indexing_params: {embedding.indexing_params}")
        _typer.echo(f"  query_params:    {embedding.query_params}")

    _typer.echo()
    _typer.echo(f"Testing embedding model: {embedding.provider} / {embedding.model}")
    _run_init_model_check(path)
    _typer.echo()


@app.command()
def init(
    litellm_model: str | None = _typer.Option(
        None,
        "--litellm-model",
        help="Use the given LiteLLM model and skip provider/model prompts.",
    ),
    force: bool = _typer.Option(False, "-f", "--force", help="Skip parent directory warning"),
) -> None:
    """Initialize a project for cocoindex-code."""
    cwd = Path.cwd().resolve()
    settings_file = project_settings_path(cwd)

    user_path = user_settings_path()
    if user_path.is_file():
        if litellm_model is not None:
            display_path = format_path_for_display(user_path)
            _typer.echo(
                f"Error: global settings already exist at {display_path}.\n"
                "Edit that file or remove it before passing `--litellm-model`.",
                err=True,
            )
            raise _typer.Exit(code=1)
    else:
        _setup_user_settings_interactive(litellm_model)

    # Check if already initialized
    if settings_file.is_file():
        _typer.echo("Project already initialized.")
        return

    # Check parent directories for markers
    if not force:
        parent = find_parent_with_marker(cwd)
        if parent is not None and parent != cwd:
            display_parent = format_path_for_display(parent)
            _typer.echo(
                f"Warning: A parent directory has a project marker: {display_parent}\n"
                "You might want to run `ccc init` there instead.\n"
                "Use `ccc init -f` to initialize here anyway."
            )
            raise _typer.Exit(code=1)

    # Create project settings
    save_project_settings(cwd, default_project_settings())
    _typer.echo(f"Created project settings: {format_path_for_display(settings_file)}")

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


# ------------------------- Multi-repo & Config CLI -------------------------
def _load_workspace(config_path: str | None):
    from .config import load_codebase_config
    from .multi_repo import MultiRepoOrchestrator

    cfg, cfg_path = load_codebase_config(config_path)
    return MultiRepoOrchestrator(cfg, cfg_path), cfg_path


def _sync_repos_impl(config_path: str | None, repo_ids: list[str], force: bool) -> None:
    orchestrator, _ = _load_workspace(config_path)
    _typer.echo("Syncing repositories...")
    results = orchestrator.sync_and_link_repos(repo_ids=repo_ids or None, force=force)
    for r in results:
        _typer.echo(
            f"{r.repo_id}: fetched={r.fetched} skipped={r.skipped} "
            f"removed={r.removed} bytes={r.bytes_downloaded} errors={len(r.errors)}"
        )


def _config_validate_impl(config_path: str | None) -> None:
    try:
        _, cfg_path = _load_workspace(config_path)
        _typer.echo(f"Config OK: {cfg_path}")
    except Exception as e:
        _typer.echo(f"Config validation failed: {e}", err=True)
        raise _typer.Exit(code=1)


def _config_show_impl(config_path: str | None) -> None:
    import json

    orchestrator, _ = _load_workspace(config_path)
    try:
        data = orchestrator.config.model_dump()
    except Exception:
        data = orchestrator.config.dict()
    _typer.echo(json.dumps(data, indent=2))


def _repo_status_impl(config_path: str | None) -> None:
    import json

    orchestrator, _ = _load_workspace(config_path)
    _typer.echo(json.dumps(orchestrator.run_status(), indent=2))


def _workspace_index_impl(
    config_path: str | None,
    repo_ids: list[str],
    force: bool,
    skip_sync: bool,
    skip_declarations: bool,
    changed_paths_file: str | None,
    refresh_semantic_index: bool,
    strict: bool,
) -> None:
    if not skip_declarations:
        _typer.echo(
            "Native declaration graph indexing is available through "
            "`ccc codebase graph build`; semantic workspace indexing will run here.",
            err=True,
        )
        if strict:
            raise _typer.Exit(code=1)

    orchestrator, _ = _load_workspace(config_path)
    repo_filter = repo_ids or None
    changed_paths = None
    if changed_paths_file:
        from .multi_repo import read_changed_paths_file

        changed_paths = read_changed_paths_file(changed_paths_file)
    if changed_paths_file and not refresh_semantic_index:
        sync_scope = repo_filter
        if sync_scope is None and changed_paths:
            sync_scope = orchestrator.repo_ids_for_changed_paths(changed_paths) or None
        orchestrator.link_repos(repo_ids=sync_scope)
        _typer.echo(
            f"Changed paths were provided ({len(changed_paths or [])}); "
            "semantic refresh skipped by request."
        )
        return

    if changed_paths_file:
        output = orchestrator.incremental_unified_index(
            repo_ids=repo_filter, skip_sync=False, changed_paths=changed_paths
        )
    else:
        if skip_sync:
            orchestrator.link_repos(repo_ids=repo_filter)
            output = orchestrator.build_unified_index(repo_ids=repo_filter, skip_sync=True)
        elif force:
            orchestrator.sync_and_link_repos(repo_ids=repo_filter, force=True)
            output = orchestrator.build_unified_index(repo_ids=repo_filter, skip_sync=True)
        else:
            output = orchestrator.build_unified_index(repo_ids=repo_filter, skip_sync=False)
    if output.strip():
        _typer.echo(output.strip())


def _workspace_watch_paths(config_path: Path) -> tuple[Path, Path, Path]:
    state_dir = config_path.parent / ".cocoindex_code"
    return (
        state_dir / "workspace-watch.pid",
        state_dir / "workspace-watch.log",
        state_dir / "workspace-watch-changed-paths.txt",
    )


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _workspace_watch_status(config_path: Path) -> None:
    pid_file, log_file, _ = _workspace_watch_paths(config_path)
    if not pid_file.exists():
        _typer.echo("Workspace watcher not running.")
        return
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        _typer.echo(f"Workspace watcher has invalid pid file: {pid_file}", err=True)
        raise _typer.Exit(code=1)
    if _pid_is_alive(pid):
        _typer.echo(f"Workspace watcher running (pid {pid}).")
        _typer.echo(f"Log: {log_file}")
        return
    _typer.echo(f"Workspace watcher not running; removing stale pid {pid}.")
    pid_file.unlink(missing_ok=True)


def _workspace_watch_stop(config_path: Path) -> None:
    pid_file, _, _ = _workspace_watch_paths(config_path)
    if not pid_file.exists():
        _typer.echo("Workspace watcher not running.")
        return
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        _typer.echo("Removed invalid workspace watcher pid file.")
        return
    if _pid_is_alive(pid):
        os.kill(pid, signal.SIGTERM)
        _typer.echo(f"Stopped workspace watcher (pid {pid}).")
    else:
        _typer.echo(f"Removed stale workspace watcher pid file (pid {pid}).")
    pid_file.unlink(missing_ok=True)


def _workspace_watch_start(config_path: Path, interval: float) -> None:
    pid_file, log_file, _ = _workspace_watch_paths(config_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = -1
        if pid > 0 and _pid_is_alive(pid):
            _typer.echo(f"Workspace watcher already running (pid {pid}).")
            return
        pid_file.unlink(missing_ok=True)

    log_fh = log_file.open("a", encoding="utf-8")
    cmd = [
        sys.argv[0],
        "workspace",
        "watch",
        "--config",
        str(config_path),
        "--interval",
        str(interval),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(config_path.parent),
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    _typer.echo(f"Workspace watcher started (pid {proc.pid}).")
    _typer.echo(f"Log: {log_file}")


def _compile_pathspec(patterns: list[str]):
    import pathspec

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _workspace_file_snapshot(orchestrator: object) -> dict[str, tuple[int, int]]:
    from .config import RepoType

    snapshot: dict[str, tuple[int, int]] = {}
    common_prune_dirs = {
        ".git",
        ".cocoindex_code",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "out",
        ".next",
        ".turbo",
        ".cache",
        "__pycache__",
    }
    for repo in getattr(orchestrator, "config").repos:
        if not getattr(repo, "enabled", True) or repo.type != RepoType.local:
            continue
        root = orchestrator._resolve_local_repo_path(repo)  # noqa: SLF001
        if not root.exists():
            continue
        settings = orchestrator._coalesced_repo_settings(repo)  # noqa: SLF001
        include_patterns = (
            settings.get("include_patterns") or getattr(orchestrator, "config").include_patterns
        )
        exclude_patterns = (
            settings.get("exclude_patterns") or getattr(orchestrator, "config").exclude_patterns
        )
        include_spec = _compile_pathspec(list(include_patterns or ["**/*"]))
        exclude_spec = _compile_pathspec(list(exclude_patterns or []))

        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = [
                d
                for d in dirnames
                if d not in common_prune_dirs
                and not exclude_spec.match_file(f"{d}/" if rel_dir == "." else f"{rel_dir}/{d}/")
            ]
            for filename in filenames:
                file_path = Path(dirpath) / filename
                rel = file_path.relative_to(root).as_posix()
                if not include_spec.match_file(rel) or exclude_spec.match_file(rel):
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                snapshot[f"{repo.id}/{rel}"] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _workspace_watch_foreground(config_path: str | None, interval: float) -> None:
    orchestrator, cfg_path = _load_workspace(config_path)
    _, _, changed_paths_file = _workspace_watch_paths(cfg_path)
    changed_paths_file.parent.mkdir(parents=True, exist_ok=True)
    previous = _workspace_file_snapshot(orchestrator)
    _typer.echo(f"Watching workspace from {cfg_path}. Press Ctrl-C to stop.")
    try:
        while True:
            import time

            time.sleep(interval)
            current = _workspace_file_snapshot(orchestrator)
            changed = sorted(
                path
                for path in set(previous) | set(current)
                if previous.get(path) != current.get(path)
            )
            if not changed:
                continue
            changed_paths_file.write_text("\n".join(changed) + "\n", encoding="utf-8")
            _typer.echo(f"Detected {len(changed)} changed indexed path(s).")
            output = orchestrator.incremental_unified_index(
                skip_sync=False,
                changed_paths=changed,
            )
            if output.strip():
                _typer.echo(output.strip())
            previous = _workspace_file_snapshot(orchestrator)
    except KeyboardInterrupt:
        _typer.echo("Stopped workspace watcher.")


def _enabled_repo_ids(orchestrator: object) -> list[str]:
    repos = getattr(orchestrator, "config").repos
    return [repo.id for repo in repos if getattr(repo, "enabled", True)]


def _default_repo_id(orchestrator: object, repo_id: str | None) -> str:
    if repo_id:
        return repo_id
    repo_ids = _enabled_repo_ids(orchestrator)
    if not repo_ids:
        _typer.echo("No enabled repositories in config.", err=True)
        raise _typer.Exit(code=1)
    return repo_ids[0]


def _workspace_declarations_db(orchestrator: object) -> Path:
    unified_root = getattr(orchestrator, "unified_root")
    declarations_db = unified_root / ".cocoindex_code" / "declarations.db"
    if declarations_db.exists():
        return declarations_db
    return target_sqlite_db_path(unified_root)


def _project_graph_db(project_root: Path) -> Path:
    declarations_db = project_root / ".cocoindex_code" / "declarations.db"
    if declarations_db.exists():
        return declarations_db
    return target_sqlite_db_path(project_root)


def _configured_repo_root(orchestrator: object, repo_id: str) -> Path:
    from .config import RepoType

    repo = getattr(orchestrator, "config").repo_by_id(repo_id)
    if repo is None:
        _typer.echo(f"Unknown repo id: {repo_id}", err=True)
        raise _typer.Exit(code=1)

    if repo.type == RepoType.local:
        root = Path(repo.path or ".")
        if not root.is_absolute():
            root = getattr(orchestrator, "repo_root_hint") / root
        return root.resolve()

    return (getattr(orchestrator, "unified_root") / repo.id).resolve()


def _print_json(data: object) -> None:
    import json

    _typer.echo(json.dumps(data, indent=2, sort_keys=True))


def _impact_impl(
    config_path: str | None,
    repo_id: str | None,
    ref_spec: str,
    path_prefix: str | None,
    top_n: int,
) -> dict[str, object]:
    from .mcp_handlers import detect_changes_tool

    orchestrator, _ = _load_workspace(config_path)
    rid = _default_repo_id(orchestrator, repo_id)
    result = detect_changes_tool(
        _workspace_declarations_db(orchestrator),
        _configured_repo_root(orchestrator, rid),
        rid,
        ref_spec,
        path_prefix=path_prefix,
        top_n=top_n,
    )
    return result


def _analytics_impl(
    config_path: str | None,
    repo_id: str | None,
    recompute: bool,
    hub_limit: int,
    community_limit: int,
) -> dict[str, object]:
    from .analytics.centrality import compute_centrality
    from .analytics.communities import compute_communities
    from .mcp_handlers import get_architecture_overview_tool, get_knowledge_gaps_tool

    orchestrator, _ = _load_workspace(config_path)
    rid = repo_id
    db_path = _workspace_declarations_db(orchestrator)
    result: dict[str, object] = {}
    if recompute:
        result["centrality"] = compute_centrality(db_path, repo_id=rid)
        result["communities_compute"] = compute_communities(db_path, repo_id=rid)
    result["architecture"] = get_architecture_overview_tool(
        db_path,
        repo_id=rid,
        hub_limit=hub_limit,
        community_limit=community_limit,
    )
    result["knowledge_gaps"] = get_knowledge_gaps_tool(db_path, repo_id=rid)
    return result


@repos_app.command("sync")
def repos_sync(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    repo_id: list[str] = _typer.Option([], "--repo-id", help="One or more repo ids to sync"),
    force: bool = _typer.Option(False, "--force", help="Force resync"),
) -> None:
    """Sync configured repositories and create symlinks in the unified root."""
    _sync_repos_impl(config, repo_id, force)


@config_app.command("validate")
def config_validate_nested(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
) -> None:
    """Validate a coco-config.yml file."""
    _config_validate_impl(config)


@config_app.command("show")
def config_show_nested(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
) -> None:
    """Pretty-print the loaded config."""
    _config_show_impl(config)


@repos_app.command("status")
def repos_status(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
) -> None:
    """Show status of configured repositories (synced_at, file counts, rate limits)."""
    _repo_status_impl(config)


@workspace_app.command("index")
def workspace_index(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    repo_id: list[str] = _typer.Option([], "--repo-id", help="One or more repo ids to index"),
    force: bool = _typer.Option(False, "--force", help="Force repository sync"),
    skip_sync: bool = _typer.Option(False, "--skip-sync", help="Skip repository sync"),
    skip_declarations: bool = _typer.Option(False, "--skip-declarations", help="Skip declarations"),
    changed_paths_file: str | None = _typer.Option(None, "--changed-paths-file"),
    refresh_semantic_index: bool = _typer.Option(False, "--refresh-semantic-index"),
    strict: bool = _typer.Option(False, "--strict", help="Fail on degraded unsupported steps"),
) -> None:
    """Index a configured multi-repository workspace."""
    _workspace_index_impl(
        config,
        repo_id,
        force,
        skip_sync,
        skip_declarations,
        changed_paths_file,
        refresh_semantic_index,
        strict,
    )


@workspace_app.command("watch")
def workspace_watch(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    daemon: str | None = _typer.Option(None, "--daemon", help="start, stop, or status"),
    interval: float = _typer.Option(5.0, "--interval", min=1.0, help="Polling interval seconds"),
) -> None:
    """Watch a configured workspace and run incremental indexing on changes."""
    if daemon not in (None, "start", "stop", "status"):
        _typer.echo("Error: --daemon must be start, stop, or status.", err=True)
        raise _typer.Exit(code=1)
    _, cfg_path = _load_workspace(config)
    if daemon == "status":
        _workspace_watch_status(cfg_path)
        return
    if daemon == "stop":
        _workspace_watch_stop(cfg_path)
        return
    if daemon == "start":
        _workspace_watch_start(cfg_path, interval)
        return
    _workspace_watch_foreground(config, interval)


@app.command()
def impact(
    ref_spec: str = _typer.Argument("HEAD", help="Git ref/range to diff against"),
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    repo: str | None = _typer.Option(None, "--repo", help="Configured repo id"),
    path_prefix: str | None = _typer.Option(None, "--path-prefix", help="Limit changed paths"),
    top_n: int = _typer.Option(20, "--top-n", min=1, help="Maximum declarations to return"),
) -> None:
    """Map git changes to affected declarations and risk scores."""
    result = _impact_impl(config, repo, ref_spec, path_prefix, top_n)
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@app.command()
def review(
    ref_spec: str = _typer.Argument("HEAD", help="Git ref/range to diff against"),
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    repo: str | None = _typer.Option(None, "--repo", help="Configured repo id"),
    path_prefix: str | None = _typer.Option(None, "--path-prefix", help="Limit changed paths"),
    top_n: int = _typer.Option(20, "--top-n", min=1, help="Maximum declarations to return"),
) -> None:
    """Emit review-oriented change impact context as JSON."""
    result = _impact_impl(config, repo, ref_spec, path_prefix, top_n)
    _print_json({"success": result.get("success", False), "review": result})
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@app.command()
def analytics(
    config: str | None = _typer.Option(None, "--config", "-c", help="Path to coco-config.yml"),
    repo: str | None = _typer.Option(None, "--repo", help="Configured repo id"),
    recompute: bool = _typer.Option(False, "--recompute", help="Recompute graph analytics first"),
    hub_limit: int = _typer.Option(20, "--hub-limit", min=1),
    community_limit: int = _typer.Option(10, "--community-limit", min=1),
) -> None:
    """Print architecture and knowledge-gap analytics for a configured workspace."""
    result = _analytics_impl(config, repo, recompute, hub_limit, community_limit)
    _print_json(result)
    failures = [
        value
        for value in result.values()
        if isinstance(value, dict) and value.get("success") is False
    ]
    if failures:
        raise _typer.Exit(code=1)


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

    project_root_path = require_project_root()
    project_root = str(project_root_path)
    print_project_header(project_root)

    _typer.echo(f"Settings: {format_path_for_display(project_settings_path(project_root_path))}")
    db_path = target_sqlite_db_path(project_root_path)
    if db_path.exists():
        _typer.echo(f"Index DB: {format_path_for_display(db_path)}")

    print_index_stats(_client.project_status(project_root))


@codebase_app.command("index")
@_catch_daemon_start_error
def codebase_index() -> None:
    """Create or update the local codebase index."""
    from .code_graph_indexer import index_code_declarations

    project_root = require_project_root()
    index()
    graph = index_code_declarations(project_root, _project_graph_db(project_root), repo_id="local")
    _typer.echo(f"Graph index: {graph['files']} files, {graph['declarations']} declarations")


@codebase_app.command("update")
@_catch_daemon_start_error
def codebase_update() -> None:
    """Refresh the local codebase index after edits."""
    codebase_index()


@codebase_graph_app.command("build")
def codebase_graph_build(
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
) -> None:
    """Build or refresh native declaration graph data."""
    from .code_graph_indexer import index_code_declarations

    project_root = require_project_root()
    result = index_code_declarations(
        project_root, _project_graph_db(project_root), repo_id=repo or "local"
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_app.command("search")
@_catch_daemon_start_error
def codebase_search(
    query: list[str] = _typer.Argument(..., help="Search query"),
    mode: str = _typer.Option(
        "hybrid", "--mode", help="Search mode: hybrid, vector, keyword, or grep"
    ),
    lang: list[str] = _typer.Option([], "--lang", help="Filter by language"),
    path: str | None = _typer.Option(None, "--path", help="Filter by file path prefix"),
    limit: int = _typer.Option(10, "--limit", min=1, max=100),
    refresh: bool = _typer.Option(False, "--refresh", help="Refresh index before searching"),
) -> None:
    """Search with semantic, keyword, hybrid, or grep mode."""
    project_root = require_project_root()
    query_str = " ".join(query)
    mode_value = mode.lower()
    if mode_value not in {"hybrid", "vector", "keyword", "grep"}:
        _typer.echo("Error: --mode must be hybrid, vector, keyword, or grep.", err=True)
        raise _typer.Exit(code=1)
    if refresh:
        _run_index_with_progress(str(project_root))

    if mode_value == "vector":
        resp = _search_with_wait_spinner(
            project_root=str(project_root),
            query=query_str,
            languages=lang or None,
            paths=[path] if path else None,
            limit=limit,
            offset=0,
        )
        print_search_results(resp)
        return

    if mode_value == "grep":
        from .mcp_handlers import ripgrep_bounded_tool

        _print_json(
            ripgrep_bounded_tool(
                str(project_root),
                query_str,
                path_prefix=path,
                max_matches=limit,
            )
        )
        return

    from . import client as _client
    from . import hybrid_search as _hybrid_search

    db_path = target_sqlite_db_path(project_root)
    _hybrid_search.ensure_fts_index(db_path, force_rebuild=refresh)
    keyword_results = _hybrid_search.keyword_search(
        db_path,
        query_str,
        limit=limit,
        path_prefix=path,
        language=lang[0] if lang else None,
    )
    if mode_value == "keyword":
        _print_json({"success": True, "results": [hit.__dict__ for hit in keyword_results]})
        return

    vector_resp = _client.search(
        str(project_root),
        query_str,
        languages=lang or None,
        paths=[path] if path else None,
        limit=limit,
    )
    vector_results = [
        {
            "file_path": result.file_path,
            "language": result.language,
            "content": result.content,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "score": result.score,
        }
        for result in vector_resp.results
    ]
    fused = _hybrid_search.reciprocal_rank_fusion(
        vector_results=vector_results, keyword_results=keyword_results, limit=limit
    )
    _print_json({"success": True, "results": fused})


@codebase_app.command("status")
@_catch_daemon_start_error
def codebase_status() -> None:
    """Show index and graph status for the current project."""
    status()


@codebase_graph_app.command("stats")
def codebase_graph_stats(
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
) -> None:
    """Show graph statistics."""
    from .mcp_handlers import codebase_graph_stats_tool

    result = codebase_graph_stats_tool(_project_graph_db(require_project_root()), repo_id=repo)
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_graph_app.command("query")
def codebase_graph_query(
    file_path: str = _typer.Argument(..., help="Relative file path"),
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
) -> None:
    """Show imports, imported-by files, and symbols for a file."""
    from .mcp_handlers import codebase_graph_query_tool

    result = codebase_graph_query_tool(
        _project_graph_db(require_project_root()), file_path, repo_id=repo
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_graph_app.command("circular")
def codebase_graph_circular(
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
    limit: int = _typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """Find file-level circular dependencies."""
    from .mcp_handlers import codebase_graph_circular_tool

    result = codebase_graph_circular_tool(
        _project_graph_db(require_project_root()), repo_id=repo, limit=limit
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_graph_app.command("visualize")
def codebase_graph_visualize(
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
    limit: int = _typer.Option(120, "--limit", min=1, max=500),
) -> None:
    """Return a Mermaid dependency graph."""
    from .declarations_db import db_connection

    db_path = _project_graph_db(require_project_root())
    try:
        with db_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.file_path AS from_file, d.file_path AS to_file
                FROM calls c
                JOIN declarations d ON d.id = c.callee_decl_id
                WHERE c.callee_decl_id IS NOT NULL
                  AND c.file_path != d.file_path
                  AND (? IS NULL OR c.repo_id = ?)
                LIMIT ?
                """,
                (repo, repo, limit),
            ).fetchall()
        lines = ["graph TD"]
        for row in rows:
            src = str(row["from_file"]).replace("-", "_").replace("/", "_").replace(".", "_")
            dst = str(row["to_file"]).replace("-", "_").replace("/", "_").replace(".", "_")
            lines.append(f'  {src}["{row["from_file"]}"] --> {dst}["{row["to_file"]}"]')
        _print_json({"success": True, "mode": "mermaid", "mermaid": "\n".join(lines)})
    except Exception as exc:
        _print_json({"success": False, "error": str(exc)})
        raise _typer.Exit(code=1)


@codebase_graph_app.command("remove")
def codebase_graph_remove() -> None:
    """Remove derived graph edges and analytics from the current project."""
    from .declarations_db import db_connection

    try:
        deleted = {}
        with db_connection(_project_graph_db(require_project_root())) as conn:
            for table in (
                "calls",
                "inherits",
                "centrality",
                "communities",
                "tests",
                "declarations",
                "imports",
                '"references"',
                "file_signatures",
            ):
                deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
        _print_json({"success": True, "removed": deleted})
    except Exception as exc:
        _print_json({"success": False, "error": str(exc)})
        raise _typer.Exit(code=1)


@codebase_app.command("impact")
def codebase_impact(
    target: str = _typer.Argument(..., help="File path or symbol name"),
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
    depth: int = _typer.Option(3, "--depth", min=1, max=10),
    max_nodes: int = _typer.Option(200, "--max-nodes", min=1, max=1000),
) -> None:
    """Show blast radius for a file path or symbol name."""
    from .mcp_handlers import codebase_impact_tool

    result = codebase_impact_tool(
        _project_graph_db(require_project_root()),
        target=target,
        repo_id=repo,
        depth=depth,
        max_nodes=max_nodes,
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_app.command("flow")
def codebase_flow(
    entrypoint: str | None = _typer.Argument(None, help="Optional entrypoint symbol"),
    file: str | None = _typer.Option(None, "--file", help="File path to disambiguate"),
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
    depth: int = _typer.Option(5, "--depth", min=1, max=10),
) -> None:
    """Trace forward call flow, or list likely entrypoints when omitted."""
    from .mcp_handlers import codebase_flow_tool

    result = codebase_flow_tool(
        _project_graph_db(require_project_root()),
        entrypoint=entrypoint,
        file=file,
        repo_id=repo,
        depth=depth,
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_app.command("symbol")
def codebase_symbol(
    name: str = _typer.Argument(..., help="Symbol name"),
    file: str | None = _typer.Option(None, "--file", help="File path to disambiguate"),
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
) -> None:
    """Show definition, callers, and callees for a symbol."""
    from .mcp_handlers import codebase_symbol_tool

    result = codebase_symbol_tool(
        _project_graph_db(require_project_root()), name=name, file=file, repo_id=repo
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_app.command("symbols")
def codebase_symbols(
    file: str | None = _typer.Option(None, "--file", help="Filter by file"),
    query: str | None = _typer.Option(None, "--query", help="Filter by symbol name"),
    repo: str | None = _typer.Option(None, "--repo", help="Optional repository id"),
    limit: int = _typer.Option(200, "--limit", min=1, max=1000),
) -> None:
    """List symbols by file or name."""
    from .mcp_handlers import codebase_symbols_tool

    result = codebase_symbols_tool(
        _project_graph_db(require_project_root()),
        file=file,
        query=query,
        repo_id=repo,
        limit=limit,
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_context_app.command("list")
def codebase_context_list() -> None:
    """List configured context artifacts."""
    from .mcp_handlers import codebase_context_list_tool

    result = codebase_context_list_tool(require_project_root())
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_context_app.command("index")
def codebase_context_index() -> None:
    """Index configured context artifact metadata."""
    from .mcp_handlers import codebase_context_index_tool

    result = codebase_context_index_tool(require_project_root())
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_context_app.command("search")
def codebase_context_search(
    query: list[str] = _typer.Argument(..., help="Search query"),
    artifact: str | None = _typer.Option(None, "--artifact", help="Optional artifact name"),
    limit: int = _typer.Option(10, "--limit", min=1, max=100),
) -> None:
    """Search configured context artifacts."""
    from .mcp_handlers import codebase_context_search_tool

    result = codebase_context_search_tool(
        require_project_root(), " ".join(query), artifact=artifact, limit=limit
    )
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_context_app.command("remove")
def codebase_context_remove() -> None:
    """Remove indexed context artifact metadata."""
    from .mcp_handlers import codebase_context_remove_tool

    result = codebase_context_remove_tool(require_project_root())
    _print_json(result)
    if not result.get("success", False):
        raise _typer.Exit(code=1)


@codebase_app.command("health")
@_catch_daemon_start_error
def codebase_health() -> None:
    """Check daemon and local project health."""
    from . import client as _client

    project_root = require_project_root()
    try:
        daemon = _client.daemon_status()
        project = _client.project_status(str(project_root))
        _print_json(
            {
                "success": True,
                "daemon": {
                    "version": daemon.version,
                    "uptime_seconds": daemon.uptime_seconds,
                    "projects": [p.project_root for p in daemon.projects],
                },
                "project": {
                    "index_exists": project.index_exists,
                    "files": project.total_files,
                    "chunks": project.total_chunks,
                    "indexing": project.indexing,
                },
            }
        )
    except Exception as exc:
        _print_json({"success": False, "error": str(exc)})
        raise _typer.Exit(code=1)


@codebase_app.command("projects")
@_catch_daemon_start_error
def codebase_projects() -> None:
    """List projects loaded in the daemon."""
    from . import client as _client

    try:
        daemon = _client.daemon_status()
        _print_json(
            {
                "success": True,
                "projects": [
                    {"project_root": p.project_root, "indexing": p.indexing}
                    for p in daemon.projects
                ],
            }
        )
    except Exception as exc:
        _print_json({"success": False, "error": str(exc)})
        raise _typer.Exit(code=1)


@codebase_app.command("about")
def codebase_about() -> None:
    """Explain available codebase tools."""
    _print_json(
        {
            "success": True,
            "name": "cocoindex-code",
            "summary": (
                "Native codebase intelligence: hybrid search, dependency graph, "
                "impact analysis, symbols, and context artifacts."
            ),
            "tools": [
                "codebase index",
                "codebase update",
                "codebase search",
                "codebase status",
                "codebase graph *",
                "codebase impact",
                "codebase flow",
                "codebase symbol",
                "codebase symbols",
                "codebase context *",
                "codebase health",
                "codebase projects",
            ],
        }
    )


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
        cocoindex_db_path(project_root),
        target_sqlite_db_path(project_root),
    ]
    settings_file = project_settings_path(project_root)

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
            _typer.echo(f"  {format_path_for_display(f)}")

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
    tag = _ok_fail_tag(result.ok)
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

    # --- 1. Global settings (local, no daemon needed) ---
    _print_section("Global Settings")
    settings_path = user_settings_path()
    _typer.echo(f"  Settings: {format_path_for_display(settings_path)}")
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
            if env_resp.host_path_mappings:
                _typer.echo("  Host path mappings:")
                for m in env_resp.host_path_mappings:
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
        ps_path = project_settings_path(project_root)
        _typer.echo(f"  Settings: {format_path_for_display(ps_path)}")
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
    from ._daemon_paths import daemon_log_path as _daemon_log_path

    _typer.echo(f"  Daemon logs: {format_path_for_display(_daemon_log_path())}")
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
            _typer.echo(f"  {format_path_for_display(p.project_root)} [{state}]")
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
    from ._daemon_paths import daemon_pid_path
    from .client import is_daemon_running, stop_daemon

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

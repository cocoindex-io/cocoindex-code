"""CLI entry point for cocoindex-code (ccc command)."""

from __future__ import annotations

import functools
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import typer as _typer

if TYPE_CHECKING:
    from .grep import FileMatches, GrepWarning
    from .protocol import (
        DoctorCheckResult,
        IndexingProgress,
        ProjectStatusResponse,
        SearchResponse,
    )

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
        from .client import DaemonStartError

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
            if isinstance(e, _client.DaemonStartError):
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


def _st_model_rejection_reason(model: str) -> str | None:
    """Why ``model`` can't be a sentence-transformers model, or None if it's fine.

    sentence-transformers loads HuggingFace model ids. An ``ollama/`` prefix is a
    LiteLLM/Ollama route that ST tries (and fails) to resolve as a HuggingFace
    repo — the user wants the litellm provider instead (issue #181). Real
    HuggingFace ids that contain an ``org/`` slash (``Snowflake/...``,
    ``openai/...``) are left alone.
    """
    if model.strip().lower().startswith("ollama/"):
        return (
            "ollama/… models run via litellm, not sentence-transformers — "
            "go back and pick the litellm provider instead."
        )
    return None


def _resolve_embedding_choice(
    litellm_model_flag: str | None,
    st_installed: bool,
    tty: bool,
    previous: EmbeddingSettings | None = None,
) -> EmbeddingSettings:
    """Resolve the embedding settings per the init control-flow diagram.

    On a retry, ``previous`` holds the choice from the last attempt; its
    provider and model become the prompt defaults so the user only edits
    what was wrong instead of retyping everything.
    """
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
                    title="sentence-transformers (local, free — built-in HuggingFace models)",
                    value="sentence-transformers",
                ),
                questionary.Choice(
                    title="litellm (100+ providers — cloud APIs & local Ollama)",
                    value="litellm",
                ),
            ],
            default=previous.provider if previous is not None else None,
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
        default_model = previous.model if previous is not None else DEFAULT_ST_MODEL
        model = questionary.text(
            "Model name",
            default=default_model,
            validate=lambda m: _st_model_rejection_reason(m) or True,
        ).ask()
    elif provider == "litellm":
        _typer.echo(f"See supported LiteLLM embedding models: {_LITELLM_MODELS_URL}")
        default_model = previous.model if previous is not None else ""
        model = questionary.text("Model name", default=default_model).ask()
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


def _run_init_model_check() -> bool:
    """Ask the daemon to test the embedding model; print results. Return True if all pass.

    Drives the check via `DoctorRequest(project_root=None)`. The daemon loads
    the model once and stays running, so the user's next `ccc index` starts
    warm. Both DaemonStartError and generic exceptions are rendered as a
    synthetic failed DoctorCheckResult — uniform failure-output shape. The
    caller decides what to show on failure (retry prompt / next-steps block).
    """
    from rich.console import Console as _Console
    from rich.live import Live as _Live
    from rich.spinner import Spinner as _Spinner

    from . import client as _client
    from .protocol import DoctorCheckResult

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

    ok = True
    for r in results:
        if r.name == "done":
            continue
        _print_doctor_result(r, verbose=False)
        if not r.ok:
            ok = False
    return ok


def _print_init_next_steps(settings_path: Path) -> None:
    """Prominent recovery block shown after a failed init model check."""
    import click as _click

    display_path = format_path_for_display(settings_path)
    _typer.echo(err=True)
    _typer.echo(_click.style("  Next steps", bold=True), err=True)
    _typer.echo(_click.style(f"  {'─' * 38}", fg="bright_black"), err=True)
    _typer.echo(
        f"  1. Edit  {_click.style(display_path, fg='cyan', bold=True)}\n"
        "     to change the model or add API keys under `envs:`.",
        err=True,
    )
    _typer.echo("  2. Run  `ccc doctor`  to verify.", err=True)
    _typer.echo()  # trailing blank before whatever init prints next


def _setup_user_settings_interactive(litellm_model_flag: str | None) -> None:
    """Interactive global-settings setup — only runs when settings are missing.

    Loops until the configured model passes its check or the user chooses to
    keep the current settings. On failure we offer a retry, but only when we
    can actually re-prompt for a different model — i.e. interactive and not
    pinned by ``--litellm-model``; otherwise we just print the next steps.
    """
    from .embedder_defaults import lookup_defaults
    from .shared import is_sentence_transformers_installed

    st_installed = is_sentence_transformers_installed()
    interactive = sys.stdin.isatty()
    previous: EmbeddingSettings | None = None

    while True:
        embedding = _resolve_embedding_choice(
            litellm_model_flag=litellm_model_flag,
            st_installed=st_installed,
            tty=interactive,
            previous=previous,
        )
        previous = embedding  # remembered as the defaults for a potential retry

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
        if _run_init_model_check():
            _typer.echo()
            return

        # Model check failed. Retry only makes sense if we can re-prompt.
        if interactive and litellm_model_flag is None:
            import questionary

            _typer.echo()  # separate the failure output from the prompt below
            choice = questionary.select(
                "The embedding model couldn't be loaded. What would you like to do?",
                choices=[
                    questionary.Choice(title="Try a different provider/model", value="retry"),
                    questionary.Choice(
                        title="Keep these settings and finish — I'll edit the file myself",
                        value="keep",
                    ),
                ],
            ).ask()
            if choice == "retry":
                continue
            # "keep" or None (cancelled) falls through to the next-steps block.

        _print_init_next_steps(path)
        return


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
def grep(
    pattern: str = _typer.Argument(
        ...,
        help=r"By-example structural pattern; use \ for metavariables, "
        r"e.g. 'def \NAME(\(ARGS*\)):' or 'foo(\(ARGS*\))'.",
    ),
    path: str = _typer.Argument(
        ".", help="File or directory to search (default: current directory)."
    ),
    lang: list[str] = _typer.Option(
        [], "--lang", help="Only match files of these languages (e.g. python, rust, cpp)."
    ),
    path_glob: str | None = _typer.Option(
        None, "--path", help="Only match files whose path matches this glob (globset syntax)."
    ),
    no_color: bool = _typer.Option(False, "--no-color", help="Disable colored output."),
) -> None:
    r"""Structurally grep code by example (no index or daemon required).

    Compiles the pattern per language and matches every supported source file
    under PATH in parallel. Inside an initialized project it honors the project's
    include/exclude and .gitignore rules; otherwise it scans all supported source
    files.
    """
    from . import grep as _grep

    target = Path(path)
    if not target.exists():
        _typer.echo(f"Error: path not found: {path}", err=True)
        raise _typer.Exit(code=1)

    req = _grep.GrepRequest(
        pattern=pattern,
        root=target,
        languages=frozenset(lang_name.lower() for lang_name in lang) or None,
        path_glob=path_glob,
    )
    use_color = not no_color and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    grep_run = _grep.Grep(req)
    matched = 0
    # `run` calls `_emit` from several worker threads at once; the lock keeps one
    # file's output (and the `matched` bookkeeping) from interleaving with another's.
    output_lock = threading.Lock()

    def _emit(item: FileMatches | GrepWarning) -> None:
        nonlocal matched
        if isinstance(item, _grep.GrepWarning):
            with output_lock:
                _typer.echo(f"warning: {item.message}", err=True)
            return
        block = _grep.render_file(item, color=use_color)  # render outside the lock
        with output_lock:
            if matched:
                _typer.echo()  # blank line between files
            _typer.echo(block)
            matched += 1

    grep_run.run(_emit)

    # The "unusable everywhere" verdict needs the whole walk, so it's known only
    # once the run is done — report it before exiting.
    if grep_run.unusable:
        langs = ", ".join(grep_run.failed_languages)
        _typer.echo(
            f"Error: the pattern did not compile for any of the languages found ({langs}).",
            err=True,
        )
        raise _typer.Exit(code=1)
    if matched == 0:
        _typer.echo("No matches found.")


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


def _print_doctor_result(result: DoctorCheckResult, *, verbose: bool = False) -> None:
    import click as _click

    if result.name == "done":
        return
    tag = _ok_fail_tag(result.ok)
    _typer.echo(f"\n  {tag} {result.name}")
    for line in result.details:
        _typer.echo(f"    {line}")
    for err in result.errors:
        _typer.echo(_click.style(f"    ERROR: {err}", fg="red"), err=True)
    if result.traceback:
        if verbose:
            for line in result.traceback.splitlines():
                _typer.echo(_click.style(f"    {line}", fg="bright_black"), err=True)
        else:
            _typer.echo(
                _click.style("    Run `ccc doctor -v` for the full traceback.", fg="bright_black"),
                err=True,
            )


@app.command()
@_catch_daemon_start_error
def doctor(
    verbose: bool = _typer.Option(
        False,
        "-v",
        "--verbose",
        help="Show full exception tracebacks for failed checks.",
    ),
) -> None:
    """Check system health and report issues."""
    from . import client as _client
    from .settings import (
        load_project_settings as _load_project_settings,
    )
    from .settings import (
        load_user_settings as _load_user_settings,
    )

    def _on_result(result: DoctorCheckResult) -> None:
        _print_doctor_result(result, verbose=verbose)

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
                on_result=_on_result,
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
                on_result=_on_result,
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

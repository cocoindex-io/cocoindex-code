"""Unit tests for shared CLI helpers."""

from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code import cli
from cocoindex_code.cli import (
    add_to_gitignore,
    remove_from_gitignore,
    require_project_root,
    resolve_default_path,
)
from cocoindex_code.protocol import SearchResponse, SearchResult

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_require_project_root_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    # Create global settings so require_project_root doesn't reject
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    assert require_project_root() == project


def test_require_project_root_exits_when_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    # Create global settings so we test the "no project" check, not "no global settings"
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    from click.exceptions import Exit

    with pytest.raises(Exit):
        require_project_root()


def test_resolve_default_path_from_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    subdir = project_root / "src" / "lib"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    result = resolve_default_path(project_root)
    assert result == "src/lib/*"


def test_resolve_default_path_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = resolve_default_path(project_root)
    assert result is None


def test_resolve_default_path_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = resolve_default_path(project_root)
    assert result is None


def test_search_help_includes_json_option() -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app, ["search", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    output = _strip_ansi(result.output)
    assert "--json" in output
    assert "--repo-key" in output


def test_bridge_help_includes_jsonrpc_option() -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app, ["bridge", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "--jsonrpc" in _strip_ansi(result.output)


def test_print_search_results_json_outputs_machine_readable_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = SearchResponse(
        success=True,
        results=[
            SearchResult(
                file_path="src/main.py",
                language="python",
                content="def main():\n    return 1",
                start_line=10,
                end_line=11,
                score=0.875,
            )
        ],
        total_returned=1,
        offset=5,
        message=None,
    )

    cli.print_search_results_json(response)

    assert json.loads(capsys.readouterr().out) == {
        "success": True,
        "results": [
            {
                "file_path": "src/main.py",
                "repo_key": None,
                "language": "python",
                "content": "def main():\n    return 1",
                "start_line": 10,
                "end_line": 11,
                "score": 0.875,
            }
        ],
        "total_returned": 1,
        "offset": 5,
        "message": None,
    }


def test_jsonrpc_bridge_ping_and_shutdown() -> None:
    input_stream = StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"shutdown"}\n'
        '{"jsonrpc":"2.0","id":3,"method":"ping"}\n'
    )
    output_stream = StringIO()

    def fake_search(
        project_root: str,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        repo_keys: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
        on_waiting: object | None = None,
    ) -> SearchResponse:
        raise AssertionError("search should not be called")

    cli.run_jsonrpc_bridge(input_stream, output_stream, fake_search)

    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert responses == [
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
        {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
    ]


def test_jsonrpc_bridge_search_uses_client_payload() -> None:
    input_stream = StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "search-1",
                "method": "search",
                "params": {
                    "project_root": "/workspace",
                    "query": "stream writer",
                    "languages": ["python"],
                    "paths": ["src/*"],
                    "repo_keys": ["repo-a"],
                    "limit": 3,
                    "offset": 2,
                },
            }
        )
        + "\n"
    )
    output_stream = StringIO()
    calls: list[dict[str, object]] = []

    def fake_search(
        project_root: str,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        repo_keys: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
        on_waiting: object | None = None,
    ) -> SearchResponse:
        calls.append(
            {
                "project_root": project_root,
                "query": query,
                "languages": languages,
                "paths": paths,
                "repo_keys": repo_keys,
                "limit": limit,
                "offset": offset,
            }
        )
        return SearchResponse(
            success=True,
            results=[
                SearchResult(
                    file_path="src/main.py",
                    repo_key="repo-a",
                    language="python",
                    content="def stream_writer(): pass",
                    start_line=4,
                    end_line=4,
                    score=0.9,
                )
            ],
            total_returned=1,
            offset=2,
            message=None,
        )

    cli.run_jsonrpc_bridge(input_stream, output_stream, fake_search)

    assert calls == [
        {
            "project_root": "/workspace",
            "query": "stream writer",
            "languages": ["python"],
            "paths": ["src/*"],
            "repo_keys": ["repo-a"],
            "limit": 3,
            "offset": 2,
        }
    ]
    response = json.loads(output_stream.getvalue())
    assert response == {
        "jsonrpc": "2.0",
        "id": "search-1",
        "result": {
            "success": True,
            "results": [
                {
                    "file_path": "src/main.py",
                    "repo_key": "repo-a",
                    "language": "python",
                    "content": "def stream_writer(): pass",
                    "start_line": 4,
                    "end_line": 4,
                    "score": 0.9,
                }
            ],
            "total_returned": 1,
            "offset": 2,
            "message": None,
        },
    }


def test_jsonrpc_bridge_returns_parse_error() -> None:
    input_stream = StringIO("{not json}\n")
    output_stream = StringIO()

    def fake_search(
        project_root: str,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        repo_keys: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
        on_waiting: object | None = None,
    ) -> SearchResponse:
        raise AssertionError("search should not be called")

    cli.run_jsonrpc_bridge(input_stream, output_stream, fake_search)

    assert json.loads(output_stream.getvalue()) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32700,
            "message": "Parse error",
        },
    }


# ---------------------------------------------------------------------------
# .gitignore helpers
# ---------------------------------------------------------------------------


def test_add_to_gitignore_creates_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    add_to_gitignore(tmp_path)
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    content = gitignore.read_text()
    assert "# CocoIndex Code (ccc)" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_appends_to_existing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "*.pyc" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("/.cocoindex_code/\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert content.count("/.cocoindex_code/") == 1


def test_add_to_gitignore_skips_when_no_git(tmp_path: Path) -> None:
    add_to_gitignore(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


def test_remove_from_gitignore(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n# CocoIndex Code (ccc)\n/.cocoindex_code/\n__pycache__/\n")
    remove_from_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "/.cocoindex_code/" not in content
    assert "# CocoIndex Code (ccc)" not in content
    assert "*.pyc" in content
    assert "__pycache__/" in content


def test_remove_from_gitignore_no_entry(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    original = "*.pyc\n__pycache__/\n"
    gitignore.write_text(original)
    remove_from_gitignore(tmp_path)
    assert gitignore.read_text() == original


# ---------------------------------------------------------------------------
# COCOINDEX_CODE_HOST_CWD callback
# ---------------------------------------------------------------------------


def test_apply_host_cwd_chdirs_to_mapped_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When COCOINDEX_CODE_HOST_CWD is set and matches the mapping, chdir to container form."""
    from cocoindex_code.cli import _apply_host_cwd
    from cocoindex_code.settings import _reset_host_path_mapping_cache

    container = tmp_path / "workspace"
    host = tmp_path / "host-home"
    (container / "proj" / "src").mkdir(parents=True)
    host.mkdir()

    _reset_host_path_mapping_cache()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", str(host / "proj" / "src"))

    _apply_host_cwd()

    # chdir resolves symlinks; compare resolved forms.
    assert Path.cwd().resolve() == (container / "proj" / "src").resolve()
    assert capsys.readouterr().err == ""

    _reset_host_path_mapping_cache()


def test_apply_host_cwd_warns_on_invalid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid COCOINDEX_CODE_HOST_CWD emits a warning but doesn't abort."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", "/nonexistent/path/xyz")
    monkeypatch.delenv("COCOINDEX_CODE_HOST_PATH_MAPPING", raising=False)

    _apply_host_cwd()

    captured = capsys.readouterr()
    assert "COCOINDEX_CODE_HOST_CWD" in captured.err
    assert "/nonexistent/path/xyz" in captured.err
    # cwd should be unchanged since chdir failed.
    assert Path.cwd() == original_cwd


def test_apply_host_cwd_noop_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With COCOINDEX_CODE_HOST_CWD unset, the callback is a silent no-op."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.delenv("COCOINDEX_CODE_HOST_CWD", raising=False)

    _apply_host_cwd()

    assert Path.cwd() == original_cwd
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# ccc init — auto-populate indexing_params / query_params from curated table
# ---------------------------------------------------------------------------


def test_init_auto_populates_known_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """For a known model, `ccc init` writes real indexing/query params into the
    file and prints an 'Applied recommended defaults' message.
    """
    from cocoindex_code.settings import EmbeddingSettings, load_user_settings

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="cohere/embed-english-v3.0"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda path: None)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "cohere/embed-english-v3.0"
    assert loaded.embedding.indexing_params == {"input_type": "search_document"}
    assert loaded.embedding.query_params == {"input_type": "search_query"}

    out = capsys.readouterr().out
    assert "Applied recommended defaults" in out


def test_init_writes_comment_template_for_unknown_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a model outside the curated table, `ccc init` writes a commented-out
    template block under ``embedding:`` instead of real keys.
    """
    from cocoindex_code.settings import (
        EmbeddingSettings,
        load_user_settings,
        user_settings_path,
    )

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="someprovider/unknown-model"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda path: None)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    content = user_settings_path().read_text()
    # Commented template present, no populated keys
    assert "# indexing_params: {}" in content
    assert "# query_params: {}" in content
    loaded = load_user_settings()
    assert loaded.embedding.indexing_params is None
    assert loaded.embedding.query_params is None

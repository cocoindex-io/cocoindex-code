from __future__ import annotations

from pathlib import Path

import click
from click.testing import CliRunner

from cocoindex_code import cgrep, hybrid_search
from cocoindex_code.settings import project_settings_path, user_settings_path


def test_normalize_argv_prepends_search() -> None:
    assert cgrep._normalize_argv(["auth flow"]) == ["search", "auth flow"]
    assert cgrep._normalize_argv(["watch"]) == ["watch"]
    assert cgrep._normalize_argv(["--help"]) == ["--help"]


def test_main_rewrites_default_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_main(*, args: list[str], prog_name: str, standalone_mode: bool) -> None:
        captured["args"] = args
        captured["prog_name"] = prog_name
        captured["standalone_mode"] = standalone_mode

    monkeypatch.setattr(cgrep.cli, "main", fake_main)
    cgrep.main(["auth flow"])

    assert captured["args"] == ["search", "auth flow"]
    assert captured["prog_name"] == "cgrep"
    assert captured["standalone_mode"] is True


def test_bootstrap_creates_settings(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    user_dir = tmp_path / "user"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))
    monkeypatch.setattr(cgrep, "is_sentence_transformers_installed", lambda: True)

    root = cgrep._bootstrap_project(project)

    assert root == project
    assert project_settings_path(project).is_file()
    assert user_settings_path().is_file()
    assert "/.cocoindex_code/" in (project / ".gitignore").read_text()


def test_bootstrap_prefers_git_root(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "proj"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(cgrep, "is_sentence_transformers_installed", lambda: True)

    root = cgrep._bootstrap_project(nested)

    assert root == project
    assert project_settings_path(project).is_file()
    assert not project_settings_path(nested).is_file()


def test_bootstrap_requires_embeddings_when_user_settings_missing(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(cgrep, "is_sentence_transformers_installed", lambda: False)

    try:
        cgrep._bootstrap_project(project)
    except click.ClickException as exc:
        assert "embeddings-local" in str(exc)
    else:
        raise AssertionError("Expected click.ClickException")


def test_search_command_uses_backend_and_renders_content(monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}
    scope = cgrep.SearchScope(
        project_root=Path("/tmp/project"),
        path_globs=["src/*"],
        path_prefix="src/",
    )

    monkeypatch.setattr(cgrep, "_ensure_search_scope", lambda raw_path: scope)
    monkeypatch.setattr(
        cgrep,
        "_maybe_refresh_index",
        lambda project_root, *, sync: captured.update(
            {"refresh_root": project_root, "refresh_sync": sync}
        ),
    )
    monkeypatch.setattr(
        cgrep,
        "_run_search",
        lambda scope_arg, query, *, mode, limit, offset, languages: captured.update(
            {
                "scope": scope_arg,
                "query": query,
                "mode": mode,
                "limit": limit,
                "offset": offset,
                "languages": languages,
            }
        )
        or [
            cgrep.ResultRow(
                file_path="src/auth.py",
                start_line=10,
                end_line=12,
                content="def login(): pass",
                language="python",
                score=0.75,
            )
        ],
    )

    result = runner.invoke(
        cgrep.cli,
        ["search", "auth flow", "src", "-c", "-s", "--mode", "hybrid", "--lang", "python"],
    )

    assert result.exit_code == 0
    assert captured["scope"] == scope
    assert captured["query"] == "auth flow"
    assert captured["mode"] == "hybrid"
    assert captured["limit"] == 10
    assert captured["offset"] == 0
    assert captured["languages"] == ["python"]
    assert captured["refresh_root"] == Path("/tmp/project")
    assert captured["refresh_sync"] is True
    assert "./src/auth.py:10-12 [python]" in result.output
    assert "def login(): pass" in result.output


def test_no_rerank_warning_only_when_flag_is_passed(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        cgrep,
        "_ensure_search_scope",
        lambda raw_path: cgrep.SearchScope(Path("/tmp/project"), None, None),
    )
    monkeypatch.setattr(cgrep, "_maybe_refresh_index", lambda project_root, *, sync: None)
    monkeypatch.setattr(
        cgrep,
        "_run_search",
        lambda scope, query, *, mode, limit, offset, languages: [],
    )

    default_result = runner.invoke(cgrep.cli, ["search", "auth flow"])
    flagged_result = runner.invoke(cgrep.cli, ["search", "auth flow", "--no-rerank"])

    assert default_result.exit_code == 0
    assert "`--no-rerank` is ignored" not in default_result.output
    assert flagged_result.exit_code == 0
    assert "`--no-rerank` is ignored" in flagged_result.output


def test_keyword_rows_uses_all_languages_and_dedupes(monkeypatch, tmp_path: Path) -> None:
    calls: list[str | None] = []

    def fake_keyword_search(
        db_path: Path,
        query: str,
        *,
        limit: int,
        path_prefix: str | None = None,
        language: str | None = None,
    ) -> list[hybrid_search.KeywordHit]:
        del db_path, query, limit, path_prefix
        calls.append(language)
        if language == "python":
            return [
                hybrid_search.KeywordHit(
                    file_path="src/auth.py",
                    content="py",
                    start_line=10,
                    end_line=12,
                    score=0.9,
                )
            ]
        if language == "typescript":
            return [
                hybrid_search.KeywordHit(
                    file_path="src/auth.py",
                    content="ts weaker duplicate",
                    start_line=10,
                    end_line=12,
                    score=0.7,
                ),
                hybrid_search.KeywordHit(
                    file_path="src/ui.ts",
                    content="ts unique",
                    start_line=20,
                    end_line=21,
                    score=0.8,
                ),
            ]
        raise AssertionError(f"unexpected language {language!r}")

    monkeypatch.setattr(hybrid_search, "keyword_search", fake_keyword_search)

    rows = cgrep._keyword_rows_for_languages(
        tmp_path / "index.db",
        "auth",
        limit=5,
        path_prefix="src/",
        languages=["python", "typescript"],
    )

    assert calls == ["python", "typescript"]
    assert [(row.file_path, row.score) for row in rows] == [
        ("src/auth.py", 0.9),
        ("src/ui.ts", 0.8),
    ]


def test_hybrid_rows_handles_offset_without_double_skipping(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cgrep, "target_sqlite_db_path", lambda project_root: tmp_path / "index.db")
    monkeypatch.setattr(hybrid_search, "ensure_fts_index", lambda db_path: None)
    monkeypatch.setattr(
        cgrep,
        "_vector_rows",
        lambda project_root, query, *, limit, offset, languages, path_globs: captured.update(
            {"vector_limit": limit, "vector_offset": offset}
        )
        or [
            cgrep.ResultRow("a.py", 1, 1, "a", "python", 0.9),
            cgrep.ResultRow("b.py", 2, 2, "b", "python", 0.8),
            cgrep.ResultRow("c.py", 3, 3, "c", "python", 0.7),
        ],
    )
    monkeypatch.setattr(
        cgrep,
        "_keyword_rows_for_languages",
        lambda db_path, query, *, limit, path_prefix, languages: captured.update(
            {"keyword_limit": limit}
        )
        or [],
    )
    monkeypatch.setattr(
        hybrid_search,
        "reciprocal_rank_fusion",
        lambda *, vector_results, keyword_results, limit: [
            {
                "file_path": row["file_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "content": row["content"],
                "language": row["language"],
                "hybrid_score": row["score"],
            }
            for row in vector_results
        ],
    )

    rows = cgrep._hybrid_rows(
        tmp_path,
        "auth",
        limit=2,
        offset=1,
        languages=["python"],
        path_globs=["src/*"],
        path_prefix="src/",
    )

    assert captured["vector_offset"] == 0
    assert captured["vector_limit"] == 2
    assert captured["keyword_limit"] == 3
    assert [row.file_path for row in rows] == ["b.py", "c.py"]

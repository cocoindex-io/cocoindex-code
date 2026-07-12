"""Tests for ``ccc search --text`` — literal / regex full-text search.

These run entirely locally (no daemon, no index, no embeddings): the engine
walks files on disk and matches their content against literal terms / regexes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code import textsearch as ts
from cocoindex_code.cli import app

runner = CliRunner()


def req_for(
    root: Path,
    *raw_terms: str,
    case_sensitive: bool | None = None,
    languages: frozenset[str] | None = None,
    path_glob: str | None = None,
) -> ts.TextSearchRequest:
    """Build a request, compiling ``raw_terms`` the way the CLI does."""
    return ts.TextSearchRequest(
        terms=tuple(ts.compile_terms(list(raw_terms), case_sensitive=case_sensitive)),
        root=root,
        languages=languages,
        path_glob=path_glob,
    )


def collect_ts(req: ts.TextSearchRequest) -> list[ts.FileMatches | ts.TextSearchWarning]:
    """Drain a run into a list (matches + read warnings), completion order."""
    items: list[ts.FileMatches | ts.TextSearchWarning] = []
    ts.TextSearch(req).run(items.append)
    return items


def run_ts(req: ts.TextSearchRequest) -> list[ts.FileMatches]:
    """Just the file matches (dropping warnings), sorted by path for deterministic
    assertions (the engine itself yields in completion order)."""
    files = [it for it in collect_ts(req) if isinstance(it, ts.FileMatches)]
    files.sort(key=lambda fm: fm.path)
    return files


def names_of(files: list[ts.FileMatches]) -> set[str]:
    return {Path(fm.path).name for fm in files}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small mixed tree (code + docs + config) with no cocoindex project marker."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def authenticate(user, password):\n"
        "    # TODO: add rate limiting\n"
        "    return check(password)\n"
    )
    (tmp_path / "src" / "db.py").write_text(
        "def connect(dsn):\n    password = dsn.get('password')\n    return password\n"
    )
    (tmp_path / "src" / "util").mkdir()
    (tmp_path / "src" / "util" / "log.py").write_text(
        "def log_error(msg):\n    # TODO: structured logging\n    print(msg)\n"
    )
    (tmp_path / "README.md").write_text(
        "# Project\n\nHandles authentication and passwords.\n\nTODO: write docs\n"
    )
    (tmp_path / "config.yaml").write_text("db:\n  host: localhost\n  password: s3cret\n")
    (tmp_path / "notes.txt").write_text("Remember the Password policy.\nNothing else here.\n")
    # Non-UTF-8 bytes, but a whitelisted extension: must be skipped silently.
    (tmp_path / "src" / "blob.py").write_bytes(b"\x00\x01\xff\xfe password \x80\x81 TODO\x00")
    return tmp_path


# ---------------------------------------------------------------------------
# Term parsing
# ---------------------------------------------------------------------------


def _one(raw: str, *, case_sensitive: bool | None = None) -> ts.Term:
    (term,) = ts.compile_terms([raw], case_sensitive=case_sensitive)
    return term


def test_literal_matches_substring_smart_case() -> None:
    term = _one("password")
    assert not term.is_regex
    assert term.pattern.search("has a password here")
    assert term.pattern.search("PASSWORD")  # lowercase term → smart-case insensitive


def test_uppercase_term_is_case_sensitive() -> None:
    term = _one("Password")
    assert term.case_sensitive
    assert term.pattern.search("Password")
    assert not term.pattern.search("password")  # an uppercase letter → case-sensitive


def test_case_override() -> None:
    assert _one("Password", case_sensitive=False).pattern.search("password")
    assert not _one("password", case_sensitive=True).pattern.search("PASSWORD")


def test_literal_metachars_are_escaped() -> None:
    # A literal with regex metacharacters matches literally, not as a pattern.
    term = _one("a.b")
    assert not term.is_regex
    assert term.pattern.search("a.b")
    assert not term.pattern.search("axb")  # '.' is a literal dot, not "any char"


def test_regex_term() -> None:
    term = _one(r"/def \w+\(/")
    assert term.is_regex
    assert term.pattern.search("def foo(")
    assert not term.pattern.search("definitely typed")


def test_slash_wrapped_with_free_slash_stays_literal() -> None:
    # GitHub rule: a free (unescaped) `/` in the body → literal, not a regex.
    term = _one("/blobs/docs-v1/")
    assert not term.is_regex
    assert term.pattern.search("see /blobs/docs-v1/ path")
    assert not term.pattern.search("blobs docs-v1")


def test_regex_can_match_literal_slashes_when_escaped() -> None:
    # Escaping the slashes inside a regex matches a literal `/foo/`.
    term = _one(r"/\/foo\//")
    assert term.is_regex
    assert term.pattern.search("x /foo/ y")
    assert not term.pattern.search("foo")


def test_bare_double_slash_is_literal() -> None:
    term = _one("//")  # too short to be a regex body
    assert not term.is_regex
    assert term.pattern.search("a // comment")


def test_invalid_regex_raises() -> None:
    with pytest.raises(ts.TermSyntaxError):
        ts.compile_terms(["/def(/"], case_sensitive=None)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def test_single_literal_across_files(corpus: Path) -> None:
    files = run_ts(req_for(corpus, "password"))
    # Every file containing "password" (case-insensitive); the binary blob skipped.
    assert names_of(files) == {"auth.py", "db.py", "README.md", "config.yaml", "notes.txt"}


def test_and_semantics(corpus: Path) -> None:
    # A file qualifies only if *every* term appears. "TODO" (uppercase → case-
    # sensitive) + "password" → only auth.py and README.md carry both.
    files = run_ts(req_for(corpus, "TODO", "password"))
    assert names_of(files) == {"auth.py", "README.md"}


def test_smart_case_sensitive(corpus: Path) -> None:
    files = run_ts(req_for(corpus, "Password"))  # uppercase → case-sensitive
    assert names_of(files) == {"notes.txt"}


def test_case_insensitive_override(corpus: Path) -> None:
    files = run_ts(req_for(corpus, "Password", case_sensitive=False))
    assert names_of(files) == {"auth.py", "db.py", "README.md", "config.yaml", "notes.txt"}


def test_regex_across_files(corpus: Path) -> None:
    files = run_ts(req_for(corpus, r"/def \w+\(/"))
    assert names_of(files) == {"auth.py", "db.py", "log.py"}


def test_language_filter(corpus: Path) -> None:
    files = run_ts(req_for(corpus, "password", languages=frozenset({"python"})))
    assert names_of(files) == {"auth.py", "db.py"}


def test_path_glob(corpus: Path) -> None:
    files = run_ts(req_for(corpus, "password", path_glob="src/**"))
    assert names_of(files) == {"auth.py", "db.py"}


def test_covers_non_code_files(corpus: Path) -> None:
    # Unlike `grep`, text search matches docs/config that have no code language.
    files = run_ts(req_for(corpus, "localhost"))
    assert names_of(files) == {"config.yaml"}


def test_binary_file_skipped_silently(corpus: Path) -> None:
    items = collect_ts(req_for(corpus, "password"))
    matched = [it for it in items if isinstance(it, ts.FileMatches)]
    assert not any(Path(fm.path).name == "blob.py" for fm in matched)
    # A non-UTF-8 file is a silent skip (like `grep -I`), not a warning.
    assert not any(isinstance(it, ts.TextSearchWarning) for it in items)


def test_no_matches(corpus: Path) -> None:
    assert run_ts(req_for(corpus, "zzz_absent_zzz")) == []


def test_matched_lines_are_reported(corpus: Path) -> None:
    # auth.py matches "password" on lines 1 and 3 (not the TODO line 2).
    (auth,) = [fm for fm in run_ts(req_for(corpus, "password")) if fm.path.endswith("auth.py")]
    assert [lm.line_no for lm in auth.matches] == [1, 3]


def test_many_files(tmp_path: Path) -> None:
    # Many files exercise the thread pool: every file matched exactly once, no
    # duplicates and no lost results.
    n = 300
    for i in range(n):
        (tmp_path / f"f{i:04d}.py").write_text(f"value_{i} = {i}\n# marker line\n")
    files = run_ts(req_for(tmp_path, "marker"))
    assert len(files) == n
    assert len({fm.path for fm in files}) == n


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_plain_format(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\nfoo = 2\n")
    rendered = ts.render_results(run_ts(req_for(tmp_path, "foo")), color=False)
    lines = rendered.split("\n")
    assert lines[0] == (tmp_path / "a.py").as_posix()  # path header
    assert lines[1] == "2| foo = 2"  # "<line>| <text>", one space after the pipe


def test_render_strips_crlf(tmp_path: Path) -> None:
    (tmp_path / "crlf.py").write_text("a = 1\r\nfoo = 2\r\n", newline="")
    rendered = ts.render_results(run_ts(req_for(tmp_path, "foo")), color=False)
    assert "\r" not in rendered
    assert "2| foo = 2" in rendered


def test_render_line_number_width(tmp_path: Path) -> None:
    (tmp_path / "w.py").write_text("".join(f"mark {i}\n" for i in range(1, 14)))  # 13 lines
    rendered = ts.render_results(run_ts(req_for(tmp_path, "mark")), color=False)
    assert "\n 1| mark 1" in rendered  # single-digit line, padded to width 2
    assert "\n13| mark 13" in rendered


def test_render_color_highlights_match(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("the password is here\n")
    rendered = ts.render_results(run_ts(req_for(tmp_path, "password")), color=True)
    assert "\x1b[" in rendered  # ANSI escapes present when color is on


# ---------------------------------------------------------------------------
# Project- and gitignore-awareness (single source of truth with the indexer)
# ---------------------------------------------------------------------------


def test_respects_project_exclude_patterns(tmp_path: Path) -> None:
    (tmp_path / ".cocoindex_code").mkdir()
    (tmp_path / ".cocoindex_code" / "settings.yml").write_text(
        "include_patterns:\n  - '**/*.py'\nexclude_patterns:\n  - '**/.*'\n  - '**/skip'\n"
    )
    (tmp_path / "keep.py").write_text("a token here\n")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "hidden.py").write_text("a token here\n")

    files = run_ts(req_for(tmp_path, "token"))
    assert "keep.py" in names_of(files)
    assert not any("skip" in fm.path for fm in files)


def test_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".cocoindex_code").mkdir()
    (tmp_path / ".cocoindex_code" / "settings.yml").write_text("include_patterns:\n  - '**/*.py'\n")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "kept.py").write_text("a token\n")
    (tmp_path / "ignored.py").write_text("a token\n")

    names = names_of(run_ts(req_for(tmp_path, "token")))
    assert "kept.py" in names
    assert "ignored.py" not in names


# ---------------------------------------------------------------------------
# CLI end-to-end (via CliRunner — no daemon needed)
# ---------------------------------------------------------------------------


def test_cli_basic(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(app, ["search", "--text", "password"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "auth.py" in result.output
    assert "db.py" in result.output


def test_cli_and(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(app, ["search", "--text", "TODO", "password"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "auth.py" in result.output
    assert "db.py" not in result.output  # db.py lacks TODO


def test_cli_no_matches(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(app, ["search", "--text", "zzz_absent"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No matches found." in result.output


def test_cli_invalid_regex(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(app, ["search", "--text", "/def(/"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "invalid regex" in result.output


def test_cli_refresh_rejected(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(
        app, ["search", "--text", "--refresh", "password"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "--refresh does not apply" in result.output


def test_cli_conflicting_case_flags(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(
        app, ["search", "--text", "-i", "-s", "password"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_cli_lang_filter(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    result = runner.invoke(
        app, ["search", "--text", "--lang", "python", "password"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "auth.py" in result.output
    assert "config.yaml" not in result.output


def test_cli_limit(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(corpus)
    # "password" matches 5 files; --limit 2 caps the printed files to 2.
    result = runner.invoke(
        app, ["search", "--text", "--limit", "2", "password"], catch_exceptions=False
    )
    assert result.exit_code == 0
    candidates = ["src/auth.py", "src/db.py", "README.md", "config.yaml", "notes.txt"]
    assert sum(1 for f in candidates if f in result.output) == 2

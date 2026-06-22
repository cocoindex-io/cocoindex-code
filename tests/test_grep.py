"""Tests for `ccc grep` — structural code search.

These run entirely locally (no daemon, no index, no embeddings): the engine
compiles a code_match pattern per language and matches files on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code import grep as g
from cocoindex_code.cli import app

runner = CliRunner()


def run_grep_obj(req: g.GrepRequest) -> tuple[list[g.FileMatches | g.GrepWarning], g.Grep]:
    """Collect a Grep run's emitted items (results + warnings, completion order) and
    return them alongside the finished Grep, so tests can inspect the verdict."""
    grep_run = g.Grep(req)
    items: list[g.FileMatches | g.GrepWarning] = []
    grep_run.run(items.append)
    return items, grep_run


def collect_grep(req: g.GrepRequest) -> list[g.FileMatches | g.GrepWarning]:
    """Drain a Grep run into a list (compile warnings + match results + read
    warnings), completion order."""
    return run_grep_obj(req)[0]


def run_grep(req: g.GrepRequest) -> list[g.FileMatches]:
    """Just the file matches (dropping warnings), sorted by path for deterministic
    assertions (the engine itself yields in completion order)."""
    files = [it for it in collect_grep(req) if isinstance(it, g.FileMatches)]
    files.sort(key=lambda fm: fm.path)
    return files


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def codebase(tmp_path: Path) -> Path:
    """A small multi-language tree (no cocoindex project marker)."""
    (tmp_path / "a.py").write_text(
        "import os\n\n\ndef foo(a, b):\n    return a + b\n\n\ndef bar(x):\n    return foo(x, 1)\n"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def baz(y):\n    return y * 2\n")
    (tmp_path / "c.rs").write_text('fn main() {\n    println!("hi");\n}\n')
    # A .txt file that contains python-looking text but is not code.
    (tmp_path / "notes.txt").write_text("def foo(not real code):\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Engine unit tests
# ---------------------------------------------------------------------------


def test_is_match_supported() -> None:
    assert g._is_match_supported("python") is True
    assert g._is_match_supported("rust") is True
    # Detected-but-not-structurally-matchable languages.
    assert g._is_match_supported("text") is False
    assert g._is_match_supported("markdown") is False


def test_pattern_compiler_caches_per_language() -> None:
    compiler = g._PatternCompiler(r"def \NAME(\(A*\)):")
    first = compiler.for_language("python")
    assert first is not None
    # Same object reused (the "compiled pattern map" from the requirement).
    assert compiler.for_language("python") is first


def test_pattern_compiler_skips_unsupported_language() -> None:
    compiler = g._PatternCompiler(r"def \NAME(\(A*\)):")
    assert compiler.for_language("text") is None


def test_pattern_compiler_warns_on_malformed_pattern() -> None:
    compiler = g._PatternCompiler(r"def \NAME \{{ return")  # unbalanced containment
    # A supported language whose pattern won't compile is skipped with a warning,
    # not raised — so one bad pattern doesn't abort the whole multi-language grep.
    assert compiler.for_language("python") is None
    assert len(compiler.warnings) == 1
    assert compiler.warnings[0].language == "python"
    assert "python" in compiler.warnings[0].message
    # Re-asking the same language doesn't duplicate the warning (cached).
    assert compiler.for_language("python") is None
    assert len(compiler.warnings) == 1


def test_grep_finds_across_files(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase)
    results = run_grep(req)
    paths = {fm.path for fm in results}
    # Both python files matched; rust and txt skipped.
    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("b.py") for p in paths)
    assert not any(p.endswith(".rs") for p in paths)
    assert not any(p.endswith(".txt") for p in paths)


def test_grep_run_emits_each_result(codebase: Path) -> None:
    # Grep.run calls `emit` once per result as each match finishes (no batching).
    grep_run = g.Grep(g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase))
    emitted: list[g.FileMatches | g.GrepWarning] = []
    grep_run.run(emitted.append)
    assert len(emitted) == 2  # a.py and sub/b.py
    assert all(isinstance(it, g.FileMatches) for it in emitted)  # valid pattern → no warnings
    assert not grep_run.unusable
    assert not grep_run.unusable  # valid pattern compiled


def test_grep_run_handles_many_files(tmp_path: Path) -> None:
    # Many files exercise the WaitGroup counter / completion sentinel — every file
    # must be matched exactly once, with no duplicates and no lost results.
    n = 250
    for i in range(n):
        (tmp_path / f"f{i:04d}.py").write_text(f"def fn{i}(a):\n    return a\n")
    results = run_grep(g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path))
    assert len(results) == n
    assert len({fm.path for fm in results}) == n  # no duplicates


def test_grep_language_filter(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"\NAME(\(A*\))", root=codebase, languages=frozenset({"rust"}))
    results = run_grep(req)
    assert {fm.path for fm in results} and all(fm.path.endswith(".rs") for fm in results)


def test_grep_single_file(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase / "a.py")
    results = run_grep(req)
    assert len(results) == 1
    assert results[0].path == (codebase / "a.py").as_posix()
    # a.py defines foo and bar.
    assert len(results[0].matches) == 2


def test_grep_path_glob(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase, path_glob="sub/**")
    results = run_grep(req)
    assert {fm.path for fm in results} == {(codebase / "sub" / "b.py").as_posix()}


def test_grep_no_matches(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"nonexistent_fn(\(A*\))", root=codebase)
    assert run_grep(req) == []


def test_grep_binary_file_skipped(tmp_path: Path) -> None:
    (tmp_path / "data.py").write_bytes(b"\xff\xfe\x00\x01 def foo(): pass")
    req = g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path)
    # Non-UTF-8 content is skipped silently (no warning), rather than crashing.
    assert collect_grep(req) == []


def test_grep_warns_once_per_supported_language_and_is_unusable(codebase: Path) -> None:
    # The fixture has two python files and one rust file. A malformed pattern warns
    # once per *supported* language (python, rust) — not once per file, not for the
    # unsupported .txt — leaves nothing to match, and is reported unusable.
    items, grep_run = run_grep_obj(g.GrepRequest(pattern=r"def \NAME \{{ x", root=codebase))
    warnings = [it for it in items if isinstance(it, g.GrepWarning)]
    assert not any(isinstance(it, g.FileMatches) for it in items)
    assert len(warnings) == 2
    assert set(grep_run.failed_languages) == {"python", "rust"}
    assert grep_run.unusable is True


def test_grep_unusable_distinct_from_no_matchable_files(tmp_path: Path) -> None:
    # A valid pattern that simply finds nothing is NOT "unusable".
    (tmp_path / "a.py").write_text("x = 1\n")
    _, ok = run_grep_obj(g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path))
    assert ok.failed_languages == []
    assert ok.unusable is False

    # A malformed pattern but only *unsupported* files (the pattern is never even
    # compiled) → not unusable, just "no matchable files found".
    onlytxt = tmp_path / "txtonly"
    onlytxt.mkdir()
    (onlytxt / "d.txt").write_text("hello\n")
    _, none_tried = run_grep_obj(g.GrepRequest(pattern=r"def \NAME \{{ x", root=onlytxt))
    assert none_tried.failed_languages == []
    assert none_tried.unusable is False


def test_match_file_unreadable_returns_warning(tmp_path: Path) -> None:
    # Reading a directory raises IsADirectoryError (an OSError) → surfaced as a
    # warning, not a silent skip and not a crash.
    cp = g.CodePattern("x", language="python")
    target = g._Target(path=tmp_path, display="adir", pattern=cp)
    result = g._match_file(target)
    assert isinstance(result, g.GrepWarning)
    assert "cannot read adir" in result.message


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_plain_format(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase / "sub" / "b.py")
    rendered = g.render_results(run_grep(req), color=False)
    lines = rendered.split("\n")
    assert lines[0] == (codebase / "sub" / "b.py").as_posix()  # path header
    # Gutter is "<line>| " — number, pipe, then exactly one space before the code.
    assert lines[1] == "1| def baz(y):"


def test_render_strips_crlf_carriage_returns(tmp_path: Path) -> None:
    # CRLF files must not leak a trailing "\r" into rendered code lines (regression:
    # source.split("\n") left it on every line). newline="" writes "\r\n" verbatim
    # instead of letting the platform translate it.
    (tmp_path / "crlf.py").write_text("def baz(y):\r\n    pass\r\n", newline="")
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=tmp_path / "crlf.py")
    rendered = g.render_results(run_grep(req), color=False)
    assert "\r" not in rendered
    assert "1| def baz(y):" in rendered


def test_render_separator_between_matches(codebase: Path) -> None:
    req = g.GrepRequest(pattern=r"def \NAME(\(ARGS*\)):", root=codebase / "a.py")
    rendered = g.render_results(run_grep(req), color=False)
    assert "\n---\n" in rendered  # two matches in one file


def test_render_line_number_width(tmp_path: Path) -> None:
    # A list literal spanning to a 2-digit line: the gutter is right-aligned to
    # width 2 (single-digit lines space-padded), with one space after the pipe.
    body = "".join(f"    {i},\n" for i in range(10))
    (tmp_path / "wide.py").write_text(f"data = [\n{body}]\n")  # `[` on line 1, `]` on line 12
    req = g.GrepRequest(pattern=r"[\(ITEMS*\)]", root=tmp_path / "wide.py")
    rendered = g.render_results(run_grep(req), color=False)
    assert "\n 1| data = [" in rendered  # single-digit line, padded to width 2
    assert "\n12| ]" in rendered  # two-digit line


def test_render_color_dims_unmatched_prefix(codebase: Path) -> None:
    # `foo(x, 1)` on the last line of bar — the leading "    return " is dimmed.
    req = g.GrepRequest(pattern=r"foo(\(A*\))", root=codebase / "a.py")
    rendered = g.render_results(run_grep(req), color=True)
    assert "\x1b[" in rendered  # ANSI present
    assert "\x1b[2m" in rendered  # dim attribute for unmatched context


# ---------------------------------------------------------------------------
# CLI end-to-end (via CliRunner — no daemon needed)
# ---------------------------------------------------------------------------


def test_cli_grep_basic(codebase: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(codebase)
    result = runner.invoke(app, ["grep", r"def \NAME(\(ARGS*\)):"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "a.py" in result.output
    assert "sub/b.py" in result.output
    assert "def foo(a, b):" in result.output


def test_cli_grep_explicit_path(codebase: Path) -> None:
    result = runner.invoke(
        app, ["grep", r"def \NAME(\(ARGS*\)):", str(codebase)], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "a.py" in result.output


def test_cli_grep_no_matches(codebase: Path) -> None:
    result = runner.invoke(app, ["grep", r"nope_fn(\(A*\))", str(codebase)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No matches found." in result.output


def test_cli_grep_malformed_pattern(codebase: Path) -> None:
    result = runner.invoke(
        app, ["grep", r"def \NAME \{{ return", str(codebase)], catch_exceptions=False
    )
    # Malformed for every language found (python + rust): per-language warnings
    # plus an explicit error, and a non-zero exit.
    assert result.exit_code == 1
    assert "pattern invalid for python" in result.output  # per-language warning
    assert "did not compile for any of the languages found" in result.output  # error


def test_cli_grep_path_not_found() -> None:
    result = runner.invoke(
        app, ["grep", r"foo(\(A*\))", "/no/such/path/xyz"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "path not found" in result.output


def test_cli_grep_lang_filter(codebase: Path) -> None:
    result = runner.invoke(
        app, ["grep", r"\NAME(\(A*\))", str(codebase), "--lang", "rust"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert ".rs" in result.output
    assert ".py" not in result.output


# ---------------------------------------------------------------------------
# Project- and gitignore-awareness
# ---------------------------------------------------------------------------


def test_grep_respects_project_exclude_patterns(tmp_path: Path) -> None:
    """Inside an initialized project, grep honors the configured exclude patterns."""
    (tmp_path / ".cocoindex_code").mkdir()
    (tmp_path / ".cocoindex_code" / "settings.yml").write_text(
        "include_patterns:\n  - '**/*.py'\nexclude_patterns:\n  - '**/.*'\n  - '**/skip'\n"
    )
    (tmp_path / "keep.py").write_text("def kept(a):\n    return a\n")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "hidden.py").write_text("def hidden(a):\n    return a\n")

    req = g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path)
    results = run_grep(req)
    paths = {fm.path for fm in results}
    assert any(p.endswith("keep.py") for p in paths)
    assert not any("skip" in p for p in paths)


def test_grep_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".cocoindex_code").mkdir()
    (tmp_path / ".cocoindex_code" / "settings.yml").write_text("include_patterns:\n  - '**/*.py'\n")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "kept.py").write_text("def kept(a):\n    return a\n")
    (tmp_path / "ignored.py").write_text("def ignored(a):\n    return a\n")

    req = g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path)
    paths = {fm.path for fm in run_grep(req)}
    assert any(p.endswith("kept.py") for p in paths)
    assert not any(p.endswith("ignored.py") for p in paths)


def test_find_git_root(tmp_path: Path) -> None:
    from cocoindex_code.file_walk import find_git_root

    # normal repo: .git is a directory
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_git_root(sub) == tmp_path
    # outside any repo
    assert find_git_root(Path(tmp_path.anchor)) is None

    # submodule / linked worktree: .git is a *file*
    other = tmp_path / "other"
    (other / "x").mkdir(parents=True)
    (other / ".git").write_text("gitdir: /elsewhere/.git/modules/other\n")
    assert find_git_root(other / "x") == other


def test_grep_anchors_gitignore_at_git_root_when_no_project(tmp_path: Path) -> None:
    # No cocoindex project, but a git repo with a root .gitignore. Grepping a deep
    # subfolder must still honor that repo-root .gitignore (anchored at the git root,
    # not the subfolder).
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    sub = tmp_path / "src" / "sub"
    sub.mkdir(parents=True)
    (sub / "keep.py").write_text("def kept(a):\n    return a\n")
    (sub / "ignored.py").write_text("def gone(a):\n    return a\n")

    paths = {
        Path(fm.path).name
        for fm in run_grep(g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=sub))
    }
    assert "keep.py" in paths
    assert "ignored.py" not in paths  # ignored by the git-root .gitignore, not just sub/


def test_grep_language_override(tmp_path: Path) -> None:
    """A project language override maps an unusual extension to a matchable language."""
    (tmp_path / ".cocoindex_code").mkdir()
    (tmp_path / ".cocoindex_code" / "settings.yml").write_text(
        "include_patterns:\n  - '**/*.inc'\nlanguage_overrides:\n  - ext: inc\n    lang: python\n"
    )
    (tmp_path / "snippet.inc").write_text("def included(a):\n    return a\n")

    req = g.GrepRequest(pattern=r"def \NAME(\(A*\)):", root=tmp_path)
    results = run_grep(req)
    assert len(results) == 1 and results[0].path.endswith("snippet.inc")

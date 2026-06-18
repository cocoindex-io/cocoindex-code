"""Unit tests for .gitignore line normalization in the indexer."""

from __future__ import annotations

from pathlib import PurePath

from pathspec import GitIgnoreSpec

from cocoindex_code.indexer import _normalize_gitignore_lines

ROOT = PurePath(".")


def test_plain_pattern_is_globbed() -> None:
    assert _normalize_gitignore_lines(["build"], ROOT) == ["**/build"]


def test_negation_is_preserved() -> None:
    assert _normalize_gitignore_lines(["build", "!build/keep.txt"], ROOT) == [
        "**/build",
        "!build/keep.txt",
    ]


def test_escaped_hash_is_literal_not_comment() -> None:
    # "\#notacomment" -> a file literally named "#notacomment".
    assert _normalize_gitignore_lines(["\\#notacomment"], ROOT) == ["**/#notacomment"]


def test_escaped_bang_is_literal_not_negation() -> None:
    # Regression: "\!important" means "ignore a file literally named '!important'",
    # NOT a negation, so it must not become a "!"-prefixed (negation) pattern.
    assert _normalize_gitignore_lines(["\\!important"], ROOT) == ["**/!important"]


def test_escaped_bang_does_not_re_include_unrelated_matches() -> None:
    # End-to-end: a "\!important" line must not cancel an unrelated "important"
    # ignore rule. Before the fix it normalized to "!**/important", which
    # re-included every "important" file the previous line had ignored.
    spec = GitIgnoreSpec.from_lines(
        _normalize_gitignore_lines(["important", "\\!important"], ROOT)
    )
    assert spec.match_file("important") is True  # still ignored
    assert spec.match_file("!important") is True  # literal file ignored too


def test_subdirectory_prefix_is_applied() -> None:
    assert _normalize_gitignore_lines(["\\!keep"], PurePath("sub/dir")) == [
        "sub/dir/**/!keep"
    ]

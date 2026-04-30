"""Smoke tests for lance_indexer — no network, no cocoindex env required."""
import pytest

from cocoindex_code.lance_indexer import (
    EXT_TO_LANGUAGE,
    LANGUAGE_CONFIG,
    UNIVERSAL_EXCLUDES,
    LangGroup,
    _ext_from_pattern,
    _mode_b_groups,
)


def test_ext_from_pattern_simple():
    assert _ext_from_pattern("**/*.ts") == "ts"
    assert _ext_from_pattern("repo/**/*.py") == "py"
    assert _ext_from_pattern("frontend/**/*.tsx") == "tsx"


def test_ext_from_pattern_no_match():
    assert _ext_from_pattern("**/*") is None
    assert _ext_from_pattern("src/") is None
    # multi-extension like *.generated.json: suffix is .json, not .generated.json
    assert _ext_from_pattern("**/*.generated.json") == "json"


def test_ext_to_language_no_ipynb():
    # ipynb is JSON — must never map to python or any text language
    assert "ipynb" not in EXT_TO_LANGUAGE


def test_language_config_keys_have_table_and_extensions():
    for lang, preset in LANGUAGE_CONFIG.items():
        assert "extensions" in preset, f"{lang}: missing extensions"
        assert "table" in preset, f"{lang}: missing table"
        assert preset["extensions"], f"{lang}: empty extensions list"
        assert preset["table"].endswith("_index"), f"{lang}: table should end with _index"


def test_universal_excludes_covers_critical_paths():
    must_exclude = [
        "**/.cocoindex_code/**",
        "**/.git/**",
        "**/node_modules/**",
        "**/.venv/**",
        "**/*.wasm",
        "**/*_pb2.py",
        "**/worktrees/**",
    ]
    for pattern in must_exclude:
        assert pattern in UNIVERSAL_EXCLUDES, f"missing critical exclude: {pattern}"


def test_mode_b_groups_basic():
    merged = {
        "include_patterns": ["repo/**/*.ts", "repo/**/*.py", "repo/**/*.rs"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    langs = {g.language for g in groups}
    assert "typescript" in langs
    assert "python" in langs
    assert "rust" in langs


def test_mode_b_groups_language_override():
    merged = {
        "include_patterns": ["repo/**/*.mq5", "repo/**/*.mqh"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {"mq5": "c", "mqh": "c"})
    assert len(groups) == 1
    assert groups[0].language == "c"
    assert groups[0].table == "c_index"


def test_mode_b_groups_deduplicates_excludes():
    merged = {
        "include_patterns": ["repo/**/*.py"],
        "exclude_patterns": ["**/node_modules/**"],  # already in UNIVERSAL_EXCLUDES
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    assert len(groups) == 1
    excludes = groups[0].excluded_patterns
    # deduplication: node_modules appears exactly once
    assert excludes.count("**/node_modules/**") == 1


def test_lang_group_is_frozen():
    g = LangGroup(
        language="python",
        table="python_index",
        included_patterns=("**/*.py",),
        excluded_patterns=("**/.*",),
    )
    with pytest.raises((AttributeError, TypeError)):
        g.language = "rust"  # type: ignore[misc]


def test_mode_b_groups_unknown_ext_skipped(capsys):
    merged = {
        "include_patterns": ["repo/**/*.xyz123"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    assert groups == []
    captured = capsys.readouterr()
    assert "skipped" in captured.err


def test_mode_b_groups_empty_source():
    merged = {
        "include_patterns": [],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    assert groups == []


def test_mode_b_groups_sorted_by_language():
    merged = {
        "include_patterns": ["r/**/*.ts", "r/**/*.py", "r/**/*.go", "r/**/*.rs"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    langs = [g.language for g in groups]
    assert langs == sorted(langs)


@pytest.mark.parametrize("lang,expected_table", [
    ("swift", "swift_index"),
    ("python", "python_index"),
    ("go", "go_index"),
    ("rust", "rust_index"),
    ("javascript", "typescript_index"),
])
def test_language_config_table_names(lang: str, expected_table: str):
    assert LANGUAGE_CONFIG[lang]["table"] == expected_table


def test_mode_b_groups_patterns_are_tuples():
    merged = {
        "include_patterns": ["repo/**/*.py"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    assert isinstance(groups[0].included_patterns, tuple)
    assert isinstance(groups[0].excluded_patterns, tuple)


def test_coco_lance_entry_point():
    """Entry point module and function must be importable."""
    from cocoindex_code.lance_indexer import main  # noqa: F401
    assert callable(main)


def test_mode_b_groups_include_universal_excludes():
    merged = {
        "include_patterns": ["repo/**/*.py"],
        "exclude_patterns": ["**/custom_exclude/**"],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    assert len(groups) == 1
    excludes = set(groups[0].excluded_patterns)
    assert "**/node_modules/**" in excludes
    assert "**/custom_exclude/**" in excludes
    assert "**/.cocoindex_code/**" in excludes


def test_mode_b_groups_path_prefix_preserved():
    merged = {
        "include_patterns": ["frontend/**/*.ts", "cocoindex/**/*.py"],
        "exclude_patterns": [],
        "language_overrides": [],
        "chunkers": [],
    }
    groups = _mode_b_groups(merged, {})
    ts_group = next(g for g in groups if g.language == "typescript")
    py_group = next(g for g in groups if g.language == "python")
    assert "frontend/**/*.ts" in ts_group.included_patterns
    assert "cocoindex/**/*.py" in py_group.included_patterns

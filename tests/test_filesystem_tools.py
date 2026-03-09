"""Tests for filesystem tools: find_files, read_file, write_file, grep_code, directory_tree."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.filesystem_tools import (
    _detect_lang,
    _directory_tree,
    _edit_file,
    _grep_files,
    _is_binary,
    _is_excluded_dir,
    _read_file,
    _safe_resolve,
    _walk_files,
    _write_file,
)


@pytest.fixture()
def sample_codebase(tmp_path: Path) -> Path:
    """Create a sample codebase for testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "__pycache__").mkdir()

    (tmp_path / "main.py").write_text(
        'def hello():\n    """Say hello."""\n    print("Hello, world!")\n'
    )
    (tmp_path / "src" / "app.ts").write_text(
        "export function greet(name: string): string {\n"
        "  return `Hello, ${name}!`;\n"
        "}\n"
        "\n"
        "// TODO: add farewell function\n"
        "export function farewell(name: string): string {\n"
        "  return `Goodbye, ${name}!`;\n"
        "}\n"
    )
    (tmp_path / "src" / "utils" / "math.ts").write_text(
        "export const add = (a: number, b: number): number => a + b;\n"
        "export const subtract = (a: number, b: number): number => a - b;\n"
    )
    (tmp_path / "lib" / "database.py").write_text(
        "class DatabaseConnection:\n"
        '    """Database connection manager."""\n'
        "\n"
        "    def connect(self) -> None:\n"
        '        """Establish connection."""\n'
        "        pass\n"
    )
    (tmp_path / "README.md").write_text("# Test Project\n\nA test project.\n")

    (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {};\n")
    (tmp_path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00" * 100)

    binary_path = tmp_path / "image.png"
    binary_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00" + b"\x00" * 50)

    return tmp_path


@pytest.fixture(autouse=True)
def _patch_config(sample_codebase: Path) -> Iterator[None]:
    """Patch filesystem_tools config to point at sample_codebase."""
    with patch("cocoindex_code.filesystem_tools.config") as mock_config:
        mock_config.codebase_root_path = sample_codebase
        yield


class TestIsExcludedDir:
    """Tests for _is_excluded_dir."""

    def test_hidden_dirs_excluded(self) -> None:
        assert _is_excluded_dir(".git") is True
        assert _is_excluded_dir(".vscode") is True

    def test_known_excluded_dirs(self) -> None:
        assert _is_excluded_dir("node_modules") is True
        assert _is_excluded_dir("__pycache__") is True
        assert _is_excluded_dir(".cocoindex_code") is True

    def test_pattern_excluded_dirs(self) -> None:
        assert _is_excluded_dir("target") is True
        assert _is_excluded_dir("build") is True
        assert _is_excluded_dir("dist") is True
        assert _is_excluded_dir("vendor") is True

    def test_normal_dirs_not_excluded(self) -> None:
        assert _is_excluded_dir("src") is False
        assert _is_excluded_dir("lib") is False
        assert _is_excluded_dir("tests") is False


class TestIsBinary:
    """Tests for _is_binary."""

    def test_text_file_not_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("Hello, world!")
        assert _is_binary(f) is False

    def test_binary_file_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert _is_binary(f) is True

    def test_nonexistent_file_returns_true(self, tmp_path: Path) -> None:
        assert _is_binary(tmp_path / "nonexistent") is True


class TestDetectLang:
    """Tests for _detect_lang."""

    def test_python(self, tmp_path: Path) -> None:
        assert _detect_lang(tmp_path / "test.py") == "python"
        assert _detect_lang(tmp_path / "test.pyi") == "python"

    def test_typescript(self, tmp_path: Path) -> None:
        assert _detect_lang(tmp_path / "test.ts") == "typescript"
        assert _detect_lang(tmp_path / "test.tsx") == "typescript"

    def test_javascript(self, tmp_path: Path) -> None:
        assert _detect_lang(tmp_path / "test.js") == "javascript"

    def test_unknown_extension(self, tmp_path: Path) -> None:
        assert _detect_lang(tmp_path / "test.xyz") == ""


class TestSafeResolve:
    """Tests for _safe_resolve path traversal protection."""

    def test_normal_path(self, sample_codebase: Path) -> None:
        resolved = _safe_resolve("src/app.ts")
        assert resolved == sample_codebase / "src" / "app.ts"

    def test_traversal_blocked(self, sample_codebase: Path) -> None:
        with pytest.raises(ValueError, match="escapes the codebase root"):
            _safe_resolve("../../etc/passwd")


class TestWalkFiles:
    """Tests for _walk_files."""

    def test_find_all_files(self, sample_codebase: Path) -> None:
        files, total, truncated = _walk_files(sample_codebase)
        assert total > 0
        assert not truncated
        paths = {f.path for f in files}
        assert "main.py" in paths
        assert "src/app.ts" in paths
        assert "README.md" in paths

    def test_excludes_node_modules(self, sample_codebase: Path) -> None:
        files, _, _ = _walk_files(sample_codebase)
        paths = {f.path for f in files}
        assert not any("node_modules" in p for p in paths)

    def test_excludes_pycache(self, sample_codebase: Path) -> None:
        files, _, _ = _walk_files(sample_codebase)
        paths = {f.path for f in files}
        assert not any("__pycache__" in p for p in paths)

    def test_pattern_filter(self, sample_codebase: Path) -> None:
        files, total, _ = _walk_files(sample_codebase, pattern="*.py")
        assert total == 2
        assert all(f.path.endswith(".py") for f in files)

    def test_language_filter(self, sample_codebase: Path) -> None:
        files, total, _ = _walk_files(sample_codebase, languages=["typescript"])
        assert total == 2
        assert all(f.language == "typescript" for f in files)

    def test_paths_filter(self, sample_codebase: Path) -> None:
        files, total, _ = _walk_files(sample_codebase, paths=["src/*"])
        assert total > 0
        assert all(f.path.startswith("src/") for f in files)

    def test_limit_truncates(self, sample_codebase: Path) -> None:
        files, total, truncated = _walk_files(sample_codebase, limit=1)
        assert len(files) == 1
        assert total > 1
        assert truncated is True

    def test_file_size_populated(self, sample_codebase: Path) -> None:
        files, _, _ = _walk_files(sample_codebase, pattern="main.py")
        assert len(files) == 1
        assert files[0].size > 0


class TestReadFile:
    """Tests for _read_file."""

    def test_read_entire_file(self, sample_codebase: Path) -> None:
        content, s, e, total = _read_file(sample_codebase / "main.py")
        assert s == 1
        assert e == total
        assert "def hello" in content

    def test_read_line_range(self, sample_codebase: Path) -> None:
        content, s, e, total = _read_file(sample_codebase / "main.py", start_line=1, end_line=1)
        assert s == 1
        assert e == 1
        assert "def hello" in content
        assert "print" not in content

    def test_start_line_clamped(self, sample_codebase: Path) -> None:
        content, s, _, _ = _read_file(sample_codebase / "main.py", start_line=0)
        assert s == 1

    def test_end_line_clamped(self, sample_codebase: Path) -> None:
        _, _, e, total = _read_file(sample_codebase / "main.py", end_line=9999)
        assert e == total


class TestGrepFiles:
    """Tests for _grep_files."""

    def test_basic_grep(self, sample_codebase: Path) -> None:
        matches, total, searched, truncated = _grep_files(sample_codebase, "def hello")
        assert total == 1
        assert matches[0].path == "main.py"
        assert matches[0].line_number == 1
        assert not truncated

    def test_grep_regex(self, sample_codebase: Path) -> None:
        matches, total, _, _ = _grep_files(sample_codebase, r"TODO|FIXME")
        assert total >= 1
        assert any("TODO" in m.line for m in matches)

    def test_grep_case_insensitive(self, sample_codebase: Path) -> None:
        matches, total, _, _ = _grep_files(sample_codebase, "hello", case_sensitive=False)
        assert total >= 1

    def test_grep_include_filter(self, sample_codebase: Path) -> None:
        matches, total, _, _ = _grep_files(sample_codebase, "export", include="*.ts")
        assert total >= 1
        assert all(m.path.endswith(".ts") for m in matches)

    def test_grep_paths_filter(self, sample_codebase: Path) -> None:
        matches, total, _, _ = _grep_files(sample_codebase, "export", paths=["src/utils/*"])
        assert total >= 1
        assert all(m.path.startswith("src/utils/") for m in matches)

    def test_grep_context_lines(self, sample_codebase: Path) -> None:
        matches, _, _, _ = _grep_files(sample_codebase, "TODO", context_lines=1)
        assert len(matches) >= 1
        assert len(matches[0].context_after) > 0 or len(matches[0].context_before) > 0

    def test_grep_limit(self, sample_codebase: Path) -> None:
        matches, total, _, truncated = _grep_files(sample_codebase, "export", limit=1)
        assert len(matches) == 1
        if total > 1:
            assert truncated is True

    def test_grep_invalid_regex(self, sample_codebase: Path) -> None:
        with pytest.raises(ValueError, match="Invalid regex"):
            _grep_files(sample_codebase, "[invalid")

    def test_grep_skips_binary(self, sample_codebase: Path) -> None:
        matches, _, _, _ = _grep_files(sample_codebase, "PNG")
        paths = {m.path for m in matches}
        assert "image.png" not in paths

    def test_grep_skips_excluded_dirs(self, sample_codebase: Path) -> None:
        matches, _, _, _ = _grep_files(sample_codebase, "module.exports")
        paths = {m.path for m in matches}
        assert not any("node_modules" in p for p in paths)


class TestDirectoryTree:
    """Tests for _directory_tree."""

    def test_basic_tree(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase)
        paths = {e.path for e in entries}
        types = {e.path: e.type for e in entries}
        assert "src" in paths
        assert types["src"] == "dir"
        assert "main.py" in paths
        assert types["main.py"] == "file"

    def test_excludes_hidden_and_known_dirs(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase)
        paths = {e.path for e in entries}
        assert not any("node_modules" in p for p in paths)
        assert not any("__pycache__" in p for p in paths)

    def test_max_depth(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase, max_depth=1)
        dirs = [e for e in entries if e.type == "dir"]
        nested = [d for d in dirs if d.path.count(os.sep) > 1]
        assert len(nested) == 0

    def test_subdirectory(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase, rel_path="src")
        paths = {e.path for e in entries}
        assert any("app.ts" in p for p in paths)

    def test_file_sizes(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase)
        file_entries = [e for e in entries if e.type == "file"]
        assert all(e.size >= 0 for e in file_entries)
        main_py = next(e for e in file_entries if e.path == "main.py")
        assert main_py.size > 0

    def test_children_count(self, sample_codebase: Path) -> None:
        entries = _directory_tree(sample_codebase)
        src_entry = next(e for e in entries if e.path == "src")
        assert src_entry.children > 0


class TestWriteFile:
    """Tests for _write_file."""

    def test_create_new_file(self, sample_codebase: Path) -> None:
        path = sample_codebase / "new_file.txt"
        bytes_written, created = _write_file(path, "hello world")
        assert created is True
        assert bytes_written == 11
        assert path.read_text() == "hello world"

    def test_overwrite_existing_file(self, sample_codebase: Path) -> None:
        path = sample_codebase / "main.py"
        original = path.read_text()
        new_content = "# replaced\n"
        bytes_written, created = _write_file(path, new_content)
        assert created is False
        assert bytes_written == len(new_content.encode("utf-8"))
        assert path.read_text() == new_content
        assert path.read_text() != original

    def test_creates_parent_directories(self, sample_codebase: Path) -> None:
        path = sample_codebase / "deep" / "nested" / "dir" / "file.go"
        bytes_written, created = _write_file(path, "package main\n")
        assert created is True
        assert path.exists()
        assert path.read_text() == "package main\n"

    def test_unicode_content(self, sample_codebase: Path) -> None:
        path = sample_codebase / "unicode.txt"
        content = "Hello, mundo! Emoji: \u2764\ufe0f"
        bytes_written, created = _write_file(path, content)
        assert created is True
        assert path.read_text(encoding="utf-8") == content
        assert bytes_written == len(content.encode("utf-8"))

    def test_empty_content(self, sample_codebase: Path) -> None:
        path = sample_codebase / "empty.txt"
        bytes_written, created = _write_file(path, "")
        assert created is True
        assert bytes_written == 0
        assert path.read_text() == ""

    def test_multiline_content(self, sample_codebase: Path) -> None:
        path = sample_codebase / "multi.py"
        content = "def foo():\n    return 42\n\ndef bar():\n    return 0\n"
        bytes_written, created = _write_file(path, content)
        assert created is True
        assert path.read_text() == content

    def test_exceeds_max_size(self, sample_codebase: Path) -> None:
        path = sample_codebase / "huge.txt"
        content = "x" * 2_000_000
        with pytest.raises(ValueError, match="exceeds maximum write size"):
            _write_file(path, content)
        assert not path.exists()

    def test_path_traversal_blocked(self, sample_codebase: Path) -> None:
        with pytest.raises(ValueError, match="escapes the codebase root"):
            resolved = _safe_resolve("../../etc/evil.txt")
            _write_file(resolved, "malicious")

    def test_write_then_read_roundtrip(self, sample_codebase: Path) -> None:
        path = sample_codebase / "roundtrip.ts"
        content = "export const x: number = 42;\n"
        _write_file(path, content)
        read_content, s, e, total = _read_file(path)
        assert read_content == content
        assert s == 1
        assert e == total == 1


class TestEditFile:
    """Tests for _edit_file."""

    def test_single_replacement(self, sample_codebase: Path) -> None:
        path = sample_codebase / "main.py"
        original = path.read_text()
        assert "def hello" in original
        replacements = _edit_file(path, "def hello", "def greet")
        assert replacements == 1
        assert "def greet" in path.read_text()
        assert "def hello" not in path.read_text()

    def test_replace_all(self, sample_codebase: Path) -> None:
        path = sample_codebase / "replace_all.txt"
        path.write_text("aaa bbb aaa ccc aaa")
        replacements = _edit_file(path, "aaa", "xxx", replace_all=True)
        assert replacements == 3
        assert path.read_text() == "xxx bbb xxx ccc xxx"

    def test_ambiguous_match_without_replace_all(self, sample_codebase: Path) -> None:
        path = sample_codebase / "ambiguous.txt"
        path.write_text("foo bar foo baz foo")
        with pytest.raises(ValueError, match="Found 3 matches"):
            _edit_file(path, "foo", "qux")

    def test_old_string_not_found(self, sample_codebase: Path) -> None:
        path = sample_codebase / "main.py"
        with pytest.raises(ValueError, match="old_string not found"):
            _edit_file(path, "nonexistent_string_xyz", "replacement")

    def test_identical_strings_rejected(self, sample_codebase: Path) -> None:
        path = sample_codebase / "main.py"
        with pytest.raises(ValueError, match="identical"):
            _edit_file(path, "def hello", "def hello")

    def test_multiline_replacement(self, sample_codebase: Path) -> None:
        path = sample_codebase / "multi.py"
        path.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        replacements = _edit_file(
            path,
            "def foo():\n    return 1",
            "def foo(x: int):\n    return x + 1",
        )
        assert replacements == 1
        content = path.read_text()
        assert "def foo(x: int):" in content
        assert "return x + 1" in content
        assert "def bar():" in content

    def test_replacement_preserves_rest_of_file(self, sample_codebase: Path) -> None:
        path = sample_codebase / "src" / "app.ts"
        original = path.read_text()
        line_count_before = original.count("\n")
        _edit_file(path, "greet", "welcome")
        updated = path.read_text()
        assert "welcome" in updated
        assert "greet" not in updated
        assert updated.count("\n") == line_count_before

    def test_delete_by_replacing_with_empty(self, sample_codebase: Path) -> None:
        path = sample_codebase / "delete.txt"
        path.write_text("keep this\nremove this line\nkeep this too\n")
        _edit_file(path, "remove this line\n", "")
        assert path.read_text() == "keep this\nkeep this too\n"

    def test_insert_by_replacing_anchor(self, sample_codebase: Path) -> None:
        path = sample_codebase / "insert.py"
        path.write_text("import os\n\ndef main():\n    pass\n")
        _edit_file(path, "import os\n", "import os\nimport sys\n")
        content = path.read_text()
        assert "import os\nimport sys\n" in content

    def test_file_not_found(self, sample_codebase: Path) -> None:
        path = sample_codebase / "nope.txt"
        with pytest.raises(FileNotFoundError):
            _edit_file(path, "a", "b")

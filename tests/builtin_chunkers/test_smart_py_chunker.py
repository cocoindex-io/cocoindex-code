"""Tests for smart_py_chunker — top-level defs + multi-line decorator walk-back."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_py_chunker import python_chunker


def test_splits_top_level_def_and_class():
    source = (
        '"""Module docstring."""\n'
        "import os\n"
        "\n"
        "CONST = 1\n"
        "\n"
        "def foo():\n"
        "    return 1\n"
        "\n"
        "class Bar:\n"
        "    def method(self):\n"
        "        pass\n"
    )
    lang, chunks = python_chunker(Path("m.py"), source)
    assert lang == "python"
    assert any("import os" in c.text and "CONST" in c.text for c in chunks), \
        "header chunk must include imports + module constants"
    assert any("def foo" in c.text for c in chunks)
    assert any("class Bar" in c.text for c in chunks)


def test_multiline_decorator_attached_to_def():
    source = (
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass(\n"
        "    frozen=True,\n"
        "    slots=True,\n"
        ")\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n"
    )
    _, chunks = python_chunker(Path("m.py"), source)
    point = next(c for c in chunks if "class Point" in c.text)
    assert "@dataclass(" in point.text
    assert "frozen=True" in point.text
    assert "slots=True" in point.text


def test_decorator_with_leading_comment_attached():
    source = (
        "# ignore reason: monkey patch\n"
        "@staticmethod\n"
        "def helper():\n"
        "    return 1\n"
    )
    _, chunks = python_chunker(Path("m.py"), source)
    helper = next(c for c in chunks if "def helper" in c.text)
    assert "@staticmethod" in helper.text
    assert "# ignore reason" in helper.text


def test_empty_file_gets_fallback_chunk():
    lang, chunks = python_chunker(Path("m.py"), "")
    assert lang == "python"
    assert chunks, "even empty content yields a fallback chunk for indexing"


def test_oversized_class_gets_resplit():
    body = "\n".join(f"    attr_{i} = {i}" for i in range(400))
    source = f"class Big:\n{body}\n"
    _, chunks = python_chunker(Path("m.py"), source)
    # Oversized block must produce more than one chunk via recursive resplit.
    assert len(chunks) > 1

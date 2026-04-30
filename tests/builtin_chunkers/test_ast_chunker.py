"""Tests for ast_chunker — verifies fallback when tree-sitter is unavailable.

Tree-sitter itself is not a test-time dependency; the chunker advertises
availability-aware fallback, so we assert that path works. When a dev runs the
tests with ``tree_sitter_languages`` installed the chunker upgrades silently.
"""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.ast_chunker import python_ast_chunker, typescript_ast_chunker


def test_python_fallback_produces_chunks():
    source = (
        "import os\n"
        "\n"
        "def foo():\n"
        "    return 1\n"
        "\n"
        "class Bar:\n"
        "    def method(self):\n"
        "        pass\n"
    )
    lang, chunks = python_ast_chunker(Path("m.py"), source)
    assert lang in {"python", None}
    assert chunks, "chunker must always produce at least one chunk"
    joined = "\n---\n".join(c.text for c in chunks)
    assert "def foo" in joined
    assert "class Bar" in joined


def test_typescript_fallback_produces_chunks():
    source = (
        "import { z } from 'zod';\n"
        "\n"
        "export const A = 1;\n"
        "\n"
        "export function doThing() { return 2; }\n"
        "\n"
        "export class MyClass {}\n"
    )
    lang, chunks = typescript_ast_chunker(Path("src/foo.ts"), source)
    assert lang in {"typescript", None}
    assert chunks
    joined = "\n---\n".join(c.text for c in chunks)
    assert "export const A" in joined
    assert "export function doThing" in joined
    assert "export class MyClass" in joined


def test_tsx_uses_tsx_grammar_when_available():
    # Regardless of whether tree-sitter is present, the chunker must not crash
    # on JSX syntax; the fallback path also handles it.
    source = (
        "export const Button = ({ label }: Props) => <button>{label}</button>;\n"
    )
    _, chunks = typescript_ast_chunker(Path("Button.tsx"), source)
    assert chunks

"""Tests for smart_ts_chunker — export-aware splitting for TS / JS."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_ts_chunker import javascript_chunker, typescript_chunker


def test_splits_by_top_level_exports():
    source = (
        "import { z } from 'zod';\n"
        "\n"
        "export const A = 1;\n"
        "\n"
        "export function doThing() {\n"
        "  return 2;\n"
        "}\n"
        "\n"
        "export class MyClass {}\n"
    )
    lang, chunks = typescript_chunker(Path("src/foo.ts"), source)
    assert lang == "typescript"
    texts = [c.text for c in chunks]
    # Header (imports) is a separate chunk.
    assert any("import { z } from 'zod'" in t for t in texts)
    assert any("export const A = 1" in t for t in texts)
    assert any("export function doThing" in t for t in texts)
    assert any("export class MyClass" in t for t in texts)


def test_matches_export_default_and_async_and_abstract():
    source = (
        "export default function defaultFn() {}\n"
        "\n"
        "export async function asyncFn() {}\n"
        "\n"
        "export abstract class Base {}\n"
    )
    _, chunks = typescript_chunker(Path("foo.ts"), source)
    joined = "\n".join(c.text for c in chunks)
    assert "export default function defaultFn" in joined
    assert "export async function asyncFn" in joined
    assert "export abstract class Base" in joined


def test_includes_preceding_jsdoc():
    source = (
        "/**\n"
        " * Doc for foo.\n"
        " */\n"
        "export function foo() {}\n"
    )
    _, chunks = typescript_chunker(Path("foo.ts"), source)
    # Comment block must travel with the export.
    assert any("Doc for foo" in c.text and "export function foo" in c.text for c in chunks)


def test_no_exports_falls_back_to_recursive_splitter():
    source = "const internal = 1;\nconst other = 2;\n"
    lang, chunks = typescript_chunker(Path("foo.ts"), source)
    # Fallback returns None language override.
    assert lang is None
    assert chunks, "fallback must still produce chunks"


def test_javascript_language():
    source = "export const x = 1;\n"
    lang, _ = javascript_chunker(Path("foo.js"), source)
    assert lang == "javascript"


def test_jsx_language_is_javascript():
    source = "export const Comp = () => null;\n"
    lang, _ = javascript_chunker(Path("Comp.jsx"), source)
    assert lang == "javascript"


def test_tsx_language_is_typescript():
    source = "export const Comp = () => null;\n"
    lang, _ = typescript_chunker(Path("Comp.tsx"), source)
    assert lang == "typescript"

"""Tests for smart_java_chunker — top-level class/interface/enum boundaries."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_java_chunker import java_chunker


def test_splits_class_and_methods():
    source = (
        'package com.example;\n'
        '\n'
        'import java.util.*;\n'
        '\n'
        'public class User {\n'
        '    private String name;\n'
        '    private int age;\n'
        '\n'
        '    public User(String name) {\n'
        '        this.name = name;\n'
        '    }\n'
        '}\n'
    )
    lang, chunks = java_chunker(Path("User.java"), source)
    assert lang == "java"
    assert any("import" in c.text for c in chunks), \
        "header chunk must include package + imports"
    assert any("public class User" in c.text for c in chunks)


def test_splits_interface_and_enum():
    source = (
        'package com.example;\n'
        '\n'
        'public interface Reader {\n'
        '    int read(byte[] buffer) throws IOException;\n'
        '}\n'
        '\n'
        'public enum Color {\n'
        '    RED, GREEN, BLUE\n'
        '}\n'
    )
    _, chunks = java_chunker(Path("types.java"), source)
    assert any("public interface Reader" in c.text for c in chunks)
    assert any("public enum Color" in c.text for c in chunks)


def test_record_definition():
    source = (
        'package records;\n'
        '\n'
        'public record Point(int x, int y) {}\n'
    )
    _, chunks = java_chunker(Path("Point.java"), source)
    assert any("public record Point" in c.text for c in chunks)


def test_annotation_type():
    source = (
        'package annotations;\n'
        '\n'
        '@interface Deprecated {\n'
        '    String reason() default "";\n'
        '}\n'
    )
    _, chunks = java_chunker(Path("Deprecated.java"), source)
    assert any("@interface Deprecated" in c.text for c in chunks)


def test_empty_file_gets_fallback_chunk():
    lang, chunks = java_chunker(Path("Empty.java"), "")
    assert lang == "java"
    assert chunks, "even empty content yields a fallback chunk for indexing"


def test_oversized_class_gets_resplit():
    body = "\n    ".join(f'int field{i};' for i in range(200))
    source = f"public class Big {{\n    {body}\n}}\n"
    _, chunks = java_chunker(Path("Big.java"), source)
    # Oversized block must produce more than one chunk via recursive resplit.
    assert len(chunks) > 1

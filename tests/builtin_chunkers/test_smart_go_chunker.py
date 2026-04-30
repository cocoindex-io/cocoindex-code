"""Tests for smart_go_chunker — top-level function/type boundaries."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_go_chunker import go_chunker


def test_splits_top_level_functions():
    source = (
        'package main\n'
        '\n'
        'import "fmt"\n'
        '\n'
        'func Foo() error {\n'
        '    return nil\n'
        '}\n'
        '\n'
        'func Bar() int {\n'
        '    return 42\n'
        '}\n'
    )
    lang, chunks = go_chunker(Path("main.go"), source)
    assert lang == "go"
    assert any("import" in c.text and "fmt" in c.text for c in chunks), \
        "header chunk must include package + imports"
    assert any("func Foo" in c.text for c in chunks)
    assert any("func Bar" in c.text for c in chunks)


def test_splits_struct_and_methods():
    source = (
        'package pkg\n'
        '\n'
        'type User struct {\n'
        '    Name string\n'
        '    Age int\n'
        '}\n'
        '\n'
        'func (u *User) String() string {\n'
        '    return u.Name\n'
        '}\n'
    )
    _, chunks = go_chunker(Path("user.go"), source)
    assert any("type User" in c.text for c in chunks)
    assert any("func (u *User)" in c.text for c in chunks)


def test_interface_definition():
    source = (
        'package interfaces\n'
        '\n'
        'type Reader interface {\n'
        '    Read(p []byte) (n int, err error)\n'
        '}\n'
    )
    _, chunks = go_chunker(Path("interfaces.go"), source)
    assert any("type Reader interface" in c.text for c in chunks)


def test_empty_file_gets_fallback_chunk():
    lang, chunks = go_chunker(Path("empty.go"), "")
    assert lang == "go"
    assert chunks, "even empty content yields a fallback chunk for indexing"


def test_oversized_function_gets_resplit():
    body = "\n    ".join(f'fmt.Println("{i}")' for i in range(200))
    source = f"package main\n\nfunc Big() {{\n    {body}\n}}\n"
    _, chunks = go_chunker(Path("main.go"), source)
    # Oversized block must produce more than one chunk via recursive resplit.
    assert len(chunks) > 1

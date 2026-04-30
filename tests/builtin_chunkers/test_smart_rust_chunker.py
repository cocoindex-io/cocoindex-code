"""Tests for smart_rust_chunker — top-level function/struct/impl boundaries."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_rust_chunker import rust_chunker


def test_splits_top_level_functions():
    source = (
        'pub fn foo() -> Result<()> {\n'
        '    Ok(())\n'
        '}\n'
        '\n'
        'fn bar() -> i32 {\n'
        '    42\n'
        '}\n'
    )
    lang, chunks = rust_chunker(Path("lib.rs"), source)
    assert lang == "rust"
    assert any("pub fn foo" in c.text for c in chunks)
    assert any("fn bar" in c.text for c in chunks)


def test_splits_struct_and_impl():
    source = (
        'pub struct User {\n'
        '    name: String,\n'
        '    age: u32,\n'
        '}\n'
        '\n'
        'impl User {\n'
        '    pub fn new(name: String) -> Self {\n'
        '        Self { name, age: 0 }\n'
        '    }\n'
        '}\n'
    )
    _, chunks = rust_chunker(Path("user.rs"), source)
    assert any("pub struct User" in c.text for c in chunks)
    assert any("impl User" in c.text for c in chunks)


def test_trait_definition():
    source = (
        'pub trait Reader {\n'
        '    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize>;\n'
        '}\n'
    )
    _, chunks = rust_chunker(Path("traits.rs"), source)
    assert any("pub trait Reader" in c.text for c in chunks)


def test_enum_definition():
    source = (
        '#[derive(Debug)]\n'
        'pub enum Color {\n'
        '    Red,\n'
        '    Green,\n'
        '    Blue,\n'
        '}\n'
    )
    _, chunks = rust_chunker(Path("color.rs"), source)
    assert any("pub enum Color" in c.text for c in chunks)


def test_empty_file_gets_fallback_chunk():
    lang, chunks = rust_chunker(Path("empty.rs"), "")
    assert lang == "rust"
    assert chunks, "even empty content yields a fallback chunk for indexing"


def test_oversized_struct_gets_resplit():
    body = "\n    ".join(f'field_{i}: i32,' for i in range(200))
    source = f"pub struct Big {{\n    {body}\n}}\n"
    _, chunks = rust_chunker(Path("big.rs"), source)
    # Oversized block must produce more than one chunk via recursive resplit.
    assert len(chunks) > 1

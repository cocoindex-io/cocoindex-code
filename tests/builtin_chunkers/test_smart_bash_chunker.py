"""Tests for smart_bash_chunker — top-level function boundaries."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_bash_chunker import bash_chunker


def test_splits_top_level_functions():
    source = (
        '#!/bin/bash\n'
        '\n'
        'foo() {\n'
        '    echo "foo"\n'
        '}\n'
        '\n'
        'bar() {\n'
        '    echo "bar"\n'
        '}\n'
    )
    lang, chunks = bash_chunker(Path("script.sh"), source)
    assert lang == "bash"
    assert any("#!/bin/bash" in c.text for c in chunks), \
        "header chunk must include shebang"
    assert any("foo()" in c.text for c in chunks)
    assert any("bar()" in c.text for c in chunks)


def test_shebang_and_variables():
    source = (
        '#!/bin/bash\n'
        '\n'
        'set -e\n'
        'export MYVAR=value\n'
        '\n'
        'main() {\n'
        '    echo "main"\n'
        '}\n'
    )
    _, chunks = bash_chunker(Path("main.sh"), source)
    assert any("#!/bin/bash" in c.text and "set -e" in c.text for c in chunks), \
        "header must include shebang and setup"
    assert any("main()" in c.text for c in chunks)


def test_empty_file_gets_fallback_chunk():
    lang, chunks = bash_chunker(Path("empty.sh"), "")
    assert lang == "bash"
    assert chunks, "even empty content yields a fallback chunk for indexing"


def test_oversized_function_gets_resplit():
    body = "\n    ".join(f'echo "line_{i}"' for i in range(200))
    source = f"big() {{\n    {body}\n}}\n"
    _, chunks = bash_chunker(Path("big.sh"), source)
    # Oversized block must produce more than one chunk via recursive resplit.
    assert len(chunks) > 1

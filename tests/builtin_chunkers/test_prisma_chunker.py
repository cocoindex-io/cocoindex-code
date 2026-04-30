"""Tests for prisma_chunker — one chunk per model/enum/generator block."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.prisma_chunker import prisma_chunker


def test_splits_by_model_and_enum():
    source = (
        "// Header comment\n"
        "generator client {\n"
        "  provider = \"prisma-client-js\"\n"
        "}\n"
        "\n"
        "datasource db {\n"
        "  provider = \"postgresql\"\n"
        "  url      = env(\"DATABASE_URL\")\n"
        "}\n"
        "\n"
        "/// Represents a trading signal\n"
        "model Signal {\n"
        "  id   String @id\n"
        "  side Side\n"
        "}\n"
        "\n"
        "enum Side {\n"
        "  BUY\n"
        "  SELL\n"
        "}\n"
    )
    lang, chunks = prisma_chunker(Path("schema.prisma"), source)
    assert lang == "prisma"
    joined = "\n---\n".join(c.text for c in chunks)
    assert "generator client" in joined
    assert "datasource db" in joined
    assert "model Signal" in joined
    assert "enum Side" in joined

    # Model chunk should carry its preceding /// doc comment.
    signal_chunk = next(c for c in chunks if "model Signal" in c.text)
    assert "Represents a trading signal" in signal_chunk.text


def test_empty_content_returns_no_chunks():
    _, chunks = prisma_chunker(Path("s.prisma"), "")
    assert chunks == []


def test_comment_only_file_falls_back_to_single_chunk():
    # Regression: previously zero chunks were emitted; now we keep a single chunk.
    source = "// just comments\n// nothing else\n"
    _, chunks = prisma_chunker(Path("s.prisma"), source)
    assert len(chunks) == 1
    assert "just comments" in chunks[0].text


def test_unterminated_block_is_still_captured():
    source = "model Broken {\n  id String @id\n"  # missing closing brace
    _, chunks = prisma_chunker(Path("s.prisma"), source)
    assert len(chunks) == 1
    assert "model Broken" in chunks[0].text

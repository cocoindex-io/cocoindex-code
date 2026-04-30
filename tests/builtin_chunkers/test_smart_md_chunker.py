"""Tests for smart_md_chunker — header splits + fenced-code-block protection."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_md_chunker import markdown_chunker


def _split(content: str, path: str = "docs/architecture/foo.md"):
    lang, chunks = markdown_chunker(Path(path), content)
    return lang, chunks


def test_splits_on_h2_and_h3():
    # Use sections >100 chars — tiny-chunk merging intentionally collapses short
    # sections to avoid embedding noise; we want to see the split boundaries.
    body_a = "Content A. " + ("alpha " * 30)
    body_b = "Content B. " + ("beta " * 30)
    body_sub = "Deeper content. " + ("gamma " * 30)
    content = (
        "# Title\n"
        "Intro paragraph.\n"
        "\n"
        f"## Section A\n{body_a}\n"
        "\n"
        f"### Subsection A1\n{body_sub}\n"
        "\n"
        f"## Section B\n{body_b}\n"
    )
    _, chunks = _split(content)
    joined = "\n---\n".join(c.text for c in chunks)
    # All three headers must appear; individual chunks may merge when short.
    assert "## Section A" in joined
    assert "### Subsection A1" in joined
    assert "## Section B" in joined
    # At minimum there should be more than one chunk once sections are long enough.
    assert len(chunks) >= 3


def test_does_not_split_inside_fenced_code_block():
    """A `##` comment inside a fenced block must not start a new chunk."""
    content = (
        "# Doc\n"
        "\n"
        "## Overview\n"
        "Text.\n"
        "\n"
        "```bash\n"
        "## not a header — this is a bash comment\n"
        "echo hi\n"
        "```\n"
        "\n"
        "## Real Next Section\n"
        "More text.\n"
    )
    _, chunks = _split(content)
    # The bash `## not a header` line must stay inside the Overview chunk.
    overview = next(c for c in chunks if "## Overview" in c.text)
    assert "## not a header" in overview.text
    # "Real Next Section" still triggers a split.
    assert any("## Real Next Section" in c.text for c in chunks)


def test_tilde_fence_also_protected():
    content = (
        "## A\n"
        "pre\n"
        "\n"
        "~~~python\n"
        "## still in code\n"
        "~~~\n"
        "\n"
        "## B\n"
        "post\n"
    )
    _, chunks = _split(content)
    a = next(c for c in chunks if c.text.startswith("## A"))
    assert "## still in code" in a.text


def test_domain_doc_sets_markdown_language():
    lang, _ = _split("## Only\ntext\n", path="docs/signal/xyz.md")
    assert lang == "markdown"


def test_non_domain_doc_returns_none_language():
    lang, chunks = _split("## Only\ntext\n", path="random/foo.md")
    assert lang is None
    assert chunks, "non-domain docs should still produce chunks"


def test_empty_content_returns_no_chunks():
    _, chunks = _split("")
    assert chunks == []

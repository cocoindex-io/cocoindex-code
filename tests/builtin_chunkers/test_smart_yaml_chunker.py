"""Tests for smart_yaml_chunker — root-section + nested mapping/list splits."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.builtin_chunkers.smart_yaml_chunker import yaml_chunker


def test_splits_root_sections():
    source = (
        "services:\n"
        "  api:\n"
        "    image: foo\n"
        "  worker:\n"
        "    image: bar\n"
        "networks:\n"
        "  default:\n"
        "    driver: bridge\n"
    )
    lang, chunks = yaml_chunker(Path("compose.yml"), source)
    assert lang == "yaml"
    labels = [c.text.splitlines()[0] for c in chunks]
    assert any("services.api" in label for label in labels)
    assert any("services.worker" in label for label in labels)
    assert any("networks.default" in label for label in labels)


def test_splits_named_list_steps():
    source = (
        "steps:\n"
        "  - name: build\n"
        "    image: alpine\n"
        "  - name: test\n"
        "    image: node\n"
    )
    _, chunks = yaml_chunker(Path("pipeline.yml"), source)
    labels = [c.text.splitlines()[0] for c in chunks]
    assert any("steps.build" in label for label in labels)
    assert any("steps.test" in label for label in labels)


def test_empty_yaml_falls_back():
    _, chunks = yaml_chunker(Path("empty.yml"), "")
    # Empty content → default splitter returns empty; that's acceptable.
    assert isinstance(chunks, list)


def test_non_mapping_root_uses_fallback():
    # Root is a plain list — no named sections — fallback to splitter still works.
    source = "- one\n- two\n- three\n"
    _, chunks = yaml_chunker(Path("list.yml"), source)
    assert chunks, "list-root YAML should still produce chunks"

"""Tests for workspace_chunker — real source line numbers."""

from __future__ import annotations

import json
from pathlib import Path

from cocoindex_code.builtin_chunkers.workspace_chunker import workspace_chunker


def _workspace_fixture() -> str:
    data = {
        "github": {"org": "muth-hq"},
        "infrastructure": {
            "redis": {"port": 6379},
            "postgres": {"port": 5432},
        },
        "repositories": [
            {"id": "alpha", "name": "alpha", "path": "apps/alpha"},
            {"id": "beta", "name": "beta", "path": "apps/beta"},
        ],
        "services": [
            {"id": "svc-a", "name": "svc-a", "port": 3000},
        ],
    }
    return json.dumps(data, indent=2)


def test_splits_workspace_by_section():
    content = _workspace_fixture()
    lang, chunks = workspace_chunker(Path("workspace.json"), content)
    assert lang == "json"
    labels = [c.text.splitlines()[0] for c in chunks]
    assert any("> github" in label for label in labels)
    assert any("> infrastructure.redis" in label for label in labels)
    assert any("> infrastructure.postgres" in label for label in labels)
    assert any("> repositories.alpha" in label for label in labels)
    assert any("> repositories.beta" in label for label in labels)
    assert any("> services.svc-a" in label for label in labels)


def test_line_numbers_reflect_source_positions():
    content = _workspace_fixture()
    _, chunks = workspace_chunker(Path("workspace.json"), content)
    # The repositories.alpha chunk should point somewhere inside the alpha
    # entry in the source (the `"id"` or `"name"` row — both equal "alpha"),
    # not the default line 1.
    alpha = next(c for c in chunks if "repositories.alpha" in c.text.splitlines()[0])
    lines = content.split("\n")
    id_line = next(i + 1 for i, line in enumerate(lines) if '"id": "alpha"' in line)
    name_line = next(i + 1 for i, line in enumerate(lines) if '"name": "alpha"' in line)
    assert alpha.start.line in {id_line, name_line}
    assert alpha.end.line >= alpha.start.line
    # And different repos get different start lines.
    beta = next(c for c in chunks if "repositories.beta" in c.text.splitlines()[0])
    assert beta.start.line > alpha.start.line


def test_non_workspace_json_uses_fallback():
    _, chunks = workspace_chunker(Path("other.json"), '{"x": 1}')
    assert chunks, "non-workspace json must still be chunked"


def test_malformed_json_falls_back():
    # Garbage content should not raise; chunker returns fallback chunks.
    lang, chunks = workspace_chunker(Path("workspace.json"), "{ not valid")
    assert lang is None
    assert chunks

"""Tests for the pluggable chunker registry.

Uses Project.create() directly with a mock embedder so no real embedding model
is needed.  Each test writes files to a temp directory, indexes them, and
queries the resulting SQLite database to verify chunk content and language.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from cocoindex.connectors import sqlite as coco_sqlite
from cocoindex.resources.schema import VectorSchema
from example_toml_chunker import toml_chunker

from cocoindex_code.chunking import CHUNKER_REGISTRY, Chunk, TextPosition
from cocoindex_code.project import Project
from cocoindex_code.settings import ProjectSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMBED_DIM = 4  # tiny dimension — enough to satisfy the vector table schema


class _StubEmbedder:
    """Minimal embedder stub satisfying cocoindex memo-key and vector-schema requirements."""

    def __coco_memo_key__(self) -> str:
        return "stub-embedder"

    async def __coco_vector_schema__(self) -> VectorSchema:
        return VectorSchema(dtype=np.dtype("float32"), size=_EMBED_DIM)

    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(_EMBED_DIM, dtype=np.float32)


async def _index_project(
    project_root: Path,
    **create_kwargs: Any,
) -> Project:
    """Create a Project and run a full index pass."""
    settings = ProjectSettings(include_patterns=["**/*.*"], exclude_patterns=["**/.cocoindex_code"])
    stub = _StubEmbedder()
    from cocoindex_code.settings import save_project_settings

    save_project_settings(project_root, settings)
    project = await Project.create(
        project_root,
        stub,
        indexing_params={},
        query_params={},
        **create_kwargs,
    )
    await project.run_index()
    return project


def _query_chunks(project_root: Path) -> list[dict[str, Any]]:
    """Read all stored chunks from the target SQLite database."""
    db_path = project_root / ".cocoindex_code" / "target_sqlite.db"
    conn = coco_sqlite.connect(str(db_path), load_vec=True)
    try:
        with conn.readonly() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT file_path, language, content, start_line, end_line FROM code_chunks_vec"
            ).fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


def _pos(line: int) -> TextPosition:
    """TextPosition with only line number set; suitable for line-granularity chunkers."""
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


# ---------------------------------------------------------------------------
# TOML fixture content
# ---------------------------------------------------------------------------

_TOML_CONTENT = """\
[section_one]
key = "value"
answer = 42

[section_two]
other = "hello"
flag = true
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_default_registry_is_empty(tmp_path: Path) -> None:
    """CHUNKER_REGISTRY is an empty dict when no registry is passed."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "hello.py").write_text("x = 1\n")

    project = await _index_project(tmp_path)
    registry = project.env.get_context(CHUNKER_REGISTRY)
    assert isinstance(registry, dict)
    assert registry == {}


async def test_unregistered_suffix_uses_splitter(tmp_path: Path) -> None:
    """Files with no registered chunker are processed by RecursiveSplitter."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "sample.py").write_text("def foo():\n    return 1\n")

    await _index_project(tmp_path)
    chunks = _query_chunks(tmp_path)

    assert len(chunks) >= 1
    assert all(c["language"] == "python" for c in chunks)
    assert any("foo" in c["content"] for c in chunks)


async def test_registered_chunker_is_called(tmp_path: Path) -> None:
    """A registered ChunkerFn splits files and may override the language."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "config.toml").write_text(_TOML_CONTENT)

    await _index_project(tmp_path, chunker_registry={".toml": toml_chunker})
    chunks = _query_chunks(tmp_path)

    assert len(chunks) == 2
    contents = {c["content"] for c in chunks}
    assert any("section_one" in c for c in contents)
    assert any("section_two" in c for c in contents)
    assert all(c["language"] == "toml" for c in chunks)


async def test_chunker_language_none_preserves_detected(tmp_path: Path) -> None:
    """When ChunkerFn returns language=None, detect_code_language() is used."""

    def _passthrough_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        lines = content.splitlines()
        return None, [Chunk(text=content, start=_pos(1), end=_pos(len(lines)))]

    (tmp_path / ".git").mkdir()
    (tmp_path / "script.py").write_text("x = 1\n")

    await _index_project(tmp_path, chunker_registry={".py": _passthrough_chunker})
    chunks = _query_chunks(tmp_path)

    assert all(c["language"] == "python" for c in chunks)


async def test_registry_does_not_affect_other_suffixes(tmp_path: Path) -> None:
    """Registering a chunker for .toml does not affect .py files."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "config.toml").write_text(_TOML_CONTENT)
    (tmp_path / "code.py").write_text("def bar():\n    pass\n")

    await _index_project(tmp_path, chunker_registry={".toml": toml_chunker})
    chunks = _query_chunks(tmp_path)

    toml_chunks = [c for c in chunks if c["language"] == "toml"]
    py_chunks = [c for c in chunks if c["language"] == "python"]

    assert len(toml_chunks) == 2
    assert len(py_chunks) >= 1
    assert any("bar" in c["content"] for c in py_chunks)

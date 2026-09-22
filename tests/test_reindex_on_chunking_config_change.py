"""Editing language_overrides or the custom chunkers must re-process unchanged files.

process_file is memoized on its arguments; the settings and the chunker registry
are not arguments, so on their own they never invalidated the memo (issue #285).
"""

from __future__ import annotations

import gc
import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from cocoindex.connectors import sqlite as coco_sqlite
from cocoindex.resources.schema import VectorSchema

from cocoindex_code.chunking import Chunk, TextPosition, chunking_fingerprint
from cocoindex_code.project import Project
from cocoindex_code.settings import LanguageOverride, ProjectSettings, save_project_settings

_EMBED_DIM = 4


class _StubEmbedder:
    def __coco_memo_key__(self) -> str:
        return "stub-embedder"

    async def __coco_vector_schema__(self) -> VectorSchema:
        return VectorSchema(dtype=np.dtype("float32"), size=_EMBED_DIM)

    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(_EMBED_DIM, dtype=np.float32)


def _settings(**overrides: Any) -> ProjectSettings:
    return ProjectSettings(
        include_patterns=["**/*.*"], exclude_patterns=["**/.cocoindex_code"], **overrides
    )


async def _project(project_root: Path, **create_kwargs: Any) -> Project:
    return await Project.create(
        project_root, _StubEmbedder(), indexing_params={}, query_params={}, **create_kwargs
    )


def _chunks(project_root: Path) -> list[dict[str, Any]]:
    db_path = project_root / ".cocoindex_code" / "target_sqlite.db"
    conn = coco_sqlite.connect(str(db_path), load_vec=True)
    try:
        with conn.readonly() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT file_path, language, content FROM code_chunks_vec").fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


def _pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _chunker_a(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return "custom", [Chunk(text=f"A:{content}", start=_pos(1), end=_pos(1))]


def _chunker_b(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return "custom", [Chunk(text=f"B:{content}", start=_pos(1), end=_pos(1))]


async def test_language_override_change_reprocesses_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "sample.py").write_text("def foo():\n    return 1\n")
    save_project_settings(tmp_path, _settings())
    project = await _project(tmp_path)
    try:
        await project.run_index()
        assert {c["language"] for c in _chunks(tmp_path)} == {"python"}

        # The file is untouched; only settings.yml changed.
        save_project_settings(
            tmp_path, _settings(language_overrides=[LanguageOverride("py", "rust")])
        )
        await project.run_index()

        assert {c["language"] for c in _chunks(tmp_path)} == {"rust"}
    finally:
        project.close()


async def test_chunker_change_reprocesses_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "notes.txt").write_text("hello world\n")
    save_project_settings(tmp_path, _settings())
    first = await _project(tmp_path, chunker_registry={".txt": _chunker_a})
    try:
        await first.run_index()
        assert [c["content"] for c in _chunks(tmp_path)] == ["A:hello world\n"]
    finally:
        first.close()
    # The cocoindex core environment is process global and only released once the
    # owning Project is garbage collected; drop it so the "restart" below can open
    # its own environment.
    del first
    gc.collect()

    # A daemon restart with settings.yml pointing ".txt" at another chunker; same file bytes.
    second = await _project(tmp_path, chunker_registry={".txt": _chunker_b})
    try:
        await second.run_index()
        assert [c["content"] for c in _chunks(tmp_path)] == ["B:hello world\n"]
    finally:
        second.close()


def test_chunking_fingerprint_tracks_overrides_and_chunkers() -> None:
    base = chunking_fingerprint([], {})
    assert base == chunking_fingerprint([], {})
    assert chunking_fingerprint([LanguageOverride("py", "rust")], {}) != base
    assert chunking_fingerprint([LanguageOverride("py", "rust")], {}) == chunking_fingerprint(
        [LanguageOverride("py", "rust")], {}
    )
    with_a = chunking_fingerprint([], {".txt": _chunker_a})
    assert with_a != base
    assert with_a == chunking_fingerprint([], {".txt": _chunker_a})
    # Another function for the same suffix, or the same function for another suffix.
    assert chunking_fingerprint([], {".txt": _chunker_b}) != with_a
    assert chunking_fingerprint([], {".md": _chunker_a}) != with_a


def test_chunking_fingerprint_tracks_chunker_source_edits(tmp_path: Path) -> None:
    """Editing the chunker's module (same ``module:fn`` descriptor) changes the digest."""
    module_file = tmp_path / "my_chunker.py"
    module_file.write_text("def whole_file(path, content):\n    return None, []\n")
    spec = importlib.util.spec_from_file_location("my_chunker", module_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = chunking_fingerprint([], {".py": module.whole_file})

    module_file.write_text("def whole_file(path, content):\n    return 'text', []\n")
    spec.loader.exec_module(module)  # what a daemon restart does: load the edited code
    assert chunking_fingerprint([], {".py": module.whole_file}) != before

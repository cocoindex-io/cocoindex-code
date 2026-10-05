"""Editing language_overrides or the custom chunkers must re-process unchanged files,
and an unchanged chunking config must not (issue #285).

process_file is memoized on its arguments and on the change-detected context values
it reads. The settings and the chunker registry used to be neither, so on their own
they never invalidated the memo.
"""

from __future__ import annotations

import gc
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cocoindex.connectors import sqlite as coco_sqlite
from conftest import StubEmbedder

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex_code.daemon import ProjectRegistry
from cocoindex_code.project import Project
from cocoindex_code.protocol import IndexingProgress
from cocoindex_code.settings import (
    ChunkerMapping,
    LanguageOverride,
    ProjectSettings,
    save_project_settings,
)
from cocoindex_code.shared import ChunkerFingerprint


def _settings(**overrides: Any) -> ProjectSettings:
    return ProjectSettings(
        include_patterns=["**/*.*"], exclude_patterns=["**/.cocoindex_code"], **overrides
    )


async def _project(project_root: Path, embedder: StubEmbedder, **create_kwargs: Any) -> Project:
    return await Project.create(
        project_root, embedder, indexing_params={}, query_params={}, **create_kwargs
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


def _contents(project_root: Path, file_path: str) -> list[str]:
    return [c["content"] for c in _chunks(project_root) if c["file_path"] == file_path]


def _pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _chunker_a(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return "custom", [Chunk(text=f"A:{content}", start=_pos(1), end=_pos(1))]


def _chunker_b(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return "custom", [Chunk(text=f"B:{content}", start=_pos(1), end=_pos(1))]


# ---------------------------------------------------------------------------
# Daemon-level helpers: chunkers configured in settings.yml by "module:attr" spec
# ---------------------------------------------------------------------------

_CHUNKER_MODULE_NAME = "ccc_test_chunkers"

# Prefixes every chunk with TAG, so a chunk shows which version of the module made it.
_CHUNKER_MODULE_SOURCE = """\
import functools

from cocoindex_code.chunking import Chunk, TextPosition

TAG = "v1"
_POS = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)


def _tagged(path, content, *, kind):
    return "custom", [Chunk(text=f"{TAG}/{kind}:{content}", start=_POS, end=_POS)]


def plain(path, content):
    return _tagged(path, content, kind="plain")


by_partial = functools.partial(_tagged, kind="partial")


class _Instance:
    def __call__(self, path, content):
        return _tagged(path, content, kind="instance")


instance = _Instance()
"""


@dataclass
class _ChunkerModule:
    file: Path

    @staticmethod
    def spec(attr: str) -> str:
        return f"{_CHUNKER_MODULE_NAME}:{attr}"

    def set_tag(self, tag: str) -> None:
        source = self.file.read_text()
        self.file.write_text(source.replace('TAG = "v1"', f"TAG = {tag!r}"))


@pytest.fixture
def chunker_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_ChunkerModule]:
    """An importable chunker module outside the indexed project."""
    module_dir = tmp_path / "chunker_lib"
    module_dir.mkdir()
    module_file = module_dir / f"{_CHUNKER_MODULE_NAME}.py"
    module_file.write_text(_CHUNKER_MODULE_SOURCE)
    # Edits must be seen by the next import, not shadowed by a same-second .pyc.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(module_dir))
    yield _ChunkerModule(module_file)
    sys.modules.pop(_CHUNKER_MODULE_NAME, None)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _restart_daemon(registry: ProjectRegistry, embedder: StubEmbedder) -> ProjectRegistry:
    """What a daemon restart does: drop the loaded projects and imported chunker modules."""
    registry.close_all()
    sys.modules.pop(_CHUNKER_MODULE_NAME, None)
    return ProjectRegistry(embedder)


async def _index(registry: ProjectRegistry, project_root: Path) -> IndexingProgress:
    """Run one index pass through the daemon's registry; return the final file stats."""
    project = await registry.get_project(str(project_root))
    progress: list[IndexingProgress] = []
    await project.run_index(on_progress=progress.append)
    return progress[-1]


def _assert_all_unchanged(stats: IndexingProgress, num_files: int) -> None:
    assert stats.num_unchanged == num_files
    assert stats.num_adds == stats.num_reprocesses == stats.num_deletes == 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_language_override_change_reprocesses_unchanged_files(
    tmp_path: Path, stub_embedder: StubEmbedder
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "sample.py").write_text("def foo():\n    return 1\n")
    save_project_settings(tmp_path, _settings())
    project = await _project(tmp_path, stub_embedder)
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


async def test_chunker_change_reprocesses_unchanged_files(
    tmp_path: Path, stub_embedder: StubEmbedder
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "notes.txt").write_text("hello world\n")
    save_project_settings(tmp_path, _settings())
    first = await _project(
        tmp_path,
        stub_embedder,
        chunker_registry={".txt": _chunker_a},
        chunker_fingerprints={".txt": ChunkerFingerprint(f"{__name__}:_chunker_a", "")},
    )
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
    second = await _project(
        tmp_path,
        stub_embedder,
        chunker_registry={".txt": _chunker_b},
        chunker_fingerprints={".txt": ChunkerFingerprint(f"{__name__}:_chunker_b", "")},
    )
    try:
        await second.run_index()
        assert [c["content"] for c in _chunks(tmp_path)] == ["B:hello world\n"]
    finally:
        second.close()


@pytest.mark.parametrize(
    ("attr", "kind"), [("plain", "plain"), ("by_partial", "partial"), ("instance", "instance")]
)
async def test_unchanged_chunking_config_leaves_files_unchanged(
    attr: str,
    kind: str,
    project_root: Path,
    chunker_module: _ChunkerModule,
    stub_embedder: StubEmbedder,
) -> None:
    (project_root / "notes.txt").write_text("hello\n")
    (project_root / "lib.inc").write_text("<?php echo 1;\n")
    (project_root / "main.py").write_text("x = 1\n")
    save_project_settings(
        project_root,
        _settings(
            language_overrides=[LanguageOverride("inc", "php")],
            chunkers=[ChunkerMapping("txt", chunker_module.spec(attr))],
        ),
    )
    registry = ProjectRegistry(stub_embedder)
    try:
        stats = await _index(registry, project_root)
        assert stats.num_adds == 3
        assert _contents(project_root, "notes.txt") == [f"v1/{kind}:hello\n"]

        _assert_all_unchanged(await _index(registry, project_root), 3)

        registry = _restart_daemon(registry, stub_embedder)
        _assert_all_unchanged(await _index(registry, project_root), 3)
    finally:
        registry.close_all()


@pytest.mark.parametrize("reload_project", [False, True], ids=["same-project", "project-reloaded"])
async def test_chunker_edit_takes_effect_after_restart(
    reload_project: bool,
    project_root: Path,
    chunker_module: _ChunkerModule,
    stub_embedder: StubEmbedder,
) -> None:
    (project_root / "notes.txt").write_text("hello\n")
    save_project_settings(
        project_root, _settings(chunkers=[ChunkerMapping("txt", chunker_module.spec("plain"))])
    )
    registry = ProjectRegistry(stub_embedder)
    try:
        await _index(registry, project_root)
        assert _contents(project_root, "notes.txt") == ["v1/plain:hello\n"]

        chunker_module.set_tag("v2")
        # The running daemon keeps the code it imported at startup, also when it loads
        # the project again (as after `ccc reset`), so the chunks stay v1 for now.
        if reload_project:
            registry.remove_project(str(project_root))
        await _index(registry, project_root)
        assert _contents(project_root, "notes.txt") == ["v1/plain:hello\n"]

        registry = _restart_daemon(registry, stub_embedder)
        stats = await _index(registry, project_root)
        assert stats.num_reprocesses == 1
        assert _contents(project_root, "notes.txt") == ["v2/plain:hello\n"]
    finally:
        registry.close_all()


async def test_chunker_spec_change_reprocesses_affected_files(
    project_root: Path, chunker_module: _ChunkerModule, stub_embedder: StubEmbedder
) -> None:
    (project_root / "notes.txt").write_text("hello\n")
    save_project_settings(
        project_root, _settings(chunkers=[ChunkerMapping("txt", chunker_module.spec("plain"))])
    )
    registry = ProjectRegistry(stub_embedder)
    try:
        await _index(registry, project_root)
        assert _contents(project_root, "notes.txt") == ["v1/plain:hello\n"]

        # Another callable from the same, unedited module.
        save_project_settings(
            project_root,
            _settings(chunkers=[ChunkerMapping("txt", chunker_module.spec("by_partial"))]),
        )
        registry = _restart_daemon(registry, stub_embedder)
        stats = await _index(registry, project_root)
        assert stats.num_reprocesses == 1
        assert _contents(project_root, "notes.txt") == ["v1/partial:hello\n"]
    finally:
        registry.close_all()

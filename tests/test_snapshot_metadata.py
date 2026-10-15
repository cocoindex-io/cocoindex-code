"""Real SQLite/vector queries and producer lifecycle; no model/network required."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from cocoindex.connectors import sqlite as coco_sqlite
from conftest import StubEmbedder
from test_query_filters import _write_vec_snapshot

from cocoindex_code.daemon import ProjectRegistry
from cocoindex_code.project import Project
from cocoindex_code.query import query_codebase_readonly
from cocoindex_code.settings import EmbeddingSettings, ProjectSettings, save_project_settings
from cocoindex_code.shared import SQLITE_DB, Embedder
from cocoindex_code.snapshot_metadata import (
    begin_snapshot_update,
    embedding_contract,
    require_snapshot_compatible,
)

_SETTINGS = EmbeddingSettings(
    provider="sentence-transformers",
    model="test-model-a",
    indexing_params={"prompt_name": "passage"},
    query_params={"prompt_name": "query"},
)
_CONTRACT = embedding_contract(_SETTINGS)


class _QueryEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, query: str, **kwargs: Any) -> np.ndarray[Any, Any]:
        self.calls += 1
        assert kwargs == {"prompt_name": "query"}
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


@pytest.mark.parametrize(
    "settings",
    [
        replace(_SETTINGS, model="test-model-b"),
        replace(_SETTINGS, provider="litellm", indexing_params={}, query_params={}),
        replace(_SETTINGS, indexing_params={"prompt_name": "other-passage"}),
        replace(_SETTINGS, query_params={"prompt_name": "other-query"}),
    ],
    ids=["same-dimension-other-model", "provider", "indexing-params", "query-params"],
)
async def test_member_rejects_mismatch_before_embedding_without_writes(
    tmp_path: Path, settings: EmbeddingSettings
) -> None:
    path = tmp_path / "target_sqlite.db"
    _write_vec_snapshot(path, contract=_CONTRACT)
    before = path.read_bytes()
    files_before = set(tmp_path.iterdir())
    embedder = _QueryEmbedder()
    registry = ProjectRegistry(
        cast(Embedder, embedder),
        query_params={"prompt_name": "query"},
        embedding_contract=embedding_contract(settings),
    )
    with pytest.raises(RuntimeError, match="embedding settings differ"):
        await registry.search_readonly(str(path), "fibonacci", None, None, 1, 0)
    assert embedder.calls == 0
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.parametrize("damage", ["legacy", "missing-row", "malformed", "version", "not-ready"])
async def test_member_rejects_unverifiable_snapshot(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "target_sqlite.db"
    _write_vec_snapshot(path, contract=None if damage == "legacy" else _CONTRACT)
    with sqlite3.connect(path) as conn:
        if damage == "missing-row":
            conn.execute("DELETE FROM ccc_snapshot_metadata")
        elif damage == "malformed":
            conn.execute("ALTER TABLE ccc_snapshot_metadata RENAME COLUMN contract TO bad")
        elif damage == "version":
            contract = json.loads(_CONTRACT)
            contract["version"] = 999
            conn.execute("UPDATE ccc_snapshot_metadata SET contract=?", (json.dumps(contract),))
        elif damage == "not-ready":
            conn.execute("UPDATE ccc_snapshot_metadata SET ready=0")
    before = path.read_bytes()
    embedder = _QueryEmbedder()
    with pytest.raises(RuntimeError, match="shared index|Shared index"):
        await query_codebase_readonly(
            "fibonacci",
            path,
            cast(Embedder, embedder),
            {},
            expected_embedding_contract=_CONTRACT,
        )
    assert embedder.calls == 0
    assert path.read_bytes() == before


async def test_member_checks_and_queries_same_snapshot_even_during_replacement(
    tmp_path: Path,
) -> None:
    published = tmp_path / "target_sqlite.db"
    replacement = tmp_path / "replacement.db"
    _write_vec_snapshot(published, contract=_CONTRACT)
    _write_vec_snapshot(
        replacement,
        contract=embedding_contract(replace(_SETTINGS, model="test-model-b")),
        first_content="different model",
    )

    class _ReplacingEmbedder(_QueryEmbedder):
        async def embed(self, query: str, **kwargs: Any) -> np.ndarray[Any, Any]:
            replacement.replace(published)
            return await super().embed(query, **kwargs)

    result = await query_codebase_readonly(
        "fibonacci",
        published,
        cast(Embedder, _ReplacingEmbedder()),
        {"prompt_name": "query"},
        limit=1,
        expected_embedding_contract=_CONTRACT,
    )
    assert result[0].content == "fibonacci"
    assert result[0].score == pytest.approx(1.0)
    next_embedder = _QueryEmbedder()
    with pytest.raises(RuntimeError, match="embedding settings differ"):
        await query_codebase_readonly(
            "fibonacci",
            published,
            cast(Embedder, next_embedder),
            {},
            expected_embedding_contract=_CONTRACT,
        )
    assert next_embedder.calls == 0


async def test_member_accepts_matching_snapshot_and_observes_next_publication(
    tmp_path: Path,
) -> None:
    published = tmp_path / "target_sqlite.db"
    replacement = tmp_path / "replacement.db"
    _write_vec_snapshot(published, contract=_CONTRACT)
    _write_vec_snapshot(replacement, contract=_CONTRACT, first_content="updated fibonacci")
    registry = ProjectRegistry(
        cast(Embedder, _QueryEmbedder()),
        query_params={"prompt_name": "query"},
        embedding_contract=_CONTRACT,
    )
    before = published.read_bytes()
    first = await registry.search_readonly(str(published), "fibonacci", None, None, 1, 0)
    assert first[0].content == "fibonacci"
    assert published.read_bytes() == before
    replacement.replace(published)
    second = await registry.search_readonly(str(published), "fibonacci", None, None, 1, 0)
    assert second[0].content == "updated fibonacci"


def test_contract_uses_resolved_defaults_and_ignores_execution_settings() -> None:
    from cocoindex_code.embedder_defaults import LEGACY_QUERY_PROMPT_MODELS

    legacy = EmbeddingSettings(
        provider="sentence-transformers", model=next(iter(LEGACY_QUERY_PROMPT_MODELS))
    )
    explicit = replace(legacy, indexing_params={}, query_params={"prompt_name": "query"})
    assert embedding_contract(legacy) == embedding_contract(explicit)
    assert embedding_contract(_SETTINGS) == embedding_contract(
        replace(_SETTINGS, device="cpu", min_interval_ms=123)
    )
    assert embedding_contract(_SETTINGS) == embedding_contract(
        replace(_SETTINGS, model="sbert/" + _SETTINGS.model)
    )


async def _project(root: Path, contract: str | None) -> Project:
    root.mkdir(exist_ok=True)
    save_project_settings(root, ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]))
    (root / "a.py").write_text("def foo():\n    return 1\n")
    return await Project.create(
        root,
        cast(Embedder, StubEmbedder()),
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
        embedding_contract=contract,
    )


async def test_real_leader_index_records_contract_and_revalidates_incrementally(
    tmp_path: Path,
) -> None:
    project = await _project(tmp_path, _CONTRACT)
    try:
        await project.run_index()
        db = project.env.get_context(SQLITE_DB)
        require_snapshot_compatible(db, _CONTRACT)
        await project.run_index()
        require_snapshot_compatible(db, _CONTRACT)
        assert await project.search("foo")
        with db.readonly() as conn:
            tables = [
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
        assert "ccc_snapshot_metadata" in tables
    finally:
        project.close()


async def test_legacy_leader_still_searches_but_incremental_update_does_not_certify(
    tmp_path: Path,
) -> None:
    legacy = await _project(tmp_path, None)
    await legacy.run_index()
    project = legacy
    project._embedding_contract = _CONTRACT
    try:
        await project.run_index()
        assert await project.search("foo")
        path = tmp_path / ".cocoindex_code" / "target_sqlite.db"
        with sqlite3.connect(path) as conn:
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='ccc_snapshot_metadata'"
            ).fetchall()
        with pytest.raises(RuntimeError, match="missing embedding metadata"):
            await query_codebase_readonly(
                "foo",
                path,
                cast(Embedder, _QueryEmbedder()),
                {},
                expected_embedding_contract=_CONTRACT,
            )
    finally:
        project.close()


def test_changed_contract_cannot_relabel_or_recover_by_reverting_settings(tmp_path: Path) -> None:
    path = tmp_path / "target_sqlite.db"
    _write_vec_snapshot(path, contract=_CONTRACT)
    db = coco_sqlite.connect(path, load_vec=True)
    try:
        other = embedding_contract(replace(_SETTINGS, model="test-model-b"))
        assert not begin_snapshot_update(db, other)
        assert not begin_snapshot_update(db, other)
        assert not begin_snapshot_update(db, _CONTRACT)
        with pytest.raises(RuntimeError, match="unknown embedding provenance"):
            require_snapshot_compatible(db, _CONTRACT)
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["exception", "cancel", "component-error", "unfinished"])
async def test_failed_update_is_unready_until_successful_retry(
    tmp_path: Path, failure: str
) -> None:
    import cocoindex as coco

    project = await _project(tmp_path, _CONTRACT)
    try:
        await project.run_index()
        db = project.env.get_context(SQLITE_DB)
        real_app = project._app

        class _FailedHandle:
            async def watch(self) -> Any:
                # Readiness must already be invalidated when indexing starts.
                with pytest.raises(RuntimeError, match="not ready"):
                    require_snapshot_compatible(db, _CONTRACT)
                if failure == "exception":
                    raise RuntimeError("indexing failed")
                if failure == "cancel":
                    raise asyncio.CancelledError()
                yield SimpleNamespace(
                    stats=SimpleNamespace(
                        total=SimpleNamespace(num_errors=int(failure == "component-error")),
                        by_component={},
                    ),
                    status=(
                        coco.UpdateStatus.RUNNING
                        if failure == "unfinished"
                        else coco.UpdateStatus.READY
                    ),
                )

        project._app = cast(Any, SimpleNamespace(update=lambda: _FailedHandle()))
        if failure == "exception":
            with pytest.raises(RuntimeError, match="indexing failed"):
                await project.run_index()
        elif failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await project.run_index()
        else:
            await project.run_index()
        with pytest.raises(RuntimeError, match="not ready"):
            require_snapshot_compatible(db, _CONTRACT)
        project._app = real_app
        await project.run_index()
        require_snapshot_compatible(db, _CONTRACT)
    finally:
        project.close()


async def test_real_leader_configuration_change_keeps_search_but_loses_provenance(
    tmp_path: Path,
) -> None:
    project = await _project(tmp_path, _CONTRACT)
    try:
        await project.run_index()
        db = project.env.get_context(SQLITE_DB)
        project._embedding_contract = embedding_contract(replace(_SETTINGS, model="test-model-b"))
        await project.run_index()
        assert await project.search("foo")
        with pytest.raises(RuntimeError, match="unknown embedding provenance"):
            require_snapshot_compatible(db, project._embedding_contract)
        project._embedding_contract = _CONTRACT
        await project.run_index()
        with pytest.raises(RuntimeError, match="unknown embedding provenance"):
            require_snapshot_compatible(db, _CONTRACT)
    finally:
        project.close()


async def test_vector_dimension_alone_accepts_incompatible_synthetic_space(tmp_path: Path) -> None:
    from cocoindex_code.query import query_codebase

    path = tmp_path / "target_sqlite.db"
    _write_vec_snapshot(path)
    db = coco_sqlite.connect(path, load_vec=True)

    class _OtherSpaceEmbedder:
        async def embed(self, query: str, **kwargs: Any) -> np.ndarray[Any, Any]:
            return np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)

    try:
        result = await query_codebase(
            "fibonacci", db, cast(Embedder, _OtherSpaceEmbedder()), {}, limit=1
        )
        assert result[0].content == "parse csv"
        assert result[0].score == pytest.approx(1.0)
    finally:
        db.close()

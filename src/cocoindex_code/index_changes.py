"""Read-only preview of files changed since the last index."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import NamedTuple

import cocoindex as coco
from cocoindex.connectorkits.fingerprint import fingerprint_bytes
from cocoindex.connectors import localfs
from cocoindex.connectors import sqlite as coco_sqlite
from cocoindex.inspect import iter_stable_paths_by_name

from .file_walk import build_matcher, iter_included_files
from .settings import cocoindex_db_path, load_project_settings, target_sqlite_db_path
from .shared import APP_NAME, CODEBASE_DIR, SQLITE_DB

FILE_MANIFEST_APP_NAME = f"{APP_NAME}FileManifest"
_FILE_MANIFEST_TABLE = "code_file_manifest"
_CREATE_FILE_MANIFEST_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_FILE_MANIFEST_TABLE} (
    path TEXT PRIMARY KEY NOT NULL,
    fingerprint BLOB NOT NULL
)
"""


@dataclass(frozen=True)
class IndexChanges:
    added: tuple[str, ...]
    updated: tuple[str, ...] | None
    deleted: tuple[str, ...]


class _FileManifestAction(NamedTuple):
    path: str
    fingerprint: bytes | None


class _FileManifestHandler(coco.TargetHandler[bytes, bytes]):
    def __init__(self) -> None:
        self._sink = coco.TargetActionSink[_FileManifestAction, None].from_fn(self._apply)

    @staticmethod
    def _apply(
        context_provider: coco.ContextProvider,
        actions: Sequence[_FileManifestAction],
        /,
    ) -> None:
        db = context_provider.get(SQLITE_DB)
        with db.transaction() as conn:
            conn.execute(_CREATE_FILE_MANIFEST_TABLE)
            conn.executemany(
                f"DELETE FROM {_FILE_MANIFEST_TABLE} WHERE path = ?",
                [(action.path,) for action in actions if action.fingerprint is None],
            )
            conn.executemany(
                f"""
                INSERT INTO {_FILE_MANIFEST_TABLE} (path, fingerprint)
                VALUES (?, ?)
                ON CONFLICT (path) DO UPDATE SET fingerprint = excluded.fingerprint
                """,
                [
                    (action.path, action.fingerprint)
                    for action in actions
                    if action.fingerprint is not None
                ],
            )

    def reconcile(
        self,
        key: coco.StableKey,
        desired_state: bytes | coco.NonExistenceType,
        prev_possible_records: Collection[bytes],
        prev_may_be_missing: bool,
        /,
    ) -> coco.TargetReconcileOutput[_FileManifestAction, bytes] | None:
        assert isinstance(key, str)
        if coco.is_non_existence(desired_state):
            if not prev_possible_records and not prev_may_be_missing:
                return None
            return coco.TargetReconcileOutput(
                action=_FileManifestAction(key, None),
                sink=self._sink,
                tracking_record=coco.NON_EXISTENCE,
            )

        if not prev_may_be_missing and all(
            previous == desired_state for previous in prev_possible_records
        ):
            return None

        return coco.TargetReconcileOutput(
            action=_FileManifestAction(key, desired_state),
            sink=self._sink,
            tracking_record=desired_state,
        )


_FILE_MANIFEST_PROVIDER = coco.register_root_target_states_provider(
    "cocoindex_code/file_manifest",
    _FileManifestHandler(),
)


@coco.fn(memo=True)
async def _track_file(file: localfs.File) -> None:
    path = file.file_path.path.as_posix()
    coco.declare_target_state(
        _FILE_MANIFEST_PROVIDER.target_state(path, await file.content_fingerprint())
    )


@coco.fn
async def _build_file_manifest() -> None:
    project_root = coco.use_context(CODEBASE_DIR)
    settings = load_project_settings(project_root)
    matcher = build_matcher(
        project_root,
        settings.include_patterns,
        settings.exclude_patterns,
        settings.max_file_size,
    )
    files = localfs.walk_dir(
        CODEBASE_DIR,
        recursive=True,
        path_matcher=matcher,
    )
    await coco.mount_each(
        coco.component_subpath(coco.Symbol("track_file")),
        _track_file,
        files.items(),
    )


def create_file_manifest_app(env: coco.Environment) -> coco.App[[], None]:
    """Create the app that persists source fingerprints after indexing."""
    return coco.App(
        coco.AppConfig(name=FILE_MANIFEST_APP_NAME, environment=env),
        _build_file_manifest,
    )


def prepare_file_manifest(db: coco_sqlite.ManagedConnection) -> None:
    """Create the fingerprint table before the first manifest update."""
    with db.transaction() as conn:
        conn.execute(_CREATE_FILE_MANIFEST_TABLE)


async def find_index_changes(project_root: Path) -> IndexChanges:
    """Return files the next index would add, update, or delete."""
    indexed = await asyncio.to_thread(_indexed_fingerprints, project_root)
    if indexed is not None:
        matched = await asyncio.to_thread(_matched_fingerprints, project_root)
        matched_paths = set(matched)
        indexed_paths = set(indexed)
        return IndexChanges(
            added=tuple(sorted(matched_paths - indexed_paths)),
            updated=tuple(
                sorted(
                    path for path in matched_paths & indexed_paths if matched[path] != indexed[path]
                )
            ),
            deleted=tuple(sorted(indexed_paths - matched_paths)),
        )

    matched_paths, indexed_paths = await asyncio.gather(
        asyncio.to_thread(_matched_paths, project_root),
        _indexed_paths(project_root),
    )
    return IndexChanges(
        added=tuple(sorted(matched_paths - indexed_paths)),
        updated=None if indexed_paths else (),
        deleted=tuple(sorted(indexed_paths - matched_paths)),
    )


def _matched_fingerprints(project_root: Path) -> dict[str, bytes]:
    fingerprints: dict[str, bytes] = {}
    for absolute_path, relative_path in _iter_matched_files(project_root):
        try:
            fingerprints[relative_path.as_posix()] = fingerprint_bytes(absolute_path.read_bytes())
        except OSError:
            continue
    return fingerprints


def _matched_paths(project_root: Path) -> set[str]:
    return {relative_path.as_posix() for _, relative_path in _iter_matched_files(project_root)}


def _iter_matched_files(project_root: Path) -> Iterator[tuple[Path, PurePath]]:
    settings = load_project_settings(project_root)
    matcher = build_matcher(
        project_root,
        settings.include_patterns,
        settings.exclude_patterns,
        settings.max_file_size,
    )
    return iter_included_files(project_root, project_root, matcher)


def _indexed_fingerprints(project_root: Path) -> dict[str, bytes] | None:
    db_path = target_sqlite_db_path(project_root)
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute(f"SELECT path, fingerprint FROM {_FILE_MANIFEST_TABLE}").fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None
    return {path: fingerprint for path, fingerprint in rows}


async def _indexed_paths(project_root: Path) -> set[str]:
    db_path = cocoindex_db_path(project_root)
    if not db_path.exists():
        return set()

    env = coco.Environment(coco.Settings.from_env(db_path))
    paths: set[str] = set()
    async for item in iter_stable_paths_by_name(env, APP_NAME):
        parts = coco.StablePath(item.path).parts()
        if (
            len(parts) == 2
            and isinstance(parts[0], coco.Symbol)
            and parts[0].name == "process_file"
            and isinstance(parts[1], str)
        ):
            paths.add(parts[1])
    return paths

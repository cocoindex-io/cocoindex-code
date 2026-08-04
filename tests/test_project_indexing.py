"""Project indexing lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from cocoindex_code.project import Project


class _WatchHandle:
    def __init__(
        self,
        *,
        on_enter: Callable[[], None],
        on_exit: Callable[[], None],
        release: asyncio.Event | None,
    ) -> None:
        self._on_enter = on_enter
        self._on_exit = on_exit
        self._release = release

    async def watch(self) -> AsyncIterator[Any]:
        self._on_enter()
        try:
            if self._release is not None:
                await self._release.wait()
        finally:
            self._on_exit()
        if False:  # pragma: no cover - makes this an async generator
            yield None


class _ControlledApp:
    def __init__(self, handle: _WatchHandle) -> None:
        self._handle = handle

    def update(self) -> _WatchHandle:
        return self._handle


class _ControlledProject(Project):  # type: ignore[misc]
    def __init__(
        self,
        *,
        on_enter: Callable[[], None],
        on_exit: Callable[[], None],
        release: asyncio.Event | None = None,
        clear_mps_cache_after_index: bool = False,
    ) -> None:
        self._app = cast(
            Any,
            _ControlledApp(_WatchHandle(on_enter=on_enter, on_exit=on_exit, release=release)),
        )
        self._index_lock = asyncio.Lock()
        self._clear_mps_cache_after_index = clear_mps_cache_after_index
        self._initial_index_done = asyncio.Event()
        self._initial_index_task = None
        self._initial_index_started = None
        self._indexing_stats = None


async def test_projects_can_prepare_indexes_concurrently() -> None:
    release = asyncio.Event()
    both_entered = asyncio.Event()
    active = 0
    max_active = 0

    def enter() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            both_entered.set()

    def exit() -> None:
        nonlocal active
        active -= 1

    first = _ControlledProject(on_enter=enter, on_exit=exit, release=release)
    second = _ControlledProject(on_enter=enter, on_exit=exit, release=release)

    first_task = asyncio.create_task(first.run_index())
    second_task = asyncio.create_task(second.run_index())
    await asyncio.wait_for(both_entered.wait(), timeout=0.5)

    assert max_active == 2

    release.set()
    await asyncio.gather(first_task, second_task)
    assert active == 0


async def test_concurrent_initial_index_requests_share_one_background_task() -> None:
    release = asyncio.Event()
    entered = asyncio.Event()
    execution_count = 0

    def enter() -> None:
        nonlocal execution_count
        execution_count += 1
        entered.set()

    project = _ControlledProject(on_enter=enter, on_exit=lambda: None, release=release)
    first = asyncio.create_task(project.ensure_indexing_started())
    second = asyncio.create_task(project.ensure_indexing_started())

    await asyncio.wait_for(asyncio.gather(first, second), timeout=0.5)
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    assert project._index_lock.locked() is True
    assert execution_count == 1

    release.set()
    await project.wait_for_indexing_done()
    assert execution_count == 1


async def test_mps_allocator_cache_is_cleared_after_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_cache = AsyncMock()
    monkeypatch.setattr("cocoindex_code.project.clear_mps_allocator_cache", clear_cache)
    project = _ControlledProject(
        on_enter=lambda: None,
        on_exit=lambda: None,
        clear_mps_cache_after_index=True,
    )

    await project.run_index()

    clear_cache.assert_awaited_once_with()
    assert project._initial_index_done.is_set() is True


async def test_mps_cache_cleanup_failure_does_not_fail_completed_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_cache = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    monkeypatch.setattr("cocoindex_code.project.clear_mps_allocator_cache", clear_cache)
    project = _ControlledProject(
        on_enter=lambda: None,
        on_exit=lambda: None,
        clear_mps_cache_after_index=True,
    )

    await project.run_index()

    assert project._initial_index_done.is_set() is True
    assert project.indexing_stats is None


async def test_cancelling_mps_cache_cleanup_still_restores_indexing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()

    async def blocked_cleanup() -> None:
        cleanup_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("cocoindex_code.project.clear_mps_allocator_cache", blocked_cleanup)
    project = _ControlledProject(
        on_enter=lambda: None,
        on_exit=lambda: None,
        clear_mps_cache_after_index=True,
    )
    task = asyncio.create_task(project.run_index())
    await asyncio.wait_for(cleanup_started.wait(), timeout=0.5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert project._initial_index_done.is_set() is True
    assert project.indexing_stats is None
    await asyncio.wait_for(project.wait_for_indexing_done(), timeout=0.5)

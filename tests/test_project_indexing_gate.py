"""Cross-project indexing concurrency tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from cocoindex_code.project import Project


class _ControlledProject(Project):  # type: ignore[misc]
    def __init__(
        self,
        *,
        indexing_gate: asyncio.Lock,
        on_enter: Callable[[], None],
        on_exit: Callable[[], None],
        release: asyncio.Event | None = None,
    ) -> None:
        self._index_lock = asyncio.Lock()
        self._indexing_gate = indexing_gate
        self._initial_index_done = asyncio.Event()
        self._initial_index_task = None
        self._initial_index_started = None
        self._indexing_stats = None
        self._on_enter = on_enter
        self._on_exit = on_exit
        self._release = release

    async def _run_index_inner(
        self,
        on_progress: Callable[..., None] | None = None,
    ) -> None:
        del on_progress
        try:
            self._on_enter()
            if self._release is not None:
                await self._release.wait()
        finally:
            self._on_exit()
            self._initial_index_done.set()
            self._indexing_stats = None


async def test_projects_share_one_indexing_gate() -> None:
    indexing_gate = asyncio.Lock()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    active = 0
    max_active = 0

    def enter_first() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)

    def enter_second() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        second_entered.set()

    def exit() -> None:
        nonlocal active
        active -= 1

    first = _ControlledProject(
        indexing_gate=indexing_gate,
        on_enter=enter_first,
        on_exit=exit,
        release=release_first,
    )
    second = _ControlledProject(
        indexing_gate=indexing_gate,
        on_enter=enter_second,
        on_exit=exit,
    )

    first_task = asyncio.create_task(first.run_index())
    while active == 0:
        await asyncio.sleep(0)

    second_task = asyncio.create_task(second.run_index())
    await asyncio.sleep(0)

    assert second._index_lock.locked() is True
    assert second_entered.is_set() is False
    assert active == 1

    release_first.set()
    await first_task
    await second_entered.wait()
    await second_task

    assert max_active == 1
    assert active == 0


async def test_initial_index_reports_started_when_queued_behind_another_project() -> None:
    indexing_gate = asyncio.Lock()
    await indexing_gate.acquire()
    project = _ControlledProject(
        indexing_gate=indexing_gate,
        on_enter=lambda: None,
        on_exit=lambda: None,
    )

    ensure_task = asyncio.create_task(project.ensure_indexing_started())

    try:
        await asyncio.wait_for(asyncio.shield(ensure_task), timeout=0.5)
        assert project._index_lock.locked() is True
        assert ensure_task.done() is True
        assert project.should_wait_for_indexing is True
    finally:
        indexing_gate.release()

    await ensure_task
    await project.wait_for_indexing_done()


async def test_concurrent_initial_index_requests_share_one_background_task() -> None:
    indexing_gate = asyncio.Lock()
    await indexing_gate.acquire()
    execution_count = 0

    def enter() -> None:
        nonlocal execution_count
        execution_count += 1

    project = _ControlledProject(
        indexing_gate=indexing_gate,
        on_enter=enter,
        on_exit=lambda: None,
    )
    first = asyncio.create_task(project.ensure_indexing_started())
    second = asyncio.create_task(project.ensure_indexing_started())

    try:
        await asyncio.wait_for(asyncio.gather(first, second), timeout=0.5)
        assert project._index_lock.locked() is True
    finally:
        indexing_gate.release()

    await project.wait_for_indexing_done()
    assert execution_count == 1

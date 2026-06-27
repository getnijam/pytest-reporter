"""Batches test executions and flushes them in chunks.

Unlike the Playwright reporter (which fire-and-forgets over the network on a worker
thread), this buffer only *appends* during the test run, it never blocks the test
path on I/O. The accumulated executions are flushed in `FLUSH_SIZE` chunks at
`drain()` time (session finish), which keeps the hot path allocation-only and avoids
threading complexity. pytest sessions are short-lived, so a single end-of-run flush
is acceptable.
"""

from __future__ import annotations

from typing import Callable

from . import log
from .models import TestExecution

FLUSH_SIZE = 50


class ExecutionBuffer:
    def __init__(self, flush_fn: Callable[[list[TestExecution]], None]) -> None:
        self._flush_fn = flush_fn
        self._items: list[TestExecution] = []

    def add(self, item: TestExecution) -> None:
        self._items.append(item)

    def drain(self) -> None:
        """Send everything collected, in FLUSH_SIZE chunks. A failed chunk drops that
        batch with a warning (the flush fn soft-fails) and we continue with the rest."""
        items, self._items = self._items, []
        for start in range(0, len(items), FLUSH_SIZE):
            batch = items[start : start + FLUSH_SIZE]
            try:
                self._flush_fn(batch)
            except Exception as err:
                log.warn(f"dropped a batch of {len(batch)} executions: {err}")

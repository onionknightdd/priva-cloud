"""Small asyncio queue with both item-count and retained-byte backpressure."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class QueueByteLimitError(asyncio.QueueFull):
    """A single payload can never fit within the queue's byte budget."""


def approximate_size(value: object) -> int:
    """Return a deterministic retained-size proxy for stream payloads."""
    if value is None:
        return 1
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except Exception:
        return len(repr(value).encode("utf-8", errors="replace"))


class BoundedAsyncQueue(Generic[T]):
    """An asyncio-compatible FIFO bounded by count and approximate bytes.

    The queue has one logical FIFO and preserves normal ``put`` backpressure.
    It intentionally implements only the small ``asyncio.Queue`` surface used
    by the agent stream: put/get, their nowait forms, size, empty and full.
    """

    def __init__(
        self,
        *,
        maxsize: int,
        max_bytes: int,
        size_fn: Callable[[T], int] = approximate_size,
    ) -> None:
        if maxsize <= 0 or max_bytes <= 0:
            raise ValueError("bounded queue limits must be positive")
        self.maxsize = maxsize
        self.max_bytes = max_bytes
        self._size_fn = size_fn
        self._items: asyncio.Queue[tuple[T, int]] = asyncio.Queue(maxsize=maxsize)
        self._buffered_bytes = 0
        self._byte_space = asyncio.Event()
        self._byte_space.set()
        # Stream receivers are normally the sole producer, but permission
        # callbacks can enqueue concurrently. Serialize the check+insert pair.
        self._put_lock = asyncio.Lock()

    @property
    def buffered_bytes(self) -> int:
        return self._buffered_bytes

    def qsize(self) -> int:
        return self._items.qsize()

    def empty(self) -> bool:
        return self._items.empty()

    def full(self) -> bool:
        return self._items.full() or self._buffered_bytes >= self.max_bytes

    def _item_size(self, item: T) -> int:
        size = max(1, int(self._size_fn(item)))
        if size > self.max_bytes:
            raise QueueByteLimitError(
                f"queue item is {size} bytes; limit is {self.max_bytes} bytes"
            )
        return size

    async def put(self, item: T) -> None:
        size = self._item_size(item)
        async with self._put_lock:
            while self._buffered_bytes + size > self.max_bytes:
                self._byte_space.clear()
                # Recheck after clearing so a consumer cannot race a wakeup
                # between the predicate and Event reset.
                if self._buffered_bytes + size <= self.max_bytes:
                    break
                await self._byte_space.wait()
            await self._items.put((item, size))
            self._buffered_bytes += size

    def put_nowait(self, item: T) -> None:
        size = self._item_size(item)
        if self._buffered_bytes + size > self.max_bytes:
            raise asyncio.QueueFull
        self._items.put_nowait((item, size))
        self._buffered_bytes += size

    async def get(self) -> T:
        item, size = await self._items.get()
        self._buffered_bytes = max(0, self._buffered_bytes - size)
        self._byte_space.set()
        return item

    def get_nowait(self) -> T:
        item, size = self._items.get_nowait()
        self._buffered_bytes = max(0, self._buffered_bytes - size)
        self._byte_space.set()
        return item

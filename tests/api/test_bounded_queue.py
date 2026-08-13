from __future__ import annotations

import asyncio

import pytest

from priva_agent_runner.services.claude_sdk.bounded_queue import (
    BoundedAsyncQueue,
    QueueByteLimitError,
)
from priva_agent_runner.services.claude_sdk.retry import should_retry_exception
from priva_agent_runner.services.claude_sdk.session_runtime_pool import (
    RuntimeDisconnectedError,
    RuntimeFrameLimitError,
    RuntimePoolCapacityError,
    SessionRuntimeBusyError,
)


@pytest.mark.asyncio
async def test_byte_limit_backpressures_until_consumer_releases_space():
    queue = BoundedAsyncQueue[str](
        maxsize=4,
        max_bytes=5,
        size_fn=len,
    )
    await queue.put("abc")
    blocked = asyncio.create_task(queue.put("def"))
    await asyncio.sleep(0)
    assert not blocked.done()
    assert queue.buffered_bytes == 3

    assert await queue.get() == "abc"
    await blocked
    assert queue.buffered_bytes == 3
    assert await queue.get() == "def"
    assert queue.buffered_bytes == 0


@pytest.mark.asyncio
async def test_count_limit_and_nowait_methods_match_asyncio_queue_contract():
    queue = BoundedAsyncQueue[str](maxsize=1, max_bytes=10, size_fn=len)
    queue.put_nowait("one")
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait("two")
    assert queue.qsize() == 1
    assert queue.get_nowait() == "one"
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


@pytest.mark.asyncio
async def test_single_item_larger_than_byte_budget_is_rejected():
    queue = BoundedAsyncQueue[str](maxsize=2, max_bytes=3, size_fn=len)
    with pytest.raises(QueueByteLimitError):
        await queue.put("oversized")
    assert queue.empty()


def test_resource_admission_and_byte_limits_are_not_retried():
    assert not should_retry_exception(RuntimePoolCapacityError("full"))
    assert not should_retry_exception(SessionRuntimeBusyError("busy"))
    assert not should_retry_exception(QueueByteLimitError("large"))
    assert not should_retry_exception(RuntimeFrameLimitError("large"))
    assert should_retry_exception(RuntimeDisconnectedError("eof"))
    assert should_retry_exception(RuntimeError("transient CLI failure"))

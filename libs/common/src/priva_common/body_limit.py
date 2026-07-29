"""Global request-body ceiling, enforced before anything parses the body.

Per-route checks cannot bound an upload. With ``UploadFile``, Starlette's
multipart parser runs during dependency resolution — *before* the endpoint
function — and streams every file part into a ``SpooledTemporaryFile`` that
rolls to disk past 1 MB, with no size limit of its own (``max_part_size``
applies only to non-file parts). So by the time a handler can call
``file.read(MAX + 1)``, the whole body is already written to the container's
/tmp. Those bounded reads cap MEMORY; they do nothing about disk.

This middleware sits outermost and counts bytes as they arrive on the ASGI
receive channel, so an oversized request is refused while it is still being
uploaded — nothing is parsed, nothing is spooled.

``Content-Length`` is checked first purely as a cheap early exit. It is
client-supplied and absent on chunked transfers, so it is never the only check:
the running byte count below is what actually enforces the limit.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging import get_app_logger

logger = get_app_logger(__name__)


class _TooLarge(Exception):
    """Raised inside the wrapped receive; caught below to emit a 413."""


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Send) -> None:
        body = (
            f"Request body exceeds the {self.max_bytes // (1024 * 1024)}MB limit"
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Cheap early exit on a declared length. Not authoritative.
        declared = dict(scope.get("headers") or {}).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    logger.warning("rejecting {} — declared body {} bytes",
                                   scope.get("path"), int(declared))
                    return await self._reject(send)
            except ValueError:
                pass  # malformed header; the running count still applies

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            logger.warning("rejecting {} — body exceeded {} bytes while streaming",
                           scope.get("path"), self.max_bytes)
            if not started:
                await self._reject(send)
            # If the response was already begun there is nothing valid left to
            # send; dropping the connection is the only honest outcome.


__all__ = ["MaxBodySizeMiddleware"]

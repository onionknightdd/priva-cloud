"""Browser-facing security headers for the control-panel (which also serves the SPAs).

The app shipped with only an access-log middleware: no CSP, no framing control,
no MIME-sniff protection, no HSTS. That matters more than usual here because the
SPA keeps its bearer token in localStorage, so any script that executes in this
origin can read it — and the same origin renders user-supplied file previews.

CSP is deliberately conservative but Vite-compatible: the built bundle injects
styles at runtime, so `style-src` allows inline. `script-src 'self'` still blocks
injected inline script, which is the token-theft vector that matters.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",   # Vite injects styles at runtime
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self' ws: wss:",        # SPA opens WebSockets to its own origin
    # The SPA frames ITSELF: SplitSessionView renders each pane as an iframe at
    # window.location.origin, and BrowserViewport renders agent HTML via srcdoc.
    # 'none' here (or X-Frame-Options: DENY) breaks split panes outright.
    "frame-ancestors 'self'",
    "frame-src 'self' blob: data:",
    # Excalidraw feature-detects OffscreenCanvas through a data: worker, and
    # several previews build blob: workers.
    "worker-src 'self' blob: data:",
    "base-uri 'self'",
    "object-src 'none'",
    "form-action 'self'",
))

_HSTS = b"max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Pure-ASGI so it also covers WebSocket and streaming responses.

    ``csp_enforce`` defaults to False: the policy ships as Report-Only so
    turning it on cannot white-screen a working SPA. Collect violations first,
    then flip PRIVA_EDGE__CSP_ENFORCE=true. The other headers are safe to
    enforce immediately.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False,
                 csp_enforce: bool = False) -> None:
        self.app = app
        csp_header = (b"content-security-policy" if csp_enforce
                      else b"content-security-policy-report-only")
        self._headers: list[tuple[bytes, bytes]] = [
            (csp_header, CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            # SAMEORIGIN, not DENY — see frame-ancestors above.
            (b"x-frame-options", b"SAMEORIGIN"),
            (b"referrer-policy", b"no-referrer"),
        ]
        if hsts:
            # Only meaningful behind TLS; asserting it over plain HTTP would pin
            # browsers to a scheme the edge may not serve yet.
            self._headers.append((b"strict-transport-security", _HSTS))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] += [h for h in self._headers if h[0] not in existing]
            await send(message)

        await self.app(scope, receive, _send)

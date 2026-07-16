"""Dial the account's pod — wake it, POST the DM to ``/run/stream``, and reduce the
SSE reply to a ``RunOutcome`` (design Flow 1). Reuses the scheduler's exact
wake+runner-token seam; only the endpoint differs (``/run/stream`` instead of
``/scheduled-run``) because the connector needs the streamed reply, not just a 202.

The ar pod's ``get_current_user`` IS the runner-token check (``deps.require_account``),
so the minted ``X-Priva-Runner-Token`` authenticates the call with no extra lane.
"""

from __future__ import annotations

import httpx

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.models.agent import AgentRunRequest
from priva_common.runner_token import mint

from . import wake
from .sse import StreamState, iter_sse, step

logger = get_app_logger(__name__)


class RunnerDialer:
    def __init__(self, *, waker=None, transport: "httpx.AsyncBaseTransport | None" = None):
        # Both seams exist for tests: a fake waker and an httpx MockTransport.
        self._waker = waker or wake.wake_and_wait
        self._transport = transport

    def _url(self, account_id: str) -> str:
        s = get_settings()
        ns = s.kubernetes.namespace_tenants
        port = s.kubernetes.runner_service_port
        return f"http://ar-{account_id}.{ns}.svc:{port}/api/sandbox/agent/run/stream"

    async def run(
        self,
        account_id: str,
        username: str | None,
        *,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        do_wake: bool = True,
        state: "StreamState | None" = None,
    ) -> StreamState:
        """Wake + dial + relay. Folds the stream into ``state`` (the worker passes the same
        object its card ticker renders, so live content shows up as it patches) and returns
        it — the single source of truth for the final card; every failure path folds its
        error into that same state, so the card can't disagree with the outcome.
        ``session_id=None`` starts a fresh SDK session (the id is captured from the
        ``result`` event). ``enable_permission_feedback`` is left False for the MVP: the run
        auto-denies gated tools and drops AskUserQuestion so it never blocks on a human the
        connector can't yet prompt (permission cards are a later milestone)."""
        if state is None:
            state = StreamState()
        if do_wake and not await self._waker(account_id):
            state.is_error = True
            state.error_text = "wake_failed"
            return state

        body = AgentRunRequest(
            message=prompt,
            session_id=session_id,
            model=model,
            enable_permission_feedback=False,
        ).model_dump(mode="json", exclude_none=True)
        headers = {"X-Priva-Runner-Token": mint(account_id, username or "")}

        try:
            async with httpx.AsyncClient(
                trust_env=False, transport=self._transport,
                # No read timeout: a run streams for as long as the turn takes.
                timeout=httpx.Timeout(None, connect=10.0),
            ) as cx:
                async with cx.stream("POST", self._url(account_id), json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode(errors="replace")[:200]
                        logger.warning("dial {} -> {}: {}", account_id, resp.status_code, detail)
                        state.is_error = True
                        state.error_text = f"dial {resp.status_code}"
                        return state
                    async for event, data_str in iter_sse(resp):
                        step(state, event, data_str)   # ticker patches; dial just folds
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("dial transport error account={}: {}", account_id, exc)
            state.is_error = True
            state.error_text = state.error_text or "dial_failed"
            return state

        return state

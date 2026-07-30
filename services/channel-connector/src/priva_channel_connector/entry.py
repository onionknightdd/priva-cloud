"""channel-connector launcher (entry-point group priva_cloud.services).

Composes the dataplane client (gRPC — this service is a data-plane *client*, never a
repo owner), the lark WS transport factory, the wake+dial dialer and the reconcile
engine, then serves the internal API; the engine's poll loop rides the app lifespan.
"""

from __future__ import annotations


def _quiet_noisy_loggers() -> None:
    """The WS / HTTP / gRPC stacks emit DEBUG through the shared intercept handler and
    drown the connector log (raw ws frames, keepalive pings, httpcore chatter). Cap them
    at WARNING so our own INFO lines (inbound / run / arm) are readable. Our modules stay
    at INFO."""
    import logging

    for name in ("websockets", "websockets.client", "websockets.server",
                 "websockets.protocol", "asyncio", "httpx", "httpcore",
                 "urllib3", "requests", "grpc", "lark_oapi"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    from priva_common.config import get_settings
    from priva_common.dataplane import get_client
    from priva_common.logging import configure_logging

    from . import config
    from .api import create_app
    from .dial import RunnerDialer
    from .engine import ReconcileEngine
    from .lark_ws import make_lark_transport

    settings = get_settings()
    configure_logging(settings)
    _quiet_noisy_loggers()

    client = get_client()
    engine = ReconcileEngine(
        client,
        make_lark_transport,
        RunnerDialer(account_getter=client.accounts.get),
    )
    app = create_app(engine)

    uvicorn.run(app, host=config.api_host(), port=config.api_port(), log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

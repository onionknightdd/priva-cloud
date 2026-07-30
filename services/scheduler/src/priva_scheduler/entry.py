"""scheduler launcher (entry-point group priva_cloud.services).

Composes the dataplane client (gRPC transport in k8s — this service is a
data-plane *client*, never a repo owner), the wake+dial dispatcher and the
engine, then serves the internal API; the engine's clock + sweep loops ride
the app lifespan. Replica id = HOSTNAME (the pod name) — the claim column's
``claimed_by``.
"""

from __future__ import annotations

import os


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    from priva_common.config import get_settings
    from priva_common.dataplane import get_client
    from priva_common.logging import configure_logging

    from .api import create_app
    from .dispatch import WakeDialDispatcher
    from .engine import SchedulerEngine

    settings = get_settings()
    configure_logging(settings)

    client = get_client()
    engine = SchedulerEngine(
        client,
        WakeDialDispatcher(account_getter=client.accounts.get),
        replica_id=os.environ.get("HOSTNAME", "scheduler-0"),
    )
    app = create_app(engine)

    host = os.environ.get("SCHEDULER_HOST", "0.0.0.0")
    port = int(os.environ.get("SCHEDULER_PORT", str(settings.scheduler.api_port)))
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Per-run capability for scheduler callback delivery.

The account-scoped agent-runner service token is intentionally readable by
tenant code in that pod.  It therefore authenticates the workload/account but
cannot, on its own, authorize proactive external messages.  The control-plane
scheduler mints this second, short-lived capability for one exact run and keeps
it in the in-process dispatch frame; job subprocesses never receive it in their
environment.
"""

from __future__ import annotations

from .service_identity import sign, verify as _verify

TOKEN_TYPE = "scheduler-callback"
HEADER = "X-Priva-Scheduler-Callback-Token"


def mint(
    account_id: str,
    run_id: str,
    job_id: str,
    *,
    ttl_seconds: int,
) -> str:
    return sign(
        {"account_id": account_id, "run_id": run_id, "job_id": job_id},
        typ=TOKEN_TYPE,
        ttl_seconds=ttl_seconds,
    )


def verify(token: str) -> dict:
    claims = _verify(token, typ=TOKEN_TYPE)
    for field in ("account_id", "run_id", "job_id"):
        value = claims.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"scheduler callback token missing {field}")
    return claims


__all__ = ["HEADER", "TOKEN_TYPE", "mint", "verify"]

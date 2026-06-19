"""Redis key catalog — one definition site so keys never drift across services.

Phase-0 increment-7 (code-split.md §6 line 251 — NEW, pure addition, no shim).
Every service that touches Redis (scheduler, control-panel/brain, channel-connector,
operator, agent-runner) builds its keys from here instead of formatting strings
inline, so a rename happens in exactly one place. The monolith does not use Redis
yet, so nothing imports this module today; it is seeded now and filled in as the
data plane lands (Phase 1, data-spine §4).

Tiers (data-spine §4): **T1** = durable (the inbox — the only durable dispatch
write); **T2** = ephemeral/regenerable (routes, locks, claims, mirrors). The
``#N`` references below point at the numbered keys in data-spine §4.

Shapes are taken verbatim from the component docs:
- scheduler.md §5.1/§5.4 (inbox, job-fire claim, session-lock mirror, route)
- operator.md §3.1/§4 (awake:lock, wake:pod nudge, approval:index)
- multi-tenant-platform.md §202 (channelconn:lease)
- control-panel.md §0.1/§9 + agent-gateway §7.3 (approval:index, in:reply)
"""
from __future__ import annotations

# --- TTLs / timing (ms unless named _S) -------------------------------------
# Wakers serialize the CR-patch behind this lock; it self-heals if a waker dies
# mid-patch (data-spine §4 #10). ~10s.
AWAKE_LOCK_TTL_MS = 10_000
# Socket-owner lease: 10s TTL refreshed by a 3s heartbeat (multi-tenant §202).
CHANNELCONN_LEASE_TTL_MS = 10_000
CHANNELCONN_LEASE_HEARTBEAT_MS = 3_000
# Per-fire claim pre-filter, generous enough to outlast a dispatch (scheduler §5
# `PX 120000`).
JOB_FIRE_CLAIM_TTL_MS = 120_000


# --- T1 — durable dispatch --------------------------------------------------
def inbox(account_id: str) -> str:
    """Durable per-account dispatch list — ``RPUSH``ed by scheduler/brain, the
    pod drains it FIFO (data-spine §4 #1; scheduler.md §5.1)."""
    return f"inbox:{account_id}"


def inbox_dedup(account_id: str) -> str:
    """Coalesce marker for inbox writes (data-spine §4 #2; scheduler.md §5.1)."""
    return f"inbox:dedup:{account_id}"


# --- T2 — routing / liveness ------------------------------------------------
def route(account_id: str) -> str:
    """Pod route + state; present ⇒ pod awake (data-spine §4 #8). The brain
    returns its ``ip:port`` as the ext_proc destination (control-panel.md §9)."""
    return f"route:{account_id}"


def awake_lock(account_id: str) -> str:
    """``SET NX PX~10s`` — serializes IM + scheduler + replicas so the
    AgentTenant CR is patched once per wake (data-spine §4 #10)."""
    return f"awake:lock:{account_id}"


def lock_session(session_uuid: str) -> str:
    """Session single-writer **mirror** (best-effort; the in-pod asyncio.Lock is
    authoritative). Read for SKIP / scale-to-zero gating (data-spine §4 #11)."""
    return f"lock:session:{session_uuid}"


# --- T2 — scheduler claim ---------------------------------------------------
def job_fire(job_id: str, fire_epoch: int | str) -> str:
    """Per-fire claim pre-filter — ``SET NX`` decides which replica dispatches a
    given fire (data-spine §4 #14; scheduler.md §5 `:156`)."""
    return f"job:{job_id}:fire:{fire_epoch}"


# --- T2 — approvals / reply relay -------------------------------------------
def approval_index(request_id: str) -> str:
    """HASH mapping a permission request → the exact pod handling it; the brain
    ``HGETALL``s it to relay an approval cross-replica (agent-gateway §7.3)."""
    return f"approval:index:{request_id}"


def reply_inbox(request_id: str) -> str:
    """Cross-replica relay channel for a human's approval/reply when the holding
    replica differs from the receiving one (agent-gateway §7.3)."""
    return f"in:reply:{request_id}"


# --- T2 — channel-connector lease -------------------------------------------
def channelconn_lease(channel: str, bot_id: str) -> str:
    """Single socket owner per (channel, bot) — lease with TTL + heartbeat; on
    owner death another replica takes over (multi-tenant §202)."""
    return f"channelconn:lease:{channel}:{bot_id}"


# --- Pub/sub channels (nudges + control) ------------------------------------
# Reconcile nudge only — the CR patch is the authoritative scale-up trigger;
# a dropped nudge just defers to the next periodic reconcile (data-spine §4 #12).
WAKE_POD_CHANNEL = "wake:pod"
# Admin control fan-out to scheduler replicas (scheduler.md §5).
SCHEDULER_RELOAD_CHANNEL = "scheduler:reload"
SCHEDULER_TRIGGER_CHANNEL = "scheduler:trigger"

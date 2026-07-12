# Phase 4 — Lift connector + scheduler (sever the inversion)

**Status:** not started
**Branch:** `main`     **Depends on:** Phase 2 (a pod to dispatch to), Phase 3 (the brain RPC to call)
**Canonical refs:** `code-split.md` §7 (the inversion), §9 (stateful → stateless); `agent-gateway.md` §4.4 (option A — connector → brain RPC → dial pod); components/`scheduler.md` (leaderless exactly-once)

## 1. Objective & scope

Cut the seam the whole split hangs off. Today the scheduler daemon and the channels daemon **import `claude_sdk` and run the agent in-process** (`code-split.md` §7). This phase strips that: both become **dispatchers** — they wake the pod and hand it the turn. **This is the cutover** — after it, the monolith no longer owns the agent run for IM/cron.

**In scope:** the channel-connector deployable (Redis lease + brain RPC + dial-pod + IM fan-out); the scheduler deployable (leaderless fire→claim→wake→dispatch); removing `claude_sdk` from both; moving the OpenClaw lifespan bridges into the connector (§9).
**Out of scope:** the operator that scales pods (Phase 5) — until then, the dev wake impl (always-on pod, §13).

## 2. Design / approach

**The inversion** (`code-split.md` §7) — the two in-process callers to sever:
- **Scheduler** `daemon.py:299-305` → stop importing `claude_sdk`; RPUSH `inbox:{account}` + CR-patch wake.
- **Connector** `daemon.py:786-794` → stop importing `claude_sdk`; call the brain RPC (`RouteTurn`, `:8081`), then **dial the woken pod directly** (agent-gateway §4.4 option A — inbound IM bypasses agentgateway), then relay + fan out.

**Connector specifics:** owns the outbound WeCom/OpenClaw socket under a **Redis lease** (one owner per bot — §9). The OpenClaw bridges currently in `main.py` lifespan (`:133-153`) move here under the lease. **Scheduler specifics:** leaderless exactly-once (claim via Redis), then dispatch (scheduler.md). Stateless (§9): in-process `conn.sessions`/`conn.pending` maps → central `channel_binding` + `approval:index`.

## 3. Actions (checklist)

- [ ] Stand up `services/channel-connector`: Redis lease, brain `RouteTurn` RPC client, dial-pod, IM fan-out.
- [ ] Move OpenClaw lifespan bridges (`main.py:133-153`) into the connector under the lease.
- [ ] Stand up `services/scheduler`: leaderless claim + RPUSH `inbox` + CR-patch wake + dispatch.
- [ ] **Remove `claude_sdk` imports** from both daemons (the acceptance test, §7).
- [ ] Replace in-process maps with `channel_binding` + `approval:index` (§9).

## 4. Acceptance criteria

- **Neither connector nor scheduler imports `claude_sdk`** (grep both — the §7 acceptance test):
  ```bash
  grep -rn "claude_sdk" services/channel-connector services/scheduler && echo "STILL COUPLED" || echo "INVERSION SEVERED"
  ```
- An IM turn and a cron turn both **run on the pod** (dispatched, not in-process), end-to-end locally.

## 5. Open items resolved here

- _(none new — connector→brain call already locked as option A in `agent-gateway.md` §4.4.)_

## 6. Verification log (append-only)

- **2026-07-13 — Scheduler half (Phase 4a) DONE; E2E gate PASSED on minikube.**
  - Build order executed per `docs/scheduler-implementation-design.md` §14: dataplane
    (`job_fire` + 15-RPC `SchedulerService` served over gRPC + real client/converters) →
    runner (`POST /api/sandbox/agent/scheduled-run` 202/409/429 admission, three executors
    through `run_registry`, D14 caps, D15 boot prune, FinishRun carries session_id) →
    `services/scheduler` deployable (engine/claim/wake+dial/reconcile/internal API + RBAC
    `agenttenants` patch+status-read only) → 7 MCP tools + user CRUD API → UI (user
    master-detail page + sidebar ⏰ + admin Dashboard→Scheduler master-detail) → cutover.
  - Tests: 213 passed / 4 skipped across `tests/api` + `tests/scheduler` +
    `tests/control_panel`, SQLite + Postgres both exercised (claim uniqueness incl.
    8-thread race → exactly one winner).
  - E2E (minikube): created `e2e-gate1` (interval 1 min, user_script) for a
    **scaled-to-zero** account → armed ≤30 s → fired → claim won → CR wake scaled the pod
    0→1 → 202 admission → script ran → pod wrote `FinishRun(success,
    result_summary="E2E OK from the scheduler spine")`;
    `priva_scheduler_fires_total{outcome="dispatched"} 1`. Job deleted afterwards; the
    run record survived with `job_id=''` (FK SET NULL — the ALL RUNS orphan home).
  - Acceptance grep: `grep -rn claude_sdk services/scheduler/src/` → no hits (the
    scheduler executes nothing).
  - Monolith daemon retired: `priva/bin/server.sh` no longer launches
    `start_scheduler` (the inversion severed); `bash -n` clean.

## 7. Status & handoff notes

**Scheduler half (4a): DONE 2026-07-13** — see §6. The channel-connector half (4b) remains;
its dispatch seam (`Dispatcher` protocol in `services/scheduler/src/priva_scheduler/dispatch.py`)
and the parked Redis-inbox upgrade are documented in `docs/scheduler-implementation-design.md`
§15. Original note (connector-first) was inverted in execution: the scheduler shipped first. **First action:** lift the connector first (it exercises the brain RPC + dial-pod path end-to-end for IM), prove one IM turn runs on the pod, then do the scheduler. After this phase the monolith no longer runs IM/cron turns — the boot-check stops being the regression gate; the functional dispatch path is.

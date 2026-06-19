# Phase 3 — control-panel / brain

**Status:** not started
**Branch:** `split/phase-3`     **Depends on:** Phase 1 (admin API on the data plane), Phase 2 (a pod to steer)
**Canonical refs:** components/`control-panel.md` (§0.1 one-process / three-listeners), `agent-gateway.md` §4.4 (the factored `resolve·mint·wake·steer`), `code-split.md` §5 (dual-face router split), §9 (stateless)

## 1. Objective & scope

Build the control panel: the **brain** (ext_proc routing) + admin/auth/config faces + serving the `web` builds. It holds **no agent state** — it resolves, mints the signed header, wakes the pod, and steers the edge; the pod runs the turn.

**In scope:** the ext_proc brain on `:9000`; the **single factored** `resolve·mint·wake·steer` function shared by the browser path (ext_proc) and the IM path (internal RPC, agent-gateway §4.4 option A); admin/auth/config routers on the data plane; faces (`:8080` edge-reachable) vs internal (`:8081` cluster-only) listener split; serving `web`.
**Out of scope:** the connector/scheduler that *use* the internal RPC (Phase 4); the operator that the wake step calls in prod (Phase 5 — until then, the dev wake impl, §13).

## 2. Design / approach

**One process, three listeners** (`control-panel.md` §0.1): `:9000` gRPC ext_proc (protocol-forced split), `:8080` HTTP faces (edge-reachable), `:8081` HTTP internal (cluster-only; the connector→brain routing RPC, option A). The faces/internal split is a **NetworkPolicy boundary**, not a separate process.

**The factored core** (agent-gateway §4.4): `resolve·mint·wake·steer` is **one function** called by both the ext_proc handler (browser) and the internal-RPC handler (IM). `RouteTurn(surface_type, surface_user_id, text) → {session_uuid, account_id, pod_endpoint}`. Brain mints the signed `account_id` the pod verifies (Phase 2). Wake/steer sit behind dev/prod-swappable interfaces (§13 design rule): dev = always-on pod + localhost steer; prod = CR-patch wake + EPP steer.

The dual-face routers SPLIT per `code-split.md` §5 — admin/auth/config to the panel; run/stream to the pod. Stateless (§9): no per-process session maps.

## 3. Actions (checklist)

- [ ] Implement the factored `resolve·mint·wake·steer` fn (one impl, two callers).
- [ ] ext_proc brain on `:9000` — steers the browser turn to the woken pod.
- [ ] Internal routing RPC on `:8081` (`RouteTurn`) — the IM seam for the connector (option A).
- [ ] Admin/auth/config routers (§5) on the data plane; faces on `:8080`.
- [ ] Wake/steer behind dev/prod interfaces (§13) — wire the dev impl now.
- [ ] Serve the `web` build(s).

## 4. Acceptance criteria

- The brain **steers a browser turn** end-to-end: resolve → mint → wake → steer → pod runs → stream back.
- Admin API operates on the data plane (no file stores).
- Monolith still boots (last phase it does): `PYTHONPATH=priva:libs/common/src python -c "import api.main"`.

## 5. Open items resolved here (decide during this phase)

- **OQ-5 — `/models` list source under BYOK:** static price-card/config (control-panel) **vs.** probe the user's provider (pod). Decide and record.
- **OQ-4** (if deferred from Phase 2): MCP `/validate` locus — the control-panel sandbox alternative.

## 6. Verification log (append-only)

- _(empty — populate as you execute)_

## 7. Status & handoff notes

Not started. **First action:** build the factored `resolve·mint·wake·steer` fn with the **dev** wake/steer impls (always-on pod, localhost) and prove a browser turn locally; the prod (CR-patch/EPP) impls land with the operator in Phase 5.

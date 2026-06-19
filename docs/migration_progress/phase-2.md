# Phase 2 — agent-pod

**Status:** not started
**Branch:** `split/phase-2`     **Depends on:** Phase 1 (the pod reads/writes via the data plane)
**Canonical refs:** `code-split.md` §4.1 (`claude_sdk` → agent-pod), §4.3 (RELOCATE scheduler executors), §4.4/§4.5 (hooks/mcp/skills SPLIT), §9 (permission singleton → in-pod); components/`agent-pod.md` (§13-1 signed-header verify)

## 1. Objective & scope

Package the agent runtime as a standalone deployable — **the only thing that runs the agent**. It serves a turn behind a signed `account_id` header, executes hooks/mcp/skills, owns the pty/files, and holds the permission rendezvous in-process.

**In scope:** `claude_sdk` + the pod-internal agent router; hooks/mcp/skills **execution**; pty/files; the RELOCATEd scheduler executors (they run the agent, so they live in the pod — §4.3); signed-header read + JWKS verify; image build with bundled skills; `permission_coordinator` Future in-pod (§9).
**Out of scope:** the brain that *routes* to the pod (Phase 3); stripping the in-process callers from scheduler/connector (Phase 4) — until then the monolith still calls `service.py` in-process.

## 2. Design / approach

`service.py`'s `agent_run_events` is the runtime; this phase makes it shippable as the pod image (`code-split.md` §7 — "`service.py` ships only in the agent-pod image"). The hooks/mcp/skills routers SPLIT (§5): **execution** goes to the pod, **config/validation** may go elsewhere (see open items). `permission_coordinator.py`'s module-global Future must be **in-pod** (the rendezvous lives where the run lives; the brain only relays via `approval:index`, §9). The pod trusts the signed `account_id` minted by the brain and verified via JWKS (agent-pod §13-1).

## 3. Actions (checklist)

- [ ] Package `claude_sdk` + pod-internal agent router (run/stream/permission/fork/rewind) into `services/agent-pod`.
- [ ] Bring hooks/mcp/skills **execution** + pty/files into the pod.
- [ ] RELOCATE scheduler executors into the pod (§4.3).
- [ ] Read + JWKS-verify the signed `account_id` header (agent-pod §13-1); reject unsigned.
- [ ] Keep `permission_coordinator` Future in-pod (§9); relay via `approval:index`.
- [ ] Build the pod image (+ bundled skills).

## 4. Acceptance criteria

- The pod serves a turn **end-to-end behind a signed header** (run → stream → permission → result), standalone (`uv run` / image).
- Monolith still boots (Phase 3 still has it): `PYTHONPATH=priva:libs/common/src python -c "import api.main"`.

## 5. Open items resolved here (decide during this phase)

- **OQ-3 — `/clipath`** (single-machine CLI path config): likely **DELETE** — the pod bakes the CLI. Confirm nothing else reads it, then remove.
- **OQ-4 — MCP `/validate` + `/capabilities` locus:** agent-pod (creds live here) **vs.** a control-panel sandbox (Phase 3). Decide and record.
- **OQ-1 — skill / skill-hub file storage locus:** per-tenant PVC (pod-served, state-reader read) **vs.** a central skill store in the data plane. Interacts with Phase 5 state-reader — decide here, implement read path in Phase 5.

## 6. Verification log (append-only)

- _(empty — populate as you execute)_

## 7. Status & handoff notes

Not started. **First action:** carve `services/agent-pod` and move `claude_sdk` + the agent router into it (behind a shim so the monolith still imports), then add signed-header verification and prove a single turn locally before bringing hooks/mcp/skills.

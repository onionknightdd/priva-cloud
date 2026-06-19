# Phase 5 — operator + state-reader (NEW infra)

**Status:** not started
**Branch:** `split/phase-5`     **Depends on:** Phase 3 (brain wake seam), Phase 2 (the pod Deployment to scale)
**Canonical refs:** components/`operator.md` (kopf controller), components/`agent-pod.md` (scale-0↔1 / wake), `code-split.md` §8 (session JSONL stays PVC), §13 (dev/prod wake seam)

## 1. Objective & scope

Add the two pieces of infrastructure that have **no current code** (`code-split.md` §10, NEW): the **operator** (the kopf `AgentTenant` CRD controller — the *sole* pod scaler) and the **state-reader** (read-only JSONL transcript reader for wake-free session reads). This also swaps the brain's wake seam from its dev impl (always-on) to the **prod** impl (CR-patch).

**In scope:** the `AgentTenant` CRD + RBAC; the kopf controller reconciling CR → Deployment scale 0↔1; wiring the brain wake → CR-patch (the prod side of the §13 wake seam); the state-reader serving session reads from the PVC without waking the pod.
**Out of scope:** the full K8s manifest/NetworkPolicy/edge wiring (Phase 6).

## 2. Design / approach

**Operator** (operator.md): the only component allowed to scale agent pods. The brain requests a wake; the operator patches the `AgentTenant` CR; the Deployment goes 0→1. This is the **prod impl** of the wake seam that Phase 3 wired as a dev stub (§13 design rule — one seam, two impls, swapped by profile). **State-reader** (§8): session JSONL is **not** a store — it lives on the per-tenant PVC; the state-reader serves transcript reads **read-only** so listing/replaying a session never has to wake the pod (M5: no session table).

## 3. Actions (checklist)

- [ ] Define the `AgentTenant` CRD + operator RBAC.
- [ ] kopf controller: reconcile CR → Deployment scale 0↔1.
- [ ] Swap the brain wake seam to the prod (CR-patch) impl (§13).
- [ ] Stand up `services/state-reader`: RO JSONL reads from the PVC, wake-free.
- [ ] Resolve **OQ-1** read path (skills storage — pod PVC vs central store, decided in Phase 2).

## 4. Acceptance criteria

- **Scale 0↔1 via the CR**: applying/patching an `AgentTenant` brings the pod up/down.
- **Wake-free session reads**: the state-reader returns a session transcript with the pod scaled to zero.

## 5. Open items resolved here

- **OQ-1 — skill / skill-hub file storage** (if not closed in Phase 2): finalize the read path through the state-reader vs a central store.

## 6. Verification log (append-only)

- _(empty — populate as you execute)_

## 7. Status & handoff notes

Not started. Requires a local K8s (kind/k3d — local mode B, `code-split.md` §13) to exercise scale 0↔1, since the operator is the one K8s-coupled component. **First action:** define the CRD and a minimal kopf controller that scales a single tenant's Deployment 0↔1, then point the brain's wake seam at it.

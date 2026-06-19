# Phase 6 — K8s wiring

**Status:** not started
**Branch:** `main`     **Depends on:** Phases 1–5 (all deployables exist)
**Canonical refs:** `deploy/README.md`, `code-split.md` §13 (dev overlay), `agent-gateway.md` (the Rust edge + CRDs), components/`control-panel.md` §0.1 (faces/internal NetworkPolicy boundary)

## 1. Objective & scope

Containerize every deployable and wire the whole platform on Kubernetes so the **full request path runs**: browser / IM / cron → edge → pod. This is the last phase — it assembles the parts built in Phases 1–5.

**In scope:** per-service Dockerfiles; Deployments/Services/RBAC; the `AgentTenant` CRD + operator RBAC; agentgateway `Gateway`/`HTTPRoute`/`AgentgatewayPolicy`/`AgentgatewayBackend`; NetworkPolicies (the pod admits ingress only from agentgateway/mesh; faces vs internal split); the two `web` builds served by control-panel; the `dev/` overlay (kind/k3d) for full-fidelity local runs.
**Out of scope:** new business logic — if a behavior is missing, it belongs to its earlier phase, not here.

## 2. Design / approach

All manifests live under `deploy/` (`deploy/README.md`). The **NetworkPolicy boundary** enforces the architecture: the agent-runner admits ingress only from agentgateway/the mesh; control-panel's `:8080` faces are edge-reachable while `:8081` internal is cluster-only (`control-panel.md` §0.1). The Rust **agentgateway** is configured via its CRDs (`Gateway`/`HTTPRoute`/`AgentgatewayPolicy`/`AgentgatewayBackend`) — but note it also runs as a standalone binary with static config for local mode A (`code-split.md` §13). The `dev/` overlay gives full-fidelity local runs on kind/k3d (mode B).

## 3. Actions (checklist)

- [ ] Per-service Dockerfiles (each `uv` workspace member).
- [ ] Deployments + Services + RBAC for all 8 deployables.
- [ ] `AgentTenant` CRD + operator RBAC.
- [ ] agentgateway CRDs: `Gateway` / `HTTPRoute` / `AgentgatewayPolicy` / `AgentgatewayBackend`.
- [ ] NetworkPolicies (pod ingress = agentgateway/mesh only; faces vs internal).
- [ ] control-panel serves the two `web` builds.
- [ ] `deploy/dev/` overlay (kind/k3d) — mode B.

## 4. Acceptance criteria

- **Full path works on kind/k3d:** a browser turn, an IM turn, and a cron turn each traverse edge → brain → pod and return.
- Per-tenant scale-to-zero holds under the operator; NetworkPolicies deny pod ingress from anything but the edge/mesh.

## 5. Open items resolved here

- _(none new — all OQ items resolved in their earlier phases.)_

## 6. Verification log (append-only)

- _(empty — populate as you execute)_

## 7. Status & handoff notes

Not started. **First action:** stand up the `deploy/dev/` kind overlay with data-spine + one pod + control-panel and get a single browser turn through the edge, then add the connector/scheduler/operator and the NetworkPolicies. When the three full paths pass, the migration is complete.

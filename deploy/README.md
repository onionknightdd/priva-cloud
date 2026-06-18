# deploy/

Kubernetes manifests, CRDs, NetworkPolicies, and agentgateway config (Phase 6).

- per-service Deployments + Services + RBAC
- `AgentTenant` CRD + operator RBAC
- agentgateway `Gateway` / `HTTPRoute` / `AgentgatewayPolicy` / `AgentgatewayBackend`
- NetworkPolicies (the pod admits ingress only from agentgateway/mesh)
- `dev/` overlay (kind/k3d) for full-fidelity local runs (see code-split.md §13)

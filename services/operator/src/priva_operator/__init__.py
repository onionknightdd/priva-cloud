"""priva_operator — the kopf AgentTenant controller.

Sole scaler of per-account agent-runner Deployments (0<->1). Reconciles each
AgentTenant CR into a Deployment + Service, provisions the account's quota'd subdir on
the shared RWX export (via the storage backend), wakes on spec.wake.requestedAt
(materializing the per-pod credential Secret fetched from data-spine), and sweeps
idle pods back to zero. Depends on priva_common; never the reverse.
"""

from __future__ import annotations

GROUP = "priva.io"
VERSION = "v1alpha1"
PLURAL = "agenttenants"
KIND = "AgentTenant"

__all__ = ["GROUP", "VERSION", "PLURAL", "KIND"]

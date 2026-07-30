"""Shared Kubernetes lifecycle contract for AgentTenant resources."""

AGENTTENANT_FINALIZER = "kopf.zalando.org/KopfFinalizerMarker"

__all__ = ["AGENTTENANT_FINALIZER"]

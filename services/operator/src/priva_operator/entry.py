"""operator launcher (``priva-cloud operator`` / ``python -m priva_operator``).

Runs the embedded kopf operator scoped to the tenants namespace. kopf handles
auth (in-cluster ServiceAccount or local kubeconfig) and the event loop. Logs to
stderr so `kubectl logs` shows reconcile activity.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    import kopf

    from priva_common.config import get_settings
    from priva_operator import reconcile  # noqa: F401 — importing registers the @kopf handlers

    ns = get_settings().kubernetes.namespace_tenants
    # standalone=True: no KopfPeering CRD/RBAC needed (single operator, alpha).
    kopf.run(namespaces=[ns], clusterwide=False, standalone=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

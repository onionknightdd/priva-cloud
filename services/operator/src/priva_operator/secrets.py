"""Per-pod credential Secret: fetch the account's decrypted bundle from data-spine
(over gRPC) and materialize it as a K8s Secret the agent-runner pod mounts via
envFrom. Created at wake, deleted at sleep (minimal plaintext window)."""

from __future__ import annotations

from kubernetes import client

from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_operator import names
from priva_operator.kube import core

logger = get_app_logger(__name__)


def materialize(namespace: str, account_id: str, owner: dict) -> int:
    """Create/replace the ar-<account>-creds Secret from the data-spine bundle.
    Returns the number of keys injected."""
    rec = get_client().secrets.get(account_id)
    bundle = dict(rec.bundle) if rec and rec.bundle else {}
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": names.secret_name(account_id), "namespace": namespace,
                     "labels": names.labels(account_id), "ownerReferences": [owner]},
        "type": "Opaque",
        "stringData": bundle,
    }
    name = names.secret_name(account_id)
    try:
        core().create_namespaced_secret(namespace, body)
    except client.ApiException as exc:
        if exc.status == 409:
            core().replace_namespaced_secret(name, namespace, body)
        else:
            raise
    logger.info("materialized creds secret {} ({} keys)", name, len(bundle))
    return len(bundle)


def delete(namespace: str, account_id: str) -> None:
    try:
        core().delete_namespaced_secret(names.secret_name(account_id), namespace)
    except client.ApiException as exc:
        if exc.status != 404:
            raise

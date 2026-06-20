"""Deterministic per-account object names + labels (so the EPP and provisioner
agree without a registry: endpoint = ar-<account_id>.<ns>.svc:<port>)."""

from __future__ import annotations

from priva_operator import GROUP, KIND, VERSION


def deploy_name(account_id: str) -> str:
    return f"ar-{account_id}"


def svc_name(account_id: str) -> str:
    return f"ar-{account_id}"


def pvc_name(account_id: str) -> str:
    return f"ar-{account_id}-data"


def secret_name(account_id: str) -> str:
    return f"ar-{account_id}-creds"


def labels(account_id: str) -> dict[str, str]:
    # app=agent-runner is the InferencePool selector; the account label lets the
    # operator/EPP find the one pod for an account.
    return {"app": "agent-runner", "priva.io/account-id": account_id}


def owner_ref(name: str, uid: str) -> dict:
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": KIND,
        "name": name,
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": True,
    }

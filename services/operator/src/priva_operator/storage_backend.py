"""Per-account volume provisioning seam (shared-RWX-export model).

Isolation is a property of the *mount*: every runner mounts only its own subdir of one
shared RWX export, while a read-only reader can mount the whole tree (wake-free
aggregation — data-spine §3.8/§3.9). The per-account quota is enforced by the storage
backend, set at provision time = the account's ``volume_gb``.

Two impls behind one interface:

- ``NfsXfsBackend`` (dev, config key ``nfs_xfs``): one in-cluster NFS server. Because
  Docker Desktop's linuxkit kernel ships with NO filesystem quota format (``QFMT_*`` /
  ``XFS_QUOTA`` all unset), per-directory project quotas are impossible — so each account
  gets a **fixed-size ext4 loop image** instead (the filesystem size IS the hard cap;
  writes past it ENOSPC). The operator drives a **quota-manager** HTTP API on the NFS box
  that creates/mounts the image, chowns it (server-side — NFS ``root_squash`` blocks pod
  chown), and reports usage via ``statvfs``. This mirrors prod's *sized* subvolume model.
- ``CephFsBackend`` (prod, stub): a CephFS subvolume / NFS-EFS access-point whose size IS
  the quota and whose root IS the account subdir. Same interface; no privileged anything.

The runner Deployment renders its volume from the returned :class:`MountInfo`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

# httpx must NOT honor the host's system proxy for in-cluster service-to-service hops
# (see memory: harness-macos-system-proxy). The quota-manager is reached by Service DNS.
_HTTP_TIMEOUT = 5.0


@dataclass(frozen=True)
class MountInfo:
    """How ``kube._deployment_body`` should render the runner's ``/workspace`` volume.

    - ``shared_pvc_subpath`` (dev): one shared RWX PVC, scoped with ``subPath=<account_id>``.
    - ``csi_pv`` (prod): a per-account CSI PV rooted at the account subdir (parent
      unreachable by construction). ``pv_claim`` is the per-account claim name.
    """

    kind: str  # "shared_pvc_subpath" | "csi_pv"
    claim: str  # the PVC name to reference
    sub_path: str | None = None  # set for shared_pvc_subpath


class StorageBackend:
    """Interface (duck-typed). Impls are idempotent and fail-soft."""

    def provision(self, account_id: str, volume_gb: int) -> MountInfo:  # pragma: no cover
        raise NotImplementedError

    def set_quota(self, account_id: str, volume_gb: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def usage(self, account_id: str) -> tuple[int, int] | None:  # pragma: no cover
        """(used_bytes, limit_bytes) or None when the source is unavailable."""
        raise NotImplementedError


class NfsXfsBackend(StorageBackend):
    """Dev: drive the quota-manager on the in-cluster NFS server (per-account fixed-size
    ext4 loop images — the kernel has no quota format; see module docstring)."""

    def __init__(self, quota_manager_url: str, claim: str, uid: int, gid: int):
        self._base = quota_manager_url.rstrip("/")
        self._claim = claim
        self._uid = uid
        self._gid = gid

    def _client(self) -> httpx.Client:
        # trust_env=False: never route this internal Service hop through a host proxy.
        return httpx.Client(base_url=self._base, timeout=_HTTP_TIMEOUT, trust_env=False)

    def provision(self, account_id: str, volume_gb: int) -> MountInfo:
        # RAISE on failure: the subdir must exist + be chowned to the runner uid BEFORE the
        # pod mounts it (kubelet would otherwise create it root-owned → root_squash → the
        # non-root pod can't write). Letting this raise makes kopf retry `ensure`.
        body = {"volume_gb": int(volume_gb), "uid": self._uid, "gid": self._gid}
        with self._client() as c:
            c.post(f"/accounts/{account_id}", json=body).raise_for_status()
        logger.info("provisioned export subdir account={} quota={}Gi", account_id, volume_gb)
        return MountInfo(kind="shared_pvc_subpath", claim=self._claim, sub_path=account_id)

    def set_quota(self, account_id: str, volume_gb: int) -> None:
        try:
            with self._client() as c:
                c.put(f"/accounts/{account_id}/quota", json={"volume_gb": int(volume_gb)}).raise_for_status()
            logger.info("set quota account={} -> {}Gi", account_id, volume_gb)
        except Exception as exc:
            logger.warning("quota-manager set_quota failed account={}: {}", account_id, exc)
            raise

    def usage(self, account_id: str) -> tuple[int, int] | None:
        try:
            with self._client() as c:
                r = c.get(f"/usage/{account_id}")
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                d = r.json()
            return int(d["used_bytes"]), int(d["limit_bytes"])
        except Exception as exc:
            logger.debug("quota-manager usage failed account={}: {}", account_id, exc)
            return None


class CephFsBackend(StorageBackend):
    """Prod/UAT: one RWX PVC per account on a CephFS CSI StorageClass.

    Dynamic provisioning maps 1 PVC -> 1 CephFS subvolume whose size IS the hard quota
    (writes past it ENOSPC) and whose root IS the account dir — the runner mounts the
    claim at /workspace with no subPath, so sibling isolation holds by construction.
    Ownership comes from the pod securityContext (kube.py sets fsGroup=runner_gid with
    OnRootMismatch; cephfs CSI honors fsGroup), so no server-side chown is needed.
    Quota grow = PVC expand (the StorageClass must set allowVolumeExpansion); shrink is
    rejected, mirroring the dev backend's 409. No privileged anything.
    """

    def __init__(self, namespace: str, storage_class: str):
        self._ns = namespace
        self._sc = storage_class or None  # "" / None => cluster default StorageClass

    def _core(self):
        # Lazy import: kube.py imports this module (get_backend), so a module-level
        # import of priva_operator.kube would be circular.
        from priva_operator.kube import core
        return core()

    @staticmethod
    def _parse_gi(qty: str) -> int:
        """Parse the Gi sizes this backend writes; raise on anything else so a
        hand-edited PVC never silently reconciles against a misread number."""
        if not qty.endswith("Gi"):
            raise ValueError(f"unexpected PVC storage quantity {qty!r} (expected <n>Gi)")
        return int(qty[:-2])

    def _grow_if_needed(self, name: str, volume_gb: int, *, strict: bool) -> None:
        """Grow the claim to volume_gb if larger. strict=True raises on a shrink
        request (explicit quota edit); strict=False ignores it (idempotent ensure)."""
        core = self._core()
        pvc = core.read_namespaced_persistent_volume_claim(name, self._ns)
        current = self._parse_gi(pvc.spec.resources.requests["storage"])
        if volume_gb > current:
            core.patch_namespaced_persistent_volume_claim(
                name, self._ns,
                {"spec": {"resources": {"requests": {"storage": f"{volume_gb}Gi"}}}})
            logger.info("grew export PVC {} {}Gi -> {}Gi", name, current, volume_gb)
        elif volume_gb < current and strict:
            raise ValueError(
                f"shrink not supported: PVC {name} is {current}Gi, requested {volume_gb}Gi")

    def provision(self, account_id: str, volume_gb: int) -> MountInfo:
        from kubernetes.client import ApiException

        from priva_operator import names
        name = names.export_claim(account_id)
        body = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": name,
                         "labels": {**names.labels(account_id), "priva.io/role": "export"}},
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": f"{int(volume_gb)}Gi"}},
                **({"storageClassName": self._sc} if self._sc else {}),
            },
        }
        # RAISE on failure (like NfsXfsBackend): the claim must exist before the pod
        # renders, and letting this raise makes kopf retry `ensure`.
        try:
            self._core().create_namespaced_persistent_volume_claim(self._ns, body)
            logger.info("provisioned export PVC {} ({}Gi, sc={})", name, volume_gb, self._sc or "<default>")
        except ApiException as exc:
            if exc.status != 409:
                raise
            self._grow_if_needed(name, int(volume_gb), strict=False)
        return MountInfo(kind="csi_pv", claim=name)

    def set_quota(self, account_id: str, volume_gb: int) -> None:
        from priva_operator import names
        try:
            self._grow_if_needed(names.export_claim(account_id), int(volume_gb), strict=True)
        except Exception as exc:
            logger.warning("cephfs set_quota failed account={}: {}", account_id, exc)
            raise

    def usage(self, account_id: str) -> tuple[int, int] | None:
        # Used-bytes lives in kubelet/CSI volume stats we don't scrape yet; report
        # "unavailable" (the interface's fail-soft contract) rather than a guess.
        return None


def get_backend(settings) -> StorageBackend:
    k = settings.kubernetes
    if k.storage_backend == "cephfs":
        return CephFsBackend(k.namespace_tenants, k.cephfs_storage_class)
    return NfsXfsBackend(k.quota_manager_url, k.export_claim_name, k.runner_uid, k.runner_gid)

"""quota-manager — DEV-ONLY sidecar on the in-cluster NFS server.

Docker Desktop's linuxkit kernel ships WITHOUT filesystem quota formats
(``CONFIG_QFMT_V2`` / ``CONFIG_XFS_QUOTA`` unset), so XFS/ext4 *project* quotas are
impossible on this node. Instead we give each account a **fixed-size ext4 loop image**:
the filesystem size IS the hard cap (writes past it fail ENOSPC), needs no kernel quota
feature, and mirrors prod's per-account *sized* CephFS subvolume more faithfully than a
shared-tree project quota would.

Per account: ``/data/images/<account_id>.img`` (size = volume_gb), loop-mounted at
``/export/<account_id>`` (chowned to the runner uid; NFS root_squash blocks pod-side
chown). The NFS server exports ``/export`` with ``crossmnt`` so a single client mount
sees every account's submount → the runner subPaths into its own, the reader mounts the
whole tree. Grow is online (truncate + resize2fs); shrink isn't supported live.

The operator drives this over HTTP; prod swaps it for the Ceph mgr / CSI behind the same
StorageBackend interface. One process, low concurrency → a threading.Lock suffices.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

EXPORT = Path(os.environ.get("EXPORT_DIR", "/export"))
IMAGES = Path(os.environ.get("IMAGES_DIR", "/data/images"))
_lock = threading.Lock()
app = FastAPI(title="priva-quota-manager")

# Every id is interpolated straight into a filesystem path, so the pattern must be unable
# to express a separator or a traversal component (a leading alphanumeric rules out "..").
# Real ids are uuid4 hex; the wider class still accepts any Kubernetes object name.
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _checked(account_id: str) -> str:
    if not _ACCOUNT_ID.fullmatch(account_id):
        raise HTTPException(400, "invalid account id")
    return account_id


def _run(*cmd: str) -> str:
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True).stdout.strip()


def _img(account_id: str) -> Path:
    return IMAGES / f"{account_id}.img"


def _loopdev(img: Path) -> str | None:
    out = _run("losetup", "-j", str(img))  # "/dev/loop3: [..](/path) ..."
    return out.split(":", 1)[0] if out else None


def _ensure_mounted(account_id: str, gb: int, uid: int, gid: int) -> None:
    """Idempotent: create+format the image if absent, loop-mount it, chown the root."""
    IMAGES.mkdir(parents=True, exist_ok=True)
    img, sub = _img(account_id), EXPORT / account_id
    sub.mkdir(parents=True, exist_ok=True)
    if not img.exists():
        _run("truncate", "-s", f"{gb}G", str(img))
        _run("mkfs.ext4", "-q", "-F", "-m", "0", str(img))  # -m 0: no root-reserved blocks
    if not os.path.ismount(sub):
        _run("mount", "-o", "loop", str(img), str(sub))
    os.chown(sub, uid, gid)
    os.chmod(sub, 0o700)


def remount_existing() -> int:
    """On NFS-server boot, re-establish loop mounts for all images that survive on /data."""
    n = 0
    if not IMAGES.exists():
        return 0
    for img in IMAGES.glob("*.img"):
        sub = EXPORT / img.stem
        sub.mkdir(parents=True, exist_ok=True)
        if not os.path.ismount(sub):
            try:
                _run("mount", "-o", "loop", str(img), str(sub))
                n += 1
            except subprocess.CalledProcessError:
                pass
    return n


class AccountReq(BaseModel):
    volume_gb: int
    uid: int = 10001
    gid: int = 10001


class QuotaReq(BaseModel):
    volume_gb: int


@app.post("/accounts/{account_id}")
def create_account(account_id: str, req: AccountReq):
    _checked(account_id)
    with _lock:
        try:
            _ensure_mounted(account_id, int(req.volume_gb), req.uid, req.gid)
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"provision failed: {e.stderr.strip()}")
        # crossmnt reveals the new submount to NFSv4 without a re-export; refresh anyway,
        # but best-effort — exportfs warns (nonzero) on this kernel yet the mount is live.
        try:
            _run("exportfs", "-ra")
        except subprocess.CalledProcessError:
            pass
    return {"sub_path": account_id, "limit_bytes": int(req.volume_gb) * 1024 ** 3}


@app.delete("/accounts/{account_id}")
def delete_account(account_id: str):
    _checked(account_id)
    img, sub = _img(account_id), EXPORT / account_id
    with _lock:
        try:
            if os.path.ismount(sub):
                try:
                    _run("umount", str(sub))
                except subprocess.CalledProcessError:
                    # A runner's NFS client may still hold the submount; detach it from the
                    # tree now and let the kernel free the image on the last close.
                    _run("umount", "-l", str(sub))
            if os.path.ismount(sub):
                raise HTTPException(500, f"delete failed: {sub} is still mounted")
            if img.exists():
                dev = _loopdev(img)  # a plain umount auto-detaches; the lazy one does not
                if dev:
                    _run("losetup", "-d", dev)
            img.unlink(missing_ok=True)
            if sub.is_dir():
                sub.rmdir()
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"delete failed: {e.stderr.strip()}")
        except OSError as e:
            raise HTTPException(500, f"delete failed: {e}")
        try:
            _run("exportfs", "-ra")  # best-effort, same caveat as the create path
        except subprocess.CalledProcessError:
            pass
    return {"status": "deleted"}


@app.put("/accounts/{account_id}/quota")
def set_quota(account_id: str, req: QuotaReq):
    _checked(account_id)
    img = _img(account_id)
    with _lock:
        if not img.exists():
            raise HTTPException(404, "unknown account")
        cur = img.stat().st_size
        want = int(req.volume_gb) * 1024 ** 3
        if want > cur:  # grow online
            try:
                _run("truncate", "-s", f"{int(req.volume_gb)}G", str(img))
                dev = _loopdev(img)
                if dev:
                    _run("losetup", "-c", dev)      # refresh the loop device's capacity
                    _run("resize2fs", dev)
            except subprocess.CalledProcessError as e:
                raise HTTPException(500, f"grow failed: {e.stderr.strip()}")
        elif want < cur:
            # Live shrink of a mounted ext4 isn't possible — the runner holds the mount.
            # Dev limitation; prod (CephFS resize) can shrink. Keep the larger image.
            raise HTTPException(409, "shrink not supported on the dev loop backend")
    return {"limit_bytes": want}


def _usage(account_id: str) -> dict | None:
    sub = EXPORT / account_id
    if not os.path.ismount(sub):
        return None
    st = os.statvfs(sub)
    return {
        "used_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
        "limit_bytes": st.f_blocks * st.f_frsize,
    }


@app.get("/usage")
def usage_all():
    with _lock:
        out = {}
        if IMAGES.exists():
            for img in IMAGES.glob("*.img"):
                u = _usage(img.stem)
                if u:
                    out[img.stem] = u
    return out


@app.get("/usage/{account_id}")
def usage_one(account_id: str):
    _checked(account_id)
    with _lock:
        u = _usage(account_id)
    if not u:
        raise HTTPException(404, "no usage yet")
    return u


@app.on_event("startup")
def _startup():
    with _lock:
        remount_existing()


@app.get("/health")
def health():
    # Gate readiness on nfsd actually serving (threads > 0) — otherwise the pod can look
    # "ready" while the NFS mount the runners need is dead.
    try:
        threads = int(Path("/proc/fs/nfsd/threads").read_text().strip())
    except Exception:
        threads = 0
    if threads <= 0:
        raise HTTPException(503, "nfsd not serving")
    return {"ok": True, "export": str(EXPORT), "images_dir": str(IMAGES), "nfsd_threads": threads}

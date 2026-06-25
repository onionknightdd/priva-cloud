#!/bin/sh
# DEV-ONLY: an NFSv4 server over /export, where each account is a fixed-size ext4 loop
# image (the size IS the hard quota — no kernel quota feature needed; Docker Desktop's
# linuxkit kernel has none). The quota-manager (foreground) creates/mounts per-account
# images on demand; here we just stand up NFS and re-mount images that survived a restart.
# Requires a privileged container. NEVER ship to prod.
# NB: no `set -e` — this kernel rejects NFS v2/v3, so rpc.nfsd exits non-zero even when v4
# comes up fine; we tolerate that and rely on the /health readiness probe.
set -u

EXPORT_DIR="${EXPORT_DIR:-/export}"
IMAGES_DIR="${IMAGES_DIR:-/data/images}"
ROOT_IMG="${ROOT_IMG:-/data/export-root.img}"
mkdir -p "$EXPORT_DIR" "$IMAGES_DIR"

# The NFSv4 pseudo-root must live on an NFS-EXPORTABLE filesystem. The container root is
# overlayfs (NOT exportable — "does not support NFS export"), so back /export with its own
# small ext4 loop image. Per-account submounts (also ext4) then nest under it; crossmnt
# reveals them to clients.
if [ ! -f "$ROOT_IMG" ]; then
  truncate -s 256M "$ROOT_IMG"
  mkfs.ext4 -q -F "$ROOT_IMG"
fi
mountpoint -q "$EXPORT_DIR" || mount -o loop "$ROOT_IMG" "$EXPORT_DIR"

# Re-establish per-account loop mounts (images persist on the /data PVC across restarts).
for img in "$IMAGES_DIR"/*.img; do
  [ -e "$img" ] || continue
  acct="$(basename "$img" .img)"
  mkdir -p "$EXPORT_DIR/$acct"
  mountpoint -q "$EXPORT_DIR/$acct" || mount -o loop "$img" "$EXPORT_DIR/$acct" || \
    echo "[entrypoint] WARN: could not remount $img"
done

# NFSv4 server. crossmnt so the single client mount reveals every per-account submount
# (runner subPaths into its own; the reader mounts the whole tree). root_squash so the
# non-root pod can't chown — the quota-manager does it server-side.
modprobe nfsd 2>/dev/null || true
mount -t nfsd nfsd /proc/fs/nfsd 2>/dev/null || true
echo "$EXPORT_DIR *(rw,sync,no_subtree_check,root_squash,fsid=0,crossmnt,insecure)" > /etc/exports
# rpcbind must run BEFORE rpc.nfsd or the kernel refuses the nfsd socket
# ("writing fd to kernel failed: Connection refused").
rpcbind -w 2>&1 || rpcbind 2>&1 || echo "[entrypoint] WARN: rpcbind failed"
sleep 1
rpc.nfsd 8 2>&1 || echo "[entrypoint] WARN: rpc.nfsd returned nonzero"
rpc.mountd 2>&1 || echo "[entrypoint] WARN: rpc.mountd returned nonzero"
exportfs -ra 2>&1 || echo "[entrypoint] WARN: exportfs -ra returned nonzero"
echo "[entrypoint] nfsd threads: $(cat /proc/fs/nfsd/threads 2>/dev/null)"
echo "[entrypoint] NFS export state:"; exportfs -v 2>&1 || true
echo "[entrypoint] NFSv4 export ready at $EXPORT_DIR (fsid=0,crossmnt → clients mount server:/)"

# quota-manager (foreground; nfsd runs as kernel threads alongside).
exec uvicorn app:app --host 0.0.0.0 --port "${QUOTA_PORT:-8099}" --app-dir /opt/quota-manager

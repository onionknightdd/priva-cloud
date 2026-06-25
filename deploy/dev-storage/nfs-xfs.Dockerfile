# DEV-ONLY in-cluster NFSv4 server on an XFS export (project quotas) + the quota-manager
# in one image (containers don't share the loopback mount namespace, so the quota-manager
# runs in the same container as nfsd to see /export).
#
# NEVER ship this to prod — prod uses CephFS subvolumes / NFS-EFS access-points with no
# privileged container. See deploy/dev-storage/ and data-spine.md §3.8.
#
# Alpine base: apk (dl-cdn.alpinelinux.org) sidesteps the flaky deb.debian.org apt mirror.
FROM alpine:3.19

# nfs-utils (nfsd + exportfs), e2fsprogs (mkfs.ext4 + resize2fs for the per-account loop
# images), util-linux (real mount/losetup/mountpoint — busybox's are too limited for loop
# mounts), kmod (modprobe), python3 + fastapi/uvicorn for the quota-manager.
RUN apk add --no-cache nfs-utils rpcbind e2fsprogs util-linux kmod python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages fastapi "uvicorn[standard]"

COPY quota-manager/app.py /opt/quota-manager/app.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 2049 8099
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

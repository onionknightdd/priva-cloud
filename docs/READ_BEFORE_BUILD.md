# READ BEFORE BUILD & DEPLOY

Pre-flight for anyone building images or deploying Priva Cloud (dev/minikube **or**
UAT). These are the setup steps and gotchas that aren't obvious from the code and will
silently bite you if skipped. Read this once before your first build.

> Deployment *procedures* live elsewhere — this file is only the **setup + gotchas**:
> - Dev bring-up: `deploy/README.md`, `deploy/minikube/up.sh`
> - Helm install & values: `deploy/helm/priva-cloud/README.md`
> - UAT (real cluster): `deploy/uat/README.md`

---

## 1. Host toolchain

| Tool | Version | Used for |
|------|---------|----------|
| Docker | any recent | building all images |
| `docker buildx` | bundled with Docker | **UAT only** — cross-build `linux/amd64` |
| Node + npm | **20+ (22 recommended)** | building the SPAs (Vite 6) |
| Python | **3.11+ (3.12 matches the images)** | running services locally, `uv` |
| `uv` | recent | Python dep install (`pip install uv`) |
| minikube | recent, **driver=docker** | dev cluster (containerd runtime) |
| kubectl, helm | recent | deploy |

---

## 2. ⚠️ Build the SPAs BEFORE building the control-panel image

The `control-panel` image bakes the **pre-built** `web/{user,admin}/dist` (via `COPY . /app`;
`.dockerignore` drops the SPA *sources* but keeps `dist/`). There is **no `npm run build`
inside the Dockerfile** — if `dist/` is stale or missing, you ship a stale or blank UI with
no build error.

Always, before any control-panel image build:

```bash
cd web && npm ci && npm run build      # build:user + build:admin
```

- `deploy/uat/build-push.sh` refuses to build if `web/*/dist/index.html` is missing — heed it.
- Frontend-only changes do **not** need an image rebuild in dev — hotload `dist/` into the
  running pod instead (see `CLAUDE.md` → "Frontend SPA — build, hotload & redeploy").

---

## 3. Python env on the host (never `uv sync`)

To run or edit the services locally:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt          # then: uv pip install -e libs/common -e services/<svc>
```

**Do not run `uv sync`.** It resolves against the workspace and prunes the monolith's
runtime deps, breaking the env. Use `uv pip install -r requirements.txt` — this is also
what every Dockerfile does.

---

## 4. Secrets & credentials you must provide

| Secret | When | How |
|--------|------|-----|
| `priva-shared-secret` (JWT + HMAC) | every install | Auto-generated at bring-up (`up.sh`) / by the chart, **preserved across upgrades**, **never committed**. Pin explicitly only if you want to. |
| Per-account `ANTHROPIC_*` creds | after first login | Entered in the SPA **Settings** — stored Fernet-encrypted, injected into the runner pod at wake. Not a build/deploy input. |
| Registry pull secret | **UAT, private registry only** | `kubectl create secret docker-registry priva-regcred …`, then set `image.pullSecrets: [priva-regcred]` in values. The first entry is also used by the operator for per-account runner pods. |

There are **no secrets to bake into images**. If you find one in an image, that's a bug.

---

## 5. Network / mirror gotchas

- **PyPI mirror is hardcoded.** Every `deploy/docker/*.Dockerfile` pins
  `PIP_INDEX_URL`/`UV_INDEX_URL` to the Tsinghua mirror. It's reachable worldwide but slow
  outside China — edit those ENV lines to `https://pypi.org/simple` if you prefer. Not a
  blocker either way.
- **agent-runner no longer needs npm/Node at build time.** The `claude` CLI ships **inside
  the `claude-agent-sdk` wheel** (`_bundled/claude`; SDK 0.2.134 bundles CLI 2.1.226), and
  the SDK prefers that bundled binary. `uv pip install` pulls the right per-arch wheel — so
  the amd64 image gets the amd64 CLI automatically, no cross-arch npm dance.
- **Dev-machine VPN quirk (original network only):** github.com / npm registry resolve to
  fake `198.18.x` IPs; HTTPS works fine, but **git-over-SSH needs port 443**. `npm ci` and
  `pip` (HTTPS) are unaffected.

---

## 6. Dev (minikube) pre-flight

- `minikube start --driver=docker --cni=calico --extra-config=kubelet.pod-pids-limit=512` (containerd runtime — `minikube image load` imports the
  host-docker-built image).
- **Reloading the same `:dev` tag won't replace the image** under `imagePullPolicy:
  IfNotPresent`. Either `minikube image rm priva/<svc>:dev` first, or use a fresh tag.
  Backend changes are **not** hotloadable — they always need this image reload + rollout.
- `up.sh` enables the `csi-hostpath-driver` addon and patches its SC to
  `allowVolumeExpansion: true` so per-account volumes can grow live.
- Dev storage is a **privileged in-cluster NFS pod on loop images** (the linuxkit kernel has
  no filesystem-quota format, so fixed-size loop images *are* the quota). Never enable it in
  UAT/prod.

---

## 7. UAT (real cluster) pre-flight

Full steps: **`deploy/uat/README.md`**. Confirm these before you start:

| Requirement | Why |
|-------------|-----|
| **amd64 nodes** | images are built `linux/amd64` via `build-push.sh` |
| **CephFS CSI StorageClass**, RWX-capable, `allowVolumeExpansion: true` | per-account export PVCs; quota grow = PVC expand. Without expansion, quota edits fail. |
| An **RWO StorageClass** (ceph-rbd / default) | data-spine's SQLite PVC |
| A **container registry** + push creds (+ pull secret if private) | images no longer live only in minikube |
| Pod **egress to `api.anthropic.com`** (or your `ANTHROPIC_BASE_URL` relay) | runners call the Claude API |
| Install-time egress to `cr.agentgateway.dev` + GitHub releases | edge prerequisites |
| **Edge prereqs installed** (Gateway API v1.5 CRDs + GIE CRDs + agentgateway controller) | the chart does **not** install these — they're cluster-level |
| **NetworkPolicy-enforcing CNI** | Terminal isolation is destination-side policy; a CNI that ignores NetworkPolicy provides no boundary |
| kubelet **`podPidsLimit` = 1..512** on every node | bounds a fork bomb at the Pod cgroup; verify with `deploy/checks/pod-pids-limit.sh` |

Storage backend switches to `cephfs` in `values-uat.yaml` (one RWX PVC per account, size =
hard quota). The dev NFS pod is disabled there (`devStorage.enabled=false`).

---

## 8. Runtime invariants baked into the images (don't undo these)

- **Runner runs non-root (uid/gid 10001).** It owns its `/workspace`; `readOnlyRootFilesystem`
  is on. No `IS_SANDBOX` root hack — the CLI accepts `--dangerously-skip-permissions` as
  non-root.
- **Terminal is a separate Pod, not an agent-runner route.** It uses the same image,
  uid/gid and workspace, but no Runner `envFrom`, process namespace or cgroup. Its Go
  daemon and shells inherit `NOFILE=4096`, `NPROC=256`, `CORE=0`; memory is bounded by
  the Terminal container limit. Do not re-enable the removed Python PTY router.
- **Never add `RLIMIT_AS` around the `claude` CLI.** Its bun/JSC binary reserves >3 GiB
  of address space at startup and SIGTRAPs under any realistic AS cap.

---

## 9. If you drive builds through Claude Code

Docker / minikube / SSH commands need the **sandbox disabled**
(`dangerouslyDisableSandbox: true` on the Bash call) — the sandbox blocks the Docker socket
and minikube's VM access. Read-only `kubectl`/`helm` calls run fine sandboxed.

---

### One-line checklist

```
□ npm run build (web/) BEFORE building control-panel image
□ .venv via `uv pip install -r requirements.txt`  (never `uv sync`)
□ dev: `minikube image rm` before reloading the same :dev tag
□ UAT: amd64 + CephFS RWX SC + registry/pull-secret + anthropic egress + edge CRDs + NetworkPolicy CNI + podPidsLimit≤512
□ shared secret auto-generated (don't commit); anthropic creds entered in the SPA
```

# Priva Cloud

**Priva Cloud** is a self-hostable, Kubernetes-native, multi-tenant personal-AI-assistant platform built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python). It gives each user an isolated, **scale-to-zero** agent-runner pod that runs full Claude Agent SDK sessions — with skills, MCP servers, hooks, and a rich web console — while a stateless control plane handles identity, routing, pod lifecycle, and durable state. It is designed for hundreds of semi-trusted internal users on shared cluster infrastructure: every tenant gets a pod that wakes on demand, runs their agent against their own model credentials, and scales back to zero when idle.

---

## Contents

- [Status](#status)
- [Concepts](#concepts)
- [Architecture](#architecture)
  - [Request byte path](#request-byte-path-end-to-end)
  - [Services at a glance](#services-at-a-glance)
  - [Ports](#ports)
  - [Repository layout](#repository-layout)
- [Components](#components)
- [Features](#features)
- [Quickstart — local cluster (minikube)](#quickstart--local-cluster-minikube)
- [Development](#development)
- [Configuration](#configuration)
- [License](#license)

---

## Status

Priva Cloud is an **early, actively-evolving** platform. It is being refactored from a single-machine monolith (`priva/`) into independently deployable cloud services via a phased "strangler" migration:

- **Phases 0–3 are complete and merged to `main`** (2026-06-21) and are validated end-to-end on minikube: user provisioning → `AgentTenant` CR → operator reconcile → scale-to-zero pod → runtime request through the edge → EPP wake + route → agent-runner handles the turn (≈4s cold start, real LLM run and WebSocket chat verified). At rest, **control-panel, operator, data-spine, and the edge run `1/1 Ready`; agent-runner pods scale `0→1` on demand** and sit at zero replicas when idle.
- **Phases 4–6 are deferred:** the **scheduler** and **channel-connector** (IM fan-out), the **state-reader** (wake-free transcript reads), and production hardening (NetworkPolicies, mTLS/JWKS pod trust, per-account KMS/DEK, edge TLS termination, Redis-based wake/idle coordination).
- The **legacy monolith** (`priva/`) still boots and tests through Phase 3 as a verification reference and is slated for removal at Phase 4. The split-off `control-panel` does **not** depend on it.

Transport in this alpha is plaintext HTTP/gRPC in-cluster, **except** the ext_proc EPP hop, which is TLS (self-signed, skip-verify in-cluster).

---

## Concepts

| Term | Meaning |
|---|---|
| **EPP** (Endpoint Picker) | The per-request routing brain run by control-panel. The edge consults it for every runtime request to resolve the tenant and steer to the right pod. |
| **ext_proc** | Envoy's External Processing gRPC API (`envoy.service.ext_proc.v3`). The edge calls the EPP over ext_proc to mutate/steer requests before forwarding. |
| **GIE** | Gateway-API-Inference-Extension — supplies the `InferencePool` CRD that wires the edge to the EPP. |
| **InferencePool** | GIE resource selecting `app=agent-runner` pods on port `8091`, with `endpointPickerRef` → control-panel EPP. |
| **AgentTenant CR** | The per-account custom resource (`priva.io/v1alpha1`) the operator reconciles into a Deployment/Service/PVC/Secret. |
| **scale-to-zero** | A tenant pod runs `0` replicas when idle; the operator wakes it `0→1` on demand and sweeps it back to `0` after an idle grace. |
| **BYOK** | Bring-Your-Own-Key — each user supplies their own model-gateway credentials (`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`); there are no virtual keys and no metering proxy. |
| **RWO / RWX** | Kubernetes volume access modes: `ReadWriteOnce` (data-spine's SQLite PVC) and `ReadWriteMany` (the shared `priva-export` workspace volume). |

---

## Architecture

### Request byte path (end-to-end)

Every runtime request is steered per-request by the control-panel EPP, which the edge consults over Envoy `ext_proc` before forwarding to the tenant's pod. The edge terminates client transport and streams bytes; **authoritative per-request authentication and account resolution happen at the EPP** it calls.

```
   Browser / IM                ┌───────────────────────────────────┐
   (HTTP · WS · SSE)  ───────► │  agentgateway  (edge, :80)        │  Rust L7 proxy
                               │  HTTP/2 · WS/SSE · TLS term       │  (Gateway API +
                               └──────────────────┬────────────────┘  Inference Extension)
                                                  │
                       HTTPRoute:  / ─────────────┼──► control-panel :8080  (SPAs, auth, admin)
                                   /api/sandbox/* ─┘   InferencePool (selector app=agent-runner)
                                                  │
                            per-request ext_proc  ▼
                            (gRPC, TLS, ALPN h2)  ┌───────────────────────────────────┐
                                       ◄────────► │  control-panel EPP  (:9000)        │
                                                  │  verify JWT · resolve account      │
                                                  │  wake pod · return steering hdrs   │
                                                  │  (x-gateway-destination-endpoint,  │
                                                  │   x-priva-runner-token)            │
                                                  └──────────────────┬────────────────┘
                                                     patch AgentTenant CR (spec.wake)
                                                                     ▼
                                                  ┌───────────────────────────────────┐
                                                  │  operator (kopf)                  │  scale 0→1,
                                                  │  reconcile AgentTenant CRD         │  inject creds
                                                  └──────────────────┬────────────────┘  Secret from
                                                                     ▼                    data-spine
   agentgateway streams to  ──────────────────►  ┌───────────────────────────────────┐
   the woken pod (trusts signed account_id)      │  agent-runner  (:8091)            │  Claude Agent
                                                  │  per-tenant pod (0↔1)             │  SDK · skills ·
                                                  └──────────────────┬────────────────┘  MCP · hooks
                                                     ANTHROPIC_BASE_URL / AUTH_TOKEN
                                                                     ▼
                                                  ┌───────────────────────────────────┐
                                                  │  model gateway (Anthropic API)    │
                                                  └───────────────────────────────────┘

   All control-plane services ── gRPC :50051 ──►  data-spine  (SQLite RWO PVC · Fernet secrets)
```

**Walkthrough:**

1. A browser/IM client hits the **agentgateway** edge on `:80` (HTTP/2, WS, SSE). `/` routes to the control-panel SPAs/APIs; `/api/sandbox/*` routes to the `InferencePool`.
2. For every `/api/sandbox/*` request, the edge consults the **control-panel EPP** over Envoy `ext_proc` (gRPC, TLS, `:9000`). The EPP verifies the JWT, resolves the account, and wakes the pod on demand (concurrent wakes are coalesced).
3. The EPP patches the **`AgentTenant`** CR (`spec.wake`); the **operator** scales the tenant Deployment `0→1` and materializes the credential Secret fetched from **data-spine**.
4. The EPP returns steering headers (`x-gateway-destination-endpoint`, `x-priva-runner-token`); the edge streams the request to the woken **agent-runner** on `:8091` — or returns `503` if the pod is not ready within `PRIVA_KUBERNETES__WAKE_HOLD_SECONDS`.
5. agent-runner runs the Claude Agent SDK turn against the user's model gateway (BYOK `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`) and streams events back over SSE/WebSocket.

### Services at a glance

| Service | Image | Port(s) | Role |
|---|---|---|---|
| **control-panel** | `priva/control-panel:dev` | `8080` HTTP · `9000` ext_proc (TLS) | The brain: Envoy `ext_proc` endpoint picker (EPP), auth/admin/resource HTTP APIs, SPA host, `AgentTenant` provisioner. N stateless replicas. |
| **agent-runner** | `priva/agent-runner:dev` | `8091` | Per-tenant runtime that wraps the Claude Agent SDK and serves `/api/sandbox/*`. One pod per tenant, scale-to-zero (0↔1). Spawned by the operator, not Helm. |
| **data-spine** | `priva/data-spine:dev` | `50051` gRPC | Single-writer durable-state and Fernet-encrypted secret store on SQLite (WAL, RWO PVC). Authoritative for accounts, quotas, jobs, runs, secrets, registrations. 1 replica (`Recreate`). |
| **operator** | `priva/operator:dev` | — | kopf controller reconciling the `AgentTenant` CRD into Deployment/Service/PVC/Secret; the **sole scaler** (0↔1), idle sweep, credential provisioning, storage quotas. |
| **agentgateway** (edge) | external, v1.3.0 | `80` | Rust L7 proxy (agentgateway.dev) provisioned via Gateway API. Terminates client transport, consults the EPP, streams to the runner. Not Priva code. |
| **nfs-xfs** (dev only) | `priva/nfs-xfs:dev` | `2049` NFSv4 · `8099` quota | In-cluster NFSv4 server backing **per-account fixed-size ext4 loop images** (the filesystem size *is* the hard quota) plus a FastAPI quota-manager. (The image is named `nfs-xfs` for historical reasons; the dev kernel lacks XFS project-quota support, so ext4 loop images are used instead.) Replaced by external CephFS/NFS CSI in prod. |
| **web** | (served by control-panel) | — | React 18 + Vite user SPA (`/`) and admin SPA (`/admin`), built to `dist/` and served from disk. |

### Ports

| Port | Service | Protocol |
|---|---|---|
| `80` | agentgateway edge | HTTP listener (external entry) |
| `8080` | control-panel | HTTP (SPAs, auth, admin) |
| `9000` | control-panel EPP | gRPC ext_proc (TLS, ALPN h2) |
| `8091` | agent-runner | HTTP/WebSocket runtime API |
| `50051` | data-spine | gRPC |
| `2049` | nfs-xfs (dev) | NFSv4 |
| `8099` | nfs-xfs quota-manager (dev) | HTTP |

### Repository layout

```
priva-cloud/
├── services/
│   ├── control-panel/      # brain + EPP + auth/admin API + SPA host + provisioner
│   ├── agent-runner/       # per-tenant Claude Agent SDK runtime
│   ├── data-spine/         # single-writer gRPC state + secret store (SQLite)
│   └── operator/           # kopf AgentTenant controller (sole scaler)
├── libs/
│   └── common/             # priva_common — shared contract layer (no service imports)
├── protos/                 # gRPC contracts (priva.dataplane.v1) + gen.sh codegen
├── web/
│   ├── user/               # user SPA (chat console)        → served at /
│   ├── admin/              # admin SPA (operations console)  → served at /admin
│   ├── shared/             # shared components, API client, Zustand stores, i18n, fonts
│   └── design-spec.md      # canonical design system spec
├── deploy/
│   ├── docker/             # Dockerfiles (control-panel, agent-runner, …)
│   ├── k8s/                # Deployments, Services, ConfigMap
│   ├── crds/               # AgentTenant CRD (priva.io/v1alpha1)
│   ├── rbac/               # ServiceAccounts, Roles, ClusterRoles
│   ├── gateway/            # Gateway, InferencePool + EPP ref, HTTPRoute
│   ├── dev-storage/        # dev NFS + quota backend, priva-export PV/PVC
│   ├── minikube/           # build.sh, up.sh (local bring-up)
│   └── helm/priva-cloud/   # Helm chart (dev + values-prod.yaml overlay)
├── tools/cli/              # priva-cloud launcher / supervisor
└── priva/                  # legacy monolith (strangler reference; removed at Phase 4)
```

---

## Components

### control-panel — the brain & EPP

The control plane. It never sees runtime data (sessions/files stay on agent-runner pods) — only control/metadata. It runs two listeners:

- **HTTP / FastAPI (`:8080`, `CONTROL_PANEL_PORT`)** — user/admin API, SPA serving, model proxy. Routers: **auth** (login/setup, JWT refresh, API-key + env-var CRUD), **admin** (user CRUD, runner defaults, fleet snapshot, resource usage, system-health graph, gateway metrics, self-registration approval, config updates for prompt/history/risky-tools/PII masking), and **console** (`WebSocket /api/admin/console/ws` — audited admin terminal into control-plane pods via Kubernetes exec).
- **gRPC `ext_proc` EPP (`:9000`, `PRIVA_EDGE__EXTPROC_PORT`, TLS)** — implements `envoy.service.ext_proc.v3.ExternalProcessor/Process` (pure-Python `grpclib` for Rust-client compatibility). For every runtime request it inspects headers, verifies JWT, resolves the account, wakes the pod on demand (coalesced concurrent wakes), and injects steering headers (`x-gateway-destination-endpoint`, `x-priva-runner-token`). Returns `503` if the pod is not ready within `PRIVA_KUBERNETES__WAKE_HOLD_SECONDS`.

The **provisioner** creates/updates `AgentTenant` CRs, polls status, introspects the fleet, and scrapes metrics (CPU/memory/volume via metrics-server; gateway via the agentgateway Prometheus endpoint). Auth is JWT (HS256) + per-user API keys (`sk-*`) + an optional global API key. SPA bundles are mounted via `StaticFiles`.

### agent-runner — the per-tenant runtime

A single-account service (one pod per tenant, pinned by `entry.py` via `ACCOUNT_ID`/`USERNAME`) that wraps **claude-agent-sdk 0.1.81** and serves `/api/sandbox/*`:

- **Endpoints:** `POST /api/sandbox/agent/run` (one-shot), `POST …/run/stream` (SSE), `WS …/agent/ws/run` (bidirectional, subprotocol `priva.ws.v1`), session CRUD (list/get/delete/pin/archive), fork/rewind, permission responses, plus **PTY** (`WS /api/sandbox/pty/ws`, xterm.js) and **file-ops** endpoints (backing the console's Web Terminal and File Explorer).
- **Claude SDK service:** builds `ClaudeAgentOptions` (model, cwd, `add_dirs`, permission mode, hooks, MCP servers, skills allowlist); `agent_run()` / `agent_run_events()`; session healing of orphan tool-use blocks on resume; retry with exponential backoff (`MAX_ATTEMPTS=10`).
- **Permission coordinator:** bridges SDK `can_use_tool` callbacks to the SSE/WS stream — emits `permission_request`, waits on an `asyncio.Future`, resolves on `POST …/agent/permission/respond`.
- **Skills:** enumerates global (`~/.claude/skills`) + project (`.claude/skills`) directories, applies a `skill_exclude` denylist, returns the allowlist for `options.skills`.
- **MCP:** reads `.mcp.json` (project) + `~/.claude/settings.json` (global); injects a built-in in-process `priva_File` server (FileCanvas) for the Canvas panel.
- **Hooks:** wraps admin-enforced + user command hooks as in-process Python callbacks (`PreToolUse`/`PostToolUse`), including a PII-masking hook.
- **Permission modes:** `default`, `acceptEdits`, `plan`, `bypassPermissions`. A risky-tool list (admin-configured) forces approval even under `bypassPermissions`. Built-in disallowed tools include `WebFetch`, `WebSearch`, `NotebookEdit`, and cron/worktree/remote-trigger tools.
- **Fork vs rewind:** *rewind* uses SDK file checkpointing (`enable_file_checkpointing=True` → `client.rewind_files(checkpoint_uuid)`); *fork* uses session forking (`fork_session=True` → `sdk_fork_session(session_id, up_to_message_uuid)`) to branch from a point. They are distinct mechanisms.
- **Isolation:** per-account Python venv bootstrapped at `<workspace>/.venv`; sessions stored as JSON-Lines under `~/.claude/projects/`; pin/archive metadata in account-level `~/.claude/priva_meta.json`. Auth is the signed `X-Priva-Runner-Token` (HS256), whose `account_id` must match the pod's `ACCOUNT_ID`.
- **Pod mounts:** the per-account workspace (`subPath <account_id>` of the shared RWX `priva-export` volume, where the venv and `~/.claude/projects` live), a per-account audit volume (`/audit/<account_id>`), a tmpfs secret mount (`/etc/secrets/priva`) holding BYOK + MCP credentials, and the SDK runtime mounted read-only.
- **Native CLI:** the image bakes the native `claude` CLI (`@anthropic-ai/claude-code`, ≥ v2.0.0, installed via `npm install -g`) onto `PATH`; the Python `claude-agent-sdk` only *spawns* it. The pod runs non-root as uid `10001`.

Model access uses the user's `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` (BYOK): users bring their own LLM key, injected by the operator at wake — no virtual keys, no metering proxy; token usage is pod-self-reported.

### data-spine — durable state & secrets

The authoritative storage layer behind a stable gRPC contract (`priva.dataplane.v1`). A single SQLite WAL connection (foreign keys on, `STRICT` tables, thread-locked single writer) backs **9 tables** — `account`, `channel_binding`, `quota`, `scheduled_job`, `job_run_record`, `secret`, `account_resource_spec`, `pending_registration`, `runner_defaults`. The DB lives at `/data/priva.dataspine.db` on the RWO PVC in-cluster (local default `~/.priva_workspace/.priva.dataspine.db`).

Services exposed over gRPC (`:50051`): **Account**, **Binding**, **Quota**, **Admin**, **Secret**, **ResourceSpec**, **RunnerDefaults**, **Registration**.

- **Scheduler** is defined in proto and service code but **not yet served over gRPC** — deferred to Phase 4 (the in-process client works).
- **Binding** is Feishu-shaped (`channel_id`/`session_uuid` ↔ `account_id`, with a `feishu_user_id` unique index). It is **greenfield with no Phase-1 writer — unused in alpha**; it backs the deferred IM channel-connector.
- Passwords are bcrypt; API keys are Fernet-encrypted with an HMAC-SHA256 lookup column; secret bundles are Fernet-encrypted at rest with a monotonic generation counter (the operator fetches and materializes them as K8s Secrets at wake).
- Transport is selectable: **in_process** (Phase 1 / single pod) or **grpc** (multi-pod). The **Postgres** backend is an interface-only stub.
- CLI: `python -m priva_data_spine {init|stats|migrate [--dry-run]|serve}`. The migration path imports monolith YAML/JSONL into SQLite idempotently.

### operator — the AgentTenant controller

A standalone kopf operator (no `KopfPeering` CRD; one instance per cluster) that reconciles the **`AgentTenant`** CRD (`priva.io/v1alpha1`, plural `agenttenants`, short name `at`) and is the sole scaler. Only four triggers:

1. **CR create/resume** → ensure Deployment (`ar-<account_id>`, `replicas=0`, `strategy=Recreate`), Service, PVC subPath mount.
2. **`spec.wake.requestedAt` patch** → scale `0→1`, fetch the credential bundle from data-spine, materialize the `ar-<account_id>-creds` Secret (mounted via `envFrom`).
3. **10s timer** → idle sweep, pod-IP self-heal (real Ready pods are source of truth; flips status to not-routable *before* teardown to shrink the EPP race window), and quota reconcile.
4. **field edits** (`agentRunnerType`, `resources`, `storageGb`).

Scale model: `auto_scale` (default, scale-to-zero after a configurable idle grace) or `persistent` (always-on, exempt from idle sweep). Storage uses one shared RWX export (`priva-export`) that every runner `subPath`s into by `account_id`, with per-account quota enforced by the backend. In dev the `NfsXfsBackend` provisions a **fixed-size ext4 loop image per account** via the quota-manager HTTP API — **grow is supported; live shrink of a mounted image is unsupported** (the quota-manager returns `409 "shrink not supported on the dev loop backend"`). A `CephFsBackend` stub is provided for prod. Inheritance cascade: CR spec override → data-spine global defaults (fail-soft) → env seeds.

### Web console (user + admin SPAs)

Two independent React 18 + Vite SPAs sharing one design system and a `web/shared` infra layer (auth, API client with REST + WebSocket, Zustand stores, i18n `en`/`zh`, fonts).

- **User SPA (`/`):** chat-driven agent console. Sidebar groups sessions by working directory (search/filter, create/archive/fork/pin); `ChatPanel` streams from `/api/sandbox/agent/ws/run` (tool-call cards, subagent frames, permission/plan approvals); `CanvasPanel` (right, 380px, resizable 280–60vw) shows the task tree, progress, and file-ops browser, auto-showing when tasks appear; **Data & Usage** view (usage, analytics, audit log, file explorer); a bottom **Web Terminal** drawer over `/api/sandbox/pty/ws` (xterm.js, into the tenant pod).
- **Plugins / Customize hub:** five subsections. **Skills is live** (list + editor + Skills hub modal). **MCP, Hooks, SubAgents, and Memory all render "coming soon" placeholders** — full panel components for MCP/Hooks/SubAgents exist in the tree but are not wired into the hub.
- **Admin SPA (`/admin`):** operations dashboard with 7 nav items — **Fleet, Resource Quota, System Topology, Console, Users, Audit, Sandbox** (operational); a Configurations tab is a placeholder. Role-gated: non-admins are redirected to `/`. The admin **Console** terminal is a Kubernetes-exec bridge into *control-plane* pods (`/api/admin/console/ws`) — distinct from the user SPA's sandbox PTY (`/api/sandbox/pty/ws`), which streams into the tenant's own agent-runner pod.
- **Design system:** GitHub Dark palette via CSS variables only (no hardcoded hex, no color Tailwind classes), Noto Sans (UI) / JetBrains Mono (code), no shadows, ≤4px radius, status shown as a 2px left border, skeleton-shimmer loading. Tailwind is layout-only. Cold-start UX: `fetchWithWake()` detects a `503` early (`WAKE_SLOW_MS=900ms`), toasts "Agent sandbox is waking…", and retries up to 6 times with backoff `[1, 2, 4, 8, 16]`s. WebSocket auth carries the JWT in the `Sec-WebSocket-Protocol` subprotocol. See `web/design-spec.md`.

Stack highlights: Tailwind 3.4, Zustand 5, lucide-react, react-markdown + remark-gfm + rehype-highlight, Recharts, xterm.js, CodeMirror 6, i18next.

### libs/common (`priva_common`) + protos

The exclusive cross-service contract layer — **services depend on it; it imports no service code.** It provides:

- **Dataplane clients** (`get_client()`, `set_inprocess_handlers()`): transport-agnostic Protocol interfaces — `AccountClient`, `BindingClient`, `QuotaClient`, `SchedulerClient`, `AdminClient`, `SecretClient`, `ResourceSpecClient`, `RunnerDefaultsClient`, `RegistrationClient` — over in-process or gRPC transports.
- **Config** (`pydantic-settings`, YAML overlay + `PRIVA_*` env override), **logging** (loguru, 5 channels, hourly rotation, HTTP access middleware), **metrics** (`prometheus-client`: `HTTP_REQUESTS`/`HTTP_DURATION`, `AGENT_RUNS_*`).
- **Crypto** (Fernet `enc:v1:`), **runner_token** (HS256 mint/verify), **user/runtime config stores**, **audit_log** (daily-partitioned JSONL, cursor pagination), the **Redis key catalog** (single source of truth for T1 durable inbox + T2 ephemeral routes/locks/claims/approvals, used by the Phase-4 services), **risky_matcher**, **sensitive_mask**, **skill_exclude**, **script_lint**, and the Pydantic **models/** DTOs.
- **protos/** holds the gRPC contracts; `protos/gen.sh` runs `grpc_tools.protoc` to generate stubs into `libs/common/src/priva_common/dataplane/v1/` (committed; no runtime codegen).

### deploy

Kubernetes manifests (`k8s/`), Dockerfiles (`docker/`, Python 3.12-slim-bookworm base, uv dependency resolution), the `AgentTenant` CRD (`crds/`), RBAC (`rbac/`), edge wiring (`gateway/`: Gateway, InferencePool + EPP ref, HTTPRoute), dev storage (`dev-storage/`), the minikube bring-up (`minikube/build.sh`, `minikube/up.sh`), and a **Helm chart** (`helm/priva-cloud/`) that mirrors the raw manifests one-for-one and supports a dev profile (nfs_xfs backend, `:dev` images) and a prod overlay (`values-prod.yaml`: external CSI, real registry/tags).

---

## Features

- **Per-tenant agent runtime** — full Claude Agent SDK sessions per pod, with SSE and bidirectional WebSocket streaming, session resume + healing, vision-model stickiness, *fork* (session forking) and *rewind* (file checkpointing).
- **Skills** — bundled and user-defined skills from `~/.claude/skills` + project `.claude/skills`, with a per-user exclude denylist and a Skills hub/marketplace in the console (the only live Plugins-hub panel).
- **MCP servers** *(backend)* — project (`.mcp.json`) and global discovery, a strict-config injection path, and a built-in FileCanvas server. *(The console MCP panel is a coming-soon placeholder.)*
- **Hooks & audit** *(backend)* — admin-enforced and user `PreToolUse`/`PostToolUse` hooks as in-process callbacks, plus PII masking; a daily-partitioned JSONL audit trail covering tool/skill invocations, run completions, and session operations. *(The console Hooks panel is a coming-soon placeholder.)*
- **Permission coordination** — interactive allow/deny approvals surfaced to the UI; admin-defined risky-tool list forces approval even in bypass mode.
- **Multi-tenant isolation** — one pod per account, signed runner-token auth (`account_id` pinned to the pod), per-account workspace subPath, per-account venv, per-account audit volume, and per-account encrypted credentials.
- **Scale-to-zero** — the operator wakes pods on demand (0→1) and sweeps idle pods back to zero, as the sole scaler; cold start ≈4s on minikube.
- **Web console** — dual user/admin SPAs with chat, task canvas, plugins hub, data/usage analytics, web terminal, and an operations dashboard.
- **Observability** — Prometheus `/metrics` on each service (control-panel exposes it unauthenticated), `/health` with dependency status, and loguru structured logging; the control-panel aggregates fleet state from the K8s API, data-spine, and Prometheus.
- **Durable, single-writer state** — accounts, quotas, resource specs, jobs/runs, registrations, and Fernet-encrypted secrets behind one gRPC contract.
- **Deferred (Phase 4+):** the scheduler (cron/scheduled agent jobs), IM channel-connector fan-out, and the wake-free state-reader.

---

## Quickstart — local cluster (minikube)

### Prerequisites

- **minikube** with the **containerd** runtime, **kubectl**, **Helm 3**, and **Docker** (image build/load).
- An **Anthropic API key** or a compatible model gateway (`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`), configured per account after bring-up.
- Node.js ≥18 and Python ≥3.11 + `uv` if you intend to rebuild the web bundles or services.
- For the Fleet/Resource-usage dashboards to populate, enable metrics-server: `minikube addons enable metrics-server` (the bring-up script enables only the CSI hostpath addon).

> Docker / minikube steps require a non-sandboxed shell.
> Day-one gotchas you'll hit fast — see [Development](#development): use `uv pip install -r requirements.txt` (**not** `uv sync`) for the dev venv, and frontend changes use a build → hotload loop (no image rebuild).

### Build the images

Builds all five images (`priva/{data-spine,control-panel,operator,agent-runner,nfs-xfs}:dev`) and loads them into minikube's containerd (idempotent; accepts a service subset):

```bash
./deploy/minikube/build.sh
```

### Bring up the control plane

**Option A — one-shot script** (idempotent, 9 steps: build/load → enable CSI hostpath addon → create namespace → deploy dev NFS+quota → install Gateway API / GIE CRDs → install agentgateway via Helm OCI → apply CRD/RBAC → generate the shared secret → deploy control-plane/data-spine/operator → deploy the edge):

```bash
./deploy/minikube/up.sh
```

**Option B — Helm** (install the prerequisites first; they are **not** in the chart):

```bash
# 0. Build & load the local :dev images into minikube first — imagePullPolicy is
#    IfNotPresent with no registry, so skipping this yields ImagePullBackoff:
./deploy/minikube/build.sh

# Prerequisites (once):
#   Gateway API v1.5                                  (kubectl apply --server-side)
#   GIE v1.5 (Gateway-API-Inference-Extension)        (kubectl apply)
#   agentgateway 1.3.0                                (Helm OCI)

helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace
```

Everything lands in the **`priva-cloud`** namespace (control plane and tenant pods share it in alpha).

### Reach the edge and open the console

```bash
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80
```

Then open **http://localhost:8080/** for the user console (or **/admin** for the admin console). On first run, complete initial admin setup via the login/setup flow, then add per-account `ANTHROPIC_*` credentials so the runner can reach your model gateway. New users can also request access via the **self-registration → admin-approval** flow (a `pending_registration` is created and an admin approves it from the admin SPA, after which the user can log in immediately).

---

## Development

### Monorepo & dev venv

Priva Cloud is a **uv workspace** monorepo (`members = [libs/common, services/*, tools/cli]`), Python ≥3.11 (Docker base 3.12).

> **Dev venv rule:** during Phases 0–3, install with
> ```bash
> uv pip install -r requirements.txt   # do NOT use `uv sync`
> ```
> `uv sync` prunes the monolith's dependencies; use the requirements file instead.

The unified launcher supervises or runs individual services:

```bash
priva-cloud serve [--only data-spine,control-panel]   # supervise discovered services
priva-cloud control-panel   # or agent-runner | data-spine | operator
```

### Frontend build + hotload loop

The SPAs are built on the host and served by the **control-panel** pod from `/app/web/{user,admin}/dist` via FastAPI `StaticFiles` (read from disk per request) — so a frontend change does **not** require an image rebuild.

```bash
cd web
npm run build:user      # → web/user/dist   (served at /)
npm run build:admin     # → web/admin/dist  (served at /admin)
npm run build           # both
# dev servers:
npm run dev:user        # localhost:5173 (proxies /api → VITE_API_TARGET, default http://localhost:8081)
npm run dev:admin       # localhost:5174
```

> **Dev-proxy port note:** the Vite dev servers default `VITE_API_TARGET` to `http://localhost:8081`, but a locally-run control-panel listens on `:8080` (`CONTROL_PANEL_PORT`). Set `VITE_API_TARGET=http://localhost:8080` (or run control-panel on `8081`), or the dev SPA can't reach the backend.

After building, **hotload** the fresh `dist/` directly into the running pod (StaticFiles serves the new hashed bundle + `index.html` immediately — no restart):

```bash
POD=$(kubectl get pods -n priva-cloud -l app=control-panel -o jsonpath='{.items[0].metadata.name}')
tar -C web/user/dist -cf - . \
  | kubectl exec -i -n priva-cloud "$POD" -- \
      tar -C /app/web/user/dist --warning=no-unknown-keyword -xf -
```

Hotloaded files live only in the running pod and are lost on restart/reschedule. To persist a change, rebuild the control-panel image (which bakes `dist/` in via `COPY . /app`) and redeploy. **Backend (`services/**`) changes are not hotloadable — they always require an image rebuild.**

### Tests & codegen

```bash
# run the suite (pytest); e.g. data-spine ships 177 passing tests
pytest

# regenerate gRPC stubs after editing protos/priva_common/dataplane/v1/*.proto
./protos/gen.sh   # writes *_pb2.py / *_pb2_grpc.py / *.pyi into libs/common/src
```

---

## Configuration

Config is a YAML file selected by `PRIVA_CONFIG_FILE` (or `--config`), with environment overrides using the `PRIVA_*` prefix and `__` as the nested delimiter (e.g. `PRIVA_DATASPINE__GRPC_DSN`).

| Variable | Purpose |
|---|---|
| `PRIVA_CONFIG_FILE` | Path to the YAML settings file. |
| `CONTROL_PANEL_PORT` | control-panel HTTP port (default `8080`). |
| `PRIVA_EDGE__EXTPROC_PORT` | EPP ext_proc gRPC port (default `9000`, TLS self-signed). |
| `PRIVA_AUTH__JWT_SECRET` | HS256 JWT signing secret. |
| `PRIVA_AUTH__GLOBAL_API_KEY` | Optional platform-wide API key (per-user `sk-*` keys also supported). |
| `PRIVA_DATASPINE__TRANSPORT` | `in_process` or `grpc`. |
| `PRIVA_DATASPINE__GRPC_DSN` | e.g. `data-spine.priva-cloud.svc.cluster.local:50051`. |
| `PRIVA_DATASPINE__API_KEY_HMAC_SECRET` | HMAC-SHA256 secret for API-key lookup. |
| `PRIVA_KUBERNETES__NAMESPACE_TENANTS` | Tenant namespace (default `priva-cloud`; alpha shares it with the control plane). |
| `PRIVA_KUBERNETES__RUNNER_SERVICE_PORT` | agent-runner port (default `8091`). |
| `PRIVA_KUBERNETES__WAKE_HOLD_SECONDS` | EPP wake timeout before a `503`. |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` | Per-account model gateway credentials (stored Fernet-encrypted in data-spine; injected by the operator as a K8s Secret at wake). |
| `X-Priva-Runner-Token` (header) | HS256 token minted by control-panel for the runner; carries `{account_id, username, exp}`. |

In-cluster, the bootstrap `ConfigMap` (`priva-config`) carries non-secret env (data-spine DSN, runner image, idle/wake timings, storage backend, quota-manager URL, runner UID/GID `10001`). The runtime-generated **`priva-shared-secret`** holds `PRIVA_AUTH__JWT_SECRET` + `PRIVA_DATASPINE__API_KEY_HMAC_SECRET` (32-byte hex each, not committed, preserved across `helm upgrade`).

---

## License

MIT.

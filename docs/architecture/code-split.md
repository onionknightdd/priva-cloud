# Code split map — monolith → multi-tenant deployables

> **Status:** authoritative implementation map (locked 2026-06-19, solo/conversational).
> **Consumes:** the six component drills (`components/agent-runner.md`, `agent-gateway.md`, `control-panel.md`, `channel-connector` (inline in agent-gateway §4.4/§5/§8), `operator.md`, `scheduler.md`, `data-spine.md`) as the **design authority**. This doc is the **per-file bridge** from today's `priva/api` + `priva/web` monolith to the deployables those drills specify.
> **Method:** every current *application* file is assigned a **target deployable** + a **reshape verb** (§10). Where a file's endpoints split across deployables, the **per-route table (§5) is authoritative** over the file table (§4). Where this doc and a component drill disagree on a subsystem's *internal* contract, the drill wins; this doc owns only the **placement**.

---

## 1. Why this doc exists

The drills answered **what** each subsystem does and **how** it behaves. They cite current code in their `§0` lift/strip/relocate tables — but those tables are scattered across six files and stop at **file granularity**. This doc:

1. **Consolidates** the six `§0` tables into one place, so there is a single answer to "where does `X.py` go?"
2. **Resolves the per-router splits** the drills deferred — 6 routers expose *both* an admin/config face (→ Control Panel) and a runtime face (→ agent-runner): `hooks`, `mcp`, `skills`, `subagents`, `pty`, `scheduler` (§5.2). Each endpoint is classified in §5.
3. **Defines `libs/common`** — the shared contract layer (wire format, models, data-plane client, crypto) that no single drill owns.
4. **Sets the extraction order** (§11) so the carve is a reversible strangler, not a big-bang.

---

## 2. Current reality (verified 2026-06-19)

- **One monolith** under `priva/api` (~140 app `.py` files + ~200 `bundled/skills/` assets) + **one React app** `priva/web` (233 files).
- **Three OS processes today** (`priva/bin/server.sh`): `uvicorn` (the API; serves 17 routers + the SPA + runs OpenClaw bridges in `main.py` lifespan `:133-153`), the **scheduler daemon** (`python -m api.services.scheduler.daemon`, `server.sh:455`), the **channels daemon** (`server.sh:539`).
- **The inversion (the seam everything hangs off):** the in-process agent runner `agent_run_events` (`services/claude_sdk/service.py`) is called by **all three** —
  - WebUI: `routers/agent.py:346` (`/run/stream`),
  - Scheduled: `services/scheduler/daemon.py:299-305`,
  - IM: `services/channels/daemon.py:786-794`.
  After the split **only the agent-runner runs `service.py`**; scheduler and connector become *dispatchers* (wake the pod, hand it the turn). See §7.
- **Persistence is file/YAML today:** `config_store.py` (channels YAML), `job_store.py` (scheduler YAML), `run_history.py` (daily JSONL), `user_store.py`, hook/mcp config managers — all become data-plane RPCs (§8).
- **CORS is `allow_origins=["*"]`** (`main.py:181`) and the SPA is mounted at `/` (`main.py:79`) — both move to the edge / Control Panel faces.

---

## 3. Target monorepo layout

```
priva-cloud/                      (one repo — monorepo, locked 2026-06-19)
├── pyproject.toml                uv workspace root: members = ["libs/*","services/*","tools/*"]
├── libs/
│   └── common/                   shared package `priva_common`: wire format, models, data-plane
│                                  client (priva_common.dataplane), crypto, redis catalog, settings, metrics
├── tools/
│   └── cli/                      `priva_cli` → the `priva-cloud` launcher (dep-free, lazy dispatch — §14)
├── protos/                       data-plane gRPC contracts (data-spine §1.7) → compiled into priva_common.dataplane
├── services/                     (each created lazily at its phase)
│   ├── agent-runner/             Py — per-tenant runtime (1 replica/tenant, 0↔1)  [was "agent-pod"]
│   ├── control-panel/            Py — brain (ext_proc) + admin API + faces (N); may embed state-reader
│   ├── channel-connector/        Py — WeCom socket + lease + IM fan-out (N, lease-owned)
│   ├── scheduler/                Py — leaderless fire→claim→wake→dispatch (N)
│   ├── operator/                 Py/kopf — AgentTenant CRD, sole scaler (NEW)
│   ├── data-spine/               Py/gRPC — Accounts/Identities/sessions/jobs RPC + Redis
│   └── state-reader/             Py — embeddable RO JSONL transcript router (standalone in prod; NEW, small)
├── web/                          TS/React — 2 build targets (/ui user, /admin admin)
├── deploy/                       K8s manifests, CRDs, NetworkPolicies, agentgateway config
└── (agentgateway itself = external Rust; we ship only its CRDs under deploy/)
```

Each `services/<name>/` and `tools/cli/` is a `uv` workspace member with **src-layout**:
`services/<name>/src/<pkg>/` holding a uniform entrypoint trio — `entry.py`
(`def main(argv=None) -> int`, the contract the launcher calls), `__main__.py`
(`from .entry import main; raise SystemExit(main())`, so `python -m <pkg>` works), and
`app.py` / `server.py` (the composition root). Internal sub-packages mirror the §4 mapping
groups so LIFTs are pure file moves. See §3.1 for naming + dependency conventions and §14
for the launcher.

**Deployable summary**

| Deployable | Lang | Replicas | Image holds |
|---|---|---|---|
| agent-runner | Py | 1 / tenant (0↔1) | SDK runtime + `bundled/skills` baked in |
| control-panel | Py | N stateless | brain + admin + serves `web/dist` |
| channel-connector | Py | N (1 socket owner/bot via lease) | WeCom/OpenClaw socket + fan-out |
| scheduler | Py | N leaderless | APScheduler-free fire engine |
| operator | Py/kopf | 1 active (leader-elected) | controller-runtime |
| data-spine | Py/gRPC | N | DB access + Redis catalog |
| state-reader | Py | N (or embedded) | broad RO JSONL mount; embeddable router (§3.2) |
| web | static | — | served by control-panel faces |

> **Control Panel = one process, three listeners — NOT three deployables.** It binds three ports in a **single** process/image (option a, control-panel.md §0.1): `:9000` gRPC `ext_proc` (the brain — a different *protocol* from HTTP, so necessarily its own listener), `:8080` HTTP faces (edge-reachable via agentgateway), `:8081` HTTP internal (cluster-only — scheduler/operator/connector; NetworkPolicy denies external ingress). The faces/internal split is a **network-policy boundary**, not a process boundary; the gRPC split is forced by protocol. Known future seam (C4): if the latency-critical brain's scaling diverges from the admin API, `:9000` can peel into its own deployable later — option (a) keeps them merged now.

### 3.1 Package & layout conventions

- **src-layout for every member** (`<member>/src/<pkg>/`). Imports resolve only to installed packages, never the CWD — clean hatchling wheels, no accidental cross-boundary imports.
- **Distribution name hyphenated · import name underscored · `priva_`-prefixed**, for all members: `priva-common`→`priva_common`, `priva-agent-runner`→`priva_agent_runner`, `priva-control-panel`→`priva_control_panel`, … (`scheduler`/`operator` are too generic to import bare).
- **The dependency graph *is* the §6 import boundary.** Each `services/*/pyproject.toml` declares exactly `priva-common` + third-party; a service may **not** depend on another service; `priva-common` depends on neither. uv path-deps enforce this — it is not a convention you police by hand.
- **One uniform entrypoint per member** (`entry.py:main` + `__main__.py` + `app.py`/`server.py`) so the `priva-cloud` launcher dispatches to all services identically (§14).
- **Generated gRPC stubs live in `priva_common.dataplane`** (one shared client for pod/brain/connector/scheduler/operator), compiled from `protos/`. `grpcio`/`protobuf` become `priva-common` deps at Phase 1 when the stubs land.

### 3.2 state-reader is an embeddable router, not unconditionally a standalone service

state-reader exists to serve **wake-free** session/transcript reads while a tenant's
agent-runner is scaled to zero (data-spine §3.9, control-panel.md §0.2). The reason it is a
*separate* deployable is **not** its size — it is its **mount**: it carries a broad,
cross-tenant, **read-only** JSONL mount that must **not** sit on the externally-exposed,
many-replica control-panel tier (blast radius). Resolution: ship it as
`priva_state_reader` exposing a `create_app()` / router that

- **embeds** into control-panel in local / small deployments (`priva-cloud control-panel` mounts the router; mode A), and
- runs **standalone** with its own NetworkPolicy (no external ingress; callable only by control-panel internally) where the mount-isolation matters (prod; mode B).

So *separate-vs-merged* is a **deployment** choice, not a code-structure one — no "tiny service" tax locally, security boundary kept in prod. `priva-cloud state-reader` runs it standalone.

---

## 4. File → deployable map (by current directory)

Verbs: **LIFT** (move as-is) · **RESHAPE** (move + change) · **SPLIT** (divides, see §5) · **RELOCATE** (non-obvious home) · **DELETE** (replaced by platform) · **NEW** (no current code).

### 4.1 `priva/api/services/claude_sdk/` → **agent-runner**

| File | LOC | → | Verb |
|---|---|---|---|
| `service.py` | 1079 | agent-runner | **RESHAPE** — runner becomes the pod-internal run API (was called in-process by 3 callers, §7); reads the brain's signed `account_id` |
| `client.py`, `options.py`, `retry.py`, `session_heal.py` | — | agent-runner | **LIFT** |
| `permission_coordinator.py` | — | agent-runner | **RESHAPE** — the `Future` rendezvous stays **in-pod** (agent-runner §5.3, decision 6); module-global singleton → per-pod |
| `serialization.py` | 188 | **libs/common** | **LIFT** — the wire format is shared **pod↔connector** (pod serializes, connector fans out; agentgateway streams bytes only) |

### 4.2 `priva/api/services/channels/` → **channel-connector**

| File | → | Verb |
|---|---|---|
| `daemon.py` (1630) | channel-connector | **RESHAPE** — keep socket + handshake + reply machinery; **strip** the in-process `agent_run_events` (`:786-794`); **add** the Redis lease (agent-gateway §5) + the connector→brain RPC (option A, agent-gateway §4.4) |
| `wecom_feedback.py` | channel-connector | **LIFT** — card render (agent-gateway §8.1) |
| `openclaw_bridge.py`, `openclaw_mcp_tools.py` | channel-connector | **LIFT** (deferred surface, agent-gateway §13-2) |
| `shared.py` | channel-connector | **RESHAPE** — file-command bus → Redis (agent-gateway §6) |
| `config_store.py` | — | **DELETE** — channel config → data-plane `channel_config_*` RPC (data-spine §2.12) |

### 4.3 `priva/api/services/scheduler/` → **scheduler** (+ relocations)

| File | → | Verb |
|---|---|---|
| `daemon.py` (695) | scheduler | **RESHAPE** — APScheduler → **leaderless** Redis exactly-once (data-spine §4 #14); **strip** `agent_run_events` (`:299-305`); execute nothing → dispatch (RPUSH inbox + CR-patch wake, scheduler.md) |
| `shared.py` | scheduler | **RESHAPE** — file-command bus → Redis pub/sub |
| `builtin_tasks.py`, `tool_retry.py` | **agent-runner** | **RELOCATE** — the executors move to the pod (scheduler.md: the pod executes every job) |
| `mcp_tools.py` | **agent-runner** | **RELOCATE** — in-pod `scheduler_*` MCP tools (re-pointed: JobStore→RPC, write_command→PUBLISH) |
| `job_store.py` | **data-spine** | **RESHAPE** — YAML → `scheduled_job` table + RPC (data-spine §2.11) |
| `run_history.py` | **data-spine** | **RESHAPE** — JSONL → `job_run` table; **split** birth (scheduler writes `running`/`skipped`) vs outcome (pod writes `FinishRun`), scheduler.md |

### 4.4 `priva/api/services/hooks/` → **SPLIT**

| File | → | Verb |
|---|---|---|
| `executor.py`, `callbacks.py`, `built_in_hooks.py`, `builder.py`, `registry.py`, `risky_matcher.py` | **agent-runner** | **LIFT** — hooks execute in-pod during the turn |
| `config_manager.py`, `prefs.py` | **control-panel** | **RESHAPE** — hook config → data-plane |
| `log_store.py` | **data-spine** | **RESHAPE** — hook logs → data-plane (read by Control Panel) |

### 4.5 `priva/api/services/mcp/` & `priva_plugin/` → **SPLIT**

| File | → | Verb |
|---|---|---|
| `mcp/built_in.py`, `mcp/validator.py` | **agent-runner** | **LIFT** — built-ins + validation run where the MCP creds live (operator bundle) — *validate-locus is OQ-4* |
| `mcp/config_manager.py` | **control-panel** | **RESHAPE** — MCP server config → data-plane |
| `priva_plugin/*` (base, manager, plugins/enterprise_user_info/*) | **agent-runner** | **LIFT** — plugin execution in-pod; plugin *enablement* config → control-panel |

### 4.6 `priva/api/services/` (top-level)

| File | → | Verb |
|---|---|---|
| `auth.py` (170) | **SPLIT** | identity **resolution** + token **mint/sign** → control-panel/brain; JWT **signature verify** → **agentgateway** (edge, not Python); `rate_limiter` (`:170`) → libs/common (Redis-backed) |
| `user_store.py` | **data-spine** | **RESHAPE** — Accounts/Identities RPC (data-spine §1.7) |
| `audit_log.py` | **data-spine** | **RESHAPE** — audit RPC |
| `config.py` | **SPLIT** | central platform config → data-spine; the `get_settings()` loader → libs/common |
| `user_env.py` | **SPLIT** | manage/write (incl. the **BYOK key**) → control-panel → data-plane encrypted secret; **inject** → operator (tmpfs) → consumed in-pod (operator §6, M6) |
| `compute_user_stats.py` | **control-panel** | **RESHAPE** — admin stats from Prometheus + data-plane |
| `pty_session.py` | **agent-runner** | **LIFT** — PTY runs in the tenant pod |
| `skills.py`, `skill_hub.py`, `subagents.py` | **SPLIT** | config/catalog → control-panel; files + test execution → agent-runner (*skill-file storage locus = OQ-1*) |
| `temp_files.py` | **agent-runner** | **LIFT** |
| `paths.py` | **libs/common** | **RESHAPE** — multi-tenant paths derived in-pod (one pod = one account) |
| `_pagination.py` | **libs/common** | **LIFT** |

### 4.7 `priva/api/` other

| Path | → | Verb |
|---|---|---|
| `main.py` (225) | **SPLIT** | each deployable gets its own entrypoint; router fan-in (`:203-219`) splits per §5; CORS (`:181`)→edge; SPA mount (`:79`)→control-panel faces; OpenClaw lifespan (`:133-153`)→connector; temp-cleanup (`:131`)→agent-runner |
| `models/*` (15) | **libs/common** | **LIFT** — shared pydantic DTOs (agent, auth, channels, scheduler, hooks, mcp, skills, skill_hub, subagents, resource, admin, admin_files, plugin, user_env) |
| `utils/crypto.py` | **libs/common** | **LIFT** — envelope crypto shared by data-spine (store) + operator (unwrap) |
| `utils/sensitive_mask.py`, `script_lint.py` | **libs/common** | **LIFT** |
| `utils/callback.py` | **agent-runner** | **RELOCATE** — follows `scheduler/tool_retry.py` (its only importer; §4.3). Lazily imports `services.user_store` (`callback.py:97`), so it **cannot** lift to `libs/common` without breaking the §6 boundary; RESHAPE to invert that dep only if ever shared. |
| `middleware/logging.py` | **libs/common** | **LIFT** — every service mounts it |
| `metrics.py`, `routers/metrics.py` (`/metrics`) | **libs/common** | **RESHAPE** — each deployable exposes its own `/metrics` |
| `static/*` (scalar/swagger) | **control-panel** | **LIFT** (API docs) or drop |
| `bundled/skills/*` (~200) | **agent-runner** | **LIFT** — baked into the pod image (the agent's capabilities) |
| `config.example.yaml` | per-service | **SPLIT** — each service's config subset |

### 4.8 Repo root

| Path | → | Verb |
|---|---|---|
| `priva/web/` (233) | **web** | **RESHAPE** — two build targets (`/ui`, `/admin`); api-base → edge (control-panel.md §5) |
| `priva/bin/server.sh` | — | **DELETE** — replaced by per-service Dockerfiles + `deploy/` manifests |
| `tests/` (20) | per-service | **SPLIT** — follow the code |
| `requirements.txt` | **SPLIT** | per-service deps + a common base |

---

## 5. Per-route classification (the dual-face routers)

Authoritative over §4 for routers that split. **P** = agent-runner · **CP** = control-panel · **CC** = channel-connector · **SCH** = scheduler · **DS** = data-spine · **SR** = state-reader · **EDGE** = agentgateway.

### 5.1 Single-destination routers

| Router | All routes → | Note |
|---|---|---|
| `agent.py` (848) | **P** | the pod-internal run API. **Exceptions:** `/sessions` list + `/sessions/{id}/messages` get a **wake-free read** alt via **SR** when the pod is asleep (data-spine §3.9); `/permission/respond` collapses into the WS and is **brain-relayed** (agent-gateway §7.3) |
| `admin.py` (745) | **CP** | admin API. `/scheduler/*` reads pull from **DS**/SCH; `/clipath` (`:508-515`) is single-machine → **DELETE** (OQ-3) |
| `admin_files.py` (227) | **CP via SR** | cross-user browse through the broad RO mount, never local FS, never wakes a pod (control-panel.md §0.2) |
| `user_data.py` (188) | **CP** | self-service stats/audit/analytics; reads DS (wake-free) |
| `files.py` (74), `user_files.py` (271) | **P** | the tenant's workspace files (per-tenant PVC); reads may use **SR** wake-free |
| `metrics.py` (20) | **all** | every service exposes `/metrics` (libs/common) |

### 5.2 Split routers

| Router | Route(s) | → | Why |
|---|---|---|---|
| **auth.py** | `/login`,`/setup`,`/refresh` | **CP** | brain issues the platform JWT; **EDGE** verifies its signature |
| | `/me`, `/me/password`, `/me/apikey*` | **CP** | identity self-service → DS |
| | `/me/env*` (`:192-219`) | **CP**→DS | per-tenant env incl. **BYOK key** (encrypted; operator injects) |
| **hooks.py** | `/catalog*`, `/config`, `/admin*` | **CP** | which hooks are enabled (config → DS) |
| | `/test`, `/test/builtin`, `/script/content` | **P** | executes / reads in the tenant space |
| | `/logs` | **CP via DS** | hook run logs (written by pod, read by admin) |
| **channels.py** | `/wecom/config`, `/openclaw/config` (GET/PUT) | **CP**→DS | channel config (replaces `config_store.py`) |
| | `/*/connect`,`/disconnect`,`/reconnect` | **CC** | operate the socket/lease (CP admin → CC control seam, :8081) |
| | `/*/status`, `/health` | **CC**→CP | channel-health condition (agent-gateway §5.2) |
| **mcp.py** | `/` CRUD, `/{lvl}/{name}` CRUD | **CP**→DS | MCP server config |
| | `/capabilities`, `/validate`, `/validate/tool` | **P** | connects to the server (creds in-pod) — *OQ-4* |
| **skills.py** | `/`, `/config`, `/{…}` meta | **CP** | catalog/config |
| | `/upload`, `/{…}/file`, `/download`, delete | **P** | skill files in tenant space — *OQ-1* |
| **skill_hub.py** | all (`/upload`,`/`,`/{name}*`,`/deliver`) | **CP** | central skill catalog; `/deliver` writes to a tenant volume — *OQ-1* |
| **subagents.py** | `/catalog`,`/list`,`/{name}` CRUD | **CP**→DS | subagent definitions |
| | `/{name}/test/stream` | **P** | executes |
| **resource.py** | `/quickactions`, `/vision-model` (GET/PUT) | **CP**→DS | user config |
| | `/models` | **CP** | model list (BYOK-provider-dependent) — *OQ-5* |
| **pty.py** | `/api/pty/ws`, `/api/pty/feature` | **P** | terminal into the tenant pod |
| | `/api/admin/pty/config` | **CP** | platform feature config |
| **scheduler.py** | `/jobs*` CRUD, `/pause`,`/resume`,`/trigger`,`/reload`,`/history` | **CP**→DS | job definitions (user/admin-facing) |
| | `/running`, `/running/{id}/output` | **P**/DS | the pod runs the job; output via DS/SR |
| | `/running/{id}/cancel` | **EDGE/brain** | stop-a-turn Redis signal, **not** scheduler (blueprint §208) |
| | `/lint-script` | **CP** | validation; `script_lint` → libs/common |
| | `/health` | **SCH** | scheduler service health |

---

## 6. `libs/common` — the shared contract layer

The one package every service depends on (a `uv` workspace path-dep). Contents:

- **Wire format** — `serialization.py` (pod serializes; connector fans out; agentgateway streams the bytes).
- **Models** — all of `models/*` (the DTOs that cross the wire).
- **Data-plane client** — **NEW** gRPC client stubs generated from `protos/` into **`priva_common.dataplane`** (one shared client used by pod, brain, connector, scheduler, operator). `grpcio`/`protobuf` join `priva-common`'s deps at Phase 1. See §3.1.
- **Redis catalog** — **NEW** the T1/T2 key definitions + helpers: `inbox:{account}`, `route:{account}`, `awake:lock`, `approval:index`, `channelconn:lease`, `lock:session`, `job:{id}:fire:{epoch}` (data-spine §4) — one definition, imported everywhere, so keys never drift.
- **Auth primitives** — JWT decode/verify + the brain's signing helpers (brain mints, pod verifies via JWKS — agent-runner §13-1).
- **Crypto** — `utils/crypto.py` (data-spine stores, operator unwraps).
- **Settings loader** — `get_settings()` from `config.py` (each service loads its own subset).
- **Observability** — `middleware/logging.py` + the `/metrics` helper.

> Rule: `libs/common` may **not** import any service. Services import `common` + their own package only. This is the import boundary that keeps the carve clean (enforced in Phase 0).

### 6.1 Extraction order within `libs/common` (dependency-grounded; **executed 2026-06-20**)

Move order is forced by the real import edges (measured on the monolith). Each move uses a **re-export shim**: the real code lands in `priva_common`, the old `api.*` path becomes `from priva_common.X import *`, so the monolith keeps booting and **no importer changes** until its service is extracted. Importer counts below are distinct importing files (re-measured 2026-06-20 across all import forms, shim excluded); they gauge blast-radius — the *order* follows the import edges, not the counts. **Status: all seven landed (phase-0.md), boot-green after each, boundary verified CLEAN.**

1. **`config`** (leaf — stdlib + pydantic only; 17 importers). **RESHAPE, not a pure move:** today `Settings.yaml_file = Path(__file__).parent.parent / "config.yaml"` (`config.py:137`) is `__file__`-relative and breaks once the file moves. Repoint to an env var **`PRIVA_CONFIG_FILE`**, **defaulting to the monolith's `priva/api/config.yaml`** (an absolute path — *not* CWD-relative `./config.yaml`, which varies per daemon). `server.sh` should `export PRIVA_CONFIG_FILE` to match its existing `CONFIG_FILE` (`server.sh:13`). **Keep `yaml_file` a `Path` ClassVar** — `logging.py:319` resolves relative log paths via `Settings.yaml_file.parent.parent / path`, so the default must keep `.parent.parent` pointing at the monolith root (config↔logging coupling — see step 3). `config.yaml` is absent today (only `config.example.yaml`); the missing-file case must stay tolerated (pydantic defaults apply).
2. **`metrics`** (leaf — `prometheus_client` only; 2 importers). **Must precede `logging`:** `logging.py:280` imports `HTTP_DURATION` / `HTTP_REQUESTS` from `..metrics`. That import is **lazy** (inside the request path), so the boot-check won't catch a broken `metrics` import — exercise/inspect a real request after the move. **RESHAPE:** each deployable exposes its own `/metrics`.
3. **`logging`** (imports `config` **and** `metrics`; 42 importers) — `get_app_logger` / `AccessLogMiddleware` / `configure_logging`. When it moves, re-point its `..metrics` import to `priva_common.metrics` and its `Settings.yaml_file` use to `priva_common.config`.
4. **`crypto`** + **`_pagination`** (each imports `logging`; 1 + 3 importers) — clean once logging is in.
5. **`models/*`** (37 importers) — the shared DTOs.
6. **`serialization`** (imports `models` **and** `claude_sdk/retry.SYNTHETIC_MODEL`, a *pod* module; 1 importer). **RESHAPE:** lift `SYNTHETIC_MODEL` (a trivial string constant, `retry.py:17`) into `priva_common.wire` so `serialization` carries no pod dependency; `retry.py` re-imports it.
7. **`redis_catalog`** (NEW — pure addition, no shim).

Acceptance per move: `PYTHONPATH=priva:libs/common/src python -c "import api.main"` still resolves (uv not required for this check). Because the `logging→metrics` edge is lazy, additionally confirm a request actually records metrics after step 3.

---

## 7. The inversion (the seam everything hangs off)

Today `service.py`'s `agent_run_events` has **three in-process callers** (§2). The split **severs** the scheduler and channels calls:

```
TODAY                                   AFTER
agent router  ─┐                        agent router (in-pod) ─┐
scheduler daemon ─┼─ in-process ─▶ run  scheduler ─ dispatch ─┼─▶ POD runs the turn
channels daemon ─┘                      connector ─ dispatch ─┘
```

- **Scheduler** (`daemon.py:299-305`) stops importing `claude_sdk`; it RPUSHes `inbox:{account}` + CR-patches wake (scheduler.md).
- **Connector** (`daemon.py:786-794`) stops importing `claude_sdk`; it calls the brain RPC, then dials the woken pod (agent-gateway §4.4 option A).
- **`service.py` ships only in the agent-runner image.** This is why §4.3 RELOCATEs the scheduler executors to the pod — they too run the agent.

Verifying these two imports are gone is the **acceptance test** for Phase 4.

---

## 8. Persistence: stores → data-plane

Every file/YAML-backed store becomes a data-spine RPC. This theme cuts across services and is sequenced first (Phase 1) because everything depends on it.

| Today | → data-spine |
|---|---|
| `user_store.py` (users) | `account` + `identity_link` RPCs (§1.7) |
| `channels/config_store.py` (YAML) | `channel_config_wecom/_openclaw` (§2.12) |
| `scheduler/job_store.py` (YAML) | `scheduled_job` (§2.11) |
| `scheduler/run_history.py` (JSONL) | `job_run` (§2.11; birth/outcome split) |
| `hooks/log_store.py` | hook-logs RPC |
| `hooks|mcp config_manager.py` | per-tenant config RPCs |
| `audit_log.py` | audit RPC |
| `config.py` (platform) | central config RPC |
| **session JSONL** | **NOT a store** — stays the per-tenant PVC; read via **state-reader** (M5: no session table) |

---

## 9. Stateful → stateless

The brain/connector/scheduler must hold **no** agent state (agent-gateway §1):

- `permission_coordinator.py` — module-global `Future` singleton → **in-pod only** (the rendezvous lives where the run lives; the brain relays via `approval:index`, agent-gateway §7).
- `main.py` OpenClaw bridges in lifespan (`:133-153`) — in-process sockets → **connector** under the lease (one owner/bot).
- `channels`/`scheduler` file-command buses + state files → **Redis** (pub/sub + durable keys).
- in-process `conn.sessions` / `conn.pending` maps → central `channel_binding` + `approval:index` (no per-process maps).

---

## 10. Reshape-verb legend

| Verb | Meaning |
|---|---|
| **LIFT** | move file as-is to its new package |
| **RESHAPE** | move + change behavior (store→RPC, in-process→dispatch, singleton→per-pod) |
| **SPLIT** | endpoints/functions divide across deployables (see §5) |
| **RELOCATE** | moves to a non-obvious home (scheduler executors → agent-runner) |
| **DELETE** | removed; a platform mechanism replaces it |
| **NEW** | no current code (operator, state-reader, data-plane client, Redis catalog, ext_proc brain) |

---

## 11. Extraction sequence (strangler, data-spine-first)

Each phase is independently shippable and reversible; the monolith keeps running until Phase 4.

| Phase | Goal | Acceptance |
|---|---|---|
| **0** | Monorepo skeleton + **in-place** boundary refactor. Create `libs/common`, `services/*` dirs; extract serialization, models, crypto, pagination, metrics, settings, redis-catalog into `common`; draw the import boundary (§6 rule). **Still one running monolith.** | `common` imports nothing service-side; tests green |
| **1** | `protos/` + **data-spine** service + **`tools/cli`** (the `priva-cloud` launcher, §14, lands with the first runnable service). Define the gRPC contracts; stand up the service over the DB; swap all store call-sites (§8) to the data-plane client. | `user_store`/`config_store`/`job_store`/`run_history`/`audit_log` call-sites go through the client; `priva-cloud data-spine` boots the service |
| **2** | **agent-runner**. Package `claude_sdk` + agent router (pod-internal) + hooks/mcp/skills execution + pty/files + relocated scheduler executors; run API reads the signed `account_id`; Future in-pod. Build the image (+ bundled skills). | pod serves a turn end-to-end behind a signed header |
| **3** | **control-panel/brain**. ext_proc brain (`resolve·mint·wake·steer` as one factored fn — agent-gateway §4.4) + admin/auth/config routers + serve `web` builds; holds no agent state. | brain steers a browser turn; admin API on data-plane |
| **4** | **Lift connector + scheduler.** Strip the in-process `agent_run` from both (§7 acceptance); connector gets the Redis lease + brain RPC (option A); scheduler gets leaderless exactly-once + dispatch. | neither imports `claude_sdk`; IM + cron turns run on the pod |
| **5** | **operator + state-reader** (NEW infra). kopf AgentTenant controller (sole scaler); RO JSONL reader. | scale 0↔1 via CR; wake-free session reads |
| **6** | **K8s wiring.** Per-service Dockerfiles, manifests, NetworkPolicies, agentgateway CRDs, two web builds served by control-panel. | full path: browser/IM/cron → edge → pod |

---

## 12. Open items (decide during extraction)

| # | Item | Options |
|---|---|---|
| **OQ-1** | **Skill / skill-hub file storage locus.** User-uploaded skills + hub `/deliver` need a home (no object storage — C2). | per-tenant PVC (pod-served, state-reader read) **vs.** a central skill store in data-plane |
| ~~OQ-2~~ | **Monorepo Python workspace tool — RESOLVED 2026-06-19: `uv` workspace.** | native path-dep workspaces, fast, clean for a from-scratch monorepo |
| **OQ-3** | **`/clipath`** (single-machine CLI path config) | DELETE (pod bakes the CLI) — likely; confirm nothing else reads it |
| **OQ-4** | **MCP `/validate` + `/capabilities` locus** | agent-runner (creds live there) **vs.** a control-panel sandbox |
| **OQ-5** | **`/models` list source** under BYOK | static price-card/config (control-panel) **vs.** probe the user's provider (pod) |

None block Phase 0–1. **OQ-2 is resolved (`uv` workspace);** the remaining items (OQ-1/3/4/5) are decided when their phase lands.

---

## 13. Local launch / dev mode

**Yes — every subsystem can run on a laptop, and the split makes this *easier*, not harder.** Today the three daemons import `claude_sdk` in-process (tangled); after the split every boundary is gRPC/HTTP/Redis, so each service runs standalone and is wired by env vars pointing at `localhost` instead of cluster Service DNS.

**Runs as a plain process (no K8s):** data-spine (gRPC + SQLite/Redis), control-panel (its 3 listeners), agent-runner (one process per dev tenant), channel-connector (+ Redis lease + WeCom socket), scheduler (+ Redis), state-reader, web (Vite dev). All just `uv run`.

**The only K8s-coupled behaviors** (everything else is portable):
- **operator (kopf)** — reconciles `AgentTenant` CRDs + scales Deployments; with no K8s there is nothing to reconcile.
- **scale-0↔1 / wake** — the brain's "CR-patch wake" assumes the operator scales pods.
- **agentgateway** — the Rust edge; but it runs as a **standalone binary with a static config**, not only via CRDs.

**Two local modes:**
- **(A) compose, NO K8s — the everyday loop:** all Python services + web as processes; **agentgateway as a local binary** (static config) *or* a brain dev-edge that skips it; **no operator** — agent-runner(s) always-on, and the brain's wake becomes "ensure the pod is reachable." Full request path locally, minus autoscaling. Fast.
- **(B) local K8s (kind/k3d) — full fidelity:** the real operator + CRDs + agentgateway CRDs + scale-0↔1, for testing the wake/scale dynamics.

**Design rule this imposes on the split (so mode A stays trivial):** the two K8s-coupled seams — **wake/scale** (brain→operator) and **steer/edge** (brain↔agentgateway) — each sit behind a small interface with a **dev impl** (always-on pod + localhost steer) and a **prod impl** (CR-patch + EPP), swapped by profile/env. One seam per service; the K8s coupling never leaks into business logic. (Phases 0–2 are plain processes anyway, so local-launch is free until the brain/operator land.)

---

## 14. Service launcher — `priva-cloud` (one command, everywhere)

A single console-script is the front door to every service, replacing today's
`priva/bin/server.sh`. It lives in the **`tools/cli`** workspace member
(`priva_cli`, dist `priva-cloud`, `[project.scripts] priva-cloud = "priva_cli.__main__:main"`)
and lands at **Phase 1** with the first runnable service; each later service contributes one
entry-point line.

```
priva-cloud serve            [--only a,b] [--reload] [--config F]   # supervise all (succeeds server.sh)
priva-cloud agent-runner     [...]      # → priva_agent_runner.entry:main
priva-cloud control-panel    [...]      # 3 listeners; may mount the state-reader router (§3.2)
priva-cloud channel-connector[...]
priva-cloud scheduler        [...]
priva-cloud data-spine       [...]
priva-cloud operator         [...]      # local mode A usually skips
priva-cloud state-reader     [...]      # standalone; otherwise embedded in control-panel
```

**Boundary-safe by construction — discovery, not imports.** Each service self-registers in
its own `pyproject.toml`:

```toml
[project.entry-points."priva_cloud.services"]
agent-runner = "priva_agent_runner.entry:main"
```

`priva_cli` discovers services via `importlib.metadata.entry_points(group="priva_cloud.services")`
and **imports nothing service-side** — it honors the §6 boundary just like every other member.
Dispatch is the entire coupling surface:

```python
# discovery.py
from importlib.metadata import entry_points
def registered():
    return {ep.name: ep for ep in entry_points(group="priva_cloud.services")}
# __main__.py
def main(argv=None):
    sub, rest = parse(argv)
    if sub == "serve": return serve.run(rest)         # supervises all discovered (subprocess each)
    ep = registered().get(sub) or die(f"{sub} not installed")
    return ep.load()(rest)                            # lazy import → entry.main(rest)
```

- **Local dev:** `uv sync` installs the whole workspace → every subcommand works, incl. `serve`.
- **Prod image:** install `priva-cloud` + the one service it ships → that subcommand works;
  `serve` (all) simply isn't used there. `python -m priva_<svc>` is the always-available fallback.
- **`serve`** is a pure supervisor: discovers the registered set (honoring `--only`), spawns each
  as a `priva-cloud <sub>` subprocess, prefix-aggregates logs, forwards signals — the direct
  successor of `priva/bin/server.sh`'s three-daemon launch.

**Target per-member `pyproject.toml` shape** (documented here; files are created lazily per phase,
not now): `name = "priva-<svc>"`, `dependencies = ["priva-common", …]`, the
`[project.entry-points."priva_cloud.services"]` stanza above, and src-layout `[tool.hatch.build]`
pointing at `src/priva_<svc>`. `tools/cli` adds the `[project.scripts]` console-script instead of a
service entry-point. (Optional during transition: a `priva-cloud legacy` subcommand wrapping
`priva/bin/server.sh` for parity until Phase 4.)

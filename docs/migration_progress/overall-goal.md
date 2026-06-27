# Priva Cloud migration — overall goal, plan & progress index

> **Living handoff doc.** This directory (`docs/migration_progress/`) is the *execution
> journal* for splitting the Priva monolith into multi-tenant deployables, run **locally**.
>
> **If you are an agent picking this up:** read this file top-to-bottom first, then the
> canonical design docs (§2), then open the active phase doc (§6 index) and continue from
> its Status block. Do **not** start editing code before you have done that.
>
> This file holds the goal, the working rules, the plan, and the progress index.
> Per-phase detail (design decisions, actions, verification) lives in `phase-N.md`.

---

## 0. Current state (update this whenever it changes)

- **Phase:** Phases 0–2 done earlier; a **vertical slice across Phases 1/3/5/6 now runs end-to-end on
  minikube** — per-account agent-runner pods provisioned on user-add, scaled 0↔1 by a kopf operator, and
  routed to via agentgateway + an ext_proc EndpointPicker. As-built: [`phase-3-agentgateway-operator.md`](phase-3-agentgateway-operator.md).
- **Branch:** the slice landed on `feat/agentgateway-operator-epp` and is **merged to `main`** (2026-06-21).
- **Done (the slice):** config (`PRIVA_` env override + K8s/Edge settings); **data-spine** real gRPC server +
  client + Fernet **secret store** (Phase 1's live transport); **operator** (kopf `AgentTenant` CRD, sole
  scaler 0↔1, wake → secret-inject → scale, idle sweep — Phase 5); **control-panel** ext_proc **EPP** brain +
  K8s provisioner + secret-store creds (Phase 3); **agentgateway edge** + InferencePool + HTTPRoute (Phase 6);
  Dockerfiles for all 4 services. **Verified on minikube:** add user → AgentTenant CR → scale-to-zero pod →
  runtime request through agentgateway → EPP wakes the pod → routes (warm + cold, 4.1s from zero); idle → 0.
- **The crack:** the runtime path was blocked for a long debug session by `InvalidContentType` — root cause:
  agentgateway dials the InferencePool EPP over **TLS** (GIE convention; captured ClientHello), our EPP was
  plaintext. Fix: EPP serves TLS (self-signed, skip-verify). See [agentgateway-epp-tls memory] + the phase doc.
- **Transport (alpha):** everything is **HTTP/plaintext** except that one forced EPP hop (agentgateway→EPP
  TLS, self-signed). Real HTTPS/mTLS/JWKS/edge-TLS are deferred.
- **Next:** a live LLM run (needs real `ANTHROPIC_*` creds); Phase 4 (scheduler + channel-connector); prod
  hardening (Redis coordination, mTLS, NetworkPolicies, per-account DEK/KMS).
- **Admin UI surface (2026-06-26):** the dashboard ships Fleet · Resource-Quota · System-Map · Console ·
  Users · Audit + a Configurations→**Agent Runner Sandbox** panel (global runner-defaults, as-built
  2026-06-25). Cross-referenced against `control-panel.md` §6.1, these §6 surfaces **remain** (detail +
  priorities in **`control-panel.md` Appendix B**, the authority): **Settings** (six runtime-config
  endpoints exist + audited at `admin.py:1025-1241` but lost their UI in the SPA split — pure-UI pickup,
  no backend work); **pod-events / live-activity feed** (§6 landing strip — surfaces the ImagePull/OOM/GC
  failure modes, today invisible); **pod-lifecycle dangerous-ops** (no terminate/wake/offboard/impersonate
  admin endpoint; `provisioner.terminate` exists but is unsurfaced); **Policies** (agentgateway CRD
  view/edit — unbuilt); **Budgets** token-usage view + Prometheus client (§9 — unbuilt). Correctly
  deferred (not gaps): Budgets `$`/ledger (M6), Sessions/state-reader (Phase 5), scheduler/connector/
  plugins (Phase 4).
- **control-panel de-history cleanup (2026-06-26):** dead-code / legacy-reference sweep of
  `services/control-panel/` + `web/admin/src/` (multi-agent scan, repo-wide verified). Removed: the
  dead-on-arrival per-user inspect feature (UserInspectPanel + 6 admin 503 stubs + store/API wiring),
  global scheduler & plugins 503 stubs, the monolith-carryover `/stats` chain, dead store/`cwd` state;
  rewrote all `proxy.py`/`priva-web`/`priva-api` legacy references; migrated `CategoryDropdown` + two
  native `<select>` to the shared `Dropdown`. Backend boot-green, admin SPA builds. Detail =
  **`control-panel.md` Appendix B.5**. Out of scope (separate user-app pass): `web/shared/api/admin.js`
  config fns + `web/user` SettingsPanel/PluginsTab.
- **agent-runner + user-SPA de-history + enforcement cleanup (2026-06-27):** same sweep applied to
  `services/agent-runner/` + `web/user/src/`, preceded by a 41-agent endpoint-map workflow (all **74
  endpoints → ability → code**, adversarially verified). Persisted the as-built API into
  **`agent-runner.md` Appendix A** (closes the "session reads" doc gap). Changes: (1) **enforcement model** —
  dropped vestigial admin-role gates on skills/MCP/skill-hub + the `user_files` explorer (now browses the
  whole pod filesystem, gated only by the sandbox uid — single-tenant pod) (→ `require_user`),
  removed the `/api/hooks/admin` management trio (enforcement read-path via `hooks/builder.py` retained),
  dropped the unused `require_admin` alias — *only admin **hook** enforcement remains*; (2) **self-serve upload** — the
  UserData→File Explorer uploads to each account's own pod via `POST /api/user/files/upload`, admin-upload
  path + `api/adminFiles.js` deleted; (3) **removed dead-on-arrival** Scheduler + Channels frontend (Phase-4
  deferred, no backend) and the unrendered `PluginsTab` + dead `fetchAdminHooks`; (4) **de-history** —
  de-shimmed `serialization.py`, removed the per-build skill-symlink shim, neutralized monolith comment
  refs; (5) **design-system** — migrated 6 native `<select>` + 5 `CategoryDropdown` to the shared
  `Dropdown`. Backend boot-green (hooks 12→9 routes), user SPA builds. Out of scope (flagged): `web/shared`
  orphaned plugin fns; `/rewind` 409 PTY/pending-approval gap (`agent-runner.md` A.3).
- **Last updated:** 2026-06-27.

---

## 1. The goal

Turn the single-tenant Priva monolith into a **multi-tenant Kubernetes platform ("Priva Cloud")**: many tenants, each with an isolatable agent runtime, served by independently deployable, independently scalable subsystems.

**Today** (verified — `code-split.md` §2): one FastAPI monolith under `priva/api` (~140 app `.py` + ~200 bundled skill assets), one React app `priva/web`, run as **three OS processes** by `priva/bin/server.sh` (uvicorn API + scheduler daemon + channels daemon). All three import the agent runner in-process — that coupling ("the inversion") is the seam the whole split hangs off (`code-split.md` §7).

**Done looks like:** the monolith is carved into the **8 deployables** below, each a `uv` workspace member with its own image, wired only by gRPC/HTTP/Redis (no in-process imports across boundaries), running on K8s with per-tenant scale-to-zero — and runnable end-to-end on a laptop (`code-split.md` §13).

| Deployable | Role |
|---|---|
| `agent-runner` | per-tenant agent runtime (run/stream/permission/fork/rewind) — the **only** thing that runs the agent |
| `control-panel` | brain (ext_proc routing) + admin API + faces — **one process, three listeners** (`:9000` gRPC, `:8080` faces, `:8081` internal) |
| `channel-connector` | WeCom/OpenClaw outbound socket + Redis lease + IM fan-out |
| `scheduler` | leaderless fire → claim → wake → dispatch |
| `operator` | `AgentTenant` CRD controller (kopf); the **sole** pod scaler |
| `data-spine` | Accounts/Identities/Sessions/Jobs RPC + Redis catalog (replaces all file/YAML stores) |
| `state-reader` | read-only JSONL transcript reader (wake-free session reads) |
| `web` | the React app(s), built and served by control-panel |
| `libs/common` | shared contract layer (`priva_common`) — imported by services, imports **no** service |

---

## 2. Source of truth (canonical — read, don't duplicate)

These are authoritative. This journal *references* them; it must not restate or fork their design. If execution reveals one is wrong, **fix the canonical doc** and note it in the phase doc.

| Doc | What it owns |
|---|---|
| `docs/architecture/multi-tenant-platform.md` | the master architecture (the whole platform) |
| `docs/architecture/code-split.md` | **the bridge** — per-file map (§4), dual-face router classification (§5), `libs/common` contract + dependency-ordered extraction (§6/§6.1), the inversion (§7), persistence migration (§8), the 7-phase strangler sequence (§11), open items (§12), local-dev modes (§13) |
| `docs/architecture/components/agent-runner.md` | the pod runtime drill |
| `docs/architecture/components/control-panel.md` | brain + admin + faces; the one-process/three-listeners model (§0.1) |
| `docs/architecture/components/agent-gateway.md` | the Rust edge **and** the channel-connector → brain call (§4.4, locked option A = internal RPC) |
| `docs/architecture/components/data-spine.md` | the data plane + gRPC contracts |
| `docs/architecture/components/scheduler.md` | leaderless scheduling drill |
| `docs/architecture/components/operator.md` | the kopf CRD controller drill |

> **Note:** there is no `channel-connector.md` — the connector is drilled inside `agent-gateway.md` §4.4 and `control-panel.md` §0.1. Read both for connector work.

---

## 3. How to work (methodology — non-negotiable)

1. **Verify, don't assume.** Ground every claim in `file:line`. Read the code before you move it. If a canonical doc and the code disagree, the code is the truth — reconcile and note it.
2. **Strangler discipline.** The monolith keeps running and booting at every step until Phase 4. Changes are **additive first**: move real code into `priva_common.X`, then leave a **re-export shim** at the old `api...X` path (`from priva_common.X import *`) so existing importers don't churn. Delete the old path only when its last importer is gone.
3. **One verified increment at a time.** Do not batch risky moves. Each increment = a small, named change + a passing acceptance check + a commit. If a module has a landmine (e.g. `config.py`'s `__file__`-relative path, `code-split.md` §6.1), give it its own increment.
4. **Boot-green is the floor.** After every increment, the monolith must still import (§4 gate). Never leave the tree red across a commit.
5. **Journal as you go.** When you start/finish an increment, update the phase doc's Status + Verification log, and update the §6 progress index in *this* file. The journal is how the next session resumes without re-reading everything.
6. **Decide open items in their phase, not early.** `code-split.md` §12 lists OQ-1/3/4/5; each is resolved when its phase lands, and the resolution is recorded in that phase doc + back-annotated into `code-split.md` §12.

---

## 4. Operational runbook (local)

**Repo / branches.** This is a **fresh repo** cut from a prior Priva branch, dedicated to the split. Migration work lands **directly on `main`** — there are **no** per-phase `split/phase-N` branches. (Cut a short-lived topic branch only if a specific increment warrants isolation; the default is `main`.)

**The boot-check gate** (verified passing 2026-06-19) — run after every increment in Phases 0–3 (while the monolith still exists):
```bash
PYTHONPATH=priva:libs/common/src python -c "import api.main; print('BOOT OK')"
```
**There is no automated test suite yet** (`priva/` has no `tests/`, no `pytest.ini`/`conftest.py`). So the boot-check *is* the regression gate for the strangler phases. Standing up a minimal smoke test (import + a couple of route assertions) is a worthwhile early action — note it where you add it.

**Run the monolith locally:** `priva/bin/server.sh` (uvicorn API + scheduler + channels daemons). Per-service local runs after the split: `code-split.md` §13 — two modes: **(A)** plain processes, no K8s (the everyday loop), **(B)** kind/k3d full fidelity.

**`uv` workspace.** The monorepo uses a `uv` workspace (root `pyproject.toml`, members `libs/* services/* tools/*`). **`uv` is not installed by default here** — `pip install uv`, then `uv sync`. You do **not** need `uv` for the boot-check above (it uses `PYTHONPATH`); you do need it to run services standalone (`uv run`).

> **Gotcha — `uv sync` prunes the boot-check venv.** The legacy monolith (`priva/`) is **not** a workspace member, so its deps (`fastapi`, `uvicorn`, …) aren't tracked by `uv sync`; running `uv sync` reconciles `.venv` to the workspace and **removes** them, which breaks the boot-check. Local setup that keeps both working: `uv venv && uv pip install -r requirements.txt` (monolith deps) — and after any `uv sync`, re-run `uv pip install -r requirements.txt` to restore them. (This is transient: the monolith goes away at Phase 4.)

**Git / push policy — read carefully:**
- **Push ONLY to the `github` remote** (`git@github.com:onionknightdd/priva.git` — public). This is the only allowed push target.
- **NEVER push to `origin`** (`git@code.weoa.com:...` — internal gitee). Not by accident, not "just this once."
- Every commit message ends with the trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Network git ops (push/fetch) in this sandboxed environment require `dangerouslyDisableSandbox: true` on the Bash call.
- **Do not push WIP** (a phase that doesn't boot green) without explicit user confirmation. Local commits are fine and encouraged; pushing is a publish.

---

## 5. The plan (7 phases — strangler, data-spine-first)

Source: `code-split.md` §11. Each phase is independently shippable and reversible; the monolith runs until Phase 4. Full per-phase detail is in `phase-N.md`.

| Ph | Goal | Key deliverables | Acceptance gate |
|----|------|------------------|-----------------|
| **0** | Monorepo skeleton + in-place boundary refactor | `libs/common` + `services/*` dirs; extract serialization/models/crypto/pagination/metrics/settings/redis-catalog into `common` via shims; draw the import boundary | `priva_common` imports nothing service-side; **boot-check green** |
| **1** | `protos/` + **data-spine** service | gRPC contracts; data-spine over the DB; swap all store call-sites (§8) to the client | `user_store`/`config_store`/`job_store`/`run_history`/`audit_log` call-sites go through the data-plane client |
| **2** | **agent-runner** | package `claude_sdk` + pod-internal agent router + hooks/mcp/skills exec + pty/files + relocated executors; read signed `account_id` | pod serves a turn end-to-end behind a signed header |
| **3** | **control-panel / brain** | ext_proc brain (`resolve·mint·wake·steer` as one factored fn — agent-gateway §4.4) + admin/auth/config routers + serve `web`; holds no agent state | brain steers a browser turn; admin API on the data plane |
| **4** | **Lift connector + scheduler** | strip in-process `agent_run` from both (§7); connector gets Redis lease + brain RPC (option A); scheduler gets leaderless exactly-once + dispatch | neither imports `claude_sdk`; IM + cron turns run on the pod |
| **5** | **operator + state-reader** | kopf `AgentTenant` controller (sole scaler); RO JSONL reader | scale 0↔1 via CR; wake-free session reads |
| **6** | **K8s wiring** | per-service Dockerfiles, manifests, NetworkPolicies, agentgateway CRDs, two web builds served by control-panel | full path: browser/IM/cron → edge → pod |

---

## 6. Progress index  ← keep this current

Status legend: `not started` · `in progress` · `blocked` · `done`. Update the row whenever a phase's status or branch changes (and mirror it in §0).

| Phase | Doc | Status | Branch | Last updated |
|-------|-----|--------|--------|--------------|
| 0 — skeleton + boundary refactor | [phase-0.md](phase-0.md) | done | `main` | 2026-06-20 |
| 1 — protos + data-spine | [phase-1.md](phase-1.md) | done (in-process earlier; **gRPC live** in the slice) | `main` | 2026-06-21 |
| 2 — agent-runner | [phase-2.md](phase-2.md) | done | `main` | 2026-06-21 |
| 3 — control-panel / brain (EPP) | [phase-3-agentgateway-operator.md](phase-3-agentgateway-operator.md) | done (minikube alpha) | `main` | 2026-06-21 |
| 4 — lift connector + scheduler | [phase-4.md](phase-4.md) | not started | — | 2026-06-19 |
| 5 — operator + state-reader | [phase-3-agentgateway-operator.md](phase-3-agentgateway-operator.md) | done (operator; state-reader deferred) | `main` | 2026-06-21 |
| 6 — K8s wiring (agentgateway) | [phase-3-agentgateway-operator.md](phase-3-agentgateway-operator.md) | done (minikube alpha) | `main` | 2026-06-21 |

---

## 7. Phase-doc protocol

Every phase has a `phase-N.md` in this directory. Keep them **living** — they are the resume point. When you change a phase's status, also update §0 and the §6 index here.

**Status lifecycle:** `not started` → `in progress` → (`blocked` ↔ `in progress`) → `done`. A phase is `done` only when its acceptance gate (§5 / the phase doc) is met and recorded in the Verification log.

**Required structure for each `phase-N.md`:**

```markdown
# Phase N — <title>

**Status:** not started | in progress | blocked | done
**Branch:** main        **Depends on:** Phase N-1
**Canonical refs:** code-split.md §…, components/<doc>.md §…

## 1. Objective & scope
What this phase delivers; what is explicitly out of scope.

## 2. Design / approach
The how. Reference canonical docs (don't restate). Record phase-specific
design decisions made during execution, with file:line evidence.

## 3. Actions (checklist)
- [ ] increment-1: <small named change>
- [ ] increment-2: …
Break risky moves into their own increments.

## 4. Acceptance criteria
The exact, runnable gate(s) that mean this phase is done.

## 5. Open items resolved here
OQ-x → decision + rationale (back-annotate code-split.md §12).

## 6. Verification log (append-only)
- <date> increment-N: <command run> → <result> (file:line evidence)

## 7. Status & handoff notes
Where it stands; what the next session should do first.
```

**The flow each work session:** read this file → open the active `phase-N.md` → continue from its Status/handoff → make one increment → run the acceptance gate → append to the Verification log → update statuses (phase doc + §0 + §6) → commit (push only per §4).

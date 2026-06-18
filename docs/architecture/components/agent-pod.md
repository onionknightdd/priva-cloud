---
Status: Draft · Date: 2026-06-17 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: Agent Pod (scale-to-zero, one per user)
Consumes: ./data-spine.md (M1 + M2 + the two-tier Redis catalog) as a binding contract
---

# Priva Agent Pod — Component Specification

**Scope:** the **Agent Pod** is the fork-and-strip of today's `priva/api` uvicorn process. There is **one scale-to-zero pod per account**. It runs the Claude Agent SDK (the `claude` CLI spawned as a subprocess; SDK installed at `/opt/miniconda3/lib/python3.13/site-packages/claude_agent_sdk`, pinned at `0.2.93`); the CLI owns the agent loop and writes session JSONL to disk under `CLAUDE_CONFIG_DIR`. The pod mounts **only** its own RWX subdir (`/export/<account_id>` → `/workspace`) plus its own audit subdir, and is the **sole writer** of its JSONL / PV / file-checkpoints / audit. It runs under hardened `runc` (non-root, drop ALL caps, RO-rootfs; `runtimeClassName` reserved on the CRD for gVisor/Kata escalation). It **consumes** the data-spine contract — it does not re-decide it: the platform-minted lowercase `session_uuid` *is* the CLI `--session-id`, immutable, no remap (M2); paths are derived **in-pod via the SDK** (single-account, so `os.environ` is correct — M5 §2.7), and there is **no central session table** (M5 — the JSONL is the session store); the in-pod `asyncio.Future` permission rendezvous and per-session `asyncio.Lock` stay in-pod, with Redis as a status mirror / pin only. The pod **keeps** the agent run loop, the `PermissionCoordinator`, the options builder, hooks, MCP, PTY, **and the fork + file-checkpoint/rewind paths**. Every load-bearing SDK and priva claim is cited as `file:line` and was verified against the installed code.

This document is the executable contract for the pod: §0 what is reused/stripped/modified, §1 process shape & the de-singletonized factory, §2 the no-remap session lifecycle (M2), §3 session fork & lineage, §4 file checkpointing & rewind, §5 concurrency & the per-pod `RunContext`, §6 the permission coordinator & pin-pod-awake, §7 secrets & BYOK-key boot, §8 idle detection & scale-to-zero, §9 pod security context & mounts, §10 the consolidated code-delta table, §11 the authoritative boot sequence, §12 the resolved-risk register, §13 the resolved decisions (the seven questions the user answered 2026-06-18).

---

## Revision note — M5 (no session table) · 2026-06-18

A binding modification from the user (decided during the Agent-Gateway drill — `components/agent-gateway.md` §3/§13), superseding this spec wherever they conflict: **there is no central `session_index` table — the JSONL is the session store (decision 15).** Consequences for the pod:

- **§2.2 (`is_first_run`)** — no longer sourced from `session_index.status`. It is supplied per turn by the gateway, derived from a **`first_run_done` CAS on `channel_binding`** (IM) or **gateway-mint-on-empty + a disk-existence guard** (WebUI). The §2.5 **reconcile-as-create** check becomes the primary WebUI backstop and is unchanged. There is **no `ClaimFirstRun` RPC**.
- **§2.7 (path derivation)** — `cwd_hash`/`config_home` are **not** persisted centrally. The pod derives the JSONL path with the **in-pod SDK** (the §1.3 single-account `os.environ` exception already makes this correct); the reader plane (`state-reader`) locates files by globbing `projects/*/<uuid>.jsonl`. The whole "persist `cwd_hash` at mint to avoid the SDK sanitizer in the parent" apparatus is moot.
- **§3.4 (fork lineage)** — `fork_parent_uuid`/`RecordForkLineage` move off `session_index` (a `channel_binding`/lineage column or dropped — TBD in the data-spine M5 body rewrite).
- **Unchanged:** the CREATE/RESUME **split** (§2.1/§2.3 — the actual remap fix, which is table-independent), the **lowercase invariant** (§2.8), and the **consistency assertion** (§2.5). M5 changes only *where the create-vs-resume bit comes from*, not the split.

---

## Revision note — M6 (BYOK + metering deferred) · 2026-06-18

A binding modification from the user (blueprint **M6**), superseding this spec wherever they conflict: **every user brings their own real LLM provider key (BYOK); spend tracking is deferred (token-count-only, pod-self-reported); the metering proxy is dropped; the egress security gateway is deferred.** Consequences for the pod (the deep §7/§9.6/§11/§13 body cleanup is **DONE 2026-06-18** — the bodies below are rewritten M6-correct, not just banner-flagged; this note remains as the decision record):

- **§7 SB2 (two secret classes) → ONE class.** No "request-auth key in the proxy / virtual key in the pod" split. The pod holds the user's **own real LLM key** (`ANTHROPIC_AUTH_TOKEN` = the user's key; `ANTHROPIC_BASE_URL` = the provider or a user-set endpoint), operator-injected in the same tmpfs bundle as MCP/env secrets (operator §6). "Credential-free" (§13-3) still holds for **KMS/DEK** (the pod calls no KMS); but the pod now holds a real provider key — its *own user's*, so there is **no cross-tenant risk** (which is *why* BYOK lets the virtual key go).
- **§7 SB8 / §11 B6b (spend) → DEFERRED.** No `spend:reserve` arming, no reserve-before, no `402 budget_exceeded`, no boot spend-reconcile. Instead the pod **self-reports token usage** from each SDK `result` to the data-plane (observability-only — nothing enforced).
- **§7 / §11 B5 (base_url probe) → relaxed.** The signed-sentinel "assert base_url points at the proxy" check is moot (no proxy). A format/reachability sanity check may remain; the anti-bypass-the-meter intent is gone.
- **§9.6 / §13-2 (egress gateway) → DEFERRED.** Outbound HTTP (LLM, MCP-HTTP, hooks, tool web) goes **direct** "for the moment" — no allow-list, no forced traversal, no credential injection at egress (MCP creds ride the operator bundle). The NetworkPolicy default-deny-internet is relaxed (operator §2.4). Re-addable.
- **Unchanged:** everything non-spend/non-egress — the session lifecycle (M2/M5), fork, checkpointing, concurrency, the permission coordinator + pin, the security context + scoped mounts, and the boot sequence's identity/path/audit steps.

**Body rewritten to M5 (2026-06-18):** §2.2, §2.4 (state diagram), §2.5 (quarantine), §2.7 (path derivation), §2.8 (lowercase layers), §3.3–§3.5 (fork lineage / crash recovery), §3.7 F3, §5.5, §10, §12 R4, §13-7. Remaining `session_index` mentions are historical ("withdrawn"/"no") only. See `components/agent-gateway.md` §3/§13 and `data-spine.md`'s M5 revision note.

---

## 0. What is reused / stripped / modified from `priva/api`

| Subsystem (current code) | Disposition | Where it goes / what changes |
|---|---|---|
| Run loop + SDK client (`services/claude_sdk/service.py` `agent_run`/`agent_run_events`/`agent_run_stream`; `ClaudeSDKClient` `async with`) | **KEEP** | One CLI subprocess per run, scoped to the `async with` (`service.py:479,761`); per-run locals (pump, output queue, mid-stream user-msg queue, interrupt-at-tool-boundary). Already N-session-safe. |
| `PermissionCoordinator` + its in-pod `asyncio.Future` (`permission_coordinator.py:14-110`) | **KEEP** | The permission rendezvous stays in-pod (decision 6). |
| Options builder `build_agent_options` (`options.py:125-379`) | **KEEP shape, MODIFY body** | Single CLI-shape seam; gains the CREATE/RESUME split, `CLAUDE_CONFIG_DIR` injection, lowercase normalization, defensive asserts (§2). |
| Hooks (`services/hooks/*`), MCP (`services/mcp/*`), FileCanvas (`mcp/built_in.py`) | **KEEP**, config source re-pointed | Run inside the agent loop; backing config → data-spine `Get/PutHookConfig`, `GetMcpServers`; hook *execution logs* stay per-PV (residual default #2). |
| PTY (`routers/pty.py`, `services/pty_session.py`) | **KEEP** | Live PTY is an idle-grace gate; runs against the per-account `/workspace`. Drop the `/tmp/priva_pty_preexec.log` write (`pty_session.py:75`). |
| Fork seam (`routers/agent.py:546-572` → `sdk_fork_session`) | **KEEP, MODIFY** | Fork is SUPPORTED (data-spine C4). Add lineage registration + sole-writer child write (§3). |
| File checkpoint/rewind (`options.py:262-264`; `routers/agent.py:503-543` `/rewind`) | **KEEP, MODIFY** | Local-disk-only on the PVC; gate on the in-pod lock; no remote `session_store` (§4). |
| `serialization.py` (`serialize_message`/`serialize_result_message`/`get_event_label`) | **KEEP verbatim** | Pure functions, zero state, zero env reads — the wire format the gateway fans out. |
| Retry engine (`retry.py:14-36`), `heal_orphan_tool_uses`/`strip_synthetic_records` logic | **KEEP logic, MODIFY path** | Correct loop hygiene; only the JSONL path derivation is multi-tenant-broken (§2.7). |
| `options.py:260-261` `if session_id: options.resume = session_id` (BOTH create + resume) | **MODIFY** | The M2 remap root cause; split CREATE (`--session-id`) vs RESUME (`--resume`) from the server-supplied `is_first_run` (M5: `channel_binding.first_run_done` CAS / WebUI disk-guard — §2.2). |
| Capture-and-remap (`service.py:469,487-489,508-510,522-523,820-829,847-855,1021-1025`) + `remap_session` (`permission_coordinator.py:126-130`) | **STRIP** | Deleted; replaced by the §2.5 consistency assertion. |
| `stream_id = session_id or str(uuid.uuid4())` (`service.py:684,995`) | **STRIP** | The pod never mints a session id; `session_uuid` is required from the caller (M2). |
| Module-global `registry = PermissionCoordinatorRegistry()` (`permission_coordinator.py:133`) | **MODIFY** | Becomes a per-pod `RunContext` on `app.state`, keyed on immutable lowercase `session_uuid` (§5). |
| `_vision_sessions` sticky map (`service.py:104-115`) | **MODIFY** | Per-account runtime config from the data-spine; if sticky, a per-`session_uuid` `RunContext` field (§5). |
| `_memfd_cache` (`options.py:31`), `_logger` (`options.py:20`) | **KEEP** | One CLI binary per pod; stateless logger. |
| User DB / auth (`services/user_store.py`, `services/auth.py`, `routers/auth.py`, `routers/admin*.py`) | **STRIP** | → control plane + data-spine; the gateway injects a signed `account_id`. |
| Channel daemon (`services/channels/*`, `routers/channels.py`) | **STRIP** | → Agent Gateway. The pod makes **no** IM calls. |
| APScheduler (`services/scheduler/*`, `routers/scheduler.py`) | **STRIP** | → central scheduler; it wakes the pod to run a turn. |
| Static SPA (`priva/web`, `main.py` StaticFiles mounts `:74-79`) | **STRIP** | → central WebUI/gateway. The pod is JSON/WS API only. |
| `crypto.py:10` hardcoded process-wide Fernet key | **STRIP from image** | Used by the one-time migrator only (data-spine §5); the operator-injected secret bundle replaces it — the pod never decrypts (§7, §13-3). |
| `read_user_env` (`user_env.py`), plaintext `settings.local.json` creds | **STRIP** | → operator-injected in-memory bundle from the tmpfs secret mount (§7). |
| Cross-tenant boot side effects: `list_users()` (`main.py:111`), OpenClaw auto-connect (`main.py:137-142`), perpetual `_temp_cleanup_loop` (`main.py:123-131`) | **STRIP / MODIFY** | The pod knows only its own account; the perpetual timer fights scale-to-zero (§1, §8). |

---

## 1. Process shape & boot factory

### 1.1 What the pod *is* after the strip

One scale-to-zero uvicorn pod per account. It keeps the JSON/WS API surface and deletes every multi-user, static-serving, channel, scheduler, and auth concern. The single load-bearing correctness rule everything else stands on: **the platform-minted lowercase `session_uuid` IS the CLI id (M2); nothing the pod does at boot or per-run ever mints or remaps a session id.** Boot only resolves *its own account context* and re-establishes *coordination state* (Redis pins/routes) so the operator's idle/wake machine is correct.

### 1.2 Router / service census (cite `main.py`)

| Router (current `main.py`) | Disposition | Rationale |
|---|---|---|
| `agent_router` (`:206`, `/api/agent`) | **KEEP** | `/run`, `/run/stream`, `/permission/respond`, `/rewind`, `/fork`, session reads — the pod's reason to exist. |
| `pty_router` (`:218`) | **KEEP** | PTY in-pod; live PTY is an idle gate (§8). |
| `hooks_router` (`:212`), `mcp_router` (`:210`), `subagents_router` (`:209`), `skills_router`/`skill_hub_router` (`:207-208`) | **KEEP**, config re-pointed | Run inside the loop; backing stores → data-spine thin client. |
| `files_router`/`user_files_router` (`:211,217`), `user_data_router` (`:213`), `resource_router` (`:214`) | **KEEP** | Per-account file ops over the pod's own `/workspace`; write paths that wake the pod. |
| `metrics_router` (`:219`) | **KEEP** | Per-pod Prometheus scrape. |
| `/health` (`:189-201`) | **KEEP, minimized** | K8s liveness/readiness; reduce to `{status,version,time}`; add `/readyz` (RED until boot completes). Drop `_detect_local_ip`/`_public_host`/`_base_url`. |
| `auth_router` (`:203`) | **STRIP** | → gateway + data-spine Accounts RPCs. |
| `admin_router`/`admin_files_router` (`:204-205`) | **STRIP** | Cross-tenant admin + whole-host file browser → control panel. |
| `channels_router` (`:216`) | **STRIP** | → Agent Gateway. |
| `scheduler_router` (`:215`) | **STRIP** | → central scheduler. |
| StaticFiles `/` + `/static` + scalar docs (`:74-79,187,221`), mimetypes font reg (`:9-15`) | **STRIP** | → central WebUI/gateway. |

**Lifespan strips.** `get_user_store().list_users()` (`:111`) and the OpenClaw auto-connect loop over ALL users (`:137-142`) are multi-tenant violations — deleted. `_temp_cleanup_loop` perpetual `asyncio.sleep(3600)` (`:123-131`) is an artificial non-idle reason that fights scale-to-zero — replaced by an on-wake bounded sweep / central `ExpireTempFiles` RPC. `cleanup_expired_files()` itself iterates **all** users (`temp_files.py` `base.iterdir()`) and must be rescoped to the single `account_id` before any in-pod sweep can run, since the pod mounts only `/export/<account_id>`. `seed_bundled_skills()` (`:106`) is KEPT but retargeted to the per-account `config_home` (today it writes `priva_home()/resource/skills`, default `~/.config/priva` — unwritable under RO-rootfs). `CORSMiddleware allow_origins=["*"]` narrows to the gateway/mesh origin.

### 1.3 The dual-set `CLAUDE_CONFIG_DIR` rule (load-bearing)

`grep` confirms `CLAUDE_CONFIG_DIR` is injected **nowhere** in priva today; the subprocess falls back to `Path.home()/.claude` (`sessions.py:124-127`) — under `readOnlyRootFilesystem` that write **fails**. The fix sets it in **two** places because the SDK reads it from two layers:

- **Process env at boot:** `os.environ["CLAUDE_CONFIG_DIR"] = config_home` and `os.environ["HOME"] = account_mount_home`. Safe because one pod == one account (process-global == account-global). This is the **only** thing that makes the SDK's in-process helpers resolve correctly: `_get_project_dir`/`_find_project_dir` (used by the **in-process** fork write `session_mutations.py:298,547` and by the kept `heal`/`strip` path helpers) read `os.environ` **only** (`sessions.py:144`), *not* `options.env`. `_get_projects_dir(env_override)` honors the override (`sessions.py:130-141`) but the fork/heal call sites do not pass it.
- **`options.env` per run:** `env_dict["CLAUDE_CONFIG_DIR"] = config_home` — authoritative for the spawned CLI (`subprocess_cli.py:430-436` overlays `options.env` onto inherited env, verified).

`PRIVA_HOME` must also be pinned at boot to a writable PV path (it backs `priva_home()` → audit default + skill seeding; `paths.py:14` falls back to `Path.home()/.config`, unwritable under RO-rootfs). `server.work_dir` (default `~/priva_workspace`, `config.py:15`, written by `get_user_workspace`'s `makedirs`, `auth.py:141`) and the credential surface must likewise resolve onto the PVC (§7, §9). The data-spine §1.6 CI ban on importing `claude_agent_sdk._internal.sessions` in the multi-tenant parent is carved narrowly: the **in-pod fork/rewind/heal** call sites (single-account, `os.environ` correct) are exempt and gated by a boot-time `assert os.environ["CLAUDE_CONFIG_DIR"] == config_home`; the ban still holds for the data-plane/gateway. This forecloses ever co-tenanting two accounts in one pod (the only safe alternative would be a future SDK whose mutation helpers thread an explicit `env_override` instead of reading `os.environ`).

### 1.4 De-singletonized `create_app()`

The current factory builds the app and runs the full multi-tenant lifespan at **import time** (`app = create_app()` at `main.py:225`). The fork keeps a `create_app()` factory but the lifespan loses every multi-user block and gains the §11 boot. Account-scoped singletons survive but are **bound to the injected account at boot**, not a process-global HOME:

| Singleton | Verdict | Boot action |
|---|---|---|
| `get_settings()` `@lru_cache` (`config.py`) | KEEP, re-point | `work_dir` → `/workspace`; log path off `Settings.yaml_file.parent.parent`. |
| `priva_home()` / `PRIVA_HOME` (`paths.py:7-16`) | KEEP, pin | Pin to the per-account mount; **drop the `Path.home()` fallback** (`paths.py:14`). |
| `get_audit_logger()` → `priva_home()` (`audit_log.py:78`) | KEEP, re-point + **eager** | Construct `base_dir=/audit/<account_id>` **in the lifespan before any router can run** — it is lazy-on-first-call (`audit_log.py:312-316`) and a fork/rewind handler (`agent.py:531`) would otherwise pin it to the global default first. |
| `registry = PermissionCoordinatorRegistry()` (`permission_coordinator.py:133`) | REPLACE | → `RunContext` on `app.state`, keyed on immutable lowercase `session_uuid`; `remap_session` deleted (§5). |
| `_memfd_cache` (`options.py:31`), `_logger` (`options.py:20`) | KEEP | One CLI binary per pod; stateless. |
| `get_user_store()` (`user_store.py:185`), `rate_limiter` (`auth.py:170`), `_bridges` (`openclaw_bridge.py`), `get_job_store()`/`get_channel_config_store()` | **DELETE** | Stripped subsystems. |

---

## 2. No-remap session lifecycle (M2)

### 2.1 The defect this removes

Today every run sets `options.resume = session_id` for **both** create and resume (`options.py:260-261`). On a first run that `--resume`s an id with no JSONL on disk; the CLI responds by **minting a fresh id** and reporting it on `system.init`. The run loop then *captures* that fresh id and *remaps* every in-pod structure onto it:

```
service.py
  agent_run        : current_resume_id := session_id                       :469
                     current_resume_id := system.init.session_id           :487-489
                     current_resume_id := result.session_id                :508-510
                     options.resume     := current_resume_id (retry)        :522-523
  agent_run_events : current_resume_id / coordinator.session_id / stream_id
                       := system.init.session_id                           :820-829
                     ...same on the result event                           :847-855
                     options.resume := current_resume_id (retry)           :919-920
  agent_run_stream : registry.remap_session(stream_id, new_sid, …)         :1021-1024
                     stream_id = new_sid                                    :1025
permission_coordinator.py
  PermissionCoordinatorRegistry.remap_session(...)                         :126-130
```

That capture-and-remap is the **running-session id mutation** M2 exists to kill. Under M2 the platform-minted `session_uuid` *is* the CLI id and is immutable for the session's whole life — there is nothing to capture and nothing to remap. **All six capture sites** above plus `remap_session` are deleted as **one atomic commit** with the split (§2.6 sequencing).

### 2.2 `is_first_run` — server-supplied per turn (M5)

`is_first_run` decides CREATE vs RESUME and is **server-authoritative**, supplied per turn by the gateway/scheduler — but under **M5 there is no `session_index.status`** to read it from. Its source is: **IM** = an atomic `first_run_done` 0→1 CAS on `channel_binding` (data-spine §2.4/§3.4); **WebUI** = empty `session_id` ⇒ CREATE (the gateway just minted the uuid), populated `session_id` ⇒ RESUME guarded by an NFS-safe `stat` of the derived path (absent ⇒ reconcile-as-create, §2.5). It is **never** inferred from a disk probe alone, a CLI `system.init`/`result` event, or merely whether the client supplied a `session_id`. The current `stream_id = session_id or str(uuid.uuid4())` (`service.py:684,995`) does the opposite — it infers "create" from the absence of a client id and **mints its own uuid4**, both forbidden under M2/M5 (the pod never mints). Both are deleted; `session_uuid` + `is_first_run` are required inputs.

The IM `first_run_done` CAS returns `True` to exactly one caller (single-writer data-plane), eliminating the TOCTOU where two surfaces both CREATE over one JSONL; the pod never re-reads any status on the hot path. `is_first_run` is added to `AgentRunRequest`/`WsInitFrame` (today they carry only `session_id`, `models/agent.py:24,209`).

**Surface contract (locked §13-7).** New-vs-resume is decided at the surface, but the **gateway mints the canonical `session_uuid` — the client never invents one** (a client-chosen id could violate the uniqueness / no-remap invariant). **WebUI:** an **empty** `session_id` for a new session (gateway mints locally + returns the `session_uuid`, which the UI reuses on every later turn) or a populated `session_id` to RESUME a history session. **IM channels:** always bound to a **fixed** `session_uuid` in `channel_binding`; a user **reset** (`RebindChannel`, resetting `first_run_done=0`) is the only way to mint a new one. The empty/new case is `is_first_run=True`; the populated case is a pure RESUME.

### 2.3 The two invocation shapes (the whole ballgame)

The SDK appends `--resume` and `--session-id` **independently, unguarded** (`subprocess_cli.py:291-295`); only the CLI *binary* rejects the illegal pair (mutually exclusive unless `--fork-session`, `types.py:1646-1650`). The pod forms exactly one shape, lowercased first (`sid = session_uuid.lower()`):

```
                       is_first_run (server-supplied per turn: binding CAS / WebUI disk-guard — §2.2)
                                    │
            ┌──────────── 'new' ────┴──────── else ──────────────┐
            ▼ CREATE                                             ▼ RESUME
 options.session_id = sid              options.resume       = sid
 options.resume     = None             options.session_id   = None
 options.fork_session = False          options.fork_session = False
   → claude … --session-id <sid>         → claude … --resume <sid>
   CLI uses uuid as id + filename         CLI reopens the same JSONL, same id

            ┌────────── FORK (sanctioned id-change, §3) ──────────┐
            ▼
 options.resume       = parent_sid     (fork rides --resume)
 options.session_id   = None
 options.fork_session = True           (--fork-session)
   → claude … --resume <parent> --fork-session   CLI mints a NEW child uuid; parent untouched
```

A defensive `assert not (options.session_id and options.resume)` lives in `build_agent_options`; the fork path satisfies it because `session_id` is `None` there. **The retry path re-runs this invariant:** on retry the loop sets **both** `options.resume = sid` **and** `options.session_id = None` together (or routes back through `build_agent_options(is_first_run=False)`), then re-asserts mutual exclusion — never mutating only `options.resume` on an object whose `session_id` is still set from a CREATE (which would emit the forbidden `--session-id --resume` pair, `subprocess_cli.py:291-295`, and get the run spuriously quarantined by §2.5). After the first CLI write the JSONL exists, so the RESUME shape on retry is correct; a crash before any write is covered by reconcile-as-create.

### 2.4 State machine — one session's id is immutable for life

```
        mint (gateway-local WebUI / BindChannel IM) — lowercase session_uuid, NO status row (M5)
                    │
                    ▼  CREATE: --session-id <uuid>
              ┌───────────┐                                   ┌──────────────────┐
              │  CREATE   │──────────────────────────────────▶│  ACTIVE / IDLE   │
              └───────────┘                                   │  liveness = Redis │
   RESUME: --resume <uuid> ◀──────────────────────────────────│  route/lock mirror│
   (same uuid every turn, forever)                            └───┬───────────┬──┘
                       consistency assertion OK ─────────────────┘           │ FORK
                       (reported id == lowercase uuid)                        ▼
   unsanctioned drift (reported id ≠ uuid, NOT a fork)        ┌──────────────────┐
        │                                                     │  CHILD session   │
        ▼                                                     │  NEW uuid,       │
   ┌──────────┐  stop run; clean teardown (no status row      │  forkedFrom      │
   │QUARANTINE│  to set — M5); SREM/DEL keyed on the orig uuid;│  header = parent │
   └──────────┘  user-visible "session interrupted, re-ask";  └──────────────────┘
                 NEVER re-key to a CLI-minted id (that IS the remap)
```

### 2.5 The consistency assertion + quarantine (data-spine §3.7)

The remap is replaced by a self-checking invariant. On the first `system.init` (the `system` `SystemMessage`, read via `data["session_id"]` at SDK `message_parser.py:71`; in priva surfaced at `service.py:486-489` / `:820-824`) and on each `ResultMessage` (`message_parser.py:255`; priva `service.py:508-510` / `:847-855`), the pod **asserts the reported id equals the lowercase `session_uuid` byte-for-byte**. A mismatch can only mean an unsanctioned id mutation or SDK-semantics drift → **quarantine** (stop the run, SREM/DEL any approval-pin/index/lock keyed on the original `session_uuid` — a clean teardown; under **M5 there is no status row to set**, **never** a re-key to the CLI-minted id, which would re-introduce the remap M2 deletes). Pin the SDK/CLI version (`0.2.93`) and run the assertion in CI against the pin so a semantics bump fails the build.

**Exemptions (must be coded).** A run launched with `options.fork_session=True` legitimately reports a *new* child id != the parent — record it as a child via `RecordForkLineage` (§3), do not quarantine; the out-of-band `POST /fork` never starts a parent run, so it never reaches the assertion. The fork exemption is gated on **more than** `is_fork`: the resume-of-missing-JSONL case (NFS attribute-cache lag hides a freshly-minted file → a bare `--resume` *may* mint a fresh id) must run a **reconcile-as-create** pre-check (trust central `is_first_run` + an NFS-safe `stat` preflight) **before** the fork exemption, so a non-fork remint is never mislabeled a fork. Bare-`--resume`-of-missing behavior is unverified by static read and is deferred to Phase 0 (data-spine §7-Q3) with the assertion + version-pin as backstop.

| Vector that could change the CLI id | Verified behavior | Control in the pod |
|---|---|---|
| Bare spawn (no flag) | mints fresh id | Never spawn bare; always one shape |
| `--resume` on first run (today's bug) | `--resume` of missing id → fresh id | **Split CREATE/RESUME** (§2.3) |
| Both `--session-id`+`--resume` | appended unguarded (`subprocess_cli.py:291-295`) | One shape; defensive `assert`; retry sets both fields together (§2.3) |
| `--fork-session` | mints NEW child uuid | **Exempt**; record child, parent unchanged (§3) |
| Resume-of-missing JSONL (NFS lag) | *may* mint a fresh id | reconcile-as-create pre-check **before** the fork exemption |
| Autocompact / summarization | asserted id-stable | runtime assertion + Phase-0 empirical test |
| Mixed-case uuid round-trip | forks JSONL on case-sensitive FS | lowercase before assign + derive (§2.8) |

### 2.6 Atomic landing order

The split (`options.py:260-261`), the six-site capture deletion + `remap_session` deletion, and the retry-resume rewrite (`service.py:522-523,919-920`) are **load-bearing for each other**. Deleting the capture without the split leaves first-run retries `--resume`ing a non-existent file (silent loss of the prior attempt's tool history); landing the split without deleting the capture leaves the remap dead-but-firing. Land all three in **one commit**. Coordinator registration moves under the immutable `session_uuid` at spawn (`service.py:1003`) in the same commit, and the `service.py:1024` `registry.remap_session` call site is deleted **with** the method — deleting the method alone would be a dangling-reference build break.

### 2.7 Path derivation — in-pod via the SDK (M5)

Under **M5 the platform persists no path metadata** (no `session_index`, no `cwd_hash`/`config_home` column). Because **one pod serves exactly one account**, the pod resolves its own JSONL path **in-process via the SDK** — `os.environ["CLAUDE_CONFIG_DIR"]` is correct for this single account (the §1.3 dual-set), so the SDK's `os.environ`-only helpers (`_get_project_dir`/`_find_project_dir`, `sessions.py:144`) resolve to the right directory. This is exactly the single-account exception to the data-spine §1.6 ban (which still forbids the *data-plane/gateway* from importing `_internal.sessions`):

```
                    <config_home>/projects/<cwd_hash>/<session_uuid>.jsonl
  config_home : operator-injected CLAUDE_CONFIG_DIR (e.g. /pv/<account_id>/claude) — fixed per account
  cwd_hash    : computed by the in-pod SDK at write/read time (single-account → correct) — NOT persisted
  session_uuid: verbatim filename, lowercased
```

`cwd_hash` is **not** a simple slug (the SDK's `_sanitize_path` truncates > 200 chars + appends a base36 `_simple_hash`; `_get_claude_config_home_dir` NFC-normalizes). The pod does **not** re-implement it — it lets the **in-pod SDK** compute it (correct because single-account, so there is nothing to persist and nothing to diverge from). A CI/boot round-trip assertion validates path resolution for long/Unicode paths against the pinned `0.2.93`. The cross-tenant **`state-reader`** (which cannot use the SDK) instead **globs** `projects/*/<session_uuid>.jsonl` — uuid-unique-per-account makes this unambiguous without `cwd_hash`.

- `options.py:160,205` — inject `env_dict["CLAUDE_CONFIG_DIR"] = config_home` (+ set `os.environ["CLAUDE_CONFIG_DIR"]` at boot, §1.3).
- `session_heal.py` / `retry.py` — **keep** `_get_project_dir(_canonicalize_path(cwd))` (correct in-pod, single-account); both are **writers** (heal appends `session_heal.py:65`, strip rewrites `retry.py:58`) and run under the in-pod single-writer lock (§5) against the writable `/workspace`/PV.
- `routers/agent.py:16,104-107` — `_session_jsonl_path` derives **in-pod via the SDK** (single-account), not from a central row.

### 2.8 Lowercase invariant

`_UUID_RE` is compiled `IGNORECASE` (`sessions.py:47`) and `_validate_uuid` returns its input **verbatim** (`sessions.py:69-72`). On a case-sensitive FS, mixed-case and lowercase `.jsonl` are different files. Enforcement is layered (M5 — **no DB `CHECK`, no `MintSession`**): the **mint lowercases `uuid4()`** (gateway-local for WebUI / `BindChannel` for IM); the pod lowercases before `options.session_id`/`options.resume` and any derived path; the §2.5 assertion compares lowercase byte-for-byte. The fork child id is `str(uuid4())` (already lowercase, `session_mutations.py:400`) but is normalized anyway before it becomes a PK; the real lowercase risk is a caller-supplied resume/`session_id`, not the SDK-minted fork id.

---

## 3. Session fork & lineage

### 3.1 What a fork is (and is not)

A fork produces a brand-new **CHILD** session with its own immutable `session_uuid`; the **parent's** id is never touched (data-spine C4, §3.6). Fork is **additive, not a remap**. The SDK transform proves it: `fork_session()` mints `forked_session_id = str(uuid4())` (`session_mutations.py:400`), remaps every message `uuid`/`parentUuid`, rewrites `sessionId` per entry, stamps `forkedFrom={sessionId:parent,messageUuid}` (`:434-475`); it reads the parent JSONL **read-only** (`:306`) and writes a new file with `O_CREAT|O_EXCL` 0o600 (`:338-339`). Two modes: **mid-session** (`up_to_message_id` slices the transcript, `:373-383`) and **tail** (full copy). A forked child **starts with no file-checkpoint/undo history** ("Forked sessions start without undo history (file-history snapshots are not copied)", `session_mutations.py:253`) and **drops subagent sidechains** (`isSidechain` entries filtered out, `:368`) — so a forked child cannot rewind into, or re-reference, the parent's subagent sidechains. This is the clean boundary §4 relies on: **fork = transcript branch; checkpoint/rewind = file state.**

```
PARENT session_uuid = P  (immutable; never mutated by fork)
   transcript: m0 ─ m1 ─ m2 ─ m3 ─ m4        file snapshots: [c1][c2][c3]  (P-local, NOT copied)
                     │
   fork(up_to=m2) ───┘  (mid-session)         tail fork copies m0…m4
                     ▼
CHILD session_uuid = C  (NEW immutable; lowercased; fork_parent_uuid=P)
   transcript: m0'─ m1'─ m2'   every uuid remapped; each entry sessionId=C, forkedFrom={P,mN}
   snapshots: ∅   sidechains: ∅
```

### 3.2 Two id-minting paths — both must register lineage

| Path | Where the child id is minted | How the pod sees it |
|---|---|---|
| **Out-of-band fork** (`POST /fork`) | SDK return `ForkSessionResult.session_id` (`session_mutations.py:345`), synchronously in the handler (`agent.py:554`) | read `result.session_id`; register immediately |
| **Resume-time fork** (`--fork-session`) | the **CLI** mints it inside the subprocess; surfaces on the run's first `system.init` | read the reported id; the **one** sanctioned mid-run id-change |

Resume-time fork is wired: `WsInitFrame.fork_session`/`AgentRunRequest.fork_session` (`models/agent.py:223,38`) → `options.fork_session` (`options.py:265-266`) → `--fork-session` (`subprocess_cli.py:343`). **The fork guard must rebind to the RESUME shape:** under the M2 split, RESUME sets `options.resume = parent_uuid` and `options.session_id = None`, so the guard becomes `if fork_session and options.resume:` — **not** the current `if fork_session and session_id:` (`options.py:265`), which under M2 would be `False` on the resume path and silently disable resume-time fork.

### 3.3 The resume-time fork capture seam (the one sanctioned mid-run id-change)

Resume-time fork is the single place a run's reported id legitimately changes mid-stream. Because the child id surfaces only on `system.init`/`ResultMessage` — the exact channel M2 deletes (§2.1) — the pod re-introduces a **constrained, single capture seam gated strictly on `options.fork_session=True`**:

1. read + lowercase the child id off `system.init`;
2. register a **new** in-pod coordinator + lock entry under the child `session_uuid` in the `RunContext` (§5), and **migrate** the in-flight run's coordinator/lock/Redis route/lock-mirror/approval-index from parent→child atomically — OR **refuse** the fork while a permission Future is pending for that session (mirror the `/rewind` 409, §6). The pod takes the **refuse** path for v1 (F10) to avoid orphaning a parked Future;
3. write the child JSONL under the child's lock, carrying its `forkedFrom` header as the lineage record (M5 — no `RecordForkLineage`/session table).

This is distinct from the deleted unconditional capture: it fires only on an explicit fork flag, and it creates a CHILD (not a re-key of the running session). The §2.5 assertion is told `is_fork=options.fork_session` and exempts it.

### 3.4 Lineage registration

Today `POST /fork` only audits and returns `ForkResponse{new_session_id, parent_session_id}` (`routers/agent.py:546-572`, response shape at `models/agent.py:278-281`) — it never registers lineage. Under **M5 there is no `session_index` row and no `RecordForkLineage` session-table RPC**: a fork is a first-class create whose lineage rides the **child JSONL's `forkedFrom` header** (the SDK already stamps `forkedFrom={sessionId:parent,messageUuid}`, `session_mutations.py:434-475`, §3.1). The flow: call the SDK transform → lowercase the child id → the child JSONL is O_EXCL-written under the child's lock (§3.5) carrying the `forkedFrom` header → optional channel bind to the child (a `RebindChannel`, never a rename). **By default fork leaves the child unbound** so the parent keeps its sole `readwrite` binding — `ux_binding_identity` is unique per identity, so auto-binding the child would orphan the parent's IM reachability. `fork_count` / lineage queries are served **wake-free by the `state-reader`** reading `forkedFrom` headers across the account's JSONLs (an optional `channel_binding` lineage column may cache it if it ever gets hot). There is still **no `RemapSession`**.

### 3.5 Sole-writer child write + crash recovery

The child JSONL is a new-file `O_EXCL` create on the PVC (`session_mutations.py:339`). Today the FastAPI handler does this write **synchronously, outside any lock**, resolving the projects dir via the `os.environ`-only helper (`session_mutations.py:298,547` → `sessions.py:144`) — wrong only in a *multi-tenant* parent; in the **single-account pod** it resolves correctly (§2.7). The fix: the **owning pod** performs the fork under the **in-pod `asyncio.Lock` on the child `session_uuid`** (a fresh uuid, no contention with the parent's lock), deriving the child path **in-pod via the SDK** (§2.7) with the boot-set `os.environ["CLAUDE_CONFIG_DIR"]` (§1.3). **M5 crash recovery (no `session_index` row to two-phase):** the child JSONL *is* the record, so the write is staged on the filesystem — create a temp/`.materializing` name → write transcript + `forkedFrom` header → atomic `rename` to `<child>.jsonl`. A crash leaves only the temp file, which a **filesystem reconciler** (the `state-reader` / an on-wake sweep) reclaims by age; a fully-renamed `<child>.jsonl` is complete by construction. The offboarding purge walks the **filesystem** (M5 — not `session_index` rows), so an orphan temp file is cleaned like any stale file.

```
fork(P) → child C   (M5: lineage lives in the JSONL, no session_index row)
  /export/<acctA>/…/projects/<cwd_hash>/
    P.jsonl                         parent, untouched
    C.jsonl.materializing  ──▶  write transcript + forkedFrom={P, mN}  ──▶  rename  ──▶  C.jsonl
                                (crash leaves only .materializing → filesystem reconciler reclaims by age)
```

### 3.6 Does a fork wake the pod?

| Fork kind | Wakes the pod? | Why |
|---|---|---|
| Resume-time `--fork-session` | already awake | the pod is mid-run; the CLI writes the child under the live process |
| Out-of-band `/fork` | **yes** (if asleep) | the owning pod must perform the `O_EXCL` write under the sole-writer lock; the gateway wakes it via CR-patch before routing the fork |

Reading lineage (`fork_count`, the child transcript) is always wake-free via the `state-reader`; only *writing* the child file is gated on the pod. The fork write counts as an in-flight `SessionRun` (§5) and pins the pod awake for its duration; the operator cannot scale-to-zero mid-fork.

### 3.7 Locked fork decisions

| # | Decision |
|---|---|
| F1 | Fork creates a CHILD with a NEW immutable `session_uuid`; the parent id is never mutated (data-spine C4; `session_mutations.py:400`). |
| F2 | Both modes supported: mid-session (`up_to_message_id`, `session_mutations.py:373-383`) and tail. |
| F3 | Every fork records lineage via the child JSONL's `forkedFrom` header (M5 — no `session_index`/`RecordForkLineage`); there is **no `RemapSession`**. |
| F4 | Lowercase the child id before lineage/bind/response; lowercase the parent before `sdk_fork_session` (`agent.py:555,566,569`). |
| F5 | BOTH minting paths register lineage — out-of-band (`ForkSessionResult.session_id`) and resume-time (child id off `system.init`, §3.3). |
| F6 | The §2.5 quarantine EXEMPTS a sanctioned fork (`is_fork=options.fork_session`), gated *after* the resume-of-missing reconcile-as-create check. |
| F7 | The child JSONL write is a PVC write under the in-pod `asyncio.Lock` on the **child** `session_uuid`, never the bare request thread; two-phase with `materializing` status + reconciler sweep (§3.5). |
| F8 | The child inherits the parent's `cwd`/`cwd_hash`/`config_home` (same project), copied into the child row. Cross-cwd fork is rejected at the API (the SDK writes the child into the same project dir, `session_mutations.py:338`). |
| F9 | A fork starts with NO checkpoints (`session_mutations.py:253`) and NO subagent sidechains (`:368`); transcript-fork and file-rewind are orthogonal. |
| F10 | Resume-time fork on a session with a pending approval is **refused** (409), not silently migrated, in v1 (§3.3). |

---

## 4. File checkpointing & rewind

### 4.1 What the mechanism is (verified, `0.2.93`)

The pod does **not** own the checkpoint store; the **CLI binary** does. The SDK contributes only (a) `process_env["CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"]="true"` on spawn when `options.enable_file_checkpointing` is set (`subprocess_cli.py:464-466` — it passes **no path**), and (b) `rewind_files(user_message_id)` over the control socket (`client.py:370-400`). The checkpoint id **is** the user-message uuid; `replay-user-messages` must be set so `UserMessage.uuid` is populated (`options.py:262-264` already pairs both flags — KEEP). Checkpoints are **LOCAL-DISK ONLY**: `validate_session_store_options` hard-raises `ValueError("session_store cannot be combined with enable_file_checkpointing …")` (`session_store_validation.py:40-43`). `build_agent_options` never sets `options.session_store`; the pod adds an explicit `assert options.session_store is None` when checkpointing is requested, and boot asserts no remote store is ever configured.

### 4.2 Where checkpoints physically live (contingent on the Phase-0 probe)

Snapshots MUST land on the per-user **RWX PVC** (writable under `readOnlyRootFilesystem`) and persist across scale-to-zero. Because the CLI resolves its store relative to `CLAUDE_CONFIG_DIR`/cwd (the SDK exposes no path API), the pod pins both onto the PVC:

```
/pv/<account_id>/claude/            ← CLAUDE_CONFIG_DIR (writable PV; NFC-normalized)
  └── projects/<cwd_hash>/
        ├── <session_uuid>.jsonl
        ├── <session_uuid>/subagents/agent-*.jsonl
        └── <CLI file-history snapshot store>   ← local-disk-only; survives scale-to-zero (PVC re-attaches)
/workspace/                         ← cwd (the files being snapshotted)
RO rootfs / $TMPDIR(tmpfs)          ← NEVER host snapshots (RO fails the write; tmpfs is lost on scale-to-zero)
```

> **Phase-0 gate (FC3 is locked CONTINGENT on this).** The exact snapshot subpath is CLI-internal and version-specific; `subprocess_cli.py:464-466` passes only the enable flag, no path. Before lock, a Phase-0 probe (against the pinned `0.2.93`) must edit→rewind→assert-restore and locate the snapshot dir, asserting it resolves onto a **persistent, writable PV path** and never `$HOME`/`$TMPDIR`/ephemeral. If the binary resolves snapshots against `$HOME` or `$TMPDIR`, boot must additionally pin those onto the PVC. The version is pinned + a CI rewind round-trip test runs against it.

### 4.3 Rewind — the gate is the lock, and it is a real (billable) turn

`POST /rewind` (`routers/agent.py:503-543`) refuses while a run is live (409), runs with `permission_mode="bypassPermissions"` + `enable_file_checkpointing=True`, then `client.query("")` → first replayed frame → `client.rewind_files(checkpoint_uuid)`. The MODIFY deltas:

- **Gate on the in-pod lock, not the registry.** The current `if registry.get(req.session_id)` (`agent.py:517`) is too weak — an `agent_run_events` run that never registered a coordinator, a live PTY editing the same workspace, or a scheduler run would be missed. The 409-while-live guard consults the **authoritative in-pod single-writer `asyncio.Lock` for `session_uuid`** (§5, decision 18). Rewind is itself a **writer** (it restores files on the PVC), so it **acquires** that lock for the whole operation and counts as an in-flight `SessionRun`; the ephemeral `ClaudeSDKClient` spawn inside must **not** re-acquire it (no self-deadlock — the handler holds it, the spawn is awaited inline). Rewind also 409s on a **live PTY** on the same workspace and on a **pending approval** for the session, so it cannot race a half-executed approved tool or a PTY edit.
- **`cwd` is `/workspace`, not `get_user_workspace(user)`** (`agent.py:514`); `req.session_id` is lowercased; the audit `session.rewound` (`agent.py:531-537`) writes through the per-account audit writer (`/audit/<account_id>/`), actor = gateway-asserted `account_id`.
- **The rewind turn is a real turn.** `client.query("")` writes a `{type:user, content:""}` message into the resumed transcript and triggers a CLI turn. Therefore rewind (i) acquires a concurrency slot (§5) and self-reports its token usage like any turn (spend deferred — M6, §7); (ii) its `system.init`/`ResultMessage` run through the §2.5 assertion (it is a normal resumed turn, **not** a fork — it must pass; a Phase-0 probe confirms the resumed empty-turn does not autocompact-mint a new id); (iii) the design does **not** claim "transcript untouched" for the empty-turn path — the empty user message is appended. (If a Phase-0 probe shows `replay-user-messages` can surface a `UserMessage` frame without sending a new turn, drop the `query("")`.)
- **No M2 break.** Rewind always RESUMEs an existing immutable `session_uuid`; after the §2 split it forces the RESUME branch (`options.resume = sid`, `options.session_id = None`, `options.fork_session = False`).

```
POST /rewind (session_uuid, checkpoint_uuid)
  sid = session_uuid.lower()
  try-acquire Lock[sid] (non-blocking)  ── held by a live run / live PTY / pending approval?
        │ yes → 409 "Finish the current run before rewinding"
        │ no  → hold Lock[sid] for the WHOLE rewind (sole writer; counts as in-flight)
        ▼
  acquire slot   →  ephemeral ClaudeSDKClient(cwd=/workspace,
        CLAUDE_CONFIG_DIR via options.env, bypassPermissions, enable_file_checkpointing=True,
        session_store=None)  →  query("") → first frame → rewind_files(checkpoint_uuid)
  audit session.rewound → /audit/<account_id>/ → release lock → 200
```

### 4.4 Two-axis model + crash safety

File-rewind changes **workspace file state only** (same `session_uuid`, transcript appended-with-empty-turn); fork branches the **transcript** (new child id, no copied snapshots). A freshly forked child has zero checkpoints, so `rewind_files` on it is a no-op until it accrues its own. File restore is **not** atomic at the FS level (CLI-internal, no journal), so a re-issued rewind to the same `checkpoint_uuid` must be **idempotent** (restoring to the same snapshot twice is safe — a Phase-0 invariant). On SIGTERM mid-restore, PreStop must drain the held rewind lock within the grace window (§8.4) and force-kill the ephemeral subprocess on teardown failure so it cannot orphan the PVC.

### 4.5 Retention

The CLI snapshot store is unbounded within a session and unmanaged today (no SDK API to enumerate/evict). **Locked (§13-4): a per-tenant `checkpoint_budget_mb` quota is adopted** (default **≈2 GB**, CRD-configurable), on top of the 30-d PVC purge / offboarding floor. On over-budget the pod **surfaces an explicit error + audit and prunes oldest-first** — it **never silently disables checkpointing** (which would make later rewinds silently restore stale files). Snapshot eviction is a **write** that requires waking the owning pod (cannot be a wake-free reader op), so cleanup is tied to a wake-bearing event (run, offboarding, PVC purge), never a background reader sweep.

### 4.6 Locked checkpoint decisions

| # | Decision |
|---|---|
| FC1 | KEEP the full checkpoint+rewind path; `enable_file_checkpointing` ⇒ flag + `replay-user-messages` (`options.py:262-264`); `checkpoint_uuid == user-message uuid`. |
| FC2 | LOCAL-DISK ONLY; never attach a remote `session_store`; `assert options.session_store is None` + SDK hard-fail (`session_store_validation.py:40-43`). |
| FC3 *(contingent on §4.2 probe)* | Snapshots on the per-user RWX PVC under `CLAUDE_CONFIG_DIR`; writable under RO-rootfs; persist across scale-to-zero. |
| FC4 | Rewind gate = the in-pod `asyncio.Lock` on `session_uuid` (not the registry); 409 on live run/PTY/pending approval; the inner spawn does not re-acquire. |
| FC5 | `cwd=/workspace`; lowercase `session_id`; audit via the per-account writer; RESUME-shape only (no M2 break). |
| FC6 | The rewind turn acquires a concurrency slot (spend deferred — M6) and passes the §2.5 assertion (it is a resumed turn, not a fork). |
| FC7 | Re-issued rewind to the same `checkpoint_uuid` is idempotent; SIGTERM drains the held lock; the ephemeral subprocess is force-killed on teardown failure. |
| FC8 *(locked §13-4)* | Per-tenant `checkpoint_budget_mb` quota (default ≈2 GB, CRD-configurable): over-budget ⇒ explicit error + audit + oldest-first prune on a wake-bearing event; **never** silent disable. |

---

## 5. Concurrency & the per-pod `RunContext`

### 5.1 The two missing primitives

The run loop is already N-session-safe (`ClaudeSDKClient` per run inside `async with`, `service.py:479,761`; per-run locals), but two things the multi-tenant pod requires are absent: (1) **no per-session single-writer lock** — zero per-session `asyncio.Lock`/`fcntl` exist in `services/claude_sdk/` or `routers/agent.py`; the only `asyncio.Lock` in kept code is the PTY registry lock (`pty_session.py:400`); the old serial daemon never needed run-vs-run serialization; (2) **no per-pod concurrency cap / admission gate.** The contract names both: an in-pod authoritative `asyncio.Lock` + a per-pod run-context.

### 5.2 `RunContext` — one per-pod object replacing every per-session global

One `RunContext` instance owned by `app.state`, constructed in lifespan and bound to the injected account at boot (not an import-time global). Modeled on the existing PTY registry (`pty_session.py:399-426`).

```
RunContext (one per pod, on app.state.run_ctx)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ account_id: str                                                            │
 │ cap: int = min(mem_derived, quota.max_concurrent_sessions, 3)              │
 │ _draining: bool = False                  (set by SIGTERM, §8.4)            │
 │ _admission: asyncio.Lock                 (serializes admit/release)        │
 │ locks:        dict[session_uuid -> asyncio.Lock]   AUTHORITATIVE writer    │
 │ active:       dict[op_id -> SessionRun]            in-flight op set        │
 │ coordinators: dict[session_uuid -> PermissionCoordinator]  (ex-registry)  │
 │ vision:       dict[session_uuid -> str]            (ex-_vision_sessions)   │
 └────────────────────────────────────────────────────────────────────────────┘
      all keys are lower(session_uuid)
 SessionRun { op_id; session_uuid; kind: "run"|"fork"|"rewind";
              started_at; last_activity; cancelled: asyncio.Event;
              coordinator: PermissionCoordinator | None }
```

| Global today | Anchor | Becomes |
|---|---|---|
| `registry = PermissionCoordinatorRegistry()` | `permission_coordinator.py:133`; `__init__.py:2,4` | `RunContext.coordinators`, keyed on immutable lowercase `session_uuid`; **`remap_session` deleted**; export removed |
| `_vision_sessions` + `_VISION_SESSIONS_MAX` | `service.py:104-115` | `RunContext.vision` (or per-account config); lowercase-keyed |
| per-session single-writer lock | does not exist | `RunContext.locks[session_uuid]` |
| run-context set | does not exist | `RunContext.active` (idle predicate input, §8) |

### 5.3 Three orthogonal guarantees

- **Per-session serialization (single-writer):** `locks[session_uuid]` is the **authoritative** acquisition gate (decision 18). Redis `lock:session:{session_uuid}` (data-spine §4 #11) is a **STATUS MIRROR ONLY** — written on acquire, heartbeat-refreshed, cleared on release, never consulted as a gate; its loss is harmless.
- **Cross-session concurrency (cap):** distinct sessions run in parallel up to `cap = min(mem-derived, quota.max_concurrent_sessions, 3)`, enforced **only** in the pod, returning **429** (with `X-Cap`/`X-Inflight`) over the cap. The cap counts **distinct `session_uuid`s** in `active`, not operations.
- **Per-request rendezvous (permissions):** each session's `PermissionCoordinator` holds its own `pending` Future map (§6); independent across sessions, in-pod.

Because asyncio is single-threaded, the lock prevents two coroutines from interleaving JSONL-mutating sections for the same session. No `fcntl` inside the pod — sole-writer at the **tenant** boundary is the mount + `Recreate` (§9); inside the pod it is `RunContext.locks`.

### 5.4 Every JSONL writer goes through the lock

| Writer | What | Anchor |
|---|---|---|
| CLI subprocess | normal run append | `service.py:479,761` |
| FastAPI process | `heal_orphan_tool_uses` — append on resume/retry | `session_heal.py:65`; `service.py:525,922` |
| FastAPI process | `strip_synthetic_records` — rewrite on retry | `retry.py:58`; `service.py:534,931` |
| FastAPI process | `sdk_fork_session` — O_EXCL **child** write | `session_mutations.py:338-339`; `agent.py:554` |

`heal`/`strip` run **after** the `async with ClaudeSDKClient` block closes, but the lock is held across that boundary so a second same-session request cannot interleave its own spawn between CLI exit and heal/strip. The current `await asyncio.sleep(1)` flush-grace (`service.py:513,876`) is folded into the bounded drain budget and the lock is held across it. Both `heal`/`strip` re-open by the **derived** path (§2.7), not a captured id.

### 5.5 Admission, queueing, cap — admit at exactly one layer

`agent_run_stream` calls `agent_run_events` internally (`service.py:1038`), and `subagents.py:144` calls `agent_run_events` directly. To avoid double-admission / self-deadlock, **admit at exactly one layer** (`agent_run`/`agent_run_events`); `agent_run_stream` is pure transport and does **not** admit, and `admit()` is a no-op when the current task already holds `Lock[sid]` (reentrancy guard for the subagent path).

```
admit(sid, kind):                              (sid = lower(session_uuid), required — never None)
  async with _admission:
    if _draining:                         -> 503 Draining (gateway re-buffers)
    if current task already holds Lock[sid] -> reentrant no-op (return existing run)
    if sid in {r.session_uuid for r in active.values()}:   # same session
        pass                              # does NOT consume a cap slot; serializes on Lock[sid]
    elif len({r.session_uuid for r in active.values()}) >= cap:
        -> 429 AtCapacity (X-Cap, X-Inflight)
    op_id = uuid4(); active[op_id] = SessionRun(sid, kind)
    lock = locks.setdefault(sid, asyncio.Lock())
  await lock.acquire()                    # OUTSIDE _admission; may block on a live run
  run.last_activity = monotonic(); mirror lock:session (status only)
  return run, lock

release(run, lock):
  lock.release()
  async with _admission:
    active.pop(run.op_id, None)
    # lock-map GC strictly under _admission; retain-with-LRU (never evict-on-release)
  # last_activity = JSONL mtime (M5 — no TouchSession / session table)
```

Lock-map GC is **retain-with-LRU** (bounded like `_VISION_SESSIONS_MAX`), never evict-on-release — there is exactly one `Lock` object per live `sid`, no `setdefault` race, and no reliance on the private `asyncio.Lock._waiters`.

**Boot-drain backpressure (§11 step).** The boot inbox drain admits up to `cap` concurrent and **leaves the remainder in the inbox** (non-destructive peek / ack-on-completion, not `LPOP`-before-start), so a crash mid-drain loses no promised turn and the cap is never exceeded at boot.

```
              POD (cap = 3)            A, B, C active; D arrives
 ┌──────────────────────────────────────────────────────────────┐
 │ Lock[A] held ── run A (CLI)        active sessions = {A,B,C}  │
 │ Lock[B] held ── run B (CLI)        len == cap                 │
 │ Lock[C] held ── rewind C                                      │
 │ 2nd turn for A ─▶ waits Lock[A]  (NOT a new cap slot)         │
 │ admit(D,"run"): D not active, len>=cap ─▶ 429 to gateway      │
 └──────────────────────────────────────────────────────────────┘
```

### 5.6 Fork / rewind interaction with the lock

| Op | Lock | Key rationale | Cap | Idle |
|---|---|---|---|---|
| run / retry | `Lock[session_uuid]` | appends to `<session_uuid>.jsonl` | new session ⇒ 1 slot; same ⇒ 0 | counted |
| rewind | `Lock[session_uuid]` | writer to same JSONL + workspace files | `kind="rewind"` | counted |
| out-of-band fork | `Lock[CHILD session_uuid]` | O_EXCL-writes the **child** JSONL (parent read-only) | `kind="fork"` | counted |
| resume-time fork | `Lock[CHILD]` after capture (§3.3) | child write under the live process | part of the run | counted |

The `fork_session=True` **run flag** (mechanism 2, §3.2) is treated as `kind="fork"` on the child (after capture), exempt from the §2.5 quarantine — **not** as an ordinary `kind="run"` on the same session (which would key the wrong lock and quarantine the legal fork). `/fork` and `/rewind` acquire the lock as their **first** action, before any FS write, and touch `last_activity`, so the idle export can never publish idle mid-fork/mid-rewind.

### 5.7 Concurrency invariants

1. At most one coroutine holds `Lock[session_uuid]`; all four JSONL writers acquire it.
2. The `asyncio.Lock` is the authoritative gate; `lock:session:` is a mirror only.
3. Cap = distinct active `session_uuid`s ≤ `min(mem, quota, 3)`, in the pod, 429 over cap.
4. All `RunContext` maps keyed on `lower(session_uuid)`; `checkpoint_uuid` is opaque, never transformed.
5. No self-deadlock: admit at one layer + reentrancy no-op; nested ops never re-acquire.
6. Drain-safe: every in-flight op is a `SessionRun` in `active`; `_draining` blocks new admits; SIGTERM awaits `active` empty (§8.4); `active` non-empty ⇒ non-idle.
7. No pod-minted ids: `admit` requires a platform-minted `session_uuid`. The gateway/control-plane MUST mint for **every** CREATE including ephemeral/subagent runs (`subagents.py:144` is routed a minted child/fresh `session_uuid`, never `None`).
8. WS and SSE permission paths both register into `RunContext.coordinators[session_uuid]` (today the WS path bypasses the registry via `coordinator_out`, `agent.py:734-747`, while only SSE registers at `service.py:1003`) so `/permission/respond` resolves on either path.

---

## 6. Permission coordinator & pin-pod-awake

### 6.1 The invariant

The `asyncio.Future` the run awaits **never leaves the pod** (decision 6). Redis is a **router and liveness signal — never the rendezvous.** Today's `PermissionCoordinator` already implements the in-pod half correctly: a per-request `asyncio.Future` keyed by a minted `request_id` (`permission_coordinator.py:45-50`), `request_permission` puts the request on the event queue and awaits the Future (`:52-67`), `resolve()` sets it by `request_id` (`:89-104`). What is missing is the Redis durability/routing layer; what is wrong is the module-global `registry` with `remap_session` and the 600s timeout.

```
┌──────────── AGENT POD (account_a) ─────────────┐
│ coordinator.request_permission(tool, input)    │
│   1. request_id = uuid4()                       │
│   2. future = create_future()  ◄ STAYS IN-POD   │
│   3. pending[request_id] = future               │
│   4. Tier-1 mint BEFORE the await:              │
│        HSET approval:index:{request_id}         │
│          {session_uuid,account_id,pod,          │
│           status=pending} EXPIRE 25h            │
│        SADD pin:approval:{account_id} request_id│
│   5. emit("permission_request", {request_id})   │
│   6. await wait_for(future, approval_window=24h)│
│   7. finally (resolve/timeout/cancel):          │
│        SREM pin ; HSET status=resolved ; EXPIRE │
│        (do NOT DEL index — see §6.3)            │
│ RunContext.coordinators[session_uuid] (per-pod) │
└──────▲────────────────────────────────▲─────────┘
       │ resolve(request_id, decision)    │ heartbeat
       │ (local OR via in:reply relay)    │
  gateway replica Rx (reply hours later)  operator reconcile
  HGETALL approval:index → {session_uuid,pod}     SCARD pin:approval > 0 ⇒ REFUSE sleep
  status==resolved? idempotent no-op
  owns pod WS? POST /permission/respond : RPUSH in:reply:{request_id}
```

### 6.2 Gate-time ordering (mint pin BEFORE the await)

The Tier-1 writes must **confirm before** the Future is awaited, else a pod dying between `await` and the Redis write leaves a parked Future with no pin (operator sleeps it → unresolvable) or a pin with no index. `approval:index:{request_id}` (HASH, **TTL 25h = approval_window+1h**, data-spine §4 #3) and `pin:approval:{account_id}` (SET, no TTL, data-spine §4 #4) are Tier-1/AOF, paired 1:1; `in:reply:{request_id}` (LIST, ~60s, data-spine §4 #7) is the cross-replica relay.

### 6.3 Index lifecycle (idempotent dup-guard)

On resolve/timeout/cancel the `finally` does **`SREM pin:approval` + `HSET approval:index status=resolved` + re-`EXPIRE` 25h** — it does **NOT `DEL` the index**. Keeping the index (status=resolved) is what makes a duplicate/late reply (two replicas, retried card tap, `in:reply` racing a direct POST) resolve **exactly once**: the gateway `HGETALL`s it, sees `status=resolved`, and idempotently no-ops instead of wrongly telling the user "expired, re-ask" for a request that was actually answered. `resolve()` already guards on `future.done()` (`permission_coordinator.py:99`). The index is `DEL`-ed only by the boot-purge of orphans (§6.5) and by TTL expiry. `cancel_all` (`:106-110`) and the timeout branch (`:68-77`) must clear Tier-1 (SREM pin + status=resolved), not just the in-memory `pending` dict.

### 6.4 Cross-replica / late-reply routing

A reply may arrive hours later on any gateway replica (sticky-L4 is best-effort; the flow is the blueprint's "2 · Permission rendezvous (reply hours later)", `multi-tenant-platform.md:260-272`). `POST /permission/respond` (`agent.py:485`) changes from `registry.get(request.session_id)` to resolve `session_uuid → RunContext.coordinators → coordinator.resolve(request_id, …)`; the gateway sends `session_uuid` (from the index HASH). The `owner_username` 403 (`:488-490`) becomes a **hard** assertion that the gateway-asserted actor **== the session's owning account**: **no impersonator may answer an approval (locked §13-6)** — a permission request is answerable only by the user, via their authenticated WebUI session or their bound IM channel; a `resolve` whose actor ≠ the owning account is **rejected (403)** even if audited. (This reverses blueprint decision-10's "impersonator may answer approvals, audited"; impersonation may still spend/act under audit, just not answer approvals.) If the receiving replica does not own the pod WS, it `RPUSH`es `in:reply:{request_id}`; the replica owning the WS `BLPOP`s and POSTs on its held connection.

### 6.5 Boot-purge (zombie-pin fix) + the operator gate

Every pod boot, **at boot step [B6c] strictly before inbox-drain [B7c] and before READY [B9]** (§11), runs an **unconditional** purge: a freshly-booted pod holds zero live Futures, so every `pin:approval:{account_id}` member is by definition orphaned → `DEL approval:index:{request_id}` + `SREM pin:approval` atomically (Lua/MULTI). This is safe **only** because of that boot ordering; if it ever changed, the sweep must become the conditional re-validate-against-live-Futures form (data-spine §4.x). The 25h index TTL is the final backstop if a pod never reboots; a periodic operator-side reaper purges pins whose `pod` field maps to a Deployment with zero ready replicas (bounds the worst case below 25h).

The operator's full sleep predicate is `SCARD pin:approval:{account_id} == 0` **AND** `lock:session:` absent/expired **AND** `route.state=idle` past `idle_grace` — pin>0 is a **hard override** forcing refusal, not the entire predicate. Pin-before-emit ordering (§6.2) makes the reply-after-sleep race safe-by-construction: the pin precedes the `permission_request` emit which precedes the human ever seeing the card, so the operator cannot mis-sleep an about-to-park run. A late reply for a purged `request_id` is answered "expired, re-ask" and never wakes a pod.

### 6.6 Timeout reconciliation + accept-loss

The in-pod `PermissionCoordinator.timeout` default **600s** (`permission_coordinator.py:30-32`) is **driven from `approval_window` (24h)** so a parked approval is not denied before the operator's pin window expires (the 600s default contradicts pin-on-approval). On timeout the coordinator SREMs the pin + sets status=resolved atomically. Accept-loss (decision 5): a pod death destroys the parked Future; the tool never ran (it was awaiting approval); the user re-asks with a fresh `request_id`.

### 6.7 SIGTERM vs pins

On graceful SIGTERM (§8.4 step [6]) pins are **deliberately left untouched** — a run parked on approval cannot reach a turn boundary in the grace window, so survival is via the 25h index TTL + boot-purge + operator reaper. Only resolve/timeout/cancel clear pins.

---

## 7. Secrets & BYOK-key boot

### 7.1 What is broken today

| # | Current | File:line | Conflict |
|---|---|---|---|
| C1 | One hardcoded Fernet key, shared fleet-wide | `crypto.py:10` | secrets decrypted by the **operator** (which holds KMS RBAC) and injected at spawn as a tmpfs K8s Secret — the pod never decrypts and never touches KMS (decision 23; §13-3) |
| C2 | `ANTHROPIC_AUTH_TOKEN` plaintext on shared RWX | `user_env.py:31,34-48` | secrets → envelope-encrypted `secret` rows |
| C3 | `options.env` carries only `ANTHROPIC_*`; no `CLAUDE_CONFIG_DIR`/`HOME` | `options.py:160,205` | the CLI resolves config home from process HOME → wrong place / RO-rootfs fail |
| C4 | No `~/.claude.json` sanitization, no active base_url probe | absent (grep: 0 hits) | a `.claude.json` `env` block overrides `ANTHROPIC_BASE_URL` → wrong-gateway 400s (MEMORY) |
| C5 | Credential read lazily per turn raising 400 | `options.py:151-157` | resolve once at boot into memory |

### 7.2 Locked decisions

| # | Decision |
|---|---|
| SB1 | **Credential-free pod (locked §13-3 — reverses the earlier per-pod-unwrap design).** The pod calls **no KMS** and holds **no DEK**. At spawn the **operator** fetches the account's wrapped secrets from the data-plane, unwraps them via KMS (operator-scoped RBAC), and injects the plaintext bundle into the pod as a **tmpfs-backed projected K8s Secret** (mounted, never on the PVC). Blast radius = one account (the operator injects only that account's secrets into that account's pod). |
| SB2 | **One secret class (M6 — BYOK; was a two-class proxy/virtual-key split).** The pod holds the user's **own real LLM provider key** (`ANTHROPIC_AUTH_TOKEN` = the user's key; `ANTHROPIC_BASE_URL` = the provider or a user-set endpoint) in the **same** operator-injected tmpfs bundle as the locally-consumed secrets (stdio-MCP tokens, user env vars) — one class, all SB1. There is **no metering proxy and no virtual key**: the virtual key existed only to keep a *shared org key* out of the pod; under BYOK the key is the user's own, so there is no cross-tenant secret to protect — which is *why* M6 lets it go. KMS is still touched **only by the operator** (which unwraps the bundle at spawn); the pod has **no KMS network path or RBAC and holds no DEK** (§13-3 still holds). The org-wide KMS backend (Vault Transit vs cloud KMS) remains open (data-spine §7-Q1) but does not block the pod build. *Reversible: a future shared-key/metering mode re-introduces the two-class split.* |
| SB3 | Plaintext secrets live only in pod-process memory + the tmpfs secret mount — never on the PVC, never persisted in `options.env`-on-disk, never logged (`mask_token`, `user_env.py:99-104`). Swap is disabled on the node pool (anonymous memory + tmpfs must not page to disk). |
| SB4 | `CLAUDE_CONFIG_DIR` injected per-account in **both** `os.environ` at boot and `options.env` per run (§1.3); `HOME` pinned to the per-account writable mount. |
| SB5 | The sanitized config is written to **`$CLAUDE_CONFIG_DIR/.claude.json`** (= `config_home/.claude.json`), re-asserted every boot, with **no `env` block** (and stripped `apiKeyHelper`/`primaryApiKey`/`ANTHROPIC_*` keys). Because `CLAUDE_CONFIG_DIR` is always set, the CLI reads `.claude.json` from `$CLAUDE_CONFIG_DIR/.claude.json`, **not** `$HOME/.claude.json` (SDK `session_resume.py:324-325,360-364`) — so the config-home-relative write is the load-bearing override defense, not HOME-pinning. The file persists on the PVC across scale-to-zero, which is exactly why it must be overwritten each boot. |
| SB6 | **Base_url check relaxed (M6 — no proxy to assert).** The old signed-sentinel "prove the request reached *our* metering proxy" round-trip is **moot** under BYOK: the pod calls the provider directly with the user's own key, so there is no shared meter to bypass. A lightweight **format / reachability sanity check** of the effective base_url (reusing `resource.py:42-57`) may remain and gate `Readyz`, but the anti-bypass-the-meter intent is gone. The `.claude.json` `env`-block sanitization (SB5) still removes a tenant-seeded override so the bundle's base_url wins. *Reversible: the signed-sentinel probe returns with a future metering mode.* |
| SB7 | **`ANTHROPIC_AUTH_TOKEN` IS the user's own BYOK key (M6 — was the proxy virtual key).** `ANTHROPIC_BASE_URL` is the provider (or a user-set endpoint) from the same bundle. Both are set in `env_dict` from the operator-injected bundle; the `.claude.json` `env`-block sanitization (SB5) still removes a tenant-seeded override so the *bundle's* values win — but there is **no force-override-to-a-meter** (it is the user's own key; there is no shared meter to redirect off). "Revocation" = the **user rotating their own key**, re-injected by the operator at next spawn (a new generation); there is no proxy-side revocation. |
| SB8 | **Spend tracking DEFERRED (M6 — token-count-only).** No `spend:reserve` arming, no reserve-before-call, no `402 budget_exceeded`, no boot spend-reconcile. Instead the pod **self-reports token usage** from each SDK `result` (`num_turns`, input/output tokens) to the data-plane — **observability-only, nothing enforced** (trust is fine because nothing gates on it). `quota.monthly_spend_cap_usd` stays as config but is not enforced. *Reversible: keep the token counts flowing so `$ = tokens × price card` + caps re-arm later by restoring the reserve/`402` path.* |
| SB9 | Boot fails closed: any failure in fetch/unwrap/decrypt/sanitize/probe keeps `Readyz` red. The fail-closed guard (`options.py:152-157`) is preserved, rebased on the in-memory bundle. |
| SB10 | The hardcoded Fernet key (`crypto.py:10`) is removed from the pod image (migrator-only, then deleted). |

### 7.3 Boot secret pipeline (the ordered steps live in §11)

Load the **operator-injected secret bundle** from the tmpfs mount into memory (`ANTHROPIC_BASE_URL`=provider/user-set endpoint, `ANTHROPIC_AUTH_TOKEN`=**the user's own BYOK key**, `ANTHROPIC_MODEL`, plus any local MCP/user secrets; exact env-var names match the fail-closed guard `options.py:156` and the probe `resource.py:36`) — **no KMS call, no DEK** (SB1/SB2) → write sanitized `$CLAUDE_CONFIG_DIR/.claude.json` → **relaxed base_url sanity check** (M6 — no proxy to assert, SB6). `build_agent_options` replaces `read_user_env(username)` (`options.py:151`) with `secrets.env_bundle()` from the in-memory provider; the credential file `settings.local.json` is dropped from the read path (credentials flow via `options.env`). **No `spend:reserve` arming or boot reconcile (M6, SB8)** — the only boot settle-or-release is the §6.5 pin purge; per-turn token usage is self-reported to the data-plane after each run.

### 7.4 Wake-free reads honored

Transcripts/audit/hook-logs are plaintext JSONL on the per-account PVC, read by the read-only `state-reader`/`audit-reader` (data-spine §3.9) with their own mount-scoped RBAC — **no read needs the unwrapped DEK and no read wakes the pod.** The pod is woken only to run a turn.

---

## 8. Idle detection & scale-to-zero

### 8.1 The idle predicate

```
idle_eligible :=
      no in-flight run        (RunContext.active has no SessionRun for any session_uuid)
  AND no pending approval     (coordinators have no pending Future AND pin:approval:{account_id} empty)
  AND no live PTY             (await pty_session.list_active_sessions() == [])   ← async; pod/account-level
  AND no writer-capable reader attached  (no run-stream/composer/PTY WS on the pod;
                                          pure transcript reads go through the state-reader,
                                          are pod-independent, and CANNOT block idle — data-spine §3.9)
  AND (now - last_activity) > idle_grace_seconds        (default 1800s/30min, quota.idle_grace_seconds — locked §13-1)
  AND (now - started_at)    > min_alive_after_wake_s     (default 180s, CRD floor)
```

The pod **never scales itself**; it **exports `IdleState`** (and a `route.state=idle|busy` hint). The `AgentTenant` operator is the **sole** scaler via CR patch. `pin:approval` non-empty is a hard override of the timers. `list_active_sessions()` is `async` (`pty_session.py:442`) and reads the process-global `_active_sessions` keyed by username (`:399`) — in a single-account pod this is pod/account-level liveness, independent of `session_uuid`; the predicate must `await` it. With the locked 30-min `idle_grace` (§13-1), minimum alive time per wake ≈ `max(idle_grace, min_alive_after_wake)` ≈ 1800s; both are per-tenant CRD knobs.

### 8.2 What survives the PVC vs is lost

```
SURVIVES (RWX PVC + audit vol, re-attached on wake):
  <session_uuid>.jsonl · subagent sidechains · FILE CHECKPOINTS · workspace files · audit JSONL
LOST on scale-to-zero (by design, accept-loss decision 5):
  PermissionCoordinator Futures · asyncio.Lock state · _vision_sessions · live PTYs · unflushed last turn
```

File checkpoints persist because they are local-disk-only on the PVC (`session_store_validation.py:40-43`); the design must NEVER introduce a remote `session_store` for checkpointed sessions.

### 8.3 Heartbeat & idle export

One background heartbeat task (replacing the perpetual `_temp_cleanup_loop`, `main.py:123-131`) writes every ~30s: `route:{account_id}` HASH (T2, ~30s TTL — miss ⇒ "treat asleep → wake", safe), `awake:set` membership (T2), `lock:session:` mirror (T2, ~30s). The **only** Tier-1 key the pod writes for lifecycle is `pin:approval`/`approval:index` (§6) — the operator's hard scale-to-zero gate.

### 8.4 Graceful SIGTERM drain (net-new — none exists today)

There is no signal handling in the kept lifespan (`main.py:158-171` only cancels the temp task + disconnects bridges; the only `SIGTERM` handling in the tree is the PTY killing its own child, `pty_session.py`). The pod adds an explicit deterministic drain so a turn is not truncated mid-JSONL-write (a SIGKILL-truncated write by the **sole** writer can leave a partial line that breaks resume — stronger than losing a turn):

```
SIGTERM:
 [1] readiness → NotReady (gateway/operator stop routing new turns)
 [2] _draining = True; new admits → 503 (gateway re-buffers to inbox)
 [3] for each in-flight SessionRun: client.interrupt() (stop at next tool boundary,
       client.py:313) → await run finish/flush JSONL (bounded by grace) → client.disconnect()
       (terminate CLI subprocess, client.py:608); force-kill on teardown failure
 [4] flush + fsync JSONL; report route.state=idle only AFTER this
 [5] spend: nothing to settle (M6 — tracking deferred, SB8); token usage already self-reported per turn
 [6] pin:approval / approval:index: DO NOT TOUCH (must survive; boot-purge/TTL/reaper decide)
 [7] DROP route/awake (best-effort; self-heal via TTL)
 [8] exit 0
```

**Locked drain policy (§13-1):** the drain is **drain-aware** — let the in-flight turn reach its next `ResultMessage`, persist + fsync, then exit — bounded by `termination_drain_max_s` (default **≈300s**, per-tenant CRD), after which the pod force-interrupts + checkpoints rather than block scale-down indefinitely. `terminationGracePeriodSeconds` must cover that worst-case (interrupt + flush + disconnect) bound and `uvicorn`'s `timeout_graceful_shutdown` must be ≥ that; a run that overruns the max is accept-loss. Idle-triggered termination is safe by construction (the operator honors the "no in-flight run / no pin / no live PTY" idle predicate as a hard pre-SIGTERM gate, so nothing is running); the drain max only bites on **involuntary** termination (node drain / evict / redeploy). Re-issued rewind is idempotent (§4.4), so a SIGKILL mid-restore is recoverable on retry.

### 8.5 Wake path — CR patch is the sole authority

```
gateway/scheduler wants to wake account_id
  → pre-gate (active + under cap)  → durable Tier-1 inbox RPUSH (ack "on it…" only after this)
  → SET awake:lock:{account_id} NX PX~10s  → CR PATCH spec.wake.requestedAt  ◀ ONLY scale-up trigger
  → (optional) PUBLISH wake:pod  (reconcile NUDGE only)
  → operator scales 0→1, re-attaches PVC/Service/NetworkPolicy → POD BOOT (§11) → drains inbox FIFO
```

`Recreate` (not `RollingUpdate`) is mandated so two pods never co-mount one user's PVC (single-writer-per-PVC; §9).

### 8.6 State machine

```
        CR patch wake
   ┌──────────────────────────────────────────────────────────────┐
   ▼                                                                │
 ┌──────────┐ boot ok  ┌──────────┐ turn  ┌──────────┐             │
 │ SCALED-0 │────────▶ │  AWAKE/  │──────▶│ RUNNING  │             │
 │ (no pod) │          │  IDLE    │◀──────│ lock held│             │
 └──────────┘          └────┬─────┘ done  └────┬─────┘             │
      ▲ operator scales 0    │ approval minted   │ approval minted  │
      │ (idle pred met AND    ▼                   ▼                 │
      │  pin set empty)     ┌─────────────────────────────────┐    │
      └─────────────────────┤ PINNED (pin:approval non-empty) │────┘
        NEVER while pinned   │ operator REFUSES scale-to-zero  │
                             │ no tokens, Future parked ≤24h   │
                             └─────────────────────────────────┘
  SIGTERM → DRAIN (§8.4) → SCALED-0 ;  PINNED → SCALED-0 only after resolve/timeout/boot-purge
```

---

## 9. Pod security context & mounts

### 9.1 Threat model

Hundreds of semi-trusted internal users (decision 1). The **primary tenant boundary is the pod itself** — one pod + one per-user PVC + per-tenant NetworkPolicy + minimal RBAC — not a sandbox. Hardened `runc` (decision 26) closes every misconfiguration escape; a kernel 0-day is an accepted v1 risk with `runtimeClassName` reserved for gVisor/Kata. The widest in-pod surface is the **PTY** (best-effort `rlimits`, every `setrlimit` wrapped `try/except`, `pty_session.py:158-172`; no PTY-layer seccomp) — it leans on this pod-level context.

### 9.2 The runc hardening matrix (locked)

```
securityContext (pod + per container):
  runAsNonRoot: true
  runAsUser:  <Uacct>     # == owner uid of /export/<account_id>, consistent across nodes
  runAsGroup: <Gacct>;  fsGroup: <Gacct>
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities: { drop: ["ALL"] }     # incl. CAP_SYS_ADMIN, CAP_DAC_OVERRIDE, CAP_CHOWN
  seccompProfile: { type: RuntimeDefault }
  # NO privileged, NO hostPath, NO host namespaces
Deployment: replicas:1, strategy:Recreate   # two pods can never co-mount the RWX PVC
runtimeClassName: runc   # reserved-settable to gvisor/kata per tenant (decision 26)
```

`runAsUser` must equal the export-dir owner uid so POSIX `0700` + foreign-uid is a real backstop; NFS export uses `root_squash`, never `no_root_squash`; the operator owns the account→uid mapping. `Cube`/E2B sandbox-as-a-service is explicitly out (it solves per-execution isolation for the shared-host model the pod-per-user model already supersedes).

### 9.3 Why RO-rootfs forces the mount design

With the rootfs RO, **every** write path must be an explicitly mounted writable volume. The writes that break today: the CLI's JSONL + file-history (resolved from `CLAUDE_CONFIG_DIR ?? ~/.claude` — injected nowhere today, §1.3); `seed_bundled_skills()` at **boot** writing `priva_home()/resource/skills` (default `~/.config/priva`, `paths.py:7-16`, `main.py:106`); `get_user_workspace`'s `makedirs(work_dir/<username>)` on every run (`auth.py:141`, default `~/priva_workspace`, `config.py:15`); the credential file `settings.local.json` (`user_env.py:56`); and the `/tmp/priva_pty_preexec.log` append (`pty_session.py:75`). All default-`$HOME` paths (`PRIVA_HOME`, `work_dir`, `CLAUDE_CONFIG_DIR`, the credential surface) must be pinned to writable PV paths at boot, or the pod does not boot.

### 9.4 Mount topology (locked)

```
 POD (account_id=A)   runtimeClassName: runc   runAsUser: Uacct
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ MOUNT                       SOURCE                      MODE   WRITER         │
 │ /  (rootfs)                 image                       RO     —              │
 │ /workspace                  subPath=<A> of /export RWX  RW     pod (sole)     │  cwd
 │ /pv/<A>/claude              per-user RWX PV             RW     pod (sole)     │  == CLAUDE_CONFIG_DIR
 │   ├ projects/<cwd_hash>/<session_uuid>.jsonl                                 │
 │   ├ projects/<cwd_hash>/<session_uuid>/subagents/agent-*.jsonl               │
 │   ├ <file-history snapshots>   (local-disk-only)                             │
 │   ├ .claude.json              (sanitized, re-asserted each boot, SB5)        │
 │   └ skills/ · mcp/ · hook logs (per-user overlay)                            │
 │ /pv/<A>/state (PRIVA_HOME)   per-user RWX PV            RW     pod            │  priva_home/work_dir/creds
 │ /audit/<A>                  dedicated audit volume      RW     pod (sole)     │  /audit/<A>/YYYY-MM-DD.jsonl
 │ /tmp                        emptyDir{medium:Memory}     RW     pod            │  tmpfs (sizeLimit set)
 └────────────────────────────────────────────────────────────────────────────┘
 THE POD NEVER MOUNTS /export OR /audit WHOLE-TREE.
 The read-only state-reader/audit-reader are the ONLY whole-tree mounts (data-spine §3.8/§3.9);
 they serve cross-tenant reads and never wake the pod.
```

**Three defense-in-depth layers (data-spine §3.8).** (1) scoped mount = primary — **locked (§13-5): the operator emits a per-user PV / CSI access-point whose root *is* the account subdir** (parent unreachable by construction; natural per-user quota); `subPath:<account_id>` on a shared PVC is the adequate fallback. The chosen CSI driver must support per-user uid + `root_squash`. The isolation is **asymmetric by design**: pods get narrow RW to their own subdir, while the trusted read-only `state-reader`/`audit-reader` get a **broad whole-tree RO** mount across all users (the only whole-tree mounts) to serve the control panel's wake-free cross-tenant reads — pods cannot traverse up, but the trusted reader can read across. (2) POSIX `0700` + non-root = backstop; (3) drop-ALL + non-root = no-escalation perimeter. The audit volume is **separate** from the session PVC (retention split: PVC 30d, audit ≥1y, decision 24); `get_audit_logger()` is constructed with `base_dir=/audit/<account_id>` (§1.4). `/tmp` is a sized tmpfs (it counts against pod memory; cap a runaway scratch so it cannot starve the concurrency budget).

### 9.5 In-pod path containment

FileCanvas (`built_in.py:64-82`) computes `_workspace_root = os.path.realpath(cwd)` and rejects files whose `commonpath != workspace_root` — correct only if `cwd` is the canonical `/workspace` mount root, so boot must `realpath`-canonicalize the mount root before any FileCanvas server is built, and `cwd` is the per-project subdir (not `work_dir/<username>`, `auth.py:134-142`, stripped). PTY `os.chdir(cwd)` into `/workspace`; the mount namespace + `0700` + foreign-uid + drop-ALL keep an interactive `cd ..` from reaching a sibling.

### 9.6 NetworkPolicy (default-deny pod-to-pod, scoped egress)

| Direction | Peer | Verdict |
|---|---|---|
| Ingress | **agentgateway** (browser `/v1` byte path — run/stream, fork, rewind; **brain-steered to this pod** via EPP `x-gateway-destination-endpoint`, control-panel §2.3-cp / agent-gateway §0.3) | allow |
| Ingress | **channel-connector** (IM byte path — relays the pod run/stream + fans out; agent-gateway §4.4) | allow |
| Ingress | **the brain** (the discrete **permission-relay POST**, agent-gateway §7.3 — coordination, not bytes) | allow |
| Ingress | Operator (probes) | allow |
| Ingress | any other pod | **deny** |
| Egress | LLM provider / user-set endpoint (`ANTHROPIC_BASE_URL`, the user's BYOK key) | allow (M6 — **direct, no proxy**) |
| Egress | general internet — MCP HTTP/SSE, hooks, agent tool web | **allow (M6 — egress gateway DEFERRED; was forced through a forward proxy)** |
| Egress | data-plane (gRPC/mTLS) | allow |
| Egress | Redis T1+T2 | allow |
| Egress | DNS (kube-dns) | allow |
| Egress | KMS | **deny** (the pod holds no KMS path — §7 SB1/SB2) |
| Egress | other tenant pods | **deny** (default-deny pod-to-pod still holds) |
| Egress | IM endpoints (WeCom/Feishu/Discord/OpenClaw) | **deny** (the gateway owns IM) |

The `state-reader`/`audit-reader` read the **volume**, never the pod, so the pod's NetworkPolicy needs **no** reader rule. **Egress security gateway DEFERRED (M6 — was the single forced outbound HTTP path, §13-2):** the MCP validator (`mcp/validator.py`) and command/HTTP hooks (`hooks/executor.py`, `hooks/builder.py`) make their outbound calls **directly** "for the moment" — no allow-list, no forced traversal, no credential injection at egress. **All outbound creds (the BYOK LLM key, MCP, hooks) ride the operator-injected bundle (§7 SB1)**, not an egress proxy. The default-deny-internet rule is relaxed (operator §2.4); **pod-to-pod stays default-deny**. *Reversible: re-add the forward proxy + allow-lists + the deny-direct-internet rule to restore controlled egress (and, with M6 lifted, metering).*

---

## 10. Code deltas (current → Agent Pod)

| File:line (current) | Change |
|---|---|
| `main.py:21-37,203-216` | Remove imports + `include_router` for `auth`, `admin`, `admin_files`, `channels`, `scheduler`. |
| `main.py:9-15,18,74-89,187,221` | Delete mimetypes font reg, scalar import, `_mount_static_assets`/`_mount_web_app`/`_configure_docs`. API/WS only. |
| `main.py:47-71,189-201` | Reduce `/health` to `{status,version,time}`; add `/readyz` (RED until boot done); drop `_detect_local_ip`/`_public_host`/`_base_url`. |
| `main.py:111` | Delete `get_user_store().list_users()` boot probe. |
| `main.py:137-142,158-169` | Delete OpenClaw auto-connect loop + disconnect. |
| `main.py:123-131` | Replace perpetual `_temp_cleanup_loop` with on-wake bounded sweep / `ExpireTempFiles`; rescope `cleanup_expired_files()` to the single account. |
| `main.py:96-171` (lifespan) | Insert the §11 boot: `os.environ["CLAUDE_CONFIG_DIR"]/["HOME"]/["PRIVA_HOME"]` dual-set → data-plane client → **load operator-injected secret bundle from the tmpfs mount (no KMS call)** → write sanitized `$CLAUDE_CONFIG_DIR/.claude.json` → signed-sentinel base_url probe → Redis T1+T2 → spend reconcile → orphan-pin purge → eager `get_audit_logger(base_dir=/audit/<A>)` → canonicalize `/workspace` realpath → construct `app.state.run_ctx` + SIGTERM handler + heartbeat → register route/awake → `Readyz` green. |
| `main.py:106` | Retarget `seed_bundled_skills()` to the per-account `config_home`. |
| `main.py:179-185` | Narrow CORS to the gateway/mesh origin. |
| `options.py:260-261` | Replace `if session_id: options.resume = session_id` with the `is_first_run` CREATE/RESUME split; add `is_first_run`, `config_home` params; `sid=session_uuid.lower()`; `assert not (session_id and resume)`. |
| `options.py:265-266` | Rebind fork guard to the RESUME shape: `if fork_session and options.resume:` (`session_id` is None on the fork path). |
| `options.py:160,205` | Inject `env_dict["CLAUDE_CONFIG_DIR"]=config_home`; set `ANTHROPIC_BASE_URL`/`AUTH_TOKEN` from the operator-injected **BYOK** bundle (M6 — no force-override-to-a-meter); add `assert options.session_store is None` when checkpointing. |
| `options.py:151-157` | Replace `read_user_env(username)` with `secrets.env_bundle()` (boot-unwrapped in-memory); keep the fail-closed guard reading the bundle. |
| `options.py:144` | `cwd` pinned to `/workspace` (drop `os.path.expanduser(settings.server.work_dir)`). |
| `service.py:684,995` | Delete `stream_id = session_id or str(uuid.uuid4())`; require platform `session_uuid`; register coordinator under it. |
| `service.py:469,487-489,508-510,522-523` | `agent_run`: delete capture; retry sets `options.resume=sid` AND `options.session_id=None` together; heal/strip by derived path; audit/vision keyed on the immutable `sid`. |
| `service.py:820-829,847-855` | `agent_run_events`: delete the system.init/result capture + coordinator/stream_id rebind; add the §2.5 assertion (`is_fork=options.fork_session`). |
| `service.py:919-920,931` | `agent_run_events` retry: `options.resume=sid`+`session_id=None`; derived heal/strip path. |
| `service.py:1003,1021-1024,1079` | Register coordinator under `session_uuid` (`:1003`); **delete** `registry.remap_session` (`:1024`) + `stream_id=new_sid` (`:1025`); unregister by `session_uuid` (`:1079`). |
| `service.py:104-115` | `_vision_sessions` → `RunContext.vision`, lowercase-keyed. |
| `service.py:436,641,977,1038` | Admit at one layer (`agent_run`/`agent_run_events`); `agent_run_stream` does not admit; reentrancy no-op; `last_activity` touch; 429 over cap; 503 if draining. |
| `service.py:513,876` | Replace `await asyncio.sleep(1)` flush-grace with a deterministic CLI-exit await inside the held lock + drain budget. |
| `permission_coordinator.py:126-130,133` | Delete `remap_session`; move the dict into `RunContext.coordinators` keyed on immutable lowercase `session_uuid`; remove the `__init__.py:2,4` export. |
| `permission_coordinator.py:30-32` | Drive `self.timeout` from `approval_window` (24h). |
| `permission_coordinator.py:45-79` | Add Tier-1 mint (HSET index +25h TTL + SADD pin) before the await; finally = SREM pin + HSET status=resolved (NOT DEL). |
| `permission_coordinator.py:68-77,106-110` | Timeout + `cancel_all` clear Tier-1, not only the in-memory dict. |
| `routers/agent.py:16,104-107,459-461` | `_session_jsonl_path`/delete path derive **in-pod via the SDK** (single-account, M5 §2.7) — not from a central row. |
| `routers/agent.py:485,488-490` | `/permission/respond`: resolve `session_uuid → RunContext → coordinator`; **hard** account-match assertion — reject (403) any actor ≠ the owning account, **no impersonator** (§13-6). |
| `routers/agent.py:503-543,514,517,531-537` | `/rewind`: gate on `Lock[lower(session_id)]` (409 on live run/PTY/pending approval); hold across the op; `cwd=/workspace`; lowercase; per-account audit writer; RESUME shape (spend reserve dropped — M6). |
| `routers/agent.py:546-572,552,555,563,566,569` | `/fork`: under `Lock[CHILD]`, lowercase parent+child, **forkedFrom-header lineage (M5 — no `RecordForkLineage`)**, filesystem two-phase materialize, child unbound by default; drop `Depends(get_current_user)`; `cwd=/workspace`; actor=`account_id`. |
| `models/agent.py:24,209` | `session_id` is a RESUME selector only; add `is_first_run` to `AgentRunRequest`/`WsInitFrame`; never a mint source. |
| `session_heal.py:6,18`, `retry.py:8,43` | **Keep** `_get_project_dir(_canonicalize_path(cwd))` (correct in-pod, single-account — M5 §2.7); run under the in-pod lock; write to `/workspace`/PV. |
| `audit_log.py:78,312-316` | `get_audit_logger(base_dir=/audit/<account_id>)`, eagerly constructed in the lifespan; fail boot if unset; no `priva_home()` fallback. |
| `paths.py:14-16` | Require `PRIVA_HOME` pinned to a writable PV path; drop the `Path.home()` fallback. |
| `pty_session.py:75,442` | Remove/gate the `/tmp/priva_pty_preexec.log` write; `await list_active_sessions()` wired into the idle predicate. |
| `crypto.py:10` | Delete the hardcoded Fernet key from the pod image (migrator-only). |
| `config.py` (new fields) | `approval_window=24h`, `idle_grace_seconds` (default **1800s/30min**), `min_alive_after_wake_s`, `termination_drain_max_s` (≈300s), Redis T1/T2 + data-plane addr + `SECRET_MOUNT` tmpfs path (**no KMS addr**), `checkpoint_budget_mb` (≈2048); read-only from the CRD/quota. |
| `services/secrets/*` (NEW) | `boot.py` (loads the operator-injected tmpfs secret mount → in-memory `SecretBundle`), `claude_config.py` (sanitized `.claude.json`), `baseurl_probe.py` (signed-sentinel). **No `kms.py` in the pod** — KMS unwrap lives in the operator (§7 SB1/SB2, §13-3). |
| `services/dataplane/client.py` (NEW) | Thin client: `Get*Config` + (IM) `BindChannel`/`RebindChannel`/`ClaimFirstRunIM`. **M5: no `MintSession`/`GetSession`/`TouchSession`/`RecordForkLineage`** — WebUI mints are gateway-local, sessions live on the filesystem, lineage is the JSONL `forkedFrom` header, listing/`fork_count` via the state-reader. **No `GetWrappedSecret`** — the operator fetches+unwraps secrets at spawn, not the pod. |
| `AgentTenant` CRD / operator (NEW manifests) | securityContext (§9.2), scoped mounts (§9.4), `runtimeClassName`, `replicas:1 strategy:Recreate`, per-tenant NetworkPolicy (§9.6), `terminationGracePeriodSeconds`. No k8s/Dockerfile manifests exist in the repo today. |

---

## 11. Boot sequence (authoritative)

The operator scaling 0→1 (CR patch) is the **only** wake trigger. `Readyz` flips green only after every step passes; each failure keeps the pod NotReady and serves no turn (SB9).

1. **[B0] Process start.** `create_app()` (no import-time cross-tenant side effects). Read injected identity: `ACCOUNT_ID`, `CONFIG_HOME`, `WORKSPACE_DIR=/workspace`, `PRIVA_HOME`, `DATA_PLANE_ADDR`, `REDIS_T1/T2`, `SECRET_MOUNT` (tmpfs path). **No `KMS_ADDR` — the pod has no KMS path (§7 SB1/SB2).** Set `os.environ["CLAUDE_CONFIG_DIR"]=config_home`, `os.environ["HOME"]=account_mount_home`, `os.environ["PRIVA_HOME"]=<writable PV>` (§1.3). `configure_logging` → pod log volume / stdout-JSON.
2. **[B1] Open data-plane client** (scoped to `ACCOUNT_ID`); `Readyz` handshake.
3. **[B2] Load the operator-injected secret bundle** from the tmpfs `SECRET_MOUNT` into memory: `ANTHROPIC_BASE_URL`(provider/user-set endpoint), `ANTHROPIC_AUTH_TOKEN`(**the user's own BYOK key** — M6), `ANTHROPIC_MODEL`, + local MCP/user secrets. **Already plaintext — no KMS call, no DEK** (the operator did the unwrap at spawn). Fail-closed if any required key is missing.
5. **[B4] Write sanitized `$CLAUDE_CONFIG_DIR/.claude.json`** (no `env` block; strip override keys, SB5).
6. **[B5] Relaxed base_url sanity check** of the *effective* base_url (post-sanitize) — format/reachability only (M6 — no proxy to assert, SB6); the old signed-sentinel anti-bypass probe is withdrawn.
7. **[B6a] Connect Redis** T1 (persistent) + T2 (cache).
8. **[B6b] Spend reconcile DEFERRED (M6, SB8)** — no `spend:reserve` to reconcile; per-turn token usage is self-reported to the data-plane after each run (observability-only).
9. **[B6c] Boot-purge orphan approvals** — `SMEMBERS pin:approval:{account_id}` → atomic `DEL approval:index` + `SREM pin` (unconditional; a fresh pod holds zero Futures) (§6.5). **Strictly before the inbox drain.**
10. **[B7a] Eager `get_audit_logger(base_dir=/audit/<account_id>)`**; canonicalize `/workspace` realpath; seed bundled skills into `config_home`.
11. **[B7b] Construct `app.state.run_ctx`** (cap, locks, coordinators); install the SIGTERM handler (§8.4) and the ~30s heartbeat task.
12. **[B7c] Drain `inbox:{account_id}` FIFO** — admit up to `cap` concurrent, **leave the remainder in the inbox** (non-destructive peek / ack-on-completion); excess over cap stays buffered.
13. **[B8] Register liveness** — `HSET route:{account_id} {pod_ip,pod_name,state,updated_at}`; `SADD awake:set account_id`; start route/lock-mirror heartbeats.
14. **[B9] READY** — `/readyz` green; serve `/api/*` + `/ws/*`; begin idle accounting (§8.1).

Ordering invariants: B2→B5 precede any LLM call (the fail-closed credential read + the relaxed base_url check are hoisted to boot); the approval purge (B6c) precedes the inbox drain (B7c) so prior-death pin state is cleared before new state can be minted (the spend reconcile B6b is gone under M6); the CR patch is the only scale-up trigger (`wake:pod` pub/sub is a nudge).

---

## 12. Resolved Risks

| # | Risk (adversarial pass) | Severity | Resolution (folded into the spec) |
|---|---|---|---|
| R1 | M2 split not actually delivered; `options.py:260-261` left as unconditional `options.resume` | blocker | §2.3/§2.6 land the CREATE/RESUME split + six-site capture deletion + `remap_session` deletion as one atomic commit; §10 lists all sites. |
| R2 | CREATE-then-retry emits the illegal `--session-id --resume` pair | blocker | §2.3: retry sets `options.resume=sid` **and** `options.session_id=None` together and re-asserts mutual exclusion (or routes back through `build_agent_options(is_first_run=False)`). |
| R3 | Deleting `remap_session` without its caller (`service.py:1024`) is a build break | blocker | §2.6/§10: register under `session_uuid` at `:1003` and delete the `:1024` call **with** the method. |
| R4 | `cwd_hash` re-implementation diverges from the SDK and forks the JSONL | blocker | **M5/§2.7:** the pod never re-implements `cwd_hash` — it lets the **in-pod SDK** compute it (correct because single-account), so nothing is persisted and nothing can diverge; CI round-trip assertion against the pin. |
| R5 | Checkpoint path unverified; may hit RO-rootfs or ephemeral storage (lost on scale-to-zero) | blocker | §4.2: FC3 locked **contingent** on a Phase-0 probe that locates the snapshot dir and asserts a persistent writable PV (never `$HOME`/`$TMPDIR`). |
| R6 | Fork via the run flag (`fork_session=True`) unhandled → wrong lock key + quarantined | blocker | §3.2/§3.3/§5.6: rebind the guard to the RESUME shape; treat as `kind="fork"` on the child; the §2.5 assertion exempts `is_fork`. |
| R7 | `heal`/`strip`/fork resolve the path via the `os.environ`-only `_internal.sessions` helpers → wrong tenant dir | blocker | §1.3 dual-set `os.environ["CLAUDE_CONFIG_DIR"]` at boot (narrow §1.6 exception); §2.7 derive read paths centrally. |
| R8 | `.claude.json` sanitizer writes the wrong file (`$HOME` vs `$CLAUDE_CONFIG_DIR`) → override trap survives | blocker | SB5: write to `$CLAUDE_CONFIG_DIR/.claude.json`; the config-home-relative write is the load-bearing defense. |
| R9 | RO-rootfs write inventory undercounted (skill seed, work_dir, creds, PRIVA_HOME) → pod won't boot | blocker | §1.3/§9.3: pin `PRIVA_HOME`/`work_dir`/`CLAUDE_CONFIG_DIR`/credential surface to writable PV; retarget `seed_bundled_skills`. |
| R10 | Live credential file unaddressed by mounts + secrets | blocker | §7.3/§9.4: credentials flow via `options.env` from the in-memory bundle; `settings.local.json` dropped from the read path. |
| R11 | SIGTERM / scale-to-zero-mid-op data loss (no handler exists) | blocker | §8.4: net-new deterministic drain (interrupt→flush→disconnect), operator pre-SIGTERM idle gate, force-kill on teardown. |
| R12 | Double-admit / self-deadlock (`agent_run_stream`→`agent_run_events`; subagents) | blocker | §5.5: admit at one layer + reentrancy no-op; `agent_run_stream` is transport-only. |
| R13 | `approval:index` DEL-on-resolve breaks the idempotent dup-guard (wrong "expired" to the user) | blocker | §6.3: finally = SREM pin + HSET status=resolved + 25h TTL; DEL reserved for boot-purge/TTL. |
| R14 | base_url probe (`/v1/models`) proves reachability, not proxy identity → MEMORY trap survives | dissolved (M6) | **Moot under BYOK** — no shared meter to bypass; the pod uses the user's own key directly (SB6 relaxed to a format/reachability check). The `.claude.json` `env`-block sanitization (SB5) still removes a tenant-seeded override so the bundle's base_url wins. *Re-arms with a future metering mode.* |
| R15 | 600s coordinator timeout denies a 24h-parked approval before the pin window | major | §6.6: `self.timeout = approval_window` (24h); the timeout path SREMs the pin. |
| R16 | Permission timeout vs `approval_window` and pin lifecycle inconsistent in the state machine | major | §6.2-6.7: pin minted before await, cleared only on resolve/timeout/cancel, left on SIGTERM, purged at boot; operator gate AND-s pin/lock/route. |
| R17 | In-pod authoritative lock absent; rewind gated on the registry, not the lock | major | §5.3/§4.3: `RunContext.locks[session_uuid]` is the gate; `/rewind` acquires it (409 on live run/PTY/pending approval); Redis is a mirror. |
| R18 | Rewind's `query("")` is a real turn (transcript write + token use + concurrency) | major | §4.3: acquire the in-pod lock + a concurrency slot; self-report its token usage like any turn (spend deferred — M6); pass the §2.5 assertion; do not claim "transcript untouched". |
| R19 | Fork bypasses sole-writer + writes outside the lock + crash-orphan window | major | §3.5: fork under `Lock[CHILD]`, two-phase `materializing` status + reconciler sweep. |
| R20 | Resume-time fork capture collides with the deleted M2 capture; orphan-pin on fork-while-approval | major | §3.3: a single fork-gated capture seam that migrates or **refuses** (409) while an approval is pending (F10). |
| R21 | Spend-reserve orphan not reconciled at boot; SIGTERM blind-DECRBY drops a real charge | dissolved (M6) | **No reservation exists** — spend tracking is deferred (SB8); the pod self-reports token usage per turn instead. No boot reconcile, no SIGTERM reservation handling. *Re-arms (SB8 reserve/reconcile) with a future metering mode.* |
| R22 | Per-run egress lets a tenant-set `ANTHROPIC_BASE_URL` bypass the metering proxy | dissolved (M6) | **No proxy to bypass** under BYOK — the key is the user's own. The `.claude.json` `env`-block sanitization (SB5) still ensures the operator-injected bundle's values win over a tenant-seeded override; no force-override-to-a-meter (SB7). *Re-arms with a future metering mode.* |
| R23 | Eager audit-logger construction missing; first request pins the global default | major | §1.4/§11 B7a: eager `get_audit_logger(base_dir=/audit/<A>)` in the lifespan, fail boot if unset. |
| R24 | `cleanup_expired_files()` iterates all users (cross-tenant) | major | §1.2: rescope to the single `account_id` before any in-pod sweep. |
| R25 | Live-pod key revocation unbounded (pinned pods keep an old key) | minor (M6) | The key is the **user's own** (BYOK) — "revocation" = the user rotating their own key, re-injected by the operator at next spawn; a live pod keeping the old key only spends the user's *own* quota at their own provider, not a shared org key. No proxy-side revocation (SB7). |
| R26 | Forked child silently drops subagent sidechains / undo history | minor | §3.1/F9: documented — a forked child cannot rewind into or re-reference the parent's sidechains. |
| R27 | Idle predicate: `list_active_sessions()` is async + the "attached reader" clause ambiguous | minor | §8.1: `await` it; only writer-capable attaches block idle; pure reads go through the state-reader and are pod-independent. |
| R28 | Stale fork "forbidden in MVP" framing | minor | data-spine C4 (2026-06-17) marks fork SUPPORTED; this spec treats it as the agreeing authority. |

---

## 13. Resolved Decisions (was Open Questions — locked 2026-06-18)

All seven open questions were answered by the user on 2026-06-18 and are now locked. Each is folded into the cited body section; this list is the decision record.

1. **Idle recycle + termination grace (§8.1, §8.4) — RESOLVED.** The **idle predicate** recycles the pod after **30 min idle** (`idle_grace_seconds` default **1800s**, per-tenant configurable on the CRD). The separate `terminationGracePeriodSeconds` knob covers an **involuntary** termination (node drain / evict / redeploy) while a turn is live: the pod is **drain-aware** — on SIGTERM it stops admitting, lets the in-flight turn run to its next `ResultMessage`, persists + fsyncs the JSONL, then exits; bounded by a **configurable max (≈300s)** after which it force-interrupts + checkpoints. Idle-triggered termination is safe by construction (nothing running). Both knobs are per-tenant on the CRD.

2. **Egress security gateway — DEFERRED (M6; was RESOLVED as a forced forward proxy, §9.6).** Outbound HTTP — LLM calls, HTTP/SSE MCP servers, command/HTTP hooks, agent tool web — goes **direct** "for the moment": no allow-list, no forced traversal, no credential injection at egress. **All outbound creds ride the operator-injected bundle** (the BYOK LLM key + MCP/hook secrets, decision 3), not an egress proxy. The default-deny-internet rule is relaxed (operator §2.4); **pod-to-pod stays default-deny**. *Reversible — re-add the forward proxy + allow-lists + deny-direct-internet to restore controlled egress (and, with M6 lifted, metering).*

3. **Pod is KMS-credential-free; one operator-injected secret class (§7, §11) — RESOLVED, reframed by M6.** The per-pod KMS `UnwrapDEK` path is **withdrawn**; the pod **calls no KMS and holds no DEK**. Under **M6 (BYOK)** there is **one** secret class, not two: the **operator** decrypts the account's whole bundle (the user's **own LLM key** + stdio-MCP tokens + user env vars) and **injects it at spawn as a tmpfs-backed K8s Secret** — the pod reads plaintext from the mount. There is **no metering proxy and no virtual key** (the virtual key existed only to keep a *shared org key* out of the pod; BYOK removes the shared key, so the indirection is moot). The pod now holds a real provider key — its *own user's*, so no cross-tenant secret risk. The org-wide **KMS backend choice (Vault Transit vs cloud KMS) remains open** (data-spine §7-Q1), scoped to the operator only — it does not block the pod build. *Reversible: a shared-key/metering mode re-introduces the proxy + virtual-key class.*

4. **Checkpoint budget adopted (§4.5, §4.6) — RESOLVED.** Snapshots live on the per-user RWX PVC under `CLAUDE_CONFIG_DIR` (FC3, contingent on the §4.2 Phase-0 probe). A per-tenant **`checkpoint_budget_mb`** quota is **adopted** (default **≈2 GB**, CRD-configurable): on over-budget the pod **errors explicitly + audits + prunes oldest-first** — **never** silently disables checkpointing (which would make later rewinds restore stale files). Eviction is a write, so it is tied to a wake-bearing event, never a reader sweep.

5. **Per-user CSI access-point for pods; state-reader broad RO (§9.4) — RESOLVED.** The operator emits a **per-user PV / CSI access-point whose root *is* the account subdir** as the default pod mount (parent unreachable by construction; natural per-user quota); `subPath:<account_id>` on a shared RWX PVC is the adequate fallback. The isolation is **asymmetric by design**: pods get narrow RW to their own subdir; the trusted **`state-reader`/`audit-reader` get broad read-only** scope across all users (whole-tree mount) to serve the control panel's wake-free cross-tenant reads. The chosen CSI driver must support per-user uid + `root_squash`.

6. **No impersonation answering of approvals (§6.4, decision 10) — RESOLVED.** A permission request is answerable **only by the user** — via their authenticated WebUI session (their own key) or their bound IM channel. The pod **rejects** any `resolve` whose gateway-asserted actor ≠ the session's owning account, **even an audited impersonator**. This **reverses** blueprint decision-10's "impersonator may answer approvals, audited." (Impersonation may still **spend the target's budget / use their tools, audited**, per the rest of decision 10 — only approval-answering is forbidden. If impersonation should be fully out, say so.)

7. **Gateway mints `session_uuid`; client never invents it (§2.2) — RESOLVED.** New-vs-resume is decided at the surface: **WebUI** sends an **empty** session id for a new session (session id in the body ⇒ resume); **IM** is always bound to a **fixed** `session_uuid` in `channel_binding` (a user **reset** mints a new one). On the empty/new case the **gateway mints** the canonical `session_uuid` (lowercase UUIDv4) **locally** (M5 — no `session_index` row/CAS), returns it, and the surface uses it thereafter — the **client never supplies/invents a uuid** (a client-chosen id could violate uniqueness / the no-remap invariant). `is_first_run` is server-derived (**IM** = `first_run_done` CAS on `channel_binding`; **WebUI** = empty-id + disk-existence guard, M5 §2.2), supplied per turn for **every** CREATE incl. ephemeral/subagent runs.

# Phase 1 — data-spine + protos

**Status:** code complete, all gates green (boot OK after every increment; 177 tests pass).
Push held pending user OK (per overall-goal §4).
**Branch:** `main`     **Depends on:** Phase 0 (`priva_common` extracted)
**Canonical refs:** the approved plan (`.claude/plans/explore-the-overall-migration-reactive-cake.md`),
`docs/architecture/data-relationship-map.md`, `components/data-spine.md`.

## 1. Objective & scope

Stand up **data-spine** — the durable-state layer — behind a stable, gRPC-shaped contract, and swap
the monolith's scattered file/YAML account + scheduler stores onto it. The schema was confirmed
table-by-table with the user and collapsed from the 16 originally designed tables to **5 in SQLite**
(`account`, `channel_binding`, `quota`, `scheduled_job`, `job_run_record`); everything else was
dropped, deferred, or left PVC-owned. Ships **in-process + a persistent SQLite file** for the alpha,
extensible by config to gRPC + Postgres. KMS is no longer a Phase-1 blocker (no envelope encryption).

**Out of scope (unchanged, file-backed):** runtime config, temp_file, MCP config, hook config,
session JSONL, audit JSONL, per-run output transcripts — all PVC-owned.

## 2. Design / approach

**Two config-driven seams** (`settings.dataspine`):
- **Storage** — `Repository` ABC; `SqliteRepo` built (WAL, `foreign_keys=ON`, single-writer lock);
  `PgRepo` is an interface-only stub.
- **Transport** — `priva_common.dataplane` client interface + DTOs; `InProcessClient` (the registered
  handlers) is the default; the gRPC client/server is structured but stubbed (lazy `grpc` import).
- Protos (`protos/priva_common/dataplane/v1/*.proto`) compiled now (contract gRPC-ready) into
  `priva_common.dataplane.v1`. Proto dir mirrors the python package so generated intra-imports
  resolve with no rewriting.

**Boundary kept clean (§6):** the contract + client live in `priva_common`; the service impls +
repo live in `services/data-spine` (`priva_data_spine`, installed editable). `priva_common` imports
no service. The composition root (monolith lifespan + each daemon's `main()`) calls
`priva_data_spine.compose()` once to build the repo + impls and register the in-process client.

**Façade swap pattern:** `get_user_store()` / `get_job_store()` / `get_run_history_store()` return
client-backed adapters that **preserve the existing username-keyed signatures**; username↔account_id
mapping is internal. So routers/auth/mcp_tools/daemon call-sites are unchanged — the swap is invisible
above the store layer.

## 3. Actions (increments; boot-green after each)

- [x] **P1 — scaffold:** protos + `protos/gen.sh` + generated stubs; `priva_common.dataplane`
  (client/factory/inprocess/grpc-stub); `services/data-spine` (repo/schema/service/migrate/CLI);
  `DataspineSettings`; additive `UserRecord` fields; grpc deps in `requirements.txt` + `libs/common`.
  *(commit `fbde984`)*
- [x] **S0 — `scheduled_agent`→`agent_run`:** canonical flip + back-compat remap in `scheduler.py`;
  propagated to `daemon.py` + `mcp_tools.py` (incl. LLM tool enum). *(commit `fbde984`)*
- [x] **U0 — split `RuntimeConfigStore`:** runtime config → its own file-backed store; `UserStore`
  delegates. *(commit `fbde984`)*
- [x] **U1 (+U2/U3/U4) — account layer → data-spine:** `UserStore` rewritten as a client-backed
  façade; `compose()` wired into `main.py` lifespan + scheduler/channels daemon `main()`;
  `priva-data-spine` installed editable. The façade automatically covers every account call-site
  (routers/auth, routers/admin, daemon existence checks, channels `list_users`) — U2/U3/U4 needed no
  separate edits.
- [x] **J1 — JobStore adapter:** client-backed; `save_jobs` expressed as a diff (create/update/delete)
  vs the stored set.
- [x] **R1 — RunHistoryStore adapter:** `append`→full-snapshot upsert; `query_cursor`→keyset
  `ListRuns` (cursors compatible via `priva_common._pagination`); `get_run`/`get_latest_run`
  ownership-safe; `purge_*`→`delete_runs_before` returning ids so the PVC transcripts are deleted
  file-side. Run `job_id` is FK-nulled when the job is absent.
- [x] **Migrate:** idempotent migrator (`python -m priva_data_spine migrate`) — account+quota,
  scheduled_job, job_run_record; `channel_binding` greenfield/empty.
- [x] **Tests:** rewrote the file-backend store tests (`test_scheduler.py`, `test_pagination_cursor.py`)
  against the SQLite backend; dropped the obsolete file-mechanism tests (daily partitions, counts
  sidecar, legacy migration, timing).

## 4. Deviations from the plan (all intentional)

- **U2/U3/U4 folded into U1.** The UserStore façade swaps all account call-sites at once, so per-router
  edits were unnecessary.
- **Monolith wiring moved P1→U1.** P1 stayed monolith-untouched (proven by a standalone in-process
  round-trip); the `compose()` wiring landed with its first consumer in U1.
- **Consolidated service layout.** `services/data-spine` uses `repo.py` / `schema.py` / `service.py`
  (not the finer `repo/` `service/` dirs in the plan) — fewer files, same boundaries.
- **`PgRepo` is a plain stub** (not a `Repository` subclass) so its ctor raises a clear
  NotImplementedError instead of the ABC's abstractmethod error.
- **Dev venv is `requirements.txt`-managed, not `uv sync`.** `uv sync` prunes the monolith's deps
  from the shared venv — use `uv pip install -r requirements.txt`. grpc trio added to requirements.

## 5. Acceptance criteria — verification log

- **codegen clean:** `./protos/gen.sh` → 6 domains generated; `from priva_common.dataplane.v1 import
  account_pb2` resolves. ✓
- **boundary CLEAN:** `grep -rnE '^\s*(from|import)\s+(api|services)\b' libs/common/src/priva_common`
  → no matches. ✓
- **5 tables, STRICT, FK on:** fresh SQLite → `{account,channel_binding,quota,scheduled_job,job_run_record}`,
  `PRAGMA foreign_keys==1`, account is STRICT, account-delete cascades to all children. ✓
- **in-process RPC round-trip:** create account → get_by_username → update api_key → find_by_api_key
  (HMAC) → quota/binding(CAS)/scheduler/run all round-trip; api_key encrypted-at-rest. ✓
- **auth core on SQLite:** JWT + per-user api-key auth via `authenticate_raw_token`; bad token rejected;
  password update; last-admin count; delete. ✓
- **migrate idempotent:** synthetic monolith data → run1 `{accounts:1,quota:1,jobs:1,runs:1}`, run2
  `{skipped:3}`; Fernet decrypt round-trip + HMAC lookup + `agent_run` confirmed. ✓
- **tests:** `pytest tests/` → **177 passed** (excluding two PRE-EXISTING broken modules, unrelated to
  this phase: `test_generated_mcp_tools.py` → missing `resolve_generated_files`; `test_logging.py` →
  missing `_HourlyRotation`). ✓
- **boot-check green after every increment:**
  `PYTHONPATH=priva:libs/common/src .venv/bin/python -c "import api.main"` → BOOT OK. ✓

## 6. Open items / handoff

- **Push pending user OK.** Foundation committed `fbde984`; U1→R1 + tests + migrate + this doc are
  uncommitted (awaiting the user's go to commit, then push to `origin` only).
- **Two pre-existing broken tests** (`resolve_generated_files`, `_HourlyRotation`) predate this phase —
  flagged for a separate cleanup; not Phase-1 regressions.
- **Deferred by design:** gRPC transport + Postgres backend (config flips, structured-but-unbuilt);
  `retention_state` + `job_fire` tables (later milestones); `channel_binding`/`quota` are greenfield
  with no Phase-1 writer (feishu channel not built).
- **Alpha bring-up:** `server.sh` works unchanged (data-spine in-process; sqlite at
  `~/priva_workspace/.priva.dataspine.db`). For an existing dev env, run `python -m priva_data_spine
  migrate` once so existing users/jobs/runs land in SQLite before first login. A fresh env's setup flow
  writes straight to SQLite.
- **Known simplification:** `JobStore.save_jobs` is N ops (not atomic); `SqliteRepo` serializes all
  access behind one lock (RO connection pool is a later optimization). Acceptable for the alpha.

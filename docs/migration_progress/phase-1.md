# Phase 1 — `protos/` + data-spine service

**Status:** not started
**Branch:** `split/phase-1`     **Depends on:** Phase 0 (needs `priva_common` + `redis_catalog`)
**Canonical refs:** `code-split.md` §8 (stores → data-plane), components/`data-spine.md` (§1.7 contracts, §2.11/§2.12 job & channel records, §4 Redis keys)

## 1. Objective & scope

Define the data-plane gRPC contracts and stand up the **data-spine** service over the DB, then **swap every file/YAML store call-site to the data-plane client**. This is sequenced first because everything downstream depends on it. The monolith keeps running — its stores just call the client instead of touching files.

**In scope:** `protos/` contracts; generated stubs exported from `priva_common`; the data-spine service; the data-plane client; migrating the stores in `code-split.md` §8.
**Out of scope:** splitting the agent runtime / brain / connector (Phases 2–4). **Session JSONL is NOT a store** — it stays on the per-tenant PVC and is read via state-reader (Phase 5); do not put it in a table.

## 2. Design / approach

Source: `code-split.md` §8 (the store → RPC table) + `data-spine.md`. Each store becomes an RPC:

| Today | → data-spine |
|---|---|
| `user_store.py` | `account` + `identity_link` RPCs |
| `channels/config_store.py` (YAML) | `channel_config_wecom/_openclaw` |
| `scheduler/job_store.py` (YAML) | `scheduled_job` |
| `scheduler/run_history.py` (JSONL) | `job_run` (birth/outcome split) |
| `hooks/log_store.py` | hook-logs RPC |
| `hooks\|mcp config_manager.py` | per-tenant config RPCs |
| `audit_log.py` | audit RPC |
| `config.py` (platform) | central config RPC |

Generated gRPC stubs live in `priva_common` so every service shares one client (`protos/README.md`). Local DB: SQLite + Redis (the catalog from Phase 0). Migrate store-by-store behind the client interface so each swap is independently boot-checkable.

## 3. Actions (checklist)

- [ ] Define protos: Accounts, Identities, Sessions, ChannelBinding, ScheduledJobs, JobRuns, Audit, Config.
- [ ] Generate stubs → export from `priva_common` (data-plane client).
- [ ] Implement data-spine service over the DB (SQLite/Redis local).
- [ ] Swap call-sites store-by-store (one increment each): `user_store` → `config_store` → `job_store` → `run_history` → hooks `log_store`/`config_manager` → `mcp` config → `audit_log` → platform `config`.
- [ ] Each swap: monolith call-site goes through the client; boot-check green.

## 4. Acceptance criteria

- All §8 store call-sites (`user_store`/`config_store`/`job_store`/`run_history`/`audit_log` + hook/mcp config) go through the data-plane client — none touch files directly.
- data-spine serves the RPCs locally (`uv run`).
- Monolith still boots: `PYTHONPATH=priva:libs/common/src python -c "import api.main"` (still applies — the monolith exists through Phase 3).

## 5. Open items resolved here

- _(none scheduled; if a store migration surfaces a schema question, record the decision here and back-annotate `data-spine.md`.)_

## 6. Verification log (append-only)

- _(empty — populate as you execute)_

## 7. Status & handoff notes

Not started. **First action:** define the protos and the data-plane client in `priva_common`, then migrate `user_store` as the first store (smallest blast radius) to validate the client pattern end-to-end before doing the rest.

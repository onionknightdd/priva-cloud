# Phase 0 — Monorepo skeleton + in-place boundary refactor

**Status:** in progress
**Branch:** `main` (fresh repo; work lands on `main` — see overall-goal §4)     **Depends on:** — (first phase)
**Canonical refs:** `code-split.md` §3 (target layout), §6 (`libs/common` contract), §6.1 (extraction order), §10 (verb legend)

## 1. Objective & scope

Stand up the monorepo and pull the shared contract layer (`libs/common` = `priva_common`) out of the monolith **without splitting any service yet**. At the end of Phase 0 there is still **one running monolith** — but its shared, cross-cutting code lives in `priva_common`, and the import boundary (§6 rule: `common` imports no service) is drawn and holds.

**In scope:** the skeleton; extracting `config`, `logging`, `crypto`, `_pagination`, `models/*`, `serialization`, and the NEW `redis_catalog` into `priva_common` via re-export shims.
**Out of scope:** standing up any `services/*` runtime, the data plane, protos, K8s. Those are Phase 1+.

## 2. Design / approach

**Re-export shim pattern** (the heart of the phase, `code-split.md` §6.1): for each module, the real code moves to `priva_common.X`; the old `api.*.X` path becomes `from priva_common.X import *`. The monolith keeps booting and **no importer changes** until that module's service is extracted in a later phase. Delete the shim only when the last `api.*` importer is gone.

**Two RESHAPEs (not pure moves) — handle each in its own increment:**
- **`config`** — `Settings.yaml_file = Path(__file__).parent.parent / "config.yaml"` (`priva/api/services/config.py:137`) is `__file__`-relative and breaks the instant the file moves. Repoint to an env var **`PRIVA_CONFIG_FILE`**, defaulting to the monolith's current config location (`priva/api/config.yaml`, absolute — **not** CWD-relative `./config.yaml`) so `server.sh` keeps working unchanged. Keep `yaml_file` a `Path` ClassVar — `logging.py:319` calls `Settings.yaml_file.parent.parent` to resolve relative log paths (config↔logging coupling; see the ordering note).
- **`serialization`** — imports `claude_sdk/retry.SYNTHETIC_MODEL`, a **pod** module. Lift `SYNTHETIC_MODEL` (a trivial string constant, `retry.py:17`) into `priva_common` as a shared wire/retry constant so `serialization` carries no pod dependency.

**Ordering note — `metrics` before `logging`:** `logging.py:280` lazily imports `HTTP_DURATION`/`HTTP_REQUESTS` from `..metrics`, so `logging` depends on `metrics` (a leaf). Extract `metrics` → `priva_common.metrics` **before** `logging` (folded into increment-3). The import is **lazy** (request path), so the boot-check won't surface a broken `metrics` import — exercise a real request after the move.

**The import boundary** (§6 rule, enforced this phase): `priva_common` may not import any service package. After the extractions, confirm `priva_common` has zero `api.*` / `services.*` imports.

## 3. Actions (checklist)

- [x] **increment-1:** monorepo skeleton — root `pyproject.toml` (uv workspace), `libs/common` package (`priva-common`, src layout, `__init__.py` with the no-service-import rule), `services/` `protos/` `deploy/` dirs + intent READMEs. *(commit `92470c2`)*
- [ ] **increment-2:** extract **`config`** → `priva_common.config` (RESHAPE: `__file__` → `PRIVA_CONFIG_FILE`), shim at `api.services.config`. 28 importers — shim keeps them untouched. **Prereqs:** (a) add `pyyaml` + `pydantic-settings` to `libs/common/pyproject.toml` (currently only `pydantic`, `redis`); (b) default `PRIVA_CONFIG_FILE` to `priva/api/config.yaml` (absolute, not CWD) and `export` it in `server.sh` to match `CONFIG_FILE` (`server.sh:13`); (c) keep `Settings.yaml_file` a `Path` ClassVar (`logging.py:319`); (d) `config.yaml` is absent today (only `config.example.yaml`) — confirm the missing-file case stays tolerated (defaults apply).
- [ ] **increment-3:** extract **`metrics`** → `priva_common.metrics` first (leaf; 2 importers), **then** **`logging`** → `priva_common.logging`, shim at `api.middleware.logging`. 44 importers. `logging` depends on **both** `config` and `metrics` (`logging.py:280` lazily imports `..metrics`; `:319` uses `Settings.yaml_file`) — re-point both when it moves. Lazy import ⇒ boot-check won't catch a broken `metrics`; exercise a real request.
- [ ] **increment-4:** extract **`crypto`** + **`_pagination`** (each depends on `logging`; 1 + 3 importers), shims at their old paths.
- [ ] **increment-5:** extract **`models/*`** → `priva_common.models` (38 importers), shims.
- [ ] **increment-6:** extract **`serialization`** (RESHAPE: lift `SYNTHETIC_MODEL` into `priva_common` first; 2 importers), shim.
- [ ] **increment-7:** add **`redis_catalog`** (NEW — pure addition, no shim; the T1/T2 key definitions, `code-split.md` §6).
- [ ] **close-out:** confirm `priva_common` imports nothing service-side; (optional but recommended) add a minimal smoke test since no suite exists.

## 4. Acceptance criteria

1. After **every** increment: boot-check green —
   ```bash
   PYTHONPATH=priva:libs/common/src python -c "import api.main; print('BOOT OK')"
   ```
   *After increment-3 additionally:* the `logging→metrics` import is **lazy** (`logging.py:280`), so `import api.main` won't exercise it — hit any endpoint and confirm `/metrics` is still populated.
2. `priva_common` imports **no** service package — verify:
   ```bash
   grep -rnE '^\s*(from|import)\s+(api|services)\b' libs/common/src/priva_common && echo "BOUNDARY VIOLATED" || echo "BOUNDARY CLEAN"
   ```
3. The monolith still runs via `priva/bin/server.sh` (config loads from `PRIVA_CONFIG_FILE` / its default).

## 5. Open items resolved here

- **OQ-2 (workspace tool) — RESOLVED: `uv` workspace.** Native path-dep workspaces; fast; clean for a from-scratch monorepo. (Back-annotated in `code-split.md` §12.)

## 6. Verification log (append-only)

- 2026-06-19 increment-1: skeleton committed `92470c2`; monolith untouched. Boot-check `PYTHONPATH=priva:libs/common/src python -c "import api.main"` → **BOOT OK** (verified). `uv` not installed locally (only needed for `uv sync` / `uv run`, not for the boot-check).

## 7. Status & handoff notes

Increment-1 (skeleton, `92470c2`) and the first docs pass are on `main` and **pushed to `origin`**. A first docs reconciliation pass landed 2026-06-19: `agent-pod`→`agent-runner` rename across `docs/`, the `priva-cloud` CLI / package-layout design in `code-split.md` §3.1/§3.2/§14, and the branch model corrected to work-on-`main`. A second, **pre-migration doc-refinement** pass followed (2026-06-19, docs only — no code): `metrics`-before-`logging` ordering (lazy import at `logging.py:280`), the increment-2 prereqs (deps + `PRIVA_CONFIG_FILE` default + `yaml_file` `Path` + missing-`config.yaml`), `callback`→agent-runner reclassification, and importer counts re-measured to 28/44/1/3/38/2. **Next: increment-2 — extract `config`** with the `__file__`→`PRIVA_CONFIG_FILE` RESHAPE (prereqs in §3), add the `api.services.config` shim, run the boot-check, append to the log above. Then proceed down the §3 checklist in order — the order is dependency-forced, don't reorder.

Do **not** push until the phase boots green *and* the user OKs (overall-goal.md §4).

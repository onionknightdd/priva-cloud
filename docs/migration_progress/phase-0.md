# Phase 0 — Monorepo skeleton + in-place boundary refactor

**Status:** done (increments 1–7 landed + boot-green; optional smoke test deferred)
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
- [x] **increment-2:** extracted **`config`** → `priva_common.config` (RESHAPE: `__file__` → `PRIVA_CONFIG_FILE`, CWD-fallback `api/config.yaml`, `yaml_file` kept a `Path` ClassVar); shim at `api.services.config`; `server.sh` exports `PRIVA_CONFIG_FILE`. Missing `config.yaml` still tolerated. Deps `pyyaml`+`pydantic-settings` added at close-out. *(commit `f4300b8`)*
- [x] **increment-3:** extracted **`metrics`** → `priva_common.metrics` (leaf, first) **then** **`logging`** → `priva_common.logging`; re-pointed logging's `config` (`:19`) + lazy `metrics` (`:280`) imports; shims at both old paths. Lazy edge verified by recording a metric + rendering `/metrics`. *(commit `5e53744`)*
- [x] **increment-4:** extracted **`crypto`** + **`_pagination`** (re-pointed each `..middleware.logging` → `priva_common.logging`), shims at old paths; encrypt/decrypt + cursor roundtrips pass. *(commit `64e43c8`)*
- [x] **increment-5:** extracted **`models/*`** → `priva_common.models` (whole package); `api/models/` is now a shim package — `__init__` + 14 per-submodule shims (importers use `from ..models.<sub> import`). *(commit `928c1a6`)*
- [x] **increment-6:** extracted **`serialization`** (RESHAPE: `SYNTHETIC_MODEL` lifted to new `priva_common.wire`; `claude_sdk/retry.py` re-imports it; serialization re-pointed `.retry`→`wire` and `...models.agent`→`priva_common.models.agent`); shim at old path. *(commit `3ab16a1`)*
- [x] **increment-7:** added **`redis_catalog`** (NEW — pure addition, no shim; the 7 T1/T2 keys from `code-split.md §6` + siblings/TTLs, stdlib only). *(commit `c3dd922`)*
- [x] **close-out:** `priva_common` imports nothing service-side (boundary CLEAN); all old import paths resolve through shims; `pyproject.toml` deps reconciled (added `pydantic-settings`, `pyyaml`, `prometheus-client`, `fastapi`, `loguru`, `cryptography`, `claude-agent-sdk==0.1.81`). Minimal smoke-test suite still deferred (no `tests/` yet).

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
- 2026-06-20 increments 2–7: each module extracted via re-export shim, **boot-check green after every increment** (`PYTHONPATH=priva:libs/common/src .venv/bin/python -c "import api.main"` → BOOT OK). Commits `f4300b8` (config), `5e53744` (metrics+logging), `64e43c8` (crypto+_pagination), `928c1a6` (models), `3ab16a1` (serialization+wire), `c3dd922` (redis_catalog). Per-increment spot-checks: shim re-exports are the **same objects** (identity asserted); config `yaml_file` resolves via `PRIVA_CONFIG_FILE`; lazy `logging→metrics` edge records + `/metrics` renders; crypto + cursor roundtrips; all 14 model submodule shims import; `SYNTHETIC_MODEL` single-valued across `wire` + `retry`.
- 2026-06-20 close-out: **boundary CLEAN** — `grep -rnE '^\s*(from|import)\s+(api|services)\b' libs/common/src/priva_common` → no matches. Full shim sanity (all old `api.*` paths import) → OK. Importer counts re-measured across **all** import forms (excl. shim), method `grep -rlE '<all-paths>' priva/api`: config 17, metrics 2, logging 42, crypto 1, `_pagination` 3, models 37, serialization 1 (counts gauge blast-radius only; the move order follows the import edges).

## 7. Status & handoff notes

**Phase 0 is functionally complete (2026-06-20).** All seven `libs/common` extractions landed via re-export shims, dependency-ordered, **boot-green after every increment**; the monolith still runs as one process and no importer changed. `priva_common` now holds `config`, `metrics`, `logging`, `crypto`, `_pagination`, `models/*`, `serialization`, `wire` (`SYNTHETIC_MODEL`), and the new `redis_catalog`; the §6 import boundary is drawn and verified CLEAN. `libs/common/pyproject.toml` deps reconciled to what the moved code needs.

Earlier passes (context): skeleton `92470c2`; docs reconciliation 2026-06-19 (`agent-pod`→`agent-runner`, `priva-cloud` CLI/layout in `code-split.md` §3.1/§3.2/§14, branch-model fix); pre-migration doc-refinement 2026-06-19 (`metrics`-before-`logging`, increment-2 prereqs, `callback`→agent-runner).

**Only remaining Phase-0 nicety:** stand up a minimal smoke test (import + a couple of route assertions) since there's still no `tests/` — deferred, not blocking.

**Next phase: Phase 1 — protos + data-spine** (`overall-goal.md §5`, `code-split.md §11`). Infra = none for now, so it proceeds with a stubbed data-plane client and the gates are flagged until real infra exists. Open the (to-be-created) `phase-1.md` and continue from there.

Push policy (overall-goal.md §4): push only when the phase boots green *and* the user OKs.

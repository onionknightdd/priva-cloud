# Phase 2 — agent-runner + control-panel (local alpha, clean break)

**Status:** landed (boot-green, runtime-verified)
**Branch:** `phase-2-agent-runner-control-panel`     **Depends on:** Phase 1 (data-spine)
**Supersedes** this doc's prior "not started" plan and its acceptance criterion #4
(the monolith intentionally no longer boots — clean break).

## 1. What shipped

The monolith (`priva/api`) was carved into **two runnable local deployables** plus a launcher,
implementing `code-split.md §13` local dev mode (A): plain processes, no K8s/agentgateway/operator.

- **`services/agent-runner`** (`priva_agent_runner`) — the agent runtime (`claude_sdk`) + the
  agent/session/permission/rewind/fork API + the execution faces of hooks/mcp/skills/subagents +
  pty + files. **Single-account**, pinned by `ACCOUNT_ID`, every route behind a signed
  `X-Priva-Runner-Token`. Serves **no HTML**.
- **`services/control-panel`** (`priva_control_panel`) — auth + admin + user-data + resource; owns
  data-spine (`compose()`); serves the **user SPA at `/`** and the **admin SPA at `/admin`**; the
  **dev edge** that reverse-proxies the runtime to agent-runner.
- **`tools/cli`** (`priva_cli`, dist `priva-cloud`) — the launcher.

scheduler + channel-connector remain **unavailable** (dormant in `priva/api`); their SPA tabs are hidden.

## 2. Architecture (as built)

```
 Browser ── GET /        → control-panel static  web/dist        USER SPA
        ── GET /admin    → control-panel static  web/dist-admin  ADMIN SPA  [role==admin]
        ── /api/auth,/api/admin,/api/user,/api/resource(quickactions|vision|models)
                         → control-panel serves directly
        ── /api/agent/*, /api/pty/ws, /api/files/*, /api/user/files/*, /api/hooks/*,
           /api/resource/(mcp|skills|skill-hub)/*, /api/subagents/*
                         → control-panel PROXY (+ minted HS256 runner token) → agent-runner
 control-panel + agent-runner each compose() the same SQLite (data-spine, in_process, WAL)
```

## 3. Increments (each committed boot-green)

1. **libs/common shared moves** — `paths`, `runtime_config_store`, `user_env`, `audit_log`,
   `user_store`, `sensitive_mask`, `script_lint` moved into `priva_common`; plus extractions
   `workspace.get_user_workspace` (keeps `UserRecord | None` sig) and `skill_exclude` (the
   `.priva.user.yml` accessors + denylist, lifted out of `channels/config_store`). `risky_matcher`
   later moved here too (shared by AR execution + CP risky-tools validation).
2. **agent-runner** — moved claude_sdk + the agent-coupled routers/services; `signed_header.verify`
   (HS256, via `priva_common.runner_token`) + `deps.require_account` replacing the auth deps; WS
   paths read the CP-injected header; options.py severed from scheduler/channels; AR-process
   credential override; `app.py` (compose + eager audit + skill seed) + `entry.py` (account-pin).
3. **control-panel** — auth/admin/admin_files/user_data/resource served directly; deferred admin
   endpoints (scheduler) → 503, per-user skill mgmt → 503 (proxied instead); CORS removed.
4. **reverse-proxy** (`proxy.py`) — explicit prefix list; httpx HTTP/JSON, streamed SSE
   (`aiter_raw`, unbuffered, follow_redirects so AR trailing-slash 307s don't leak its origin), and
   a `websockets` WS relay (peek init frame → authenticate → mint → inject header → bidir pump +
   close-code propagation).
5. **launcher** `tools/cli` — entry-point discovery; `serve` runs `data-spine init` one-shot (no
   daemon in in_process mode) then supervises agent-runner + control-panel.
6. **SPA split** — hid Scheduler/Channels tabs; stripped admin from `UserDataPanel` (user SPA);
   promoted `components/admin/*` into a new `AdminApp` shell + `index-admin.html`/`main-admin.jsx`,
   `ADMIN_BUILD` Vite switch → `web/dist-admin` (`base '/admin/'`), `npm run build:admin`.
7. **tests** — repointed to the split packages; deferred (scheduler/channels) skipped via
   `tests/api/conftest.py`. **88 passed, 4 skipped.**

## 4. Deviations from the plan (and why)

- **Router placement.** The plan's §C fine-grained dual-face split (CP serving hooks/mcp/skills
  config faces) proved partly infeasible: hook `prefs`/`catalog` depend on the in-process hook
  **registry**, which lives in AR — CP can't serve them without importing AR (forbidden). Resolution:
  **AR keeps the agent-coupled routers whole** (`hooks`, `mcp`, `skills`, `skill_hub`, `subagents`,
  `agent`, `pty`, `files`, `user_files`) and **CP proxies them**; CP directly serves only
  auth/admin/admin_files/user_data/resource. Still honors "no service→service imports" (CP proxies
  over HTTP) and meets acceptance. `/api/admin/pty/config` is proxied to AR (pty lives there).
- **`risky_matcher` → libs/common** (plan had it in AR). It is pure pattern logic shared by AR hook
  execution *and* CP risky-tools validation — same "shared logic → common" pattern as the other moves.
- **`get_audit_logger()`** takes no `base_dir` at runtime (it reads `PRIVA_HOME` via `priva_home()`);
  called eagerly in the AR lifespan after the env-pin.
- **Two stale tests fixed** (pre-existing on the base commit, not caused by this phase):
  `resolve_generated_files` → `resolve_file_canvas_files` (+ its error text); the require_permission
  hook's `user_store` patch target.
- **`serve` + data-spine:** in the in_process transport data-spine has no daemon, so `serve` inits
  the schema once and supervises only the two long-running services.

## 5. Verification log

- `python -c "import priva_agent_runner.app, priva_control_panel.app"` → ok (both build full routes).
- `priva-cloud serve --only data-spine,agent-runner,control-panel` → data-spine init + AR (`:8091`) +
  CP (`:8081`) all healthy.
- Login at CP → JWT; `GET /api/agent/sessions` through CP round-trips CP→AR with a minted runner
  token (200, `{"sessions":[],...}`); direct AR without the token → 401.
- Proxied config+execution faces return data: `/api/hooks/catalog`, `/api/resource/skills`,
  `/api/resource/mcp`. CP-served `/api/admin/users` returns the account.
- `POST /api/agent/run/stream` through CP → reaches AR's `build_agent_options`, returns the
  credential error as an unbuffered SSE `stream_error` event (full run plumbing proven up to the
  credential boundary — a live streamed run additionally needs a real `ANTHROPIC_*` key + the
  `claude` CLI, absent in this smoke env).
- `GET /` → user SPA (`<title>Priva</title>`); `GET /admin/` → admin SPA (`<title>Priva · Admin</title>`).
- `pytest tests/ -q` → **88 passed, 4 skipped**.

## 6. Remaining debt / next phases

- No package-boundary debt under the clean break (code physically moved into packages).
- **Phase 4:** lift scheduler + channel-connector (dormant in `priva/api`) into their own services.
- **Prod seams (later):** JWKS/mTLS (swap `signed_header`), per-pod `RunContext`, the M2 session-id
  create/resume split, operator/scale-to-zero + agentgateway.
- The fine-grained CP-served config faces (§C) could be revisited if the registry coupling is broken,
  but proxying them is correct and simpler for the alpha.

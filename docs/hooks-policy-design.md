# Hooks Policy — Complete Design Review (rev 4 · final · 2026-07-11)

> Status: design complete, implementation not started. All decisions closed, including UI.
> Produced in the 2026-07-11 brainstorm session (admin forced hooks). Numbered for review reference.

---

## §1 Problem, scope & motivating findings

**Problem:** "Admin forced skill/MCP/hooks is untested and has nowhere to be configured."

**Scope (this phase):** admin-stored hooks, force-on governance only. **Out:** forced skills, forced MCP, restrictive governance, per-group targeting, terminal-CLI governance, mcp_tool execution.

**Findings that shaped everything:**

| # | Finding | Consequence |
|---|---|---|
| 1 | Legacy admin→runner config channel **disconnected in k8s** — control-panel writes its own local `.priva.settings.yml`; runners read per-account `PRIVA_HOME=/workspace/.priva` (also affects presetprompt, risky-tools, PII, clipath, retention) | Policy goes through **data-spine** (the `runner_defaults` precedent) |
| 2 | Per-account files **user-tamperable** (whole pod = uid 10001) | Source of truth lives outside the account mount |
| 3 | Old plumbing orphaned: `enforced_hook_ids` had no writer; `ensure_admin_hooks` never cleaned revocations | Deleted wholesale (rev 3), not fixed |
| 4 | `retry-failed-tools` dead (imports nonexistent module); user **http hooks never fire** (engine runs `command` only; `settings.local.json` excluded from `setting_sources`); **audit log is a third per-pod silo** | Removals, shared http executor, §7 observability split |

---

## §2 Data model — `hook_policy` in data-spine (frozen)

One table + `HookPolicyService` gRPC (`List(enabled_only)` / `Upsert` / `Delete`), seeded insert-if-absent on startup. No `builtin` type exists.

```proto
message HookPolicy {
  string id              = 1;   // slug; seeds reuse legacy builtin slugs (prefs carry over)
  string hook_type       = 2;   // "command" | "http" | "mcp_tool" — native strings
  string name            = 3;
  string description     = 4;   // REQUIRED, zh-only content, shown verbatim to users (§9-H)
  repeated string events = 5;   // expanded per event at injection
  string matcher         = 6;   // "" = all tools
  int32  timeout_seconds = 7;   // command default 30 · http default 5

  string interpreter     = 8;   // command: "bash" | "python3"
  string script_body     = 9;   // command: ≤64 KiB — THE payload, stored in DB
  string content_hash    = 10;  // server-computed sha256

  string url             = 11;  // http
  string headers_json    = 12;  // http: env refs ("Bearer $TOKEN"), never literals
  repeated string allowed_env_vars = 13;

  string mcp_server      = 14;  // mcp_tool — reserved, validation-rejected in v1
  string mcp_tool        = 15;

  bool   enabled         = 16;  // admin master switch; new rows save FALSE
  bool   enforced        = 17;  // user cannot toggle
  bool   default_on      = 18;  // initial user state when not enforced
  bool   predefined      = 19;  // seeded: editable, disable-able, NOT deletable
  int32  seed_version    = 20;  // upgrade mechanism (§6)
  string target          = 21;  // reserved (canary/groups, v2)
  string updated_at      = 22;
  string updated_by      = 23;
}
```

---

## §3 Delivery & execution (programmatic-pure)

```
Admin saves → data-spine row (script_body + sha256)
  → each runner, next session build (snapshot fetch, ~30s TTL cache):
      hash changed → rewrite $PRIVA_HOME/admin-hooks/<id>/hook.{sh,py}
      row removed  → delete dir
  → registered as in-process SDK callbacks in build_hooks()
  → fires in-pod: `bash /…/hook.sh`   (interpreter-invoked, noexec-safe)
```

- **No settings.json involvement for admin hooks** — no `__priva_enforced` tags, no reconcile, no double-fire, no tamper surface. Visibility via catalog API.
- **Data-spine never on the fire hot path** — one gRPC round-trip per session build.
- **Concurrency (verified):** parallel within an event, blocking barrier per event (Pre gates tool, Post gates model continuation). Cost ≈ max(hook durations) per barrier; 1-core pods can serialize toward the sum.
- **Failure semantics:** exit 2 = deliberate block; any other non-zero / timeout / spawn failure = non-blocking, logged, ignored. Full Claude Code output-protocol parity (`permissionDecision`, `updatedToolOutput`, `systemMessage`, `continue`, stderr rules), tested against doc examples.
- **Environment:** constructed, never inherited — base allowlist (`PATH HOME LANG/LC_* TMPDIR TZ` + `CLAUDE_*` context + `PRIVA_LOG_DIR`) + per-row `allowed_env_vars`; `ANTHROPIC_*`/JWT/HMAC/DSNs default-denied. One `build_hook_env()`; applies to admin **and** user hooks.
- **http executor:** shared aiohttp callback (un-breaks user http hooks); header secrets via env refs only.
- **Precedence:** enforced > user pref (initial = `default_on`) > user's own hooks. **Conflict:** admin overwrites user via non-destructive shadowing (event+matcher+type+executable identity); "overridden by admin" in UI; auto-resumes if the admin row goes away.
- **Timing:** next session build; kill = disable, worst case ~30 s TTL + build. **Degraded:** warm pod = cached snapshot; fresh pod + data-spine down = fail-open (logged). **Boundary:** SDK sessions only; terminal-launched `claude` out of scope (stated in spec).

---

## §4 API surface

**control-panel** (`require_admin`, mutations audited): `GET /api/admin/hook-policy` · `POST` (create, saves `enabled=false`; validates slug/events/timeout 1–600/script ≤64 KiB/syntax compile-only/description required; `mcp_tool` → 422) · `PUT /{id}` (any field incl. `enforced`) · `DELETE /{id}` (409 if predefined) · `POST /validate`.

**agent-runner** (user-facing): `GET /catalog` → snapshot + per-user state (no script body) · `POST /catalog/{id}/enable|disable` → `user_hook_prefs` (403 if enforced) · `GET /config` → user hooks + admin virtual entries + `shadowed` flags. Removed: `/test/builtin`, `source_code` exposure. No registry-sharing refactor needed — the registry is gone.

---

## §5 UI design (final)

### §5.1 Admin — "Runtime" section inside Agent Runner Sandbox

New fifth entry in the page's internal section nav (below Image). Backend unchanged.

```
Agent Runner Sandbox › Runtime                        生效：下次会话（≤1 分钟）

 │ Lifecycle │ Resources │ Isolation │ Image │ ▌Runtime ◀ new

 ▾ PreToolUse ────────────────────────────────────────────  2/3 activated ─
   ▌block-dangerous-bash   CMD·Bash   PREDEFINED   [Enforced ●][Enable ●][Edit]
   ▌require-permission-…   CMD·all    PREDEFINED   [Enforced ●][Enable ●][Edit]
    audit-tool-use         CMD·all    PREDEFINED·2 events
                                                   [Enforced ○][Enable ○][Edit]
   ── [+ Add hook]                                            [Save (0)] ──
 ▸ PostToolUse ───────────────────────────────────────────  2/2 activated ─
 ▸ Stop ──────────────────────────────────────────────────  0/1 activated ─
 ▸ Notification / UserPromptSubmit / …    (every supported event gets a group)
```

```
[Edit] / [+ Add hook] open the standard right drawer (480px, slide-in 220ms):
┌─ drawer ────────────────────────────┐
│ block-dangerous-bash                │
│ PREDEFINED · seed v1（最新）         │
│ 描述（必填 · 用户可见）               │
│ [在执行前拦截 rm -rf、mkfs、dd 等…]  │
│ Type [command ▾]                    │
│ Events [✓PreToolUse][ PostToolUse]… │
│ Matcher [Bash]  Timeout [10] s      │
│ Interpreter [bash ▾]                │
│ Script                  [Validate]  │
│ ┌─────────────────────────────────┐ │
│ │ #!/usr/bin/env bash             │ │
│ │ input=$(cat) …                  │ │
│ └─────────────────────────────────┘ │
│ Env passthrough [PRIVA_LOG_DIR ×][+]│
│ (custom rows only: [Delete])        │
│ [Cancel]                    [Done]  │
└─────────────────────────────────────┘
```

Locked interaction rules:

- **Groups:** collapsible per event (150 ms chevron); empty events render collapsed so `[+ Add hook]` is always reachable; header count `n/N activated` = enabled/total in group. Multi-event hooks appear in every touched group (`2 events` marker) — same row, state syncs.
- **Row controls:** Enforced toggle → **confirm dialog at flip time** ("将对所有用户强制启用，用户无法关闭"); Enable toggle; Edit. 2px left border = enabled status; selection/hover via background only.
- **Commit model — staged per group:** toggle flips and drawer `Done` mark rows dirty; `[Save (n)]` commits via existing per-row PUTs (multi-event rows commit wholly from whichever group saves). **Exception:** Delete (custom rows, in-drawer) is immediate after typed-name confirmation — never staged. Predefined rows have no Delete (server 409).
- **Drawer type variants:** command = interpreter + mono script editor + Validate (server syntax check, errors pinned with line numbers); http = URL, headers key/value with env refs, allowed_env_vars tags, 5 s timeout, ⚠ PreToolUse-latency warning; `mcp_tool` = disabled option "即将支持".
- **New hook:** drawer pre-bound to the group's event; created `enabled=false` unless armed before Save.
- **Seed updates:** edited seeds show `新版本种子可用 · 查看差异` banner with side-by-side diff.
- **System compliance:** CSS variables only, skeleton shimmer (group-header + row shapes), lucide `strokeWidth={1.5}`, shared Dropdown, no dots/no shadows/no rounded-full. UI chrome bilingual via locale keys; only description *content* is zh.

### §5.2 User — existing Hooks panel, additive

```
▾ PreToolUse
  ▌Block Dangerous Commands            [ENFORCED]        ← locked, no toggle
    在执行前拦截 rm -rf、mkfs、dd 等破坏性 bash 命令。
  ▌Require Permission for Risky Tools  [ENFORCED]
  ▌Audit Tool Use                      [Enable ●]        ← non-enforced: toggleable
  ── your hooks ──
    my-pre-hook          CMD · Bash    覆盖提示: 已被管理员策略覆盖   ← shadowed
```

Admin/predefined hooks render first per event section: name + description + type chip; enforced = ENFORCED chip + disabled controls; non-enforced = working toggle (initial `default_on`); **no script body shown**; shadowed user hooks marked "overridden by admin".

---

## §6 Seeds & migration

| Seed (id = legacy slug) | Events / matcher | default_on | Notes |
|---|---|---|---|
| `block-dangerous-bash` | PreToolUse / Bash | ✓ | patterns live **in the script body** — edit patterns = edit script |
| `audit-tool-use` | Pre+PostToolUse / all | ✓ | lean stdlib JSONL append; trail per-pod in v1 (§7) |
| `lint-on-write` | PostToolUse / Write\|Edit | ✗ | bash + ruff |
| `require-permission-risky-tools` | PreToolUse / all | ✓ | matcher embedded; patterns → local JSON at session build |

Removed: `notify-slack`, `retry-failed-tools` (`retryable_tools` setting dormant); PII masking stays programmatic-only, untouched. **Seed upgrades:** release bumps `seed_version`; migration auto-updates rows whose hash == shipped hash; edited rows untouched + diff banner. Orphaned prefs ignored/cleaned.

**Deleted code:** `registry.py`, `built_in_hooks.py` (PII factory relocates), builtin logic in `prefs.py`/builder steps 1+3, `ensure_admin_hooks` + reconcile, `BuiltInHookInfo`, `/test/builtin`, runtime-config `enforced_hook_ids`/`hooks` reads.

---

## §7 Cross-cutting

- **Perf — accepted:** subprocess ~30–80 ms stdlib-only (bash ~5–15); seeds banned from importing `priva_common` (200–500 ms); parallel-per-event softens to max-per-barrier; measured free via `duration_ms`.
- **Observability split:** health → Prometheus from the executor (`priva_hook_fires_total{hook_id,status}` + duration histogram) on the runner's `/metrics`, ADR 0002 rails — this is also the blast-radius alarm. Audit *records* stay per-pod JSONL v1 (documented limitation); data-spine centralization is the follow-up.
- **Secrets:** none in policy — env refs resolved from the sanitized env at fire time.

## §8 Test plan

**Unit:** env allowlist (secrets absent), precedence, shadowing identity, hash materialization + orphan cleanup, 403 enforced-disable, protocol-parity fixtures from docs, validation matrix. **Integration:** gRPC roundtrip, `build_hooks` with fake snapshot, seed insert + upgrade (edited/unedited). **E2E (minikube):** create→fires; disable→gone ≤ TTL+build; enforce→chip+403; shadowing; crashing enforced hook doesn't block tools; metrics on `/metrics`; project-file hooks don't double-fire. **Perf:** seed `duration_ms` under budget.

## §9 Decision ledger — all closed

A terminal CLI out of scope → programmatic-pure · B blast-radius package (exit-2 semantics, save-disabled arming, enforce confirm, TTL kill path, executor metrics) · C patterns-in-script + DB delivery + seed_version upgrades · D constructed env allowlist · E full protocol parity · F Prometheus-for-health / JSONL-for-audit-v1 · G legacy slugs as ids · **H descriptions zh-only** · I interpreter invocation · UI: Runtime section, event groups, drawer, staged save. **Standing defaults (say the word to flip):** fail-open on data-spine unreachable (fresh pod); global scope v1.

**Parked:** audit centralization · Test-run execution (v1 = validate only) · legacy runtime-config knob migration · `skill_hub` require_user fix (recommend soon) · mcp_tool executor · canary targeting · restrictive governance · forced skills/MCP.

## §10 Build order

1. **Backend:** proto + table/repo/seeds + gRPC + dataplane stub → control-panel REST/validation/audit → runner: snapshot fetch, materializer, executor (env/parity/http), precedence+shadowing, metrics; delete builtin stack.
2. **Admin UI:** Runtime section in AgentRunnerSandbox (groups + drawer + staged save), locale keys.
3. **User UI:** catalog-driven sections, description cards, shadowed markers.
4. **Tests + spec edits** (7 sections + boundary statement, en + zh editions).

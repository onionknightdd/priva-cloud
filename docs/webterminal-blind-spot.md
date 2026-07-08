# Web Terminal — Blind-Spot Review

| | |
|---|---|
| **Scope** | The tenant-facing web terminal: xterm.js SPA → WebSocket → agent-runner PTY (`/api/sandbox/pty/ws`), the PTY session lifecycle, its auth/routing, and the pod it runs in. |
| **Audience decision** | **Tenant-facing (untrusted users).** This reframes the findings below from "hardening" to "launch blockers." |
| **Method** | Code read end-to-end **plus verification against the live minikube cluster** (`priva-cloud` namespace). Every "Evidence" line was observed, not inferred. |
| **Date** | 2026-07-07 |
| **Status** | Findings open. Nothing in the register has been remediated yet. The four session bugs in Appendix A are already fixed. |

> ⚠️ **One-line verdict:** a single tenant with terminal access can extract the platform's shared token-signing secret, forge a runner token for any account or admin, and — because there is no NetworkPolicy — reach any other tenant's pod directly and open a shell in it. **The terminal must not reach real tenants until Tier 0 below is done.**

---

## 1. The critical chain (how one terminal becomes full platform compromise)

Each step is independently verified.

1. **The shell inherits the service's environment.** `PtySession._fork_and_exec` (`services/agent-runner/src/priva_agent_runner/services/pty_session.py`) execs `bash -l` with `os.execvp`, which inherits `os.environ`. The agent-runner process env is populated by the pod's `envFrom`.
2. **That environment contains platform signing secrets.** `envFrom` pulls in the Secret `priva-shared-secret`, whose keys are `PRIVA_AUTH__JWT_SECRET` and `PRIVA_DATASPINE__API_KEY_HMAC_SECRET`. **These values are identical in every tenant's pod** (it is a single shared Secret).
3. **Those secrets sign the runner tokens.** `libs/common/src/priva_common/runner_token.py` → `_secret()` returns `dataspine.api_key_hmac_secret or auth.jwt_secret`; `mint()` signs an HS256 JWT with it; `verify()` checks it. So a tenant holding the secret can `mint()` a valid runner token for **any** `account_id` / `username`.
4. **There is no network isolation.** No `NetworkPolicy` exists in the namespace. From a terminal shell, TCP connects succeed to `redis`, `data-spine`, `control-panel`, `priva-gateway`, `kubernetes.default`, and every other `ar-<account>` pod.
5. **The account guard does not stop a forged token.** `deps._resolve` only checks `claims.account_id == ` the **target pod's** own `ACCOUNT_ID` env. A forged token sets that field to the victim's account, and the attacker reaches the victim pod directly (step 4), so the guard passes.

**Result:** extract secret → forge token for victim/admin → reach victim pod directly → shell in another tenant's pod, and/or authenticate to `data-spine`/`control-panel` as any account. Full multi-tenant + admin compromise from one terminal.

**Good news that narrows it:** the **Anthropic** token is *per-account* (`ar-<account>-creds`), not shared, so "steal one key and bill everyone" is off the table. The catastrophic part is exactly two **shared** secrets that the terminal has no legitimate need for — which is why the decisive fix (Tier 0 #1) is small.

---

## 2. Blind-spot register (detailed)

Severity key: **HIGH** = launch blocker for a tenant-facing terminal · **MID** = fix soon after · **DEFER** = track, low urgency or needs a decision/verification.

### HIGH

#### H1 — Platform signing secrets are readable in the terminal environment
- **What:** `bash -l` inherits `PRIVA_AUTH__JWT_SECRET` and `PRIVA_DATASPINE__API_KEY_HMAC_SECRET` from the agent-runner process env. They sign/verify every runner token, so possession = ability to forge tokens for any account or admin.
- **Evidence:** `env | grep -iE 'token|secret'` in a live shell returned `PRIVA_AUTH__JWT_SECRET=…`, `PRIVA_DATASPINE__API_KEY_HMAC_SECRET=…`, `ANTHROPIC_AUTH_TOKEN=sk-…`. `priva-shared-secret` holds the first two and is mounted into every ar pod via `envFrom`. `runner_token._secret()` uses exactly these.
- **Why it's a blind spot:** the secrets arrive via `envFrom` (invisible in the pod's inline env list) and are inherited implicitly by any child process. Nobody "put a secret in the terminal" — it leaked through two layers of inheritance (Secret → service env → forked shell). And the preview was already visible earlier: the OAuth token showed up in the crash **core dump** during the RLIMIT_AS investigation.
- **Impact:** the entire step-1→5 chain. Catastrophic.
- **Fix:** Tier 0 #1 — allowlist the shell's env (the terminal needs none of these; `claude` reads its creds from `settings.json`, see H-note below).

#### H2 — No NetworkPolicy: the terminal can reach every shared service and every tenant pod
- **What:** the pod (and therefore the terminal shell inside it) has unrestricted egress to the cluster.
- **Evidence:** `kubectl get networkpolicy -n priva-cloud` → *No resources found*. TCP connect from the shell succeeded to `redis:6379`, `data-spine:50051`, `data-spine:8090`, `control-panel:8080`, `priva-gateway:8080`, `kubernetes.default:443`.
- **Why it's a blind spot:** the design comments treat the EPP/edge as "the only path to the pod" (`extproc._steer`, the runner-token handshake). That assumption is false at L3/L4 — nothing enforces it. **Deeper trap:** the terminal shares the pod's netns/uid/cgroup/fs with the agent-runner service, so a NetworkPolicy can only constrain the pod *as a whole*. Whatever egress the service legitimately needs (e.g. data-spine), the terminal inherits. A policy shrinks the surface but cannot separate terminal from service while they share a pod.
- **Impact:** turns H1's forged token into cross-tenant reach; independent lateral-movement surface even without H1.
- **Fix:** Tier 0 #2 (default-deny + narrow egress allowlist) + the pod-split decision (§4).

#### H3 — Wrong threat model: "the sandbox is hardened, so the terminal is safe"
- **What:** the hardening in place — `capabilities: drop [ALL]`, non-root uid 10001, `readOnlyRootFilesystem: true`, `seccompProfile: RuntimeDefault` — defends against **breaking out** of the container. A user terminal's real threat is the opposite: the user is *supposed* to be inside; the risk is what the container legitimately *holds* (secrets in env, network reach, an SA token).
- **Evidence:** the pod securityContext is fully hardened, yet H1/H2/M1 are all wide open. The hardening and the actual exposure are orthogonal.
- **Why it's a blind spot:** it's a *conceptual* root cause, not a line of code. The hardening is real and correct — it just answers a different question than "what can a legitimate shell user reach/read?" Every other finding here is downstream of this mental model being off by one.
- **Impact:** systemic — it's why H1/H2/M1 shipped.
- **Fix:** adopt the "trusted-user-inside-container" threat model for the terminal specifically; Tier 0 items follow from it.

> **H-note (why the Anthropic token is *not* H-class):** the token in the shell is the tenant's own per-account credential, and by design (`libs/common/src/priva_common/user_env.py`) it also lives in the tenant's own `/workspace/.claude/settings.json` (0600, but they own the uid). It is tenant-readable no matter what we do to the env. It is therefore **not** a secret we can hide from the tenant, and it must not be a security boundary — see D5.

### MID

#### M1 — ServiceAccount token is automounted into the shell
- **What:** `/var/run/secrets/kubernetes.io/serviceaccount/token` is present and readable by the shell user.
- **Evidence:** file present in the live pod. `kubectl auth can-i --list --as=system:serviceaccount:priva-cloud:default` → only `selfsubjectreviews`/`selfsubjectaccessreviews`/`selfsubjectrulesreviews` (create) + API discovery (get). No resource access.
- **Why it's MID not HIGH:** the `default` SA's RBAC is thin today, so the token is not *directly* escalatable. But it is a live bearer credential in an untrusted shell, and the day anyone adds a RoleBinding to `default`, this silently becomes a terminal escalation.
- **Fix:** `automountServiceAccountToken: false` on the ar pod spec (operator `services/operator/src/priva_operator/kube.py`, `_deployment_body`).

#### M2 — No real per-session resource isolation (fork/memory bombs hit the agent runtime)
- **What:** the terminal shares the pod's single **2 GiB** cgroup with the agent-runner service and any claude runs. `pids.max` is `max` and there is no `RLIMIT_NPROC`, so a fork bomb is unbounded; a memory hog can drive the cgroup OOM-killer and evict the agent runtime.
- **Evidence:** `cat /sys/fs/cgroup/pids.max` → `max`; no `nproc`/`NPROC` in `PtySettings` (`libs/common/src/priva_common/config.py`) or `pty_session.py`; pod `resources.limits.memory = 2Gi`; single container in the pod.
- **Why it's a blind spot / honest disclosure:** removing `RLIMIT_AS` this session (required — it SIGTRAPs `claude`; see Appendix A) did **not** create this gap but made it visible. `RLIMIT_AS` was per-process and easily sidestepped by spawning many processes, so it never really isolated the terminal. The truth it exposes: there was never a resource boundary between "the user's toy shell" and "the paid agent runtime."
- **Impact:** tenant **self-DoS** — a tenant can kill their own agent sessions. Not cross-tenant (pods are per-account).
- **Fix:** Tier 1 — set `RLIMIT_NPROC` + a pids cgroup limit; optionally a child cgroup per PTY for memory. Note a real per-process memory cap on Linux effectively requires cgroups v2 (`RLIMIT_AS` is the wrong tool, `RLIMIT_RSS` is unenforced).

#### M3 — In-memory session registry assumes one replica forever
- **What:** `_active_sessions` in `pty_session.py` is a per-process dict. `max_sessions_per_user`, FIFO eviction, and admin "shutdown runner" (`kill_all_sessions`) all operate within one process.
- **Evidence:** each account is its own `ar-<account_id>` Deployment at `replicas: 1` (verified). The registry is module-global state.
- **Why it's a blind spot:** correct *today*, silently wrong on any scale-out. Set `replicas > 1` or add an HPA and session caps stop being enforced across replicas and the admin kill-switch only reaches whichever replica served the request. It "works in the demo."
- **Fix:** Tier 2 — only needed when ar scales past 1; would require shared state (redis) or sticky routing + per-replica caps.

#### M4 — The auth boundary rests on two assumptions that are both false
- **What:** the runner-token design (mint at EPP, inject as header, pod trusts the header) implicitly assumes (a) the pod is unreachable except through the edge, and (b) the signing secret is secret.
- **Evidence:** (a) is false — no NetworkPolicy (H2); (b) is false — the secret is in the shell env (H1). `deps.account_from_ws` trusts `x-priva-runner-token` off the handshake with no origin/path check beyond the JWT.
- **Why it's a blind spot:** each assumption looks locally reasonable; the failure is that **both** are quietly untrue at once, so there is currently no intact layer holding the boundary.
- **Fix:** H1 restores (b); H2 restores (a). Either alone materially helps; both = defense in depth.

### DEFER

#### D1 — Crash cores dump into the tenant's NFS volume
- **What:** `core_pattern = core` → cores land in cwd under `/workspace` (NFS), up to 100 MiB each (the `RLIMIT_FSIZE` cap), on a **974 MiB** per-account quota.
- **Evidence:** `cat /proc/sys/kernel/core_pattern` → `core`; `df -h /workspace` → `974M`; the earlier claude crash left a 100 MiB `/workspace/admin/core` (since removed).
- **Fix:** set `RLIMIT_CORE = 0` in the PTY child. Low blast radius, trivial fix.

#### D2 — Terminal-escape injection from untrusted content
- **What:** `allowProposedApi: true` + WebLinksAddon means content rendered in the terminal (e.g. `cat` of an attacker-controlled file) could emit OSC 52 (clipboard write), title-set, or hyperlink sequences into the viewer's browser.
- **Evidence:** `new Terminal({ allowProposedApi: true, … })` in `web/shared/components/terminal/TerminalSession.jsx`.
- **Fix:** review which OSC sequences xterm honors; disable clipboard-write if not needed. Low severity.

#### D3 — WebSocket send-side backpressure
- **What:** the token bucket throttles the PTY **read** side, but `websocket.send_json` has no explicit backpressure; a fast producer + slow client could grow memory.
- **Evidence:** `routers/pty.py` `on_output` → `safe_send`; read side throttled in `PtySession._read_loop`, send side unbounded.
- **Fix:** bounded in practice (finite output, capped by the 2 GiB cgroup). Defer; revisit if streaming large output.

#### D4 — Client/server model drift
- **What:** `web/shared/api/terminal.js` comments "the server caps users at 1 concurrent"; the server default is `max_sessions_per_user: 3`.
- **Fix:** doc-only, but a signal to check for other client/server divergence.

#### D5 — Unknown: does the Anthropic proxy meter by account? (downgraded, not a blocker)
- **What:** `ANTHROPIC_BASE_URL = http://host.minikube.internal:8000` — a host-side proxy, outside the repo. Since the per-account token is tenant-readable by design (H-note), the security of billing depends entirely on whether that proxy enforces per-account quota keyed on an identity the tenant can't forge.
- **Evidence:** base URL read from the live pod; the `:8000` proxy is not in the repo (host-side dev component).
- **Why DEFER not HIGH:** it's per-account (not a shared key), the tenant can read their own token regardless, and it's a billing/quota question, not cross-tenant compromise.
- **Action:** verify the proxy meters by account identity. If yes, token exposure is acceptable. If the app-runner is the only quota enforcer, direct proxy use is a per-account bypass to close — independent of the terminal.

---

## 3. Wrong hypotheses currently baked into the project

1. **"Container hardening makes the terminal safe."** (H3) It defends breakout, not insider reach.
2. **"The edge is the only path to the pod."** (H2/M4) No NetworkPolicy enforces this; it's reachable directly.
3. **"The runner token is the security boundary."** (M4) Its secrecy depends on a secret the terminal can read (H1), and its uniqueness depends on network isolation that doesn't exist (H2).
4. **"Per-account pods = per-tenant isolation."** Partly true for *data*, but the terminal and the agent runtime share netns/uid/cgroup/fs *within* the pod, so the terminal is not isolated from the runtime (M2, §4).
5. **"`RLIMIT_AS` bounds terminal memory."** (M2 / Appendix A) It's per-process, sidesteppable, and it SIGTRAPs `claude`. It bounded nothing useful.

---

## 4. The direction-changing decision

**Should the terminal run in the same pod as the agent-runner service?**

It currently shares **netns + uid + cgroup + filesystem** with the service. Consequences that no in-pod control can fully undo:
- **Network:** the terminal inherits the service's egress (H2) — a NetworkPolicy can't separate them.
- **Filesystem:** the terminal can read the agent's on-disk session state under `/workspace`.
- **Memory:** the terminal shares the agent's 2 GiB budget (M2).

Tier 0/1 make co-location *acceptable* via defense-in-depth (can't forge tokens, can't fork-bomb, narrowed egress). If **hard** isolation is required, the terminal belongs in its own netns/uid — a sidecar container or an ephemeral per-session pod. This is the fork to settle before building more on the current shape. **Everything in Tier 0/1 is compatible with either answer**, so it does not block starting remediation.

---

## 5. Remediation plan (tiered)

### Tier 0 — before any tenant touches the terminal (launch blockers)
1. **Scrub the shell env to an allowlist** *(decisive, do first)*. In `pty_session._fork_and_exec`, use `os.execvpe` with an explicit env (PATH, HOME, USER, TERM, LANG, SHELL, TZ, `CLAUDE_CONFIG_DIR`) instead of inheriting `os.environ`. Removes `PRIVA_AUTH__JWT_SECRET` + HMAC secret from tenant reach entirely (they exist only in env, nowhere on tenant disk). Kills the H1→chain. No impact on the service (keeps its own env) or on `claude` (reads `settings.json` per `user_env.py`) — **verify with an actually-authenticated `claude` run**, not just `--version`. ~10 lines, one file.
2. **NetworkPolicy: default-deny + narrow egress allowlist** (DNS, data-spine, the Anthropic proxy). Shrinks lateral reach. Caveat: cannot separate terminal from service while co-located (H2).
3. **`automountServiceAccountToken: false`** on the ar pod (operator `kube.py`). Drops the SA token (M1).

### Tier 1 — hardening, soon after
4. **Fork/resource guard** — add `RLIMIT_NPROC` + a pids cgroup limit so a terminal fork/memory bomb can't evict the agent runtime (M2).
5. **Confirm data-spine authorizes per-account on every read** — so "reachable" ≠ "readable" (backstop to H2).
6. **`RLIMIT_CORE = 0`** — stop cores dumping into the tenant's NFS volume (D1).

### Tier 2 — verify / defer
7. Verify the `:8000` proxy meters by account (D5).
8. Multi-replica session registry (M3) — only when ar scales past 1.
9. Terminal-escape hardening (D2); client/server doc drift (D4).

---

## 6. Tech-lead questions to put to the team

1. **Is the terminal tenant-facing or operator-only?** *(Answered: tenant-facing — hence the launch-blocker framing.)*
2. **Why does the agent runtime hold raw platform secrets in env at all?** Env leaks to every child and into core dumps (observed). Even ignoring the terminal, a mount or broker is the right home.
3. **Where is billing enforced?** The per-account Anthropic token is tenant-readable by design, so metering must live at the `:8000` proxy keyed on an unforgeable identity — is it? (D5)
4. **Does data-spine enforce per-account authz on every read, or trust any valid runner token?** Determines the blast radius of reachability.
5. **Is co-locating terminal + agent runtime in one pod acceptable?** (§4)

---

## Appendix A — Already resolved this session

These were found and fixed while investigating the terminal; recorded so they aren't re-discovered.

| Issue | Root cause | Fix |
|---|---|---|
| `claude` "Trace/breakpoint trap (core dumped)" in the terminal | PTY applied `RLIMIT_AS = 2 GiB`; the `claude` bun/JSC binary reserves >3 GiB of (mostly `PROT_NONE`) virtual address space at startup → SIGTRAP | `PtySettings.rlimit_as_bytes` default → `0` = don't apply; skip-if-zero guard in `_fork_and_exec`; admin API accepts `0`. Removed dead `_build_preexec`. |
| Prompt `❯` rendered as `)`; whole UI in fallback font | xterm built its glyph atlas before webfonts loaded | `await document.fonts.load(...)` for the three terminal faces before `new Terminal()` in `TerminalSession.jsx` |
| Duplicated rows / stray line fragments in claude's UI | `routers/pty.py` decoded each PTY chunk with `bytes.decode()`; a multi-byte glyph split across chunks became `�`, widening rows and desyncing repaints | Stateful `codecs.getincrementaldecoder("utf-8")` across chunks |
| Shift+Enter submitted instead of inserting a newline | xterm doesn't speak the kitty keyboard protocol → Shift+Enter reached the pty as bare `\r` | `attachCustomKeyEventHandler` sends `ESC`+`\r` (the `/terminal-setup` sequence) |

## Appendix B — Reproduce the evidence

Run against the account's ar pod (`POD=$(kubectl get pods -n priva-cloud -o name | grep pod/ar- | head -1 | cut -d/ -f2)`), sandbox disabled for `kubectl exec`.

```bash
# H1 — secrets in the shell env
kubectl exec -n priva-cloud "$POD" -- bash -lc 'env | grep -iE "token|key|secret"'
kubectl get secret -n priva-cloud priva-shared-secret -o json | python3 -c 'import sys,json;print("\n".join(json.load(sys.stdin)["data"]))'
# runner_token._secret() = dataspine hmac || auth jwt_secret  (libs/common/.../runner_token.py)

# H2 — no NetworkPolicy, everything reachable
kubectl get networkpolicy -n priva-cloud
kubectl exec -n priva-cloud "$POD" -- bash -lc 'for hp in redis:6379 data-spine:50051 control-panel:8080 priva-gateway:8080 kubernetes.default:443; do timeout 2 bash -c "echo > /dev/tcp/${hp%:*}/${hp#*:}" 2>/dev/null && echo "REACHABLE $hp"; done'

# M1 — SA token + RBAC
kubectl exec -n priva-cloud "$POD" -- test -f /var/run/secrets/kubernetes.io/serviceaccount/token && echo present
kubectl auth can-i --list -n priva-cloud --as=system:serviceaccount:priva-cloud:default

# M2 — fork/memory guards
kubectl exec -n priva-cloud "$POD" -- cat /sys/fs/cgroup/pids.max     # -> max

# D1 — where cores land
kubectl exec -n priva-cloud "$POD" -- bash -lc 'cat /proc/sys/kernel/core_pattern; df -h /workspace | tail -1'

# env sources (envFrom)
kubectl get pod -n priva-cloud "$POD" -o jsonpath='{.spec.containers[0].envFrom}'
```

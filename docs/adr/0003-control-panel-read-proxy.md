# Large sandbox reads ride a generic control-panel proxy lane, not the GIE/EPP InferencePool

agentgateway v1.3.0's GIE InferencePool routes every response body through the control-panel EndpointPicker (EPP) ext_proc, and `InferenceRouting::build()` hardcodes `response_body_mode=FullDuplexStreamed` with `allow_mode_override=false`. This is not a tunable buffer and there is no upstream fix (byte-identical on `main` and `v1.3.1`); the EPP's own `mode_override=NONE` is ignored, so the EPP cannot recover the bytes. Effect: any `/api/sandbox` GET over ~8KB is cut mid-body and breaks client `JSON.parse` ("Unterminated string") — session transcripts (35–300KB), file previews (≤1MB), large list/JSON bodies. The `/` catch-all lane to `control-panel:8080` carries **no** ext_proc and is already proven for `/sandbox/apidocs` and the one-off `/api/session-history/{id}/messages` proxy.

So we build **one** generic control-panel reverse-proxy lane, `GET /api/cp-proxy/{sandbox_path:path}`, riding the `/` catch-all. A shared helper `_proxy_runner_get` re-does the EPP's per-account steering: authenticate the bearer token (else 401/403), `wake_and_wait` the pod (else 503), mint a per-account `X-Priva-Runner-Token`, then `httpx` `trust_env=False` GET the caller's pod at `/api/sandbox/{path}` for the full body (upstream error → 502). It is GET-only, rejects `..` (400), and the per-account token means a caller reaches only its **own** pod. Since `/api/cp-proxy` is not under `/api/sandbox`, Gateway-API most-specific-prefix routing sends it to the catch-all automatically — **no `deploy/gateway` / HTTPRoute change**. The old `/api/session-history/{id}/messages` becomes a thin back-compat alias of the helper; `/sandbox/apidocs` is its account-independent sibling. The frontend `sandboxRead(path)` (`web/shared/api/client.js`) tries `/api/cp-proxy`, then falls back to the direct `/api/sandbox` lane on 404/network error — a drop-in swap for `sandboxGet`.

## Considered Options

- **An agentgateway `AgentgatewayPolicy`/EPP experiment** to disable response-body buffering. Rejected — likely a dead end: the InferencePool EPP mode is hardcoded, and the policy ext_proc is a separate filter.
- **Forking agentgateway** to patch the two `FullDuplexStreamed` lines. Rejected — there is no upstream patch to cherry-pick, and it is high-maintenance.
- **The control-panel catch-all proxy (chosen).** Already proven in production for `/sandbox/apidocs`, and it needs no gateway change.

## Rollout

Incremental, covering only >8KB-risk reads.

- **Step 1 (shipped)** — rerouted `web/user/src/api/sessions.js` only (grouped/cwd/archived session lists + transcripts). Shipped via a control-panel image rebuild, since the backend is not hotloadable.
- **Step 2 (pending; frontend-only, hotloadable)** — reroute the remaining large reads (files, hooks, mcp, skills, subagents, settings models, user data).
- **Never rerouted** — writes, streams/SSE/WS, blob downloads, `/api/auth/audit`, the `/health` poll, and tiny configs.

## Consequences

Large reads return full bodies — verified live: a 142KB transcript gave 8063 truncated bytes via the pool lane versus the full 142937 valid-JSON bytes via `/api/cp-proxy`. The cost is one extra in-cluster hop (control-panel → pod) and double request handling on large reads. Failure is graceful: 404/network error falls back to the direct lane. Streams are unaffected (the EPP is consulted only at WS setup, never on the byte path), and there is no gateway config to maintain. If agentgateway ever fixes the EPP body mode, this lane can be retired.

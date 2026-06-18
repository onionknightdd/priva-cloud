# services/

One subdirectory per deployable. Each is a `uv` workspace member with its own
`pyproject.toml` (depending on `priva-common`) and Dockerfile.

| Service | Role | Status |
|---|---|---|
| `agent-pod/` | per-tenant agent runtime (run/stream/permission/fork/rewind) | planned (Phase 2) |
| `control-panel/` | brain (ext_proc) + admin API + faces; one process, 3 listeners | planned (Phase 3) |
| `channel-connector/` | WeCom/OpenClaw socket + lease + IM fan-out | planned (Phase 4) |
| `scheduler/` | leaderless fire → claim → wake → dispatch | planned (Phase 4) |
| `operator/` | `AgentTenant` CRD controller (kopf), sole scaler | planned (Phase 5) |
| `data-spine/` | Accounts/Identities/sessions/jobs RPC + Redis catalog | planned (Phase 1) |
| `state-reader/` | read-only JSONL transcript reader | planned (Phase 5) |

See `docs/architecture/code-split.md` for the per-file map and the extraction sequence.

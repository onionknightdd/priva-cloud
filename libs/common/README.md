# priva-common

The one package every Priva Cloud service depends on (a `uv` workspace path-dep).

Contents (see `docs/architecture/code-split.md` §6):

- **Wire format** — `serialization` (pod serializes; connector fans out; agentgateway streams the bytes).
- **Models** — the shared pydantic DTOs that cross the wire.
- **Data-plane client** — gRPC stubs generated from `protos/`.
- **Redis catalog** — the T1/T2 key definitions + helpers (one source of truth).
- **Auth/crypto** — JWT verify + signing helpers; envelope crypto.
- **Settings** — the `get_settings()` loader.
- **Observability** — logging middleware + the `/metrics` helper.

**Rule:** `priva_common` may not import any service package.

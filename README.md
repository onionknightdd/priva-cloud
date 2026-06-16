# Priva

**Your private, self‑hosted personal AI assistant.**

Priva is a self‑hostable personal assistant platform built on the
[Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
It pairs a FastAPI backend with a React web console so you — or your whole
team — can run a capable agent that uses skills, MCP servers, scheduled jobs,
and chat channels, all on infrastructure you control.

> Status: early. Priva runs from source today; a one‑command install is on the
> roadmap (the `priva` package name is reserved on PyPI).

---

## Features

- **Web console** (React + Vite) — chat with your agent and watch task progress live.
- **Agent runtime** on the Claude Agent SDK — tool use, streaming, multi‑turn sessions.
- **Skills system** — bundled office skills (`docx`, `xlsx`, `pptx`, `pdf`),
  diagram tools (`mermaid-visualizer`, `excalidraw-diagram`), an `mcp-server-creator`
  and a `skill-creator`, plus a central Skill Hub (upload → review → distribute).
- **MCP support** — connect Model Context Protocol servers as tools.
- **IM channels** — talk to your assistant from messaging apps (WeCom included).
- **Scheduler** — recurring / cron‑style agent jobs (APScheduler).
- **Hooks & audit log** — observe and gate what the agent does.
- **Multi‑user** — JWT auth, per‑user workspaces, admin roles.
- **Server‑side plugins** — inject context per run (e.g. `enterprise_user_info`).

## Architecture

```
priva/
├── api/        FastAPI backend (ASGI app: api.main:app)
│   ├── routers/        HTTP + SSE/WebSocket endpoints
│   ├── services/       agent runtime, skills, MCP, scheduler, auth, channels, plugins
│   ├── bundled/skills/ skills seeded on first boot
│   └── config.example.yaml
├── web/        React + Vite console (package: priva-web)
└── bin/        server.sh launcher (server / scheduler / channels)
```

## Quickstart

**Prerequisites:** Python ≥ 3.12, Node ≥ 18 (for the web console), and an
Anthropic API key (or a compatible gateway).

### 1. Backend

```bash
# from the repo root
pip install -r requirements.txt

# configure (optional — sensible defaults are built in)
cp priva/api/config.example.yaml priva/api/config.yaml
#   then edit config.yaml: set a strong auth.jwt_secret before any network use

# provide your model credentials (used by the Claude Agent SDK)
export ANTHROPIC_API_KEY=sk-ant-...

# launch (server + scheduler + channels)
priva/bin/server.sh
```

The API listens on `http://localhost:8001` by default (override via
`server.port` in `config.yaml`).

### 2. Web console

```bash
cd priva/web
npm install
npm run dev        # development
# or: npm run build   # production bundle into priva/web/dist
```

## Configuration

- `priva/api/config.yaml` — your local config (git‑ignored). Start from
  `config.example.yaml`. Any omitted key falls back to a built‑in default
  (`priva/api/services/config.py`).
- Useful environment variables: `ANTHROPIC_API_KEY`, `PRIVA_HOME`
  (state directory, default `~/.config`), and `NO_PROXY` (gateway egress).

## Packaging

`./pack.sh` builds a self‑contained release tarball (optionally bundling
dependency wheels for a target Python version). See `pack.sh --help`.

## License

[MIT](./LICENSE).

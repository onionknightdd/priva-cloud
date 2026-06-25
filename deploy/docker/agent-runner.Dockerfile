# agent-runner — per-account runtime pod (:8091). Bundles the `claude` CLI the
# Claude Agent SDK spawns (min v2.0.0).
#
# NOTE: the native installer (`curl https://claude.ai/install.sh`) downloads from
# downloads.claude.ai, which is unreachable on this network (TLS reset). We instead
# install the CLI from the npm registry in a Node stage and copy the Node runtime +
# package into the runtime image. The SDK only needs `claude` on PATH.
FROM node:22-slim AS claudecli
RUN npm install -g @anthropic-ai/claude-code && claude --version

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
# Bring the Node runtime + the npm-installed `claude` CLI in from the claudecli stage.
# /usr/local/bin/claude is a relative symlink into node_modules, preserved by the copy.
COPY --from=claudecli /usr/local/bin /usr/local/bin
COPY --from=claudecli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN /usr/local/bin/claude --version
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/agent-runner
# Non-root sandbox identity. The pod runs as uid 10001 (operator securityContext) which
# owns /export/<account_id>; whoami resolves to `sandbox` via this /etc/passwd entry.
# No more IS_SANDBOX root hack — the claude CLI accepts --dangerously-skip-permissions
# as non-root. HOME is set to a writable volume path at runtime (operator env).
RUN groupadd -g 10001 sandbox && useradd -u 10001 -g 10001 -m -d /home/sandbox -s /bin/bash sandbox
USER 10001:10001
EXPOSE 8091
CMD ["python", "-m", "priva_agent_runner"]

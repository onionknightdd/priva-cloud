# agent-runner — per-account runtime pod (:8091). Bundles the `claude` CLI the
# Claude Agent SDK spawns (min v2.0.0) via the native installer (single binary,
# no Node toolchain).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
# The SDK resolves `claude` via shutil.which; install the native CLI and put it on PATH.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp "$HOME/.local/bin/claude" /usr/local/bin/claude \
    && /usr/local/bin/claude --version
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/agent-runner
EXPOSE 8091
CMD ["python", "-m", "priva_agent_runner"]

# agent-runner — per-account runtime pod (:8091).
#
# The `claude` CLI ships INSIDE the claude-agent-sdk wheel since v0.1.8: the platform
# wheels (manylinux_2_17_{x86_64,aarch64}, macosx, win) carry a native binary at
# claude_agent_sdk/_bundled/claude, and the SDK's subprocess transport prefers that
# bundled binary over anything on PATH. So no Node stage / npm install — `uv pip
# install` below brings the CLI in for whatever --platform this image is built for.
# The /usr/local/bin/claude symlink only serves web-terminal (PTY) users and keeps
# them on the exact same binary the SDK spawns.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/agent-runner
# Expose the SDK-bundled CLI on PATH and sanity-check it actually execs.
RUN CLI="$(python -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" \
    && chmod +x "$CLI" \
    && ln -sf "$CLI" /usr/local/bin/claude \
    && claude --version
# Non-root sandbox identity. The pod runs as uid 10001 (operator securityContext) which
# owns /export/<account_id>; whoami resolves to `sandbox` via this /etc/passwd entry.
# No more IS_SANDBOX root hack — the claude CLI accepts --dangerously-skip-permissions
# as non-root. HOME is set to a writable volume path at runtime (operator env).
RUN groupadd -g 10001 sandbox && useradd -u 10001 -g 10001 -m -d /home/sandbox -s /bin/bash sandbox
USER 10001:10001
EXPOSE 8091
CMD ["python", "-m", "priva_agent_runner"]

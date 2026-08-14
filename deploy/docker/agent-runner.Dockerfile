# One immutable image serves both per-account runtimes:
#   * agent-runner API (:8091)
#   * independent terminald (:8092, selected by the Terminal Deployment command)
#
# The `claude` CLI ships INSIDE the claude-agent-sdk wheel since v0.1.8: the platform
# wheels (manylinux_2_17_{x86_64,aarch64}, macosx, win) carry a native binary at
# claude_agent_sdk/_bundled/claude, and the SDK's subprocess transport prefers that
# bundled binary over anything on PATH. So no Node stage / npm install — `uv pip
# install` below brings the CLI in for whatever --platform this image is built for.
# The /usr/local/bin/claude symlink only serves web-terminal (PTY) users and keeps
# them on the exact same binary the SDK spawns.
FROM golang:1.24-bookworm AS terminal-builder
WORKDIR /src
COPY services/terminald/ ./
RUN go mod download \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/priva-terminald .

# Keep the Python 3.12 runtime below while sourcing a maintained Node LTS binary and
# its package-manager shims from the official image. Both stages use Debian bookworm,
# so the copied Node binary has the same libc baseline as the final image.
FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dnsutils \
        git \
        iproute2 \
        iputils-ping \
        jq \
        less \
        openssh-client \
        procps \
        tar \
        unzip \
        util-linux \
        wget \
        xz-utils \
        zip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && node --version \
    && npm --version \
    && npx --version
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/agent-runner
# Expose the SDK-bundled CLI on PATH and sanity-check it actually execs.
RUN CLI="$(python -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" \
    && chmod +x "$CLI" \
    && ln -sf "$CLI" /usr/local/bin/claude \
    && claude --version
COPY --from=terminal-builder /out/priva-terminald /usr/local/bin/priva-terminald
# Non-root app:sandbox identity. Both Runner and Terminal pods run as uid/gid 10001,
# which owns /export/<account_id>; terminald also gives shells this fixed identity.
# No more IS_SANDBOX root hack — the claude CLI accepts --dangerously-skip-permissions
# as non-root. HOME is set to a writable volume path at runtime (operator env).
RUN groupadd -g 10001 sandbox && useradd -u 10001 -g sandbox -m -d /home/app -s /bin/bash app
USER app:sandbox
EXPOSE 8091 8092
CMD ["python", "-m", "priva_agent_runner"]

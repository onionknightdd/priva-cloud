# channel-connector — always-on Feishu WS byte-path (thread-per-app), internal API :8083.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
# uv resolves the workspace path-deps (priva-common = {workspace=true}) from /app/pyproject.toml.
# Pulls lark-oapi (Feishu/Lark WS + IM REST) from the index.
RUN uv pip install --system -e libs/common -e services/channel-connector
EXPOSE 8083
CMD ["python", "-m", "priva_channel_connector"]

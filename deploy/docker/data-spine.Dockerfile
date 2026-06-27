# data-spine — single-writer state + secret store, served over gRPC (:50051).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
# uv resolves the workspace path-deps (priva-common = {workspace=true}) from /app/pyproject.toml.
RUN uv pip install --system -e libs/common -e services/data-spine
EXPOSE 50051
CMD ["python", "-m", "priva_data_spine", "serve"]

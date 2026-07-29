# scheduler — leaderless firing engine (claim → wake → dial), internal API :8082.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
# uv resolves the workspace path-deps (priva-common = {workspace=true}) from /app/pyproject.toml.
RUN uv pip install --system -e libs/common -e services/scheduler
EXPOSE 8082
# Drop privileges. These images ran as root, so any RCE in an internet-facing
# process started as uid 0 inside the pod. uid 10001 matches the runner's sandbox
# uid and the fsGroup set on the PodSpec.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/app app
ENV HOME=/home/app
USER 10001:10001

CMD ["python", "-m", "priva_scheduler"]

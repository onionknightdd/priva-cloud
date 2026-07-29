# control-panel — brain/EPP (:9000 ext_proc) + HTTP faces + SPAs (:8080).
# Serves the built SPAs from web/{user,admin}/dist (built on the host before docker build;
# app.py also auto-discovers them via the repo-root probe, env vars make it explicit).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
ENV PRIVA_WEB_DIST=/app/web/user/dist PRIVA_WEB_DIST_ADMIN=/app/web/admin/dist
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/control-panel
EXPOSE 8080 9000
# Drop privileges. These images ran as root, so any RCE in an internet-facing
# process started as uid 0 inside the pod. uid 10001 matches the runner's sandbox
# uid and the fsGroup set on the PodSpec.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/app app
ENV HOME=/home/app
USER 10001:10001

CMD ["python", "-m", "priva_control_panel"]

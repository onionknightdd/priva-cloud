# control-panel — brain/EPP (:9000 ext_proc) + HTTP faces + SPAs (:8080).
# Serves the built SPAs from web/{user,admin}/dist (built on the host before docker build;
# app.py also auto-discovers them via the repo-root probe, env vars make it explicit).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ENV PRIVA_WEB_DIST=/app/web/user/dist PRIVA_WEB_DIST_ADMIN=/app/web/admin/dist
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/control-panel
EXPOSE 8080 9000
CMD ["python", "-m", "priva_control_panel"]

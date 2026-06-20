# control-panel — brain/EPP (:9000 ext_proc) + HTTP faces + SPAs (:8080).
# Includes the built SPAs at /app/priva/web/{dist,dist-admin} (served by app.py).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . /app
RUN uv pip install --system -e libs/common -e services/data-spine -e services/control-panel
EXPOSE 8080 9000
CMD ["python", "-m", "priva_control_panel"]

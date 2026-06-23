from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.resource import (
    ModelInfo,
    ModelListResponse,
)
from ..services.auth import require_user
from ..services.secret_env import read_user_env
from priva_common.user_store import UserRecord

logger = get_app_logger(__name__)

# NOTE: per-user config (quickactions, vision-model) is NOT served here. It lives
# in the user's ``.priva.user.yml`` under the agent's workspace, which on the
# agent-runner is the per-account PVC (/workspace/<user>) — the same file the
# agent reads. The control-panel mounts no such volume (its work_dir is the
# pod-local /tmp/cp-workspace), so serving that config here would write to an
# ephemeral dir the agent never sees. Those routes now live on the agent-runner
# (routers/user_config.py) and the gateway steers /api/resource/quickactions and
# /api/resource/vision-model to the per-account pod. Only /models — a pure proxy
# to the user's upstream API (no workspace I/O) — stays on the control-panel.
router = APIRouter(prefix="/api/resource", tags=["resource"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(user: UserRecord = Depends(require_user)):
    env = read_user_env(user.username)
    if env is None:
        raise HTTPException(400, "API credentials not configured")

    base_url = env.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN", "")

    if not base_url or not auth_token:
        raise HTTPException(400, "API credentials not configured. Please set base URL and auth token.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/v1/models",
                headers={"Authorization": f"Bearer {auth_token}"},
            )
    except httpx.ConnectError as e:
        raise HTTPException(502, f"Cannot connect to API: {e}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"API request timed out: {e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"API request failed: {e}") from e

    if resp.status_code == 401:
        raise HTTPException(400, "Invalid auth token — upstream returned 401")
    if resp.status_code != 200:
        raise HTTPException(502, f"Upstream API returned {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(502, "Invalid JSON response from upstream API")

    # Handle both OpenAI-style {"data": [...]} and flat list responses
    model_list = data.get("data") if isinstance(data, dict) else data
    if not isinstance(model_list, list):
        model_list = []

    models = []
    for m in model_list:
        if isinstance(m, dict) and "id" in m:
            models.append(ModelInfo(id=m["id"]))
        elif isinstance(m, str):
            models.append(ModelInfo(id=m))

    return ModelListResponse(models=models)

"""Per-account BYOK credentials — owned by the agent-runner, persisted in the
claude CLI's native ``$CLAUDE_CONFIG_DIR/settings.json`` ``env`` block.

This is the single home for the 6 ``ANTHROPIC_*`` cred keys. The browser writes
here through agentgateway (``/api/sandbox/credentials`` → EPP wake+route → this
pod); control-panel/admin write the SAME endpoint on the target account's pod.
The claude CLI reads the ``env`` block itself on every run, so a change is honored
on the next run with NO re-wake — no process-env injection, no data-spine, no
wake-time K8s Secret (the old plumbing that caused cred staleness).

Mirrors the surface of the retired control-panel ``/auth/me/env`` + ``/resource/
models`` endpoints, now served pod-side where the creds actually live and where
the pod can reach the account's (LAN) ``base_url`` for the connection test.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.models.auth import UserRecord
from priva_common.models.resource import ModelInfo, ModelListResponse
from priva_common.models.user_env import (
    UserEnvResponse,
    UserEnvSettings,
    UserEnvUpdateRequest,
)
from priva_common.user_env import (
    has_settings_env,
    read_settings_env,
    write_settings_env,
)
from ..deps import require_user

router = APIRouter(prefix="/api/sandbox/credentials", tags=["credentials"])


@router.get("", response_model=UserEnvResponse)
async def get_credentials(user: UserRecord = Depends(require_user)):
    """Return the account's ``env`` block (the BYOK creds) from settings.json.

    The token is returned unmasked: the Settings panel prefills the auth-token
    input from this response and re-sends it on the next save, so a masked value
    would round-trip back and clobber the real token."""
    env = read_settings_env()
    if not env:
        return UserEnvResponse(has_env=False)
    return UserEnvResponse(has_env=has_settings_env(), env=UserEnvSettings(**env))


@router.put("", response_model=UserEnvResponse)
async def update_credentials(
    request: UserEnvUpdateRequest,
    user: UserRecord = Depends(require_user),
):
    """Merge-write the provided cred keys (partial allowed) into the settings.json
    ``env`` block. Preserves ``hooks``/``mcpServers``/etc; written atomically."""
    creds = request.model_dump(exclude_none=True)
    if not creds:
        raise HTTPException(400, "No credential fields provided")

    write_settings_env(creds)

    try:
        get_audit_logger().append(AuditEntry(
            actor=user.username,
            action="credentials.updated",
            target=user.username,
            details={"keys": sorted(creds.keys())},
        ))
    except Exception:  # pragma: no cover - audit must never block a cred write
        pass

    env = read_settings_env()
    return UserEnvResponse(
        has_env=has_settings_env(),
        env=UserEnvSettings(**env) if env else None,
    )


@router.get("/status")
async def get_credentials_status(user: UserRecord = Depends(require_user)):
    """Lightweight presence check (base_url + auth_token set) — no values."""
    return {"has_env": has_settings_env()}


@router.get("/models", response_model=ModelListResponse)
async def list_models(user: UserRecord = Depends(require_user)):
    """Connection test: proxy ``{base_url}/v1/models`` with the account's creds.

    Runs pod-side so the per-account base_url (often a LAN endpoint the pod can
    reach but the control-panel can't) is the one being tested."""
    return ModelListResponse(models=await load_model_list())


async def load_model_list(timeout: float = 15.0) -> list[ModelInfo]:
    """Fetch upstream model ids using the account's saved credentials."""
    env = read_settings_env()
    if not env:
        raise HTTPException(400, "API credentials not configured")

    base_url = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    if not base_url or not auth_token:
        raise HTTPException(400, "API credentials not configured. Please set base URL and auth token.")

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
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

    # Handle both OpenAI-style {"data": [...]} and flat list responses.
    model_list = data.get("data") if isinstance(data, dict) else data
    if not isinstance(model_list, list):
        model_list = []

    models = []
    for m in model_list:
        if isinstance(m, dict) and "id" in m:
            models.append(ModelInfo(id=m["id"]))
        elif isinstance(m, str):
            models.append(ModelInfo(id=m))

    return models

"""User-owned LLM profile CRUD and provider model discovery."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.models.auth import UserRecord
from priva_common.models.llm_profiles import (
    ImageCapabilityProbeRequest,
    ImageCapabilityProbeResponse,
    LlmProfile,
    LlmProfileCreateRequest,
    LlmProfileDefaultResponse,
    LlmProfileSummary,
    LlmProfileUpdateRequest,
    LlmProfilesResponse,
)
from priva_common.models.resource import ModelInfo, ModelListResponse
from priva_common.skill_exclude import get_user_yaml_key, save_user_yaml_key

from ..deps import require_user
from ..services.llm_profiles import (
    profile_summary,
    profile_store_path,
    store,
    validate_endpoint,
    validate_profile_id,
)
from ..services.vision import ImageProbeUnavailable, probe_image_capability

router = APIRouter(prefix="/api/sandbox/credentials/profiles", tags=["llm-profiles"])


def _migrate_legacy_vision() -> str | None:
    value = get_user_yaml_key("vision_model")
    return value if isinstance(value, str) and value else None


def _ensure():
    vision = _migrate_legacy_vision()
    was_present = profile_store_path().exists()
    profiles, default_id = store.read(vision)
    # Once the canonical app-config store exists, the old per-user vision key
    # is no longer read.  Remove it only after a successful store read/write.
    if vision and profiles and not was_present:
        save_user_yaml_key("vision_model", None)
    return profiles, default_id


def _as_summary(profile: LlmProfile) -> LlmProfileSummary:
    return LlmProfileSummary.model_validate(profile_summary(profile))


async def _fetch_models(profile: LlmProfile, timeout: float = 15.0) -> list[ModelInfo]:
    base_url = profile.base_url.rstrip("/")
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    urls: list[str] = []

    def add_url(url: str) -> None:
        if url not in urls:
            urls.append(url)

    # Keep the existing OpenAI-compatible endpoint first, then support
    # providers whose base URL already includes /v1 or an Anthropic route.
    if path.endswith("/v1"):
        add_url(f"{base_url}/models")
    else:
        add_url(f"{base_url}/v1/models")
        add_url(f"{base_url}/models")
    if parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        add_url(f"{origin}/v1/models")
        add_url(f"{origin}/models")

    headers = {
        "Authorization": f"Bearer {profile.auth_token}",
        # Anthropic-compatible providers such as DeepSeek use x-api-key.
        "x-api-key": profile.auth_token,
        "anthropic-version": "2023-06-01",
    }
    last_response = None
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            for url in urls:
                response = await client.get(url, headers=headers)
                last_response = response
                if response.status_code == 200:
                    break
                if response.status_code not in {401, 404, 405}:
                    break
    except httpx.ConnectError as exc:
        raise HTTPException(502, f"Cannot connect to API: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"API request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"API request failed: {exc}") from exc

    response = last_response
    if response is None:
        raise HTTPException(502, "No model discovery endpoint configured")
    if response.status_code == 401:
        raise HTTPException(400, "Invalid auth token — upstream returned 401")
    if response.status_code != 200:
        raise HTTPException(502, f"Upstream API returned {response.status_code}: {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Invalid JSON response from upstream API") from exc
    values = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        values = []
    return [
        ModelInfo(id=item["id"])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        else ModelInfo(id=item)
        for item in values
        if (isinstance(item, dict) and isinstance(item.get("id"), str)) or isinstance(item, str)
    ]


@router.get("", response_model=LlmProfilesResponse)
async def list_profiles(user: UserRecord = Depends(require_user)):
    profiles, default_id = _ensure()
    return LlmProfilesResponse(
        profiles=[_as_summary(profile) for profile in profiles],
        default_profile_id=default_id,
    )


@router.post("", response_model=LlmProfileSummary, status_code=201)
async def create_profile(
    request: LlmProfileCreateRequest,
    user: UserRecord = Depends(require_user),
):
    profile = LlmProfile(
        id=validate_profile_id(request.id),
        label=request.label.strip(),
        base_url=validate_endpoint(request.base_url),
        auth_token=request.auth_token.strip(),
        default_model=request.default_model or None,
        opus_model=request.opus_model or None,
        sonnet_model=request.sonnet_model or None,
        haiku_model=request.haiku_model or None,
        vision_model=request.vision_model or None,
    )
    if not profile.auth_token:
        raise HTTPException(422, "auth_token is required")
    store.upsert(profile, vision_model=_migrate_legacy_vision())
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="llm_profile.created", target=profile.id,
    ))
    return _as_summary(profile)


@router.get("/{profile_id}", response_model=LlmProfile)
async def get_profile(profile_id: str, user: UserRecord = Depends(require_user)):
    return store.get(validate_profile_id(profile_id), _migrate_legacy_vision())


@router.patch("/{profile_id}", response_model=LlmProfileSummary)
async def update_profile(
    profile_id: str,
    request: LlmProfileUpdateRequest,
    user: UserRecord = Depends(require_user),
):
    profile_id = validate_profile_id(profile_id)
    current = store.get(profile_id, _migrate_legacy_vision())
    values = current.model_dump()
    for key, value in request.model_dump(exclude_unset=True).items():
        if key == "auth_token" and value == "":
            raise HTTPException(422, "auth_token cannot be empty")
        if value is not None or key == "auth_token":
            values[key] = value.strip() if isinstance(value, str) else value
    if "base_url" in values:
        values["base_url"] = validate_endpoint(values["base_url"])
    values["id"] = profile_id
    updated = LlmProfile.model_validate(values)
    store.upsert(
        updated,
        replacing_id=profile_id,
        vision_model=_migrate_legacy_vision(),
    )
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="llm_profile.updated", target=profile_id,
        details={"fields": sorted(request.model_dump(exclude_unset=True).keys())},
    ))
    return _as_summary(updated)


@router.put("/{profile_id}/default", response_model=LlmProfileDefaultResponse)
async def set_default_profile(profile_id: str, user: UserRecord = Depends(require_user)):
    profile_id = validate_profile_id(profile_id)
    profile = store.get(profile_id, _migrate_legacy_vision())
    if not profile.base_url or not profile.auth_token or not profile.default_model:
        raise HTTPException(409, "profile_not_ready")
    store.set_default(profile_id, _migrate_legacy_vision())
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="llm_profile.default_changed", target=profile_id,
    ))
    return LlmProfileDefaultResponse(default_profile_id=profile_id)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(profile_id: str, user: UserRecord = Depends(require_user)):
    profile_id = validate_profile_id(profile_id)
    store.delete(profile_id, _migrate_legacy_vision())
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="llm_profile.deleted", target=profile_id,
    ))


@router.get("/{profile_id}/models", response_model=ModelListResponse)
async def list_profile_models(profile_id: str, user: UserRecord = Depends(require_user)):
    profile = store.get(validate_profile_id(profile_id), _migrate_legacy_vision())
    return ModelListResponse(models=await _fetch_models(profile))


@router.post(
    "/{profile_id}/image-capability/probe",
    response_model=ImageCapabilityProbeResponse,
)
async def probe_profile_image_capability(
    profile_id: str,
    request: ImageCapabilityProbeRequest,
    user: UserRecord = Depends(require_user),
):
    """Force a fresh native-image probe and replace that model's cache fact."""
    profile_id = validate_profile_id(profile_id)
    profile = store.get(profile_id, _migrate_legacy_vision())
    model_id = request.model_id.strip()
    try:
        supported, _ = await probe_image_capability(profile, model_id, force=True)
    except ImageProbeUnavailable as exc:
        raise HTTPException(502, "model_unavailable") from exc
    # A manual force-probe is also the explicit recovery path for a previously
    # cached Vision transport decision. The next image_read call rediscovers
    # chat-completions -> images/edits without changing the native image fact.
    store.update_model_capability(
        profile_id,
        model_id,
        image_read_transport=None,
    )
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="llm_profile.image_capability_probed",
        target=profile_id,
        details={"model_id": model_id, "image": supported},
    ))
    return ImageCapabilityProbeResponse(
        profile_id=profile_id,
        model_id=model_id,
        image=supported,
        cached=False,
    )


@router.post("/test", response_model=ModelListResponse)
async def test_profile_draft(
    request: LlmProfileCreateRequest,
    user: UserRecord = Depends(require_user),
):
    """Test the values currently being edited without persisting them."""
    profile = LlmProfile(
        id=validate_profile_id(request.id),
        label=request.label.strip(),
        base_url=validate_endpoint(request.base_url),
        auth_token=request.auth_token.strip(),
        default_model=request.default_model or None,
        opus_model=request.opus_model or None,
        sonnet_model=request.sonnet_model or None,
        haiku_model=request.haiku_model or None,
        vision_model=request.vision_model or None,
    )
    if not profile.auth_token:
        raise HTTPException(422, "auth_token is required")
    return ModelListResponse(models=await _fetch_models(profile))


@router.post("/{profile_id}/test", response_model=ModelListResponse)
async def test_profile(profile_id: str, user: UserRecord = Depends(require_user)):
    profile = store.get(validate_profile_id(profile_id), _migrate_legacy_vision())
    return ModelListResponse(models=await _fetch_models(profile))


async def load_model_list(profile: LlmProfile | None = None, timeout: float = 15.0) -> list[ModelInfo]:
    """Used by overview/bootstrap and tests; always targets the default profile."""
    if profile is None:
        profile = store.default()
    return await _fetch_models(profile, timeout)

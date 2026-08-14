"""Image capability routing and OpenAI-compatible Vision model calls."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.agent import ImageRouteResponse
from priva_common.models.llm_profiles import LlmProfile

from .llm_profiles import profile_store_path, resolve_model, store

logger = get_app_logger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
_PROBE_IMAGE_BASE64 = (
    # 32x32 flat PNG: still only 97 bytes, but large enough for providers that
    # reject 1x1 images before model capability validation.
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKElEQVR42u3NQQEAAAQEMBS/6krw2wqsk9SnqWcCgUAgEAgEAoHgygKZ4wG9qjws0wAAAABJRU5ErkJggg=="
)
_PROBE_PROMPT = "Reply with OK."

_probe_tasks: dict[tuple[str, str], asyncio.Task[bool]] = {}
_probe_tasks_lock = asyncio.Lock()


class ImageProbeUnavailable(RuntimeError):
    """The model could not be classified; no negative cache may be written."""


TransportErrorKind = Literal["protocol", "auth", "rate_limit", "transient"]


@dataclass
class VisionTransportError(RuntimeError):
    kind: TransportErrorKind
    message: str

    def __str__(self) -> str:
        return self.message


def _endpoint_candidates(base_url: str, resource: str) -> list[str]:
    """Return configured-base-first OpenAI-compatible endpoint candidates."""
    base = base_url.rstrip("/")
    parsed = urlparse(base)
    path = parsed.path.rstrip("/")
    candidates: list[str] = []

    def add(value: str) -> None:
        if value not in candidates:
            candidates.append(value)

    if path.endswith("/v1"):
        add(f"{base}/{resource}")
    else:
        add(f"{base}/v1/{resource}")
        add(f"{base}/{resource}")
    if parsed.netloc:
        add(f"{parsed.scheme}://{parsed.netloc}/v1/{resource}")
    return candidates


def _anthropic_message_candidates(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if path.endswith("/v1"):
        return [f"{base}/messages"]
    return [f"{base}/v1/messages", f"{base}/messages"]


def _headers(profile: LlmProfile, *, anthropic: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {profile.auth_token}",
        "x-api-key": profile.auth_token,
    }
    if anthropic:
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _safe_error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                value = error.get("message") or error.get("type")
                if value:
                    return str(value)[:300]
            detail = payload.get("detail") or payload.get("message")
            if detail:
                return str(detail)[:300]
    except (ValueError, TypeError):
        pass
    return (response.text or f"HTTP {response.status_code}")[:300]


def _looks_like_missing_model(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in (
        "model not found",
        "model does not exist",
        "unknown model",
        "invalid model",
        "no such model",
    ))


def _looks_like_non_capability_error(text: str) -> bool:
    lowered = text.lower()
    return _looks_like_missing_model(text) or any(marker in lowered for marker in (
        "api key",
        "authentication",
        "unauthorized",
        "quota",
        "billing",
        "rate limit",
        "max_tokens",
        "max tokens",
    ))


async def _probe_image_once(profile: LlmProfile, model_id: str) -> bool:
    payload = {
        "model": model_id,
        "max_tokens": 1,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _PROBE_IMAGE_BASE64,
                    },
                },
                {"type": "text", "text": _PROBE_PROMPT},
            ],
        }],
    }
    last_response: httpx.Response | None = None
    try:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            for url in _anthropic_message_candidates(profile.base_url):
                response = await client.post(
                    url,
                    headers={**_headers(profile, anthropic=True), "Content-Type": "application/json"},
                    json=payload,
                )
                last_response = response
                if response.status_code not in {404, 405}:
                    break
    except httpx.TimeoutException as exc:
        raise ImageProbeUnavailable("model_unavailable") from exc
    except httpx.HTTPError as exc:
        raise ImageProbeUnavailable("model_unavailable") from exc

    if last_response is None:
        raise ImageProbeUnavailable("model_unavailable")
    if 200 <= last_response.status_code < 300:
        return True

    detail = _safe_error_text(last_response)
    status = last_response.status_code
    if status in {400, 415, 422} and not _looks_like_non_capability_error(detail):
        # The same endpoint/model is already usable for text in Priva. A
        # schema/content rejection of the minimal image block is therefore a
        # deterministic negative capability result.
        return False
    raise ImageProbeUnavailable("model_unavailable")


def _acquire_probe_file_lock(profile_id: str, model_id: str):
    lock_dir = profile_store_path().parent / "runtime" / "image-capability-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(lock_dir, 0o700)
    except OSError:
        pass
    digest = hashlib.sha256(f"{profile_id}\0{model_id}".encode("utf-8")).hexdigest()
    handle = (lock_dir / f"{digest}.lock").open("a+", encoding="utf-8")
    try:
        os.chmod(handle.name, 0o600)
    except OSError:
        pass
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
    except Exception:
        handle.close()
        raise
    return handle


def _release_probe_file_lock(handle) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


async def _probe_and_store(profile_id: str, model_id: str, force: bool) -> bool:
    # The asyncio task map coalesces this worker; the file lock coalesces all
    # workers in the account pod without blocking their event loops.
    lock_handle = await asyncio.to_thread(
        _acquire_probe_file_lock, profile_id, model_id
    )
    try:
        try:
            profile = store.get(profile_id)
        except Exception as exc:
            raise ImageProbeUnavailable("model_unavailable") from exc
        current = profile.model_capabilities.get(model_id)
        if not force and current is not None and current.image is not None:
            return current.image
        result = await _probe_image_once(profile, model_id)
        try:
            store.update_model_capability(profile_id, model_id, image=result)
        except Exception as exc:
            raise ImageProbeUnavailable("model_unavailable") from exc
        return result
    finally:
        await asyncio.to_thread(_release_probe_file_lock, lock_handle)


async def probe_image_capability(
    profile: LlmProfile,
    model_id: str,
    *,
    force: bool = False,
) -> tuple[bool, bool]:
    """Probe once per profile/model and persist the result without a TTL.

    Returns ``(supports_image, was_cached)``. Network/auth/rate-limit/server
    failures raise ``ImageProbeUnavailable`` and never poison the cache.
    Concurrent callers share one in-flight upstream request.
    """
    current = profile.model_capabilities.get(model_id)
    if not force and current is not None and current.image is not None:
        return current.image, True

    key = (profile.id, model_id)
    async with _probe_tasks_lock:
        task = _probe_tasks.get(key)
        if task is None or task.done():
            task = asyncio.create_task(
                _probe_and_store(profile.id, model_id, force),
                name=f"image-capability:{profile.id}:{model_id}",
            )
            _probe_tasks[key] = task
    try:
        return await asyncio.shield(task), False
    finally:
        if task.done():
            async with _probe_tasks_lock:
                if _probe_tasks.get(key) is task:
                    _probe_tasks.pop(key, None)


async def resolve_image_route(model_reference: str | None) -> ImageRouteResponse:
    """Resolve the fastest safe route before any image upload occurs."""
    try:
        resolved = resolve_model(model_reference)
    except HTTPException:
        return ImageRouteResponse(route="probe_failed", reason="model_unavailable")

    profile = resolved.profile
    # Claude Code's ``[1m]`` suffix is a per-run context capability. Image
    # routing and Profile capability caches are keyed by the base model id.
    model_id = resolved.model_id
    if not model_id:
        return ImageRouteResponse(
            route="probe_failed",
            profile_id=profile.id,
            reason="model_unavailable",
        )

    common = {
        "profile_id": profile.id,
        "model_id": model_id,
        "vision_model": profile.vision_model,
    }
    if profile.vision_model and model_id == profile.vision_model:
        return ImageRouteResponse(route="direct", reason="configured_vision_model", **common)

    capability = profile.model_capabilities.get(model_id)
    probed = False
    if capability is None or capability.image is None:
        try:
            supports_image, _ = await probe_image_capability(profile, model_id)
            probed = True
        except ImageProbeUnavailable:
            return ImageRouteResponse(
                route="probe_failed",
                probed=True,
                reason="model_unavailable",
                **common,
            )
    else:
        supports_image = capability.image

    if supports_image:
        return ImageRouteResponse(route="direct", probed=probed, reason="image_capable", **common)
    if profile.vision_model:
        vision_capability = profile.model_capabilities.get(profile.vision_model)
        if (
            vision_capability is not None
            and vision_capability.image_read_transport == "unsupported"
        ):
            return ImageRouteResponse(
                route="probe_failed",
                probed=probed,
                reason="vision_model_unavailable",
                **common,
            )
        return ImageRouteResponse(route="vision_mcp", probed=probed, reason="vision_fallback", **common)
    return ImageRouteResponse(route="blocked", probed=probed, reason="vision_model_missing", **common)


def detect_image_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported or invalid image file")


def read_image_file(path: str) -> tuple[bytes, str, str]:
    file_path = Path(path)
    if file_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 5MB size limit")
    data = file_path.read_bytes()
    return data, detect_image_media_type(data), file_path.name


def _text_from_content(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {None, "text", "output_text"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                values.append(text.strip())
    return values


def extract_chat_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    values: list[str] = []
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                values.extend(_text_from_content(message.get("content")))
    if not values:
        values.extend(_text_from_content(payload.get("output_text")))
    return "\n".join(values).strip() or None


def extract_edit_text(payload: Any) -> str | None:
    """Extract provider-extension text while deliberately ignoring images."""
    if not isinstance(payload, dict):
        return None
    values: list[str] = []
    values.extend(_text_from_content(payload.get("text")))
    values.extend(_text_from_content(payload.get("output_text")))
    values.extend(_text_from_content(payload.get("content")))
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            # Never treat revised_prompt, url or b64_json as an answer.
            values.extend(_text_from_content(item.get("text")))
            values.extend(_text_from_content(item.get("content")))
    if not values:
        values.extend(_text_from_content(extract_chat_text(payload)))
    return "\n".join(values).strip() or None


def _transport_error(response: httpx.Response) -> VisionTransportError:
    status = response.status_code
    detail = _safe_error_text(response)
    if status in {401, 403}:
        return VisionTransportError("auth", "Vision model authentication failed")
    if status == 429:
        return VisionTransportError("rate_limit", "Vision model is rate limited")
    if status >= 500:
        return VisionTransportError("transient", "Vision model is unavailable")
    return VisionTransportError("protocol", f"Vision endpoint rejected the request: {detail}")


def _cache_image_read_transport(
    profile_id: str,
    model_id: str,
    transport: Literal["chat_completions", "images_edits", "unsupported"],
) -> None:
    try:
        store.update_model_capability(
            profile_id,
            model_id,
            image_read_transport=transport,
        )
    except Exception:
        # A successful image analysis remains useful even if this optional
        # optimization cannot be persisted (for example, the Profile was
        # deleted while the run was active).
        logger.warning(
            "Failed to cache Vision transport for profile={} model={}",
            profile_id,
            model_id,
            exc_info=True,
        )


async def _post_chat_completions(
    client: httpx.AsyncClient,
    profile: LlmProfile,
    model_id: str,
    image_data: bytes,
    media_type: str,
    prompt: str,
) -> str:
    encoded = base64.b64encode(image_data).decode("ascii")
    payload = {
        "model": model_id,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                },
            ],
        }],
        "max_tokens": 2048,
        "stream": False,
    }
    last_response: httpx.Response | None = None
    for url in _endpoint_candidates(profile.base_url, "chat/completions"):
        response = await client.post(url, headers=_headers(profile), json=payload)
        last_response = response
        if response.status_code not in {404, 405}:
            break
    if last_response is None:
        raise VisionTransportError("protocol", "Vision chat endpoint is unavailable")
    if not 200 <= last_response.status_code < 300:
        raise _transport_error(last_response)
    try:
        text = extract_chat_text(last_response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise VisionTransportError("protocol", "Vision chat endpoint returned invalid JSON") from exc
    if not text:
        raise VisionTransportError("protocol", "Vision chat endpoint returned no text")
    return text


async def _post_images_edits(
    client: httpx.AsyncClient,
    profile: LlmProfile,
    model_id: str,
    image_data: bytes,
    media_type: str,
    filename: str,
    prompt: str,
) -> str:
    last_response: httpx.Response | None = None
    for url in _endpoint_candidates(profile.base_url, "images/edits"):
        response = await client.post(
            url,
            headers=_headers(profile),
            data={"model": model_id, "prompt": prompt},
            files={"image": (filename or "image", image_data, media_type)},
        )
        last_response = response
        if response.status_code not in {404, 405}:
            break
    if last_response is None:
        raise VisionTransportError("protocol", "Vision image edit endpoint is unavailable")
    if not 200 <= last_response.status_code < 300:
        raise _transport_error(last_response)
    try:
        text = extract_edit_text(last_response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise VisionTransportError("protocol", "Vision image edit endpoint returned invalid JSON") from exc
    if not text:
        # A standard image-only response is intentionally discarded and does
        # not count as a successful image_read result.
        raise VisionTransportError("protocol", "Vision image edit endpoint returned no text")
    return text


async def image_read_text(
    profile: LlmProfile,
    model_id: str,
    image_data: bytes,
    media_type: str,
    filename: str,
    prompt: str,
) -> str:
    """Return text only, using cached transport preference when available."""
    if len(image_data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 5MB size limit")
    detected = detect_image_media_type(image_data)
    if media_type != detected:
        media_type = detected
    prompt = (prompt or "Describe the image accurately.").strip()

    capability = profile.model_capabilities.get(model_id)
    preferred = capability.image_read_transport if capability else None
    if preferred == "unsupported":
        raise VisionTransportError("protocol", "Vision model is unavailable")

    try:
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if preferred == "images_edits":
                order = ["images_edits", "chat_completions"]
            else:
                order = ["chat_completions", "images_edits"]

            errors: list[VisionTransportError] = []
            for index, transport in enumerate(order):
                try:
                    if transport == "chat_completions":
                        text = await _post_chat_completions(
                            client, profile, model_id, image_data, media_type, prompt
                        )
                    else:
                        text = await _post_images_edits(
                            client, profile, model_id, image_data, media_type, filename, prompt
                        )
                    _cache_image_read_transport(profile.id, model_id, transport)
                    return text
                except VisionTransportError as exc:
                    errors.append(exc)
                    # Auth, rate-limit and transient failures would affect the
                    # fallback endpoint too; avoid a second costly request.
                    if exc.kind != "protocol" or index == len(order) - 1:
                        break

            if errors and all(error.kind == "protocol" for error in errors) and len(errors) == len(order):
                _cache_image_read_transport(profile.id, model_id, "unsupported")
            raise errors[-1] if errors else VisionTransportError(
                "protocol", "Vision model is unavailable"
            )
    except httpx.TimeoutException as exc:
        raise VisionTransportError("transient", "Vision model timed out") from exc
    except httpx.HTTPError as exc:
        raise VisionTransportError("transient", "Vision model is unavailable") from exc

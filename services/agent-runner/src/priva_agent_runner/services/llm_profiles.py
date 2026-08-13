"""Per-account LLM profile store, migration, resolution and SDK overlays."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.llm_profiles import (
    ImageReadTransport,
    LlmProfile,
    ModelCapabilities,
)
from priva_common.paths import priva_home
from priva_common.user_env import read_settings_env

logger = get_app_logger(__name__)

PROFILE_STORE_VERSION = 2
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
PROFILE_STORE_PATH = "llm-profiles.json"
RUNTIME_OVERLAY_DIR = "runtime/llm-profile-overlays"

_thread_lock = threading.RLock()
_CAPABILITY_UNSET = object()


def profile_store_path() -> Path:
    return priva_home() / PROFILE_STORE_PATH


def overlay_dir() -> Path:
    return priva_home() / RUNTIME_OVERLAY_DIR


def validate_profile_id(value: str) -> str:
    value = (value or "").strip().lower()
    if not PROFILE_ID_RE.fullmatch(value) or ":" in value:
        raise HTTPException(422, "Profile id must match [a-z0-9][a-z0-9._-]{0,62}")
    return value


def validate_endpoint(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(422, "base_url must not contain embedded credentials")
    return value


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        lock_path = path.with_name(f".{path.name}.lock")
        _ensure_private_dir(lock_path.parent)
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                with path.open("r", encoding="utf-8") as handle:
                    value = json.load(handle)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Failed to read LLM profile store {}: {}", path, exc)
        return {}


def _write_json(data: dict[str, Any]) -> None:
    path = profile_store_path()
    _ensure_private_dir(path.parent)
    lock_path = path.with_name(f".{path.name}.lock")
    with _thread_lock, lock_path.open("a+", encoding="utf-8") as lock:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _atomic_write(path, data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    _ensure_private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _raw_profiles(data: dict[str, Any]) -> tuple[list[LlmProfile], str | None]:
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list):
        return [], None
    profiles: list[LlmProfile] = []
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            continue
        try:
            profiles.append(LlmProfile.model_validate(raw))
        except Exception as exc:
            logger.warning("Skipping invalid LLM profile: {}", exc)
    default_id = data.get("default_profile_id")
    default_id = default_id if isinstance(default_id, str) else None
    if default_id not in {p.id for p in profiles}:
        default_id = profiles[0].id if profiles else None
    return profiles, default_id


def _legacy_profile(vision_model: str | None = None) -> LlmProfile | None:
    env = read_settings_env()
    base_url = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    auth_token = (env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not base_url or not auth_token:
        return None
    try:
        base_url = validate_endpoint(base_url)
    except HTTPException:
        logger.warning("Skipping legacy profile migration: invalid ANTHROPIC_BASE_URL")
        return None
    return LlmProfile(
        id="default",
        label="Default",
        base_url=base_url,
        auth_token=auth_token,
        default_model=env.get("ANTHROPIC_MODEL") or None,
        opus_model=env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or None,
        sonnet_model=env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or None,
        haiku_model=env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or None,
        vision_model=vision_model or None,
    )


class LlmProfileStore:
    """Synchronous store; routes call it directly because operations are tiny."""

    def ensure_migrated(self, vision_model: str | None = None) -> tuple[list[LlmProfile], str | None]:
        path = profile_store_path()
        with _thread_lock:
            if path.exists():
                return _raw_profiles(_read_json(path))
            profile = _legacy_profile(vision_model)
            data = {
                "version": PROFILE_STORE_VERSION,
                "default_profile_id": profile.id if profile else None,
                "profiles": [profile.model_dump() for profile in [profile] if profile],
            }
            _write_json(data)
            return _raw_profiles(data)

    def read(self, vision_model: str | None = None) -> tuple[list[LlmProfile], str | None]:
        return self.ensure_migrated(vision_model)

    def save(self, profiles: list[LlmProfile], default_profile_id: str | None) -> None:
        ids = {p.id for p in profiles}
        if default_profile_id is not None and default_profile_id not in ids:
            raise HTTPException(409, "default_profile_missing")
        if profiles and default_profile_id is None:
            raise HTTPException(409, "default_profile_missing")
        _write_json({
            "version": PROFILE_STORE_VERSION,
            "default_profile_id": default_profile_id,
            "profiles": [p.model_dump() for p in profiles],
        })

    def get(self, profile_id: str, vision_model: str | None = None) -> LlmProfile:
        profiles, _ = self.read(vision_model)
        for profile in profiles:
            if profile.id == profile_id:
                return profile
        raise HTTPException(404, "profile_not_found")

    def default(self, vision_model: str | None = None) -> LlmProfile:
        profiles, default_id = self.read(vision_model)
        if not default_id:
            raise HTTPException(400, "default_profile_missing")
        for profile in profiles:
            if profile.id == default_id:
                return profile
        raise HTTPException(400, "default_profile_missing")

    def upsert(self, profile: LlmProfile, *, replacing_id: str | None = None, vision_model: str | None = None) -> None:
        profiles, default_id = self.read(vision_model)
        if replacing_id is None and any(p.id == profile.id for p in profiles):
            raise HTTPException(409, "profile_id_exists")
        profiles = [profile if (p.id == replacing_id or p.id == profile.id) else p for p in profiles]
        if replacing_id is None:
            profiles.append(profile)
        if default_id is None:
            default_id = profile.id
        self.save(profiles, default_id)

    def delete(self, profile_id: str, vision_model: str | None = None) -> None:
        profiles, default_id = self.read(vision_model)
        if not any(p.id == profile_id for p in profiles):
            raise HTTPException(404, "profile_not_found")
        remaining = [p for p in profiles if p.id != profile_id]
        if default_id == profile_id:
            default_id = remaining[0].id if remaining else None
        self.save(remaining, default_id)

    def set_default(self, profile_id: str, vision_model: str | None = None) -> None:
        profiles, _ = self.read(vision_model)
        if not any(p.id == profile_id for p in profiles):
            raise HTTPException(404, "profile_not_found")
        self.save(profiles, profile_id)

    def update_model_capability(
        self,
        profile_id: str,
        model_id: str,
        *,
        image: bool | None | object = _CAPABILITY_UNSET,
        image_read_transport: ImageReadTransport | None | object = _CAPABILITY_UNSET,
        vision_model: str | None = None,
    ) -> LlmProfile:
        """Atomically update capability facts for one exact model id.

        The capability map is internal state: ordinary profile PATCH requests
        preserve it but cannot set it.  This method holds the store's file lock
        across read/modify/write so concurrent probes in different workers do
        not overwrite one another.
        """
        model_id = (model_id or "").strip()
        if not model_id:
            raise HTTPException(422, "model_id is required")
        if len(model_id) > 512:
            raise HTTPException(422, "model_id is too long")

        self.ensure_migrated(vision_model)
        path = profile_store_path()
        lock_path = path.with_name(f".{path.name}.lock")
        _ensure_private_dir(lock_path.parent)
        with _thread_lock, lock_path.open("a+", encoding="utf-8") as lock:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except (OSError, ValueError, TypeError):
                    data = {}
                profiles, default_id = _raw_profiles(data)
                updated_profile: LlmProfile | None = None
                updated_profiles: list[LlmProfile] = []
                for profile in profiles:
                    if profile.id != profile_id:
                        updated_profiles.append(profile)
                        continue
                    capabilities = dict(profile.model_capabilities)
                    current = capabilities.get(model_id, ModelCapabilities())
                    values = current.model_dump()
                    if image is not _CAPABILITY_UNSET:
                        values["image"] = image
                    if image_read_transport is not _CAPABILITY_UNSET:
                        values["image_read_transport"] = image_read_transport
                    capabilities[model_id] = ModelCapabilities.model_validate(values)
                    updated_profile = profile.model_copy(
                        update={"model_capabilities": capabilities}
                    )
                    updated_profiles.append(updated_profile)

                if updated_profile is None:
                    raise HTTPException(404, "profile_not_found")
                _atomic_write(path, {
                    "version": PROFILE_STORE_VERSION,
                    "default_profile_id": default_id,
                    "profiles": [profile.model_dump() for profile in updated_profiles],
                })
                return updated_profile
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


store = LlmProfileStore()


def profile_summary(profile: LlmProfile, model_count: int | None = None) -> dict[str, Any]:
    return {
        **profile.model_dump(),
        "auth_token_set": bool(profile.auth_token),
        "model_count": model_count,
    }


def resolve_model_reference(reference: str | None, *, profiles: list[LlmProfile], default_profile_id: str | None) -> tuple[LlmProfile, str | None]:
    """Resolve ``profile:model`` or an unqualified model in the default profile.

    Only a known profile id is treated as a qualifier.  This preserves model ids
    containing colons (for example Ollama names) and leaves model existence to
    the upstream provider so its existing error behavior is unchanged.
    """
    by_id = {p.id: p for p in profiles}
    if not default_profile_id or default_profile_id not in by_id:
        raise HTTPException(400, "default_profile_missing")
    value = (reference or "").strip()
    profile = by_id[default_profile_id]
    model = value or profile.default_model
    if ":" in value:
        prefix, remainder = value.split(":", 1)
        if prefix in by_id:
            profile = by_id[prefix]
            model = remainder or profile.default_model
            if not model:
                raise HTTPException(400, "invalid_model_reference")
    return profile, model


@dataclass(frozen=True)
class ResolvedProfile:
    profile: LlmProfile
    model: str | None


def resolve_model(reference: str | None, vision_model: str | None = None) -> ResolvedProfile:
    profiles, default_id = store.read(vision_model)
    profile, model = resolve_model_reference(reference, profiles=profiles, default_profile_id=default_id)
    if not profile.base_url or not profile.auth_token or not model:
        raise HTTPException(400, "profile_not_ready")
    return ResolvedProfile(profile=profile, model=model)


@contextmanager
def profile_settings_overlay(
    profile: LlmProfile,
    *,
    model: str | None,
    extra_settings: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Write a per-run, app-config-local settings file for ``--settings``.

    The file is removed in the context manager's finally block.  Secrets never
    appear in process arguments; ClaudeAgentOptions.settings carries only the
    path, while options.model carries the highest-priority model flag.
    """
    directory = overlay_dir()
    _ensure_private_dir(directory)
    path = directory / f"{uuid.uuid4().hex}.json"
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": profile.base_url,
        "ANTHROPIC_AUTH_TOKEN": profile.auth_token,
    }
    if profile.default_model:
        env["ANTHROPIC_MODEL"] = profile.default_model
    if profile.opus_model:
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = profile.opus_model
    if profile.sonnet_model:
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = profile.sonnet_model
    if profile.haiku_model:
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = profile.haiku_model
    payload = dict(extra_settings or {})
    payload["env"] = env
    _atomic_write(path, payload)
    try:
        yield str(path)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def open_profile_settings_overlay(
    profile: LlmProfile,
    *,
    model: str | None,
    extra_settings: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """Open an overlay and return ``(path, context_manager)``.

    The caller owns the context manager and must call ``close_profile_settings_overlay``
    after the complete SDK retry/stream lifecycle.
    """
    manager = profile_settings_overlay(
        profile,
        model=model,
        extra_settings=extra_settings,
    )
    path = manager.__enter__()
    return path, manager


def close_profile_settings_overlay(manager: Any | None) -> None:
    if manager is None:
        return
    try:
        manager.__exit__(None, None, None)
    except Exception:
        logger.warning("Failed to clean up LLM profile settings overlay", exc_info=True)


def cleanup_stale_overlays() -> None:
    directory = overlay_dir()
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            logger.warning("Failed to remove stale LLM profile overlay {}", path)

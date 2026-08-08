from __future__ import annotations

import fcntl
import os
import tempfile
import threading
from typing import Any
from pathlib import Path

import yaml

from ...models.channels import OpenClawChannelConfig, WeComChannelConfig
from ...middleware.logging import get_channels_logger
from priva_common.paths import priva_home

logger = get_channels_logger(__name__)


def _get_user_config_path() -> Path:
    return priva_home() / ".priva.user.yml"


class ChannelConfigStore:
    def __init__(self):
        self._lock = threading.Lock()

    def get_config(self) -> WeComChannelConfig:
        path = _get_user_config_path()
        if not path.exists():
            return WeComChannelConfig()

        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = yaml.safe_load(f) or {}
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            logger.warning("Failed to read channel config")
            return WeComChannelConfig()

        channels = data.get("channels", {})
        if not isinstance(channels, dict):
            return WeComChannelConfig()

        wecom = channels.get("wecom", {})
        if not isinstance(wecom, dict):
            return WeComChannelConfig()

        try:
            return WeComChannelConfig.model_validate(wecom)
        except Exception:
            logger.warning("Invalid channel config, using defaults")
            return WeComChannelConfig()

    def save_config(self, config: WeComChannelConfig) -> None:
        path = _get_user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # Read existing data to preserve sibling keys
            existing = {}
            if path.exists():
                try:
                    with open(path, "r") as f:
                        fcntl.flock(f, fcntl.LOCK_SH)
                        try:
                            existing = yaml.safe_load(f) or {}
                        finally:
                            fcntl.flock(f, fcntl.LOCK_UN)
                except Exception:
                    existing = {}

            # Ensure channels dict exists, preserve siblings
            if "channels" not in existing or not isinstance(existing["channels"], dict):
                existing["channels"] = {}

            existing["channels"]["wecom"] = config.model_dump(mode="json")

            # Atomic write: temp file + os.replace()
            fd, tmp_path = tempfile.mkstemp(
                dir=path.parent, suffix=".tmp", prefix=".priva.user."
            )
            try:
                with os.fdopen(fd, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    # -- Generic .priva.user.yml key accessors --

    def _read_user_yaml(self) -> dict:
        """Read full .priva.user.yml as a dict."""
        path = _get_user_config_path()
        if not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    return yaml.safe_load(f) or {}
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            logger.warning("Failed to read .priva.user.yml")
            return {}

    def get_user_yaml_key(self, key: str, default: Any = None) -> Any:
        """Read a top-level key from .priva.user.yml."""
        return self._read_user_yaml().get(key, default)

    def save_user_yaml_key(self, key: str, value: Any) -> None:
        """Atomically set (or delete, when ``value`` is ``None``) a top-level
        key in .priva.user.yml. Passing ``None`` pops the key entirely."""
        path = _get_user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            existing = self._read_user_yaml()
            if value is None:
                existing.pop(key, None)
            else:
                existing[key] = value

            fd, tmp_path = tempfile.mkstemp(
                dir=path.parent, suffix=".tmp", prefix=".priva.user."
            )
            try:
                with os.fdopen(fd, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def get_skill_exclude(self) -> list[str]:
        """Return the single-pod skill_exclude denylist."""
        value = self._read_user_yaml().get("skill_exclude", [])
        return list(value) if isinstance(value, list) else []

    def save_skill_exclude(self, value: list[str]) -> None:
        """Write the explicit skill_exclude denylist."""
        self.save_user_yaml_key("skill_exclude", list(value or []))

    # -- OpenClaw config (stored under channels.openclaw) --

    def get_openclaw_config(self) -> OpenClawChannelConfig:
        path = _get_user_config_path()
        if not path.exists():
            return OpenClawChannelConfig()

        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = yaml.safe_load(f) or {}
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            logger.warning("Failed to read openclaw config")
            return OpenClawChannelConfig()

        channels = data.get("channels", {})
        if not isinstance(channels, dict):
            return OpenClawChannelConfig()

        openclaw = channels.get("openclaw", {})
        if not isinstance(openclaw, dict):
            return OpenClawChannelConfig()

        try:
            return OpenClawChannelConfig.model_validate(openclaw)
        except Exception:
            logger.warning("Invalid openclaw config, using defaults")
            return OpenClawChannelConfig()

    def save_openclaw_config(self, config: OpenClawChannelConfig) -> None:
        path = _get_user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            existing = {}
            if path.exists():
                try:
                    with open(path, "r") as f:
                        fcntl.flock(f, fcntl.LOCK_SH)
                        try:
                            existing = yaml.safe_load(f) or {}
                        finally:
                            fcntl.flock(f, fcntl.LOCK_UN)
                except Exception:
                    existing = {}

            if "channels" not in existing or not isinstance(existing["channels"], dict):
                existing["channels"] = {}

            existing["channels"]["openclaw"] = config.model_dump(mode="json")

            fd, tmp_path = tempfile.mkstemp(
                dir=path.parent, suffix=".tmp", prefix=".priva.user."
            )
            try:
                with os.fdopen(fd, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def list_enabled_openclaw_configs(self) -> dict[str, OpenClawChannelConfig]:
        """Return the pod account's enabled OpenClaw config."""
        username = os.environ.get("USERNAME")
        config = self.get_openclaw_config()
        return {username: config} if username and config.enabled else {}

    def list_enabled_configs(self) -> dict[str, WeComChannelConfig]:
        """Return the pod account's enabled WeCom config."""
        username = os.environ.get("USERNAME")
        config = self.get_config()
        return {username: config} if username and config.enabled else {}

    def find_bot_id_owner(self, bot_id: str, exclude_username: str | None = None) -> str | None:
        """Check if any user already has this bot_id with enabled=True. Returns username or None."""
        if not bot_id:
            return None
        username = os.environ.get("USERNAME")
        if not username or username == exclude_username:
            return None
        config = self.get_config()
        if config.enabled and config.bot_id == bot_id:
            return username
        return None


_store: ChannelConfigStore | None = None


def get_channel_config_store() -> ChannelConfigStore:
    global _store
    if _store is None:
        _store = ChannelConfigStore()
    return _store

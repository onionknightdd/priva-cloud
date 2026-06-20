"""Per-account env (BYOK ANTHROPIC_* creds) backed by the data-spine secret store.

Drop-in for priva_common.user_env's read/write/has API, but instead of a
settings.local.json file (gone once CP and the runner are separate pods) it
persists to data-spine (Fernet-encrypted), where the operator reads it to inject
a per-pod K8s Secret at wake. Keyed by username on the surface (call-sites are
unchanged); resolved to account_id internally.
"""

from __future__ import annotations

from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_common.user_env import ENV_KEYS
from priva_common.user_store import get_user_store

logger = get_app_logger(__name__)


def _account_id(username: str) -> str | None:
    u = get_user_store().get_user(username)
    return getattr(u, "account_id", None) if u else None


def read_user_env(username: str) -> dict | None:
    aid = _account_id(username)
    if not aid:
        return None
    rec = get_client().secrets.get(aid)
    return dict(rec.bundle) if rec and rec.bundle else None


def write_user_env(username: str, env: dict) -> None:
    aid = _account_id(username)
    if not aid:
        logger.warning("write_user_env: no account for username={}", username)
        return
    rec = get_client().secrets.get(aid)
    current = dict(rec.bundle) if rec and rec.bundle else {}
    # Mirror user_env merge: set provided keys; ensure every ENV_KEY exists ("").
    for key in ENV_KEYS:
        if key in env and env[key] is not None:
            current[key] = env[key]
        elif key not in current:
            current[key] = ""
    get_client().secrets.put(aid, current)


def has_user_env(username: str) -> bool:
    env = read_user_env(username)
    if not env:
        return False
    return bool(env.get("ANTHROPIC_BASE_URL")) and bool(env.get("ANTHROPIC_AUTH_TOKEN"))

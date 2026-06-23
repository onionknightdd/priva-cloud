"""Account store — client-backed façade over the data-plane (Phase 1, U1).

Every account method delegates to the data-spine (SQLite) via
`priva_common.dataplane.get_client().accounts`, preserving the username-keyed
signatures so existing routers/auth call-sites are unchanged; account_id↔username
mapping is internal. Runtime config stays file-backed (PVC-owned) via
RuntimeConfigStore (see U0). The in-process handlers are registered once at
startup by `priva_data_spine.compose()` (main.py lifespan).
"""

from __future__ import annotations

from .dataplane import get_client
from .logging import get_app_logger
from .models.auth import UserRecord
from .runtime_config_store import get_runtime_config_store

logger = get_app_logger(__name__)

# Sentinel matching the original update_user(api_key=...) convention:
# omitted -> leave, None -> clear, value -> set.
_UNSET = ...


class UserStore:
    def _accounts(self):
        return get_client().accounts

    # --- account methods (delegate to data-spine) ---

    def has_users(self) -> bool:
        return self._accounts().has_users()

    def get_user(self, username: str) -> UserRecord | None:
        return self._accounts().get_by_username(username)

    def list_users(self) -> list[UserRecord]:
        return self._accounts().list()

    def create_user(self, username: str, password: str = "", role: str = "user",
                    agent_runner_type: str = "auto_scale", password_hash: str | None = None) -> UserRecord:
        return self._accounts().create(
            username, password, role, agent_runner_type=agent_runner_type, password_hash=password_hash)

    def update_user(
        self,
        username: str,
        password: str | None = None,
        role: str | None = None,
        api_key=_UNSET,
        agent_runner_type: str | None = None,
    ) -> UserRecord:
        accounts = self._accounts()
        rec = accounts.get_by_username(username)
        if rec is None:
            raise ValueError(f"User '{username}' not found")
        return accounts.update(rec.account_id, password=password, role=role, api_key=api_key,
                               agent_runner_type=agent_runner_type)

    def delete_user(self, username: str) -> None:
        accounts = self._accounts()
        rec = accounts.get_by_username(username)
        if rec is None:
            raise ValueError(f"User '{username}' not found")
        accounts.delete(rec.account_id)

    def verify_password(self, username: str, password: str) -> bool:
        return self._accounts().verify_password(username, password)

    def find_by_api_key(self, key: str) -> UserRecord | None:
        return self._accounts().find_by_api_key(key)

    def count_admins(self) -> int:
        return self._accounts().count_admins()

    # --- runtime config (PVC-owned, file-backed; see U0) ---

    def get_runtime_config(self) -> dict:
        return get_runtime_config_store().get_runtime_config()

    def update_runtime_config(self, key: str, value: dict | None) -> dict:
        return get_runtime_config_store().update_runtime_config(key, value)


_store: UserStore | None = None


def get_user_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store

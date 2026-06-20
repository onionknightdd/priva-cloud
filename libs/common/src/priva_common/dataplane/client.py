"""Data-plane client interface (the transport seam) + boundary DTOs.

These Protocols define the gRPC-shaped contract that every service calls. The
in-process transport (Phase 1) backs them with `priva_data_spine` service impls;
the gRPC transport (deferred) backs them with generated stubs. Callers depend
only on this module — never on the service package.

DTOs reuse the existing pydantic models where they already exist
(`UserRecord`, `ScheduledJobDefinition`, `JobRunRecord`); `BindingRecord` and
`QuotaRecord` are defined here (greenfield).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition

__all__ = [
    "UNSET",
    "BindingRecord",
    "QuotaRecord",
    "RunPage",
    "AccountClient",
    "BindingClient",
    "QuotaClient",
    "SchedulerClient",
    "AdminClient",
    "DataplaneClient",
]

# Sentinel for "field not provided" vs "field set to None" (mirrors the monolith's
# UserStore.update_user(api_key=...) convention).
UNSET: Any = ...


class BindingRecord(BaseModel):
    binding_id: str
    account_id: str
    session_uuid: str
    first_run_done: bool = False
    feishu_chat_id: str | None = None
    bound_at: str | None = None
    rebound_at: str | None = None


class QuotaRecord(BaseModel):
    account_id: str
    tier: str = "default"
    max_concurrent_sessions: int = 3
    idle_grace_seconds: int = 1800
    updated_at: str | None = None


class RunPage(BaseModel):
    runs: list[JobRunRecord]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total: int | None = None  # None when a filter is active (total unknown)


class AccountClient(Protocol):
    def get(self, account_id: str) -> UserRecord | None: ...
    def get_by_username(self, username: str) -> UserRecord | None: ...
    def list(self) -> list[UserRecord]: ...
    def create(self, username: str, password: str, role: str = "user") -> UserRecord: ...
    def update(
        self,
        account_id: str,
        *,
        password: str | None = None,
        role: str | None = None,
        api_key: Any = UNSET,  # UNSET=leave, None=clear, str=set
        status: str | None = None,
        feishu_user_id: Any = UNSET,
        feishu_display_name: Any = UNSET,
    ) -> UserRecord: ...
    def delete(self, account_id: str) -> None: ...
    def verify_password(self, username: str, password: str) -> bool: ...
    def find_by_api_key(self, api_key: str) -> UserRecord | None: ...
    def count_admins(self) -> int: ...
    def find_by_feishu_user_id(self, feishu_user_id: str) -> UserRecord | None: ...
    def has_users(self) -> bool: ...


class BindingClient(Protocol):
    def bind(self, account_id: str, session_uuid: str, feishu_chat_id: str | None = None) -> BindingRecord: ...
    def rebind(self, account_id: str, session_uuid: str, feishu_chat_id: str | None = None) -> BindingRecord: ...
    def claim_first_run_im(self, binding_id: str) -> bool: ...
    def get_binding(self, binding_id: str) -> BindingRecord | None: ...
    def list_bindings(self, account_id: str) -> list[BindingRecord]: ...


class QuotaClient(Protocol):
    def get(self, account_id: str) -> QuotaRecord | None: ...
    def ensure(self, account_id: str) -> QuotaRecord: ...  # seed defaults if absent
    def set(
        self,
        account_id: str,
        *,
        tier: str | None = None,
        max_concurrent_sessions: int | None = None,
        idle_grace_seconds: int | None = None,
    ) -> QuotaRecord: ...


class SchedulerClient(Protocol):
    def create_job(self, account_id: str, defn: ScheduledJobDefinition) -> ScheduledJobDefinition: ...
    def get_job(self, job_id: str) -> ScheduledJobDefinition | None: ...
    def update_job(self, job_id: str, defn: ScheduledJobDefinition) -> ScheduledJobDefinition | None: ...
    def delete_job(self, job_id: str) -> bool: ...
    def list_jobs(self, account_id: str) -> list[ScheduledJobDefinition]: ...
    # (account_id, defn) pairs — the daemon needs the owner of each active job.
    def list_active_jobs(self) -> list[tuple[str, ScheduledJobDefinition]]: ...
    def set_job_status(self, job_id: str, status: str) -> ScheduledJobDefinition | None: ...
    def start_run(self, account_id: str, record: JobRunRecord) -> JobRunRecord: ...
    def finish_run(self, record: JobRunRecord) -> JobRunRecord: ...
    def record_run(self, account_id: str, record: JobRunRecord) -> JobRunRecord: ...  # full-snapshot upsert
    def get_run(self, account_id: str, run_id: str) -> JobRunRecord | None: ...
    def get_latest_run(self, account_id: str, job_id: str) -> JobRunRecord | None: ...
    def list_runs(
        self,
        account_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> RunPage: ...
    def delete_runs_before(self, account_id: str, cutoff_date: str) -> list[str]: ...  # returns deleted run_ids


class AdminClient(Protocol):
    def healthz(self) -> str: ...
    def readyz(self) -> tuple[bool, str]: ...
    def stats(self) -> dict[str, int]: ...


@dataclass
class DataplaneClient:
    """Aggregate handle — one per process. `get_client()` returns this."""

    accounts: AccountClient
    bindings: BindingClient
    quota: QuotaClient
    scheduler: SchedulerClient
    admin: AdminClient

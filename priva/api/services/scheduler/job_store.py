"""JobStore — client-backed adapter over the data-plane (Phase 1, J1).

Preserves the username-keyed signatures (list_jobs / get_job / save_jobs /
list_all_user_jobs) so routers, mcp_tools and the daemon are unchanged;
username↔account_id mapping is internal. Jobs live in data-spine (SQLite).
"""

from __future__ import annotations

from priva_common.dataplane import get_client

from ...middleware.logging import get_scheduler_logger
from ...models.scheduler import ScheduledJobDefinition

logger = get_scheduler_logger(__name__)


class JobStore:
    @staticmethod
    def _client():
        return get_client()

    def _account_id(self, username: str) -> str | None:
        user = self._client().accounts.get_by_username(username)
        return user.account_id if user else None

    def list_jobs(self, username: str) -> list[ScheduledJobDefinition]:
        account_id = self._account_id(username)
        if account_id is None:
            return []
        return self._client().scheduler.list_jobs(account_id)

    def get_job(self, username: str, job_id: str) -> ScheduledJobDefinition | None:
        # Ownership-safe: only return the job if it belongs to this user's account.
        account_id = self._account_id(username)
        if account_id is None:
            return None
        for job in self._client().scheduler.list_jobs(account_id):
            if job.id == job_id:
                return job
        return None

    def save_jobs(self, username: str, jobs: list[ScheduledJobDefinition]) -> None:
        # Replace-the-list semantics, expressed as a diff against the stored set.
        # (Not atomic across the N ops — acceptable for the alpha; documented.)
        account_id = self._account_id(username)
        if account_id is None:
            raise ValueError(f"User '{username}' not found")
        sched = self._client().scheduler
        existing = {j.id for j in sched.list_jobs(account_id)}
        desired = set()
        for defn in jobs:
            desired.add(defn.id)
            if defn.id in existing:
                sched.update_job(defn.id, defn)
            else:
                sched.create_job(account_id, defn)
        for job_id in existing - desired:
            sched.delete_job(job_id)

    def list_all_user_jobs(self) -> dict[str, list[ScheduledJobDefinition]]:
        client = self._client()
        result: dict[str, list[ScheduledJobDefinition]] = {}
        for user in client.accounts.list():
            jobs = client.scheduler.list_jobs(user.account_id)
            if jobs:
                result[user.username] = jobs
        return result


_store: JobStore | None = None


def get_job_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore()
    return _store

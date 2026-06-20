"""RunHistoryStore — client-backed adapter over the data-plane (Phase 1, R1).

Run METADATA lives in data-spine (SQLite); the per-run OUTPUT transcript
(runs_dir/{run_id}.jsonl) stays on the PVC. Preserves the username-keyed
signatures (append / query_cursor / query / get_run / get_latest_run /
purge_old_records / purge_all_users) so routers and the daemon are unchanged.
username↔account_id mapping is internal; returned records get username re-stamped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from priva_common.dataplane import get_client

from ...middleware.logging import get_scheduler_logger
from ...models.scheduler import JobRunRecord
from .shared import get_user_runs_dir

logger = get_scheduler_logger(__name__)


class RunHistoryStore:
    @staticmethod
    def _client():
        return get_client()

    def _account_id(self, username: str) -> str | None:
        user = self._client().accounts.get_by_username(username)
        return user.account_id if user else None

    # --- write path -------------------------------------------------------

    def append(self, record: JobRunRecord) -> None:
        account_id = self._account_id(record.username)
        if account_id is None:
            logger.warning("run append skipped: no account for user {}", record.username)
            return
        # Each append is a full snapshot → upsert by run_id (birth + outcomes).
        self._client().scheduler.record_run(account_id, record)

    # --- read paths -------------------------------------------------------

    def query_cursor(
        self,
        username: str,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[JobRunRecord], str | None, str | None, int | None]:
        account_id = self._account_id(username)
        if account_id is None:
            return [], None, None, 0
        page = self._client().scheduler.list_runs(
            account_id, limit=limit, before=before, after=after, job_id=job_id, status=status
        )
        for rec in page.runs:
            rec.username = username  # repo doesn't store username; re-stamp on read
        return page.runs, page.next_cursor, page.prev_cursor, page.total

    def query(
        self,
        username: str,
        limit: int = 50,
        offset: int = 0,
        job_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[JobRunRecord], int]:
        """Legacy wrapper for tests. New callers must use query_cursor."""
        if offset != 0:
            raise ValueError("query(offset=...) is no longer supported; use query_cursor")
        records, _, _, _ = self.query_cursor(username, limit=limit, job_id=job_id, status=status)
        return records, len(records)

    def get_run(self, username: str, run_id: str) -> JobRunRecord | None:
        account_id = self._account_id(username)
        if account_id is None:
            return None
        rec = self._client().scheduler.get_run(account_id, run_id)
        if rec is not None:
            rec.username = username
        return rec

    def get_latest_run(self, username: str, job_id: str) -> JobRunRecord | None:
        account_id = self._account_id(username)
        if account_id is None:
            return None
        rec = self._client().scheduler.get_latest_run(account_id, job_id)
        if rec is not None:
            rec.username = username
        return rec

    # --- maintenance ------------------------------------------------------

    def purge_old_records(self, username: str, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        account_id = self._account_id(username)
        if account_id is None:
            return 0
        # Keep the most recent `retention_days` calendar days (today .. today-(N-1));
        # delete everything dated on/before today-N. The exclusive lexicographic
        # cutoff for `started_at < ?` is therefore today-(N-1) = today-N+1.
        cutoff = (
            datetime.now(timezone.utc).date() - timedelta(days=retention_days - 1)
        ).isoformat()
        deleted_ids = self._client().scheduler.delete_runs_before(account_id, cutoff)
        # Delete the per-run output transcripts on the PVC (they stay file-side).
        if deleted_ids:
            runs_dir = get_user_runs_dir(username)
            if runs_dir.exists():
                for run_id in deleted_ids:
                    output_file = runs_dir / f"{run_id}.jsonl"
                    if output_file.exists():
                        try:
                            output_file.unlink()
                        except OSError:
                            pass
        if deleted_ids:
            logger.info("Purged {} run records for user {}", len(deleted_ids), username)
        return len(deleted_ids)

    def purge_all_users(self, retention_days: int) -> None:
        if retention_days <= 0:
            return
        total = 0
        for user in self._client().accounts.list():
            total += self.purge_old_records(user.username, retention_days)
        if total > 0:
            logger.info("Purged {} old run records across all users", total)


_store: RunHistoryStore | None = None


def get_run_history_store() -> RunHistoryStore:
    global _store
    if _store is None:
        _store = RunHistoryStore()
    return _store

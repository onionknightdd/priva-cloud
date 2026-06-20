"""Service layer — domain logic over the repo: crypto (bcrypt/Fernet/HMAC),
UUID minting, JSON (de)serialization, DTO mapping. Each class implements the
matching Protocol in priva_common.dataplane.client; compose() assembles them
into a DataplaneClient and registers it for the in-process transport.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import bcrypt

from priva_common.crypto import decrypt_value, encrypt_value
from priva_common.dataplane import (
    BindingRecord,
    DataplaneClient,
    QuotaRecord,
    RunPage,
    UNSET,
    set_inprocess_handlers,
)
from priva_common._pagination import compute_cursors, decode_cursor, encode_cursor
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition

from .repo import PgRepo, Repository, SqliteRepo


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _canon_job_type(jt: str | None) -> str:
    # data-spine enforces 'agent_run' as canonical (S0). Accept the legacy alias.
    return "agent_run" if jt in ("agent_run", "scheduled_agent", None) else jt


# --- Account ---------------------------------------------------------------

class AccountService:
    def __init__(self, repo: Repository, settings):
        self.repo = repo
        self.settings = settings

    def _lookup(self, plaintext: str) -> str:
        key = (self.settings.dataspine.api_key_hmac_secret or self.settings.auth.jwt_secret).encode()
        return hmac.new(key, plaintext.encode(), hashlib.sha256).hexdigest()

    def _to_user(self, row: dict | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            username=row["username"],
            password_hash=row["password_hash"],
            role=row["role"],
            api_key=decrypt_value(row["api_key"]) if row.get("api_key") else None,
            account_id=row["account_id"],
            status=row["status"],
            feishu_user_id=row.get("feishu_user_id"),
            feishu_display_name=row.get("feishu_display_name"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get(self, account_id):
        return self._to_user(self.repo.account_get(account_id))

    def get_by_username(self, username):
        return self._to_user(self.repo.account_get_by_username(username))

    def list(self):
        return [self._to_user(r) for r in self.repo.account_list()]

    def create(self, username, password, role="user"):
        if self.repo.account_get_by_username(username) is not None:
            raise ValueError(f"User '{username}' already exists")
        account_id = uuid.uuid4().hex
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        self.repo.account_insert({
            "account_id": account_id,
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "status": "active",
        })
        self.repo.quota_insert({"account_id": account_id})  # seed defaults
        return self.get(account_id)

    def update(self, account_id, *, password=None, role=None, api_key=UNSET,
               status=None, feishu_user_id=UNSET, feishu_display_name=UNSET):
        fields: dict = {}
        if password is not None:
            fields["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if role is not None:
            fields["role"] = role
        if status is not None:
            fields["status"] = status
        if api_key is not UNSET:
            if api_key is None:
                fields["api_key"] = None
                fields["api_key_lookup"] = None
            else:
                fields["api_key"] = encrypt_value(api_key)
                fields["api_key_lookup"] = self._lookup(api_key)
        if feishu_user_id is not UNSET:
            fields["feishu_user_id"] = feishu_user_id
        if feishu_display_name is not UNSET:
            fields["feishu_display_name"] = feishu_display_name
        if self.repo.account_get(account_id) is None:
            raise ValueError(f"account '{account_id}' not found")
        self.repo.account_update(account_id, fields)
        return self.get(account_id)

    def delete(self, account_id):
        self.repo.account_delete(account_id)

    def verify_password(self, username, password):
        row = self.repo.account_get_by_username(username)
        if row is None:
            return False
        return bcrypt.checkpw(password.encode(), row["password_hash"].encode())

    def find_by_api_key(self, api_key):
        if not api_key:
            return None
        return self._to_user(self.repo.account_find_by_api_key_lookup(self._lookup(api_key)))

    def count_admins(self):
        return self.repo.account_count_admins()

    def find_by_feishu_user_id(self, feishu_user_id):
        return self._to_user(self.repo.account_find_by_feishu(feishu_user_id))

    def has_users(self):
        return self.repo.table_count("account") > 0


# --- Binding ---------------------------------------------------------------

class BindingService:
    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _to_binding(row: dict | None) -> BindingRecord | None:
        if row is None:
            return None
        return BindingRecord(
            binding_id=row["binding_id"],
            account_id=row["account_id"],
            session_uuid=row["session_uuid"],
            first_run_done=bool(row["first_run_done"]),
            feishu_chat_id=row.get("feishu_chat_id"),
            bound_at=row.get("bound_at"),
            rebound_at=row.get("rebound_at"),
        )

    def bind(self, account_id, session_uuid, feishu_chat_id=None):
        binding_id = uuid.uuid4().hex
        self.repo.binding_insert({
            "binding_id": binding_id,
            "account_id": account_id,
            "session_uuid": session_uuid,
            "first_run_done": 0,
            "feishu_chat_id": feishu_chat_id,
        })
        return self._to_binding(self.repo.binding_get(binding_id))

    def rebind(self, account_id, session_uuid, feishu_chat_id=None):
        self.repo.binding_rebind(account_id, session_uuid, feishu_chat_id, _now_iso())
        return self._to_binding(self.repo.binding_get_by_account(account_id))

    def claim_first_run_im(self, binding_id):
        return self.repo.binding_claim_first_run(binding_id)

    def get_binding(self, binding_id):
        return self._to_binding(self.repo.binding_get(binding_id))

    def list_bindings(self, account_id):
        return [self._to_binding(r) for r in self.repo.binding_list_by_account(account_id)]


# --- Quota -----------------------------------------------------------------

class QuotaService:
    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _to_quota(row: dict | None) -> QuotaRecord | None:
        if row is None:
            return None
        return QuotaRecord(
            account_id=row["account_id"],
            tier=row["tier"],
            max_concurrent_sessions=row["max_concurrent_sessions"],
            idle_grace_seconds=row["idle_grace_seconds"],
            updated_at=row.get("updated_at"),
        )

    def get(self, account_id):
        return self._to_quota(self.repo.quota_get(account_id))

    def ensure(self, account_id):
        self.repo.quota_insert({"account_id": account_id})  # OR IGNORE
        return self.get(account_id)

    def set(self, account_id, *, tier=None, max_concurrent_sessions=None, idle_grace_seconds=None):
        self.repo.quota_insert({"account_id": account_id})  # ensure row exists
        fields = {}
        if tier is not None:
            fields["tier"] = tier
        if max_concurrent_sessions is not None:
            fields["max_concurrent_sessions"] = max_concurrent_sessions
        if idle_grace_seconds is not None:
            fields["idle_grace_seconds"] = idle_grace_seconds
        self.repo.quota_update(account_id, fields)
        return self.get(account_id)


# --- Scheduler -------------------------------------------------------------

class SchedulerService:
    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _job_row(account_id: str, defn: ScheduledJobDefinition) -> dict:
        jt = _canon_job_type(defn.job_config.job_type if defn.job_config else None)
        job_config = None
        if defn.job_config:
            jc = defn.job_config.model_dump(mode="json")
            jc["job_type"] = jt  # keep the blob's discriminator == the column
            job_config = json.dumps(jc)
        return {
            "job_id": defn.id,
            "account_id": account_id,
            "name": defn.name,
            "prompt": defn.prompt or "",
            "trigger": defn.trigger.model_dump_json(),
            "job_type": jt,
            "job_config": job_config,
            "timezone": defn.timezone,
            "model": defn.model,
            "status": defn.status,
        }

    @staticmethod
    def _to_job(row: dict | None) -> ScheduledJobDefinition | None:
        if row is None:
            return None
        return ScheduledJobDefinition.model_validate({
            "id": row["job_id"],
            "name": row["name"],
            "prompt": row["prompt"],
            "trigger": json.loads(row["trigger"]),
            "timezone": row["timezone"],
            "status": row["status"],
            "model": row["model"],
            "job_config": json.loads(row["job_config"]) if row["job_config"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    @staticmethod
    def _to_run(row: dict) -> JobRunRecord:
        return JobRunRecord(
            run_id=row["run_id"],
            job_id=row["job_id"] or "",
            job_name=row["job_name"],
            username="",  # repo stores account_id; the R1 adapter fills username
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            is_error=bool(row["is_error"]),
            error_message=row["error_message"],
            num_turns=row["num_turns"],
            result_summary=row["result_summary"],
            session_id=row["session_id"],
        )

    def create_job(self, account_id, defn):
        self.repo.job_insert(self._job_row(account_id, defn))
        return self.get_job(defn.id)

    def get_job(self, job_id):
        return self._to_job(self.repo.job_get(job_id))

    def update_job(self, job_id, defn):
        row = self._job_row(self.repo.job_get(job_id)["account_id"], defn) if self.repo.job_get(job_id) else None
        if row is None:
            return None
        fields = {k: v for k, v in row.items() if k not in ("job_id", "account_id")}
        self.repo.job_update(job_id, fields)
        return self.get_job(job_id)

    def delete_job(self, job_id):
        return self.repo.job_delete(job_id)

    def list_jobs(self, account_id):
        return [self._to_job(r) for r in self.repo.job_list_by_account(account_id)]

    def list_active_jobs(self):
        return [(r["account_id"], self._to_job(r)) for r in self.repo.job_list_active()]

    def set_job_status(self, job_id, status):
        self.repo.job_update(job_id, {"status": status})
        return self.get_job(job_id)

    def start_run(self, account_id, record: JobRunRecord):
        self.repo.run_insert({
            "run_id": record.run_id,
            "job_id": record.job_id or None,
            "job_name": record.job_name,
            "account_id": account_id,
            "session_id": record.session_id,
            "started_at": _iso(record.started_at),
            "status": record.status,
            "is_error": int(record.is_error),
            "duration_ms": record.duration_ms,
            "error_message": record.error_message,
            "num_turns": record.num_turns,
            "result_summary": record.result_summary,
        })
        return self._to_run(self.repo.run_get(record.run_id))

    def finish_run(self, record: JobRunRecord):
        self.repo.run_update(record.run_id, {
            "finished_at": _iso(record.finished_at),
            "status": record.status,
            "duration_ms": record.duration_ms,
            "is_error": int(record.is_error),
            "error_message": record.error_message,
            "num_turns": record.num_turns,
            "result_summary": record.result_summary,
        })
        return self._to_run(self.repo.run_get(record.run_id))

    def list_runs(self, account_id, *, limit=50, before=None, after=None, job_id=None, status=None):
        before_cur = decode_cursor(before) if before else None
        after_cur = decode_cursor(after) if after else None
        rows, has_more = self.repo.run_list(
            account_id, limit=limit, before=before_cur, after=after_cur, job_id=job_id, status=status
        )
        next_cursor, prev_cursor = compute_cursors(
            rows, before_cur, after_cur, has_more,
            iso=lambda r: r["started_at"], rid=lambda r: r["run_id"],
        )
        total = None if (job_id or status) else self.repo.run_count(account_id)
        return RunPage(
            runs=[self._to_run(r) for r in rows],
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            total=total,
        )

    def delete_runs_before(self, account_id, cutoff_date):
        return self.repo.run_delete_before(account_id, cutoff_date)


# --- Admin -----------------------------------------------------------------

class AdminService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def healthz(self):
        return "ok"

    def readyz(self):
        try:
            self.repo.table_count("account")
            return True, "ok"
        except Exception as e:  # pragma: no cover
            return False, str(e)

    def stats(self):
        return {
            "accounts": self.repo.table_count("account"),
            "jobs": self.repo.table_count("scheduled_job"),
            "runs": self.repo.table_count("job_run_record"),
        }


# --- composition -----------------------------------------------------------

def build_repo(settings) -> Repository:
    ds = settings.dataspine
    if ds.backend == "postgres":
        return PgRepo(ds.grpc_dsn or "")  # raises NotImplementedError
    return SqliteRepo(ds.sqlite_path)


def build_inprocess_client(repo: Repository, settings) -> DataplaneClient:
    return DataplaneClient(
        accounts=AccountService(repo, settings),
        bindings=BindingService(repo),
        quota=QuotaService(repo),
        scheduler=SchedulerService(repo),
        admin=AdminService(repo),
    )


def compose(settings=None) -> DataplaneClient:
    """Build repo + service impls and register them as the in-process client."""
    from priva_common.config import get_settings

    s = settings or get_settings()
    client = build_inprocess_client(build_repo(s), s)
    set_inprocess_handlers(client)
    return client

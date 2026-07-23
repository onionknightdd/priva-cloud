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
from datetime import datetime, timedelta, timezone

import bcrypt

from priva_common.crypto import decrypt_value, encrypt_value
from priva_common.dataplane import (
    BindingRecord,
    ChannelPlatformConfigRecord,
    DataplaneClient,
    FeishuChannelConfigRecord,
    FeishuSecretRecord,
    HookPolicyRecord,
    PendingRegistrationRecord,
    QuotaRecord,
    ResourceSpecRecord,
    RunnerDefaultsRecord,
    RunPage,
    UNSET,
    set_inprocess_handlers,
)
from priva_common.hook_seeds import HOOK_SEEDS, content_hash as hook_content_hash
from priva_common._pagination import compute_cursors, decode_cursor
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
            agent_runner_type=row.get("agent_runner_type") or "auto_scale",
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

    def create(self, username, password="", role="user", agent_runner_type="auto_scale",
               password_hash=None):
        if self.repo.account_get_by_username(username) is not None:
            raise ValueError(f"User '{username}' already exists")
        account_id = uuid.uuid4().hex
        # password_hash is supplied directly by the approval path (the plaintext was
        # bcrypted at registration); otherwise bcrypt the given plaintext here.
        pw_hash = password_hash or bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        self.repo.account_insert({
            "account_id": account_id,
            "username": username,
            "password_hash": pw_hash,
            "role": role,
            "status": "active",
            "agent_runner_type": agent_runner_type or "auto_scale",
        })
        self.repo.quota_insert({"account_id": account_id})  # seed defaults
        return self.get(account_id)

    def update(self, account_id, *, password=None, role=None, api_key=UNSET,
               status=None, agent_runner_type=None, feishu_user_id=UNSET, feishu_display_name=UNSET):
        fields: dict = {}
        if password is not None:
            fields["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if role is not None:
            fields["role"] = role
        if status is not None:
            fields["status"] = status
        if agent_runner_type is not None:
            fields["agent_runner_type"] = agent_runner_type
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
            chat_type=row.get("chat_type") or "",
            chat_name=row.get("chat_name") or "",
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
        # Keyed by (account, chat) — per-chat sessions (feat_feishu_DM.md §5.2).
        self.repo.binding_rebind(account_id, session_uuid, feishu_chat_id, _now_iso())
        return self._to_binding(self.repo.binding_get_by_account_chat(account_id, feishu_chat_id))

    def set_display(self, account_id, feishu_chat_id, *, chat_type="", chat_name=""):
        """Stamp/refresh a chat's display metadata (connector, per inbound message).
        No-op UPDATE when the row doesn't exist yet — the connector stamps after
        commit_session created it."""
        self.repo.binding_set_display(account_id, feishu_chat_id, chat_type, chat_name)
        return self._to_binding(self.repo.binding_get_by_account_chat(account_id, feishu_chat_id))

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

    def _resolve_job_id(self, job_id):
        # FK-safe: a run may reference a job that never existed or was deleted
        # mid-flight; store NULL rather than violating the FK (matches migrate).
        return job_id if (job_id and self.repo.job_get(job_id)) else None

    def start_run(self, account_id, record: JobRunRecord):
        self.repo.run_insert({
            "run_id": record.run_id,
            "job_id": self._resolve_job_id(record.job_id),
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

    def record_run(self, account_id, record: JobRunRecord):
        # Full-snapshot upsert (mirrors the monolith's append-the-whole-record).
        self.repo.run_upsert({
            "run_id": record.run_id,
            "job_id": self._resolve_job_id(record.job_id),
            "job_name": record.job_name,
            "account_id": account_id,
            "session_id": record.session_id,
            "started_at": _iso(record.started_at),
            "finished_at": _iso(record.finished_at),
            "status": record.status,
            "duration_ms": record.duration_ms,
            "is_error": int(record.is_error),
            "error_message": record.error_message,
            "num_turns": record.num_turns,
            "result_summary": record.result_summary,
        })
        return self._to_run(self.repo.run_get(record.run_id))

    def get_run(self, account_id, run_id):
        row = self.repo.run_get(run_id)
        if row is None or row["account_id"] != account_id:
            return None  # ownership-safe
        return self._to_run(row)

    def get_latest_run(self, account_id, job_id):
        row = self.repo.run_get_latest(account_id, job_id)
        return self._to_run(row) if row else None

    def finish_run(self, record: JobRunRecord):
        fields = {
            "finished_at": _iso(record.finished_at),
            "status": record.status,
            "duration_ms": record.duration_ms,
            "is_error": int(record.is_error),
            "error_message": record.error_message,
            "num_turns": record.num_turns,
            "result_summary": record.result_summary,
        }
        # The pod learns the CLI session id only after dispatch (StartRun has
        # none), so the outcome write carries it — conditional, never clobbers.
        if record.session_id:
            fields["session_id"] = record.session_id
        self.repo.run_update(record.run_id, fields)
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

    def claim_fire(self, job_id, fire_epoch, claimed_by):
        # The leaderless exactly-once claim (scheduler-implementation-design D5):
        # INSERT-wins on job_fire's composite PK; losers (and fires of deleted
        # jobs) get False.
        return self.repo.fire_claim(job_id, int(fire_epoch), claimed_by)

    def prune_fires_before(self, cutoff):
        return self.repo.fire_prune_before(cutoff)


# --- Admin -----------------------------------------------------------------

class AdminService:
    def __init__(self, repo: Repository, settings=None):
        self.repo = repo
        # Self-reported storage backend (surfaces in Stats → the admin System Map).
        self.backend = settings.dataspine.backend if settings else "sqlite"

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
            "backend": self.backend,
        }


# --- ResourceSpec ----------------------------------------------------------

class ResourceSpecService:
    """Per-account agent-runner pod sizing (cpu/memory/volume)."""

    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _to_record(row: dict | None) -> ResourceSpecRecord | None:
        if row is None:
            return None
        return ResourceSpecRecord(
            account_id=row["account_id"],
            cpu_cores=float(row["cpu_cores"]),
            memory_mb=int(row["memory_mb"]),
            volume_gb=int(row["volume_gb"]),
            updated_at=row.get("updated_at"),
        )

    def get(self, account_id):
        return self._to_record(self.repo.resource_spec_get(account_id))

    def set(self, account_id, *, cpu_cores=None, memory_mb=None, volume_gb=None):
        fields: dict = {}
        if cpu_cores is not None:
            fields["cpu_cores"] = float(cpu_cores)
        if memory_mb is not None:
            fields["memory_mb"] = int(memory_mb)
        if volume_gb is not None:
            fields["volume_gb"] = int(volume_gb)
        return self._to_record(self.repo.resource_spec_upsert(account_id, fields))

    def list(self):
        return [self._to_record(r) for r in self.repo.resource_spec_list()]


# --- FeishuChannelConfig ----------------------------------------------------

class FeishuChannelConfigService:
    """Per-account Feishu bot config (Model B). Three role-scoped setters mirror the
    wire's separate Set RPCs. app_secret is Fernet-encrypted at set and NEVER
    returned in cleartext — the record exposes only ``has_app_secret``. The
    connection ``desired_digest`` (app_id + secret fingerprint + enable gates +
    domain) is recomputed on every desired write so the connector can teardown/
    re-arm the WS only when the connection identity actually changes; status
    write-back goes through a separate path that never touches updated_at/digest."""

    # Desired columns whose change requires the connector to teardown/re-arm the WS.
    # owner_union_id is included so bind/unbind refreshes the worker's cfg snapshot
    # (feat_feishu_DM.md ruling #3); the pending link-code cols are deliberately NOT
    # here — minting a code must not bounce the connection. The group-chat gate rides
    # as the COMPOSED effective bit (user opt-in AND NOT global kill switch): the user
    # toggle only re-arms while the switch actually changes behaviour, and an admin
    # global flip re-arms every affected row via recompute_digests().
    _DIGEST_COLS = ("app_id", "app_secret_enc", "user_enabled", "admin_disabled", "domain",
                    "owner_union_id", "group_chat_enabled")

    # Owner link-code (feat_feishu_DM.md §4.1): 6-char Crockford base32 (no I/L/O/U),
    # single-use, 10-minute TTL, only the SHA-256 hex is stored.
    _LINK_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    _LINK_CODE_LEN = 6
    _LINK_CODE_TTL_SECONDS = 600

    def __init__(self, repo: Repository):
        self.repo = repo

    def _group_globally_disabled(self) -> bool:
        row = self.repo.channel_platform_get()
        return bool(row and row.get("group_chat_disabled"))

    @staticmethod
    def _digest(merged: dict, group_disabled: bool) -> str:
        payload = json.dumps(
            [
                merged.get("app_id") or "",
                merged.get("app_secret_enc") or "",   # ciphertext fingerprint; changes on rotate/clear
                int(merged.get("user_enabled") or 0),
                int(merged.get("admin_disabled") or 0),
                merged.get("domain") or "feishu",
                merged.get("owner_union_id") or "",
                # composed effective_group bit — recompute_digests() re-derives this
                # for every row when the admin flips the global switch
                int(bool(merged.get("group_chat_enabled")) and not group_disabled),
            ],
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @staticmethod
    def _to_record(row: dict | None, group_disabled: bool = False) -> FeishuChannelConfigRecord | None:
        if row is None:
            return None
        has_secret = bool(row.get("app_secret_enc"))
        user_enabled = bool(row.get("user_enabled"))
        admin_disabled = bool(row.get("admin_disabled"))
        effective = user_enabled and not admin_disabled and bool(row.get("app_id")) and has_secret
        group_enabled = bool(row.get("group_chat_enabled"))
        return FeishuChannelConfigRecord(
            account_id=row["account_id"],
            app_id=row.get("app_id") or None,
            has_app_secret=has_secret,
            app_secret_updated_at=row.get("app_secret_updated_at"),
            user_enabled=user_enabled,
            admin_disabled=admin_disabled,
            effective_enabled=effective,
            single_chat_access_mode=row.get("single_chat_access_mode") or "owner_only",
            allowed_union_ids=row.get("allowed_union_ids") or "[]",
            group_chat_enabled=group_enabled,
            effective_group_enabled=group_enabled and not group_disabled,
            welcome_message=row.get("welcome_message") or "",
            reject_message=row.get("reject_message") or "",
            model=row.get("model") or None,
            max_queue_size=int(row.get("max_queue_size") or 3),
            enable_permission_feedback=bool(row.get("enable_permission_feedback")),
            feedback_timeout_seconds=int(row.get("feedback_timeout_seconds") or 180),
            domain=row.get("domain") or "feishu",
            owner_union_id=row.get("owner_union_id") or "",
            owner_open_id=row.get("owner_open_id") or "",
            owner_bound_at=row.get("owner_bound_at"),
            conn_status=row.get("conn_status") or "disabled",
            last_error_code=row.get("last_error_code"),
            last_error_message=row.get("last_error_message"),
            last_connected_at=row.get("last_connected_at"),
            status_updated_at=row.get("status_updated_at"),
            desired_digest=row.get("desired_digest"),
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at"),
        )

    def get(self, account_id):
        return self._to_record(self.repo.feishu_get(account_id), self._group_globally_disabled())

    def _write_desired(self, account_id, fields: dict):
        gd = self._group_globally_disabled()
        if not fields:
            return self._to_record(self.repo.feishu_get(account_id), gd)
        merged = {**(self.repo.feishu_get(account_id) or {}), **fields}
        fields["desired_digest"] = self._digest(merged, gd)
        return self._to_record(self.repo.feishu_upsert(account_id, fields), gd)

    def set_user(self, account_id, *, app_id=None, app_secret=UNSET, user_enabled=None,
                 single_chat_access_mode=None, allowed_union_ids=None, welcome_message=None,
                 reject_message=None, model=None, max_queue_size=None,
                 enable_permission_feedback=None, feedback_timeout_seconds=None,
                 domain=None, group_chat_enabled=None, updated_by=""):
        fields: dict = {}
        if app_id is not None:
            fields["app_id"] = app_id
        if app_secret is not UNSET:
            if app_secret:                                    # non-empty => set/rotate
                fields["app_secret_enc"] = encrypt_value(app_secret)
                fields["app_secret_updated_at"] = _now_iso()
            else:                                             # "" => clear
                fields["app_secret_enc"] = None
                fields["app_secret_updated_at"] = None
        if user_enabled is not None:
            fields["user_enabled"] = 1 if user_enabled else 0
        if single_chat_access_mode is not None:
            fields["single_chat_access_mode"] = single_chat_access_mode
        if allowed_union_ids is not None:
            fields["allowed_union_ids"] = allowed_union_ids
        if welcome_message is not None:
            fields["welcome_message"] = welcome_message
        if reject_message is not None:
            fields["reject_message"] = reject_message
        if model is not None:
            fields["model"] = model
        if max_queue_size is not None:
            fields["max_queue_size"] = int(max_queue_size)
        if enable_permission_feedback is not None:
            fields["enable_permission_feedback"] = 1 if enable_permission_feedback else 0
        if feedback_timeout_seconds is not None:
            fields["feedback_timeout_seconds"] = int(feedback_timeout_seconds)
        if domain is not None:
            fields["domain"] = domain
        if group_chat_enabled is not None:
            fields["group_chat_enabled"] = 1 if group_chat_enabled else 0
        if updated_by:
            fields["updated_by"] = updated_by
        return self._write_desired(account_id, fields)

    def set_admin(self, account_id, *, admin_disabled=None, updated_by=""):
        fields: dict = {}
        if admin_disabled is not None:
            fields["admin_disabled"] = 1 if admin_disabled else 0
        if updated_by:
            fields["updated_by"] = updated_by
        return self._write_desired(account_id, fields)

    def set_status(self, account_id, *, conn_status=None, last_error_code=None,
                   last_error_message=None, last_connected_at=None):
        fields: dict = {}
        if conn_status is not None:
            fields["conn_status"] = conn_status
        if last_error_code is not None:
            fields["last_error_code"] = int(last_error_code)
        if last_error_message is not None:
            fields["last_error_message"] = last_error_message
        if last_connected_at is not None:
            fields["last_connected_at"] = last_connected_at
        return self._to_record(self.repo.feishu_status_update(account_id, fields),
                               self._group_globally_disabled())

    def list(self):
        gd = self._group_globally_disabled()
        return [self._to_record(r, gd) for r in self.repo.feishu_list()]

    def list_effective(self):
        gd = self._group_globally_disabled()
        return [self._to_record(r, gd) for r in self.repo.feishu_list_effective()]

    def recompute_digests(self) -> int:
        """Re-derive desired_digest for every row against the CURRENT global
        group-chat switch (called by ChannelPlatformConfigService.set on flip).
        Only rows whose digest actually changes are rewritten — i.e. exactly the
        accounts whose effective_group_enabled flipped — so the connector's poll
        re-arms them and nothing else. Returns the number of rows touched."""
        gd = self._group_globally_disabled()
        touched = 0
        for row in self.repo.feishu_list():
            fresh = self._digest(row, gd)
            if fresh != (row.get("desired_digest") or ""):
                self.repo.feishu_upsert(row["account_id"], {"desired_digest": fresh})
                touched += 1
        return touched

    # --- owner link-code (feat_feishu_DM.md §4) ----------------------------
    def create_link_code(self, account_id) -> tuple[str, str]:
        """Mint a single-use owner-binding code (control-panel route, behind the
        platform login). Overwrites any previous pending code; only the SHA-256 is
        stored. Returns (plaintext_code, expires_at) — the plaintext exists only in
        this response and the user's screen."""
        import secrets as _secrets
        code = "".join(_secrets.choice(self._LINK_CODE_ALPHABET)
                       for _ in range(self._LINK_CODE_LEN))
        expires = (datetime.now(timezone.utc)
                   + timedelta(seconds=self._LINK_CODE_TTL_SECONDS)).isoformat()
        self._write_desired(account_id, {
            "link_code_hash": hashlib.sha256(code.encode()).hexdigest(),
            "link_code_expires_at": expires,
        })
        return code, expires

    def bind_owner_with_code(self, account_id, code, union_id, open_id) -> bool:
        """CONNECTOR route: atomically validate the code (hashed, constant-time,
        unexpired) and bind the sender as owner, clearing the code (single-use).
        Failure reasons are deliberately not distinguished (no existence oracle)."""
        row = self.repo.feishu_get(account_id)
        if row is None or not code or not union_id:
            return False
        stored = row.get("link_code_hash") or ""
        expires = row.get("link_code_expires_at") or ""
        if not stored or not expires:
            return False
        presented = hashlib.sha256(code.strip().upper().encode()).hexdigest()
        if not hmac.compare_digest(stored, presented):
            return False
        try:
            if datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        self._write_desired(account_id, {
            "owner_union_id": union_id,
            "owner_open_id": open_id or "",
            "owner_bound_at": _now_iso(),
            "link_code_hash": None,
            "link_code_expires_at": None,
        })
        return True

    def unbind_owner(self, account_id, *, updated_by=""):
        """USER route (control-panel): drop the owner binding — the gate falls back
        to allow-all (unbound semantics, ruling #1). Also discards any pending code."""
        fields = {
            "owner_union_id": "",
            "owner_open_id": "",
            "owner_bound_at": None,
            "link_code_hash": None,
            "link_code_expires_at": None,
        }
        if updated_by:
            fields["updated_by"] = updated_by
        return self._write_desired(account_id, fields)

    def get_secret(self, account_id) -> FeishuSecretRecord | None:
        """Connector-only: decrypt and return the plaintext app_secret. app_secret is
        "" when unset OR when the ciphertext fails to decrypt (decrypt_value → None on
        key rotation/corruption) — the connector treats "" as unusable and parks the
        app with conn_status=error. Never logged."""
        row = self.repo.feishu_get(account_id)
        if row is None:
            return None
        enc = row.get("app_secret_enc")
        return FeishuSecretRecord(
            account_id=row["account_id"],
            app_id=row.get("app_id") or None,
            app_secret=(decrypt_value(enc) or "") if enc else "",
            domain=row.get("domain") or "feishu",
        )


# --- ChannelPlatformConfig ---------------------------------------------------

class ChannelPlatformConfigService:
    """ADMIN-only platform-wide channel settings — a single row (id=1), same
    pattern as runner_defaults but with static defaults (all-off), so no seeding
    is needed: a missing row simply reads as the defaults. Flipping the global
    group-chat switch recomputes every feishu row's desired_digest (the composed
    effective_group bit lives in the digest) so the connector re-arms exactly the
    accounts whose effective behaviour changed (feat_feishu_DM.md §5.1)."""

    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _to_record(row: dict | None) -> ChannelPlatformConfigRecord:
        if row is None:
            return ChannelPlatformConfigRecord()
        return ChannelPlatformConfigRecord(
            group_chat_disabled=bool(row.get("group_chat_disabled")),
            updated_by=row.get("updated_by") or "",
            updated_at=row.get("updated_at"),
        )

    def get(self) -> ChannelPlatformConfigRecord:
        return self._to_record(self.repo.channel_platform_get())

    def set(self, *, group_chat_disabled=None, updated_by="") -> ChannelPlatformConfigRecord:
        fields: dict = {}
        if group_chat_disabled is not None:
            fields["group_chat_disabled"] = 1 if group_chat_disabled else 0
        if updated_by:
            fields["updated_by"] = updated_by
        if not fields:
            return self.get()
        rec = self._to_record(self.repo.channel_platform_upsert(fields))
        if group_chat_disabled is not None:
            FeishuChannelConfigService(self.repo).recompute_digests()
        return rec


# --- RunnerDefaults ---------------------------------------------------------

class RunnerDefaultsService:
    """Platform-wide GLOBAL defaults for per-account agent-runner pods. A single
    row, seeded from the cluster settings (`KubernetesSettings`) on first access
    so a fresh DB reports the same values the operator's env fallback would."""

    def __init__(self, repo: Repository, settings):
        self.repo = repo
        self.settings = settings

    def _seed_values(self) -> dict:
        k = self.settings.kubernetes
        return {
            "idle_grace_seconds": int(k.idle_grace_seconds),
            "min_alive_after_wake_seconds": int(k.min_alive_after_wake_seconds),
            "cpu_cores": float(k.runner_cpu_cores),
            "memory_mb": int(k.runner_memory_mb),
            "storage_gb": int(k.runner_storage_gb),
            "runner_image": str(k.runner_image),
            "terminal_resource_percent": int(k.terminal_resource_percent),
            "terminal_max_sessions": int(k.terminal_max_sessions),
            "terminal_idle_timeout_seconds": int(k.terminal_idle_timeout_seconds),
            "terminal_max_lifetime_seconds": int(k.terminal_max_lifetime_seconds),
            "terminal_scale_down_grace_seconds": int(k.terminal_scale_down_grace_seconds),
        }

    @staticmethod
    def _to_record(row: dict) -> RunnerDefaultsRecord:
        return RunnerDefaultsRecord(
            idle_grace_seconds=int(row["idle_grace_seconds"]),
            min_alive_after_wake_seconds=int(row["min_alive_after_wake_seconds"]),
            cpu_cores=float(row["cpu_cores"]),
            memory_mb=int(row["memory_mb"]),
            storage_gb=int(row["storage_gb"]),
            runner_image=row["runner_image"],
            terminal_resource_percent=int(row["terminal_resource_percent"]),
            terminal_max_sessions=int(row["terminal_max_sessions"]),
            terminal_idle_timeout_seconds=int(row["terminal_idle_timeout_seconds"]),
            terminal_max_lifetime_seconds=int(row["terminal_max_lifetime_seconds"]),
            terminal_scale_down_grace_seconds=int(row["terminal_scale_down_grace_seconds"]),
            updated_at=row.get("updated_at"),
        )

    def get(self) -> RunnerDefaultsRecord:
        row = self.repo.runner_defaults_get()
        if row is None:
            row = self.repo.runner_defaults_seed(self._seed_values())
        return self._to_record(row)

    def set(self, *, idle_grace_seconds=None, min_alive_after_wake_seconds=None,
            cpu_cores=None, memory_mb=None, storage_gb=None,
            runner_image=None, terminal_resource_percent=None, terminal_max_sessions=None,
            terminal_idle_timeout_seconds=None, terminal_max_lifetime_seconds=None,
            terminal_scale_down_grace_seconds=None) -> RunnerDefaultsRecord:
        if self.repo.runner_defaults_get() is None:  # ensure the single row exists
            self.repo.runner_defaults_seed(self._seed_values())
        fields: dict = {}
        if idle_grace_seconds is not None:
            fields["idle_grace_seconds"] = int(idle_grace_seconds)
        if min_alive_after_wake_seconds is not None:
            fields["min_alive_after_wake_seconds"] = int(min_alive_after_wake_seconds)
        if cpu_cores is not None:
            fields["cpu_cores"] = float(cpu_cores)
        if memory_mb is not None:
            fields["memory_mb"] = int(memory_mb)
        if storage_gb is not None:
            fields["storage_gb"] = int(storage_gb)
        if runner_image is not None:
            fields["runner_image"] = str(runner_image)
        if terminal_resource_percent is not None:
            fields["terminal_resource_percent"] = int(terminal_resource_percent)
        if terminal_max_sessions is not None:
            fields["terminal_max_sessions"] = int(terminal_max_sessions)
        if terminal_idle_timeout_seconds is not None:
            fields["terminal_idle_timeout_seconds"] = int(terminal_idle_timeout_seconds)
        if terminal_max_lifetime_seconds is not None:
            fields["terminal_max_lifetime_seconds"] = int(terminal_max_lifetime_seconds)
        if terminal_scale_down_grace_seconds is not None:
            fields["terminal_scale_down_grace_seconds"] = int(terminal_scale_down_grace_seconds)
        return self._to_record(self.repo.runner_defaults_upsert(fields))


# --- HookPolicy ---------------------------------------------------------------

class HookPolicyService:
    """Admin-stored hooks (the admin "Runtime" panel). Predefined rows are the
    legacy builtin hooks, seeded insert-if-absent at construction (data-spine
    startup); a seed_version bump auto-refreshes rows still carrying a known
    (unedited) body hash and leaves admin-edited rows alone — the admin UI shows
    a diff banner for those instead.

    Error contract (server maps to gRPC codes): upsert(expect="create") raises
    ValueError on id collision; expect="update" / delete raise LookupError when
    the row is missing; delete raises PermissionError for predefined rows."""

    # Fields the API may write. predefined / seed_version are seeder-owned;
    # content_hash is always derived from script_body server-side.
    _WRITABLE = ("hook_type", "name", "description", "events", "matcher",
                 "timeout_seconds", "interpreter", "script_body", "url",
                 "headers_json", "allowed_env_vars", "mcp_server", "mcp_tool",
                 "enabled", "enforced", "enforced_events", "default_on", "target",
                 "updated_by")

    def __init__(self, repo: Repository):
        self.repo = repo
        self._seed()

    @staticmethod
    def _to_record(row: dict | None) -> HookPolicyRecord | None:
        if row is None:
            return None

        def _json_list(raw) -> list[str]:
            try:
                v = json.loads(raw) if raw else []
                return [str(x) for x in v] if isinstance(v, list) else []
            except (ValueError, TypeError):
                return []

        return HookPolicyRecord(
            id=row["id"],
            hook_type=row["hook_type"],
            name=row["name"],
            description=row["description"],
            events=_json_list(row["events"]),
            matcher=row["matcher"],
            timeout_seconds=int(row["timeout_seconds"]),
            interpreter=row["interpreter"],
            script_body=row["script_body"],
            content_hash=row["content_hash"],
            url=row["url"],
            headers_json=row["headers_json"],
            allowed_env_vars=_json_list(row["allowed_env_vars"]),
            mcp_server=row["mcp_server"],
            mcp_tool=row["mcp_tool"],
            enabled=bool(row["enabled"]),
            enforced=bool(row["enforced"]),
            default_on=bool(row["default_on"]),
            predefined=bool(row["predefined"]),
            seed_version=int(row["seed_version"]),
            target=row["target"],
            updated_at=row.get("updated_at"),
            updated_by=row.get("updated_by") or "",
            enforced_events=_json_list(row.get("enforced_events")),
        )

    @staticmethod
    def _fields_from(policy: HookPolicyRecord, names) -> dict:
        fields: dict = {}
        for n in names:
            if n == "events":
                fields[n] = json.dumps(list(policy.events))
            elif n == "enforced_events":
                fields[n] = json.dumps(list(policy.enforced_events))
            elif n == "allowed_env_vars":
                fields[n] = json.dumps(list(policy.allowed_env_vars))
            elif n in ("enabled", "enforced", "default_on"):
                fields[n] = int(getattr(policy, n))
            elif n == "timeout_seconds":
                fields[n] = int(policy.timeout_seconds)
            else:
                fields[n] = getattr(policy, n)
        if "script_body" in fields:
            fields["content_hash"] = hook_content_hash(policy.script_body)
        return fields

    def list(self, enabled_only: bool = False) -> list[HookPolicyRecord]:
        return [self._to_record(r) for r in self.repo.hook_policy_list(enabled_only)]

    def get(self, policy_id: str) -> HookPolicyRecord | None:
        return self._to_record(self.repo.hook_policy_get(policy_id))

    @staticmethod
    def _normalize_activation(merged: HookPolicyRecord, mask: list[str]) -> list[str]:
        """Per-event activation invariants, applied on every write.

        ``enforced_events`` (⊆ events) is the source of truth for where the
        hook fires; ``enforced`` is derived from its non-emptiness. Legacy
        clients that only flip the booleans keep working: enforced=true →
        all events, enforced=false (or enabled=false) → none. Returns the
        write mask extended with the derived fields."""
        if "enforced_events" in mask:
            ee = list(merged.enforced_events)
        elif "enforced" in mask:
            ee = list(merged.events) if merged.enforced else []
        elif "enabled" in mask and not merged.enabled:
            ee = []
        else:
            ee = list(merged.enforced_events)
        # Clamp to the (possibly just-edited) event set, preserving its order.
        allowed = set(ee)
        merged.enforced_events = [e for e in merged.events if e in allowed]
        merged.enforced = bool(merged.enforced_events)
        out = list(mask)
        for extra in ("enforced_events", "enforced"):
            if extra not in out:
                out.append(extra)
        if merged.enforced and not merged.enabled:
            merged.enabled = True  # a firing hook is by definition armed
            if "enabled" not in out:
                out.append("enabled")
        return out

    def upsert(self, policy: HookPolicyRecord, *, update_mask=None, expect: str = "") -> HookPolicyRecord:
        existing = self.repo.hook_policy_get(policy.id)
        if expect == "create" and existing is not None:
            raise ValueError(f"hook policy '{policy.id}' already exists")
        if expect == "update" and existing is None:
            raise LookupError(f"hook policy '{policy.id}' not found")
        if existing is None:
            create = policy.model_copy()
            # Legacy create with enforced/enabled set but no per-event list
            # means "all events" (mirrors the pre-per-event behavior).
            if not create.enforced_events and (create.enforced or create.enabled):
                create.enforced_events = list(create.events)
            self._normalize_activation(create, list(self._WRITABLE))
            fields = self._fields_from(create, self._WRITABLE)  # create is always the full row
            fields["id"] = create.id
            fields["predefined"] = 0
            fields["seed_version"] = 0
            self.repo.hook_policy_insert(fields)
        else:
            mask = [f for f in (update_mask or []) if f in self._WRITABLE] or list(self._WRITABLE)
            # Normalization needs the merged row (a partial mask may touch
            # events without enforced_events, or vice versa).
            merged = self._to_record(existing).model_copy(
                update={f: getattr(policy, f) for f in mask})
            mask = self._normalize_activation(merged, mask)
            self.repo.hook_policy_update(policy.id, self._fields_from(merged, mask))
        return self.get(policy.id)

    def delete(self, policy_id: str) -> None:
        row = self.repo.hook_policy_get(policy_id)
        if row is None:
            raise LookupError(f"hook policy '{policy_id}' not found")
        if row["predefined"]:
            raise PermissionError(f"hook policy '{policy_id}' is predefined and cannot be deleted")
        self.repo.hook_policy_delete(policy_id)

    def _seed(self) -> None:
        for seed in HOOK_SEEDS:
            row = self.repo.hook_policy_get(seed.id)
            if row is None:
                try:
                    self.repo.hook_policy_insert({
                        "id": seed.id,
                        "hook_type": "command",
                        "name": seed.name,
                        "description": seed.description,
                        "events": json.dumps(list(seed.events)),
                        "matcher": seed.matcher,
                        "timeout_seconds": seed.timeout_seconds,
                        "interpreter": seed.interpreter,
                        "script_body": seed.script_body,
                        "content_hash": seed.hash,
                        "allowed_env_vars": "[]",
                        "enabled": 1,
                        "enforced": int(seed.enforced),
                        "enforced_events": json.dumps(list(seed.events)) if seed.enforced else "[]",
                        "default_on": int(seed.default_on),
                        "predefined": 1,
                        "seed_version": seed.seed_version,
                        "updated_by": "seed",
                    })
                except Exception:  # lost an insert race with a sibling seeder — fine
                    pass
            elif (int(row["seed_version"] or 0) < seed.seed_version
                  and row["content_hash"] in seed.known_hashes()):
                # Unedited row from an older release → refresh to the shipped seed.
                # Edited rows keep their content; the admin UI offers the diff.
                # A version bump also adopts the seed's default enforcement flags
                # (rev-5 flips block-dangerous-bash / audit-tool-use /
                # require-permission-risky-tools to enforced) — safe here because
                # the content is unedited, and an admin's later flag change (which
                # leaves content_hash intact) is only re-synced on the NEXT bump.
                self.repo.hook_policy_update(seed.id, {
                    "name": seed.name,
                    "description": seed.description,
                    "events": json.dumps(list(seed.events)),
                    "matcher": seed.matcher,
                    "timeout_seconds": seed.timeout_seconds,
                    "interpreter": seed.interpreter,
                    "script_body": seed.script_body,
                    "content_hash": seed.hash,
                    "enabled": 1,
                    "enforced": int(seed.enforced),
                    "enforced_events": json.dumps(list(seed.events)) if seed.enforced else "[]",
                    "default_on": int(seed.default_on),
                    "seed_version": seed.seed_version,
                    "updated_by": "seed-upgrade",
                })


# --- Registration ----------------------------------------------------------

class RegistrationService:
    """Self-service account requests awaiting admin approval. password_hash is the
    bcrypt of the user-chosen password — never returned on list, only on the
    internal get the approval path reads."""

    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _to_record(row: dict | None, *, with_hash: bool = False) -> PendingRegistrationRecord | None:
        if row is None:
            return None
        return PendingRegistrationRecord(
            request_id=row["request_id"],
            username=row["username"],
            display_name=row.get("display_name"),
            runner_type=row["runner_type"],
            cpu_cores=float(row["cpu_cores"]),
            memory_mb=int(row["memory_mb"]),
            volume_gb=int(row["volume_gb"]),
            note=row.get("note"),
            status=row["status"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            password_hash=row["password_hash"] if with_hash else None,
        )

    def create(self, *, username, password_hash, display_name=None, runner_type="auto_scale",
               cpu_cores=1.0, memory_mb=2048, volume_gb=1, note=None):
        request_id = uuid.uuid4().hex
        self.repo.pending_insert({
            "request_id": request_id,
            "username": username,
            "password_hash": password_hash,
            "display_name": display_name,
            "runner_type": runner_type or "auto_scale",
            "cpu_cores": float(cpu_cores),
            "memory_mb": int(memory_mb),
            "volume_gb": int(volume_gb),
            "note": note,
            "status": "pending",
        })
        return self._to_record(self.repo.pending_get(request_id))

    def get_open_by_username(self, username):
        return self._to_record(self.repo.pending_get_open_by_username(username))

    def list(self, status=None):
        return [self._to_record(r) for r in self.repo.pending_list_by_status(status)]

    def get(self, request_id):
        # Includes the password_hash — used by the approval path to mint the account.
        return self._to_record(self.repo.pending_get(request_id), with_hash=True)

    def set_status(self, request_id, status):
        return self._to_record(self.repo.pending_set_status(request_id, status))


# --- composition -----------------------------------------------------------

def build_repo(settings) -> Repository:
    ds = settings.dataspine
    if ds.backend == "postgres":
        if not ds.postgres_dsn:
            raise ValueError(
                "dataspine.backend='postgres' (the default) requires dataspine.postgres_dsn "
                "(env PRIVA_DATASPINE__POSTGRES_DSN), e.g. postgresql://priva:pw@127.0.0.1:5432/priva. "
                "Local dev server: docker run -d --name priva-pg -p 5432:5432 -e POSTGRES_USER=priva "
                "-e POSTGRES_PASSWORD=pw -e POSTGRES_DB=priva postgres:16-alpine. "
                "Legacy sqlite backend: set PRIVA_DATASPINE__BACKEND=sqlite."
            )
        return PgRepo(ds.postgres_dsn)
    return SqliteRepo(ds.sqlite_path)


def mask_dsn(dsn: str) -> str:
    """postgresql://user:password@host/db → postgresql://user:***@host/db (log-safe)."""
    import re

    return re.sub(r"(//[^:/@]+):[^@]+@", r"\1:***@", dsn)


def describe_store(settings) -> str:
    """Log-safe one-liner of where the repo lives (never leaks the DSN password)."""
    ds = settings.dataspine
    if ds.backend == "postgres":
        return f"postgres={mask_dsn(ds.postgres_dsn or '<unset>')}"
    return f"sqlite={ds.sqlite_path}"


def build_inprocess_client(repo: Repository, settings) -> DataplaneClient:
    return DataplaneClient(
        accounts=AccountService(repo, settings),
        bindings=BindingService(repo),
        quota=QuotaService(repo),
        scheduler=SchedulerService(repo),
        admin=AdminService(repo, settings),
        resource_specs=ResourceSpecService(repo),
        runner_defaults=RunnerDefaultsService(repo, settings),
        registrations=RegistrationService(repo),
        hook_policies=HookPolicyService(repo),
        feishu_configs=FeishuChannelConfigService(repo),
        channel_platform=ChannelPlatformConfigService(repo),
    )


def compose(settings=None) -> DataplaneClient:
    """Build repo + service impls and register them as the in-process client."""
    from priva_common.config import get_settings

    s = settings or get_settings()
    client = build_inprocess_client(build_repo(s), s)
    set_inprocess_handlers(client)
    return client

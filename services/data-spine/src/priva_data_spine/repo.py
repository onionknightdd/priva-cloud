"""Storage seam: Repository (ABC) + SqliteRepo + PgRepo.

The repo speaks rows (dicts) and SQL only — no crypto, no DTOs, no UUID minting
(that's the service layer). SqliteRepo serializes all access behind one lock over
a single WAL connection (a simple, correct single-writer model for the alpha).
PgRepo runs the same SQL (PG dialect) over a thread-safe psycopg connection pool
— Postgres handles concurrency natively, so no lock. Both return identically
shaped rows (TEXT ISO timestamps, 0/1 int booleans) so the service layer is
backend-blind.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from . import schema

# --- helpers ---------------------------------------------------------------

_ACCOUNT_COLS = (
    "account_id", "username", "password_hash", "api_key", "api_key_lookup",
    "role", "status", "agent_runner_type", "feishu_user_id", "feishu_display_name",
    "created_at", "updated_at",
)


def _set_clause(fields: dict) -> tuple[str, list]:
    keys = list(fields.keys())
    return ", ".join(f"{k} = ?" for k in keys), [fields[k] for k in keys]


# --- interface -------------------------------------------------------------

class Repository(ABC):
    # account
    @abstractmethod
    def account_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def account_get_by_username(self, username: str) -> dict | None: ...
    @abstractmethod
    def account_find_by_api_key_lookup(self, lookup: str) -> dict | None: ...
    @abstractmethod
    def account_find_by_feishu(self, feishu_user_id: str) -> dict | None: ...
    @abstractmethod
    def account_list(self) -> list[dict]: ...
    @abstractmethod
    def account_count_admins(self) -> int: ...
    @abstractmethod
    def account_insert(self, row: dict) -> None: ...
    @abstractmethod
    def account_update(self, account_id: str, fields: dict) -> None: ...
    @abstractmethod
    def account_delete(self, account_id: str) -> None: ...
    # binding
    @abstractmethod
    def binding_insert(self, row: dict) -> None: ...
    @abstractmethod
    def binding_get(self, binding_id: str) -> dict | None: ...
    @abstractmethod
    def binding_get_by_account(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def binding_list_by_account(self, account_id: str) -> list[dict]: ...
    @abstractmethod
    def binding_claim_first_run(self, binding_id: str) -> bool: ...
    @abstractmethod
    def binding_rebind(self, account_id: str, session_uuid: str, feishu_chat_id: str | None, rebound_at: str) -> None: ...
    # quota
    @abstractmethod
    def quota_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def quota_insert(self, row: dict) -> None: ...
    @abstractmethod
    def quota_update(self, account_id: str, fields: dict) -> None: ...
    # jobs
    @abstractmethod
    def job_insert(self, row: dict) -> None: ...
    @abstractmethod
    def job_get(self, job_id: str) -> dict | None: ...
    @abstractmethod
    def job_update(self, job_id: str, fields: dict) -> None: ...
    @abstractmethod
    def job_delete(self, job_id: str) -> bool: ...
    @abstractmethod
    def job_list_by_account(self, account_id: str) -> list[dict]: ...
    @abstractmethod
    def job_list_active(self) -> list[dict]: ...
    # runs
    @abstractmethod
    def run_insert(self, row: dict) -> None: ...
    @abstractmethod
    def run_upsert(self, row: dict) -> None: ...
    @abstractmethod
    def run_update(self, run_id: str, fields: dict) -> None: ...
    @abstractmethod
    def run_get(self, run_id: str) -> dict | None: ...
    @abstractmethod
    def run_get_latest(self, account_id: str, job_id: str) -> dict | None: ...
    @abstractmethod
    def run_list(self, account_id: str, *, limit: int, before: tuple | None,
                 after: tuple | None, job_id: str | None, status: str | None) -> tuple[list[dict], bool]: ...
    @abstractmethod
    def run_count(self, account_id: str) -> int: ...
    @abstractmethod
    def run_delete_before(self, account_id: str, cutoff_date: str) -> list[str]: ...
    # fires (the exactly-once claim)
    @abstractmethod
    def fire_claim(self, job_id: str, fire_epoch: int, claimed_by: str) -> bool: ...
    @abstractmethod
    def fire_prune_before(self, cutoff: str) -> int: ...
    # resource_spec
    @abstractmethod
    def resource_spec_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def resource_spec_upsert(self, account_id: str, fields: dict) -> dict: ...
    @abstractmethod
    def resource_spec_list(self) -> list[dict]: ...
    # feishu_channel_config
    @abstractmethod
    def feishu_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def feishu_upsert(self, account_id: str, fields: dict) -> dict: ...          # desired cols; stamps updated_at
    @abstractmethod
    def feishu_status_update(self, account_id: str, fields: dict) -> dict | None: ...  # status cols only; NOT updated_at
    @abstractmethod
    def feishu_list(self) -> list[dict]: ...
    @abstractmethod
    def feishu_list_effective(self) -> list[dict]: ...
    # runner_defaults (single row, id=1)
    @abstractmethod
    def runner_defaults_get(self) -> dict | None: ...
    @abstractmethod
    def runner_defaults_seed(self, values: dict) -> dict: ...
    @abstractmethod
    def runner_defaults_upsert(self, fields: dict) -> dict: ...
    # hook_policy
    @abstractmethod
    def hook_policy_list(self, enabled_only: bool = False) -> list[dict]: ...
    @abstractmethod
    def hook_policy_get(self, policy_id: str) -> dict | None: ...
    @abstractmethod
    def hook_policy_insert(self, row: dict) -> None: ...
    @abstractmethod
    def hook_policy_update(self, policy_id: str, fields: dict) -> dict | None: ...
    @abstractmethod
    def hook_policy_delete(self, policy_id: str) -> bool: ...
    # pending_registration
    @abstractmethod
    def pending_insert(self, row: dict) -> None: ...
    @abstractmethod
    def pending_get(self, request_id: str) -> dict | None: ...
    @abstractmethod
    def pending_get_open_by_username(self, username: str) -> dict | None: ...
    @abstractmethod
    def pending_list_by_status(self, status: str | None) -> list[dict]: ...
    @abstractmethod
    def pending_set_status(self, request_id: str, status: str) -> dict | None: ...
    # admin
    @abstractmethod
    def table_count(self, table: str) -> int: ...


# --- SQLite implementation -------------------------------------------------

class SqliteRepo(Repository):
    def __init__(self, path: str):
        self._path = os.path.expanduser(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    def _bootstrap(self) -> None:
        c = self._conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA temp_store=MEMORY")
        schema.create_all(c)
        c.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # low-level
    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

    # account ---------------------------------------------------------------
    def account_get(self, account_id):
        return self._one("SELECT * FROM account WHERE account_id = ?", (account_id,))

    def account_get_by_username(self, username):
        return self._one("SELECT * FROM account WHERE username = ?", (username,))

    def account_find_by_api_key_lookup(self, lookup):
        return self._one("SELECT * FROM account WHERE api_key_lookup = ?", (lookup,))

    def account_find_by_feishu(self, feishu_user_id):
        return self._one("SELECT * FROM account WHERE feishu_user_id = ?", (feishu_user_id,))

    def account_list(self):
        return self._all("SELECT * FROM account ORDER BY created_at ASC")

    def account_count_admins(self):
        return self._one("SELECT COUNT(*) AS n FROM account WHERE role = 'admin'")["n"]

    def account_insert(self, row):
        cols = [c for c in _ACCOUNT_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT INTO account ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def account_update(self, account_id, fields):
        fields = {k: v for k, v in fields.items() if k != "updated_at"}
        # updated_at is always stamped server-side (a SQL expr, not a bound value);
        # works even when `fields` is empty.
        set_parts = [f"{k} = ?" for k in fields]
        set_parts.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params = list(fields.values()) + [account_id]
        self._write(f"UPDATE account SET {', '.join(set_parts)} WHERE account_id = ?", tuple(params))

    def account_delete(self, account_id):
        self._write("DELETE FROM account WHERE account_id = ?", (account_id,))

    # binding ---------------------------------------------------------------
    def binding_insert(self, row):
        self._write(
            "INSERT INTO channel_binding (binding_id, account_id, session_uuid, first_run_done, feishu_chat_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["binding_id"], row["account_id"], row["session_uuid"],
             int(row.get("first_run_done", 0)), row.get("feishu_chat_id")),
        )

    def binding_get(self, binding_id):
        return self._one("SELECT * FROM channel_binding WHERE binding_id = ?", (binding_id,))

    def binding_get_by_account(self, account_id):
        return self._one("SELECT * FROM channel_binding WHERE account_id = ?", (account_id,))

    def binding_list_by_account(self, account_id):
        return self._all("SELECT * FROM channel_binding WHERE account_id = ?", (account_id,))

    def binding_claim_first_run(self, binding_id):
        # atomic CAS 0→1
        return self._write(
            "UPDATE channel_binding SET first_run_done = 1 WHERE binding_id = ? AND first_run_done = 0",
            (binding_id,),
        ) == 1

    def binding_rebind(self, account_id, session_uuid, feishu_chat_id, rebound_at):
        self._write(
            "UPDATE channel_binding SET session_uuid = ?, first_run_done = 0, feishu_chat_id = ?, rebound_at = ? "
            "WHERE account_id = ?",
            (session_uuid, feishu_chat_id, rebound_at, account_id),
        )

    # quota -----------------------------------------------------------------
    def quota_get(self, account_id):
        return self._one("SELECT * FROM quota WHERE account_id = ?", (account_id,))

    def quota_insert(self, row):
        self._write(
            "INSERT OR IGNORE INTO quota (account_id, tier, max_concurrent_sessions, idle_grace_seconds) "
            "VALUES (?, ?, ?, ?)",
            (row["account_id"], row.get("tier", "default"),
             int(row.get("max_concurrent_sessions", 3)), int(row.get("idle_grace_seconds", 1800))),
        )

    def quota_update(self, account_id, fields):
        if not fields:
            return
        clause, params = _set_clause(fields)
        self._write(
            f"UPDATE quota SET {clause}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE account_id = ?",
            tuple(params) + (account_id,),
        )

    # jobs ------------------------------------------------------------------
    _JOB_COLS = ("job_id", "account_id", "name", "prompt", "trigger", "job_type",
                 "job_config", "timezone", "model", "status")

    def job_insert(self, row):
        cols = [c for c in self._JOB_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT INTO scheduled_job ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def job_get(self, job_id):
        return self._one("SELECT * FROM scheduled_job WHERE job_id = ?", (job_id,))

    def job_update(self, job_id, fields):
        if not fields:
            return
        sets, params = _set_clause(fields)
        self._write(
            f"UPDATE scheduled_job SET {sets}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE job_id = ?",
            tuple(params) + (job_id,),
        )

    def job_delete(self, job_id):
        return self._write("DELETE FROM scheduled_job WHERE job_id = ?", (job_id,)) > 0

    def job_list_by_account(self, account_id):
        return self._all("SELECT * FROM scheduled_job WHERE account_id = ? ORDER BY created_at ASC", (account_id,))

    def job_list_active(self):
        return self._all("SELECT * FROM scheduled_job WHERE status = 'active' ORDER BY account_id, created_at ASC")

    # runs ------------------------------------------------------------------
    _RUN_COLS = ("run_id", "job_id", "job_name", "account_id", "session_id", "started_at",
                 "finished_at", "status", "duration_ms", "is_error", "error_message",
                 "num_turns", "result_summary")

    def run_insert(self, row):
        cols = [c for c in self._RUN_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT INTO job_run_record ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def run_upsert(self, row):
        # Each append writes the full current snapshot of the run; upsert on run_id
        # so birth (running) + outcome (+ skip-without-birth) all converge.
        cols = [c for c in self._RUN_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
        self._write(
            f"INSERT INTO job_run_record ({', '.join(cols)}) VALUES ({ph}) "
            f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
            tuple(row[c] for c in cols),
        )

    def run_get_latest(self, account_id, job_id):
        return self._one(
            "SELECT * FROM job_run_record WHERE account_id = ? AND job_id = ? "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            (account_id, job_id),
        )

    def run_update(self, run_id, fields):
        if not fields:
            return
        clause, params = _set_clause(fields)
        self._write(f"UPDATE job_run_record SET {clause} WHERE run_id = ?", tuple(params) + (run_id,))

    def run_get(self, run_id):
        return self._one("SELECT * FROM job_run_record WHERE run_id = ?", (run_id,))

    def run_list(self, account_id, *, limit, before, after, job_id, status):
        # newest-first keyset on (started_at, run_id). `before` pages older,
        # `after` pages newer. before/after are (started_at, run_id) tuples.
        where = ["account_id = ?"]
        params: list = [account_id]
        if job_id:
            where.append("job_id = ?")
            params.append(job_id)
        if status:
            where.append("status = ?")
            params.append(status)
        order_desc = True
        if before:
            where.append("(started_at < ? OR (started_at = ? AND run_id < ?))")
            params += [before[0], before[0], before[1]]
        elif after:
            where.append("(started_at > ? OR (started_at = ? AND run_id > ?))")
            params += [after[0], after[0], after[1]]
            order_desc = False
        order = "DESC" if order_desc else "ASC"
        sql = (
            f"SELECT * FROM job_run_record WHERE {' AND '.join(where)} "
            f"ORDER BY started_at {order}, run_id {order} LIMIT ?"
        )
        rows = self._all(sql, tuple(params) + (limit + 1,))
        has_more = len(rows) > limit
        rows = rows[:limit]
        if not order_desc:
            rows = list(reversed(rows))  # always return newest-first
        return rows, has_more

    def run_count(self, account_id):
        return self._one("SELECT COUNT(*) AS n FROM job_run_record WHERE account_id = ?", (account_id,))["n"]

    def run_delete_before(self, account_id, cutoff_date):
        # Returns the deleted run_ids (so callers can delete their PVC transcripts).
        # cutoff_date is an ISO date/datetime string; lexicographic compare works on ISO-8601.
        with self._lock:
            ids = [
                r["run_id"]
                for r in self._conn.execute(
                    "SELECT run_id FROM job_run_record WHERE account_id = ? AND started_at < ?",
                    (account_id, cutoff_date),
                ).fetchall()
            ]
            self._conn.execute(
                "DELETE FROM job_run_record WHERE account_id = ? AND started_at < ?",
                (account_id, cutoff_date),
            )
            self._conn.commit()
            return ids

    # fires -------------------------------------------------------------------
    def fire_claim(self, job_id, fire_epoch, claimed_by):
        # INSERT-wins on the composite PK. OR IGNORE swallows the PK conflict
        # (another replica won) but NOT an FK violation (job deleted mid-flight)
        # — treat that as "no claim" too, not an error.
        try:
            return self._write(
                "INSERT OR IGNORE INTO job_fire (job_id, fire_epoch, claimed_by) VALUES (?, ?, ?)",
                (job_id, int(fire_epoch), claimed_by),
            ) == 1
        except sqlite3.IntegrityError:
            return False

    def fire_prune_before(self, cutoff):
        return self._write("DELETE FROM job_fire WHERE claimed_at < ?", (cutoff,))

    # resource_spec ----------------------------------------------------------
    _RSPEC_COLS = ("cpu_cores", "memory_mb", "volume_gb")

    def resource_spec_get(self, account_id):
        return self._one("SELECT * FROM account_resource_spec WHERE account_id = ?", (account_id,))

    def resource_spec_upsert(self, account_id, fields):
        # Only the named columns are written; unset ones keep their default / prior value.
        cols = [c for c in self._RSPEC_COLS if c in fields]
        insert_cols = ["account_id"] + cols
        ph = ", ".join("?" for _ in insert_cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        updates = (updates + ", " if updates else "") + "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        self._write(
            f"INSERT INTO account_resource_spec ({', '.join(insert_cols)}) VALUES ({ph}) "
            f"ON CONFLICT(account_id) DO UPDATE SET {updates}",
            tuple([account_id] + [fields[c] for c in cols]),
        )
        return self.resource_spec_get(account_id)

    def resource_spec_list(self):
        return self._all("SELECT * FROM account_resource_spec ORDER BY account_id")

    # feishu_channel_config --------------------------------------------------
    # Desired columns (user + admin roles, filtered by role in the service before
    # they reach here) + the service-computed desired_digest / updated_by. The
    # status columns are written by a SEPARATE method so status write-back never
    # stamps updated_at (the connector poll diffs on desired_digest).
    _FEISHU_COLS = (
        "app_id", "app_secret_enc", "app_secret_updated_at",
        "user_enabled", "admin_disabled",
        "single_chat_access_mode", "allowed_union_ids", "welcome_message",
        "reject_message", "model", "max_queue_size", "enable_permission_feedback",
        "feedback_timeout_seconds", "domain", "desired_digest", "updated_by",
    )
    _FEISHU_STATUS_COLS = ("conn_status", "last_error_code", "last_error_message", "last_connected_at")

    def feishu_get(self, account_id):
        return self._one("SELECT * FROM feishu_channel_config WHERE account_id = ?", (account_id,))

    def feishu_upsert(self, account_id, fields):
        cols = [c for c in self._FEISHU_COLS if c in fields]
        insert_cols = ["account_id"] + cols
        ph = ", ".join("?" for _ in insert_cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        updates = (updates + ", " if updates else "") + "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        self._write(
            f"INSERT INTO feishu_channel_config ({', '.join(insert_cols)}) VALUES ({ph}) "
            f"ON CONFLICT(account_id) DO UPDATE SET {updates}",
            tuple([account_id] + [fields[c] for c in cols]),
        )
        return self.feishu_get(account_id)

    def feishu_status_update(self, account_id, fields):
        cols = {c: fields[c] for c in self._FEISHU_STATUS_COLS if c in fields}
        if not cols:
            return self.feishu_get(account_id)
        clause, params = _set_clause(cols)
        self._write(
            f"UPDATE feishu_channel_config SET {clause}, "
            "status_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE account_id = ?",
            tuple(params) + (account_id,),
        )
        return self.feishu_get(account_id)

    def feishu_list(self):
        return self._all("SELECT * FROM feishu_channel_config ORDER BY account_id")

    def feishu_list_effective(self):
        return self._all(
            "SELECT * FROM feishu_channel_config "
            "WHERE user_enabled = 1 AND admin_disabled = 0 "
            "AND app_id IS NOT NULL AND app_id <> '' "
            "AND app_secret_enc IS NOT NULL AND app_secret_enc <> '' "
            "ORDER BY account_id"
        )

    # runner_defaults (single row id=1) --------------------------------------
    _RDEFAULTS_COLS = ("idle_grace_seconds", "min_alive_after_wake_seconds",
                       "cpu_cores", "memory_mb", "storage_gb", "runner_image",
                       "terminal_resource_percent", "terminal_max_sessions",
                       "terminal_idle_timeout_seconds", "terminal_max_lifetime_seconds",
                       "terminal_scale_down_grace_seconds")

    def runner_defaults_get(self):
        return self._one("SELECT * FROM runner_defaults WHERE id = 1")

    def runner_defaults_seed(self, values):
        # Insert the single row from the supplied seed iff it doesn't exist yet.
        cols = list(self._RDEFAULTS_COLS)
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT OR IGNORE INTO runner_defaults (id, {', '.join(cols)}) "
            f"VALUES (1, {ph})",
            tuple(values[c] for c in cols),
        )
        return self.runner_defaults_get()

    def runner_defaults_upsert(self, fields):
        # Update only the named columns of the seeded row (callers seed first).
        cols = [c for c in self._RDEFAULTS_COLS if c in fields]
        if not cols:
            return self.runner_defaults_get()
        sets = ", ".join(f"{c} = ?" for c in cols)
        self._write(
            f"UPDATE runner_defaults SET {sets}, "
            f"updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = 1",
            tuple(fields[c] for c in cols),
        )
        return self.runner_defaults_get()

    # hook_policy -------------------------------------------------------------
    _HOOK_POLICY_COLS = ("id", "hook_type", "name", "description", "events", "matcher",
                         "timeout_seconds", "interpreter", "script_body", "content_hash",
                         "url", "headers_json", "allowed_env_vars", "mcp_server", "mcp_tool",
                         "enabled", "enforced", "enforced_events", "default_on", "predefined",
                         "seed_version", "target", "updated_by")

    def hook_policy_list(self, enabled_only=False):
        sql = "SELECT * FROM hook_policy"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self._all(sql + " ORDER BY id ASC")

    def hook_policy_get(self, policy_id):
        return self._one("SELECT * FROM hook_policy WHERE id = ?", (policy_id,))

    def hook_policy_insert(self, row):
        cols = [c for c in self._HOOK_POLICY_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT INTO hook_policy ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def hook_policy_update(self, policy_id, fields):
        cols = [c for c in self._HOOK_POLICY_COLS if c in fields and c != "id"]
        set_parts = [f"{c} = ?" for c in cols]
        set_parts.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        self._write(
            f"UPDATE hook_policy SET {', '.join(set_parts)} WHERE id = ?",
            tuple(fields[c] for c in cols) + (policy_id,),
        )
        return self.hook_policy_get(policy_id)

    def hook_policy_delete(self, policy_id):
        return self._write("DELETE FROM hook_policy WHERE id = ?", (policy_id,)) > 0

    # pending_registration ---------------------------------------------------
    _PENDING_COLS = ("request_id", "username", "password_hash", "display_name", "runner_type",
                     "cpu_cores", "memory_mb", "volume_gb", "note", "status")

    def pending_insert(self, row):
        cols = [c for c in self._PENDING_COLS if c in row]
        ph = ", ".join("?" for _ in cols)
        self._write(
            f"INSERT INTO pending_registration ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def pending_get(self, request_id):
        return self._one("SELECT * FROM pending_registration WHERE request_id = ?", (request_id,))

    def pending_get_open_by_username(self, username):
        return self._one(
            "SELECT * FROM pending_registration WHERE username = ? AND status = 'pending'", (username,))

    def pending_list_by_status(self, status):
        if status:
            return self._all(
                "SELECT * FROM pending_registration WHERE status = ? ORDER BY created_at DESC", (status,))
        return self._all("SELECT * FROM pending_registration ORDER BY created_at DESC")

    def pending_set_status(self, request_id, status):
        self._write(
            "UPDATE pending_registration SET status = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE request_id = ?",
            (status, request_id),
        )
        return self.pending_get(request_id)

    # admin -----------------------------------------------------------------
    def table_count(self, table):
        if table not in schema.TABLES:
            raise ValueError(f"unknown table: {table}")
        return self._one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


# --- Postgres implementation -------------------------------------------------

class PgRepo(Repository):
    """Method-for-method PG port of SqliteRepo over a psycopg3 connection pool.

    Dialect deltas only: %s placeholders, ON CONFLICT DO NOTHING for OR IGNORE,
    to_char(now()) for the strftime updated_at stamps, DELETE..RETURNING for
    run_delete_before. The pool (not an RLock) provides thread-safety — the sync
    gRPC server calls in from a 16-thread executor.
    """

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8):
        # Lazy import: sqlite-only deployments never need psycopg installed.
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        from . import schema_pg

        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=5,                                  # fail readyz fast on a dead DSN
            check=ConnectionPool.check_connection,      # drop stale conns (PG restart)
            open=True,
            kwargs={"row_factory": dict_row},
        )
        with self._pool.connection() as conn:
            schema_pg.create_all(conn)

    def close(self) -> None:
        self._pool.close()

    # low-level (each call = one pooled connection = one committed transaction)
    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._pool.connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._pool.connection() as conn:
            return conn.execute(sql, params).rowcount

    # SQL expr stamping updated_at server-side (mirror of the strftime literal)
    _NOW = "to_char((now() AT TIME ZONE 'utc'), 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"

    # account ---------------------------------------------------------------
    def account_get(self, account_id):
        return self._one("SELECT * FROM account WHERE account_id = %s", (account_id,))

    def account_get_by_username(self, username):
        return self._one("SELECT * FROM account WHERE username = %s", (username,))

    def account_find_by_api_key_lookup(self, lookup):
        return self._one("SELECT * FROM account WHERE api_key_lookup = %s", (lookup,))

    def account_find_by_feishu(self, feishu_user_id):
        return self._one("SELECT * FROM account WHERE feishu_user_id = %s", (feishu_user_id,))

    def account_list(self):
        return self._all("SELECT * FROM account ORDER BY created_at ASC")

    def account_count_admins(self):
        return self._one("SELECT COUNT(*) AS n FROM account WHERE role = 'admin'")["n"]

    def account_insert(self, row):
        cols = [c for c in _ACCOUNT_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO account ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def account_update(self, account_id, fields):
        fields = {k: v for k, v in fields.items() if k != "updated_at"}
        # updated_at is always stamped server-side (a SQL expr, not a bound value);
        # works even when `fields` is empty.
        set_parts = [f"{k} = %s" for k in fields]
        set_parts.append(f"updated_at = {self._NOW}")
        params = list(fields.values()) + [account_id]
        self._write(f"UPDATE account SET {', '.join(set_parts)} WHERE account_id = %s", tuple(params))

    def account_delete(self, account_id):
        self._write("DELETE FROM account WHERE account_id = %s", (account_id,))

    # binding ---------------------------------------------------------------
    def binding_insert(self, row):
        self._write(
            "INSERT INTO channel_binding (binding_id, account_id, session_uuid, first_run_done, feishu_chat_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (row["binding_id"], row["account_id"], row["session_uuid"],
             int(row.get("first_run_done", 0)), row.get("feishu_chat_id")),
        )

    def binding_get(self, binding_id):
        return self._one("SELECT * FROM channel_binding WHERE binding_id = %s", (binding_id,))

    def binding_get_by_account(self, account_id):
        return self._one("SELECT * FROM channel_binding WHERE account_id = %s", (account_id,))

    def binding_list_by_account(self, account_id):
        return self._all("SELECT * FROM channel_binding WHERE account_id = %s", (account_id,))

    def binding_claim_first_run(self, binding_id):
        # atomic CAS 0→1
        return self._write(
            "UPDATE channel_binding SET first_run_done = 1 WHERE binding_id = %s AND first_run_done = 0",
            (binding_id,),
        ) == 1

    def binding_rebind(self, account_id, session_uuid, feishu_chat_id, rebound_at):
        self._write(
            "UPDATE channel_binding SET session_uuid = %s, first_run_done = 0, feishu_chat_id = %s, rebound_at = %s "
            "WHERE account_id = %s",
            (session_uuid, feishu_chat_id, rebound_at, account_id),
        )

    # quota -----------------------------------------------------------------
    def quota_get(self, account_id):
        return self._one("SELECT * FROM quota WHERE account_id = %s", (account_id,))

    def quota_insert(self, row):
        self._write(
            "INSERT INTO quota (account_id, tier, max_concurrent_sessions, idle_grace_seconds) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING",
            (row["account_id"], row.get("tier", "default"),
             int(row.get("max_concurrent_sessions", 3)), int(row.get("idle_grace_seconds", 1800))),
        )

    def quota_update(self, account_id, fields):
        if not fields:
            return
        clause, params = _set_clause_pg(fields)
        self._write(
            f"UPDATE quota SET {clause}, updated_at = {self._NOW} WHERE account_id = %s",
            tuple(params) + (account_id,),
        )

    # jobs ------------------------------------------------------------------
    _JOB_COLS = SqliteRepo._JOB_COLS

    def job_insert(self, row):
        cols = [c for c in self._JOB_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO scheduled_job ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def job_get(self, job_id):
        return self._one("SELECT * FROM scheduled_job WHERE job_id = %s", (job_id,))

    def job_update(self, job_id, fields):
        if not fields:
            return
        sets, params = _set_clause_pg(fields)
        self._write(
            f"UPDATE scheduled_job SET {sets}, updated_at = {self._NOW} WHERE job_id = %s",
            tuple(params) + (job_id,),
        )

    def job_delete(self, job_id):
        return self._write("DELETE FROM scheduled_job WHERE job_id = %s", (job_id,)) > 0

    def job_list_by_account(self, account_id):
        return self._all("SELECT * FROM scheduled_job WHERE account_id = %s ORDER BY created_at ASC", (account_id,))

    def job_list_active(self):
        return self._all("SELECT * FROM scheduled_job WHERE status = 'active' ORDER BY account_id, created_at ASC")

    # runs ------------------------------------------------------------------
    _RUN_COLS = SqliteRepo._RUN_COLS

    def run_insert(self, row):
        cols = [c for c in self._RUN_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO job_run_record ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def run_upsert(self, row):
        # Each append writes the full current snapshot of the run; upsert on run_id
        # so birth (running) + outcome (+ skip-without-birth) all converge.
        cols = [c for c in self._RUN_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
        self._write(
            f"INSERT INTO job_run_record ({', '.join(cols)}) VALUES ({ph}) "
            f"ON CONFLICT (run_id) DO UPDATE SET {updates}",
            tuple(row[c] for c in cols),
        )

    def run_get_latest(self, account_id, job_id):
        return self._one(
            "SELECT * FROM job_run_record WHERE account_id = %s AND job_id = %s "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            (account_id, job_id),
        )

    def run_update(self, run_id, fields):
        if not fields:
            return
        clause, params = _set_clause_pg(fields)
        self._write(f"UPDATE job_run_record SET {clause} WHERE run_id = %s", tuple(params) + (run_id,))

    def run_get(self, run_id):
        return self._one("SELECT * FROM job_run_record WHERE run_id = %s", (run_id,))

    def run_list(self, account_id, *, limit, before, after, job_id, status):
        # newest-first keyset on (started_at, run_id) — identical to SqliteRepo;
        # TEXT ISO timestamps keep lexicographic == chronological.
        where = ["account_id = %s"]
        params: list = [account_id]
        if job_id:
            where.append("job_id = %s")
            params.append(job_id)
        if status:
            where.append("status = %s")
            params.append(status)
        order_desc = True
        if before:
            where.append("(started_at < %s OR (started_at = %s AND run_id < %s))")
            params += [before[0], before[0], before[1]]
        elif after:
            where.append("(started_at > %s OR (started_at = %s AND run_id > %s))")
            params += [after[0], after[0], after[1]]
            order_desc = False
        order = "DESC" if order_desc else "ASC"
        sql = (
            f"SELECT * FROM job_run_record WHERE {' AND '.join(where)} "
            f"ORDER BY started_at {order}, run_id {order} LIMIT %s"
        )
        rows = self._all(sql, tuple(params) + (limit + 1,))
        has_more = len(rows) > limit
        rows = rows[:limit]
        if not order_desc:
            rows = list(reversed(rows))  # always return newest-first
        return rows, has_more

    def run_count(self, account_id):
        return self._one("SELECT COUNT(*) AS n FROM job_run_record WHERE account_id = %s", (account_id,))["n"]

    def run_delete_before(self, account_id, cutoff_date):
        # Returns the deleted run_ids (so callers can delete their PVC transcripts).
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM job_run_record WHERE account_id = %s AND started_at < %s RETURNING run_id",
                (account_id, cutoff_date),
            )
            return [r["run_id"] for r in cur.fetchall()]

    # fires -------------------------------------------------------------------
    def fire_claim(self, job_id, fire_epoch, claimed_by):
        # Concurrent-safe by construction: ON CONFLICT DO NOTHING on the composite
        # PK admits exactly one winner. An FK violation (job deleted mid-flight)
        # is likewise "no claim", not an error.
        import psycopg

        try:
            return self._write(
                "INSERT INTO job_fire (job_id, fire_epoch, claimed_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (job_id, fire_epoch) DO NOTHING",
                (job_id, int(fire_epoch), claimed_by),
            ) == 1
        except psycopg.errors.ForeignKeyViolation:
            return False

    def fire_prune_before(self, cutoff):
        return self._write("DELETE FROM job_fire WHERE claimed_at < %s", (cutoff,))

    # resource_spec ----------------------------------------------------------
    _RSPEC_COLS = SqliteRepo._RSPEC_COLS

    def resource_spec_get(self, account_id):
        return self._one("SELECT * FROM account_resource_spec WHERE account_id = %s", (account_id,))

    def resource_spec_upsert(self, account_id, fields):
        # Only the named columns are written; unset ones keep their default / prior value.
        cols = [c for c in self._RSPEC_COLS if c in fields]
        insert_cols = ["account_id"] + cols
        ph = ", ".join("%s" for _ in insert_cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        updates = (updates + ", " if updates else "") + f"updated_at = {self._NOW}"
        self._write(
            f"INSERT INTO account_resource_spec ({', '.join(insert_cols)}) VALUES ({ph}) "
            f"ON CONFLICT (account_id) DO UPDATE SET {updates}",
            tuple([account_id] + [fields[c] for c in cols]),
        )
        return self.resource_spec_get(account_id)

    def resource_spec_list(self):
        return self._all("SELECT * FROM account_resource_spec ORDER BY account_id")

    # feishu_channel_config --------------------------------------------------
    _FEISHU_COLS = SqliteRepo._FEISHU_COLS
    _FEISHU_STATUS_COLS = SqliteRepo._FEISHU_STATUS_COLS

    def feishu_get(self, account_id):
        return self._one("SELECT * FROM feishu_channel_config WHERE account_id = %s", (account_id,))

    def feishu_upsert(self, account_id, fields):
        cols = [c for c in self._FEISHU_COLS if c in fields]
        insert_cols = ["account_id"] + cols
        ph = ", ".join("%s" for _ in insert_cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        updates = (updates + ", " if updates else "") + f"updated_at = {self._NOW}"
        self._write(
            f"INSERT INTO feishu_channel_config ({', '.join(insert_cols)}) VALUES ({ph}) "
            f"ON CONFLICT (account_id) DO UPDATE SET {updates}",
            tuple([account_id] + [fields[c] for c in cols]),
        )
        return self.feishu_get(account_id)

    def feishu_status_update(self, account_id, fields):
        cols = {c: fields[c] for c in self._FEISHU_STATUS_COLS if c in fields}
        if not cols:
            return self.feishu_get(account_id)
        clause, params = _set_clause_pg(cols)
        self._write(
            f"UPDATE feishu_channel_config SET {clause}, "
            f"status_updated_at = {self._NOW} WHERE account_id = %s",
            tuple(params) + (account_id,),
        )
        return self.feishu_get(account_id)

    def feishu_list(self):
        return self._all("SELECT * FROM feishu_channel_config ORDER BY account_id")

    def feishu_list_effective(self):
        return self._all(
            "SELECT * FROM feishu_channel_config "
            "WHERE user_enabled = 1 AND admin_disabled = 0 "
            "AND app_id IS NOT NULL AND app_id <> '' "
            "AND app_secret_enc IS NOT NULL AND app_secret_enc <> '' "
            "ORDER BY account_id"
        )

    # runner_defaults (single row id=1) --------------------------------------
    _RDEFAULTS_COLS = SqliteRepo._RDEFAULTS_COLS

    def runner_defaults_get(self):
        return self._one("SELECT * FROM runner_defaults WHERE id = 1")

    def runner_defaults_seed(self, values):
        # Insert the single row from the supplied seed iff it doesn't exist yet.
        cols = list(self._RDEFAULTS_COLS)
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO runner_defaults (id, {', '.join(cols)}) "
            f"VALUES (1, {ph}) ON CONFLICT (id) DO NOTHING",
            tuple(values[c] for c in cols),
        )
        return self.runner_defaults_get()

    def runner_defaults_upsert(self, fields):
        # Update only the named columns of the seeded row (callers seed first).
        cols = [c for c in self._RDEFAULTS_COLS if c in fields]
        if not cols:
            return self.runner_defaults_get()
        sets = ", ".join(f"{c} = %s" for c in cols)
        self._write(
            f"UPDATE runner_defaults SET {sets}, updated_at = {self._NOW} WHERE id = 1",
            tuple(fields[c] for c in cols),
        )
        return self.runner_defaults_get()

    # hook_policy -------------------------------------------------------------
    _HOOK_POLICY_COLS = SqliteRepo._HOOK_POLICY_COLS

    def hook_policy_list(self, enabled_only=False):
        sql = "SELECT * FROM hook_policy"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self._all(sql + " ORDER BY id ASC")

    def hook_policy_get(self, policy_id):
        return self._one("SELECT * FROM hook_policy WHERE id = %s", (policy_id,))

    def hook_policy_insert(self, row):
        cols = [c for c in self._HOOK_POLICY_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO hook_policy ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def hook_policy_update(self, policy_id, fields):
        cols = [c for c in self._HOOK_POLICY_COLS if c in fields and c != "id"]
        set_parts = [f"{c} = %s" for c in cols]
        set_parts.append(f"updated_at = {self._NOW}")
        self._write(
            f"UPDATE hook_policy SET {', '.join(set_parts)} WHERE id = %s",
            tuple(fields[c] for c in cols) + (policy_id,),
        )
        return self.hook_policy_get(policy_id)

    def hook_policy_delete(self, policy_id):
        return self._write("DELETE FROM hook_policy WHERE id = %s", (policy_id,)) > 0

    # pending_registration ---------------------------------------------------
    _PENDING_COLS = SqliteRepo._PENDING_COLS

    def pending_insert(self, row):
        cols = [c for c in self._PENDING_COLS if c in row]
        ph = ", ".join("%s" for _ in cols)
        self._write(
            f"INSERT INTO pending_registration ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in cols),
        )

    def pending_get(self, request_id):
        return self._one("SELECT * FROM pending_registration WHERE request_id = %s", (request_id,))

    def pending_get_open_by_username(self, username):
        return self._one(
            "SELECT * FROM pending_registration WHERE username = %s AND status = 'pending'", (username,))

    def pending_list_by_status(self, status):
        if status:
            return self._all(
                "SELECT * FROM pending_registration WHERE status = %s ORDER BY created_at DESC", (status,))
        return self._all("SELECT * FROM pending_registration ORDER BY created_at DESC")

    def pending_set_status(self, request_id, status):
        self._write(
            f"UPDATE pending_registration SET status = %s, updated_at = {self._NOW} WHERE request_id = %s",
            (status, request_id),
        )
        return self.pending_get(request_id)

    # admin -----------------------------------------------------------------
    def table_count(self, table):
        if table not in schema.TABLES:
            raise ValueError(f"unknown table: {table}")
        return self._one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def _set_clause_pg(fields: dict) -> tuple[str, list]:
    keys = list(fields.keys())
    return ", ".join(f"{k} = %s" for k in keys), [fields[k] for k in keys]

"""Storage seam: Repository (ABC) + SqliteRepo (built) + PgRepo (interface-only).

The repo speaks rows (dicts) and SQL only — no crypto, no DTOs, no UUID minting
(that's the service layer). SqliteRepo serializes all access behind one lock over
a single WAL connection (a simple, correct single-writer model for the alpha; a
read-only connection pool is a later optimization). foreign_keys are enforced.
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
    # secret
    @abstractmethod
    def secret_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def secret_upsert(self, account_id: str, bundle: str) -> dict: ...
    @abstractmethod
    def secret_list_account_ids(self) -> list[str]: ...
    # resource_spec
    @abstractmethod
    def resource_spec_get(self, account_id: str) -> dict | None: ...
    @abstractmethod
    def resource_spec_upsert(self, account_id: str, fields: dict) -> dict: ...
    @abstractmethod
    def resource_spec_list(self) -> list[dict]: ...
    # runner_defaults (single row, id=1)
    @abstractmethod
    def runner_defaults_get(self) -> dict | None: ...
    @abstractmethod
    def runner_defaults_seed(self, values: dict) -> dict: ...
    @abstractmethod
    def runner_defaults_upsert(self, fields: dict) -> dict: ...
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

    # secret ----------------------------------------------------------------
    def secret_get(self, account_id):
        return self._one("SELECT * FROM secret WHERE account_id = ?", (account_id,))

    def secret_upsert(self, account_id, bundle):
        # bundle is already Fernet-encrypted ciphertext; generation bumps per put.
        self._write(
            "INSERT INTO secret (account_id, bundle, generation) VALUES (?, ?, 1) "
            "ON CONFLICT(account_id) DO UPDATE SET "
            "bundle = excluded.bundle, generation = generation + 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (account_id, bundle),
        )
        return self.secret_get(account_id)

    def secret_list_account_ids(self):
        return [r["account_id"] for r in self._all("SELECT account_id FROM secret ORDER BY account_id")]

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

    # runner_defaults (single row id=1) --------------------------------------
    _RDEFAULTS_COLS = ("idle_grace_seconds", "min_alive_after_wake_seconds",
                       "cpu_cores", "memory_mb", "storage_gb", "runner_image")

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


# --- Postgres (deferred) ---------------------------------------------------

class PgRepo:
    """Structured but not implemented in Phase 1 (backend='postgres').

    Deliberately NOT subclassing Repository so the ABC's abstractmethod check
    doesn't mask this clear error at construction time.
    """

    def __init__(self, dsn: str):
        raise NotImplementedError(
            "data-spine Postgres backend is structured but not implemented in Phase 1; "
            "set dataspine.backend='sqlite'"
        )

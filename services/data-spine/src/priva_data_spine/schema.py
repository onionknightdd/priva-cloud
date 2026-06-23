"""The data-spine SQLite schema.

All STRICT; foreign_keys enforced per connection; timestamps TEXT ISO-8601 UTC.
create_all() is idempotent (CREATE TABLE IF NOT EXISTS + guarded column ALTERs).
"""

from __future__ import annotations

import sqlite3

# UTC ISO-8601 with millisecond precision, e.g. 2026-06-20T03:32:01.112Z
NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"

DDL: tuple[str, ...] = (
    # 1 ── account ----------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS account (
      account_id     TEXT PRIMARY KEY,
      username       TEXT NOT NULL,
      password_hash  TEXT NOT NULL,
      api_key        TEXT,
      api_key_lookup TEXT,
      role           TEXT NOT NULL DEFAULT 'user'   CHECK (role   IN ('user','admin')),
      status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','offboarding','purged')),
      agent_runner_type TEXT NOT NULL DEFAULT 'auto_scale' CHECK (agent_runner_type IN ('auto_scale','persistent')),
      feishu_user_id      TEXT,
      feishu_display_name TEXT,
      created_at     TEXT NOT NULL DEFAULT {NOW},
      updated_at     TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_username   ON account(username)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_apikey     ON account(api_key_lookup) WHERE api_key_lookup IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_feishu_uid ON account(feishu_user_id)  WHERE feishu_user_id  IS NOT NULL",
    # 2 ── channel_binding --------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS channel_binding (
      binding_id     TEXT PRIMARY KEY,
      account_id     TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
      session_uuid   TEXT NOT NULL,
      first_run_done INTEGER NOT NULL DEFAULT 0 CHECK (first_run_done IN (0,1)),
      feishu_chat_id TEXT,
      bound_at       TEXT NOT NULL DEFAULT {NOW},
      rebound_at     TEXT
    ) STRICT
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_account ON channel_binding(account_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_session ON channel_binding(session_uuid)",
    # 3 ── quota ------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS quota (
      account_id              TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      tier                    TEXT NOT NULL DEFAULT 'default',
      max_concurrent_sessions INTEGER NOT NULL DEFAULT 3,
      idle_grace_seconds      INTEGER NOT NULL DEFAULT 1800,
      updated_at              TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    # 4 ── scheduled_job ----------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS scheduled_job (
      job_id     TEXT PRIMARY KEY,
      account_id TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
      name       TEXT NOT NULL,
      prompt     TEXT NOT NULL DEFAULT '',
      trigger    TEXT NOT NULL,
      job_type   TEXT NOT NULL CHECK (job_type IN ('agent_run','http_call','user_script','tool_retry')),
      job_config TEXT,
      timezone   TEXT NOT NULL,
      model      TEXT,
      status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
      created_at TEXT NOT NULL DEFAULT {NOW},
      updated_at TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS ix_job_account ON scheduled_job(account_id)",
    "CREATE INDEX IF NOT EXISTS ix_job_active  ON scheduled_job(status) WHERE status = 'active'",
    # 5 ── job_run_record ---------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS job_run_record (
      run_id        TEXT PRIMARY KEY,
      job_id        TEXT REFERENCES scheduled_job(job_id) ON DELETE SET NULL,
      job_name      TEXT NOT NULL,
      account_id    TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
      session_id    TEXT,
      started_at    TEXT NOT NULL,
      finished_at   TEXT,
      status        TEXT NOT NULL CHECK (status IN ('running','success','error','cancelled','skipped')),
      duration_ms   INTEGER,
      is_error      INTEGER NOT NULL DEFAULT 0 CHECK (is_error IN (0,1)),
      error_message TEXT,
      num_turns     INTEGER,
      result_summary TEXT
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS ix_run_account_started ON job_run_record(account_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_run_job_started     ON job_run_record(job_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_run_status          ON job_run_record(status) WHERE status = 'running'",
    # 6 ── secret -----------------------------------------------------------
    # Per-account credential bundle, Fernet-encrypted. The operator reads it at
    # wake to materialize the per-pod K8s Secret (alpha: single shared key).
    f"""
    CREATE TABLE IF NOT EXISTS secret (
      account_id TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      bundle     TEXT NOT NULL,
      generation INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    # 7 ── account_resource_spec --------------------------------------------
    # Per-account agent-runner pod sizing. The operator reads these (via the CR
    # the control-panel stamps) to set container resources + PVC size. volume_gb
    # is grow-only (K8s can't shrink a PVC). cpu_cores is fractional.
    f"""
    CREATE TABLE IF NOT EXISTS account_resource_spec (
      account_id TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      cpu_cores  REAL    NOT NULL DEFAULT 1.0,
      memory_mb  INTEGER NOT NULL DEFAULT 2048,
      volume_gb  INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT    NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    # 8 ── pending_registration ---------------------------------------------
    # Self-service account requests awaiting admin approval. password_hash is the
    # bcrypt of the user-chosen password; on approval the account is created from
    # it directly. One open ('pending') request per username (partial unique idx).
    f"""
    CREATE TABLE IF NOT EXISTS pending_registration (
      request_id    TEXT PRIMARY KEY,
      username      TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      display_name  TEXT,
      runner_type   TEXT NOT NULL DEFAULT 'auto_scale' CHECK (runner_type IN ('auto_scale','persistent')),
      cpu_cores     REAL    NOT NULL DEFAULT 1.0,
      memory_mb     INTEGER NOT NULL DEFAULT 2048,
      volume_gb     INTEGER NOT NULL DEFAULT 1,
      note          TEXT,
      status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
      created_at    TEXT NOT NULL DEFAULT {NOW},
      updated_at    TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_username ON pending_registration(username) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS ix_pending_status ON pending_registration(status, created_at DESC)",
)

TABLES = (
    "account", "channel_binding", "quota", "scheduled_job", "job_run_record",
    "secret", "account_resource_spec", "pending_registration",
)

# Idempotent column additions for DBs created before a column existed. CREATE
# TABLE IF NOT EXISTS won't alter an existing table, so each (table, column, ddl)
# is applied only when PRAGMA table_info shows the column missing. A bare ALTER
# would fail on the 2nd boot with "duplicate column name".
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("account", "agent_runner_type",
     "ALTER TABLE account ADD COLUMN agent_runner_type TEXT NOT NULL DEFAULT 'auto_scale' "
     "CHECK (agent_runner_type IN ('auto_scale','persistent'))"),
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(ddl)


def create_all(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)
    _apply_migrations(conn)

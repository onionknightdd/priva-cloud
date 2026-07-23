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
      feishu_open_id      TEXT,
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
      session_uuid   TEXT,
      first_run_done INTEGER NOT NULL DEFAULT 0 CHECK (first_run_done IN (0,1)),
      feishu_chat_id TEXT,
      bound_at       TEXT NOT NULL DEFAULT {NOW},
      rebound_at     TEXT
    ) STRICT
    """,
    # session_uuid is NULLable: the "/new" (empty session id) command detaches the
    # binding (session_uuid = NULL) so the next DM starts a fresh SDK session; the
    # unique index is partial so many detached rows coexist.
    # Sessions are PER CHAT (feat_feishu_DM.md §5.2): one binding per
    # (account, feishu_chat_id) — every group and every p2p chat gets its own
    # session, and "/new" only resets the chat it was typed in.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_account_chat ON channel_binding(account_id, feishu_chat_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_session_active ON channel_binding(session_uuid) WHERE session_uuid IS NOT NULL",
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
    # 5b ── job_fire ---------------------------------------------------------
    # The leaderless exactly-once claim: INSERT-wins on the composite PK.
    # fire_epoch = the trigger's SCHEDULED instant (epoch seconds), so every
    # scheduler replica computes the same key for the same fire. Rows exist only
    # to dedupe concurrent claims; the scheduler's reconcile sweep prunes them.
    f"""
    CREATE TABLE IF NOT EXISTS job_fire (
      job_id     TEXT    NOT NULL REFERENCES scheduled_job(job_id) ON DELETE CASCADE,
      fire_epoch INTEGER NOT NULL,
      claimed_by TEXT    NOT NULL,
      claimed_at TEXT    NOT NULL DEFAULT {NOW},
      PRIMARY KEY (job_id, fire_epoch)
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS ix_fire_claimed_at ON job_fire(claimed_at)",
    # 6 ── account_resource_spec --------------------------------------------
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
    # 7 ── pending_registration ---------------------------------------------
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
    # 8 ── runner_defaults --------------------------------------------------
    # Platform-wide GLOBAL defaults for per-account agent-runner pods (the admin
    # "Agent Runner Sandbox" panel). Single row (id=1), seeded from the cluster
    # settings on first read. An account whose AgentTenant CR omits a field
    # inherits the matching value here; a per-account override (CR field present)
    # wins. No NOT NULL DEFAULTs — the row is seeded explicitly from settings so
    # the seed stays single-sourced in the service, not duplicated in the schema.
    f"""
    CREATE TABLE IF NOT EXISTS runner_defaults (
      id                           INTEGER PRIMARY KEY CHECK (id = 1),
      idle_grace_seconds           INTEGER NOT NULL,
      min_alive_after_wake_seconds INTEGER NOT NULL,
      cpu_cores                    REAL    NOT NULL,
      memory_mb                    INTEGER NOT NULL,
      storage_gb                   INTEGER NOT NULL,
      runner_image                 TEXT    NOT NULL,
      terminal_resource_percent    INTEGER NOT NULL DEFAULT 0,
      terminal_max_sessions        INTEGER NOT NULL DEFAULT 2,
      terminal_idle_timeout_seconds INTEGER NOT NULL DEFAULT 1800,
      terminal_max_lifetime_seconds INTEGER NOT NULL DEFAULT 14400,
      terminal_scale_down_grace_seconds INTEGER NOT NULL DEFAULT 120,
      updated_at                   TEXT    NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    # 9 ── hook_policy -------------------------------------------------------
    # Admin-stored hooks delivered to every agent-runner (the admin "Runtime"
    # panel). hook_type uses Claude Code native strings; predefined rows are the
    # legacy builtin hooks, seeded insert-if-absent by HookPolicyService at
    # startup (ids = legacy slugs). script_body is THE payload for command hooks
    # (runners materialize it on content_hash change); events / allowed_env_vars
    # ride as JSON-array TEXT per the wire convention.
    f"""
    CREATE TABLE IF NOT EXISTS hook_policy (
      id               TEXT PRIMARY KEY,
      hook_type        TEXT NOT NULL CHECK (hook_type IN ('command','http','mcp_tool')),
      name             TEXT NOT NULL,
      description      TEXT NOT NULL,
      events           TEXT NOT NULL,
      matcher          TEXT NOT NULL DEFAULT '',
      timeout_seconds  INTEGER NOT NULL DEFAULT 30,
      interpreter      TEXT NOT NULL DEFAULT '',
      script_body      TEXT NOT NULL DEFAULT '',
      content_hash     TEXT NOT NULL DEFAULT '',
      url              TEXT NOT NULL DEFAULT '',
      headers_json     TEXT NOT NULL DEFAULT '',
      allowed_env_vars TEXT NOT NULL DEFAULT '[]',
      mcp_server       TEXT NOT NULL DEFAULT '',
      mcp_tool         TEXT NOT NULL DEFAULT '',
      enabled          INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
      enforced         INTEGER NOT NULL DEFAULT 0 CHECK (enforced IN (0,1)),
      enforced_events  TEXT NOT NULL DEFAULT '[]',
      default_on       INTEGER NOT NULL DEFAULT 0 CHECK (default_on IN (0,1)),
      predefined       INTEGER NOT NULL DEFAULT 0 CHECK (predefined IN (0,1)),
      seed_version     INTEGER NOT NULL DEFAULT 0,
      target           TEXT NOT NULL DEFAULT '',
      updated_at       TEXT NOT NULL DEFAULT {NOW},
      updated_by       TEXT NOT NULL DEFAULT ''
    ) STRICT
    """,
    # 10 ── feishu_channel_config -------------------------------------------
    # Per-account Feishu bot config (Model B: each user's own self-built app).
    # Column classes are spec/status-separated (like a k8s status subresource);
    # write authority is enforced by which columns each caller lists in its
    # update_mask — the USER route writes credentials + user_enabled + behaviour,
    # the ADMIN route writes ONLY admin_disabled, the CONNECTOR writes ONLY the
    # status/* columns. app_secret_enc is Fernet-encrypted (never returned in
    # cleartext; the read DTO exposes only a boolean). The connector polls the
    # effective set and diffs on desired_digest (NOT updated_at — status
    # write-back must not perturb the diff).
    f"""
    CREATE TABLE IF NOT EXISTS feishu_channel_config (
      account_id              TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      -- desired · credentials (user-written only) --------------------------
      app_id                  TEXT,
      app_secret_enc          TEXT,
      app_secret_updated_at   TEXT,
      -- desired · enable double-gate --------------------------------------
      user_enabled            INTEGER NOT NULL DEFAULT 0 CHECK (user_enabled   IN (0,1)),
      admin_disabled          INTEGER NOT NULL DEFAULT 0 CHECK (admin_disabled IN (0,1)),
      -- desired · behaviour -----------------------------------------------
      single_chat_access_mode TEXT NOT NULL DEFAULT 'owner_only'
                              CHECK (single_chat_access_mode IN ('owner_only','allowlist','all')),
      allowed_union_ids       TEXT NOT NULL DEFAULT '[]',
      welcome_message         TEXT NOT NULL DEFAULT '',
      reject_message          TEXT NOT NULL DEFAULT '',
      model                   TEXT,
      max_queue_size          INTEGER NOT NULL DEFAULT 3,
      enable_permission_feedback INTEGER NOT NULL DEFAULT 1 CHECK (enable_permission_feedback IN (0,1)),
      feedback_timeout_seconds   INTEGER NOT NULL DEFAULT 180,
      domain                  TEXT NOT NULL DEFAULT 'feishu' CHECK (domain IN ('feishu','lark')),
      -- group-chat participation (feat_feishu_DM.md §5; user opt-in, default off) --
      group_chat_enabled      INTEGER NOT NULL DEFAULT 0 CHECK (group_chat_enabled IN (0,1)),
      -- owner link-code binding (feat_feishu_DM.md §4; ids in BOT app namespace) --
      owner_union_id          TEXT NOT NULL DEFAULT '',
      owner_open_id           TEXT NOT NULL DEFAULT '',
      owner_bound_at          TEXT,
      link_code_hash          TEXT,
      link_code_expires_at    TEXT,
      -- status · connector-written only -----------------------------------
      conn_status             TEXT NOT NULL DEFAULT 'disabled'
                              CHECK (conn_status IN ('disabled','connecting','connected','auth_failed','error','conflict')),
      last_error_code         INTEGER,
      last_error_message      TEXT,
      last_connected_at       TEXT,
      status_updated_at       TEXT,
      -- diff key + provenance ---------------------------------------------
      desired_digest          TEXT,
      updated_by              TEXT NOT NULL DEFAULT '',
      updated_at              TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
    # Partial index for the connector's list_effective() poll (enabled rows only;
    # the creds-present filter is applied in SQL on top of this).
    "CREATE INDEX IF NOT EXISTS ix_feishu_effective ON feishu_channel_config(account_id) "
    "WHERE user_enabled = 1 AND admin_disabled = 0",
    # 11 ── channel_platform_config -------------------------------------------
    # ADMIN-only platform-wide channel settings. Single row (id=1), same pattern
    # as runner_defaults. group_chat_disabled is the global group-chat kill
    # switch (feat_feishu_DM.md §5.1): the service folds it into every feishu
    # row's effective_group_enabled AND recomputes their desired_digest on flip.
    f"""
    CREATE TABLE IF NOT EXISTS channel_platform_config (
      id                  INTEGER PRIMARY KEY CHECK (id = 1),
      group_chat_disabled INTEGER NOT NULL DEFAULT 0 CHECK (group_chat_disabled IN (0,1)),
      updated_by          TEXT NOT NULL DEFAULT '',
      updated_at          TEXT NOT NULL DEFAULT {NOW}
    ) STRICT
    """,
)

TABLES = (
    "account", "channel_binding", "quota", "scheduled_job", "job_run_record", "job_fire",
    "account_resource_spec", "pending_registration", "runner_defaults", "hook_policy",
    "feishu_channel_config", "channel_platform_config",
)

# Idempotent column additions for DBs created before a column existed. CREATE
# TABLE IF NOT EXISTS won't alter an existing table, so each (table, column, ddl)
# is applied only when PRAGMA table_info shows the column missing. A bare ALTER
# would fail on the 2nd boot with "duplicate column name".
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("account", "agent_runner_type",
     "ALTER TABLE account ADD COLUMN agent_runner_type TEXT NOT NULL DEFAULT 'auto_scale' "
     "CHECK (agent_runner_type IN ('auto_scale','persistent'))"),
    ("hook_policy", "enforced_events",
     "ALTER TABLE hook_policy ADD COLUMN enforced_events TEXT NOT NULL DEFAULT '[]'"),
    ("account", "feishu_open_id",
     "ALTER TABLE account ADD COLUMN feishu_open_id TEXT"),
    ("runner_defaults", "terminal_resource_percent",
     "ALTER TABLE runner_defaults ADD COLUMN terminal_resource_percent INTEGER NOT NULL DEFAULT 0"),
    ("runner_defaults", "terminal_max_sessions",
     "ALTER TABLE runner_defaults ADD COLUMN terminal_max_sessions INTEGER NOT NULL DEFAULT 2"),
    ("runner_defaults", "terminal_idle_timeout_seconds",
     "ALTER TABLE runner_defaults ADD COLUMN terminal_idle_timeout_seconds INTEGER NOT NULL DEFAULT 1800"),
    ("runner_defaults", "terminal_max_lifetime_seconds",
     "ALTER TABLE runner_defaults ADD COLUMN terminal_max_lifetime_seconds INTEGER NOT NULL DEFAULT 14400"),
    ("runner_defaults", "terminal_scale_down_grace_seconds",
     "ALTER TABLE runner_defaults ADD COLUMN terminal_scale_down_grace_seconds INTEGER NOT NULL DEFAULT 120"),
    ("feishu_channel_config", "owner_union_id",
     "ALTER TABLE feishu_channel_config ADD COLUMN owner_union_id TEXT NOT NULL DEFAULT ''"),
    ("feishu_channel_config", "owner_open_id",
     "ALTER TABLE feishu_channel_config ADD COLUMN owner_open_id TEXT NOT NULL DEFAULT ''"),
    ("feishu_channel_config", "owner_bound_at",
     "ALTER TABLE feishu_channel_config ADD COLUMN owner_bound_at TEXT"),
    ("feishu_channel_config", "link_code_hash",
     "ALTER TABLE feishu_channel_config ADD COLUMN link_code_hash TEXT"),
    ("feishu_channel_config", "link_code_expires_at",
     "ALTER TABLE feishu_channel_config ADD COLUMN link_code_expires_at TEXT"),
    ("feishu_channel_config", "group_chat_enabled",
     "ALTER TABLE feishu_channel_config ADD COLUMN group_chat_enabled INTEGER NOT NULL DEFAULT 0 "
     "CHECK (group_chat_enabled IN (0,1))"),
)

# One-time backfills, safe to run every boot. Pre-migration rows carry
# enforced=1 with an empty enforced_events; post-migration the service derives
# enforced from enforced_events on every write, so this WHERE never matches
# again once a row has been normalized.
_BACKFILLS: tuple[str, ...] = (
    "UPDATE hook_policy SET enforced_events = events "
    "WHERE enforced = 1 AND enforced_events = '[]'",
    # Per-chat sessions (feat_feishu_DM.md §5.2): retire the one-binding-per-account
    # unique index; the composite ux_binding_account_chat is created by the DDL above.
    "DROP INDEX IF EXISTS ux_binding_account",
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(ddl)


def _migrate_binding_session_nullable(conn: sqlite3.Connection) -> None:
    """Drop the NOT NULL on channel_binding.session_uuid for DBs created before
    the "/new" detach flow. SQLite can't ALTER a column's NOT NULL in place, so
    rebuild the (greenfield/empty) table. Idempotent: skips once session_uuid is
    already nullable. Runs before the fresh-DDL index would notice, so it also
    retires the old non-partial ux_binding_session index."""
    info = conn.execute("PRAGMA table_info(channel_binding)").fetchall()
    if not info:
        return  # table not created yet — fresh DDL builds it nullable
    notnull = {row[1]: row[3] for row in info}  # row = (cid, name, type, notnull, dflt, pk)
    if notnull.get("session_uuid", 0) == 0:
        return  # already nullable
    conn.execute("DROP INDEX IF EXISTS ux_binding_account")
    conn.execute("DROP INDEX IF EXISTS ux_binding_session")
    conn.execute("ALTER TABLE channel_binding RENAME TO _channel_binding_old")
    conn.execute(f"""
    CREATE TABLE channel_binding (
      binding_id     TEXT PRIMARY KEY,
      account_id     TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
      session_uuid   TEXT,
      first_run_done INTEGER NOT NULL DEFAULT 0 CHECK (first_run_done IN (0,1)),
      feishu_chat_id TEXT,
      bound_at       TEXT NOT NULL DEFAULT {NOW},
      rebound_at     TEXT
    ) STRICT
    """)
    conn.execute(
        "INSERT INTO channel_binding "
        "(binding_id, account_id, session_uuid, first_run_done, feishu_chat_id, bound_at, rebound_at) "
        "SELECT binding_id, account_id, session_uuid, first_run_done, feishu_chat_id, bound_at, rebound_at "
        "FROM _channel_binding_old"
    )
    conn.execute("DROP TABLE _channel_binding_old")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_account_chat "
        "ON channel_binding(account_id, feishu_chat_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_session_active "
        "ON channel_binding(session_uuid) WHERE session_uuid IS NOT NULL"
    )


def create_all(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)
    _apply_migrations(conn)
    _migrate_binding_session_nullable(conn)
    for stmt in _BACKFILLS:
        conn.execute(stmt)

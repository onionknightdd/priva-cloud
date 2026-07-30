"""The data-spine Postgres schema — the PG-dialect twin of schema.py.

Row-shape parity with SQLite is deliberate so the service layer never knows
which backend it is on: timestamps stay TEXT ISO-8601 UTC (lexicographic ==
chronological — the keyset pagination and retention cutoffs rely on it) and
booleans stay 0/1 integers. create_all() is idempotent (CREATE ... IF NOT
EXISTS + ADD COLUMN IF NOT EXISTS).
"""

from __future__ import annotations

from .schema import TABLES  # single source of truth for the table whitelist

__all__ = ["DDL", "NOW", "TABLES", "create_all"]

# UTC ISO-8601 with millisecond precision, e.g. 2026-06-20T03:32:01.112Z —
# byte-compatible with schema.NOW's strftime('%Y-%m-%dT%H:%M:%fZ','now').
NOW = "to_char((now() AT TIME ZONE 'utc'), 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"

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
    )
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
      first_run_done BIGINT NOT NULL DEFAULT 0 CHECK (first_run_done IN (0,1)),
      feishu_chat_id TEXT,
      chat_type      TEXT NOT NULL DEFAULT '',
      chat_name      TEXT NOT NULL DEFAULT '',
      bound_at       TEXT NOT NULL DEFAULT {NOW},
      rebound_at     TEXT
    )
    """,
    # session_uuid NULLable: "/new" detaches the binding (NULL) → next DM starts a
    # fresh SDK session; the unique index is partial so detached rows coexist.
    # Sessions are PER CHAT (feat_feishu_DM.md §5.2): one binding per
    # (account, feishu_chat_id) — "/new" only resets the chat it was typed in.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_account_chat ON channel_binding(account_id, feishu_chat_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_session_active ON channel_binding(session_uuid) WHERE session_uuid IS NOT NULL",
    # 3 ── quota ------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS quota (
      account_id              TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      tier                    TEXT NOT NULL DEFAULT 'default',
      max_concurrent_sessions BIGINT NOT NULL DEFAULT 3,
      idle_grace_seconds      BIGINT NOT NULL DEFAULT 1800,
      updated_at              TEXT NOT NULL DEFAULT {NOW}
    )
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
    )
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
      duration_ms   BIGINT,
      is_error      BIGINT NOT NULL DEFAULT 0 CHECK (is_error IN (0,1)),
      error_message TEXT,
      num_turns     BIGINT,
      result_summary TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_run_account_started ON job_run_record(account_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_run_job_started     ON job_run_record(job_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_run_status          ON job_run_record(status) WHERE status = 'running'",
    # 5b ── job_fire ---------------------------------------------------------
    # The leaderless exactly-once claim (mirror of schema.py 5b): INSERT-wins on
    # the composite PK; concurrent INSERT ... ON CONFLICT DO NOTHING is native
    # here — no lock, no pre-filter needed.
    f"""
    CREATE TABLE IF NOT EXISTS job_fire (
      job_id     TEXT   NOT NULL REFERENCES scheduled_job(job_id) ON DELETE CASCADE,
      fire_epoch BIGINT NOT NULL,
      claimed_by TEXT   NOT NULL,
      claimed_at TEXT   NOT NULL DEFAULT {NOW},
      PRIMARY KEY (job_id, fire_epoch)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_fire_claimed_at ON job_fire(claimed_at)",
    # 6 ── account_resource_spec --------------------------------------------
    # Per-account agent-runner pod sizing. The operator reads these (via the CR
    # the control-panel stamps) to set container resources + PVC size. volume_gb
    # is grow-only (K8s can't shrink a PVC). cpu_cores is fractional.
    f"""
    CREATE TABLE IF NOT EXISTS account_resource_spec (
      account_id TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      cpu_cores  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      memory_mb  BIGINT NOT NULL DEFAULT 2048,
      volume_gb  BIGINT NOT NULL DEFAULT 1,
      updated_at TEXT   NOT NULL DEFAULT {NOW}
    )
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
      cpu_cores     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      memory_mb     BIGINT NOT NULL DEFAULT 2048,
      volume_gb     BIGINT NOT NULL DEFAULT 1,
      note          TEXT,
      status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
      created_at    TEXT NOT NULL DEFAULT {NOW},
      updated_at    TEXT NOT NULL DEFAULT {NOW}
    )
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
      id                           BIGINT PRIMARY KEY CHECK (id = 1),
      idle_grace_seconds           BIGINT NOT NULL,
      min_alive_after_wake_seconds BIGINT NOT NULL,
      cpu_cores                    DOUBLE PRECISION NOT NULL,
      memory_mb                    BIGINT NOT NULL,
      storage_gb                   BIGINT NOT NULL,
      terminal_resource_percent    BIGINT NOT NULL DEFAULT 0,
      terminal_max_sessions        BIGINT NOT NULL DEFAULT 2,
      terminal_idle_timeout_seconds BIGINT NOT NULL DEFAULT 1800,
      terminal_max_lifetime_seconds BIGINT NOT NULL DEFAULT 14400,
      terminal_scale_down_grace_seconds BIGINT NOT NULL DEFAULT 120,
      updated_at                   TEXT   NOT NULL DEFAULT {NOW}
    )
    """,
    # 9 ── hook_policy -------------------------------------------------------
    # Admin-stored hooks delivered to every agent-runner (the admin "Runtime"
    # panel). Mirror of schema.py: booleans as 0/1 BIGINTs, JSON arrays as TEXT.
    f"""
    CREATE TABLE IF NOT EXISTS hook_policy (
      id               TEXT PRIMARY KEY,
      hook_type        TEXT NOT NULL CHECK (hook_type IN ('command','http','mcp_tool')),
      name             TEXT NOT NULL,
      description      TEXT NOT NULL,
      events           TEXT NOT NULL,
      matcher          TEXT NOT NULL DEFAULT '',
      timeout_seconds  BIGINT NOT NULL DEFAULT 30,
      interpreter      TEXT NOT NULL DEFAULT '',
      script_body      TEXT NOT NULL DEFAULT '',
      content_hash     TEXT NOT NULL DEFAULT '',
      url              TEXT NOT NULL DEFAULT '',
      headers_json     TEXT NOT NULL DEFAULT '',
      allowed_env_vars TEXT NOT NULL DEFAULT '[]',
      mcp_server       TEXT NOT NULL DEFAULT '',
      mcp_tool         TEXT NOT NULL DEFAULT '',
      enabled          BIGINT NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
      enforced         BIGINT NOT NULL DEFAULT 0 CHECK (enforced IN (0,1)),
      enforced_events  TEXT NOT NULL DEFAULT '[]',
      default_on       BIGINT NOT NULL DEFAULT 0 CHECK (default_on IN (0,1)),
      predefined       BIGINT NOT NULL DEFAULT 0 CHECK (predefined IN (0,1)),
      seed_version     BIGINT NOT NULL DEFAULT 0,
      target           TEXT NOT NULL DEFAULT '',
      updated_at       TEXT NOT NULL DEFAULT {NOW},
      updated_by       TEXT NOT NULL DEFAULT ''
    )
    """,
    # 10 ── feishu_channel_config (mirror of schema.py: 0/1 flags as BIGINT) --
    f"""
    CREATE TABLE IF NOT EXISTS feishu_channel_config (
      account_id              TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
      app_id                  TEXT,
      app_secret_enc          TEXT,
      app_secret_updated_at   TEXT,
      user_enabled            BIGINT NOT NULL DEFAULT 0 CHECK (user_enabled   IN (0,1)),
      admin_disabled          BIGINT NOT NULL DEFAULT 0 CHECK (admin_disabled IN (0,1)),
      single_chat_access_mode TEXT NOT NULL DEFAULT 'owner_only'
                              CHECK (single_chat_access_mode IN ('owner_only','allowlist','all')),
      allowed_union_ids       TEXT NOT NULL DEFAULT '[]',
      welcome_message         TEXT NOT NULL DEFAULT '',
      reject_message          TEXT NOT NULL DEFAULT '',
      model                   TEXT,
      max_queue_size          BIGINT NOT NULL DEFAULT 3,
      enable_permission_feedback BIGINT NOT NULL DEFAULT 1 CHECK (enable_permission_feedback IN (0,1)),
      feedback_timeout_seconds   BIGINT NOT NULL DEFAULT 180,
      domain                  TEXT NOT NULL DEFAULT 'feishu' CHECK (domain IN ('feishu','lark')),
      group_chat_enabled      BIGINT NOT NULL DEFAULT 0 CHECK (group_chat_enabled IN (0,1)),
      owner_union_id          TEXT NOT NULL DEFAULT '',
      owner_open_id           TEXT NOT NULL DEFAULT '',
      owner_bound_at          TEXT,
      link_code_hash          TEXT,
      link_code_expires_at    TEXT,
      conn_status             TEXT NOT NULL DEFAULT 'disabled'
                              CHECK (conn_status IN ('disabled','connecting','connected','auth_failed','error','conflict')),
      last_error_code         BIGINT,
      last_error_message      TEXT,
      last_connected_at       TEXT,
      status_updated_at       TEXT,
      desired_digest          TEXT,
      updated_by              TEXT NOT NULL DEFAULT '',
      updated_at              TEXT NOT NULL DEFAULT {NOW}
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_feishu_effective ON feishu_channel_config(account_id) "
    "WHERE user_enabled = 1 AND admin_disabled = 0",
    # 11 ── channel_platform_config (mirror of schema.py: admin singleton, id=1) --
    f"""
    CREATE TABLE IF NOT EXISTS channel_platform_config (
      id                  BIGINT PRIMARY KEY CHECK (id = 1),
      group_chat_disabled BIGINT NOT NULL DEFAULT 0 CHECK (group_chat_disabled IN (0,1)),
      updated_by          TEXT NOT NULL DEFAULT '',
      updated_at          TEXT NOT NULL DEFAULT {NOW}
    )
    """,
    # 12 ── network_isolation (mirror of schema.py #12) ------------------------
    f"""
    CREATE TABLE IF NOT EXISTS network_isolation (
      id                     BIGINT PRIMARY KEY CHECK (id = 1),
      runner_deny_internal   BIGINT NOT NULL DEFAULT 1 CHECK (runner_deny_internal IN (0,1)),
      terminal_deny_internal BIGINT NOT NULL DEFAULT 1 CHECK (terminal_deny_internal IN (0,1)),
      deny_tenant_peers      BIGINT NOT NULL DEFAULT 1 CHECK (deny_tenant_peers IN (0,1)),
      egress_mode            TEXT NOT NULL DEFAULT 'allowlist'
                             CHECK (egress_mode IN ('unrestricted','allowlist','deny_all')),
      egress_allowlist       TEXT NOT NULL DEFAULT '[]',
      updated_at             TEXT NOT NULL DEFAULT {NOW}
    )
    """,
)

# Idempotent column additions for DBs created before a column existed (mirror of
# schema._MIGRATIONS). PG has native ADD COLUMN IF NOT EXISTS, so no table_info
# probing is needed.
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE account ADD COLUMN IF NOT EXISTS agent_runner_type TEXT NOT NULL "
    "DEFAULT 'auto_scale' CHECK (agent_runner_type IN ('auto_scale','persistent'))",
    "ALTER TABLE hook_policy ADD COLUMN IF NOT EXISTS enforced_events TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE account ADD COLUMN IF NOT EXISTS feishu_open_id TEXT",
    "ALTER TABLE runner_defaults ADD COLUMN IF NOT EXISTS terminal_resource_percent BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE runner_defaults ADD COLUMN IF NOT EXISTS terminal_max_sessions BIGINT NOT NULL DEFAULT 2",
    "ALTER TABLE runner_defaults ADD COLUMN IF NOT EXISTS terminal_idle_timeout_seconds BIGINT NOT NULL DEFAULT 1800",
    "ALTER TABLE runner_defaults ADD COLUMN IF NOT EXISTS terminal_max_lifetime_seconds BIGINT NOT NULL DEFAULT 14400",
    "ALTER TABLE runner_defaults ADD COLUMN IF NOT EXISTS terminal_scale_down_grace_seconds BIGINT NOT NULL DEFAULT 120",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS owner_union_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS owner_open_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS owner_bound_at TEXT",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS link_code_hash TEXT",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS link_code_expires_at TEXT",
    "ALTER TABLE feishu_channel_config ADD COLUMN IF NOT EXISTS group_chat_enabled BIGINT NOT NULL DEFAULT 0 "
    "CHECK (group_chat_enabled IN (0,1))",
    # session_uuid: drop NOT NULL for the "/new" detach flow, and retire the old
    # non-partial index in favour of the partial ux_binding_session_active (created
    # by the DDL above). All idempotent — safe to run every boot.
    "ALTER TABLE channel_binding ALTER COLUMN session_uuid DROP NOT NULL",
    "DROP INDEX IF EXISTS ux_binding_session",
    # Per-chat sessions (feat_feishu_DM.md §5.2): retire the one-binding-per-account
    # unique index; the composite ux_binding_account_chat comes from the DDL above.
    "DROP INDEX IF EXISTS ux_binding_account",
    "ALTER TABLE channel_binding ADD COLUMN IF NOT EXISTS chat_type TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE channel_binding ADD COLUMN IF NOT EXISTS chat_name TEXT NOT NULL DEFAULT ''",
    # The runner image moved out of runner_defaults: the operator's deployment
    # settings are the authority (AgentTenant spec.image = per-account override).
    "ALTER TABLE runner_defaults DROP COLUMN IF EXISTS runner_image",
    # One-time backfill, safe every boot: pre-migration rows carry enforced=1
    # with an empty enforced_events; the service keeps enforced derived from
    # enforced_events afterwards, so this WHERE never matches again.
    "UPDATE hook_policy SET enforced_events = events "
    "WHERE enforced = 1 AND enforced_events = '[]'",
)


def create_all(conn) -> None:
    """Run the idempotent DDL on a psycopg connection (caller commits)."""
    for stmt in DDL:
        conn.execute(stmt)
    for stmt in _MIGRATIONS:
        conn.execute(stmt)

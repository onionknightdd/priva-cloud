"""Idempotent SQLite → Postgres copier (the backend-cutover data move).

Reads the SQLite file read-only and inserts row-for-row into Postgres with
ON CONFLICT DO NOTHING per primary key, so re-runs converge. Rows move
byte-for-byte — password hashes, encrypted api_keys and their HMAC lookups
stay valid as long as the same shared secrets are configured on both sides.

Run it with the data-spine writer idle (or briefly stopped): rows written to
SQLite after a table has been read are not seen by that invocation (re-running
after the flip picks up stragglers — inserts are skip-if-exists).
"""

from __future__ import annotations

import os
import sqlite3

# (table, primary key) in FK-safe order: account first, jobs before runs.
_TABLES: tuple[tuple[str, str], ...] = (
    ("account", "account_id"),
    ("quota", "account_id"),
    ("channel_binding", "binding_id"),
    ("scheduled_job", "job_id"),
    ("job_run_record", "run_id"),
    ("job_fire", "job_id, fire_epoch"),
    ("account_resource_spec", "account_id"),
    ("pending_registration", "request_id"),
    ("runner_defaults", "id"),
    ("hook_policy", "id"),
)


def run_copy(sqlite_path: str, pg_dsn: str, *, dry_run: bool = False) -> dict:
    """Copy all data-spine tables SQLite → PG. Returns per-table counts."""
    import psycopg
    from psycopg.rows import dict_row

    from . import schema_pg

    # mode=ro: never mutates the source, and fails loudly on a wrong path
    # (a bare connect() would silently create an empty DB).
    src = sqlite3.connect(f"file:{os.path.expanduser(sqlite_path)}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    counts: dict[str, dict] = {}
    try:
        with psycopg.connect(pg_dsn, row_factory=dict_row) as dst:
            schema_pg.create_all(dst)
            for table, pk in _TABLES:
                rows = [dict(r) for r in src.execute(f"SELECT * FROM {table}")]
                if dry_run:
                    n_dst = dst.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                    counts[table] = {"source": len(rows), "target": n_dst}
                    continue
                inserted = 0
                for row in rows:
                    cols = list(row.keys())
                    ph = ", ".join("%s" for _ in cols)
                    cur = dst.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph}) "
                        f"ON CONFLICT ({pk}) DO NOTHING",
                        tuple(row[c] for c in cols),
                    )
                    inserted += cur.rowcount
                counts[table] = {
                    "source": len(rows),
                    "inserted": inserted,
                    "skipped": len(rows) - inserted,
                }
            # single transaction: commits on clean context exit, rolls back on error
    finally:
        src.close()
    return counts

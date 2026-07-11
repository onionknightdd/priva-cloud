"""SQLite → Postgres copy (`data-spine migrate-to-pg`): per-table counts,
byte-parity of credentials (bcrypt login + encrypted api_key + HMAC lookup all
work on the PG side), and idempotent re-run. Runs only when TEST_POSTGRES_DSN
is set (e.g. postgresql://postgres:test@127.0.0.1:5433/priva).
"""

from __future__ import annotations

import os

import pytest

PG_DSN = os.environ.get("TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not PG_DSN, reason="TEST_POSTGRES_DSN not set")


def test_copy_counts_credentials_idempotency(tmp_path):
    from priva_common.config import Settings
    from priva_data_spine.copy_to_pg import run_copy
    from priva_data_spine.repo import PgRepo, SqliteRepo
    from priva_data_spine.service import (
        AccountService,
        BindingService,
        RegistrationService,
        ResourceSpecService,
        RunnerDefaultsService,
    )

    from .test_dataplane_grpc import wipe_pg

    wipe_pg(PG_DSN)
    s = Settings()
    s.dataspine.sqlite_path = str(tmp_path / "src.db")

    # Seed every table through the real services (bcrypt, Fernet, HMAC lookup).
    src = SqliteRepo(s.dataspine.sqlite_path)
    accounts = AccountService(src, s)
    alice = accounts.create("alice", "pw", "admin")            # also seeds quota
    accounts.update(alice.account_id, api_key="sk-live-key")   # ciphertext + lookup
    BindingService(src).bind(alice.account_id, "sess-1")
    ResourceSpecService(src).set(alice.account_id, cpu_cores=2.0, memory_mb=4096)
    RunnerDefaultsService(src, s).get()                        # seeds the single row
    RegistrationService(src).create(username="bob", password_hash="$2b$fake")
    src.job_insert({
        "job_id": "job-1", "account_id": alice.account_id, "name": "j",
        "prompt": "", "trigger": '{"trigger_type":"interval","seconds":60}',
        "job_type": "agent_run", "timezone": "UTC", "status": "active",
    })
    src.run_insert({
        "run_id": "run-1", "job_id": "job-1", "job_name": "j",
        "account_id": alice.account_id,
        "started_at": "2026-07-11T00:00:00.000Z", "status": "success",
    })
    src.close()

    # dry-run reports source/target counts without writing
    dry = run_copy(s.dataspine.sqlite_path, PG_DSN, dry_run=True)
    assert dry["account"] == {"source": 1, "target": 0}

    counts = run_copy(s.dataspine.sqlite_path, PG_DSN)
    expect_one = ("account", "quota", "channel_binding", "scheduled_job",
                  "job_run_record", "account_resource_spec",
                  "pending_registration", "runner_defaults")
    for table in expect_one:
        assert counts[table]["inserted"] == 1, (table, counts[table])

    # credentials moved byte-for-byte: login + api-key auth work against PG
    dst = PgRepo(PG_DSN)
    try:
        pg_accounts = AccountService(dst, s)
        assert pg_accounts.verify_password("alice", "pw") is True
        found = pg_accounts.find_by_api_key("sk-live-key")
        assert found is not None and found.account_id == alice.account_id
        assert dst.run_get("run-1")["job_id"] == "job-1"       # FK order held

        # re-run converges: everything skipped, nothing double-inserted
        again = run_copy(s.dataspine.sqlite_path, PG_DSN)
        assert all(c["inserted"] == 0 and c["skipped"] == c["source"]
                   for c in again.values()), again
    finally:
        dst.close()

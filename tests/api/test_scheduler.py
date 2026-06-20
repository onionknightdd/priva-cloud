"""Tests for scheduler components: models, job store, run history, shared utilities."""
from __future__ import annotations
import pytest
pytestmark = pytest.mark.skip(reason="scheduler service deferred to Phase 4")

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from priva.api.models.scheduler import (
    CreateJobRequest,
    CronTriggerConfig,
    IntervalTriggerConfig,
    JobRunRecord,
    ScheduledJobDefinition,
    UpdateJobRequest,
)
from priva.api.services.scheduler.shared import build_trigger, write_command


class TriggerConfigTests(unittest.TestCase):
    def test_cron_trigger_config(self) -> None:
        config = CronTriggerConfig(expr="0 9 * * mon-fri")
        self.assertEqual(config.type, "cron")
        self.assertEqual(config.expr, "0 9 * * mon-fri")

    def test_interval_trigger_config(self) -> None:
        config = IntervalTriggerConfig(hours=1, minutes=30)
        self.assertEqual(config.type, "interval")
        self.assertEqual(config.hours, 1)
        self.assertEqual(config.minutes, 30)

    def test_build_cron_trigger(self) -> None:
        config = CronTriggerConfig(expr="0 9 * * *")
        trigger = build_trigger(config, "Asia/Shanghai")
        self.assertIsNotNone(trigger)

    def test_build_interval_trigger(self) -> None:
        config = IntervalTriggerConfig(hours=2)
        trigger = build_trigger(config, "UTC")
        self.assertIsNotNone(trigger)


class ScheduledJobDefinitionTests(unittest.TestCase):
    def test_create_job_definition(self) -> None:
        job = ScheduledJobDefinition(
            id="test-1",
            name="Test Job",
            prompt="Do something",
            trigger=CronTriggerConfig(expr="0 9 * * *"),
            timezone="Asia/Shanghai",
        )
        self.assertEqual(job.id, "test-1")
        self.assertEqual(job.status, "active")
        self.assertIsNone(job.model)

    def test_job_definition_json_roundtrip(self) -> None:
        job = ScheduledJobDefinition(
            id="test-2",
            name="Test Job 2",
            prompt="Do something else",
            trigger=IntervalTriggerConfig(minutes=10),
            timezone="UTC",
            model="claude-sonnet-4-6",
        )
        data = job.model_dump(mode="json")
        restored = ScheduledJobDefinition.model_validate(data)
        self.assertEqual(restored.id, job.id)
        self.assertEqual(restored.trigger.type, "interval")
        self.assertEqual(restored.model, "claude-sonnet-4-6")


class JobRunRecordTests(unittest.TestCase):
    def test_create_run_record(self) -> None:
        record = JobRunRecord(
            run_id="run-1",
            job_id="job-1",
            job_name="Test",
            username="alice",
            status="running",
        )
        self.assertEqual(record.status, "running")
        self.assertFalse(record.is_error)

    def test_run_record_success(self) -> None:
        record = JobRunRecord(
            run_id="run-2",
            job_id="job-1",
            job_name="Test",
            username="alice",
            status="success",
            duration_ms=5000,
            num_turns=3,
            total_cost_usd=0.05,
            result_summary="Completed successfully",
        )
        self.assertEqual(record.duration_ms, 5000)
        self.assertFalse(record.is_error)

    def test_run_record_error(self) -> None:
        record = JobRunRecord(
            run_id="run-3",
            job_id="job-1",
            job_name="Test",
            username="alice",
            status="error",
            is_error=True,
            error_message="API credentials not configured",
        )
        self.assertTrue(record.is_error)


class CreateJobRequestTests(unittest.TestCase):
    def test_create_request(self) -> None:
        req = CreateJobRequest(
            name="Daily Report",
            prompt="Generate a report",
            trigger=CronTriggerConfig(expr="0 9 * * *"),
            timezone="Asia/Shanghai",
        )
        self.assertEqual(req.status, "active")

    def test_update_request_all_optional(self) -> None:
        req = UpdateJobRequest()
        self.assertIsNone(req.name)
        self.assertIsNone(req.prompt)


class WriteCommandTests(unittest.TestCase):
    def test_write_command_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("priva.api.services.scheduler.shared.get_commands_dir") as mock:
                cmd_dir = Path(tmpdir) / "commands"
                cmd_dir.mkdir()
                mock.return_value = cmd_dir

                write_command("reload_user", {"username": "alice"})

                files = list(cmd_dir.glob("*.json"))
                self.assertEqual(len(files), 1)

                with open(files[0]) as f:
                    data = json.load(f)
                self.assertEqual(data["type"], "reload_user")
                self.assertEqual(data["payload"]["username"], "alice")

    def test_write_multiple_commands_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("priva.api.services.scheduler.shared.get_commands_dir") as mock:
                cmd_dir = Path(tmpdir) / "commands"
                cmd_dir.mkdir()
                mock.return_value = cmd_dir

                write_command("reload_user", {"username": "alice"})
                write_command("trigger_now", {"username": "alice", "job_id": "j1"})

                files = sorted(cmd_dir.glob("*.json"), key=lambda f: f.name)
                self.assertEqual(len(files), 2)

                with open(files[0]) as f:
                    self.assertEqual(json.load(f)["type"], "reload_user")
                with open(files[1]) as f:
                    self.assertEqual(json.load(f)["type"], "trigger_now")


class _DataSpineStoreTest(unittest.TestCase):
    """Base: a fresh in-process data-plane on a temp SQLite, with test accounts.

    Replaces the old file-backed store tests (daily-partition JSONL + legacy
    migration + counts sidecar) — that storage mechanism was replaced by
    data-spine (SQLite) in Phase 1 (J1/R1).
    """

    def setUp(self) -> None:
        from priva_common.config import get_settings
        from priva_common.dataplane import set_inprocess_handlers
        from priva_data_spine.repo import SqliteRepo
        from priva_data_spine.service import build_inprocess_client
        from priva.api.services.user_store import get_user_store

        self._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self._tmp.name, "ds.db")
        set_inprocess_handlers(build_inprocess_client(SqliteRepo(db), get_settings()))
        us = get_user_store()
        us.create_user("alice", "pw12345678")
        us.create_user("bob", "pw12345678")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class JobStoreTests(_DataSpineStoreTest):
    def setUp(self) -> None:
        super().setUp()
        from priva.api.services.scheduler.job_store import get_job_store
        self.store = get_job_store()

    def test_save_and_load_jobs(self) -> None:
        self.store.save_jobs("alice", [
            ScheduledJobDefinition(id="job-1", name="Test Job", prompt="Do something",
                                   trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
        ])
        loaded = self.store.list_jobs("alice")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].id, "job-1")
        self.assertEqual(loaded[0].name, "Test Job")

    def test_get_job(self) -> None:
        self.store.save_jobs("alice", [
            ScheduledJobDefinition(id="j1", name="Job 1", prompt="p1", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
            ScheduledJobDefinition(id="j2", name="Job 2", prompt="p2", trigger=CronTriggerConfig(expr="0 10 * * *"), timezone="UTC"),
        ])
        found = self.store.get_job("alice", "j2")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "Job 2")
        self.assertIsNone(self.store.get_job("alice", "nonexistent"))

    def test_save_jobs_diff_update_and_delete(self) -> None:
        self.store.save_jobs("alice", [
            ScheduledJobDefinition(id="a", name="A", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
            ScheduledJobDefinition(id="b", name="B", prompt="p", trigger=IntervalTriggerConfig(minutes=5), timezone="UTC"),
        ])
        self.store.save_jobs("alice", [
            ScheduledJobDefinition(id="b", name="B2", prompt="p", trigger=IntervalTriggerConfig(minutes=10), timezone="UTC"),
            ScheduledJobDefinition(id="c", name="C", prompt="p", trigger=CronTriggerConfig(expr="0 0 * * *"), timezone="UTC"),
        ])
        self.assertEqual({j.id for j in self.store.list_jobs("alice")}, {"b", "c"})
        self.assertEqual(self.store.get_job("alice", "b").name, "B2")
        self.assertIsNone(self.store.get_job("alice", "a"))

    def test_empty_user(self) -> None:
        self.assertEqual(self.store.list_jobs("alice"), [])

    def test_unknown_user(self) -> None:
        self.assertEqual(self.store.list_jobs("nobody"), [])

    def test_list_all_user_jobs(self) -> None:
        self.store.save_jobs("alice", [
            ScheduledJobDefinition(id="a", name="A", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
        ])
        allj = self.store.list_all_user_jobs()
        self.assertIn("alice", allj)
        self.assertEqual(len(allj["alice"]), 1)
        self.assertNotIn("bob", allj)


class RunHistoryStoreTests(_DataSpineStoreTest):
    def setUp(self) -> None:
        super().setUp()
        from priva.api.services.scheduler.job_store import get_job_store
        from priva.api.services.scheduler.run_history import get_run_history_store
        self.store = get_run_history_store()
        # Jobs j1/j2 must exist so run.job_id linkage (FK) is preserved.
        get_job_store().save_jobs("alice", [
            ScheduledJobDefinition(id="j1", name="J1", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
            ScheduledJobDefinition(id="j2", name="J2", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
        ])

    def test_append_and_query(self) -> None:
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="Test", username="alice", status="success"))
        self.store.append(JobRunRecord(run_id="r2", job_id="j1", job_name="Test", username="alice", status="error", is_error=True))
        runs, total = self.store.query("alice")
        self.assertEqual(total, 2)
        self.assertEqual(runs[0].run_id, "r2")        # newest first
        self.assertEqual(runs[0].username, "alice")   # re-stamped on read

    def test_query_filter_by_job_id(self) -> None:
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T1", username="alice"))
        self.store.append(JobRunRecord(run_id="r2", job_id="j2", job_name="T2", username="alice"))
        runs, _ = self.store.query("alice", job_id="j1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].job_id, "j1")

    def test_get_run_ownership(self) -> None:
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T1", username="alice"))
        self.assertIsNotNone(self.store.get_run("alice", "r1"))
        self.assertIsNone(self.store.get_run("bob", "r1"))

    def test_upsert_by_run_id(self) -> None:
        # birth (running) then outcome (success) — same run_id upserts to one row.
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T1", username="alice", status="running"))
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T1", username="alice", status="success", duration_ms=5000))
        runs, total = self.store.query("alice")
        self.assertEqual(total, 1)
        self.assertEqual(runs[0].status, "success")
        self.assertEqual(runs[0].duration_ms, 5000)

    def test_get_latest_run(self) -> None:
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T", username="alice", status="success",
                                       started_at=datetime(2026, 3, 30, 10, tzinfo=timezone.utc)))
        self.store.append(JobRunRecord(run_id="r2", job_id="j1", job_name="T", username="alice", status="success",
                                       started_at=datetime(2026, 3, 31, 10, tzinfo=timezone.utc)))
        self.assertEqual(self.store.get_latest_run("alice", "j1").run_id, "r2")

    def test_purge_old_records(self) -> None:
        today = datetime.now(timezone.utc).date()
        for i in range(10):
            d = today - timedelta(days=i)
            self.store.append(JobRunRecord(run_id=f"r{i}", job_id="j1", job_name="T", username="alice",
                                           status="success",
                                           started_at=datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)))
        self.assertEqual(self.store.purge_old_records("alice", 7), 3)  # keep 7 most recent days
        _, total = self.store.query("alice")
        self.assertEqual(total, 7)

    def test_purge_disabled_when_zero(self) -> None:
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T", username="alice", status="success",
                                       started_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        self.assertEqual(self.store.purge_old_records("alice", 0), 0)
        _, total = self.store.query("alice")
        self.assertEqual(total, 1)

    def test_purge_cleans_run_outputs(self) -> None:
        from unittest.mock import patch as _patch
        runs_dir = Path(self._tmp.name) / "runs" / "alice"
        runs_dir.mkdir(parents=True)
        old = datetime.now(timezone.utc).date() - timedelta(days=30)
        self.store.append(JobRunRecord(run_id="old-run-1", job_id="j1", job_name="T", username="alice",
                                       status="success",
                                       started_at=datetime(old.year, old.month, old.day, 12, tzinfo=timezone.utc)))
        output_file = runs_dir / "old-run-1.jsonl"
        output_file.write_text('{"event":"result"}\n')
        with _patch("priva.api.services.scheduler.run_history.get_user_runs_dir", return_value=runs_dir):
            self.store.purge_old_records("alice", 7)
        self.assertFalse(output_file.exists())


if __name__ == "__main__":
    unittest.main()

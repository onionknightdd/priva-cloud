"""Tests for cursor-paginated stores: scheduler run_history, audit_log, hooks log_store."""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from priva_common.models.scheduler import (
    CronTriggerConfig,
    JobRunRecord,
    ScheduledJobDefinition,
)
from priva_common.models.hooks import HookLogEntry
from priva_common._pagination import decode_cursor, encode_cursor


class CursorEncodingTests(unittest.TestCase):
    def test_roundtrip(self):
        ts = "2026-05-15T12:00:00+00:00"
        rid = "abc-123"
        c = encode_cursor(ts, rid)
        ts2, rid2 = decode_cursor(c)
        self.assertEqual(ts2, ts)
        self.assertEqual(rid2, rid)

    def test_id_with_pipe(self):
        # Should still decode cleanly: split only on first '|'
        ts = "2026-05-15T12:00:00+00:00"
        rid = "a|b|c"
        c = encode_cursor(ts, rid)
        ts2, rid2 = decode_cursor(c)
        self.assertEqual(ts2, ts)
        self.assertEqual(rid2, rid)


@unittest.skip("scheduler stores deferred to Phase 4")
class SchedulerCursorTests(unittest.TestCase):
    """Cursor pagination for RunHistoryStore (data-spine / SQLite backend)."""

    def setUp(self):
        from priva_common.config import get_settings
        from priva_common.dataplane import set_inprocess_handlers
        from priva_data_spine.repo import SqliteRepo
        from priva_data_spine.service import build_inprocess_client
        from priva_common.user_store import get_user_store
        from priva.api.services.scheduler.job_store import get_job_store
        from priva.api.services.scheduler.run_history import get_run_history_store

        self._tmp = tempfile.TemporaryDirectory()
        set_inprocess_handlers(build_inprocess_client(SqliteRepo(f"{self._tmp.name}/ds.db"), get_settings()))
        get_user_store().create_user("alice", "pw12345678")
        get_job_store().save_jobs("alice", [
            ScheduledJobDefinition(id="j1", name="J1", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
            ScheduledJobDefinition(id="j2", name="J2", prompt="p", trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"),
        ])
        self.store = get_run_history_store()

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_page_returns_newest_first(self):
        base = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            self.store.append(JobRunRecord(run_id=f"r{i}", job_id="j1", job_name="T", username="alice",
                                           status="success", started_at=base + timedelta(minutes=i)))
        runs, next_cursor, prev_cursor, total = self.store.query_cursor("alice", limit=3)
        self.assertEqual([r.run_id for r in runs], ["r4", "r3", "r2"])
        self.assertIsNotNone(next_cursor)
        self.assertIsNone(prev_cursor)  # initial page
        self.assertEqual(total, 5)

    def test_paging_with_before_cursor(self):
        base = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(7):
            self.store.append(JobRunRecord(run_id=f"r{i}", job_id="j1", job_name="T", username="alice",
                                           status="success", started_at=base + timedelta(minutes=i)))
        runs1, c1, _, _ = self.store.query_cursor("alice", limit=3)
        self.assertEqual([r.run_id for r in runs1], ["r6", "r5", "r4"])
        runs2, c2, p2, _ = self.store.query_cursor("alice", limit=3, before=c1)
        self.assertEqual([r.run_id for r in runs2], ["r3", "r2", "r1"])
        self.assertIsNotNone(c2)
        self.assertIsNotNone(p2)
        runs3, c3, p3, _ = self.store.query_cursor("alice", limit=3, before=c2)
        self.assertEqual([r.run_id for r in runs3], ["r0"])
        self.assertIsNone(c3)  # exhausted
        self.assertIsNotNone(p3)

    def test_paging_across_days(self):
        # 2 records per day across 5 days; keyset paging must not duplicate.
        for day in range(5):
            for slot in range(2):
                t = datetime(2026, 5, 10 + day, 8 + slot, 0, 0, tzinfo=timezone.utc)
                self.store.append(JobRunRecord(run_id=f"d{day}-s{slot}", job_id="j1", job_name="T",
                                               username="alice", status="success", started_at=t))
        runs1, c1, _, total = self.store.query_cursor("alice", limit=4)
        self.assertEqual(len(runs1), 4)
        self.assertEqual(total, 10)
        self.assertEqual(runs1[0].run_id, "d4-s1")  # newest first
        runs2, _, _, _ = self.store.query_cursor("alice", limit=4, before=c1)
        self.assertEqual(len(runs2), 4)
        self.assertEqual(len({r.run_id for r in runs1} & {r.run_id for r in runs2}), 0)

    def test_filter_returns_null_total(self):
        self.store.append(JobRunRecord(run_id="r1", job_id="j1", job_name="T", username="alice", status="success"))
        self.store.append(JobRunRecord(run_id="r2", job_id="j2", job_name="T", username="alice", status="success"))
        runs, _, _, total = self.store.query_cursor("alice", limit=10, job_id="j1")
        self.assertEqual(len(runs), 1)
        self.assertIsNone(total)  # total unknown when a filter is active
        _, _, _, total2 = self.store.query_cursor("alice", limit=10)
        self.assertEqual(total2, 2)


class AuditCursorTests(unittest.TestCase):
    def _logger(self, tmpdir):
        from priva_common.audit_log import AuditLogger
        return AuditLogger(base_dir=Path(tmpdir))

    def test_append_and_page(self):
        from priva_common.audit_log import AuditEntry
        with tempfile.TemporaryDirectory() as tmpdir:
            log = self._logger(tmpdir)

            base = datetime(2026, 5, 15, 10, 0, 0)
            for i in range(5):
                log.append(AuditEntry(
                    timestamp=base + timedelta(minutes=i),
                    actor="alice",
                    action="user.created",
                    target=f"u{i}",
                ))

            entries, next_cursor, prev_cursor, total = log.query_cursor(limit=3)
            self.assertEqual(len(entries), 3)
            self.assertEqual(total, 5)
            self.assertIsNotNone(next_cursor)
            self.assertIsNone(prev_cursor)

            entries2, c2, p2, _ = log.query_cursor(limit=3, before=next_cursor)
            self.assertEqual(len(entries2), 2)
            self.assertIsNone(c2)
            self.assertIsNotNone(p2)
            ids = {e.id for e in entries + entries2}
            self.assertEqual(len(ids), 5)

    def test_filter_returns_null_total(self):
        from priva_common.audit_log import AuditEntry
        with tempfile.TemporaryDirectory() as tmpdir:
            log = self._logger(tmpdir)
            log.append(AuditEntry(actor="alice", action="login.success"))
            log.append(AuditEntry(actor="bob", action="user.created", target="alice"))

            _, _, _, total = log.query_cursor(limit=10, actor_filter="alice")
            self.assertIsNone(total)

            _, _, _, total = log.query_cursor(limit=10)
            self.assertEqual(total, 2)

    def test_migration_from_legacy(self):
        from priva_common.audit_log import AuditEntry, AuditLogger
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Write a legacy file directly
            legacy = base / ".priva.audit.jsonl"
            with open(legacy, "w") as f:
                e1 = AuditEntry(
                    timestamp=datetime(2026, 3, 30, 10, 0, 0),
                    actor="alice", action="login.success",
                )
                e2 = AuditEntry(
                    timestamp=datetime(2026, 3, 31, 10, 0, 0),
                    actor="bob", action="user.created", target="charlie",
                )
                f.write(e1.model_dump_json() + "\n")
                f.write(e2.model_dump_json() + "\n")

            log = AuditLogger(base_dir=base)
            entries, _, _, total = log.query_cursor(limit=10)
            self.assertEqual(len(entries), 2)
            self.assertEqual(total, 2)
            self.assertFalse(legacy.exists())
            self.assertTrue((base / ".priva.audit.2026-03-30.jsonl").exists())
            self.assertTrue((base / ".priva.audit.2026-03-31.jsonl").exists())


class HookLogCursorTests(unittest.TestCase):
    def test_append_and_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("priva_agent_runner.services.hooks.log_store._get_work_dir", return_value=Path(tmpdir)):
                from priva_agent_runner.services.hooks.log_store import HookLogStore
                store = HookLogStore()
                (Path(tmpdir) / "alice").mkdir()

                base = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
                for i in range(5):
                    store.append("alice", HookLogEntry(
                        timestamp=(base + timedelta(minutes=i)).isoformat(),
                        event_type="PreToolUse",
                        handler_type="command",
                        exit_code=0,
                        duration_ms=10,
                    ))

                entries, c, p, total = store.query_cursor("alice", limit=3)
                self.assertEqual(len(entries), 3)
                self.assertEqual(total, 5)
                self.assertIsNotNone(c)
                self.assertIsNone(p)

                entries2, c2, _, _ = store.query_cursor("alice", limit=3, before=c)
                self.assertEqual(len(entries2), 2)
                self.assertIsNone(c2)

    def test_event_type_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("priva_agent_runner.services.hooks.log_store._get_work_dir", return_value=Path(tmpdir)):
                from priva_agent_runner.services.hooks.log_store import HookLogStore
                store = HookLogStore()
                (Path(tmpdir) / "alice").mkdir()

                base = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
                for i, et in enumerate(["A", "B", "A", "B", "A"]):
                    store.append("alice", HookLogEntry(
                        timestamp=(base + timedelta(minutes=i)).isoformat(),
                        event_type=et, handler_type="command",
                        exit_code=0, duration_ms=10,
                    ))

                entries, _, _, total = store.query_cursor("alice", limit=10, event_type="A")
                self.assertEqual(len(entries), 3)
                self.assertIsNone(total)  # filter active

    def test_migration_from_legacy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("priva_agent_runner.services.hooks.log_store._get_work_dir", return_value=Path(tmpdir)):
                from priva_agent_runner.services.hooks.log_store import HookLogStore

                user_dir = Path(tmpdir) / "alice"
                user_dir.mkdir()
                legacy = user_dir / ".priva.hooks.log.jsonl"

                with open(legacy, "w") as f:
                    e1 = HookLogEntry(
                        timestamp="2026-03-30T10:00:00+00:00",
                        event_type="PreToolUse", handler_type="command",
                        exit_code=0, duration_ms=10,
                    )
                    e2 = HookLogEntry(
                        timestamp="2026-03-31T14:00:00+00:00",
                        event_type="PostToolUse", handler_type="command",
                        exit_code=0, duration_ms=10,
                    )
                    f.write(e1.model_dump_json() + "\n")
                    f.write(e2.model_dump_json() + "\n")

                store = HookLogStore()
                entries, _, _, total = store.query_cursor("alice", limit=10)
                self.assertEqual(len(entries), 2)
                self.assertEqual(total, 2)
                self.assertFalse(legacy.exists())
                self.assertTrue((user_dir / ".priva.hooks.log.2026-03-30.jsonl").exists())
                self.assertTrue((user_dir / ".priva.hooks.log.2026-03-31.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

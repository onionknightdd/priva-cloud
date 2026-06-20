"""Idempotent migrator: monolith YAML/JSONL → the 5 data-spine SQLite tables.

Sources (read directly; no priva.api import):
  - account + quota ← {priva_home}/.priva.settings.yml (users map)
  - scheduled_job   ← {work_dir}/{username}/.priva.user.yml (scheduled_jobs[])
  - job_run_record  ← {work_dir}/{username}/.priva.scheduler.history.<date>.jsonl

Idempotent: existing rows (by username / job_id / run_id) are skipped, so a second
run yields the same counts. channel_binding is greenfield (left empty).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import yaml

from priva_common.crypto import decrypt_value
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition

from .service import AccountService, SchedulerService, build_repo


def _priva_home() -> Path:
    # Mirror of priva.api.services.paths.priva_home (avoid importing the monolith).
    raw = os.environ.get("PRIVA_HOME")
    base = Path(raw).expanduser() if raw else Path.home() / ".config"
    return base / "priva"


def run_migration(settings=None, dry_run: bool = False) -> dict:
    from priva_common.config import get_settings

    s = settings or get_settings()
    repo = build_repo(s)
    accounts = AccountService(repo, s)
    scheduler = SchedulerService(repo)
    work_dir = Path(os.path.expanduser(s.server.work_dir))
    counts = {"accounts": 0, "quota": 0, "jobs": 0, "runs": 0, "skipped": 0}

    # 1 ── accounts (+ seed quota) -----------------------------------------
    settings_file = _priva_home() / ".priva.settings.yml"
    users: dict = {}
    if settings_file.exists():
        data = yaml.safe_load(settings_file.read_text()) or {}
        users = data.get("users", {}) or {}
    for username, info in users.items():
        if repo.account_get_by_username(username):
            counts["skipped"] += 1
            continue
        counts["accounts"] += 1
        counts["quota"] += 1
        if dry_run:
            continue
        account_id = uuid.uuid4().hex
        api_key = info.get("api_key")
        lookup = accounts._lookup(decrypt_value(api_key)) if api_key else None
        repo.account_insert({
            "account_id": account_id,
            "username": username,
            "password_hash": info.get("password_hash", ""),
            "api_key": api_key,
            "api_key_lookup": lookup,
            "role": info.get("role", "user"),
            "status": "active",
        })
        repo.quota_insert({"account_id": account_id})

    def account_id_for(username: str) -> str | None:
        row = repo.account_get_by_username(username)
        return row["account_id"] if row else None

    # 2 ── scheduled_job + 3 ── job_run_record (per user dir) --------------
    if work_dir.exists():
        for user_dir in sorted(work_dir.iterdir()):
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            username = user_dir.name
            account_id = account_id_for(username)
            if account_id is None:
                continue  # work_dir for a user with no account row — skip

            ujob = user_dir / ".priva.user.yml"
            if ujob.exists():
                jdata = yaml.safe_load(ujob.read_text()) or {}
                for item in (jdata.get("scheduled_jobs") or []):
                    try:
                        defn = ScheduledJobDefinition.model_validate(item)
                    except Exception:
                        continue
                    if repo.job_get(defn.id):
                        counts["skipped"] += 1
                        continue
                    counts["jobs"] += 1
                    if not dry_run:
                        scheduler.create_job(account_id, defn)

            for hist in sorted(user_dir.glob(".priva.scheduler.history.*.jsonl")):
                for line in hist.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = JobRunRecord.model_validate(json.loads(line))
                    except Exception:
                        continue
                    if repo.run_get(rec.run_id):
                        counts["skipped"] += 1
                        continue
                    counts["runs"] += 1
                    if dry_run:
                        continue
                    job_id = rec.job_id if (rec.job_id and repo.job_get(rec.job_id)) else None
                    repo.run_insert({
                        "run_id": rec.run_id,
                        "job_id": job_id,
                        "job_name": rec.job_name,
                        "account_id": account_id,
                        "session_id": rec.session_id,
                        "started_at": rec.started_at.isoformat() if rec.started_at else None,
                        "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
                        "status": rec.status,
                        "duration_ms": rec.duration_ms,
                        "is_error": int(rec.is_error),
                        "error_message": rec.error_message,
                        "num_turns": rec.num_turns,
                        "result_summary": rec.result_summary,
                    })

    print(("DRY-RUN " if dry_run else "") + f"migrate: {counts}")
    return counts

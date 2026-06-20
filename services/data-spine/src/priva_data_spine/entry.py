"""data-spine launcher CLI (entry-point group priva_cloud.services).

Phase 1 has no network server (the in-process transport is composed by the host).
This CLI provides the operational commands: init (create schema), stats, migrate.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-spine", description="Priva Cloud data-spine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the SQLite schema (idempotent)")
    sub.add_parser("stats", help="print table row counts")
    mp = sub.add_parser("migrate", help="migrate monolith YAML/JSONL into SQLite")
    mp.add_argument("--dry-run", action="store_true", help="report counts without writing")
    args = parser.parse_args(argv)

    from priva_common.config import get_settings

    settings = get_settings()

    if args.cmd == "init":
        from priva_data_spine.service import build_repo

        build_repo(settings)
        print(f"schema ready at {settings.dataspine.sqlite_path}")
        return 0

    if args.cmd == "stats":
        from priva_data_spine.service import AdminService, build_repo

        print(AdminService(build_repo(settings)).stats())
        return 0

    if args.cmd == "migrate":
        from priva_data_spine.migrate import run_migration

        run_migration(settings=settings, dry_run=args.dry_run)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

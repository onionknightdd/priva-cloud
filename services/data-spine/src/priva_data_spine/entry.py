"""data-spine launcher CLI (entry-point group priva_cloud.services).

Phase 1 has no network server (the in-process transport is composed by the host).
This CLI provides the operational commands: init (create schema), stats, migrate.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-spine", description="Priva Cloud data-spine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the schema on the configured backend (idempotent)")
    sub.add_parser("stats", help="print table row counts")
    mp = sub.add_parser("migrate", help="migrate monolith YAML/JSONL into the configured backend")
    mp.add_argument("--dry-run", action="store_true", help="report counts without writing")
    mtp = sub.add_parser(
        "migrate-to-pg",
        help="copy the SQLite tables into Postgres (idempotent; needs dataspine.postgres_dsn)")
    mtp.add_argument("--dry-run", action="store_true", help="report source/target counts without writing")
    srv = sub.add_parser("serve", help="run the gRPC server (the data plane)")
    srv.add_argument("--host", default=os.environ.get("DATA_SPINE_HOST", "0.0.0.0"))
    srv.add_argument("--port", type=int, default=int(os.environ.get("DATA_SPINE_PORT", "50051")))
    args = parser.parse_args(argv)

    from priva_common.config import get_settings

    settings = get_settings()

    if args.cmd == "init":
        from priva_data_spine.service import build_repo, describe_store

        build_repo(settings)
        print(f"schema ready ({describe_store(settings)})")
        return 0

    if args.cmd == "stats":
        from priva_data_spine.service import AdminService, build_repo

        print(AdminService(build_repo(settings), settings).stats())
        return 0

    if args.cmd == "migrate":
        from priva_data_spine.migrate import run_migration

        run_migration(settings=settings, dry_run=args.dry_run)
        return 0

    if args.cmd == "migrate-to-pg":
        from priva_data_spine.copy_to_pg import run_copy

        ds = settings.dataspine
        if not ds.postgres_dsn:
            print("error: dataspine.postgres_dsn is not set (env PRIVA_DATASPINE__POSTGRES_DSN)",
                  file=sys.stderr)
            return 2
        counts = run_copy(ds.sqlite_path, ds.postgres_dsn, dry_run=args.dry_run)
        prefix = "DRY-RUN " if args.dry_run else ""
        for table, c in counts.items():
            print(f"{prefix}migrate-to-pg {table}: {c}")
        return 0

    if args.cmd == "serve":
        from priva_common.logging import configure_logging

        from priva_data_spine.server import serve

        configure_logging(settings)
        return serve(settings, host=args.host, port=args.port)

    return 1


if __name__ == "__main__":
    sys.exit(main())

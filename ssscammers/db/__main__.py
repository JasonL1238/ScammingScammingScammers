"""``python -m ssscammers.db`` — apply pending migrations.

Exit codes: 0 on success (including nothing to do), 1 on any failure. The compose
``migrate`` one-shot service gates the agent on exactly this.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ssscammers.db")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="defaults to $DATABASE_URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be baselined/applied without touching the database",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        print(
            "no database URL: pass --database-url or set DATABASE_URL",
            file=sys.stderr,
        )
        return 1

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import asyncpg

    from ssscammers.db.files import MigrationFileError
    from ssscammers.db.runner import MigrationError, migrate

    try:
        report = asyncio.run(migrate(args.database_url, dry_run=args.dry_run))
    except (MigrationError, MigrationFileError) as exc:
        print(f"refusing to migrate: {exc}", file=sys.stderr)
        return 1
    except (asyncpg.PostgresError, asyncpg.exceptions.InterfaceError) as exc:
        # Covers bad credentials, a malformed DSN, and a migration whose SQL
        # fails: the deploy's most routine errors deserve a message, not a
        # traceback. The failed migration itself rolled back atomically.
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"cannot reach the database: {exc}", file=sys.stderr)
        return 1

    verb = "would be" if args.dry_run else "were"
    for label, names in (
        ("baselined", report.baselined),
        ("applied", report.applied),
        ("already applied", report.already_applied),
        ("pending", report.pending),
    ):
        if names:
            print(f"{label}: {', '.join(names)}")
    if not (report.baselined or report.applied or report.pending):
        print(f"up to date; no migrations {verb} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

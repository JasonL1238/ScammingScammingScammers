"""Applies migrations in order, tracking what ran — fresh and existing volumes.

Semantics, in the order they matter:

* **One advisory lock per run.** Two runners racing (a deploy retry, a manual run
  during a deploy) serialize on ``pg_advisory_lock`` rather than interleaving DDL.
* **The tracking table is created by the runner, not by a migration** — the
  chicken-and-egg is resolved in code, once.
* **Existing volumes are baselined, not re-executed — and only complete ones.**
  The pre-runner deployment applied ``001`` via the compose initdb mount, which
  records nothing. If the tracking table is empty and the *last* object ``001``
  creates (the ``wasted_time`` view) exists, ``001`` is recorded as ``baselined``
  without running it. If instead only the *first* object (``personas``) exists,
  the initdb apply died midway — since the mount runs statements in autocommit —
  and baselining would certify a half-built schema, so the run refuses with a
  hard error instead.
* **Until the initdb mount is removed from docker-compose.yml, no ``002`` may be
  committed.** A fresh volume would receive every ``db/migrations/*.sql`` raw
  from the mount, but only ``001`` can ever be baselined (the runner cannot know
  how far initdb got), so the first run would re-execute ``002`` into an error.
  The mount removal is the very next scheduled task; this note is the fence
  until it lands.
* **Each migration commits atomically with its tracking row**, in one
  transaction. asyncpg's parameterless ``execute`` uses the simple-query
  protocol, so multi-statement files run as written. Two consequences for
  migration authors: a statement that refuses to run inside any transaction
  (``CREATE INDEX CONCURRENTLY``, ``VACUUM``) cannot appear in a migration, and
  an enum value added by ``ALTER TYPE … ADD VALUE`` cannot be *used* (inserted,
  set as a default) later in the same file — add in one migration, use in the
  next. Requires PostgreSQL 12+ for in-transaction ``ADD VALUE``; the
  deployment pins 16.
* **An applied migration is frozen.** A checksum mismatch between the tracking
  row and the file on disk is a hard error: never edit an applied migration,
  write a new one. A baselined row necessarily records the checksum of the file
  *as of baselining* — what initdb actually ran is unknowable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from ssscammers.db.files import MIGRATIONS_DIR, Migration, ordered_migrations

logger = logging.getLogger(__name__)

__all__ = ["MigrationError", "MigrationReport", "migrate"]

_ADVISORY_LOCK_KEY = 0x55C4_3A11
"""Arbitrary fixed key; all runners against one database contend on it."""

_BASELINE_COMPLETE_SENTINEL = "wasted_time"
"""The LAST object 001 creates: its existence proves the file ran to the end."""

_BASELINE_STARTED_SENTINEL = "personas"
"""The FIRST table 001 creates: present without the complete-sentinel, the
initdb-era apply died midway and the volume must not be baselined."""

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    checksum   text NOT NULL,
    baselined  boolean NOT NULL DEFAULT false,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """The database and the migration files disagree in a way a run must not paper over."""


@dataclass
class MigrationReport:
    """What one run did (or, for a dry run, would do)."""

    baselined: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    already_applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    """Only populated on a dry run; a real run applies instead."""


async def migrate(
    database_url: str, *, dry_run: bool = False, directory: Path = MIGRATIONS_DIR
) -> MigrationReport:
    """Bring ``database_url`` up to date with the migrations in ``directory``."""
    migrations = ordered_migrations(directory)
    report = MigrationReport()

    conn = await asyncpg.connect(database_url)
    try:
        if dry_run:
            recorded = await _recorded(conn)
            _verify_recorded(migrations, recorded)
            report.already_applied = sorted(recorded)
            report.pending = [
                m.filename for m in migrations if m.filename not in recorded
            ]
            if not recorded and await _pre_runner_schema(conn):
                # The baseline the real run would record instead of applying.
                report.pending.remove(migrations[0].filename)
                report.baselined.append(migrations[0].filename)
            return report

        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        await conn.execute(_TRACKING_DDL)

        recorded = await _recorded(conn)
        if not recorded and await _pre_runner_schema(conn):
            first = migrations[0]
            await conn.execute(
                "INSERT INTO schema_migrations (filename, checksum, baselined) "
                "VALUES ($1, $2, true)",
                first.filename,
                first.checksum,
            )
            recorded[first.filename] = first.checksum
            report.baselined.append(first.filename)
            logger.info(
                "baselined %s: the schema predates the runner (initdb-era volume)",
                first.filename,
            )

        _verify_recorded(migrations, recorded)

        for migration in migrations:
            if migration.filename in recorded:
                if migration.filename not in report.baselined:
                    report.already_applied.append(migration.filename)
                continue
            async with conn.transaction():
                await conn.execute(migration.sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum) "
                    "VALUES ($1, $2)",
                    migration.filename,
                    migration.checksum,
                )
            report.applied.append(migration.filename)
            logger.info("applied %s", migration.filename)
    finally:
        await conn.close()

    return report


async def _pre_runner_schema(conn: asyncpg.Connection) -> bool:
    """True when the initdb-era schema exists *in full*; hard error when half.

    Baselining must prove ``001`` ran to its end, not that it started: the
    initdb mount applies statements in autocommit, so a mid-file death commits
    a prefix. Certifying that prefix as "applied" would hide the missing tables
    forever behind an up-to-date report.
    """
    complete = (
        await conn.fetchval("SELECT to_regclass($1)", _BASELINE_COMPLETE_SENTINEL)
    ) is not None
    if complete:
        return True
    started = (
        await conn.fetchval("SELECT to_regclass($1)", _BASELINE_STARTED_SENTINEL)
    ) is not None
    if started:
        raise MigrationError(
            f"the schema is half-applied: {_BASELINE_STARTED_SENTINEL!r} exists "
            f"but {_BASELINE_COMPLETE_SENTINEL!r} does not — an initdb-era apply "
            f"died midway; recreate the volume (or restore it) rather than "
            f"baselining a half-built schema"
        )
    return False


async def _recorded(conn: asyncpg.Connection) -> dict[str, str]:
    """The tracking rows, or empty if the table does not exist yet (dry runs)."""
    if (await conn.fetchval("SELECT to_regclass('schema_migrations')")) is None:
        return {}
    rows = await conn.fetch("SELECT filename, checksum FROM schema_migrations")
    return {row["filename"]: row["checksum"] for row in rows}


def _verify_recorded(
    migrations: tuple[Migration, ...], recorded: dict[str, str]
) -> None:
    """The tracking table must be a checksum-faithful prefix of the file list."""
    by_filename = {m.filename: m for m in migrations}

    for filename, stored in recorded.items():
        migration = by_filename.get(filename)
        if migration is None:
            raise MigrationError(
                f"schema_migrations records {filename!r} but no such file exists in "
                f"db/migrations; applied migrations must never be deleted or renamed"
            )
        if migration.checksum != stored:
            raise MigrationError(
                f"{filename} changed after it was applied (checksum "
                f"{migration.checksum[:12]}… != recorded {stored[:12]}…); never edit "
                f"an applied migration — write a new one"
            )

    applied_numbers = sorted(by_filename[f].number for f in recorded)
    if applied_numbers != list(range(1, len(applied_numbers) + 1)):
        raise MigrationError(
            f"applied migrations are not a contiguous prefix: {sorted(recorded)}; "
            f"a hole means some past run half-happened and needs a human"
        )

"""The runner against a real PostgreSQL — fresh volumes, initdb-era volumes, drift.

Marked ``migrations`` and skipped unless ``MIGRATIONS_TEST_DATABASE_URL`` points at
a disposable server (CI provides one as a service container). Every test creates
and drops its own scratch database, so runs are isolated and repeatable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from ssscammers.db.files import MIGRATIONS_DIR, ordered_migrations  # noqa: E402
from ssscammers.db.runner import MigrationError, migrate  # noqa: E402

pytestmark = [
    pytest.mark.migrations,
    pytest.mark.skipif(
        not os.environ.get("MIGRATIONS_TEST_DATABASE_URL"),
        reason="needs a disposable PostgreSQL (set MIGRATIONS_TEST_DATABASE_URL)",
    ),
]

ADMIN_URL = os.environ.get("MIGRATIONS_TEST_DATABASE_URL", "")

REAL_001 = MIGRATIONS_DIR / "001_initial.sql"

THROWAWAY_002 = (
    "-- Throwaway migration for the existing-volume exit criterion.\n"
    "ALTER TYPE scam_type ADD VALUE IF NOT EXISTS 'test_only_value';\n"
)


async def _admin_execute(statement: str) -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute(statement)
    finally:
        await conn.close()


@pytest.fixture
def scratch_db():
    """A freshly created database, dropped afterwards.

    Synchronous (driving asyncpg via ``asyncio.run``) so both the async runner
    tests and the synchronous CLI subprocess test can use it.
    """
    name = f"mig_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin_execute(f'CREATE DATABASE "{name}"'))

    base, _, _ = ADMIN_URL.rpartition("/")
    yield f"{base}/{name}"

    asyncio.run(_admin_execute(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
def real_migrations(tmp_path: Path) -> Path:
    """A copy of the real migrations, safe to extend or tamper with."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    shutil.copy(REAL_001, directory / "001_initial.sql")
    return directory


async def simulate_initdb_volume(url: str, *, sql: str | None = None) -> None:
    """Apply 001 the way the compose initdb mount did: raw, tracked nowhere."""
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql if sql is not None else REAL_001.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def fetch_tracking(url: str) -> list[dict]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT filename, checksum, baselined FROM schema_migrations "
            "ORDER BY filename"
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


class TestFreshVolume:
    async def test_a_fresh_database_gets_the_full_schema(self, scratch_db: str) -> None:
        report = await migrate(scratch_db)
        assert report.applied == ["001_initial.sql"]
        assert report.baselined == []

        conn = await asyncpg.connect(scratch_db)
        try:
            assert await conn.fetchval("SELECT to_regclass('personas')") is not None
            assert await conn.fetchval("SELECT to_regclass('calls')") is not None
        finally:
            await conn.close()

        (row,) = await fetch_tracking(scratch_db)
        assert row["filename"] == "001_initial.sql"
        assert row["baselined"] is False
        assert row["checksum"] == ordered_migrations()[0].checksum

    async def test_a_second_run_is_a_no_op(self, scratch_db: str) -> None:
        await migrate(scratch_db)
        report = await migrate(scratch_db)
        assert report.applied == []
        assert report.baselined == []
        assert report.already_applied == ["001_initial.sql"]

    async def test_two_racing_runners_serialize_on_the_advisory_lock(
        self, scratch_db: str
    ) -> None:
        # A deploy retry racing a manual run must not interleave DDL: exactly
        # one runner applies, the other waits on the lock and finds the work
        # done. Without the lock both would see empty tracking and collide.
        reports = await asyncio.gather(migrate(scratch_db), migrate(scratch_db))
        applied = sorted(report.applied for report in reports)
        assert applied == [[], ["001_initial.sql"]]
        assert len(await fetch_tracking(scratch_db)) == 1


class TestInitdbEraVolume:
    """The volume the pre-runner deployment leaves behind: schema, no tracking."""

    async def test_the_existing_schema_is_baselined_not_reexecuted(
        self, scratch_db: str
    ) -> None:
        await simulate_initdb_volume(scratch_db)
        # Re-executing 001 here would die on duplicate types; baselining must not.
        report = await migrate(scratch_db)
        assert report.baselined == ["001_initial.sql"]
        assert report.applied == []

        (row,) = await fetch_tracking(scratch_db)
        assert row["baselined"] is True

    async def test_a_half_applied_initdb_schema_is_refused_not_baselined(
        self, scratch_db: str
    ) -> None:
        # The initdb mount runs statements in autocommit, so a mid-file death
        # commits a prefix. Baselining that prefix would hide the missing tables
        # forever behind an "up to date" report — the runner must refuse loudly.
        full = REAL_001.read_text(encoding="utf-8")
        cut = full.index("CREATE TABLE callers")
        await simulate_initdb_volume(scratch_db, sql=full[:cut])

        with pytest.raises(MigrationError, match="half-applied"):
            await migrate(scratch_db)
        with pytest.raises(MigrationError, match="half-applied"):
            await migrate(scratch_db, dry_run=True)

    async def test_a_002_applies_onto_an_existing_volume(
        self, scratch_db: str, real_migrations: Path
    ) -> None:
        # The Phase 1 exit criterion: a throwaway 002 applies to a volume that
        # predates the runner, in the same run that baselines 001.
        await simulate_initdb_volume(scratch_db)
        (real_migrations / "002_add_enum_value.sql").write_text(
            THROWAWAY_002, encoding="utf-8"
        )

        report = await migrate(scratch_db, directory=real_migrations)
        assert report.baselined == ["001_initial.sql"]
        assert report.applied == ["002_add_enum_value.sql"]

        conn = await asyncpg.connect(scratch_db)
        try:
            labels = await conn.fetch(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = 'scam_type'"
            )
        finally:
            await conn.close()
        assert "test_only_value" in {row["enumlabel"] for row in labels}


class TestDriftIsRefused:
    async def test_an_edited_applied_migration_is_a_hard_error(
        self, scratch_db: str, real_migrations: Path
    ) -> None:
        await migrate(scratch_db, directory=real_migrations)
        path = real_migrations / "001_initial.sql"
        path.write_text(path.read_text(encoding="utf-8") + "\n-- edited\n")
        with pytest.raises(MigrationError, match="never edit an applied migration"):
            await migrate(scratch_db, directory=real_migrations)

    async def test_a_recorded_migration_missing_on_disk_is_a_hard_error(
        self, scratch_db: str, real_migrations: Path
    ) -> None:
        (real_migrations / "002_extra.sql").write_text(
            "CREATE TABLE extra (id int);\n", encoding="utf-8"
        )
        await migrate(scratch_db, directory=real_migrations)
        (real_migrations / "002_extra.sql").unlink()
        with pytest.raises(MigrationError, match="never be deleted or renamed"):
            await migrate(scratch_db, directory=real_migrations)

    async def test_a_failing_migration_leaves_no_partial_state(
        self, scratch_db: str, real_migrations: Path
    ) -> None:
        # The tracking row commits atomically with the DDL: a migration that dies
        # mid-file records nothing and leaves nothing of itself behind.
        (real_migrations / "002_broken.sql").write_text(
            "CREATE TABLE half_done (id int);\nSELECT 1/0;\n", encoding="utf-8"
        )
        with pytest.raises(asyncpg.PostgresError):
            await migrate(scratch_db, directory=real_migrations)

        conn = await asyncpg.connect(scratch_db)
        try:
            assert await conn.fetchval("SELECT to_regclass('half_done')") is None
        finally:
            await conn.close()
        rows = await fetch_tracking(scratch_db)
        assert [row["filename"] for row in rows] == ["001_initial.sql"]


class TestDryRun:
    async def test_a_dry_run_reports_without_touching_anything(
        self, scratch_db: str
    ) -> None:
        report = await migrate(scratch_db, dry_run=True)
        assert report.pending == ["001_initial.sql"]

        conn = await asyncpg.connect(scratch_db)
        try:
            assert (
                await conn.fetchval("SELECT to_regclass('schema_migrations')") is None
            )
        finally:
            await conn.close()

    async def test_a_dry_run_predicts_the_baseline(self, scratch_db: str) -> None:
        await simulate_initdb_volume(scratch_db)
        report = await migrate(scratch_db, dry_run=True)
        assert report.baselined == ["001_initial.sql"]
        assert report.pending == []


class TestTheCli:
    def test_a_fresh_apply_exits_zero(self, scratch_db: str) -> None:
        # The exact invocation the compose migrate one-shot will use.
        result = subprocess.run(
            [sys.executable, "-m", "ssscammers.db", "--database-url", scratch_db],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "001_initial.sql" in result.stdout

    def test_an_unreachable_database_exits_one(self) -> None:
        from ssscammers.db.__main__ import main

        assert main(["--database-url", "postgresql://nobody@127.0.0.1:1/nope"]) == 1

    def test_a_missing_url_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ssscammers.db.__main__ import main

        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert main([]) == 1

"""The migration-file layout contract — no database required.

``ssscammers.db.files`` is the stdlib-only half of the migration machinery; these
tests pin the contract the runner and the enum-sync test both build on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ssscammers.db.files import MIGRATIONS_DIR, MigrationFileError, ordered_migrations

VALID_SQL = "CREATE TABLE example (id int);\n"


def write(directory: Path, name: str, sql: str = VALID_SQL) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


class TestTheRealMigrationsDirectory:
    def test_it_parses(self) -> None:
        migrations = ordered_migrations()
        assert [m.number for m in migrations] == list(range(1, len(migrations) + 1))
        first = migrations[0]
        assert first.filename == "001_initial.sql"
        assert len(first.checksum) == 64
        assert "CREATE TYPE" in first.sql

    def test_the_real_directory_is_the_default(self) -> None:
        assert MIGRATIONS_DIR.name == "migrations"
        assert MIGRATIONS_DIR.parent.name == "db"

    def test_no_real_migration_manages_its_own_transaction(self) -> None:
        # The runner owns transaction boundaries; 001's BEGIN/COMMIT were removed
        # when the runner landed and must never come back.
        ordered_migrations()  # raises if any file carries BEGIN;/COMMIT;


class TestLayoutViolations:
    def test_a_gap_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "001_first.sql")
        write(tmp_path, "003_third.sql")
        with pytest.raises(MigrationFileError, match="contiguous"):
            ordered_migrations(tmp_path)

    def test_a_duplicate_number_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "001_first.sql")
        write(tmp_path, "001_other.sql")
        with pytest.raises(MigrationFileError, match="contiguous"):
            ordered_migrations(tmp_path)

    def test_numbering_must_start_at_001(self, tmp_path: Path) -> None:
        write(tmp_path, "002_second.sql")
        with pytest.raises(MigrationFileError, match="contiguous"):
            ordered_migrations(tmp_path)

    @pytest.mark.parametrize(
        "name",
        [
            "1_short.sql",
            "0001_long.sql",
            "001-dashes.sql",
            "001_MixedCase.sql",
            "001_spaces in name.sql",
            "abc_letters.sql",
        ],
    )
    def test_a_malformed_name_is_refused(self, tmp_path: Path, name: str) -> None:
        write(tmp_path, name)
        with pytest.raises(MigrationFileError, match="NNN_lower_snake"):
            ordered_migrations(tmp_path)

    @pytest.mark.parametrize(
        "statement",
        [
            "BEGIN;",
            "COMMIT;",
            "  begin ;",
            "commit;",
            # The evasions a two-spelling ban missed: every one of these smuggles
            # transaction control past the runner. A ROLLBACK; produces a
            # migration that is recorded but never applied; a COMMIT; before a
            # failing statement leaves a half-applied change.
            "ROLLBACK;",
            "END;",
            "ABORT;",
            "START TRANSACTION;",
            "BEGIN WORK;",
            "COMMIT WORK;",
            "COMMIT; -- with a trailing comment",
            "SELECT 1; COMMIT;",
        ],
    )
    def test_a_file_managing_its_own_transaction_is_refused(
        self, tmp_path: Path, statement: str
    ) -> None:
        write(tmp_path, "001_txn.sql", f"CREATE TABLE t (id int);\n{statement}\n")
        with pytest.raises(MigrationFileError, match="owns transaction"):
            ordered_migrations(tmp_path)

    def test_transaction_words_in_comments_and_identifiers_are_fine(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path,
            "001_ok.sql",
            "-- BEGIN; and COMMIT; live in the runner\n"
            "/* END; in a block comment too */\n"
            "CREATE TABLE commit_log (begin_at timestamptz);\n"
            "INSERT INTO commit_log VALUES (now()); -- ROLLBACK; as trailing prose\n",
        )
        assert ordered_migrations(tmp_path)[0].name == "ok"

    def test_procedural_bodies_and_case_expressions_are_fine(
        self, tmp_path: Path
    ) -> None:
        # plpgsql bodies begin lines with BEGIN/END and views use CASE ... END;
        # the scan strips dollar-quoted bodies and only checks statement-leading
        # keywords, so neither may be refused.
        write(
            tmp_path,
            "001_proc.sql",
            "CREATE FUNCTION touch() RETURNS trigger AS $fn$\n"
            "BEGIN\n"
            "  NEW.updated_at := now();\n"
            "  RETURN NEW;\n"
            "END;\n"
            "$fn$ LANGUAGE plpgsql;\n"
            "CREATE VIEW v AS SELECT CASE WHEN true THEN 1 ELSE 0 END AS c;\n",
        )
        assert ordered_migrations(tmp_path)[0].name == "proc"

    def test_transaction_words_inside_string_literals_are_fine(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path,
            "001_strings.sql",
            "INSERT INTO notes (body) VALUES ('shout BEGIN; then COMMIT; loudly');\n",
        )
        assert ordered_migrations(tmp_path)[0].name == "strings"

    def test_a_non_utf8_file_is_refused_with_the_contract_error(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "001_latin.sql").write_bytes(b"-- caf\xe9\nCREATE TABLE t (id int);\n")
        with pytest.raises(MigrationFileError, match="not valid UTF-8"):
            ordered_migrations(tmp_path)

    @pytest.mark.parametrize(
        "sql",
        [
            # Cross-construct blinding: sequential strip passes were fooled by
            # each of these (a dollar tag named in a comment pairing with a real
            # literal; a -- inside a string eating its line; a /* in one string
            # closing at a */ in another). The single-pass scanner must not be.
            "-- note about $b$ quoting\nCOMMIT;\nINSERT INTO t VALUES ($b$x$b$);\n",
            "INSERT INTO notes (b) VALUES ('a -- b'); COMMIT;\n",
            "INSERT INTO a VALUES ('has /*'); COMMIT; INSERT INTO b VALUES ('*/ x');\n",
            "PREPARE TRANSACTION 'gid';\n",
        ],
    )
    def test_cross_construct_blinding_is_caught(self, tmp_path: Path, sql: str) -> None:
        write(tmp_path, "001_sneaky.sql", sql)
        with pytest.raises(MigrationFileError, match="owns transaction"):
            ordered_migrations(tmp_path)

    def test_a_prepared_statement_is_not_transaction_control(
        self, tmp_path: Path
    ) -> None:
        # Bare PREPARE is a prepared statement; only PREPARE TRANSACTION is
        # two-phase commit.
        write(tmp_path, "001_prep.sql", "PREPARE stmt AS SELECT 1;\n")
        assert ordered_migrations(tmp_path)[0].name == "prep"

    def test_an_empty_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationFileError, match="no migrations"):
            ordered_migrations(tmp_path)

    def test_a_missing_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationFileError, match="does not exist"):
            ordered_migrations(tmp_path / "nowhere")

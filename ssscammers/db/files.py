"""Discovery and validation of ``db/migrations/`` — stdlib-only, by contract.

Written to be importable by the safety suite with zero third-party packages
(the enum-sync test converges on it in the Phase 1 redesign); anything needing a
database driver lives in :mod:`ssscammers.db.runner` instead.

The rules enforced here exist because migrations are append-only history:

* **Names are rigid** (``NNN_lower_snake.sql``) so ordering is the filename, not a
  convention someone remembers.
* **Numbers are contiguous from 001** — a gap is ambiguous once anything has been
  applied (was 004 deleted, or never written?), and renumbering after apply is
  impossible, so ambiguity is banned before it can exist.
* **No transaction-control statements** — the runner owns transaction boundaries,
  one per migration, with the tracking row committed atomically alongside the
  DDL. A file managing its own transaction breaks that silently: a smuggled
  ``ROLLBACK;`` produces a migration that is *recorded but never applied*, and a
  smuggled ``COMMIT;`` before a failing statement leaves a half-applied change
  the next run re-executes into an error. The scan is a lexical heuristic — one
  positional walk blanks dollar-quoted bodies, strings, and comments in
  encounter order, then every ``;``-separated statement's leading keyword is
  checked — so a sufficiently exotic file may be refused even though it is
  harmless; that failure is loud and at authoring time, which is the safe
  direction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Migration", "MigrationFileError", "MIGRATIONS_DIR", "ordered_migrations"]

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_TXN_KEYWORDS = frozenset({"BEGIN", "COMMIT", "END", "ROLLBACK", "ABORT", "START"})
"""Every statement-leading keyword PostgreSQL treats as transaction control,
including the spellings ``START TRANSACTION``, ``BEGIN WORK``, ``COMMIT WORK``,
``END``, and ``ABORT``. ``PREPARE TRANSACTION`` is banned as a two-token check
(bare ``PREPARE`` is a legitimate prepared statement)."""

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def _strip_non_sql(sql: str) -> str:
    """Blank out strings, dollar-quoted bodies, and comments in one pass.

    A single positional walk, deliberately not sequential regex passes: passes
    can be blinded by constructs that span each other — a ``--`` inside a string
    literal would eat the rest of its line, a dollar tag mentioned in a comment
    would pair with a later literal's tag — and a blinded scan fails in the
    unsafe direction, letting transaction control through. An *unterminated*
    construct swallows the rest of the file here, which is safe: it is invalid
    SQL, so the migration fails loudly and atomically at execute time.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        two = sql[i : i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif two == "/*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        elif sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and sql[j : j + 2] != "''":
                    break
                j += 2 if sql[j : j + 2] == "''" else 1
            i = j + 1 if j < n else n
            out.append("''")
        elif sql[i] == "$" and (match := _DOLLAR_TAG.match(sql, i)):
            tag = match.group(0)
            j = sql.find(tag, i + len(tag))
            i = n if j == -1 else j + len(tag)
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _transaction_violation(sql: str) -> str | None:
    """The first transaction-control statement in ``sql``, or ``None``."""
    for fragment in _strip_non_sql(sql).split(";"):
        words = fragment.split()
        if not words:
            continue
        first = words[0].upper()
        second = words[1].upper() if len(words) > 1 else ""
        if first in _TXN_KEYWORDS or (first, second) == ("PREPARE", "TRANSACTION"):
            return " ".join(words)[:60]
    return None


class MigrationFileError(ValueError):
    """A migration file or the set of them violates the layout contract."""


@dataclass(frozen=True)
class Migration:
    """One migration file, read and checksummed."""

    number: int
    name: str
    path: Path
    checksum: str
    sql: str

    @property
    def filename(self) -> str:
        return self.path.name


def ordered_migrations(directory: Path = MIGRATIONS_DIR) -> tuple[Migration, ...]:
    """Every migration in apply order, or raise :class:`MigrationFileError`."""
    if not directory.is_dir():
        raise MigrationFileError(f"migrations directory does not exist: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationFileError(
                f"{path.name} does not match NNN_lower_snake.sql; a stray file in "
                f"{directory} would silently change what a fresh database gets"
            )
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationFileError(f"{path.name} is not valid UTF-8: {exc}") from exc
        violation = _transaction_violation(sql)
        if violation is not None:
            raise MigrationFileError(
                f"{path.name} manages its own transaction ({violation!r}); the "
                f"runner owns transaction boundaries so the tracking row commits "
                f"atomically with the migration"
            )
        migrations.append(
            Migration(
                number=int(match.group(1)),
                name=match.group(2),
                path=path,
                checksum=hashlib.sha256(raw).hexdigest(),
                sql=sql,
            )
        )

    if not migrations:
        raise MigrationFileError(f"no migrations found in {directory}")

    numbers = [m.number for m in migrations]
    expected = list(range(1, len(migrations) + 1))
    if numbers != expected:
        raise MigrationFileError(
            f"migration numbers must be contiguous from 001; found "
            f"{[m.filename for m in migrations]}"
        )

    return tuple(migrations)

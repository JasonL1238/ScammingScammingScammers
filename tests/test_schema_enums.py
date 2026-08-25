"""Guards the SQL schema against drifting from the Python vocabulary.

Adding a triage class in Python and forgetting the migration produces a runtime
insert error on some future call, at the worst possible moment. This turns that
into a build failure. No database required — the SQL is parsed as text,
cumulatively across every migration in order, so an ``ALTER TYPE … ADD VALUE``
in a later file counts exactly like the value being present from the start.

The equivalence enforced here: **SQL enum evolution is append-only** — a type is
CREATEd once and only ever extended at the end (``BEFORE``/``AFTER`` clauses,
re-CREATE, DROP, and RENAME of a mirrored type are all build failures) — and
therefore exact-order equality against the Python enums is the same statement as
"Python enum evolution is append-only too": a mid-list insertion on either side
breaks the match.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from ssscammers.db.files import ordered_migrations, stripped_sql
from ssscammers.shared.enums import (
    CallerClass,
    CallerKind,
    CallPhase,
    CallStatus,
    EndReason,
    EntryPath,
    LabelSource,
    ScamType,
    Tactic,
    TriageClass,
    TurnRole,
)

#: SQL type name -> the Python enum it must mirror.
PAIRS = {
    "call_status": CallStatus,
    "end_reason": EndReason,
    "entry_path": EntryPath,
    "turn_role": TurnRole,
    "caller_class": CallerClass,
    "triage_class": TriageClass,
    "scam_type": ScamType,
    "call_phase": CallPhase,
    "caller_kind": CallerKind,
    "tactic": Tactic,
    "label_source": LabelSource,
}


class SchemaSyncError(AssertionError):
    """A migration evolves an enum in a way the append-only contract forbids."""


_CREATE_TYPE = re.compile(
    r"CREATE\s+TYPE\s+(\w+)\s+AS\s+ENUM\s*\((.*?)\)\s*;",
    re.DOTALL | re.IGNORECASE,
)
_ALTER_TYPE = re.compile(r"ALTER\s+TYPE\s+(\w+)\s+([^;]*);", re.DOTALL | re.IGNORECASE)
_ADD_VALUE = re.compile(
    # fullmatch'd against the whole action: trailing tokens the pattern does
    # not consume (BEFORE E'x', AFTER $$y$$ — valid PG spellings that insert
    # MID-LIST) must refuse, never silently model as an append.
    r"\s*ADD\s+VALUE\s+(?:IF\s+NOT\s+EXISTS\s+)?'([^']+)'"
    r"(?P<position>\s+(?:BEFORE|AFTER)\s+'[^']+')?\s*",
    re.IGNORECASE,
)
_DROP_TYPE = re.compile(r"DROP\s+TYPE\s+(?:IF\s+EXISTS\s+)?(\w+)\s*;", re.IGNORECASE)

_ANY_TYPE_DDL = re.compile(r"\b(?:CREATE|ALTER|DROP)\s+TYPE\b", re.IGNORECASE)


def _dollar_body_type_ddl(sql: str) -> str | None:
    """The first dollar-quoted body touching type DDL, or ``None``.

    Dollar-quoted bodies are not comments: a ``DO`` block executes at migration
    time (the pre-PG12 idempotent-enum idiom wraps ``ADD VALUE`` in exactly
    this), so a body this parser cannot model must be refused, never blanked
    into invisibility. Pairing the tags with a regex is safe *here* because the
    text has already been lexed with comments and strings blanked — the desync
    sources are gone.
    """
    bodies_kept = stripped_sql(sql, keep_dollar_bodies=True)
    tag_pattern = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
    position = 0
    while (opening := tag_pattern.search(bodies_kept, position)) is not None:
        tag = opening.group(0)
        # find(), not a backreference: a regex backreference to the optional
        # tag group never matches the bare-$$ spelling.
        closing = bodies_kept.find(tag, opening.end())
        end = len(bodies_kept) if closing == -1 else closing + len(tag)
        body = bodies_kept[opening.start() : end]
        if _ANY_TYPE_DDL.search(body):
            return body[:60]
        position = end
    return None


def lexed(sql: str) -> str:
    """Migration SQL with comments/bodies blanked and string interiors defanged.

    ``stripped_sql(keep_strings=True)`` removes comments and dollar-quoted
    bodies while keeping literals readable (the value lists this parser must
    see). Whitespace and semicolons are then removed *inside* each literal so
    DDL-looking prose in a string ('run ALTER TYPE …; later') can never match a
    statement pattern — real enum values and settings keys are snake_case and
    dotted, so they pass through untouched, and a value that did contain
    whitespace would surface as a loud drift mismatch, never a silent skip.
    """
    text = stripped_sql(sql, keep_strings=True)
    return re.sub(r"'[^']*'", lambda m: re.sub(r"[\s;]+", "", m.group(0)), text)


def cumulative_enum_values(migrations) -> dict[str, list[str]]:
    """Every SQL enum's value list after applying all migrations in order.

    Raises :class:`SchemaSyncError` on any evolution the append-only contract
    forbids — which is what makes exact-order comparison against the Python
    enums equivalent to append-only enforcement on both sides. Fails **closed**:
    the SQL is lexed first — comments contribute nothing (a commented-out
    ``ADD VALUE`` must not count as applied), while dollar-quoted procedural
    bodies *execute at migration time* and are therefore refused outright if
    they touch type DDL, since this parser cannot model what a ``DO`` block
    does. Any ``CREATE/ALTER/DROP TYPE`` the specific patterns cannot parse (a
    schema-qualified or quoted name, an exotic spelling, an action with
    trailing tokens the strict pattern does not consume) is likewise a refusal,
    never a silent skip — an unparsed statement would otherwise leave this
    gate's model of the database fictional forever after.
    """
    types: dict[str, list[str]] = {}
    for migration in migrations:
        body_ddl = _dollar_body_type_ddl(migration.sql)
        if body_ddl is not None:
            raise SchemaSyncError(
                f"{migration.filename} touches type DDL inside a dollar-quoted "
                f"body ({body_ddl!r}); a DO block executes at migration time "
                f"and this gate cannot model it — write the DDL as plain "
                f"statements (a function body mentioning type DDL is refused "
                f"too: loud at authoring time is the safe direction)"
            )
        sql = lexed(migration.sql)
        consumed: list[tuple[int, int]] = []
        for match in _CREATE_TYPE.finditer(sql):
            consumed.append(match.span())
            name = match.group(1).lower()
            if name in types:
                raise SchemaSyncError(
                    f"{migration.filename} re-creates enum type {name!r}; a "
                    f"mirrored type is created once and only ever appended to"
                )
            types[name] = re.findall(r"'([^']+)'", match.group(2))
        for match in _ALTER_TYPE.finditer(sql):
            consumed.append(match.span())
            name, action = match.group(1).lower(), match.group(2)
            if re.match(r"\s*RENAME\b", action, re.IGNORECASE):
                raise SchemaSyncError(
                    f"{migration.filename} renames {name!r} (or one of its "
                    f"values); renames break every row already labelled with it"
                )
            add = _ADD_VALUE.fullmatch(action)
            if add is None:
                if re.search(r"\bADD\s+VALUE\b", action, re.IGNORECASE):
                    raise SchemaSyncError(
                        f"{migration.filename} adds a value to {name!r} in a "
                        f"spelling this gate cannot fully parse "
                        f"({action.strip()[:60]!r}); use "
                        f"ADD VALUE [IF NOT EXISTS] 'plain_literal' with no "
                        f"trailing clauses"
                    )
                continue  # OWNER TO and friends — not enum evolution
            if name not in types:
                raise SchemaSyncError(
                    f"{migration.filename} adds a value to {name!r}, which no "
                    f"earlier migration created"
                )
            if add.group("position"):
                raise SchemaSyncError(
                    f"{migration.filename} inserts into {name!r} with "
                    f"BEFORE/AFTER; enum evolution is append-only, at the end"
                )
            value = add.group(1)
            if value in types[name]:
                raise SchemaSyncError(
                    f"{migration.filename} re-adds {value!r} to {name!r}"
                )
            types[name].append(value)
        for match in _DROP_TYPE.finditer(sql):
            consumed.append(match.span())
            name = match.group(1).lower()
            if name in types:
                raise SchemaSyncError(
                    f"{migration.filename} drops enum type {name!r}; rows "
                    f"labelled with it would be orphaned"
                )
        for match in _ANY_TYPE_DDL.finditer(sql):
            if not any(start <= match.start() < end for start, end in consumed):
                raise SchemaSyncError(
                    f"{migration.filename} contains type DDL this gate cannot "
                    f"parse near offset {match.start()} "
                    f"({sql[match.start() : match.start() + 60]!r}); use bare "
                    f"lowercase identifiers and terminated statements so enum "
                    f"evolution stays visible to the sync test"
                )
    return types


@pytest.fixture(scope="module")
def sql_types() -> dict[str, list[str]]:
    return cumulative_enum_values(ordered_migrations())


@pytest.fixture(scope="module")
def all_sql() -> str:
    return "\n".join(lexed(m.sql) for m in ordered_migrations())


_RETENTION_TUPLE = re.compile(r"\('retention\.legit_audio_days',\s*'(\d+)'\)")
_WASTED_TIME_VIEW = re.compile(
    # Qualified and quoted spellings count too — pg_dump emits
    # public."wasted_time", and a fork guard blind to it is no guard.
    r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:\w+\.)?\"?wasted_time\"?\s+AS\s+(.*?);",
    re.DOTALL | re.IGNORECASE,
)


def assert_retention_pinned(all_sql: str) -> None:
    """Every mention of the retention key must be the pinned seed-tuple form.

    A later ``UPDATE settings SET value='365' WHERE key='retention…'`` would
    otherwise raise the retention silently while the original INSERT literal
    kept this test green.
    """
    tuples = list(_RETENTION_TUPLE.finditer(all_sql))
    assert tuples, "no migration seeds retention.legit_audio_days"
    spans = [m.span() for m in tuples]
    for occurrence in re.finditer(r"retention\.legit_audio_days", all_sql):
        assert any(start <= occurrence.start() < end for start, end in spans), (
            "retention.legit_audio_days is touched outside the pinned seed "
            "tuple (an UPDATE?); raising retention must go through this test"
        )
    for match in tuples:
        assert int(match.group(1)) <= 7


def wasted_time_definitions(all_sql: str) -> list[str]:
    return [m.group(1) for m in _WASTED_TIME_VIEW.finditer(all_sql)]


@pytest.mark.parametrize(("type_name", "enum_cls"), PAIRS.items(), ids=list(PAIRS))
def test_sql_enum_matches_python(
    type_name: str, enum_cls: type, sql_types: dict[str, list[str]]
) -> None:
    assert type_name in sql_types, f"no migration defines SQL type {type_name!r}"
    assert sql_types[type_name] == [m.value for m in enum_cls], (
        f"SQL type {type_name} has drifted from {enum_cls.__name__}; append the "
        f"new value in an ALTER TYPE migration and at the END of the Python enum"
    )


def test_every_sql_enum_is_mirrored(sql_types: dict[str, list[str]]) -> None:
    # A type created in SQL but absent from PAIRS would evolve unguarded.
    assert set(sql_types) == set(PAIRS), (
        "every SQL enum type must appear in PAIRS with its Python counterpart"
    )


def test_every_python_enum_is_mirrored() -> None:
    # The other direction: a 12th persisted-intent StrEnum added to
    # shared.enums but forgotten in PAIRS would evolve unguarded. An enum that
    # is deliberately never persisted goes in the exclusion set, with a comment.
    import inspect
    from enum import StrEnum

    from ssscammers.shared import enums as enums_module

    not_persisted: set[type] = set()
    strenums = {
        obj
        for _, obj in inspect.getmembers(enums_module, inspect.isclass)
        if issubclass(obj, StrEnum)
        and obj is not StrEnum
        and obj.__module__ == enums_module.__name__
    }
    assert strenums - not_persisted == set(PAIRS.values()), (
        "every StrEnum in ssscammers.shared.enums must be in PAIRS (or in this "
        "test's not_persisted set, deliberately, with a comment)"
    )


def test_the_headline_metric_is_defined_once(all_sql: str) -> None:
    # The dashboard and the nightly rollup both read this view, so the definition
    # of "wasted time" cannot fork between them — including via a later
    # CREATE OR REPLACE that would leave the original text green in this file.
    definitions = wasted_time_definitions(all_sql)
    assert len(definitions) == 1, (
        "wasted_time is defined more than once across migrations; redefining "
        "the headline metric must be a deliberate change to this test"
    )
    assert "flagged_legit" in definitions[0], (
        "the metric must exclude reviewed misroutes"
    )


def test_legit_audio_retention_is_short_by_default(all_sql: str) -> None:
    # A real person who reached this line by accident must not persist in a scam
    # archive. If someone raises this default, they should have to change a test.
    assert_retention_pinned(all_sql)


def fake_migrations(*sqls: str):
    return [
        SimpleNamespace(filename=f"{i:03d}_fake.sql", sql=sql)
        for i, sql in enumerate(sqls, start=1)
    ]


class TestTheParserActuallyBites:
    """Meta-tests on synthetic migration sets — a gate never proven to bite is
    not a gate (the pattern from test_no_outbound's scanner self-tests)."""

    CREATE = "CREATE TYPE mood AS ENUM ('calm', 'wary');\n"

    def test_create_then_append_accumulates_in_order(self) -> None:
        types = cumulative_enum_values(
            fake_migrations(
                self.CREATE,
                "ALTER TYPE mood ADD VALUE 'spooked';\n"
                "ALTER TYPE mood ADD VALUE IF NOT EXISTS 'furious';\n",
            )
        )
        assert types["mood"] == ["calm", "wary", "spooked", "furious"]

    def test_a_before_clause_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="append-only"):
            cumulative_enum_values(
                fake_migrations(
                    self.CREATE,
                    "ALTER TYPE mood ADD VALUE 'tense' BEFORE 'wary';\n",
                )
            )

    def test_an_after_clause_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="append-only"):
            cumulative_enum_values(
                fake_migrations(
                    self.CREATE,
                    "ALTER TYPE mood ADD VALUE 'tense' AFTER 'calm';\n",
                )
            )

    def test_recreating_a_type_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="re-creates"):
            cumulative_enum_values(fake_migrations(self.CREATE, self.CREATE))

    def test_dropping_a_mirrored_type_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="drops"):
            cumulative_enum_values(
                fake_migrations(self.CREATE, "DROP TYPE mood;\n")
            )

    def test_renaming_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="renames"):
            cumulative_enum_values(
                fake_migrations(self.CREATE, "ALTER TYPE mood RENAME TO humour;\n")
            )

    def test_adding_to_an_unknown_type_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="no earlier migration"):
            cumulative_enum_values(
                fake_migrations("ALTER TYPE ghost ADD VALUE 'boo';\n")
            )

    def test_re_adding_a_value_is_a_build_failure(self) -> None:
        with pytest.raises(SchemaSyncError, match="re-adds"):
            cumulative_enum_values(
                fake_migrations(self.CREATE, "ALTER TYPE mood ADD VALUE 'calm';\n")
            )

    def test_non_enum_alters_are_ignored(self) -> None:
        types = cumulative_enum_values(
            fake_migrations(self.CREATE, "ALTER TYPE mood OWNER TO ssscammers;\n")
        )
        assert types["mood"] == ["calm", "wary"]

    def test_commented_out_ddl_contributes_nothing(self) -> None:
        # The unsafe direction: a commented "defer to DBA review" ADD VALUE must
        # not count as applied — the database never receives it, and a gate that
        # counted it would green-light the exact runtime insert error this
        # module exists to prevent.
        types = cumulative_enum_values(
            fake_migrations(
                self.CREATE,
                "-- ALTER TYPE mood ADD VALUE 'spooked';\n"
                "/* CREATE TYPE ghost AS ENUM ('boo'); */\n",
            )
        )
        assert types == {"mood": ["calm", "wary"]}

    def test_ddl_in_string_literals_contributes_nothing(self) -> None:
        types = cumulative_enum_values(
            fake_migrations(
                self.CREATE,
                "INSERT INTO notes (b) VALUES "
                "('run ALTER TYPE mood ADD VALUE ''spooked''; later');\n",
            )
        )
        assert types == {"mood": ["calm", "wary"]}

    @pytest.mark.parametrize(
        "sql",
        [
            # Fail closed, never open: type DDL the specific patterns cannot
            # parse must be refused, not silently skipped — a qualified rename
            # or drop would otherwise leave this gate's model of the database
            # fictional forever (pg_dump emits schema-qualified DDL).
            "ALTER TYPE public.mood RENAME TO humour;\n",
            'ALTER TYPE "mood" RENAME TO "humour";\n',
            "DROP TYPE public.mood;\n",
            "CREATE TYPE public.shade AS ENUM ('dim');\n",
            "ALTER TYPE mood ADD VALUE E'tense';\n",
            "ALTER TYPE mood ADD VALUE 'tense'\n",  # no terminating semicolon
            # Valid PG that inserts MID-LIST via operand spellings the strict
            # pattern does not consume — modeling these as appends would leave
            # the gate green while SQL order diverged from Python order.
            "ALTER TYPE mood ADD VALUE 'tense' BEFORE E'wary';\n",
            "ALTER TYPE mood ADD VALUE 'tense' AFTER $$calm$$;\n",
            "ALTER TYPE mood ADD VALUE 'tense' CASCADE;\n",
        ],
    )
    def test_unparseable_type_ddl_is_refused_not_skipped(self, sql: str) -> None:
        with pytest.raises(SchemaSyncError):
            cumulative_enum_values(fake_migrations(self.CREATE, sql))

    @pytest.mark.parametrize(
        "sql",
        [
            # A DO block executes at migration time — it is not a comment, and
            # a parser that cannot model it must refuse it, not blank it.
            "DO $do$ BEGIN ALTER TYPE mood RENAME TO humour; END $do$;\n",
            "DO $$ BEGIN ALTER TYPE mood ADD VALUE 'spooked'; END $$;\n",
            "DO $x$ BEGIN DROP TYPE mood; END $x$;\n",
        ],
    )
    def test_type_ddl_inside_dollar_bodies_is_refused(self, sql: str) -> None:
        with pytest.raises(SchemaSyncError, match="dollar-quoted body"):
            cumulative_enum_values(fake_migrations(self.CREATE, sql))

    def test_a_function_body_without_type_ddl_is_fine(self) -> None:
        types = cumulative_enum_values(
            fake_migrations(
                self.CREATE,
                "CREATE FUNCTION touch() RETURNS trigger AS $fn$\n"
                "BEGIN\n  NEW.updated_at := now();\n  RETURN NEW;\nEND;\n"
                "$fn$ LANGUAGE plpgsql;\n",
            )
        )
        assert types == {"mood": ["calm", "wary"]}


class TestTheSettingsGuardsActuallyBite:
    def test_a_later_retention_update_is_refused(self) -> None:
        text = (
            "INSERT INTO settings VALUES ('retention.legit_audio_days', '7');\n"
            "UPDATE settings SET value = '365' "
            "WHERE key = 'retention.legit_audio_days';\n"
        )
        with pytest.raises(AssertionError, match="outside the pinned seed"):
            assert_retention_pinned(text)

    def test_a_view_redefinition_is_counted(self) -> None:
        text = (
            "CREATE VIEW wasted_time AS SELECT flagged_legit FROM x;\n"
            "CREATE OR REPLACE VIEW wasted_time AS SELECT 1;\n"
        )
        assert len(wasted_time_definitions(text)) == 2

    def test_a_qualified_or_quoted_redefinition_is_counted(self) -> None:
        # pg_dump emits public."wasted_time"; a fork guard blind to that
        # spelling would pass green while the headline metric forked.
        text = (
            "CREATE VIEW wasted_time AS SELECT flagged_legit FROM x;\n"
            'CREATE OR REPLACE VIEW public."wasted_time" AS SELECT 1;\n'
        )
        assert len(wasted_time_definitions(text)) == 2

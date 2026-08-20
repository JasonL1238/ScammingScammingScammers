"""Guards the SQL schema against drifting from the Python vocabulary.

Adding a triage class in Python and forgetting the migration produces a runtime insert
error on some future call, at the worst possible moment. This turns that into a build
failure. No database required — the SQL is parsed as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "001_initial.sql"

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


def sql_enum_values(sql: str, type_name: str) -> list[str]:
    match = re.search(
        rf"CREATE TYPE {type_name} AS ENUM\s*\((.*?)\);", sql, re.DOTALL | re.IGNORECASE
    )
    if match is None:
        raise AssertionError(f"migration defines no SQL type named {type_name!r}")
    return re.findall(r"'([^']+)'", match.group(1))


@pytest.fixture(scope="module")
def sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize(("type_name", "enum_cls"), PAIRS.items(), ids=list(PAIRS))
def test_sql_enum_matches_python(type_name: str, enum_cls: type, sql: str) -> None:
    assert sql_enum_values(sql, type_name) == [m.value for m in enum_cls], (
        f"SQL type {type_name} has drifted from {enum_cls.__name__}; "
        "update db/migrations to match ssscammers/shared/enums.py"
    )


def test_the_headline_metric_is_defined_once(sql: str) -> None:
    # The dashboard and the nightly rollup both read this view, so the definition
    # of "wasted time" cannot fork between them.
    assert "CREATE VIEW wasted_time" in sql
    assert "flagged_legit" in sql, "the metric must exclude reviewed misroutes"


def test_legit_audio_retention_is_short_by_default(sql: str) -> None:
    # A real person who reached this line by accident must not persist in a scam
    # archive. If someone raises this default, they should have to change a test.
    match = re.search(r"\('retention\.legit_audio_days',\s*'(\d+)'\)", sql)
    assert match is not None
    assert int(match.group(1)) <= 7

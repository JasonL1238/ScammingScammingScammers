"""What today has already cost, and whether today is over.

G-14 bounds one call at ninety minutes and G-15's concurrency half bounds five at once.
Neither bounds a *day*: five slots recycling for twenty-four hours is about a hundred and
twenty call-hours, reachable by an ordinary redialling robocaller. The daily counters are
the rest of G-15, and they were declared in :class:`~ssscammers.shared.config.Settings`
and enforced nowhere until this module.

Separate from :mod:`ssscammers.agent.registry` on purpose. That registry is about the
calls held *right now* — in-process, monotonic, nothing persisted. Today's totals are the
opposite on every axis, and they must survive a deploy or the cap is a suggestion.

Three rules, each of which was originally got wrong in the direction of spending money.
``docs/guardrails.md`` records the specifics; what matters here is:

1. **A cap sends the caller to voicemail, never a rejection** (as G-20 does). The honeypot
   cannot tell a scammer from a real person before answering, so a wrong guess must cost a
   missed bait, not someone's ability to leave a message.
2. **Nothing here raises.** A cost control that can throw is one that drops calls. Every
   failure latches a flag that fails closed on the *next* admission instead of throwing on
   this one.
3. **Every failure fails closed** — unreadable state, unwritable state, and well-formed
   JSON holding the wrong types. The alternative is assuming today cost nothing and being
   wrong towards an unbounded bill. Numeric strings are coerced rather than refused,
   because hand-editing this file is the expected repair; a missing file is not a failure
   at all, but the first call of the first day. A latched failure clears on the next
   successful read or probe write, so repairing or removing the file recovers without a
   restart.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DailyLedger", "CapReason"]

CapReason = str

UNREADABLE = "ledger_unreadable"
UNWRITABLE = "ledger_unwritable"


def _utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class DailyLedger:
    """Persisted per-day counters, consulted at admission and updated at release.

    Args:
        path: JSON state file, replaced atomically on every update.
        minutes_cap: Total call minutes allowed per day. ``0`` disables the check.
        spend_cap_usd: Estimated dollars allowed per day. ``0`` disables the check.
        repeat_caller_cap: Admissions allowed per caller per day. ``0`` disables it.
        usd_per_minute: Cost estimate, NOT a measurement. Roughly $0.035/min of
            telephony, speech recognition and synthesis plus ~$0.008/min of model spend on
            a cached long call, rounded up. The synthesis half scales with how much the
            persona talks, so treat the spend cap as an order-of-magnitude brake and
            reconcile against real invoices. The minutes cap is exactly measurable.
        per_call_cap_seconds: G-14's per-call ceiling, used to reserve pessimistically for
            calls that are still up. See :meth:`cap_reason`.
        today: Injectable for tests. ``YYYY-MM-DD`` in UTC — a fixed zone deliberately, so
            the reset boundary does not move twice a year.
    """

    path: Path
    minutes_cap: int = 0
    spend_cap_usd: int = 0
    repeat_caller_cap: int = 0
    usd_per_minute: float = 0.05
    per_call_cap_seconds: float = 5400.0
    today: Callable[[], str] = _utc_today

    _read_failed: bool = field(init=False, default=False, repr=False)
    _write_failed: bool = field(init=False, default=False, repr=False)
    """Separate from ``_read_failed``: an unwritable ledger still reads back perfectly, so
    one shared flag would un-latch on the next admission and fail open again."""

    # -- state ----------------------------------------------------------------

    def _fresh(self) -> dict[str, Any]:
        return {
            "date": self.today(),
            "seconds": 0.0,
            "usd": 0.0,
            "callers": {},
            # A set, not a list: membership is checked on every admission and every
            # release, and the list version was an O(n) scan over a structure that grows
            # all day. Serialised as a sorted list, since JSON has no set.
            "counted": set(),
        }

    @staticmethod
    def _validated(raw: object) -> dict[str, Any] | None:
        """Stored state with every value type-checked, or ``None`` if unusable.

        ``_load`` merges stored values over typed defaults, so any one of them can reach
        arithmetic downstream as the wrong type.
        """
        if not isinstance(raw, dict):
            return None
        state: dict[str, Any] = {}
        for key in ("seconds", "usd"):
            try:
                state[key] = float(raw.get(key, 0.0))
            except (TypeError, ValueError):
                return None
        callers, counted = raw.get("callers", {}), raw.get("counted", [])
        if not isinstance(callers, dict) or not isinstance(counted, list):
            return None
        clean: dict[str, int] = {}
        for number, count in callers.items():
            if not isinstance(number, str):
                return None
            try:
                clean[number] = int(count)
            except (TypeError, ValueError):
                return None
        state["callers"] = clean
        state["counted"] = {entry for entry in counted if isinstance(entry, str)}
        state["date"] = raw.get("date")
        return state

    def _load(self) -> dict[str, Any]:
        """Today's counters, reset if the stored date is not today."""
        fresh = self._fresh()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._read_failed = False  # first call of the first day
            return fresh
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._read_failed = True
            logger.error(
                "daily ledger at %s is unreadable (%s); every caller goes to voicemail "
                "until it is repaired or removed",
                self.path,
                exc,
            )
            return fresh

        validated = self._validated(raw)
        if validated is None:
            self._read_failed = True
            logger.error(
                "daily ledger at %s holds values of the wrong type; every caller goes to "
                "voicemail until it is repaired or removed",
                self.path,
            )
            return fresh

        # Cleared on every successful read, including a new day, so removing or repairing
        # the file recovers the line without a restart.
        self._read_failed = False
        if validated["date"] != fresh["date"]:
            return fresh
        return {**fresh, **validated}

    def _save(self, state: dict[str, Any]) -> bool:
        """Replace the state file atomically. Returns whether it worked.

        ``os.replace`` because ``write_text`` truncates in place, and the deployment
        SIGKILLs this process while ``release()`` writes here — a torn write reads back as
        corrupt, which fails closed, turning a restart into an outage.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f"{self.path.name}.tmp")
            temp.write_text(
                json.dumps({**state, "counted": sorted(state["counted"])}), encoding="utf-8"
            )
            os.replace(temp, self.path)
        except OSError as exc:
            self._write_failed = True
            logger.error(
                "could not write the daily ledger at %s (%s); the counters are frozen, so "
                "every caller goes to voicemail until writes succeed again",
                self.path,
                exc,
            )
            return False
        self._write_failed = False
        return True

    # -- the admission question ------------------------------------------------

    def cap_reason(self, caller_number: str, *, calls_in_flight: int = 0) -> CapReason | None:
        """Which cap blocks this caller right now, or ``None`` to admit them.

        ``calls_in_flight`` is charged **pessimistically**, at the full per-call cap each,
        because minutes are only banked when a call is released. Counting only finished
        calls made a 360-minute cap into an 810-minute one: five calls admitted at minute
        359 each run to the 90-minute ceiling. Reserving the ceiling up front bounds the
        day at the cap plus one call instead of the cap plus ``max_concurrent`` calls.
        """
        state = self._load()

        if self._write_failed:
            # Probe, so a disk that recovers un-latches. Without this the first failed
            # write closes the line permanently: a capped call returns before anything
            # else here would attempt a save.
            self._save(state)
        if self._read_failed:
            return UNREADABLE
        if self._write_failed:
            return UNWRITABLE

        committed = float(state["seconds"]) + calls_in_flight * self.per_call_cap_seconds
        if self.minutes_cap and committed / 60.0 >= self.minutes_cap:
            return "daily_minutes_cap"
        if self.spend_cap_usd and (
            float(state["usd"]) + (calls_in_flight * self.per_call_cap_seconds / 60.0)
            * self.usd_per_minute
        ) >= self.spend_cap_usd:
            return "daily_spend_cap"
        if (
            self.repeat_caller_cap
            and caller_number
            and state["callers"].get(caller_number, 0) >= self.repeat_caller_cap
        ):
            return "repeat_caller_cap"
        return None

    # -- recording -------------------------------------------------------------

    def note_admission(self, call_sid: str, caller_number: str) -> None:
        """Count one admitted call against its caller's daily allowance.

        Idempotent by ``call_sid``: Twilio retries a webhook whose response it disliked,
        and a retry must not spend a second admission.
        """
        if not self.repeat_caller_cap:
            # The only cap that reads `callers`. With it off there is nothing to record,
            # and recording anyway is what made this file grow without bound for a whole
            # day when every cap was disabled.
            return
        if not caller_number:
            # Withheld caller id. Bucketing every blank together would lock out each
            # anonymous caller after the fifth.
            return
        state = self._load()
        key = f"admit:{call_sid}"
        if key in state["counted"]:
            return
        state["counted"].add(key)
        state["callers"][caller_number] = state["callers"].get(caller_number, 0) + 1
        self._save(state)

    def record_duration(self, call_sid: str, seconds: float) -> None:
        """Add a finished call's minutes and estimated cost to today's totals.

        Idempotent by ``call_sid``. Called from the release *and reap* paths — a reaped
        call was billable for its whole life, and skipping it made exactly the calls whose
        status callback never arrived invisible to the caps.
        """
        if not (self.minutes_cap or self.spend_cap_usd):
            return  # no cap reads these totals; see `note_admission`
        if seconds <= 0:
            return
        state = self._load()
        key = f"dur:{call_sid}"
        if key in state["counted"]:
            return
        state["counted"].add(key)
        state["seconds"] = float(state["seconds"]) + seconds
        state["usd"] = float(state["usd"]) + (seconds / 60.0) * self.usd_per_minute
        self._save(state)
        logger.info(
            "day so far: %.1f min, ~$%.2f (caps: %s min, $%s)",
            state["seconds"] / 60.0,
            state["usd"],
            self.minutes_cap or "off",
            self.spend_cap_usd or "off",
        )

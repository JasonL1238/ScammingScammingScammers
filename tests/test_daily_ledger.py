"""The daily caps, which were declared in config and enforced nowhere.

G-14 bounds one call at ninety minutes and G-15 bounds five at once. Neither bounds a day:
five slots recycling for twenty-four hours is about a hundred and twenty call-hours, and a
robocaller that redials gets there without anything being wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import RESERVED_CALLER, SimulatedClock, reserve_call

from ssscammers.agent.daily_ledger import DailyLedger
from ssscammers.agent.registry import CallRegistry

#: Same number reserve_call books admissions under, so the repeat-caller and cap
#: assertions below are checking the caller the registry actually admitted.
CALLER = RESERVED_CALLER


def ledger(tmp_path: Path, *, day: str = "2026-08-20", **kwargs) -> DailyLedger:
    return DailyLedger(path=tmp_path / "ledger.json", today=lambda: day, **kwargs)


class TestTheMinutesCap:
    def test_a_fresh_day_admits(self, tmp_path: Path) -> None:
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) is None

    def test_the_cap_closes_the_day(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=60)
        led.record_duration("CA1", 60 * 60)
        assert led.cap_reason(CALLER) == "daily_minutes_cap"

    def test_a_cap_of_zero_is_off(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=0)
        led.record_duration("CA1", 10_000 * 60)
        assert led.cap_reason(CALLER) is None

    def test_tomorrow_starts_from_zero(self, tmp_path: Path) -> None:
        # The reset is what makes this a *daily* cap rather than a lifetime one.
        ledger(tmp_path, day="2026-08-20", minutes_cap=60).record_duration("CA1", 60 * 60)
        assert ledger(tmp_path, day="2026-08-21", minutes_cap=60).cap_reason(CALLER) is None


class TestTheSpendCap:
    def test_spend_accrues_at_the_configured_rate(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, spend_cap_usd=1, usd_per_minute=0.10)
        led.record_duration("CA1", 9 * 60)  # $0.90
        assert led.cap_reason(CALLER) is None
        led.record_duration("CA2", 2 * 60)  # $1.10 total
        assert led.cap_reason(CALLER) == "daily_spend_cap"


    def test_landing_exactly_on_the_cap_closes_the_day(self, tmp_path: Path) -> None:
        # `>=`, not `>`. Flipping that comparison used to pass the whole suite.
        led = ledger(tmp_path, spend_cap_usd=1, usd_per_minute=0.10)
        led.record_duration("CA1", 10 * 60)  # exactly $1.00
        assert led.cap_reason(CALLER) == "daily_spend_cap"


class TestTheRepeatCallerCap:
    def test_one_number_is_cut_off_but_others_are_not(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, repeat_caller_cap=2)
        led.note_admission("CA1", CALLER)
        led.note_admission("CA2", CALLER)
        assert led.cap_reason(CALLER) == "repeat_caller_cap"
        assert led.cap_reason("+19375550102") is None, "the cap is per caller, not global"

    def test_an_unknown_caller_number_is_not_counted(self, tmp_path: Path) -> None:
        # Withheld caller id is a blank string, and blanks must not collapse into one
        # bucket that locks out every anonymous caller after the fifth.
        led = ledger(tmp_path, repeat_caller_cap=1)
        for i in range(5):
            led.note_admission(f"CA{i}", "")
        # Asserted on the persisted state, because `cap_reason` has its own blank guard —
        # so checking that alone passed even with this guard deleted.
        assert not (tmp_path / "ledger.json").exists(), "a blank caller wrote state"
        assert led.cap_reason("") is None


class TestIdempotency:
    """Twilio retries. A retry must not spend a second admission or double-count minutes."""

    def test_a_replayed_admission_counts_once(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, repeat_caller_cap=2)
        for _ in range(5):
            led.note_admission("CA1", CALLER)
        assert led.cap_reason(CALLER) is None

    def test_a_replayed_duration_counts_once(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=60)
        for _ in range(5):
            led.record_duration("CA1", 30 * 60)
        assert led.cap_reason(CALLER) is None


class TestItSurvivesARestart:
    def test_counters_are_read_back_from_disk(self, tmp_path: Path) -> None:
        # The whole point. An in-memory counter is cleared by the cheapest possible
        # action — restarting the process — so it is not a limit.
        ledger(tmp_path, minutes_cap=60).record_duration("CA1", 60 * 60)
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) == "daily_minutes_cap"


class TestUnreadableStateFailsClosed:
    def test_a_corrupt_file_sends_everyone_to_voicemail(self, tmp_path: Path) -> None:
        # The alternative is assuming today has cost nothing, which is the one mistake
        # this module exists to prevent.
        (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) == "ledger_unreadable"

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        # First call of the first day.
        assert not (tmp_path / "ledger.json").exists()
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) is None

    def test_a_file_holding_the_wrong_shape_also_fails_closed(self, tmp_path: Path) -> None:
        # This used to assert the opposite — that a bad shape reset the counters — which
        # is a fail-*open* on a cost control.
        (tmp_path / "ledger.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) == "ledger_unreadable"

    @pytest.mark.parametrize(
        "payload",
        [
            {"date": "2026-08-20", "seconds": None},
            {"date": "2026-08-20", "usd": None},
            {"date": "2026-08-20", "callers": []},
            {"date": "2026-08-20", "counted": "admit:CA1"},
            {"date": "2026-08-20", "seconds": {"a": 1}},
        ],
        ids=["null-seconds", "null-usd", "list-callers", "string-counted", "dict-seconds"],
    )
    def test_well_formed_json_with_wrong_types_fails_closed_without_raising(
        self, tmp_path: Path, payload: dict
    ) -> None:
        # These used to raise straight out of the webhook handler: TypeError on the
        # arithmetic, a 500 on /twilio/voice, and Twilio does not retry the primary voice
        # webhook — so the caller was dropped rather than sent to voicemail.
        (tmp_path / "ledger.json").write_text(json.dumps(payload), encoding="utf-8")
        led = ledger(tmp_path, minutes_cap=60, repeat_caller_cap=2)
        assert led.cap_reason(CALLER) == "ledger_unreadable"
        # And nothing here may raise, whether or not a cap is checked first.
        led.note_admission("CA1", CALLER)
        led.record_duration("CA1", 60.0)

    def test_a_number_stored_as_a_string_is_coerced_not_refused(self, tmp_path: Path) -> None:
        # Hand-editing this file to reset a counter is the expected repair, and
        # `"seconds": "600"` is what that edit looks like.
        (tmp_path / "ledger.json").write_text(
            json.dumps({"date": "2026-08-20", "seconds": "3600"}), encoding="utf-8"
        )
        assert ledger(tmp_path, minutes_cap=60).cap_reason(CALLER) == "daily_minutes_cap"


class TestAnUnwritableLedgerAlsoFailsClosed:
    """The failure that matters more, because reads keep working while writes do not.

    A read-only mount or a full disk leaves the counters frozen at their last written
    value, so without this every cap silently switches off — the precise unbounded-bill
    scenario the module exists to prevent, behind one log line.
    """

    @staticmethod
    def unwritable(tmp_path: Path) -> DailyLedger:
        directory = tmp_path / "locked"
        directory.mkdir()
        (directory / "ledger.json").write_text(
            json.dumps({"date": "2026-08-20", "seconds": 0.0, "usd": 0.0,
                        "callers": {}, "counted": []}), encoding="utf-8")
        directory.chmod(0o500)  # readable, not writable
        return DailyLedger(path=directory / "ledger.json", minutes_cap=60,
                           today=lambda: "2026-08-20")

    def test_a_failed_write_closes_the_line(self, tmp_path: Path) -> None:
        led = self.unwritable(tmp_path)
        assert led.cap_reason(CALLER) is None, "a healthy first read admits"
        led.record_duration("CA1", 60.0)  # this write fails
        assert led.cap_reason(CALLER) == "ledger_unwritable"

    def test_it_recovers_when_writes_work_again(self, tmp_path: Path) -> None:
        # The probe write in `cap_reason` is what makes this possible: a capped call
        # returns before anything else would attempt a save, so without it the first
        # failed write would close the line for the life of the process.
        led = self.unwritable(tmp_path)
        led.record_duration("CA1", 60.0)
        assert led.cap_reason(CALLER) == "ledger_unwritable"
        led.path.parent.chmod(0o700)
        assert led.cap_reason(CALLER) is None


class TestTheDocumentedRepairActuallyWorks:
    """The error log tells an operator to repair or remove the file. It must then recover.

    The flag used to be set and never cleared, so following those instructions left every
    caller going to voicemail until someone restarted the process.
    """

    def test_removing_the_file_recovers(self, tmp_path: Path) -> None:
        (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
        led = ledger(tmp_path, minutes_cap=60)
        assert led.cap_reason(CALLER) == "ledger_unreadable"
        (tmp_path / "ledger.json").unlink()
        assert led.cap_reason(CALLER) is None

    def test_repairing_the_file_recovers(self, tmp_path: Path) -> None:
        (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
        led = ledger(tmp_path, minutes_cap=60)
        assert led.cap_reason(CALLER) == "ledger_unreadable"
        (tmp_path / "ledger.json").write_text("{}", encoding="utf-8")
        assert led.cap_reason(CALLER) is None


class TestTheWriteIsAtomic:
    def test_a_torn_write_cannot_be_observed(self, tmp_path: Path) -> None:
        # The deployment SIGKILLs this process after a grace period while `release()` is
        # writing here. `write_text` truncates in place, so a kill mid-write left a partial
        # file — which reads back as corrupt, fails closed, and turns an ordinary restart
        # into an outage needing manual repair.
        led = ledger(tmp_path, minutes_cap=60)
        led.record_duration("CA1", 60.0)
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "ledger.json"]
        assert leftovers == [], f"temp files left behind: {leftovers}"
        assert json.loads((tmp_path / "ledger.json").read_text())["seconds"] == 60.0


class TestTheRegistryHonoursIt:
    """The cap has to bite at admission, which is the only place it can."""

    @staticmethod
    def registry(led: DailyLedger | None, clock: SimulatedClock | None = None) -> CallRegistry:
        # `SimulatedClock` from tests/helpers.py, not a third hand-rolled clock. The iterator
        # version this replaced advanced on every read — `reserve` reads twice — so a
        # banked duration was an accident of call order and could only be asserted as `>0`.
        return CallRegistry(max_concurrent=5, ledger=led, clock=clock or SimulatedClock())

    def test_a_capped_day_refuses_and_names_the_cap(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=1)
        led.record_duration("EARLIER", 60 * 60)
        admission = reserve_call(self.registry(led), "CA1")
        assert not admission.admitted
        assert admission.capped == "daily_minutes_cap"
        assert not admission.at_capacity, "a full day is not a full line"

    def test_without_a_ledger_nothing_is_capped(self, tmp_path: Path) -> None:
        assert reserve_call(self.registry(None), "CA1").admitted

    def test_releasing_a_call_banks_exactly_its_minutes(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=60)
        clock = SimulatedClock()
        reg = self.registry(led, clock)
        reserve_call(reg, "CA1")
        clock.advance(12 * 60)
        reg.release("CA1")
        assert json.loads((tmp_path / "ledger.json").read_text())["seconds"] == 12 * 60

    def test_reserving_counts_against_the_repeat_caller_cap(self, tmp_path: Path) -> None:
        # Deleting the `note_admission` call in `reserve` used to pass the entire suite:
        # the cap was covered at the ledger level and its only real call site was not.
        led = ledger(tmp_path, repeat_caller_cap=2)
        reg = self.registry(led)
        assert reserve_call(reg, "CA1").admitted
        assert reserve_call(reg, "CA2").admitted
        third = reserve_call(reg, "CA3")
        assert not third.admitted
        assert third.capped == "repeat_caller_cap"

    def test_a_reaped_call_still_banks_its_minutes(self, tmp_path: Path) -> None:
        # Reaping used to `del` the call outright, so the calls whose Twilio status
        # callback never arrived — the exact failure the reaper exists for — were invisible
        # to both the minutes and the spend cap. A run of callback failures blinded them.
        led = ledger(tmp_path, minutes_cap=600)
        clock = SimulatedClock()
        reg = self.registry(led, clock)
        reserve_call(reg, "CA1")
        clock.advance(reg.stale_after_seconds + 1)
        assert reg.active_count == 0, "the reaper should have dropped it"
        assert json.loads((tmp_path / "ledger.json").read_text())["seconds"] > 0

    def test_in_flight_calls_are_charged_before_they_finish(self, tmp_path: Path) -> None:
        # Minutes only bank at release, so counting finished calls alone made a 360-minute
        # cap into an 810-minute one: five calls admitted at minute 359 each run to the
        # 90-minute ceiling. In-flight calls are now reserved at that ceiling up front.
        led = ledger(tmp_path, minutes_cap=90, per_call_cap_seconds=90 * 60)
        reg = self.registry(led)
        assert reserve_call(reg, "CA1").admitted, "nothing committed yet, so the first admits"
        second = reserve_call(reg, "CA2")
        # CA1 has banked nothing — it has not finished — but it commits the full ceiling,
        # which is the whole cap. Before this fix the ledger saw 0 minutes and admitted.
        assert not second.admitted
        assert second.capped == "daily_minutes_cap"

    def test_the_overshoot_is_bounded_by_one_call(self, tmp_path: Path) -> None:
        # The property that matters: the worst case is the cap plus one call, not the cap
        # plus `max_concurrent` calls.
        cap_minutes, per_call = 360, 90 * 60
        led = ledger(tmp_path, minutes_cap=cap_minutes, per_call_cap_seconds=per_call)
        reg = self.registry(led)
        admitted = [
            sid for sid in ("CA1", "CA2", "CA3", "CA4", "CA5")
            if reserve_call(reg, sid).admitted
        ]
        worst_case_minutes = len(admitted) * per_call / 60.0
        assert worst_case_minutes <= cap_minutes + per_call / 60.0

    def test_a_retry_of_an_admitted_call_is_never_refused_by_a_cap(
        self, tmp_path: Path
    ) -> None:
        # The idempotency lookup runs before the cap check on purpose: the leg is already
        # up, and refusing the retry would drop a call that is mid-flight.
        led = ledger(tmp_path, minutes_cap=60)
        reg = self.registry(led)
        assert reserve_call(reg, "CA1").admitted
        led.record_duration("SOMETHING_ELSE", 99 * 60)  # day is now over
        again = reserve_call(reg, "CA1")
        assert again.admitted and again.capped is None


class TestItKeepsNoBooksNobodyReads:
    """With a cap off, the state it feeds must not be recorded at all.

    `counted` and `callers` grow all day and the whole file is rewritten per call. When
    every cap was disabled — a documented configuration — nothing bounded either one, so
    the file grew until the per-admission read cost became an audible stall on a process
    whose design note is that it does no blocking work.
    """

    def test_with_no_caps_nothing_is_written(self, tmp_path: Path) -> None:
        led = ledger(tmp_path)  # every cap 0
        led.note_admission("CA1", CALLER)
        led.record_duration("CA1", 600.0)
        assert not (tmp_path / "ledger.json").exists()

    def test_with_only_the_repeat_cap_on_durations_are_not_banked(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, repeat_caller_cap=2)
        led.note_admission("CA1", CALLER)
        led.record_duration("CA1", 600.0)
        state = json.loads((tmp_path / "ledger.json").read_text())
        assert state["callers"] == {CALLER: 1}
        assert state["seconds"] == 0.0, "no minutes cap reads this"

    def test_with_only_the_minutes_cap_on_callers_are_not_counted(self, tmp_path: Path) -> None:
        led = ledger(tmp_path, minutes_cap=60)
        led.note_admission("CA1", CALLER)
        led.record_duration("CA1", 600.0)
        state = json.loads((tmp_path / "ledger.json").read_text())
        assert state["callers"] == {}
        assert state["seconds"] == 600.0

    def test_counted_round_trips_through_json(self, tmp_path: Path) -> None:
        # It is a set in memory and a sorted list on disk; idempotency must survive that.
        led = ledger(tmp_path, minutes_cap=600)
        led.record_duration("CA1", 60.0)
        again = ledger(tmp_path, minutes_cap=600)
        again.record_duration("CA1", 60.0)
        assert json.loads((tmp_path / "ledger.json").read_text())["seconds"] == 60.0


class TestTheCostRateCannotDisableTheSpendCap:
    """`float()` accepts "nan", and every comparison against NaN is False.

    So `ESTIMATED_USD_PER_CALL_MINUTE=nan` made `usd >= cap` unfalsifiable and switched the
    daily spend cap off with no error — a fail-open on the control that exists to stop an
    unbounded bill.
    """

    @pytest.mark.parametrize("raw", ["nan", "0", "-1", "inf", "-inf"])
    def test_a_rate_that_would_break_the_comparison_is_rejected(
        self, raw: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ssscammers.shared.config import load_settings

        monkeypatch.setenv("ESTIMATED_USD_PER_CALL_MINUTE", raw)
        rate = load_settings().estimated_usd_per_call_minute
        assert rate > 0 and rate == rate  # finite, positive, not NaN

    def test_a_sane_rate_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ssscammers.shared.config import load_settings

        monkeypatch.setenv("ESTIMATED_USD_PER_CALL_MINUTE", "0.08")
        assert load_settings().estimated_usd_per_call_minute == 0.08

    def test_a_nan_rate_would_have_defeated_the_cap(self, tmp_path: Path) -> None:
        # The mechanism, stated directly: this is what the guard above prevents.
        led = DailyLedger(path=tmp_path / "l.json", spend_cap_usd=1,
                          usd_per_minute=float("nan"), today=lambda: "2026-08-20")
        led.record_duration("CA1", 10_000 * 60)
        assert led.cap_reason(CALLER) is None, (
            "documents the hazard: with a NaN rate no amount of spend ever trips the cap"
        )

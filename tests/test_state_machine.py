"""Call state machine — the merged FSM.

The tests that matter most are the ones proving a real person never gets baited: the
safety exits must fire from every state, including mid-bait, and must outrank whatever
the persona was doing.
"""

from __future__ import annotations

import pytest

from ssscammers.agent.state_machine import CallContext, CallStateMachine, Trigger
from ssscammers.shared.enums import CallPhase, EndReason, EntryPath, TriageClass


def machine(**kwargs: object) -> CallStateMachine:
    return CallStateMachine(**kwargs)  # type: ignore[arg-type]


def scam_ctx(**kwargs: object) -> CallContext:
    base = {
        "entry_path": EntryPath.DIRECT,
        "triage": TriageClass.SCAM,
        "triage_confidence": 0.95,
        "caller_turns": 1,
        "elapsed_seconds": 35.0,
    }
    base.update(kwargs)
    return CallContext(**base)  # type: ignore[arg-type]


class TestHappyPath:
    def test_answers_neutral_and_stays_neutral_until_someone_speaks(self) -> None:
        sm = machine()
        assert sm.phase is CallPhase.GREETING
        sm.advance(CallContext(caller_turns=0))
        assert sm.phase is CallPhase.GREETING
        assert not sm.baiting

    def test_first_caller_turn_moves_to_probation_not_to_baiting(self) -> None:
        sm = machine()
        sm.advance(CallContext(caller_turns=1, elapsed_seconds=3.0))
        assert sm.phase is CallPhase.ASSESSING
        # Nothing funny happens yet — this is the window a real caller lands in.
        assert not sm.baiting

    def test_commits_to_baiting_once_triage_is_confident(self) -> None:
        sm = machine()
        sm.advance(CallContext(caller_turns=1, elapsed_seconds=3.0))
        transition = sm.advance(scam_ctx())
        assert transition.to is CallPhase.HOOK
        assert transition.trigger is Trigger.TRIAGE_COMMITTED_SCAM
        assert sm.baiting

    def test_settles_into_stalling_after_the_hook(self) -> None:
        sm = machine()
        sm.advance(CallContext(caller_turns=1, elapsed_seconds=3.0))
        sm.advance(scam_ctx())
        sm.advance(scam_ctx(caller_turns=4, elapsed_seconds=60.0))
        assert sm.phase is CallPhase.STALL

    def test_winds_down_when_the_scammer_is_fed_up(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(frustration=9.0, elapsed_seconds=600.0))
        assert sm.phase is CallPhase.WIND_DOWN

    def test_returns_to_stalling_if_they_take_the_bait_again(self) -> None:
        sm = machine(phase=CallPhase.WIND_DOWN)
        sm.advance(scam_ctx(frustration=2.0, elapsed_seconds=600.0))
        assert sm.phase is CallPhase.STALL


class TestProbationProtectsRealCallers:
    def test_unclear_caller_is_not_baited_during_probation(self) -> None:
        sm = machine(phase=CallPhase.ASSESSING)
        sm.advance(CallContext(triage=TriageClass.UNCLEAR, triage_confidence=0.4, elapsed_seconds=10.0))
        assert sm.phase is CallPhase.ASSESSING
        assert not sm.baiting

    def test_forwarded_calls_face_a_higher_bar_than_direct_ones(self) -> None:
        # The owner's own cell rolls over real callers all day; the seeded honeypot
        # number does not. Same evidence, different conclusion.
        evidence = {"triage": TriageClass.SCAM, "triage_confidence": 0.65, "elapsed_seconds": 40.0}

        direct = machine(phase=CallPhase.ASSESSING)
        direct.advance(CallContext(entry_path=EntryPath.DIRECT, **evidence))  # type: ignore[arg-type]
        assert direct.phase is CallPhase.HOOK

        forwarded = machine(phase=CallPhase.ASSESSING)
        forwarded.advance(CallContext(entry_path=EntryPath.CONDITIONAL_FORWARD, **evidence))  # type: ignore[arg-type]
        assert forwarded.phase is CallPhase.ASSESSING

    def test_early_in_the_call_only_an_unambiguous_read_commits(self) -> None:
        sm = machine(phase=CallPhase.ASSESSING)
        sm.advance(CallContext(triage=TriageClass.SCAM, triage_confidence=0.65, elapsed_seconds=5.0))
        assert sm.phase is CallPhase.ASSESSING

    def test_a_caller_who_never_states_their_business_is_eventually_baited(self) -> None:
        # A real dentist says why they're calling in the first fifteen seconds.
        sm = machine(phase=CallPhase.ASSESSING)
        transition = sm.advance(
            CallContext(triage=TriageClass.UNCLEAR, triage_confidence=0.2, elapsed_seconds=95.0)
        )
        assert transition.trigger is Trigger.PROBATION_EXPIRED
        assert sm.phase is CallPhase.HOOK


class TestSafetyExitsOutrankEverything:
    @pytest.mark.parametrize(
        "phase",
        [CallPhase.GREETING, CallPhase.ASSESSING, CallPhase.HOOK, CallPhase.STALL, CallPhase.WIND_DOWN],
    )
    def test_a_real_person_stops_the_bait_from_any_phase(self, phase: CallPhase) -> None:
        sm = machine(phase=phase)
        sm.advance(
            CallContext(triage=TriageClass.LEGIT_BUSINESS, triage_confidence=0.6, elapsed_seconds=200.0)
        )
        assert sm.phase is CallPhase.DISCLOSE_EXIT
        assert not sm.baiting

    def test_stopping_needs_less_evidence_than_starting(self) -> None:
        # 0.5 is enough to stop baiting; 0.5 would not have been enough to start.
        sm = machine(phase=CallPhase.STALL)
        sm.advance(
            CallContext(triage=TriageClass.LEGIT_PERSONAL, triage_confidence=0.5, elapsed_seconds=100.0)
        )
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_dtmf_five_escapes_mid_bait(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(dtmf_digits="5"))
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_safeword_escapes_mid_bait(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(heard_safeword=True))
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_allowlisted_caller_is_never_baited(self) -> None:
        sm = machine(phase=CallPhase.HOOK)
        sm.advance(scam_ctx(allowlisted=True))
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_a_victim_calling_back_gets_the_disclosure(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(
            CallContext(triage=TriageClass.VICTIM_CALLBACK, triage_confidence=0.7, elapsed_seconds=30.0)
        )
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_emergency_outranks_even_a_confirmed_scammer(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(emergency_suspected=True))
        assert sm.phase is CallPhase.EMERGENCY_EXIT
        assert sm.end_reason is EndReason.EMERGENCY_EXIT

    def test_exits_are_one_way(self) -> None:
        # Once disclosed, a later "actually they seem scammy" reading must not drag
        # the persona back. We already told them we're a machine.
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(dtmf_digits="5"))
        sm.advance(scam_ctx(triage_confidence=0.99))
        assert sm.phase is CallPhase.DISCLOSE_EXIT


class TestEveryReleaseRecordsWhyItEnded:
    """`end_reason` is what the media pipeline persists for the call.

    It reaches `DISCLOSED_EXIT` only through `_record_end_reason`'s guard plus its
    `mapping.get(..., DISCLOSED_EXIT)` fallback, so a guard that omits `DISCLOSE_EXIT`
    leaves the reason `None` — and `media.report_outcome()` writes
    `end_reason or EndReason.PIPELINE_ERROR`. Every real person the system correctly
    released would be filed as a pipeline error, with nothing failing.
    """

    @pytest.mark.parametrize(
        ("label", "ctx"),
        [
            ("safeword", {"heard_safeword": True}),
            ("dtmf escape", {"dtmf_digits": "5"}),
            ("allowlisted", {"allowlisted": True}),
            (
                "triage found a real person",
                {"triage": TriageClass.LEGIT_BUSINESS, "triage_confidence": 0.8},
            ),
        ],
    )
    def test_a_released_caller_is_recorded_as_a_disclosed_exit(
        self, label: str, ctx: dict[str, object]
    ) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(**ctx))
        assert sm.phase is CallPhase.DISCLOSE_EXIT, label
        assert sm.end_reason is EndReason.DISCLOSED_EXIT, label


class TestHardStops:
    def test_hard_cap_terminates_regardless_of_how_well_it_is_going(self) -> None:
        sm = machine(phase=CallPhase.STALL, hard_cap_seconds=5400)
        sm.advance(scam_ctx(elapsed_seconds=5400.0, frustration=0.0))
        assert sm.phase is CallPhase.TERMINATE
        assert sm.end_reason is EndReason.MAX_DURATION

    def test_dead_air_hangs_up(self) -> None:
        sm = machine(phase=CallPhase.STALL, dead_air_seconds=60)
        sm.advance(scam_ctx(silence_seconds=61.0))
        assert sm.end_reason is EndReason.DEAD_AIR

    def test_threats_end_the_call_without_escalating(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(threat_detected=True))
        assert sm.phase is CallPhase.TERMINATE

    def test_watchdog_can_kill_from_anywhere(self) -> None:
        sm = machine(phase=CallPhase.HOOK)
        sm.advance(scam_ctx(watchdog_killed=True))
        assert sm.end_reason is EndReason.WATCHDOG_KILL


class TestTheWatchdogNeverCostsACallerTheirExit:
    """G-17's kill ranks *below* every exit that keeps a promise to the caller.

    The watchdog is a bare hangup: no disclosure, no voicemail, no 911 redirect.
    Every exit above it in ``_evaluate`` also stops the persona, and does it
    while telling the caller something they are owed. So a verdict landing in the
    same evaluation as a real-person signal must lose, and a verdict landing
    *after* the call has already committed to an exit must not drag it back.

    That second half is the fixed-script carve-out. It falls out of the machine's
    own ordering rather than being a rule every caller of this has to remember,
    which is the point — the monitor cannot forget it.
    """

    @pytest.mark.parametrize(
        ("signal", "value"),
        [
            ("heard_safeword", True),
            ("dtmf_digits", "5"),
            ("allowlisted", True),
        ],
    )
    def test_a_real_persons_signal_beats_a_verdict_in_the_same_evaluation(
        self, signal: str, value: object
    ) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(watchdog_killed=True, **{signal: value}))
        assert sm.phase is CallPhase.DISCLOSE_EXIT, (
            f"{signal} lost to a watchdog kill — a real person would be hung up on "
            "instead of hearing the disclosure and being handed a voicemail"
        )
        assert sm.end_reason is EndReason.DISCLOSED_EXIT

    def test_a_positive_real_person_read_beats_a_verdict(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(
            scam_ctx(
                watchdog_killed=True,
                triage=TriageClass.LEGIT_BUSINESS,
                triage_confidence=0.6,
            )
        )
        assert sm.phase is CallPhase.DISCLOSE_EXIT

    def test_an_emergency_beats_a_verdict(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(watchdog_killed=True, emergency_suspected=True))
        assert sm.phase is CallPhase.EMERGENCY_EXIT, (
            "a watchdog kill outranked someone in danger — they would get silence "
            "rather than being told to hang up and dial 911"
        )

    # Each case reaches an exit, then lets the *signal that caused it* go away before
    # the verdict lands. That is what makes the "once we have exited" guard the only
    # thing holding the exit — and therefore the only thing under test.
    #
    # An earlier version re-passed the original trigger into the second evaluation, and
    # a review showed it proved nothing: `heard_safeword` and `emergency_suspected` are
    # one-way latches in the triage engine, so in production they re-fire on every later
    # evaluation and are answered *above* the watchdog. The guard was unreachable by
    # either vector, and the test reproduced that unreachability instead of testing it.
    _EXIT_THEN_FADE = [
        # DTMF is drained per evaluation, so the digits are genuinely gone next time.
        ("dtmf", {"dtmf_digits": "5"}, CallPhase.DISCLOSE_EXIT),
        ("emergency_cleared", {"emergency_suspected": True}, CallPhase.EMERGENCY_EXIT),
        # The realistic one: a caller released on a legit read who then talks scammy.
        # `_is_real_person` is recomputed every evaluation and the scam score only
        # accumulates, so the read that released them stops holding.
        (
            "legit_read_stops_qualifying",
            {"triage": TriageClass.LEGIT_BUSINESS, "triage_confidence": 0.6},
            CallPhase.DISCLOSE_EXIT,
        ),
    ]

    @pytest.mark.parametrize(
        ("name", "trigger", "exit_phase"), _EXIT_THEN_FADE, ids=[c[0] for c in _EXIT_THEN_FADE]
    )
    def test_a_verdict_cannot_un_commit_a_call_that_already_exited(
        self, name: str, trigger: dict[str, object], exit_phase: CallPhase
    ) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(**trigger))
        assert sm.phase is exit_phase, f"{name}: setup did not reach the exit"
        reason_before = sm.end_reason

        # The verdict lands one evaluation later, with the releasing signal gone.
        sm.advance(scam_ctx(watchdog_killed=True))
        assert sm.phase is exit_phase, (
            f"{name}: a watchdog verdict pulled the call out of an exit it had "
            "already committed to — the fixed script it owed the caller would "
            "never be said"
        )
        assert sm.end_reason is reason_before

    def test_but_it_still_kills_a_call_that_is_actually_baiting(self) -> None:
        """The carve-out must not be so wide that the watchdog stops working."""
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(watchdog_killed=True))
        assert sm.phase is CallPhase.TERMINATE
        assert sm.end_reason is EndReason.WATCHDOG_KILL
        assert sm.history[-1].trigger is Trigger.WATCHDOG_KILL

    def test_spend_cap_terminates(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(spend_exceeded=True))
        assert sm.end_reason is EndReason.SPEND_CAP

    def test_caller_hangup_beats_every_other_signal(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx(caller_hung_up=True, emergency_suspected=True))
        assert sm.end_reason is EndReason.CALLER_HANGUP


class TestHistory:
    def test_every_change_is_recorded_with_its_reason(self) -> None:
        sm = machine()
        sm.advance(CallContext(caller_turns=1, elapsed_seconds=2.0))
        sm.advance(scam_ctx())
        sm.advance(scam_ctx(caller_turns=4, elapsed_seconds=90.0))

        assert [t.to for t in sm.history] == [CallPhase.ASSESSING, CallPhase.HOOK, CallPhase.STALL]
        assert all(t.changed for t in sm.history)

    def test_unchanged_evaluations_are_not_recorded(self) -> None:
        sm = machine(phase=CallPhase.STALL)
        sm.advance(scam_ctx())
        assert sm.history == []

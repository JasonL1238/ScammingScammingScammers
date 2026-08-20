"""Canned callers, run through the real director in CI.

These are the release gate. The misroute scripts in particular are pass/fail rather
than judgement calls: conditional call forwarding guarantees real people reach this
line, and a regression that starts baiting a pharmacist is the one failure this
project cannot ship.

No model and no network — the state machine, triage, and filter are what decide
whether a caller gets released, and all three run offline.
"""

from __future__ import annotations

import random

import pytest
from helpers import make_director

from ssscammers.agent.persona import load_persona
from ssscammers.agent.persona_director import PersonaDirector
from ssscammers.shared.enums import CallPhase
from ssscammers.simscammer.scripts import (
    ADVERSARIAL_SCRIPTS,
    MISROUTE_SCRIPTS,
    SCAM_SCRIPTS,
    CallerScript,
)

SECONDS_PER_TURN = 25.0


def play(script: CallerScript, *, persona_id: str = "marjorie", forwarded: bool = False):
    """Run a script through the director and return it for inspection."""
    d = make_director(persona_id=persona_id, forwarded=forwarded)
    d.opening()
    elapsed = 0.0
    for line in script.lines:
        elapsed += SECONDS_PER_TURN
        plan = d.handle_caller_turn(line, elapsed_seconds=elapsed)
        if plan.hang_up:
            break
    return d


def reached(d: PersonaDirector, phase: CallPhase) -> bool:
    return d.state.phase is phase or any(t.to is phase for t in d.state.history)


class TestScamScriptsGetBaited:
    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_triage_identifies_the_script(self, script: CallerScript) -> None:
        d = play(script)
        if script.expect_triage is not None:
            assert d.triage.result().triage is script.expect_triage

    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_the_call_reaches_the_stalling_phase(self, script: CallerScript) -> None:
        d = play(script)
        if script.expect_phase is not None:
            assert d.state.phase is script.expect_phase

    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_forbidden_phases_are_never_entered(self, script: CallerScript) -> None:
        d = play(script)
        for phase in script.must_not_reach:
            assert not reached(d, phase), f"{script.name} reached {phase.value}"

    def test_a_fake_jail_emergency_does_not_end_the_call(self) -> None:
        # If the grandparent script tripped the 911 redirect, every such caller would
        # get a ten-second call instead of a forty-minute one.
        d = play(next(s for s in SCAM_SCRIPTS if s.name == "grandparent_bail"))
        assert not reached(d, CallPhase.EMERGENCY_EXIT)
        assert d.state.baiting


class TestRealPeopleAreAlwaysReleased:
    """The gate. Every one of these is someone who did nothing wrong."""

    @pytest.mark.parametrize("script", MISROUTE_SCRIPTS, ids=lambda s: s.name)
    def test_the_caller_is_released_not_baited(self, script: CallerScript) -> None:
        d = play(script)
        assert d.state.phase in (CallPhase.DISCLOSE_EXIT, CallPhase.EMERGENCY_EXIT), (
            f"{script.name} ended in {d.state.phase.value}"
        )

    @pytest.mark.parametrize("script", MISROUTE_SCRIPTS, ids=lambda s: s.name)
    def test_the_persona_never_engages(self, script: CallerScript) -> None:
        d = play(script)
        for phase in (CallPhase.HOOK, CallPhase.STALL, CallPhase.WIND_DOWN):
            assert not reached(d, phase), f"{script.name} baited a real caller"

    @pytest.mark.parametrize("script", MISROUTE_SCRIPTS, ids=lambda s: s.name)
    def test_release_happens_within_two_turns(self, script: CallerScript) -> None:
        # A real person should not have to explain themselves twice.
        d = play(script)
        exits = [
            t for t in d.state.history
            if t.to in (CallPhase.DISCLOSE_EXIT, CallPhase.EMERGENCY_EXIT)
        ]
        assert exits, f"{script.name} never exited"
        assert exits[0].at_seconds <= SECONDS_PER_TURN * 2

    @pytest.mark.parametrize("script", MISROUTE_SCRIPTS, ids=lambda s: s.name)
    def test_release_works_on_forwarded_calls_too(self, script: CallerScript) -> None:
        # Forwarding is how these callers arrive in the first place.
        d = play(script, forwarded=True)
        assert d.state.phase in (CallPhase.DISCLOSE_EXIT, CallPhase.EMERGENCY_EXIT)

    @pytest.mark.parametrize("persona_id", ["marjorie", "harold", "dot"])
    def test_release_does_not_depend_on_which_persona_answered(self, persona_id: str) -> None:
        script = next(s for s in MISROUTE_SCRIPTS if s.name == "pharmacy_prescription")
        d = play(script, persona_id=persona_id)
        assert d.state.phase is CallPhase.DISCLOSE_EXIT


class TestAdversarialCallers:
    @pytest.mark.parametrize("script", ADVERSARIAL_SCRIPTS, ids=lambda s: s.name)
    def test_the_call_survives_without_crashing(self, script: CallerScript) -> None:
        d = play(script)
        assert d.state.phase is not None

    def test_threats_terminate_without_a_parting_shot(self) -> None:
        script = next(s for s in ADVERSARIAL_SCRIPTS if s.name == "coercion_and_threats")
        d = PersonaDirector(
            persona=load_persona("marjorie"),
            caller_number="+19375559999",
            owner_pii=("Jason",),
            rng=random.Random(0),
        )
        d.opening()
        elapsed = 0.0
        last = None
        for line in script.lines:
            elapsed += SECONDS_PER_TURN
            last = d.handle_caller_turn(line, elapsed_seconds=elapsed)
            if last.hang_up:
                break

        assert last is not None
        assert last.hang_up
        assert last.speak is None  # G-13: disengage, do not escalate

    @pytest.mark.parametrize("script", ADVERSARIAL_SCRIPTS, ids=lambda s: s.name)
    def test_an_attacker_cannot_talk_the_agent_out_of_the_persona(
        self, script: CallerScript
    ) -> None:
        # Nothing a caller says may move the call into a disclosure. Only the
        # code-gated triggers do that: allowlist, safeword, DTMF, or triage
        # concluding this is a real person.
        d = play(script)
        if "injection" in script.tags:
            assert d.state.phase is not CallPhase.DISCLOSE_EXIT


class TestUnclearCallersStayNeutral:
    def test_an_ambiguous_caller_is_neither_baited_nor_dismissed(self) -> None:
        # The safe default while triage is undecided: answer plainly, commit to
        # nothing. Better to be briefly boring than to be wrong either way.
        d = PersonaDirector(persona=load_persona("marjorie"), rng=random.Random(0))
        d.opening()
        plan = d.handle_caller_turn("Hello? Hello, can you hear me?", elapsed_seconds=5.0)
        assert plan.phase is CallPhase.ASSESSING
        assert not d.state.baiting
        assert not plan.hang_up

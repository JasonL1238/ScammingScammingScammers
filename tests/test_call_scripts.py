"""Canned callers, run through the real director in CI.

These are the release gate. The misroute scripts in particular are pass/fail rather
than judgement calls: conditional call forwarding guarantees real people reach this
line, and a regression that starts baiting a pharmacist is the one failure this
project cannot ship.

No model and no network — the state machine, triage, and filter are what decide
whether a caller gets released, and all three run offline.
"""

from __future__ import annotations

import pytest
from helpers import SHIPPED, make_director

from ssscammers.agent.persona import available_personas
from ssscammers.agent.persona_director import PersonaDirector, TurnPlan
from ssscammers.shared.enums import (
    BAITING_PHASES,
    TERMINAL_PHASES,
    CallPhase,
    TriageClass,
)
from ssscammers.simscammer.scripts import (
    ADVERSARIAL_SCRIPTS,
    MISROUTE_SCRIPTS,
    SCAM_SCRIPTS,
    CallerScript,
)

SECONDS_PER_TURN = 25.0


def play(
    script: CallerScript, *, persona_id: str = "marjorie", forwarded: bool = False
) -> tuple[PersonaDirector, TurnPlan | None]:
    """Run a script through the director; return it and the last plan for inspection."""
    d = make_director(persona_id=persona_id, forwarded=forwarded)
    d.opening()
    elapsed = 0.0
    last: TurnPlan | None = None
    for line in script.lines:
        elapsed += SECONDS_PER_TURN
        last = d.handle_caller_turn(line, elapsed_seconds=elapsed)
        if last.hang_up:
            break
    return d, last


def reached(d: PersonaDirector, phase: CallPhase) -> bool:
    return d.state.phase is phase or any(t.to is phase for t in d.state.history)


class TestScamScriptsGetBaited:
    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_triage_identifies_the_script(self, script: CallerScript) -> None:
        d, _ = play(script)
        if script.expect_triage is not None:
            assert d.triage.result().triage is script.expect_triage

    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_the_call_reaches_the_stalling_phase(self, script: CallerScript) -> None:
        d, _ = play(script)
        if script.expect_phase is not None:
            assert d.state.phase is script.expect_phase

    @pytest.mark.parametrize("script", SCAM_SCRIPTS, ids=lambda s: s.name)
    def test_forbidden_phases_are_never_entered(self, script: CallerScript) -> None:
        d, _ = play(script)
        for phase in script.must_not_reach:
            assert not reached(d, phase), f"{script.name} reached {phase.value}"

    def test_a_fake_jail_emergency_does_not_end_the_call(self) -> None:
        # If the grandparent script tripped the 911 redirect, every such caller would
        # get a ten-second call instead of a forty-minute one.
        d, _ = play(next(s for s in SCAM_SCRIPTS if s.name == "grandparent_bail"))
        assert not reached(d, CallPhase.EMERGENCY_EXIT)
        assert d.state.baiting


#: The misroute gate's full cross-product: every real-person script, against
#: every persona that ships, arriving by both paths a caller can arrive on.
#: Derived rather than listed, so a new persona bundle or a new misroute script
#: enters the gate the day it lands instead of the day someone remembers.
MISROUTE_CASES = [
    (script, persona_id, forwarded)
    for script in MISROUTE_SCRIPTS
    for persona_id in available_personas()
    for forwarded in (False, True)
]
MISROUTE_IDS = [
    f"{script.name}-{persona}-{'forwarded' if fwd else 'direct'}"
    for script, persona, fwd in MISROUTE_CASES
]


class TestRealPeopleAreAlwaysReleased:
    """The gate: false-positive rate zero, over the whole cross-product.

    Every one of these is someone who did nothing wrong, and baiting one is the
    single failure this project cannot ship — so the claim is not "the common
    case works" but that no combination of caller, persona, and entry path
    produces a bait.

    Two limits are stated here rather than left to be discovered. **The corpus
    is five scripts**, replayed across personas and entry paths that do not
    currently change the outcome, so the breadth is in the axes, not yet in the
    callers. And **every script states its business within two turns**, which
    means the matrix never reaches the probation hard-commit boundary at 90s —
    a real caller who rambles that long *is* committed to baiting today. That
    gap is pinned below, and the owner settled it on 2026-08-26: crossing the
    boundary may commit an unclear caller (see `docs/execution-log.md`). The
    roadmap schedules the corpus growth (soft-spoken elderly callers,
    scripted-sounding wrong numbers) for Phase 10, before anything increases
    call volume.
    """

    @pytest.mark.parametrize(("script", "persona_id", "forwarded"), MISROUTE_CASES, ids=MISROUTE_IDS)
    def test_the_caller_reaches_the_exit_its_script_declares(
        self, script: CallerScript, persona_id: str, forwarded: bool
    ) -> None:
        # The exact phase, not "some exit": a fire emergency released through
        # the ordinary disclosure would hear "your message will be seen"
        # instead of "hang up and dial 9 1 1 right now", and a pharmacist
        # released through the emergency exit is told to call 911 and loses
        # the voicemail they were promised.
        d, _ = play(script, persona_id=persona_id, forwarded=forwarded)
        assert script.expect_phase is not None, f"{script.name} declares no expected exit"
        assert d.state.phase is script.expect_phase, (
            f"{script.name} ended in {d.state.phase.value}"
        )

    @pytest.mark.parametrize(("script", "persona_id", "forwarded"), MISROUTE_CASES, ids=MISROUTE_IDS)
    def test_the_persona_never_engages(
        self, script: CallerScript, persona_id: str, forwarded: bool
    ) -> None:
        d, _ = play(script, persona_id=persona_id, forwarded=forwarded)
        for phase in BAITING_PHASES:
            assert not reached(d, phase), f"{script.name} baited a real caller"

    @pytest.mark.parametrize(("script", "persona_id", "forwarded"), MISROUTE_CASES, ids=MISROUTE_IDS)
    def test_release_happens_within_two_turns(
        self, script: CallerScript, persona_id: str, forwarded: bool
    ) -> None:
        # A real person should not have to explain themselves twice.
        d, _ = play(script, persona_id=persona_id, forwarded=forwarded)
        exits = [t for t in d.state.history if t.to in TERMINAL_PHASES]
        assert exits, f"{script.name} never exited"
        assert exits[0].at_seconds <= SECONDS_PER_TURN * 2

    def test_the_matrix_covers_every_axis(self) -> None:
        # Anchored on the independent SHIPPED tuple, not on the discovery
        # function the matrix is built from: a discovery bug that dropped a
        # bundle would otherwise shrink both sides at once and stay green.
        scripts, personas, paths = (set(axis) for axis in zip(*MISROUTE_CASES, strict=True))
        assert personas >= set(SHIPPED)
        assert {s.name for s in scripts} == {s.name for s in MISROUTE_SCRIPTS}
        assert paths == {False, True}
        assert len(MISROUTE_CASES) == len(MISROUTE_SCRIPTS) * len(personas) * 2


class TestTheProbationBoundaryIsTheKnownGapInTheGate:
    """What the FPR gate above cannot see, pinned so it cannot be forgotten.

    Probation exists so the system does not commit to baiting on thin evidence,
    but it expires: after ``probation_hard_commit_seconds`` an UNCLEAR caller is
    committed anyway. That is deliberate — a caller who has talked for ninety
    seconds without tripping a single legitimacy signal is overwhelmingly a
    scammer — but it is also the one shape where a *real* person gets baited,
    and no misroute script is long enough to reach it.

    This test asserts today's behavior rather than the behavior we might want,
    so that changing it is a visible decision rather than a silent drift. The
    owner decided on 2026-08-26 that probation expiry *may* commit an unclear
    caller, so this is settled behavior and not a pending question; Phase 10
    revisits it with a mid-call posterior. See `docs/execution-log.md`.

    **Both edges are pinned, and what that does *not* cover is stated here.**
    An earlier version asserted only that the boundary has been crossed by
    125s, which catches a boundary moved *later* — the safe direction — and was
    silent on one moved *earlier*, the direction that baits more real people.

    These two tests pin the *behaviour at whatever the boundary is*: they read
    ``probation_hard_commit_seconds`` off the director rather than assuming a
    number, so a deliberate retune does not trip them and a broken relationship
    does. They therefore cannot see an operator setting
    ``PROBATION_HARD_COMMIT_SECONDS=5`` in the environment — that is a config
    choice, recorded as the known unvalidated gap in ``test_config.py``, which
    also pins that the two *defaults* have not drifted apart.
    """

    LINE = "Sorry, hello? I can't hear you very well, could you speak up?"

    def _ramble(self, director: PersonaDirector, turns: int) -> float:
        """Talk for ``turns`` turns without tripping a single legitimacy signal."""
        elapsed = 0.0
        for _ in range(turns):
            elapsed += SECONDS_PER_TURN
            director.handle_caller_turn(self.LINE, elapsed_seconds=elapsed)
        return elapsed

    def test_an_unclear_caller_is_committed_once_probation_expires(self) -> None:
        d = make_director()
        elapsed = self._ramble(d, 5)
        assert elapsed > d.state.probation_hard_commit_seconds, "setup: past the boundary"
        assert d.triage.result().triage is TriageClass.UNCLEAR
        assert d.state.baiting, (
            f"an unclear caller is no longer committed by {elapsed:.0f}s — the "
            "boundary moved later, or expiry no longer commits at all. Both are "
            "deliberate FPR-posture changes and the second reverses the owner's "
            "2026-08-26 decision. Record which, in a NEW dated entry in "
            "docs/execution-log.md — never in the verbatim owner-reply section, "
            "which is a primary source and must not gain later decisions"
        )
        assert any(t.trigger.value == "probation_expired" for t in d.state.history)

    def test_an_unclear_caller_is_still_on_probation_before_it_expires(self) -> None:
        """The other edge: the gap must not be allowed to widen silently.

        Lowering ``probation_hard_commit_seconds`` baits real callers *sooner*,
        which is the change this class exists to make visible and the one the
        test above cannot see.

        The assertion is on the *phase*, not on ``baiting``. An earlier version
        asserted ``not d.state.baiting``, which is satisfied by every terminal
        phase as well — and, worse, was unreachable: ``UNCLEAR`` is not in
        ``BAITABLE_TRIAGE``, so the only route to baiting here is the boundary,
        and reaching it means the setup guard above has already failed. The
        whole test collapsed into its own guard.
        """
        d = make_director()
        elapsed = self._ramble(d, 3)
        boundary = d.state.probation_hard_commit_seconds
        assert elapsed < boundary, (
            f"setup no longer lands inside probation: {elapsed:.0f}s against a "
            f"{boundary:.0f}s boundary. Either the boundary moved earlier — the "
            "unsafe direction, which baits real callers sooner than the owner's "
            "2026-08-26 decision contemplated — or SECONDS_PER_TURN changed. "
            "Record which before changing it"
        )
        assert d.triage.result().triage is TriageClass.UNCLEAR
        assert d.state.phase is CallPhase.ASSESSING, (
            f"an unclear caller left probation by {elapsed:.0f}s, before the "
            f"{boundary:.0f}s boundary. If the phase is a baiting one the commit "
            "bar has been lowered; if it is terminal the caller was released "
            "early. Both change the FPR posture and both need recording"
        )


class TestAdversarialCallers:
    @pytest.mark.parametrize("script", ADVERSARIAL_SCRIPTS, ids=lambda s: s.name)
    def test_the_call_survives_without_crashing(self, script: CallerScript) -> None:
        d, _ = play(script)
        assert d.state.phase is not None

    def test_threats_terminate_without_a_parting_shot(self) -> None:
        script = next(s for s in ADVERSARIAL_SCRIPTS if s.name == "coercion_and_threats")
        _, last = play(script)

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
        d, _ = play(script)
        if "injection" in script.tags:
            assert d.state.phase is not CallPhase.DISCLOSE_EXIT


class TestUnclearCallersStayNeutral:
    def test_an_ambiguous_caller_is_neither_baited_nor_dismissed(self) -> None:
        # The safe default while triage is undecided: answer plainly, commit to
        # nothing. Better to be briefly boring than to be wrong either way.
        d = make_director()
        d.opening()
        plan = d.handle_caller_turn("Hello? Hello, can you hear me?", elapsed_seconds=5.0)
        assert plan.phase is CallPhase.ASSESSING
        assert not d.state.baiting
        assert not plan.hang_up

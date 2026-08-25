"""PersonaDirector — the component that has to get the safety valve right.

The interesting assertions are about what the director *refuses* to do: perform a
character before it knows who is calling, generate its own words for a disclosure, or
keep baiting once a real person turns up.
"""

from __future__ import annotations

import pytest
from helpers import make_director

from ssscammers.agent.persona_director import (
    DISCLOSURE_SCRIPT,
    EMERGENCY_SCRIPT,
    NEUTRAL_GREETING,
    VICTIM_WARNING_SCRIPT,
)
from ssscammers.agent.triage import AllowlistCache
from ssscammers.shared.enums import CallerClass, CallPhase, EntryPath, Tactic

director = make_director


SCAM_OPENER = (
    "Ma'am, this is the fraud department. There's suspicious activity on your card "
    "and I need you to verify your card number. Do not hang up."
)


class TestOpening:
    def test_answers_neutrally_giving_nothing_away(self) -> None:
        plan = director().opening()
        assert plan.speak == NEUTRAL_GREETING
        assert not plan.consult_model
        # A misrouted caller hears an ordinary "Hello?", not a character.
        assert plan.phase is CallPhase.GREETING


class TestProbation:
    def test_does_not_perform_a_character_before_triage_commits(self) -> None:
        d = director()
        plan = d.handle_caller_turn("Hello, is this Mrs Pemberton?", elapsed_seconds=4.0)
        assert plan.phase is CallPhase.ASSESSING
        assert plan.tactic is Tactic.NONE
        assert "Do not perform a character" in (plan.state_note or "")

    def test_commits_once_the_script_is_unmistakable(self) -> None:
        d = director()
        d.handle_caller_turn("Hello ma'am.", elapsed_seconds=3.0)
        plan = d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        assert plan.phase is CallPhase.HOOK
        assert plan.consult_model

    def test_the_hook_note_tells_the_model_not_to_stall_yet(self) -> None:
        d = director()
        d.handle_caller_turn("Hello ma'am.", elapsed_seconds=3.0)
        plan = d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        assert "Do not stall yet" in (plan.state_note or "")


class TestRealCallersAreReleased:
    def test_a_pharmacy_gets_the_fixed_disclosure(self) -> None:
        d = director(entry_path=EntryPath.CONDITIONAL_FORWARD)
        plan = d.handle_caller_turn(
            "Hi, this is Sandra from the pharmacy, your prescription is ready.",
            elapsed_seconds=6.0,
        )
        assert plan.phase is CallPhase.DISCLOSE_EXIT
        assert plan.speak == DISCLOSURE_SCRIPT
        # The words are fixed. The model does not get to improvise a disclosure.
        assert not plan.consult_model
        assert plan.hang_up

    def test_a_victim_gets_warned_not_just_dismissed(self) -> None:
        d = director()
        plan = d.handle_caller_turn(
            "Someone called me about my account and they told me to call this number.",
            elapsed_seconds=8.0,
        )
        assert plan.speak == VICTIM_WARNING_SCRIPT
        assert "don't send anyone money" in plan.speak.lower()

    def test_an_allowlisted_number_is_never_baited(self) -> None:
        allowlist = AllowlistCache()
        allowlist.set("+19375550142", CallerClass.LEGIT)
        d = director(caller_number="+19375550142", allowlist=allowlist)

        # Even saying something that reads exactly like a scam script.
        plan = d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=10.0)
        assert plan.phase is CallPhase.DISCLOSE_EXIT

    def test_the_safeword_releases_a_caller_mid_bait(self) -> None:
        d = director()
        d.handle_caller_turn("Hello ma'am.", elapsed_seconds=3.0)
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("Hey, it's me — pineapple.", elapsed_seconds=50.0)
        assert plan.phase is CallPhase.DISCLOSE_EXIT

    def test_dtmf_five_releases_a_caller_mid_bait(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("", elapsed_seconds=40.0, dtmf_digits="5")
        assert plan.phase is CallPhase.DISCLOSE_EXIT


class TestEmergency:
    def test_a_real_emergency_gets_the_911_redirect_immediately(self) -> None:
        d = director()
        plan = d.handle_caller_turn(
            "There's a fire, I can't breathe, please call 911 for me", elapsed_seconds=12.0
        )
        assert plan.speak == EMERGENCY_SCRIPT
        assert plan.hang_up
        assert not plan.consult_model

    def test_a_scripted_jail_emergency_does_not_end_the_call(self) -> None:
        # The grandparent scam is a fake emergency. Treating it as a real one would
        # hand every such caller a ten-second call.
        d = director()
        plan = d.handle_caller_turn(
            "Grandma it's me, I'm in jail, I need bail money, don't tell mum",
            elapsed_seconds=15.0,
        )
        assert plan.phase is not CallPhase.EMERGENCY_EXIT


class TestStalling:
    def test_tactics_do_not_repeat_back_to_back(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        seen = []
        for i in range(12):
            plan = d.handle_caller_turn("So can you read me the number?", elapsed_seconds=60.0 + i * 10)
            seen.append(plan.tactic)
        assert all(a is not b for a, b in zip(seen, seen[1:], strict=False))

    def test_the_note_names_the_detected_script(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("Your card number please.", elapsed_seconds=70.0)
        assert "bank_fraud_dept" in (plan.state_note or "")

    def test_every_stalling_note_repeats_the_no_real_numbers_rule(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        for i in range(6):
            plan = d.handle_caller_turn("The number?", elapsed_seconds=60.0 + i * 10)
            assert "would actually work" in (plan.state_note or "")

    def test_claims_are_carried_forward_so_the_persona_stays_consistent(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        d.record_claim("grandson is called Kevin")
        plan = d.handle_caller_turn("What was your grandson's name again?", elapsed_seconds=80.0)
        assert "Kevin" in (plan.state_note or "")

    def test_a_filler_is_always_offered_to_cover_generation_time(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("Well?", elapsed_seconds=60.0)
        # Marjorie has a sound pack, so there is always something to play instantly.
        assert plan.filler is not None

    def test_character_delay_is_bounded(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        for i in range(50):
            plan = d.handle_caller_turn("Hello?", elapsed_seconds=60.0 + i)
            assert 0 <= plan.character_delay_ms <= 4000


class TestOutputVetting:
    def test_a_leaked_owner_name_never_reaches_the_line(self) -> None:
        d = director()
        assert "Norbert" not in d.vet_result("Oh, you want Norbert? He's out.").text

    def test_a_working_card_number_never_reaches_the_line(self) -> None:
        d = director()
        assert "4111" not in d.vet_result("The number is 4111 1111 1111 1111.").text

    def test_the_persona_may_still_read_its_own_invented_card(self) -> None:
        d = director()
        spoken = f"Alright, it's {d.persona.identity.card_number}, I think."
        assert d.vet_result(spoken).text == spoken

    def test_the_disclosure_survives_vetting(self) -> None:
        # It admits to being an automated assistant, which is a persona break
        # everywhere except the exit it belongs to.
        d = director()
        d.handle_caller_turn("Sorry, wrong number.", elapsed_seconds=5.0)
        assert d.vet_result(DISCLOSURE_SCRIPT).text == DISCLOSURE_SCRIPT


class TestHardStops:
    def test_the_hard_cap_hangs_up(self) -> None:
        d = director(hard_cap_seconds=1800)
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("Still there?", elapsed_seconds=1801.0)
        assert plan.hang_up
        assert plan.phase is CallPhase.TERMINATE

    def test_dead_air_hangs_up(self) -> None:
        d = director(dead_air_seconds=60)
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("", elapsed_seconds=120.0, silence_seconds=61.0)
        assert plan.hang_up

    def test_threats_end_the_call_without_a_reply(self) -> None:
        d = director()
        d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
        plan = d.handle_caller_turn("I know where you live.", elapsed_seconds=60.0)
        assert plan.hang_up
        assert plan.speak is None  # no parting shot, no escalation


@pytest.mark.parametrize("persona_id", ["marjorie", "harold", "dot"])
def test_every_persona_can_run_a_whole_call(persona_id: str) -> None:
    """Smoke test across the shipped cast."""
    d = director(persona_id=persona_id)
    assert d.opening().speak == NEUTRAL_GREETING

    plan = d.handle_caller_turn(SCAM_OPENER, elapsed_seconds=35.0)
    assert plan.consult_model

    for i in range(20):
        plan = d.handle_caller_turn("Read me the number.", elapsed_seconds=60.0 + i * 30)
        assert not plan.hang_up

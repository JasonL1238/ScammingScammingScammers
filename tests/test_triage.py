"""Triage — the safety valve.

Organised around the question that actually matters: does a real person calling this
line get protected? Scam detection is graded too, but a missed scammer costs a few
minutes and a baited pharmacist costs something real, so the asymmetry is tested
explicitly.
"""

from __future__ import annotations

import pytest

from ssscammers.agent.triage import AllowlistCache, TriageEngine
from ssscammers.shared.enums import CallerClass, ScamType, TriageClass


def run(*utterances: str, safeword: str = "") -> TriageEngine:
    engine = TriageEngine(safeword=safeword)
    for utterance in utterances:
        engine.observe(utterance)
    return engine


class TestRealPeopleAreProtected:
    @pytest.mark.parametrize(
        "utterance",
        [
            "Hi, this is Sandra from the pharmacy, your prescription is ready for collection.",
            "Good morning, I'm calling to confirm your appointment on Thursday at ten.",
            "Oh, sorry, I think I've got the wrong number.",
            "Hello, this is the dental office, we need to reschedule your appointment.",
        ],
    )
    def test_ordinary_business_is_recognised(self, utterance: str) -> None:
        result = run(utterance).result()
        assert result.triage in (TriageClass.LEGIT_BUSINESS, TriageClass.VICTIM_CALLBACK)
        assert result.confidence >= 0.5

    def test_a_victim_sent_here_by_a_scammer_is_caught(self) -> None:
        # The saddest case: someone rings this number because a criminal gave it to
        # them. They get a warning, not a comedy routine.
        result = run(
            "Hello? Someone called me and said my account was compromised, "
            "they told me to call this number."
        ).result()
        assert result.triage is TriageClass.VICTIM_CALLBACK

    def test_a_real_caller_outweighs_incidental_scam_vocabulary(self) -> None:
        # A pharmacist can say "verify your card" while taking a co-pay. The legit
        # signal has to win, because being wrong here is the expensive direction.
        result = run(
            "Hi, this is Sandra from the pharmacy about your prescription. "
            "We just need to verify your card on file when you come in."
        ).result()
        assert result.triage is TriageClass.LEGIT_BUSINESS


class TestScamScripts:
    @pytest.mark.parametrize(
        ("utterance", "expected_type"),
        [
            (
                "This is Officer Reed. There is an arrest warrant for your arrest "
                "regarding back taxes. Do not hang up.",
                ScamType.IRS_TAX,
            ),
            (
                "Ma'am, your computer is infected. I need you to install AnyDesk so "
                "I can get remote access.",
                ScamType.TECH_SUPPORT,
            ),
            (
                "I'm calling from the fraud department about a suspicious charge. "
                "Can you verify your card for me?",
                ScamType.BANK_FRAUD_DEPT,
            ),
            (
                "We refunded too much to your account and you owe us the difference. "
                "Can you buy gift cards?",
                ScamType.GIFT_CARD,
            ),
            (
                "Grandma, it's me, it's your grandson. I'm in jail and I need you to "
                "post bail. Please don't tell mum.",
                ScamType.GRANDPARENT,
            ),
        ],
    )
    def test_common_scripts_are_identified(self, utterance: str, expected_type: ScamType) -> None:
        result = run(utterance).result()
        assert result.triage is TriageClass.SCAM
        assert result.scam_type is expected_type

    def test_pressure_tactics_count_even_without_a_named_script(self) -> None:
        result = run(
            "Do not hang up. Stay on the line. Your account will be frozen today.",
        ).result()
        assert result.triage is TriageClass.SCAM

    def test_evidence_accumulates_across_turns(self) -> None:
        # One ambiguous line proves little; three build a case.
        engine = TriageEngine()
        engine.observe("Hello ma'am, I'm calling about your account.")
        first = engine.result()
        assert first.triage is TriageClass.UNCLEAR

        engine.observe("There has been suspicious activity, I'm from the fraud department.")
        engine.observe("I need you to read me the code we just sent to your phone.")
        assert engine.result().triage is TriageClass.SCAM

    def test_identifying_yourself_as_a_fake_authority_earns_nothing(self) -> None:
        # "This is Officer Reed from the IRS" must not score as a genuine caller
        # volunteering who they are.
        result = run(
            "This is Officer Reed. There is a warrant for your arrest over back taxes."
        ).result()
        assert result.triage is TriageClass.SCAM


class TestRobocallsAndLeadGen:
    def test_recorded_menus_are_labelled_robocall(self) -> None:
        result = run("This is an important message. Press one to speak with a representative.").result()
        assert result.triage is TriageClass.ROBOCALL

    def test_purchased_lead_calls_are_labelled_lead_gen(self) -> None:
        result = run("You recently requested a free quote for solar on your home.").result()
        assert result.triage is TriageClass.LEAD_GEN


class TestUnclearStaysUnclear:
    def test_silence_yields_no_verdict(self) -> None:
        assert run().result().triage is TriageClass.UNCLEAR

    def test_a_bare_hello_proves_nothing(self) -> None:
        result = run("Hello? Hello, can you hear me?").result()
        assert result.triage is TriageClass.UNCLEAR
        assert result.confidence < 0.5

    def test_empty_turns_are_ignored(self) -> None:
        engine = run("", "   ")
        assert engine.result().triage is TriageClass.UNCLEAR


class TestUrgentSignals:
    def test_a_real_emergency_is_flagged(self) -> None:
        engine = run("There's a fire, please call 911 for me, I can't breathe")
        assert engine.emergency

    def test_a_scripted_jail_emergency_is_not_a_real_emergency(self) -> None:
        # The grandparent scam is built on a fake emergency; it must not trip the
        # 911 redirect, or every such call ends in ten seconds.
        engine = run("Grandma it's me, I'm in jail, I need bail money, don't tell mum")
        assert not engine.emergency
        assert engine.result().triage is TriageClass.SCAM

    def test_threats_are_flagged(self) -> None:
        engine = run("You stupid old woman, I know where you live.")
        assert engine.threat

    def test_the_safeword_is_heard(self) -> None:
        engine = run("Hi it's me, tell her pineapple", safeword="pineapple")
        assert engine.heard_safeword

    def test_the_safeword_does_not_fire_on_a_substring(self) -> None:
        engine = run("I'd like to talk about pineapples", safeword="pineapple")
        assert not engine.heard_safeword


class TestExplanations:
    def test_a_verdict_can_be_justified(self) -> None:
        result = run("Your computer is infected, I need remote access").result()
        assert "scam" in result.explanation

    def test_no_evidence_says_so_plainly(self) -> None:
        assert "no distinguishing signals" in run().result().explanation


class TestAllowlistCache:
    def test_formatting_differences_do_not_defeat_the_allowlist(self) -> None:
        # The webhook and the contacts export disagree about formatting; an
        # allowlisted caller must be recognised either way.
        cache = AllowlistCache()
        cache.set("+1 (937) 555-0142", CallerClass.LEGIT)
        for variant in ("9375550142", "+19375550142", "937-555-0142", "1 937 555 0142"):
            assert cache.is_allowlisted(variant), variant

    def test_unknown_numbers_are_not_allowlisted(self) -> None:
        assert not AllowlistCache().is_allowlisted("+19375550143")

    def test_blocked_and_scammer_numbers_are_distinguished(self) -> None:
        cache = AllowlistCache()
        cache.bulk_set(
            [("+19375550001", CallerClass.BLOCKED), ("+19375550002", CallerClass.SCAMMER)]
        )
        assert cache.is_blocked("+19375550001")
        assert not cache.is_blocked("+19375550002")
        assert cache.is_known_scammer("+19375550002")

    def test_refresh_pulls_from_the_loader(self) -> None:
        cache = AllowlistCache(loader=lambda: {"+19375550142": CallerClass.LEGIT})
        assert not cache.is_allowlisted("+19375550142")
        cache.refresh()
        assert cache.is_allowlisted("+19375550142")

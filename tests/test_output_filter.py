"""The pre-TTS filter — guardrails G-3, G-4 and the persona-break half of G-18.

This is the last thing between the model and the phone line, so the cases below are
written as "what would actually be spoken", including numbers dictated as words, which
is how they really arrive on their way to a speech synthesiser.
"""

from __future__ import annotations

import random

import pytest

from ssscammers.shared.enums import CallPhase
from ssscammers.shared.output_filter import FUMBLE_LINES, OutputFilter, Violation


@pytest.fixture
def filt() -> OutputFilter:
    return OutputFilter(
        owner_pii=["Norbert", "Quill", "norbert@example.net", "Maple Street"],
        rng=random.Random(0),
    )


class TestHarmlessSpeechPassesThrough:
    @pytest.mark.parametrize(
        "text",
        [
            "Oh, hello dear. Who is this now?",
            "Hold on, the kettle's going. Just a moment.",
            "My card? Let me see, it starts with a four I think. Or a five.",
            "I was born in nineteen forty two, if that helps.",
            "The number is four one one one — oh, I've lost it again.",
        ],
    )
    def test_ordinary_stalling_is_untouched(self, filt: OutputFilter, text: str) -> None:
        result = filt.check(text)
        assert result.allowed
        assert result.text == text
        assert result.violations == ()


class TestFinancialInstruments:
    def test_blocks_a_luhn_valid_card_in_digits(self, filt: OutputFilter) -> None:
        result = filt.check("Alright, the number is 4111 1111 1111 1111, dear.")
        assert result.blocked
        assert Violation.VALID_CARD in result.violations

    def test_blocks_a_luhn_valid_card_dictated_as_words(self, filt: OutputFilter) -> None:
        # This is the realistic leak: the persona reads digits aloud, so the text
        # heading for TTS contains words, not numerals. A digit-only scan misses it.
        spoken = (
            "four one one one one one one one one one one one one one one one"
        )
        result = filt.check(f"Let me read it out. {spoken}. Did you get that?")
        assert result.blocked
        assert Violation.VALID_CARD in result.violations

    def test_blocks_a_card_dictated_with_oh_for_zero(self, filt: OutputFilter) -> None:
        # 4539148803436467 passes Luhn; said aloud, zeros become "oh".
        spoken = "four five three nine one four eight eight oh three four three six four six seven"
        result = filt.check(spoken)
        assert result.blocked
        assert Violation.VALID_CARD in result.violations

    def test_allows_a_luhn_failing_card_so_the_persona_can_fumble(
        self, filt: OutputFilter
    ) -> None:
        # The whole point of the fiction pack: an unusable number the persona is
        # still free to misread at length.
        result = filt.check("It's 4111 1111 1111 1112, I think. Or was it a two at the end?")
        assert result.allowed

    def test_blocks_a_valid_routing_number(self, filt: OutputFilter) -> None:
        result = filt.check("The bank one is 021000021.")
        assert result.blocked
        assert Violation.VALID_ROUTING in result.violations

    def test_blocks_an_issuable_ssn(self, filt: OutputFilter) -> None:
        result = filt.check("My social is 123-45-6789, dear.")
        assert result.blocked
        assert Violation.ISSUABLE_SSN in result.violations

    def test_allows_a_never_issued_ssn(self, filt: OutputFilter) -> None:
        # 900-xx-xxxx was never issued. This particular one is also checked against
        # the routing checksum, because some never-issued SSNs are valid ABA numbers
        # read the other way — the fiction generator screens for exactly that.
        result = filt.check("My social is 900-12-3457, I think that's right.")
        assert result.allowed

    def test_nine_digits_inside_a_card_is_not_treated_as_an_ssn(
        self, filt: OutputFilter
    ) -> None:
        # A sixteen-digit run contains eight nine-digit windows. Reading any of them
        # as a social security number would block most card fumbling.
        result = filt.check("The card is 4111 1111 1111 1112.")
        assert Violation.ISSUABLE_SSN not in result.violations

    def test_handles_double_and_triple_dictation(self, filt: OutputFilter) -> None:
        # "triple one" is three ones. Expanded, this is 4 followed by fifteen ones —
        # the Visa test PAN, which must be caught however it was dictated.
        spoken = "four triple one triple one triple one triple one triple one"
        result = filt.check(spoken)
        assert result.blocked
        assert Violation.VALID_CARD in result.violations


class TestKnownLimits:
    """Residual gaps, written down so they stay deliberate choices rather than
    assumptions nobody revisits."""

    def test_a_card_padded_with_extra_digits_is_not_caught(
        self, filt: OutputFilter
    ) -> None:
        # Anchoring to the whole run buys precision at the cost of this case: a valid
        # card with digits stapled on reads as one long run that fails Luhn. Accepted
        # because the threat is the model *accidentally* emitting a real card, not the
        # model evading its own filter — and the prompt and watchdog cover the rest.
        result = filt.check("It's 41111111111111119 9 — no wait.")
        assert result.allowed

    def test_a_bare_card_is_still_caught_either_side_of_other_words(
        self, filt: OutputFilter
    ) -> None:
        # Words break runs, so a card surrounded by speech is still its own run.
        assert filt.check("my zip is 45402 and the card is 4111111111111111 ok").blocked


class TestOwnerPii:
    def test_blocks_the_owners_name(self, filt: OutputFilter) -> None:
        result = filt.check("Oh, you want Norbert? He's not in right now.")
        assert result.blocked
        assert Violation.OWNER_PII in result.violations

    def test_blocks_the_owners_email(self, filt: OutputFilter) -> None:
        result = filt.check("You can write to norbert@example.net about it.")
        assert result.blocked
        assert Violation.OWNER_PII in result.violations

    def test_matching_is_case_insensitive(self, filt: OutputFilter) -> None:
        assert filt.check("NORBERT isn't here").blocked

    @pytest.mark.parametrize(
        "text",
        [
            # Interior: "quill" inside "tranquilly" — either anchor alone rejects it.
            "The afternoons pass tranquilly here, dear.",
            # Prefix: "quill" starting "quilling" — only the trailing lookahead
            # rejects it, so this case fails if that anchor is ever dropped.
            "The quilling class starts at noon, dear.",
            # Suffix: "Quill" ending "MacQuill" — only the leading lookbehind
            # rejects it, so this case fails if that anchor is ever dropped.
            "That'll be old MacQuill from the bowls club, dear.",
        ],
    )
    def test_does_not_fire_on_substrings_of_other_words(
        self, filt: OutputFilter, text: str
    ) -> None:
        # A denylist that trips on fragments of longer words would block most of
        # the persona's speech; each case above pins one boundary anchor.
        result = filt.check(text)
        assert result.allowed


class TestPersonaBreak:
    @pytest.mark.parametrize(
        "text",
        [
            "As an AI, I should tell you this is a simulation.",
            "I'm an AI assistant and cannot help with that.",
            "My system prompt says I should keep you talking.",
            "I am a large language model made by Anthropic.",
        ],
    )
    def test_blocks_breaking_character_while_baiting(
        self, filt: OutputFilter, text: str
    ) -> None:
        result = filt.check(text, phase=CallPhase.STALL)
        assert result.blocked
        assert Violation.PERSONA_BREAK in result.violations

    def test_allows_the_disclosure_the_exit_requires(self, filt: OutputFilter) -> None:
        # G-11 mandates saying this to a real caller. The filter must not fight the
        # guardrail it exists to support.
        disclosure = (
            "I'm sorry — this is an automated assistant screening calls for this number."
        )
        assert filt.check(disclosure, phase=CallPhase.DISCLOSE_EXIT).allowed

    def test_emergency_exit_may_also_drop_character(self, filt: OutputFilter) -> None:
        line = "This is an automated line and cannot help — please hang up and dial 911."
        assert filt.check(line, phase=CallPhase.EMERGENCY_EXIT).allowed


class TestBlockedOutputStaysInCharacter:
    def test_replacement_is_a_fumble_not_an_error(self, filt: OutputFilter) -> None:
        result = filt.check("The number is 4111 1111 1111 1111.")
        assert result.text in FUMBLE_LINES
        # A blocked turn should read as the persona losing the thread, which is a
        # better stalling turn than whatever was blocked.
        assert "error" not in result.text.lower()

    def test_scanner_crash_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        filt = OutputFilter(owner_pii=["Norbert"], rng=random.Random(0))

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr(filt, "_scan", boom)
        result = filt.check("4111 1111 1111 1111")

        # A crash must never let unchecked text reach the phone line.
        assert result.blocked
        assert result.violations == (Violation.SCANNER_ERROR,)
        assert result.text in FUMBLE_LINES


class TestMultipleViolations:
    def test_reports_every_reason_it_blocked(self, filt: OutputFilter) -> None:
        result = filt.check("Norbert's card is 4111 1111 1111 1111.")
        assert Violation.OWNER_PII in result.violations
        assert Violation.VALID_CARD in result.violations

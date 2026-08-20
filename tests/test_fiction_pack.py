"""Fiction-pack invariants — guardrail G-3.

Two properties have to hold at once, and they pull in opposite directions:

* Nothing in the pack may be usable. No card a processor would take, no routable
  bank, no SSN that belongs to a living person, no phone number that rings.
* Everything in the pack must survive the pre-TTS filter. A persona that cannot
  recite its own fact sheet cannot run the fumbling tactic the whole project leans on.

The last test is the one that actually matters: it takes each identity's fact sheet
and pushes it through the real filter.
"""

from __future__ import annotations

import random

import pytest

from ssscammers.shared.fiction import (
    FictionIdentity,
    assert_identity_safe,
    generate_identity,
    load_pack,
)
from ssscammers.shared.output_filter import OutputFilter
from ssscammers.shared.validators import (
    aba_routing_valid,
    is_issuable_ssn,
    is_reserved_fictional_phone,
    luhn_valid,
)

#: Enough draws to catch a generator that is only usually safe.
_SAMPLE = 300


@pytest.fixture(scope="module")
def generated() -> list[FictionIdentity]:
    return [generate_identity(f"probe-{i}", seed=i) for i in range(_SAMPLE)]


class TestGeneratedIdentitiesAreUnusable:
    def test_no_card_would_ever_be_accepted(self, generated: list[FictionIdentity]) -> None:
        offenders = [i.persona_id for i in generated if luhn_valid(i.card_number)]
        assert offenders == []

    def test_no_bank_is_routable(self, generated: list[FictionIdentity]) -> None:
        offenders = [i.persona_id for i in generated if aba_routing_valid(i.routing_number)]
        assert offenders == []

    def test_no_ssn_belongs_to_anyone(self, generated: list[FictionIdentity]) -> None:
        offenders = [i.persona_id for i in generated if is_issuable_ssn(i.ssn)]
        assert offenders == []

    def test_nine_digit_values_are_unusable_read_either_way(
        self, generated: list[FictionIdentity]
    ) -> None:
        # A never-issued SSN can still be a checksum-valid routing number, and vice
        # versa. Both readings have to be dead.
        assert [i.persona_id for i in generated if aba_routing_valid(i.ssn)] == []
        assert [i.persona_id for i in generated if is_issuable_ssn(i.routing_number)] == []

    def test_no_phone_number_can_ring_a_real_person(
        self, generated: list[FictionIdentity]
    ) -> None:
        offenders = [i.persona_id for i in generated if not is_reserved_fictional_phone(i.phone)]
        assert offenders == []

    def test_every_email_domain_is_reserved(self, generated: list[FictionIdentity]) -> None:
        offenders = [i.persona_id for i in generated if not i.email.endswith("@example.com")]
        assert offenders == []

    def test_the_bundled_assertion_agrees(self, generated: list[FictionIdentity]) -> None:
        for identity in generated:
            assert_identity_safe(identity)


class TestIdentitiesAreStable:
    def test_same_persona_regenerates_identically(self) -> None:
        # A persona whose SSN changes between deploys contradicts itself when a
        # scammer calls back, which is precisely the tell we are avoiding.
        assert generate_identity("marjorie") == generate_identity("marjorie")

    def test_different_personas_get_different_identities(self) -> None:
        assert generate_identity("marjorie").ssn != generate_identity("harold").ssn


class TestPersonaCanReciteItsOwnFactSheet:
    """The integration that ties G-3 and G-4 together."""

    @pytest.mark.parametrize("index", range(25))
    def test_fact_sheet_passes_the_pre_tts_filter(self, index: int) -> None:
        identity = generate_identity(f"recite-{index}", seed=1000 + index)
        filt = OutputFilter.for_identity(identity, owner_pii=["Jason"], rng=random.Random(0))

        result = filt.check(identity.to_prompt_block())

        assert result.allowed, (
            f"identity {identity.persona_id} cannot say its own details: "
            f"{[v.value for v in result.violations]}"
        )

    def test_reading_the_card_aloud_digit_by_digit_is_allowed(self) -> None:
        identity = generate_identity("reader", seed=7)
        filt = OutputFilter.for_identity(identity, rng=random.Random(0))

        spoken_digits = " ".join(
            {
                "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
                "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
            }[char]
            for char in identity.card_number
            if char.isdigit()
        )
        result = filt.check(f"Alright dear, it's {spoken_digits}. Did you get all that?")

        assert result.allowed

    def test_a_real_card_is_still_blocked_for_that_same_persona(self) -> None:
        # Knowing its own numbers must not make the filter permissive generally.
        identity = generate_identity("reader", seed=7)
        filt = OutputFilter.for_identity(identity, rng=random.Random(0))

        assert filt.check("Try 4111 1111 1111 1111 instead.").blocked


class TestCheckedInPack:
    def test_every_shipped_identity_is_safe(self) -> None:
        pack = load_pack()
        assert pack, "fiction pack is empty — run scripts/generate_fiction_pack.py"
        for identity in pack.values():
            assert_identity_safe(identity)

    def test_every_numeric_field_is_plain_ascii(self) -> None:
        # The filter strips a persona's own numbers using `\D` (via `digits_only`), which
        # is what `_digit_runs` also uses. `str.isdigit()` additionally accepts 128 other
        # code points — superscripts, circled and Ethiopic digits — so a pack value
        # containing one would normalise differently under the two, and the persona's own
        # card could stop being strippable. Keeping the pack ASCII keeps them equivalent.
        numeric = ("phone", "routing_number", "account_number", "card_number", "card_cvv",
                   "ssn", "postal_code", "card_expiry")
        for identity in load_pack().values():
            for field in numeric:
                value = getattr(identity, field)
                assert value.isascii(), f"{identity.persona_id}.{field} is not ASCII: {value!r}"

    def test_every_shipped_identity_can_be_spoken(self) -> None:
        for identity in load_pack().values():
            filt = OutputFilter.for_identity(identity, rng=random.Random(0))
            result = filt.check(identity.to_prompt_block())
            assert result.allowed, f"{identity.persona_id}: {result.violations}"

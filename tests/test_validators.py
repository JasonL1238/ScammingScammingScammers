"""Checksum helpers. These back both the fiction pack and the output filter, so a bug
here either ships a usable card number or fails to catch one."""

from __future__ import annotations

import pytest

from ssscammers.shared.validators import (
    aba_routing_valid,
    digits_only,
    is_issuable_ssn,
    is_reserved_fictional_phone,
    luhn_valid,
)


class TestLuhn:
    @pytest.mark.parametrize(
        "number",
        [
            "4111111111111111",  # the canonical Visa test PAN
            "4242424242424242",  # Stripe's test card
            "5555555555554444",  # Mastercard test PAN
            "4539 1488 0343 6467",  # spaced, as a person would write it
        ],
    )
    def test_accepts_real_check_digits(self, number: str) -> None:
        # These all pass Luhn, which is exactly why the fiction pack must not use
        # them: the output filter blocks anything that passes.
        assert luhn_valid(number)

    @pytest.mark.parametrize(
        "number", ["4111111111111112", "4539148803436468", "1234567890123456"]
    )
    def test_rejects_broken_check_digits(self, number: str) -> None:
        assert not luhn_valid(number)

    def test_rejects_fragments_too_short_to_be_a_card(self) -> None:
        # A four-digit run that happens to satisfy the parity rule is not a card,
        # and treating it as one would block half the persona's sentences.
        assert not luhn_valid("4242")
        assert not luhn_valid("18")


class TestAbaRouting:
    @pytest.mark.parametrize("number", ["021000021", "011401533", "091000019"])
    def test_accepts_real_routing_numbers(self, number: str) -> None:
        assert aba_routing_valid(number)

    def test_rejects_broken_checksum(self) -> None:
        assert not aba_routing_valid("021000022")

    def test_rejects_wrong_length(self) -> None:
        assert not aba_routing_valid("02100002")
        assert not aba_routing_valid("0210000210")


class TestSsn:
    @pytest.mark.parametrize("number", ["123-45-6789", "001-01-0001", "899-99-9999"])
    def test_flags_issuable_ranges(self, number: str) -> None:
        assert is_issuable_ssn(number)

    @pytest.mark.parametrize(
        "number",
        [
            "900-12-3456",  # 900+ was never issued
            "999-99-9999",
            "666-12-3456",  # 666 was never issued
            "000-12-3456",
            "123-00-4567",  # group 00 never issued
            "123-45-0000",  # serial 0000 never issued
        ],
    )
    def test_clears_never_issued_ranges(self, number: str) -> None:
        assert not is_issuable_ssn(number)


class TestReservedPhone:
    @pytest.mark.parametrize(
        "number", ["(937) 555-0142", "9375550100", "1-937-555-0199", "937.555.0155"]
    )
    def test_accepts_the_fiction_block(self, number: str) -> None:
        assert is_reserved_fictional_phone(number)

    @pytest.mark.parametrize(
        "number",
        [
            "(937) 555-0200",  # just past the reserved range
            "(937) 555-1234",
            "(937) 234-0142",  # right subscriber, wrong exchange
            "555-0142",  # no area code
        ],
    )
    def test_rejects_numbers_that_could_ring_someone(self, number: str) -> None:
        assert not is_reserved_fictional_phone(number)


def test_digits_only_strips_formatting() -> None:
    assert digits_only("(937) 555-0142") == "9375550142"

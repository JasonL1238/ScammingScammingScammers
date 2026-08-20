"""Checks and normalisation for financial, identity, and phone numbers.

Three callers with different jobs share these functions, which is the point of one place:

* The fiction pack uses the checks to *prove* every number it hands the persona is
  structurally invalid — unusable card, unroutable bank, never-issued SSN.
* The output filter uses the same checks to *catch* a number that would actually work, in
  case the model invents one despite being told not to. If those two disagreed, the pack
  would ship something usable — so they are the same code.
* Triage's allowlist uses :func:`national_digits` to bucket phone numbers. That one is a
  normaliser rather than a check, and it is load-bearing for safety: the allowlist drives
  ``Trigger.ALLOWLISTED``, a G-11 exit, so loosening it to fix a formatting edge case
  widens or narrows who never gets baited.
"""

from __future__ import annotations

import re

__all__ = [
    "digits_only",
    "national_digits",
    "luhn_valid",
    "aba_routing_valid",
    "is_issuable_ssn",
    "is_reserved_fictional_phone",
    "DIGIT_WORDS",
]

_NON_DIGIT = re.compile(r"\D")

#: Spoken forms of each digit. The persona reads numbers aloud, so by the time text
#: reaches the filter a card number looks like "four one one one" — never "4111".
#: "oh" and "o" are how people actually say zero on the phone.
DIGIT_WORDS: dict[str, str] = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "nought": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def digits_only(value: str) -> str:
    """Strip everything that is not a digit."""
    return _NON_DIGIT.sub("", value)


def national_digits(number: str) -> str:
    """Reduce a phone number to comparable ten-digit NANP form.

    Formatting varies by carrier and by webhook field, so "+1 (937) 555-0142" and
    "9375550142" have to land in the same bucket — otherwise an allowlisted caller gets
    baited. Shorter or longer inputs come back as their bare digits; the caller decides
    whether that is usable.
    """
    digits = digits_only(number)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def luhn_valid(number: str) -> bool:
    """True if ``number`` passes the Luhn check digit used by real payment cards.

    Every card the persona reads aloud must fail this: anything that passes is a number a
    processor might accept, the one thing the agent must never say.
    """
    raw = digits_only(number)
    if len(raw) < 12:
        # Too short to be a payment card; Luhn on a fragment is meaningless.
        return False

    total = 0
    # Double every second digit counting from the right.
    for index, char in enumerate(reversed(raw)):
        digit = ord(char) - 48
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def aba_routing_valid(number: str) -> bool:
    """True if ``number`` is a checksum-valid ABA routing number.

    Weights are 3-7-1 repeating across the nine digits; a real routing number sums to
    a multiple of ten.
    """
    raw = digits_only(number)
    if len(raw) != 9:
        return False

    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    total = sum(weight * (ord(char) - 48) for weight, char in zip(weights, raw, strict=True))
    return total % 10 == 0


def is_issuable_ssn(number: str) -> bool:
    """True if ``number`` falls in a range the SSA has ever issued.

    The pack draws only from ranges that were never issued, so no persona SSN can
    collide with a living person's. Never-issued: area 000, area 666, area 900-999,
    group 00, serial 0000.
    """
    raw = digits_only(number)
    if len(raw) != 9:
        return False

    area, group, serial = int(raw[:3]), int(raw[3:5]), int(raw[5:])
    if area == 0 or area == 666 or area >= 900:
        return False
    return group != 0 and serial != 0


def is_reserved_fictional_phone(number: str) -> bool:
    """True if ``number`` is in the NANP block reserved for fiction (555-0100..555-0199).

    These are the only phone numbers the persona may say. They cannot ring anyone.
    """
    raw = national_digits(number)
    if len(raw) != 10:
        return False

    exchange, subscriber = raw[3:6], int(raw[6:])
    return exchange == "555" and 100 <= subscriber <= 199

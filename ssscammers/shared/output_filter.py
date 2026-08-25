"""The one place agent speech is checked before it reaches text-to-speech.

Guardrails G-3 (no real personal data) and G-4 (no usable financial instruments) are
enforced here, and the persona-break check for G-18 rides along, because splitting them
across two components was how the original design allowed a gap between them. Every
caller uses this scan; nobody implements their own. (The model-backed watchdog in
``docs/guardrails.md`` is designed to sit alongside it and is not built yet.)

When a check trips, the utterance is replaced with an in-character fumble — the persona
drops the pen, loses its place, squints at the card. A false positive is therefore cheap
and a false negative is the worst outcome the project has, so the bias runs toward
blocking. A bug in the scan itself **fails closed**: an exception yields a fumble line,
never the unchecked text.

Cheap is not free, though, and one class of false positive would gut the project: **the
persona has to be able to read its own fiction-pack card and SSN aloud**, because fumbling
digits for ten minutes is the best stalling tactic in the playbook. Two things keep the
scan precise enough to allow it:

* The active identity's own numbers are stripped from the digit stream before any check
  runs, so the fact sheet is speakable by construction.
* Checks are anchored rather than slid across every offset. A long digit run contains a
  Luhn-passing sub-window by chance often enough to matter, and a nine-digit window inside
  a card number is not an SSN.
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from ssscammers.shared.enums import CallPhase
from ssscammers.shared.validators import (
    DIGIT_WORDS,
    aba_routing_valid,
    digits_only,
    is_issuable_ssn,
    luhn_valid,
)

logger = logging.getLogger(__name__)

__all__ = ["Violation", "FilterResult", "OutputFilter", "FUMBLE_LINES", "trailing_digit_run"]


class Violation(StrEnum):
    """Why an utterance was blocked. Logged verbatim so prompt drift is visible."""

    OWNER_PII = "owner_pii"
    """Text contained something from the owner's real-identity denylist."""

    VALID_CARD = "valid_card"
    """A digit run passed the Luhn check — a processor might have accepted it."""

    VALID_ROUTING = "valid_routing"
    """A nine-digit run was a checksum-valid ABA routing number."""

    ISSUABLE_SSN = "issuable_ssn"
    """A nine-digit run fell inside a range the SSA actually issues."""

    PERSONA_BREAK = "persona_break"
    """The model started explaining that it is an AI while still baiting."""

    SCANNER_ERROR = "scanner_error"
    """The scan itself raised. Fail closed: speak a fumble, not the unchecked text."""


#: Said instead of a blocked utterance. Each one is a plausible next turn for a
#: confused elderly caller, so a block reads as character rather than as a glitch.
FUMBLE_LINES: tuple[str, ...] = (
    "Oh — hold on, dear, I've lost my place. Where were we?",
    "Wait now, the pen's gone and stopped writing. Just a moment.",
    "Hang on, hang on — I've got the wrong card out, I think. Let me look again.",
    "Oh dear, my glasses have slid right off. Give me a second, would you?",
    "Sorry, sorry — say that again for me? I got muddled.",
)

#: Phrases that mean the model has stepped out of the persona. Harmless during a
#: disclosure exit (where saying it is the whole point) and disqualifying anywhere else.
_PERSONA_BREAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bas an ai\b",
        r"\bai (?:language )?model\b",
        r"\bi(?:'m| am) an ai\b",
        r"\bi(?:'m| am) (?:a|an) (?:bot|chatbot|virtual assistant|automated)\b",
        r"\bmy (?:system )?(?:prompt|instructions)\b",
        r"\banthropic\b",
        r"\bclaude\b",
        r"\blarge language model\b",
    )
)

#: "double seven" / "triple five" — how people dictate repeated digits on the phone.
_REPEAT_WORDS: dict[str, int] = {"double": 2, "triple": 3, "treble": 3}

_WORD_RE = re.compile(r"[a-z']+|\d")

#: Lengths real payment cards come in.
_CARD_LENGTHS: frozenset[int] = frozenset({13, 14, 15, 16, 19})
_NINE = 9


@dataclass(frozen=True)
class FilterResult:
    """Verdict on one candidate utterance."""

    text: str
    """What to actually speak — the original, or a fumble line if blocked."""

    allowed: bool
    """True when the original text passed untouched."""

    violations: tuple[Violation, ...] = ()

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass
class OutputFilter:
    """Scans candidate agent speech. Construct one per call and reuse across turns.

    Args:
        owner_pii: Real strings that must never be spoken — the owner's name, email,
            family names, street. Matched case-insensitively on word boundaries.
        fiction_digits: Digit strings from the active identity's fact sheet (card,
            SSN, routing, account, phone). These are removed from the digit stream
            before checking, so the persona can read its own invented details aloud.
            Pass the identity's values here, never real ones.
        rng: Injected for deterministic fumble selection in tests.
    """

    owner_pii: Sequence[str] = ()
    fiction_digits: Sequence[str] = ()
    rng: random.Random = field(default_factory=random.Random)

    _pii_pattern: re.Pattern[str] | None = field(init=False, default=None, repr=False)
    _fiction: tuple[str, ...] = field(init=False, default=(), repr=False)

    def __post_init__(self) -> None:
        terms = [term.strip() for term in self.owner_pii if term and term.strip()]
        if terms:
            # Longest first so "norbert@example.net" wins over a bare "norbert".
            terms.sort(key=len, reverse=True)
            joined = "|".join(re.escape(term) for term in terms)
            self._pii_pattern = re.compile(rf"(?<!\w)(?:{joined})(?!\w)", re.IGNORECASE)

        # Longest first so a card is removed before its own nine-digit sub-slice.
        fiction = {digits_only(value) for value in self.fiction_digits}
        self._fiction = tuple(sorted((f for f in fiction if len(f) >= 4), key=len, reverse=True))

    @classmethod
    def for_identity(
        cls,
        identity: object,
        owner_pii: Sequence[str] = (),
        rng: random.Random | None = None,
    ) -> OutputFilter:
        """Build a filter that knows one persona's invented numbers.

        Accepts anything with the fiction-pack field names; typed loosely so this
        module stays importable without pulling in the fiction pack.
        """
        values = [
            getattr(identity, name, "") or ""
            for name in ("card_number", "ssn", "routing_number", "account_number", "phone", "card_cvv")
        ]
        return cls(
            owner_pii=owner_pii,
            fiction_digits=[str(v) for v in values],
            rng=rng or random.Random(),
        )

    def check(self, text: str, *, phase: CallPhase = CallPhase.STALL) -> FilterResult:
        """Return what may safely be spoken in place of ``text``.

        Args:
            text: The model's candidate utterance.
            phase: Current call phase. The exits are *required* to break persona, so
                that check is skipped there.
        """
        try:
            violations = tuple(self._scan(text, phase))
        except Exception:  # noqa: BLE001 - fail closed, never leak unchecked text
            logger.exception("output filter scan raised; falling back to a fumble line")
            return FilterResult(self._fumble(), allowed=False, violations=(Violation.SCANNER_ERROR,))

        if not violations:
            return FilterResult(text, allowed=True)

        logger.error(
            "blocked agent utterance: violations=%s phase=%s len=%d",
            [v.value for v in violations],
            phase.value,
            len(text),
        )
        return FilterResult(self._fumble(), allowed=False, violations=violations)

    def _scan(self, text: str, phase: CallPhase) -> Iterable[Violation]:
        if self._pii_pattern is not None and self._pii_pattern.search(text):
            yield Violation.OWNER_PII

        # Persona breaks are mandatory during the exits and disqualifying elsewhere.
        if phase not in (CallPhase.DISCLOSE_EXIT, CallPhase.EMERGENCY_EXIT) and any(
            pattern.search(text) for pattern in _PERSONA_BREAK_PATTERNS
        ):
            yield Violation.PERSONA_BREAK

        yield from self._scan_number_runs(text)

    def _scan_number_runs(self, text: str) -> Iterable[Violation]:
        """Check every contiguous run of spoken or written digits.

        Digits arrive as words far more often than as numerals — this text is on its way to
        a synthesiser — so both forms normalise into the same run before checking.
        """
        seen: set[Violation] = set()

        for raw_run in _digit_runs(text):
            for run in self._strip_fiction(raw_run):
                length = len(run)

                # Anchored to the complete run, deliberately: sliding a window over a
                # long run is ~nine independent one-in-ten checks, so an innocent
                # sixteen-digit number would be blocked more often than not. The risk
                # worth catching is a well-formed card, which *is* the whole run.
                if length in _CARD_LENGTHS and luhn_valid(run):
                    seen.add(Violation.VALID_CARD)

                # Likewise: only a run that is exactly nine digits is a candidate SSN
                # or routing number. Nine digits carved out of a card is neither.
                if length == _NINE:
                    if aba_routing_valid(run):
                        seen.add(Violation.VALID_ROUTING)
                    if is_issuable_ssn(run):
                        seen.add(Violation.ISSUABLE_SSN)

        yield from sorted(seen)

    def _strip_fiction(self, run: str) -> list[str]:
        """Remove the active identity's own numbers, returning what is left to check.

        Splitting rather than blanking: digits either side of a removed card are separate
        runs, not one concatenation that could look like a different instrument.
        """
        parts = [run]
        for known in self._fiction:
            nxt: list[str] = []
            for part in parts:
                nxt.extend(part.split(known))
            parts = nxt
        return [part for part in parts if part]

    def _fumble(self) -> str:
        return self.rng.choice(FUMBLE_LINES)


def trailing_digit_run(text: str, *, limit: int = 19) -> str:
    """The digit run ``text`` ends on, as a listener would have heard it.

    Exists for the streaming path. A reply is checked cumulatively within itself, but a
    scammer prompting "and the rest?" splits a number across two *turns*, which a per-turn
    check cannot see. Carrying this tail into the next check restores the adjacency: a run
    continues across the boundary only if no ordinary word interrupted it, which is exactly
    when a listener could still assemble the number. Returns "" when the text does not end
    mid-run, since a trailing word means the run is already broken.

    Args:
        limit: Longest tail worth keeping — one digit more than the longest instrument
            checked, so padding cannot defeat it and a long call cannot accumulate a run
            past every length being matched.
    """
    runs = _digit_runs(text)
    if not runs:
        return ""
    tokens = _WORD_RE.findall(text.lower())
    while tokens and tokens[-1] in _REPEAT_WORDS:
        tokens.pop()
    if not tokens:
        return ""
    last = tokens[-1]
    if not (last.isdigit() or last in DIGIT_WORDS):
        return ""
    return runs[-1][-limit:]


def _digit_runs(text: str) -> list[str]:
    """Collapse ``text`` into the digit sequences a listener would hear.

    "four one one one, one one one one" and "4111 1111" come back as the same run.
    Non-digit words break it: "four score and seven" is not an account number.
    """
    runs: list[str] = []
    current: list[str] = []
    pending_repeat = 0

    for token in _WORD_RE.findall(text.lower()):
        if token.isdigit():
            current.append(token)
            pending_repeat = 0
            continue

        if token in _REPEAT_WORDS:
            pending_repeat = _REPEAT_WORDS[token]
            continue

        digit = DIGIT_WORDS.get(token)
        if digit is not None:
            current.append(digit * pending_repeat if pending_repeat else digit)
            pending_repeat = 0
            continue

        # A non-numeric word ends the run.
        pending_repeat = 0
        if current:
            runs.append("".join(current))
            current = []

    if current:
        runs.append("".join(current))
    return runs

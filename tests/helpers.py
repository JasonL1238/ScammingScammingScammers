"""Shared test fixtures for the call layer.

Three test modules drive a real :class:`~ssscammers.agent.persona_director.PersonaDirector`
and each used to build one from scratch. That is not a style problem: the base kwargs
encode what a *pinned* call looks like — seeded RNG, a known owner-PII term, a known
safeword — and three copies drift, so a test can quietly stop pinning the thing it
believes it pinned.
"""

from __future__ import annotations

import random

from ssscammers.agent.persona import Persona, load_persona
from ssscammers.agent.persona_director import PersonaDirector
from ssscammers.shared.enums import EntryPath

#: The caller number every offline test dials from. Not on any allowlist.
CALLER = "+19375559999"

#: Stands in for the owner's real name, which the output filter must never speak.
OWNER_PII: tuple[str, ...] = ("Jason",)

SAFEWORD = "pineapple"


class FakeClock:
    """A monotonic clock a test winds forward by hand.

    Injected wherever production reads ``time.monotonic``, which is what lets a
    ninety-minute call, a sixty-second dead-air window, and a ninety-second hold all be
    exercised in the same millisecond.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_director(
    *,
    persona_id: str = "marjorie",
    persona: Persona | None = None,
    forwarded: bool = False,
    **overrides: object,
) -> PersonaDirector:
    """A director with every source of randomness pinned down.

    Args:
        persona: Pre-built persona, for tests that need to pin pacing as well.
        forwarded: Arrive as a conditional-forward call, which triage treats more
            carefully than a direct dial.
        overrides: Any ``PersonaDirector`` field — caps and thresholds, mostly.
    """
    base: dict[str, object] = {
        "persona": persona if persona is not None else load_persona(persona_id),
        "caller_number": CALLER,
        "entry_path": EntryPath.CONDITIONAL_FORWARD if forwarded else EntryPath.DIRECT,
        "owner_pii": OWNER_PII,
        "safeword": SAFEWORD,
        "rng": random.Random(0),
    }
    base.update(overrides)
    return PersonaDirector(**base)  # type: ignore[arg-type]


UNSERVABLE_BUNDLE = {
    "id": "unvoiceable",
    "display_name": "Unvoiceable",
    "voice": {"tts": "elevenlabs", "voice_id": "some-elevenlabs-id"},
}
"""A bundle naming a provider ``media.IMPLEMENTED_TTS`` does not cover.

The refusal tests used the shipped ``dot`` persona for this, so fixing that persona broke
four unrelated tests — and, worse, the guard would have been left untested the moment
every shipped bundle became servable. Synthetic instead, and shared, because both
``test_media.py`` and ``test_webhooks.py`` need it.
"""

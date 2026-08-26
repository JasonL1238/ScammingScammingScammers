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
from ssscammers.agent.registry import Admission, CallRegistry
from ssscammers.shared.enums import EntryPath

# Re-exported for the test suite: the ONE simulated clock, shared with the
# textloop harness. There must never be a second implementation — the replay
# work runs the same call under both, so they must be the same object.
from ssscammers.simscammer.clock import SimulatedClock

__all__ = [
    "CALLER",
    "RESERVED_CALLER",
    "OWNER_PII",
    "SAFEWORD",
    "SHIPPED",
    "SimulatedClock",
    "make_director",
    "reserve_call",
    "UNSERVABLE_BUNDLE",
]

#: The caller number every offline test dials from. Not on any allowlist.
CALLER = "+19375559999"

#: The caller number registry tests reserve from. Deliberately distinct from
#: ``CALLER``: this one pins the admission path, that one the director path.
RESERVED_CALLER = "+19375550101"

#: The personas that ship. Hardcoded on purpose: it is the independent anchor
#: that `available_personas()` is checked *against*, so a discovery bug that
#: silently drops a bundle cannot also shrink the expectation and stay green.
SHIPPED: tuple[str, ...] = ("marjorie", "harold", "dot")

#: Stands in for the owner's real name, which the output filter must never speak.
#: Deliberately fictional — the test data carries no real owner PII.
OWNER_PII: tuple[str, ...] = ("Norbert",)

SAFEWORD = "pineapple"


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


def reserve_call(
    registry: CallRegistry, call_sid: str, *, persona_id: str = "marjorie"
) -> Admission:
    """Reserve a pinned direct call — the admission every registry test starts from.

    Rolled identically in three test modules before it lived here; the kwargs are the
    pinned shape of a direct-dial admission, and three copies drift.
    """
    return registry.reserve(
        call_sid=call_sid,
        caller_number=RESERVED_CALLER,
        entry_path=EntryPath.DIRECT,
        persona_id=persona_id,
    )


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

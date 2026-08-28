"""Shared scaffolding for the call layer: the pinned call, and the fakes around it.

Three test modules drive a real :class:`~ssscammers.agent.persona_director.PersonaDirector`
and each used to build one from scratch. That is not a style problem: the base kwargs
encode what a *pinned* call looks like — seeded RNG, a known owner-PII term, a known
safeword — and three copies drift, so a test can quietly stop pinning the thing it
believes it pinned.

:func:`build` and its fakes moved here for the same reason, one duplicate later:
``test_conversation.py`` owned them, ``test_replay.py`` had rolled its own
``RecordingSink``, and ``test_monitor.py`` would have been the third. What ``build``
pins is the single shared RNG stream — the same shape ``build_conversation`` wires in
production — and a second copy of that is a copy that can quietly stop pinning it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import random
from collections.abc import AsyncIterator

from ssscammers.agent.conversation import Action, CallEvent, Conversation, Say
from ssscammers.agent.persona import Pacing, Persona, load_persona
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
    "RecordingSink",
    "ScriptedBrain",
    "build",
    "drain",
    "make_director",
    "reserve_call",
    "spoken",
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


class ScriptedBrain:
    """Streams fixed sentences, optionally spending simulated time or failing."""

    def __init__(
        self,
        *sentences: str,
        clock: SimulatedClock | None = None,
        seconds_per_sentence: float = 0.0,
        raises: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.sentences = sentences
        self.clock = clock
        self.seconds_per_sentence = seconds_per_sentence
        self.raises = raises
        self.hang = hang
        self.calls: list[str | None] = []
        self.histories: list[list] = []

    async def stream_reply(self, history, state_note=None) -> AsyncIterator[str]:  # noqa: ANN001
        self.calls.append(state_note)
        self.histories.append(list(history))
        if self.raises is not None:
            raise self.raises
        if self.hang:
            # Never yields and never returns; the turn must time out rather than hang.
            await asyncio.sleep(3600)
        for sentence in self.sentences:
            if self.clock is not None and self.seconds_per_sentence:
                self.clock.advance(self.seconds_per_sentence)
            yield sentence


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[CallEvent] = []
        self.fail = fail

    async def emit(self, event: CallEvent) -> None:
        if self.fail:
            raise RuntimeError("sink is down")
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]


def build(
    *,
    brain: ScriptedBrain | None = None,
    clock: SimulatedClock | None = None,
    sink: RecordingSink | None = None,
    character_delay_ms: int | None = None,
    hold_probability: float = 0.0,
    hold_seconds: int = 30,
    dead_air_seconds: float = 60.0,
    hard_cap_seconds: float = 5400.0,
    safeword: str = "pineapple",
) -> tuple[Conversation, SimulatedClock, RecordingSink]:
    """A conversation with every source of randomness pinned down.

    One ``Random(0)`` shared by the director, the filter, and the conversation —
    the same single-stream shape ``build_conversation`` wires in production. Two
    separately seeded streams would pin a draw order production never executes.
    """
    clock = clock or SimulatedClock()
    sink = sink or RecordingSink()

    persona = load_persona("marjorie")
    pacing = Pacing(
        reply_delay_ms_mean=character_delay_ms if character_delay_ms is not None else 0,
        reply_delay_ms_stdev=0,
        hold_probability=hold_probability,
        hold_seconds_min=hold_seconds,
        hold_seconds_max=hold_seconds,
    )
    shared_rng = random.Random(0)
    director = make_director(
        persona=dataclasses.replace(persona, pacing=pacing),
        safeword=safeword,
        dead_air_seconds=dead_air_seconds,
        hard_cap_seconds=hard_cap_seconds,
        rng=shared_rng,
    )
    conversation = Conversation(
        director=director,
        brain=brain,  # type: ignore[arg-type] - duck-typed on stream_reply
        clock=clock,
        events=sink,
        rng=shared_rng,
    )
    return conversation, clock, sink


async def drain(conversation: Conversation, utterance: str) -> list[Action]:
    return [action async for action in conversation.respond(utterance)]


def spoken(actions: list[Action]) -> list[str]:
    return [action.text for action in actions if isinstance(action, Say)]

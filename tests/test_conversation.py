"""Driving a call, without a phone line or a model.

The whole point of :class:`~ssscammers.agent.conversation.Conversation` is that these
behaviours are checkable offline: what the caller hears, in what order, and what stops
them hearing something they should not. No network, no speech stack, no waiting.
"""

from __future__ import annotations

import dataclasses
import logging
import random
from collections.abc import AsyncIterator

import pytest
from helpers import FakeClock, make_director

from ssscammers.agent.conversation import (
    Action,
    CallEvent,
    Conversation,
    HangUp,
    Pause,
    PlayClip,
    Say,
)
from ssscammers.agent.llm import Turn
from ssscammers.agent.persona import Pacing, load_persona
from ssscammers.agent.persona_director import (
    DISCLOSURE_SCRIPT,
    EMERGENCY_SCRIPT,
    NEUTRAL_GREETING,
    VICTIM_WARNING_SCRIPT,
)
from ssscammers.shared.enums import CallPhase, EndReason
from ssscammers.shared.output_filter import FUMBLE_LINES

#: A Luhn-valid card, split so that neither half is blockable on its own but the pair
#: reads out a working number. Verified against the real filter in the test below.
CARD_FIRST_HALF = "The number is 4539 1488."
CARD_SECOND_HALF = "0343 6467."


class ScriptedBrain:
    """Streams fixed sentences, optionally spending simulated time or failing."""

    def __init__(
        self,
        *sentences: str,
        clock: FakeClock | None = None,
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
            import asyncio

            await asyncio.sleep(3600)
        for sentence in self.sentences:
            if self.clock is not None and self.seconds_per_sentence:
                self.clock.advance(self.seconds_per_sentence)
            yield sentence


class TurnScriptedBrain:
    """Streams a different set of sentences on each successive turn."""

    def __init__(self, *turns: list[str]) -> None:
        self.turns = list(turns)
        self.index = 0

    async def stream_reply(self, history, state_note=None) -> AsyncIterator[str]:  # noqa: ANN001
        sentences = self.turns[min(self.index, len(self.turns) - 1)]
        self.index += 1
        for sentence in sentences:
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
    clock: FakeClock | None = None,
    sink: RecordingSink | None = None,
    character_delay_ms: int | None = None,
    hold_probability: float = 0.0,
    hold_seconds: int = 30,
    dead_air_seconds: float = 60.0,
    hard_cap_seconds: float = 5400.0,
    safeword: str = "pineapple",
) -> tuple[Conversation, FakeClock, RecordingSink]:
    """A conversation with every source of randomness pinned down.

    One ``Random(0)`` shared by the director, the filter, and the conversation —
    the same single-stream shape ``build_conversation`` wires in production. Two
    separately seeded streams would pin a draw order production never executes.
    """
    clock = clock or FakeClock()
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


class TestAnswering:
    async def test_the_call_opens_on_a_neutral_greeting(self) -> None:
        # A misrouted caller must not hear a character before triage has looked at them.
        conversation, _, _ = build()
        actions = await conversation.open()
        assert spoken(actions) == [NEUTRAL_GREETING]

    async def test_opening_is_logged(self) -> None:
        conversation, _, sink = build()
        await conversation.open()
        assert sink.types() == ["call_opened"]
        assert sink.events[0].payload["persona"] == "marjorie"

    async def test_elapsed_time_only_starts_at_answer(self) -> None:
        conversation, clock, _ = build()
        clock.advance(30)
        assert conversation.elapsed_seconds == 0.0

        await conversation.open()
        clock.advance(12)
        assert conversation.elapsed_seconds == pytest.approx(12.0)


class TestFixedScriptsAreSpokenVerbatim:
    """G-11 and G-12. These words are the product, not a suggestion to the model."""

    async def test_a_real_caller_hears_the_disclosure_and_the_call_ends(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("should never be spoken"))
        await conversation.open()

        actions = await drain(
            conversation, "Hi, this is Sandra from the pharmacy, your prescription is ready."
        )
        assert spoken(actions) == [DISCLOSURE_SCRIPT]
        assert any(isinstance(a, HangUp) for a in actions)
        assert conversation.final_phase is CallPhase.DISCLOSE_EXIT

    async def test_the_model_is_not_consulted_on_a_scripted_turn(self) -> None:
        brain = ScriptedBrain("nope")
        conversation, _, _ = build(brain=brain)
        await conversation.open()
        await drain(conversation, "Sorry, I think I've got the wrong number.")
        assert brain.calls == []

    async def test_an_emergency_gets_the_redirect_and_nothing_else(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"))
        await conversation.open()
        actions = await drain(conversation, "There's a fire, please call 911 for me!")
        assert spoken(actions) == [EMERGENCY_SCRIPT]
        assert conversation.final_phase is CallPhase.EMERGENCY_EXIT


class TestTheFilterCannotBeEvadedAcrossSentences:
    """G-4, and the reason sentences are checked cumulatively rather than alone."""

    def test_the_evasion_is_real(self) -> None:
        # Guards the premise of the tests below: each half passes on its own, so a
        # per-sentence filter would speak the whole card.
        conversation, _, _ = build()
        assert not conversation.director.vet_result(CARD_FIRST_HALF).blocked
        assert not conversation.director.vet_result(CARD_SECOND_HALF).blocked
        combined = f"{CARD_FIRST_HALF} {CARD_SECOND_HALF}"
        assert conversation.director.vet_result(combined).blocked

    async def test_the_second_half_of_a_card_is_never_spoken(self) -> None:
        brain = ScriptedBrain(CARD_FIRST_HALF, CARD_SECOND_HALF, "And that's the lot.")
        conversation, _, _ = build(brain=brain)
        await conversation.open()

        actions = await drain(conversation, "Hello? Who is this?")
        said = spoken(actions)
        assert CARD_SECOND_HALF not in said
        assert said[-1] in FUMBLE_LINES

    async def test_a_block_ends_the_turn(self) -> None:
        # The rest of the reply is exactly where the remainder of the number was going.
        brain = ScriptedBrain(CARD_FIRST_HALF, CARD_SECOND_HALF, "Third sentence.")
        conversation, _, _ = build(brain=brain)
        await conversation.open()

        said = spoken(await drain(conversation, "Hello?"))
        assert "Third sentence." not in said

    async def test_a_block_is_logged_as_an_event(self) -> None:
        brain = ScriptedBrain(CARD_FIRST_HALF, CARD_SECOND_HALF)
        conversation, _, sink = build(brain=brain)
        await conversation.open()
        await drain(conversation, "Hello?")

        blocked = [e for e in sink.events if e.type == "output_blocked"]
        assert blocked and "valid_card" in blocked[0].payload["violations"]

    async def test_the_transcript_records_what_was_actually_said(self) -> None:
        # If history kept the blocked text, the model would believe it had said it and
        # would answer follow-up questions about a number nobody heard.
        brain = ScriptedBrain(CARD_FIRST_HALF, CARD_SECOND_HALF)
        conversation, _, _ = build(brain=brain)
        await conversation.open()
        await drain(conversation, "Hello?")

        assert not any(CARD_SECOND_HALF in turn.content for turn in conversation.history)

    async def test_safe_replies_stream_sentence_by_sentence(self) -> None:
        # The latency win has to survive the filtering: one Say per sentence, so TTS
        # can start on the first while the second is still being written.
        brain = ScriptedBrain("Oh dear.", "Let me find my glasses.", "Now then.")
        conversation, _, _ = build(brain=brain)
        await conversation.open()

        assert spoken(await drain(conversation, "Hello?")) == [
            "Oh dear.",
            "Let me find my glasses.",
            "Now then.",
        ]


class TestTheFilterCannotBeEvadedAcrossTurns:
    """The same G-4 hole, one turn wider.

    A scammer does not need the persona to split a number across two sentences of one
    reply — "and the rest?" splits it across two *turns*, and the two highest-weighted
    tactics for this persona (`fumble_data`, `read_back`) both direct the model to read a
    number out in pieces while being prompted.
    """

    async def test_the_second_half_of_a_card_is_not_spoken_next_turn(self) -> None:
        brain = TurnScriptedBrain([CARD_FIRST_HALF], [CARD_SECOND_HALF])
        conversation, _, _ = build(brain=brain)
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()

        first = spoken(await drain(conversation, "What's the card number?"))
        second = spoken(await drain(conversation, "And the rest?"))
        assert first == [CARD_FIRST_HALF]
        assert CARD_SECOND_HALF not in second
        assert second and second[-1] in FUMBLE_LINES

    async def test_an_interrupting_word_breaks_the_run_and_is_allowed(self) -> None:
        # The tail is only carried when the previous turn actually ended mid-run. A
        # listener whose digits were interrupted by ordinary speech cannot assemble them,
        # so blocking here would gut the strongest tactic in the playbook for nothing.
        brain = TurnScriptedBrain(
            ["The number is 4539 1488, now where has my pen gone."], [CARD_SECOND_HALF]
        )
        conversation, _, _ = build(brain=brain)
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()

        await drain(conversation, "What's the card number?")
        second = spoken(await drain(conversation, "And the rest?"))
        assert second == [CARD_SECOND_HALF]

    async def test_ordinary_digits_across_turns_are_not_blocked(self) -> None:
        # Over an hour-long call the persona says numbers constantly. Nothing here may
        # turn into a filter that blocks a house number or a time of day.
        brain = TurnScriptedBrain(["I'm at number 42."], ["Half past three, dear."])
        conversation, _, _ = build(brain=brain)
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()

        assert spoken(await drain(conversation, "Where do you live?")) == ["I'm at number 42."]
        assert spoken(await drain(conversation, "What time?")) == ["Half past three, dear."]


class TestTimerTicksDoNotDisturbTheCall:
    async def test_an_idle_tick_does_not_consume_a_tactic(self) -> None:
        # `choose_tactic(exclude={last})` stops the persona mishearing four turns running.
        # A tick that runs the baiting planner picks a tactic nobody performs, which
        # poisons that exclusion — at one tick a second, ~25 phantom tactics per real turn.
        conversation, clock, _ = build(brain=ScriptedBrain("Oh yes dear."))
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Tell me about the warranty.")

        before = conversation.director._last_tactic
        for _ in range(25):
            clock.advance(1.0)
            assert [a async for a in conversation.tick()] == []
        assert conversation.director._last_tactic is before

    async def test_an_idle_tick_does_not_rewrite_the_last_plan(self) -> None:
        conversation, clock, _ = build(brain=ScriptedBrain("Oh yes dear."))
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Tell me about the warranty.")

        planned = conversation.last_plan
        clock.advance(1.0)
        assert [a async for a in conversation.tick()] == []
        assert conversation.last_plan is planned


class TestWhoIsOwedAVoicemail:
    """`DISCLOSE_EXIT` covers two scripts that promise opposite things."""

    async def test_an_ordinary_released_caller_is_promised_one(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"))
        await conversation.open()
        await drain(conversation, "Hi, this is Sandra from the pharmacy, prescription's ready.")
        assert conversation.final_phase is CallPhase.DISCLOSE_EXIT
        assert conversation.offered_voicemail

    async def test_a_victim_told_to_hang_up_is_not(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"))
        await conversation.open()
        actions = await drain(
            conversation,
            "Someone called me and said my account was compromised, "
            "they told me to call this number.",
        )
        assert spoken(actions) == [VICTIM_WARNING_SCRIPT]
        assert conversation.final_phase is CallPhase.DISCLOSE_EXIT
        assert not conversation.offered_voicemail

    async def test_nobody_else_is(self) -> None:
        conversation, clock, _ = build(hard_cap_seconds=100.0)
        await conversation.open()
        clock.advance(101)
        assert [a async for a in conversation.tick()]
        assert not conversation.offered_voicemail


class TestLatency:
    async def test_character_delay_is_added_when_the_model_was_quick(self) -> None:
        conversation, clock, _ = build(
            brain=ScriptedBrain("Hello dear."), character_delay_ms=1100
        )
        await conversation.open()

        actions = await drain(conversation, "Hello?")
        pauses = [a for a in actions if isinstance(a, Pause)]
        assert pauses and pauses[0].seconds == pytest.approx(1.1)

    async def test_character_delay_is_not_added_on_top_of_real_latency(self) -> None:
        # The caller has already had their silence. Adding another second on top is how
        # a slow turn becomes a dead line.
        clock = FakeClock()
        brain = ScriptedBrain("Hello dear.", clock=clock, seconds_per_sentence=2.0)
        conversation, _, _ = build(brain=brain, clock=clock, character_delay_ms=1100)
        await conversation.open()

        actions = await drain(conversation, "Hello?")
        assert not [a for a in actions if isinstance(a, Pause)]

    async def test_a_filler_plays_before_the_model_is_consulted(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("Hello dear."))
        await conversation.open()

        actions = await drain(conversation, "Hello?")
        assert isinstance(actions[0], PlayClip)
        assert actions[0].kind == "filler"


class TestGenerationFailuresNeverBecomeSilence:
    async def test_an_api_error_becomes_a_stalling_line(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain(raises=RuntimeError("502")))
        await conversation.open()

        said = spoken(await drain(conversation, "Hello?"))
        assert said and said[0] in FUMBLE_LINES
        assert not conversation.ended

    async def test_a_hung_stream_times_out_into_a_stalling_line(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain(hang=True))
        conversation.generation_timeout_seconds = 0.05
        await conversation.open()

        said = spoken(await drain(conversation, "Hello?"))
        assert said and said[0] in FUMBLE_LINES

    async def test_the_failure_is_recorded_on_the_turn(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain(raises=RuntimeError("502")))
        await conversation.open()
        await drain(conversation, "Hello?")

        turns = [e for e in sink.events if e.type == "agent_turn"]
        assert turns[-1].payload["failure"] == "error"

    async def test_an_empty_reply_still_produces_speech(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain())
        await conversation.open()
        assert spoken(await drain(conversation, "Hello?"))


class TestTimersFireWhenNobodyIsTalking:
    async def test_an_idle_tick_says_nothing(self) -> None:
        # A timer must never make the persona start a turn on its own.
        conversation, clock, _ = build(brain=ScriptedBrain("should not be spoken"))
        await conversation.open()
        clock.advance(5)

        assert [a async for a in conversation.tick()] == []

    async def test_the_hard_cap_ends_the_call(self) -> None:
        # G-14, enforced by the clock rather than trusted to the model.
        conversation, clock, _ = build(hard_cap_seconds=100.0)
        await conversation.open()
        clock.advance(101)

        actions = [a async for a in conversation.tick()]
        assert any(isinstance(a, HangUp) for a in actions)
        assert conversation.end_reason is EndReason.MAX_DURATION

    async def test_dead_air_ends_the_call(self) -> None:
        conversation, clock, _ = build(dead_air_seconds=60.0)
        await conversation.open()
        clock.advance(61)

        actions = [a async for a in conversation.tick()]
        assert any(isinstance(a, HangUp) for a in actions)
        assert conversation.end_reason is EndReason.DEAD_AIR

    async def test_a_hold_is_not_dead_air(self) -> None:
        # G-16 is about an *empty* line. The persona deliberately puts the phone down for
        # up to ninety seconds — longer than the sixty-second window — and a scammer
        # waiting through it is the best stalling tactic in the playbook working.
        #
        # This deliberately does NOT call note_agent_audio_finished() by hand. An earlier
        # version of this test did, and it passed against an implementation that hung up
        # on itself at the sixty-second mark of every long hold: nothing in the transport
        # marks agent audio *during* a hold, because the hold is a `Pause` the transport
        # is still sleeping through. The tick loop below is what production actually does.
        conversation, clock, _ = build(
            brain=ScriptedBrain("Oh, hold on dear, let me find my glasses."),
            dead_air_seconds=60.0,
            hold_probability=1.0,
            hold_seconds=75,
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()

        actions = await drain(conversation, "Can you read me the card number?")
        holds = [a.seconds for a in actions if isinstance(a, Pause)]
        assert holds == [75.0], "expected a 75s hold, longer than the dead-air window"

        for _ in range(75):
            clock.advance(1.0)
            assert [a async for a in conversation.tick()] == [], (
                f"hung up on itself {clock.now % 100:.0f}s into its own hold"
            )
        assert not conversation.ended

    async def test_dead_air_still_fires_once_a_hold_is_over(self) -> None:
        # The fix must not disable the guardrail it was protecting.
        conversation, clock, _ = build(
            brain=ScriptedBrain("Hold on dear."),
            dead_air_seconds=60.0,
            hold_probability=1.0,
            hold_seconds=75,
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Read me the number.")

        clock.advance(75)  # the hold plays out
        clock.advance(61)  # and then the caller says nothing at all
        actions = [a async for a in conversation.tick()]
        assert any(isinstance(a, HangUp) for a in actions)
        assert conversation.end_reason is EndReason.DEAD_AIR

    async def test_ticking_after_the_call_ended_does_nothing(self) -> None:
        conversation, clock, _ = build(hard_cap_seconds=100.0)
        await conversation.open()
        clock.advance(101)
        assert [a async for a in conversation.tick()]

        clock.advance(50)
        assert [a async for a in conversation.tick()] == []


class TestTheEscapeHatches:
    async def test_pressing_five_releases_the_caller(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"))
        await conversation.open()
        conversation.note_dtmf("5")

        actions = [a async for a in conversation.tick()]
        assert spoken(actions) == [DISCLOSURE_SCRIPT]

    async def test_the_safeword_releases_the_caller(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"), safeword="pineapple")
        await conversation.open()

        actions = await drain(conversation, "Marjorie, it's me — pineapple.")
        assert spoken(actions) == [DISCLOSURE_SCRIPT]

    async def test_a_caller_hanging_up_ends_the_call_silently(self) -> None:
        conversation, _, _ = build(brain=ScriptedBrain("nope"))
        await conversation.open()

        actions = await conversation.caller_hung_up()
        assert spoken(actions) == []
        assert any(isinstance(a, HangUp) for a in actions)
        assert conversation.ended

    async def test_hanging_up_twice_is_harmless(self) -> None:
        conversation, _, _ = build()
        await conversation.open()
        await conversation.caller_hung_up()
        assert await conversation.caller_hung_up() == []


class TestEvents:
    async def test_a_phase_change_is_recorded_with_its_trigger(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain("Hello dear."))
        await conversation.open()
        await drain(conversation, "Hello, who is this?")

        changes = [e for e in sink.events if e.type == "phase_changed"]
        assert changes
        assert changes[0].payload["from"] == CallPhase.GREETING.value
        assert changes[0].payload["to"] == CallPhase.ASSESSING.value
        assert changes[0].payload["trigger"] == "caller_spoke"
        # A change the caller caused carries no timer marker at all.
        assert "on_timer" not in changes[0].payload

    async def test_a_phase_change_nobody_spoke_for_is_marked_as_a_timer(self) -> None:
        # Probation expiring is the clearest case: no caller turn triggers it, and a
        # reviewer reading the log needs to see that the machine moved on its own.
        conversation, clock, sink = build(
            brain=ScriptedBrain("Oh, hello."), dead_air_seconds=3600.0
        )
        await conversation.open()
        await drain(conversation, "Hello, is this the account holder?")
        assert conversation.final_phase is CallPhase.ASSESSING

        clock.advance(120.0)
        # A tick that lands a transition still says nothing — but it must log it.
        assert [action async for action in conversation.tick()] == []

        on_timer = [
            e for e in sink.events if e.type == "phase_changed" and e.payload.get("on_timer")
        ]
        assert on_timer, "a transition landed by a tick was dropped from the log"
        assert on_timer[-1].payload["to"] == CallPhase.HOOK.value
        assert on_timer[-1].payload["trigger"] == "probation_expired"

    async def test_sequence_numbers_are_dense_and_ordered(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain("Hello dear."))
        await conversation.open()
        await drain(conversation, "Hello?")

        assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))

    async def test_a_broken_sink_does_not_break_the_call(self) -> None:
        # Observability is not worth a live call.
        conversation, _, _ = build(brain=ScriptedBrain("Hello dear."), sink=RecordingSink(fail=True))
        await conversation.open()
        assert spoken(await drain(conversation, "Hello?")) == ["Hello dear."]


class TestDryRuns:
    async def test_without_a_model_nothing_is_spoken_but_the_call_still_runs(self) -> None:
        # This is what the harness's --dry mode and the script gate depend on.
        conversation, _, _ = build(brain=None)
        await conversation.open()

        actions = await drain(conversation, "Hello, who is this?")
        assert spoken(actions) == []
        assert conversation.final_phase is CallPhase.ASSESSING


class TestTheRequestSentToTheModel:
    """The greeting is an assistant turn, and the API requires a user turn first.

    These assert on the history as the brain actually receives it, then run it through
    the real message builder — the request that would go on the wire, not a
    reconstruction of it.
    """

    @staticmethod
    def text_of(message: dict) -> str:
        """The text of a message, whether it is a bare string or cache-marked blocks."""
        content = message["content"]
        if isinstance(content, str):
            return content
        return "".join(block["text"] for block in content)

    async def sent_messages(self, note: str | None = "[call state] stall") -> list[dict]:
        from ssscammers.agent.llm import ClaudeBrain

        brain = ScriptedBrain("Oh, hello dear.")
        conversation, _, _ = build(brain=brain)
        await conversation.open()
        await drain(conversation, "Hello, is this the account holder?")

        assert brain.histories, "the model was never consulted"
        return ClaudeBrain._build_messages(brain.histories[0], note)

    async def test_the_first_message_is_never_the_greeting(self) -> None:
        # `open()` records "Hello?" before the caller has said anything, so a call's
        # transcript starts assistant-first. Sent as-is that is a 400 on every request,
        # and `_generate`'s fail-soft path turns each one into a fumble line — a whole
        # call of "hold on, I've lost my place" with nothing but a log to show why.
        messages = await self.sent_messages()
        assert messages[0]["role"] == "user"

    async def test_the_state_note_rides_in_the_last_caller_turn(self) -> None:
        # Sonnet 5 has no mid-conversation `role: "system"` message — sending one is a
        # 400 on every turn that carries a note, which is every turn after the caller
        # first speaks. The note goes inside the newest user turn instead.
        messages = await self.sent_messages()
        assert messages[-1]["role"] == "user"
        assert "[call state] stall" in self.text_of(messages[-1])
        assert "<system-reminder>" in self.text_of(messages[-1])

    async def test_no_message_uses_the_system_role(self) -> None:
        # The role this model rejects must not appear anywhere in the request.
        messages = await self.sent_messages()
        assert all(m["role"] in ("user", "assistant") for m in messages)

    async def test_the_caller_turn_is_still_intact_alongside_the_note(self) -> None:
        # Folding the note in must not overwrite what the caller actually said — that
        # text is the only thing the model has to respond to.
        messages = await self.sent_messages()
        assert "Hello, is this the account holder?" in self.text_of(messages[-1])

    async def test_a_turn_with_no_note_is_left_alone(self) -> None:
        messages = await self.sent_messages(note=None)
        assert "<system-reminder>" not in self.text_of(messages[-1])
        assert self.text_of(messages[-1]) == "Hello, is this the account holder?"

    @staticmethod
    def request_kwargs() -> dict:
        from ssscammers.agent.llm import ClaudeBrain

        brain = ClaudeBrain(system_prompt="you are a persona", api_key="not-a-real-key")
        return brain._request_kwargs([Turn("user", "hi")], None)

    async def test_the_model_is_the_one_the_request_shape_was_built_for(self) -> None:
        from ssscammers.agent.llm import MODEL

        assert self.request_kwargs()["model"] == MODEL == "claude-sonnet-5"

    async def test_thinking_is_switched_off_explicitly(self) -> None:
        # Omitting `thinking` on Sonnet 5 runs *adaptive*, and adaptive shares the 400
        # token `max_tokens` ceiling with the spoken reply while streaming nothing (
        # `thinking.display` defaults to "omitted"). Left implicit, a thinking burst is a
        # truncated sentence preceded by dead air on a live line — G-16.
        assert self.request_kwargs()["thinking"] == {"type": "disabled"}

    async def test_effort_is_pinned_low_rather_than_left_to_default(self) -> None:
        # Unset, `effort` defaults to `high` on Sonnet 5: more latency and more tokens
        # for a task that is rambling in character, not reasoning.
        assert self.request_kwargs()["output_config"] == {"effort": "low"}

    async def test_the_request_is_a_shape_the_sdk_accepts(self) -> None:
        # The contract check, and the only one here that can catch a mistake nobody
        # thought to enumerate. A hand-written denylist of forbidden names ("speed",
        # "betas", "temperature", ...) passes any typo not on the list: `display` at top
        # level instead of inside `thinking` is the plausible one, and it raises TypeError
        # on the first turn, which `_generate`'s bare `except Exception` converts into a
        # fumble line for every turn of the whole call. Binding against the real signature
        # covers the entire class instead of five names.
        import inspect

        from ssscammers.agent.llm import ClaudeBrain

        brain = ClaudeBrain(system_prompt="you are a persona", api_key="not-a-real-key")
        accepted = set(inspect.signature(brain._client.messages.stream).parameters)
        unknown = set(brain._request_kwargs([Turn("user", "hi")], None)) - accepted
        assert not unknown, f"the SDK would reject: {sorted(unknown)}"

    async def test_no_sampling_parameter_is_set(self) -> None:
        # Deliberately a denylist, and NOT covered by the signature check above: the SDK
        # accepts `temperature`/`top_p`/`top_k` on every model, and Sonnet 5 is what
        # rejects a non-default value with a 400. A model-level constraint is invisible to
        # `inspect.signature`, so this is the only place it can be caught before a call.
        kwargs = self.request_kwargs()
        for banned in ("temperature", "top_p", "top_k"):
            assert banned not in kwargs, (
                f"{banned} is rejected by the model at any non-default value; steer with "
                "the persona prompt instead"
            )

    async def test_the_breakpoint_marks_the_caller_text_and_not_the_state_note(self) -> None:
        # Placement is the whole game. The note is rebuilt every turn and only ever rides
        # the newest turn, so a breakpoint *after* it stores a prefix no later request
        # reproduces: nothing is ever read and every turn still pays the 1.25x cache-write
        # premium for a guaranteed miss — strictly worse than not caching at all.
        messages = await self.sent_messages()
        blocks = messages[-1]["content"]
        assert isinstance(blocks, list), "the newest turn must be block-shaped to be marked"
        assert "cache_control" in blocks[0], "the stable caller text must carry the marker"
        assert "<system-reminder>" not in blocks[0]["text"], "the marked block must be stable"
        assert "<system-reminder>" in blocks[-1]["text"]
        assert "cache_control" not in blocks[-1], "the volatile note must not be marked"

    async def test_the_cached_prefix_survives_into_the_next_turn(self) -> None:
        # The property that decides whether the breakpoint pays for itself, and the one
        # the first version of this change got wrong. Everything up to and including the
        # marked block must reappear verbatim in the next turn's request, or the cache
        # entry written here is dead on arrival.
        from ssscammers.agent.llm import ClaudeBrain

        def cached_prefix(messages: list[dict]) -> list[str]:
            texts: list[str] = []
            for message in messages:
                content = message["content"]
                if isinstance(content, str):
                    texts.append(content)
                    continue
                for block in content:
                    texts.append(block["text"])
                    if "cache_control" in block:
                        return texts
            return texts

        def every_text(messages: list[dict]) -> list[str]:
            texts: list[str] = []
            for message in messages:
                content = message["content"]
                texts.extend(
                    [content] if isinstance(content, str) else [b["text"] for b in content]
                )
            return texts

        history = [Turn("assistant", NEUTRAL_GREETING), Turn("user", "Is this Marjorie?")]
        first = ClaudeBrain._build_messages(history, "[call state] assessing")

        history = history + [Turn("assistant", "Who's this?"), Turn("user", "Your bank.")]
        second = ClaudeBrain._build_messages(history, "[call state] hook, tactic=mishear")

        prefix = cached_prefix(first)
        assert prefix, "turn one cached nothing"
        assert every_text(second)[: len(prefix)] == prefix, (
            "turn one's cached prefix is not a prefix of turn two's request, so the entry "
            "it wrote can never be read"
        )

    async def test_only_the_newest_turn_is_marked(self) -> None:
        # A breakpoint on every turn would burn the four-breakpoint budget and write a
        # separate entry per turn. Older turns are read via the prefix, not re-marked.
        from ssscammers.agent.llm import ClaudeBrain

        history = [Turn("user", "one"), Turn("assistant", "two"), Turn("user", "three")]
        messages = ClaudeBrain._build_messages(history, None)
        marked = [i for i, m in enumerate(messages) if isinstance(m["content"], list)]
        assert marked == [len(messages) - 1]

    async def test_a_truncated_reply_is_labelled_as_one(self) -> None:
        # On truncation the stream just ends: text arrives, nothing raises, and the
        # residual buffer is flushed as a final chunk with no sentence terminator. Without
        # a label the event log cannot be told apart from a clean turn.
        brain = ScriptedBrain("Oh, hello dear, I was just")
        brain.last_stop_reason = "max_tokens"
        conversation, _, events = build(brain=brain)
        await conversation.open()
        await drain(conversation, "Is this the account holder?")

        turns = [e for e in events.events if e.type == "agent_turn" and not e.payload["scripted"]]
        assert turns and turns[-1].payload["failure"] == "truncated"

    async def test_a_clean_reply_is_not_labelled_truncated(self) -> None:
        conversation, _, events = build(brain=ScriptedBrain("Oh, hello dear."))
        await conversation.open()
        await drain(conversation, "Is this the account holder?")

        turns = [e for e in events.events if e.type == "agent_turn" and not e.payload["scripted"]]
        assert turns and turns[-1].payload["failure"] is None

    async def test_a_trailing_assistant_turn_is_not_sent_as_a_prefill(self) -> None:
        # An assistant turn in last position is an assistant prefill, which Sonnet 5
        # rejects with a 400 where Haiku 4.5 accepted it. Unreachable from the transport
        # today, but the drop keeps a planning slip from costing the rest of the call.
        from ssscammers.agent.llm import ClaudeBrain

        history = [
            Turn("assistant", NEUTRAL_GREETING),
            Turn("user", "Is this the account holder?"),
            Turn("assistant", "Oh, hello dear."),
        ]
        messages = ClaudeBrain._build_messages(history, "[call state] stall")
        assert [m["role"] for m in messages] == ["user"]
        assert "<system-reminder>" in self.text_of(messages[-1])

    async def test_several_trailing_assistant_turns_all_go(self) -> None:
        # The loop, not just its first iteration: dropping only one still leaves a prefix
        # ending in an assistant turn, which is the same 400.
        from ssscammers.agent.llm import ClaudeBrain

        history = [
            Turn("assistant", NEUTRAL_GREETING),
            Turn("user", "Is this the account holder?"),
            Turn("assistant", "Oh, hello dear."),
            Turn("assistant", "Sorry, who is this?"),
            Turn("assistant", "Hello?"),
        ]
        assert [m["role"] for m in ClaudeBrain._build_messages(history, None)] == ["user"]

    async def test_the_trailing_drop_says_so_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The drop is defence for a guard moving upstream, so the log line is the only
        # way anyone would find out it fired. An unasserted message can rot into a
        # TypeError at the moment it is finally needed.
        from ssscammers.agent.llm import ClaudeBrain

        history = [Turn("user", "hello?"), Turn("assistant", "Oh, hello dear.")]
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.llm"):
            ClaudeBrain._build_messages(history, None)
        assert "prefill" in caplog.text
        assert "dropped 1 trailing" in caplog.text

    async def test_the_trailing_drop_works_without_a_state_note(self) -> None:
        from ssscammers.agent.llm import ClaudeBrain

        history = [Turn("user", "hello?"), Turn("assistant", "Oh, hello dear.")]
        messages = ClaudeBrain._build_messages(history, None)
        assert [m["role"] for m in messages] == ["user"]
        assert self.text_of(messages[-1]) == "hello?"

    async def test_the_greeting_is_still_in_the_transcript(self) -> None:
        # Dropped from the request, not from the record: the event log and the dashboard
        # transcript must still show that the persona answered.
        conversation, _, _ = build(brain=ScriptedBrain("Oh, hello dear."))
        await conversation.open()
        assert conversation.history[0].content == NEUTRAL_GREETING

    async def test_a_greeting_only_history_produces_no_sendable_request(self) -> None:
        from ssscammers.agent.llm import ClaudeBrain

        conversation, _, _ = build()
        await conversation.open()
        assert ClaudeBrain._build_messages(conversation.history, None) == []


class TestTheOwnersRealNumberIsUnspeakable:
    """`OWNER_REAL_NUMBER` was loaded from the environment and read by nothing.

    Its config section promises "the filter blocks them, never speaks them", so an operator
    setting it would reasonably believe their own number could not be said aloud. It could,
    unless they also duplicated it into OWNER_PII_DENYLIST.
    """

    def test_it_is_merged_into_the_filters_denylist(self) -> None:
        from ssscammers.agent.conversation import _owner_pii
        from ssscammers.shared.config import Settings

        settings = Settings(
            owner_pii_denylist=("Norbert",), owner_real_number="+19375550199"
        )
        assert set(_owner_pii(settings)) == {"Norbert", "+19375550199"}

    def test_an_unset_number_adds_nothing(self) -> None:
        from ssscammers.agent.conversation import _owner_pii
        from ssscammers.shared.config import Settings

        assert _owner_pii(Settings(owner_pii_denylist=("Norbert",))) == ("Norbert",)


class TestSeedAndDrawLogging:
    """Roadmap Phase 2, move one: production calls are seeded and every consequential
    draw's outcome lands in the event stream — the precondition for byte-identical
    replay, established before anything durable is recorded."""

    async def test_build_conversation_records_its_seed_in_call_opened(self) -> None:
        from ssscammers.agent.conversation import build_conversation
        from ssscammers.shared.config import Settings

        sink = RecordingSink()
        conversation = build_conversation(
            Settings(), persona_id="marjorie", events=sink, clock=FakeClock(), seed=1234
        )
        await conversation.open()
        assert conversation.seed == 1234
        assert sink.events[0].type == "call_opened"
        assert sink.events[0].payload["seed"] == 1234

    async def test_an_unseeded_build_draws_entropy_and_still_records_it(self) -> None:
        from ssscammers.agent.conversation import build_conversation
        from ssscammers.shared.config import Settings

        first = build_conversation(
            Settings(), persona_id="marjorie", events=(sink := RecordingSink())
        )
        second = build_conversation(Settings(), persona_id="marjorie")
        assert first.seed is not None and second.seed is not None
        # 64-bit OS entropy: equal seeds here mean it is not entropy at all.
        assert first.seed != second.seed
        await first.open()
        assert sink.events[0].payload["seed"] == first.seed

    async def test_the_same_seed_reproduces_the_call_draw_for_draw(self) -> None:
        from ssscammers.agent.conversation import build_conversation
        from ssscammers.shared.config import Settings

        async def run() -> tuple[list[Action], list[CallEvent]]:
            sink = RecordingSink()
            conversation = build_conversation(
                Settings(), persona_id="marjorie", events=sink, clock=FakeClock(), seed=6
            )
            conversation.director.state.phase = CallPhase.STALL
            await conversation.open()
            actions: list[Action] = []
            for line in ("Read me the card number.", "The code, please.", "Now."):
                actions.extend(await drain(conversation, line))
            return actions, sink.events

        first_actions, first_events = await run()
        second_actions, second_events = await run()
        assert first_actions == second_actions
        assert first_events == second_events

        # Seed 6 is chosen because it reaches the hold sites (seed 99, the first
        # draft's pick, never drew a hold — a determinism test that skips a draw
        # site cannot catch that site escaping the seeded stream). The exact values
        # are pinned deliberately: run-vs-run equality can catch an unseeded draw
        # only at a seed that reaches it, and can never catch a seeded-draw
        # *reorder* (both runs reorder identically) — only pins go red on that.
        # These pins also make cross-version rng stability a CI-checked fact on
        # the 3.11/3.13 matrix. If a pin breaks, the seeded stream changed: that
        # is a deliberate contract change to record, not a flake to paper over.
        assert first_actions == [
            PlayClip(clip="cough_soft.wav", kind="filler"),
            PlayClip(clip="kettle_boiling.wav", kind="hold"),
            Pause(seconds=20.0),
            PlayClip(clip="oh_let_me_see.wav", kind="filler"),
            PlayClip(clip="sorry_dear.wav", kind="filler"),
            PlayClip(clip="rummaging_drawer.wav", kind="hold"),
            Pause(seconds=72.0),
        ]
        holds = [dict(e.payload) for e in first_events if e.type == "hold"]
        assert holds == [
            {"seconds": 20, "clip": "kettle_boiling.wav"},
            {"seconds": 72, "clip": "rummaging_drawer.wav"},
        ]

    async def test_the_same_seed_and_replies_reproduce_a_wet_call(self) -> None:
        # The replay contract's fourth input: the model's replies. The fumble draw
        # fires only on an empty stream and the filter's replacement draw only on a
        # blocked sentence, so this leg drives both model-conditioned draw sites —
        # unreachable in dry mode, where _generate returns before either — with a
        # fixed reply stream, exactly what the ReplayBrain seam will do.
        from ssscammers.agent.conversation import build_conversation
        from ssscammers.shared.config import Settings

        card = f"{CARD_FIRST_HALF.rstrip('.')} {CARD_SECOND_HALF}"

        async def run() -> tuple[list[Action], list[CallEvent]]:
            sink = RecordingSink()
            conversation = build_conversation(
                Settings(),
                persona_id="marjorie",
                brain=TurnScriptedBrain([card], []),  # type: ignore[arg-type]
                events=sink,
                clock=FakeClock(),
                seed=6,
            )
            conversation.director.state.phase = CallPhase.STALL
            await conversation.open()
            actions: list[Action] = []
            for line in ("Read me the card number.", "Go on."):
                actions.extend(await drain(conversation, line))
            return actions, sink.events

        first_actions, first_events = await run()
        second_actions, second_events = await run()
        assert first_actions == second_actions
        assert first_events == second_events

        types = [e.type for e in first_events]
        assert "output_blocked" in types, "the card sentence must trip the filter draw"
        fumbles = [
            e.payload["fumbled"]
            for e in first_events
            if e.type == "agent_turn" and not e.payload["scripted"]
        ]
        assert fumbles == [False, True], "turn one is filtered text, turn two fumbles"

    async def test_agent_turn_records_the_drawn_values(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain(), character_delay_ms=350)
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Read me the number.")

        turns = [e for e in sink.events if e.type == "agent_turn" and not e.payload["scripted"]]
        assert turns, "a model turn should have been logged"
        payload = turns[-1].payload
        # An empty stream fumbles, and the fumble is a recorded draw.
        assert payload["fumbled"] is True
        assert payload["text"] in FUMBLE_LINES
        assert payload["character_delay_ms"] == 350
        assert payload["filler"] in conversation.director.persona.fillers

    async def test_a_clean_model_turn_records_fumbled_false(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain("Oh, hello dear."))
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Read me the number.")

        payload = [e for e in sink.events if e.type == "agent_turn" and not e.payload["scripted"]][-1].payload
        assert payload["fumbled"] is False

    async def test_a_hold_records_its_clip_pick(self) -> None:
        conversation, _, sink = build(
            brain=ScriptedBrain("Hold on dear."), hold_probability=1.0, hold_seconds=40
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        actions = await drain(conversation, "Read me the number.")

        hold = [e for e in sink.events if e.type == "hold"][-1]
        assert hold.payload["seconds"] == 40
        clip = hold.payload["clip"]
        assert clip in conversation.director.persona.holds
        played = [a.clip for a in actions if isinstance(a, PlayClip) and a.kind == "hold"]
        assert played == [clip]


class TestPayloadWidening:
    """Roadmap Phase 2, move two (first family): the event log explains itself.

    caller_turn carries the verdict the utterance produced and its evidence;
    agent_turn carries what the caller actually waited through. Payload assertions
    here are the deliberate contract updates the roadmap schedules.
    """

    async def test_caller_turn_carries_the_verdict_and_its_evidence(self) -> None:
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "This is the fraud department of your bank.")

        turn = [e for e in sink.events if e.type == "caller_turn"][-1]
        assert turn.payload["triage"] == "scam"
        assert turn.payload["triage_confidence"] > 0
        assert "scam_type" in turn.payload
        signals = turn.payload["signals"]
        assert signals, "a fraud-department opener must leave evidence"
        assert all({"pattern", "weight", "toward"} <= set(s) for s in signals)
        assert any(s["toward"] == "scam" for s in signals)

    async def test_the_verdict_is_this_turns_not_the_previous_ones(self) -> None:
        # The emit was moved after the director advances precisely so the event
        # reflects the utterance it carries; a stale verdict here would make the
        # log lie about why the call moved.
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "Do not hang up.")
        await drain(conversation, "There is a warrant for your arrest, pay today.")

        first, second = [e.payload for e in sink.events if e.type == "caller_turn"]
        assert first["triage"] == "unclear"
        assert second["triage"] == "scam"

    async def test_signals_accumulate_and_a_repeat_raises_count_not_size(self) -> None:
        # The self-contained-event contract, pinned as containment rather than
        # length: turn one's evidence must still be present on turn two (a
        # per-turn-slicing mutant passes any length comparison), and a repeated
        # phrase must dedupe into a count — the caller controls how often a
        # phrase repeats, never how much evidence the log stores.
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "Do not hang up.")
        await drain(conversation, "Do not hang up. There is a warrant for your arrest.")

        first, second = [e.payload for e in sink.events if e.type == "caller_turn"]
        first_patterns = {s["pattern"] for s in first["signals"]}
        assert first_patterns, "turn one must leave evidence"
        assert first_patterns <= {s["pattern"] for s in second["signals"]}
        repeated = [s for s in second["signals"] if s["pattern"] in first_patterns]
        assert any(s["count"] == 2 for s in repeated), "a repeat must raise count"
        assert all(s["count"] == 1 for s in first["signals"])

    async def test_an_emergency_leaves_evidence_in_the_event_log(self) -> None:
        # The most consequential classification must be explicable from the
        # caller_turn event alone — an emergency exit whose event shows
        # "unclear, no signals" is a log that cannot explain the call.
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "There's a fire, please call 911 for me!")

        payload = [e for e in sink.events if e.type == "caller_turn"][-1].payload
        assert payload["emergency"] is True
        assert any(s["toward"] == "emergency" for s in payload["signals"])
        assert conversation.final_phase is CallPhase.EMERGENCY_EXIT

    async def test_the_event_order_of_a_turn_is_pinned(self) -> None:
        # The reorder's contract, as a red-able test rather than a comment:
        # caller_turn precedes everything _execute emits. Run-vs-run equality
        # cannot catch a reorder — both runs reorder identically.
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "Sorry, I think I've got the wrong number.")
        assert sink.types() == ["call_opened", "caller_turn", "phase_changed", "agent_turn", "call_ended"]

    async def test_a_crashing_planner_still_logs_the_caller_turn(self) -> None:
        # The input that crashes the planner is the most forensically valuable
        # one. The marker is explicit: absent triage keys would be ambiguous
        # between a crash, a verdict-free plan, and a serializer bug.
        conversation, _, sink = build()
        await conversation.open()

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("director bug")

        conversation.director.handle_caller_turn = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="director bug"):
            await drain(conversation, "This crashes the planner.")

        payload = [e for e in sink.events if e.type == "caller_turn"][-1].payload
        assert payload["text"] == "This crashes the planner."
        assert payload["planning_failed"] is True
        assert "triage" not in payload

    async def test_a_scripted_turn_carries_no_measured_fields(self) -> None:
        # The asymmetry is deliberate — no model, no latency to measure — and
        # pinned so a regression cannot quietly pollute scripted turns with
        # meaningless zeros.
        conversation, _, sink = build()
        await conversation.open()
        await drain(conversation, "Sorry, I think I've got the wrong number.")

        scripted = [e for e in sink.events if e.type == "agent_turn"][-1].payload
        assert set(scripted) == {"text", "scripted"}

    async def test_agent_turn_measures_what_the_caller_waited_through(self) -> None:
        clock = FakeClock()
        conversation, _, sink = build(
            brain=ScriptedBrain(
                "One sentence.", "Two sentences.", clock=clock, seconds_per_sentence=2.0
            ),
            clock=clock,
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Read me the number.")

        payload = [
            e for e in sink.events if e.type == "agent_turn" and not e.payload["scripted"]
        ][-1].payload
        assert payload["first_sentence_ms"] == 2000
        assert payload["stream_ms"] == 4000
        assert payload["character_pause_ms"] == 0  # the model spent far more than any delay

    async def test_a_fast_model_still_pays_the_character_delay(self) -> None:
        conversation, _, sink = build(
            brain=ScriptedBrain("Oh, hello dear."), character_delay_ms=350
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        actions = await drain(conversation, "Read me the number.")

        payload = [
            e for e in sink.events if e.type == "agent_turn" and not e.payload["scripted"]
        ][-1].payload
        assert payload["first_sentence_ms"] == 0
        assert payload["character_pause_ms"] == 350
        # The first pause is the character delay; the seeded tactic draw is free to
        # add a hold pause after it.
        pauses = [a.seconds for a in actions if isinstance(a, Pause)]
        assert pauses[0] == 0.35

    async def test_a_turn_with_no_sentence_records_none_for_first_sentence(self) -> None:
        conversation, _, sink = build(brain=ScriptedBrain())
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        await drain(conversation, "Read me the number.")

        payload = [
            e for e in sink.events if e.type == "agent_turn" and not e.payload["scripted"]
        ][-1].payload
        assert payload["first_sentence_ms"] is None
        assert payload["fumbled"] is True

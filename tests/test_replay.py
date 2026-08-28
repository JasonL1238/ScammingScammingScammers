"""Replaying a recorded call: the model made deterministic, at both depths.

`ReplayBrain` is the fast seam a corpus is driven through. The recorded client
goes deeper, plugging into a real `ClaudeBrain` so *that* module's request
construction runs — the part `--dry` never executes and which fails invisibly
when it breaks, because `_generate` catches everything and speaks a fumble.

The tests that matter most here are the divergence ones. A replay that quietly
yields a stale reply when the call takes a different path is worse than no
replay at all: it turns a diverged run green.
"""

from __future__ import annotations

import pytest
from helpers import RecordingSink, SimulatedClock, make_director

from ssscammers.agent.conversation import CallEvent, Conversation, build_conversation
from ssscammers.agent.llm import ClaudeBrain, Turn
from ssscammers.agent.persona import load_persona
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import CallPhase
from ssscammers.shared.fiction import PACK_VERSION
from ssscammers.simscammer.replay import (
    CallRecording,
    DivergedError,
    RecordedAnthropicClient,
    RecordedTurn,
    ReplayBrain,
    describe_request,
)


def recording(*turns: RecordedTurn, **kwargs: object) -> CallRecording:
    return CallRecording(turns=turns, **kwargs)  # type: ignore[arg-type]


async def drain(brain: ReplayBrain, history: list[Turn], note: str | None = None) -> list[str]:
    return [s async for s in brain.stream_reply(history, note)]


CALLER = [Turn("user", "Hello, is this the account holder?")]


class TestReplayBrain:
    async def test_it_replays_the_recorded_reply(self) -> None:
        brain = ReplayBrain(recording(RecordedTurn(("Oh, hello dear. ", "Who is this?"))))
        assert await drain(brain, CALLER) == ["Oh, hello dear.", "Who is this?"]

    async def test_the_splitter_runs_on_replay_not_the_recording(self) -> None:
        # The deltas are raw API chunks, split mid-sentence on purpose: if the
        # recording held finished sentences, a boundary bug would survive its
        # own recording. Two deltas, one sentence.
        brain = ReplayBrain(recording(RecordedTurn(("Oh, hel", "lo dear."))))
        assert await drain(brain, CALLER) == ["Oh, hello dear."]

    async def test_the_stop_reason_is_replayed_per_turn(self) -> None:
        brain = ReplayBrain(
            recording(
                RecordedTurn(("Fine.",), stop_reason="end_turn"),
                RecordedTurn(("Cut off mid",), stop_reason="max_tokens"),
            )
        )
        await drain(brain, CALLER)
        assert brain.last_stop_reason == "end_turn"
        await drain(brain, CALLER)
        assert brain.last_stop_reason == "max_tokens"

    async def test_running_past_the_recording_is_an_error(self) -> None:
        brain = ReplayBrain(recording(RecordedTurn(("Only one turn.",))))
        await drain(brain, CALLER)
        with pytest.raises(DivergedError, match="model turn 2"):
            await drain(brain, CALLER)

    async def test_different_steering_is_an_error(self) -> None:
        brain = ReplayBrain(
            recording(RecordedTurn(("Hello.",), state_note="[call state] Phase: stall."))
        )
        with pytest.raises(DivergedError, match="steering differs"):
            await drain(brain, CALLER, "[call state] Phase: hook.")

    async def test_a_different_transcript_length_is_an_error(self) -> None:
        brain = ReplayBrain(recording(RecordedTurn(("Hello.",), caller_turns=3)))
        with pytest.raises(DivergedError, match="caller turn"):
            await drain(brain, CALLER)

    async def test_a_stale_stop_reason_cannot_survive_a_divergence(self) -> None:
        brain = ReplayBrain(
            recording(RecordedTurn(("Cut off",), stop_reason="max_tokens"))
        )
        await drain(brain, CALLER)
        assert brain.last_stop_reason == "max_tokens"
        with pytest.raises(DivergedError):
            await drain(brain, CALLER)
        assert brain.last_stop_reason is None

    async def test_completeness_distinguishes_a_short_replay(self) -> None:
        brain = ReplayBrain(recording(RecordedTurn(("One.",)), RecordedTurn(("Two.",))))
        await drain(brain, CALLER)
        assert not brain.complete, "a replay that ends early must be detectable"
        await drain(brain, CALLER)
        assert brain.complete

    async def test_a_diverged_replay_is_never_complete(self) -> None:
        # The property a runner asserts. Consuming every recorded turn is not
        # enough: `_generate` swallows exceptions into a fumble line, so a
        # replay can diverge, be caught, and still reach the end.
        brain = ReplayBrain(recording(RecordedTurn(("One.",))))
        await drain(brain, CALLER)
        assert brain.complete
        with pytest.raises(DivergedError):
            await drain(brain, CALLER)
        assert not brain.complete, "an overrun must not report complete"

    async def test_a_failed_steering_check_is_never_complete(self) -> None:
        brain = ReplayBrain(recording(RecordedTurn(("Hi.",), state_note="recorded")))
        with pytest.raises(DivergedError):
            await drain(brain, CALLER, "different")
        assert not brain.complete

    async def test_it_reports_the_recorded_request_construction(self) -> None:
        # `call_opened` reads these off the brain; without them the very first
        # event of every replay differs from the recorded run.
        brain = ReplayBrain(
            recording(
                RecordedTurn(("hi.",)),
                model="claude-sonnet-5",
                effort="low",
                max_tokens=400,
            )
        )
        assert (brain.model, brain.effort, brain.max_tokens) == ("claude-sonnet-5", "low", 400)

    async def test_it_honours_the_no_addressable_turn_guard(self) -> None:
        # The real brain returns without touching the wire when there is no
        # caller turn to answer. A replay that consumed a turn here would be
        # permanently off-by-one against its own recording.
        brain = ReplayBrain(recording(RecordedTurn(("Never spoken.",))))
        assert await drain(brain, []) == []
        assert await drain(brain, [Turn("assistant", "Hello?", scripted=True)]) == []
        assert brain.index == 0

    async def test_an_unrecorded_field_disables_only_its_own_check(self) -> None:
        # UNRECORDED must be distinguishable from a recorded None, or a fixture
        # that omits a field silently switches off the guard it drives.
        lax = ReplayBrain(recording(RecordedTurn(("Hi.",))))
        assert await drain(lax, CALLER, "any steering at all") == ["Hi."]

        pinned = ReplayBrain(recording(RecordedTurn(("Hi.",), state_note=None)))
        with pytest.raises(DivergedError, match="steering differs"):
            await drain(pinned, CALLER, "any steering at all")


class TestTheRecordingFormat:
    def test_it_round_trips_through_json(self) -> None:
        original = recording(
            RecordedTurn(("a ", "b."), stop_reason="max_tokens", state_note="note", caller_turns=2),
            persona_id="marjorie",
            seed=7,
            model="claude-sonnet-5",
            effort="low",
            max_tokens=400,
        )
        assert CallRecording.from_json(original.to_json()) == original

    def test_it_round_trips_through_a_file(self, tmp_path) -> None:  # noqa: ANN001
        original = recording(RecordedTurn(("hello.",)), persona_id="dot", seed=3)
        assert CallRecording.read(original.write(tmp_path / "call.json")) == original

    def test_a_regenerated_fiction_pack_refuses_the_replay(self) -> None:
        # The pack is generated from a seeded rng: regenerate it and every fact
        # the persona speaks changes. That is a refusal, not a diff to explain.
        stale = recording(RecordedTurn(("hi.",)), pack_version="v0")
        with pytest.raises(DivergedError, match="fiction pack"):
            stale.check_environment()

    def test_a_different_persona_or_seed_refuses_the_replay(self) -> None:
        rec = recording(RecordedTurn(("hi.",)), persona_id="marjorie", seed=7)
        rec.check_environment(persona_id="marjorie", seed=7)  # the matching case
        with pytest.raises(DivergedError, match="persona"):
            rec.check_environment(persona_id="harold")
        with pytest.raises(DivergedError, match="seed"):
            rec.check_environment(seed=8)

    def test_the_current_pack_version_is_the_default(self) -> None:
        assert recording(RecordedTurn(("hi.",))).pack_version == PACK_VERSION


class TestTheRecordedClientDrivesTheRealRequestConstruction:
    """The depth that matters: `--dry` never runs any of this.

    A malformed request fails invisibly in production — `_generate` catches
    everything and speaks a stalling line — so the request surface is only ever
    checked here or on a live call.
    """

    def brain(self, rec: CallRecording) -> tuple[ClaudeBrain, RecordedAnthropicClient]:
        client = RecordedAnthropicClient(rec)
        return ClaudeBrain(system_prompt="SYSTEM PROMPT", client=client), client

    async def test_it_streams_the_recorded_reply_through_the_real_brain(self) -> None:
        brain, _ = self.brain(recording(RecordedTurn(("Oh, hello. ", "Who's this?"))))
        assert [s async for s in brain.stream_reply(CALLER)] == ["Oh, hello.", "Who's this?"]

    async def test_the_request_carries_the_cached_system_block(self) -> None:
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        [s async for s in brain.stream_reply(CALLER)]

        system = client.requests[0]["system"]
        assert system[0]["text"] == "SYSTEM PROMPT"
        assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    async def test_the_request_pins_thinking_and_effort_explicitly(self) -> None:
        # Both fail open to the expensive default if omitted: adaptive thinking
        # is dead air on a phone line, and unset effort defaults to high.
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        [s async for s in brain.stream_reply(CALLER)]

        assert client.requests[0]["thinking"] == {"type": "disabled"}
        assert client.requests[0]["output_config"] == {"effort": brain.effort}

    async def test_the_state_note_rides_inside_the_newest_caller_turn(self) -> None:
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        [s async for s in brain.stream_reply(CALLER, "STEERING")]

        blocks = client.requests[0]["messages"][-1]["content"]
        assert blocks[0]["text"] == CALLER[0].content
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[1]["text"] == "<system-reminder>\nSTEERING\n</system-reminder>"
        assert "cache_control" not in blocks[1]

    async def test_edge_assistant_turns_are_stripped(self) -> None:
        # A call's transcript legitimately starts assistant-first, and a
        # trailing assistant turn is a prefill this model rejects.
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        history = [
            Turn("assistant", "Hello?", scripted=True),
            Turn("user", "Is this the account holder?"),
            Turn("assistant", "trailing prefill"),
        ]
        [s async for s in brain.stream_reply(history)]

        roles = [m["role"] for m in client.requests[0]["messages"]]
        assert roles == ["user"]

    async def test_the_provenance_flag_never_reaches_the_request(self) -> None:
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        [s async for s in brain.stream_reply([Turn("user", "hello", scripted=True)])]

        message = client.requests[0]["messages"][0]
        assert set(message) == {"role", "content"}

    async def test_a_truncated_reply_is_labelled(self) -> None:
        brain, _ = self.brain(
            recording(RecordedTurn(("cut off mid",), stop_reason="max_tokens"))
        )
        [s async for s in brain.stream_reply(CALLER)]
        assert brain.last_stop_reason == "max_tokens"

    async def test_an_empty_transcript_never_reaches_the_wire(self) -> None:
        # The API requires at least one message; a 400 here is indistinguishable
        # from a real failure downstream.
        brain, client = self.brain(recording(RecordedTurn(("hi.",))))
        assert [s async for s in brain.stream_reply([])] == []
        assert client.requests == []

    async def test_running_past_the_recording_is_an_error(self) -> None:
        brain, _ = self.brain(recording(RecordedTurn(("one.",))))
        [s async for s in brain.stream_reply(CALLER)]
        with pytest.raises(DivergedError, match="request 2"):
            [s async for s in brain.stream_reply(CALLER)]


class TestAReplayedCallRunsTheProductionDriver:
    """End to end: the same `Conversation` a phone call runs, model replaced."""

    def build(self, brain: object) -> tuple[Conversation, SimulatedClock]:
        clock = SimulatedClock()
        conversation = Conversation(
            director=make_director(),
            brain=brain,  # type: ignore[arg-type]
            clock=clock,
            rng=__import__("random").Random(6),
        )
        conversation.director.state.phase = CallPhase.STALL
        return conversation, clock

    async def test_two_replays_of_one_recording_are_identical(self) -> None:
        rec = recording(
            RecordedTurn(("Oh dear. ", "Let me find my glasses.")),
            RecordedTurn(("Which card was it now?",)),
        )

        async def once() -> tuple[list[object], list[object]]:
            conversation, _ = self.build(ReplayBrain(rec, strict=False))
            await conversation.open()
            actions: list[object] = []
            for line in ("Read me the card number.", "The long number, please."):
                actions.extend([a async for a in conversation.respond(line)])
            return actions, list(conversation.history)

        first_actions, first_history = await once()
        second_actions, second_history = await once()
        assert first_actions == second_actions
        assert first_history == second_history

    async def test_the_replayed_transcript_is_the_recorded_speech(self) -> None:
        rec = recording(RecordedTurn(("Oh dear. ", "Let me find my glasses.")))
        conversation, _ = self.build(ReplayBrain(rec, strict=False))
        await conversation.open()
        [a async for a in conversation.respond("Read me the card number.")]

        spoken = [t.content for t in conversation.history if t.role == "assistant" and not t.scripted]
        assert spoken == ["Oh dear. Let me find my glasses."]

    async def test_a_diverged_replay_fails_the_call_loudly(self) -> None:
        # `_generate` catches everything so a live call survives a bad turn —
        # which would quietly swallow a divergence. The runner therefore checks
        # the brain, not just the transcript.
        rec = recording(RecordedTurn(("One turn only.",)))
        brain = ReplayBrain(rec, strict=False)
        conversation, _ = self.build(brain)
        await conversation.open()
        [a async for a in conversation.respond("First.")]
        [a async for a in conversation.respond("Second.")]

        assert brain.index == 1, "the second turn found no recording"
        payloads = [t.content for t in conversation.history if t.role == "assistant"]
        assert payloads[-1] != "One turn only."


class TestTheTwoDepthsAgree:
    """The module's whole claim. If the fast seam and the deep one disagree,
    "byte-identical replay" means whichever depth you happened to use."""

    REC = CallRecording(
        turns=(
            RecordedTurn(("Of course dear. ", "It is 4539 1488 0343 6467"), stop_reason="max_tokens"),
        ),
        model="claude-sonnet-5",
        effort="low",
        max_tokens=400,
    )

    async def fast(self, consume: int | None) -> tuple[list[str], str | None]:
        brain = ReplayBrain(self.REC, strict=False)
        return await self.run(brain, consume)

    async def deep(self, consume: int | None) -> tuple[list[str], str | None]:
        brain = ClaudeBrain(system_prompt="S", client=RecordedAnthropicClient(self.REC, strict=False))
        return await self.run(brain, consume)

    async def run(self, brain: object, consume: int | None) -> tuple[list[str], str | None]:
        got: list[str] = []
        async for sentence in brain.stream_reply(CALLER):  # type: ignore[attr-defined]
            got.append(sentence)
            if consume is not None and len(got) >= consume:
                break  # what the output filter does when it blocks
        return got, brain.last_stop_reason  # type: ignore[attr-defined]

    @pytest.mark.parametrize("consume", [None, 1, 2])
    async def test_sentences_and_stop_reason_match_at_both_depths(self, consume: int | None) -> None:
        # consume=1 is the case that used to diverge: the filter blocks, the
        # consumer breaks, and the fast seam never recorded why the turn ended.
        assert await self.fast(consume) == await self.deep(consume)

    async def test_the_truncated_tail_is_spoken_not_dropped(self) -> None:
        # A truncated reply has no sentence terminator by definition, so the
        # residual buffer *is* the speech. Dropping it would substitute a
        # fumble line for the caller's last words on every cut-off turn.
        sentences, stop_reason = await self.fast(None)
        assert sentences[-1] == "It is 4539 1488 0343 6467"
        assert stop_reason == "max_tokens"

    async def test_an_empty_history_is_a_no_op_at_both_depths(self) -> None:
        fast = ReplayBrain(self.REC, strict=False)
        deep_client = RecordedAnthropicClient(self.REC, strict=False)
        deep = ClaudeBrain(system_prompt="S", client=deep_client)

        assert [s async for s in fast.stream_reply([])] == []
        assert [s async for s in deep.stream_reply([])] == []
        assert fast.index == deep_client.index == 0

    async def test_the_deep_seam_detects_divergence_too(self) -> None:
        # The seam that runs the real request construction must not be the lax
        # one — that would be exactly backwards.
        rec = CallRecording(turns=(RecordedTurn(("Hi.",), state_note="recorded"),))
        brain = ClaudeBrain(system_prompt="S", client=RecordedAnthropicClient(rec))
        with pytest.raises(DivergedError, match="steering differs"):
            [s async for s in brain.stream_reply(CALLER, "different steering")]

    async def test_the_deep_seam_reads_the_steering_back_out_of_the_request(self) -> None:
        rec = CallRecording(turns=(RecordedTurn(("Hi.",), state_note="STEERING", caller_turns=1),))
        brain = ClaudeBrain(system_prompt="S", client=RecordedAnthropicClient(rec))
        assert [s async for s in brain.stream_reply(CALLER, "STEERING")] == ["Hi."]

    async def test_an_unserved_request_is_kept_apart_from_the_served_ones(self) -> None:
        client = RecordedAnthropicClient(CallRecording(turns=(RecordedTurn(("one.",)),)))
        brain = ClaudeBrain(system_prompt="S", client=client)
        [s async for s in brain.stream_reply(CALLER)]
        with pytest.raises(DivergedError):
            [s async for s in brain.stream_reply(CALLER)]

        assert len(client.requests) == 1, "requests must count only what got a reply"
        assert len(client.unserved) == 1
        assert not client.complete


class TestTheRecordingFormatIsTotal:
    def test_every_field_survives_the_round_trip(self) -> None:
        # Driven off the dataclass, not a hand-written list: a field added and
        # forgotten in to_json/from_json would otherwise round-trip silently as
        # its default, and a golden would pin less than it claims.
        import dataclasses

        original = CallRecording(
            turns=(RecordedTurn(("a ", "b."), stop_reason="max_tokens", state_note="n", caller_turns=2),),
            persona_id="marjorie",
            seed=7,
            model="claude-sonnet-5",
            effort="high",
            max_tokens=123,
        )
        restored = CallRecording.from_json(original.to_json())
        for field_ in dataclasses.fields(CallRecording):
            assert getattr(restored, field_.name) == getattr(original, field_.name), field_.name

    def test_an_omitted_metadata_key_deserializes_to_the_constructor_default(self) -> None:
        # Fails closed: a fixture without pack_version must still be guarded.
        restored = CallRecording.from_json('{"turns": [{"deltas": ["hi."]}]}')
        assert restored.pack_version == PACK_VERSION
        assert restored.turns[0].stop_reason == "end_turn"

    def test_the_unrecorded_sentinel_survives_the_round_trip(self) -> None:
        from ssscammers.simscammer.replay import UNRECORDED

        lax = CallRecording(turns=(RecordedTurn(("hi.",)),))
        pinned = CallRecording(turns=(RecordedTurn(("hi.",), state_note=None),))
        assert CallRecording.from_json(lax.to_json()).turns[0].state_note is UNRECORDED
        assert CallRecording.from_json(pinned.to_json()).turns[0].state_note is None

    def test_a_mismatched_model_refuses_the_replay(self) -> None:
        rec = CallRecording(turns=(RecordedTurn(("hi.",)),), model="claude-sonnet-5", effort="high")
        rec.check_environment(brain=ClaudeBrain(system_prompt="S", client=object(), model="claude-sonnet-5", effort="high"))
        with pytest.raises(DivergedError, match="effort"):
            rec.check_environment(brain=ClaudeBrain(system_prompt="S", client=object()))


class TestTheEventStreamSurvivesTheRoundTrip:
    """The precondition for golden replay: a call driven through the deep seam
    and the same recording driven through the fast one must emit the *same
    events*, payload for payload. If they differ, "byte-identical replay" is a
    claim about whichever depth the runner happened to pick."""

    REC = CallRecording(
        turns=(
            RecordedTurn(("Oh dear. ", "Let me find my glasses.")),
            RecordedTurn(("Which card was it now?",), stop_reason="max_tokens"),
        ),
        persona_id="marjorie",
        seed=6,
        model="claude-sonnet-5",
        effort="low",
        max_tokens=400,
    )
    LINES = ("Read me the card number.", "The long number, please.")

    async def drive(self, brain: object) -> list[CallEvent]:
        sink = RecordingSink()
        conversation = build_conversation(
            Settings(),
            persona_id="marjorie",
            brain=brain,  # type: ignore[arg-type]
            events=sink,
            clock=SimulatedClock(),
            seed=6,
        )
        conversation.director.state.phase = CallPhase.STALL
        await conversation.open()
        for line in self.LINES:
            [a async for a in conversation.respond(line)]
        return sink.events

    async def test_both_depths_emit_the_same_event_stream(self) -> None:
        deep_client = RecordedAnthropicClient(self.REC, strict=False)
        deep = await self.drive(
            ClaudeBrain(system_prompt=load_persona("marjorie").system_prompt(), client=deep_client)
        )
        fast_brain = ReplayBrain(self.REC, strict=False)
        fast = await self.drive(fast_brain)

        assert deep == fast
        assert deep_client.complete and fast_brain.complete

    async def test_the_first_event_carries_the_recorded_request_construction(self) -> None:
        # `call_opened` is seq 1; getting it wrong means every golden diverges
        # before the caller has said anything.
        events = await self.drive(ReplayBrain(self.REC, strict=False))
        opened = events[0].payload
        assert (opened["model"], opened["effort"], opened["max_tokens"]) == (
            "claude-sonnet-5",
            "low",
            400,
        )

    async def test_a_truncated_turn_is_labelled_the_same_at_both_depths(self) -> None:
        deep = await self.drive(
            ClaudeBrain(
                system_prompt=load_persona("marjorie").system_prompt(),
                client=RecordedAnthropicClient(self.REC, strict=False),
            )
        )
        fast = await self.drive(ReplayBrain(self.REC, strict=False))
        failures = lambda events: [  # noqa: E731
            e.payload["failure"]
            for e in events
            if e.type == "agent_turn" and not e.payload["scripted"]
        ]
        assert failures(deep) == failures(fast) == [None, "truncated"]

    async def test_strict_mode_survives_a_real_conversation(self) -> None:
        # The default mode, exercised through the production driver rather than
        # a hand-built history: record the steering off the deep seam, then
        # replay against it with every check armed.
        client = RecordedAnthropicClient(self.REC, strict=False)
        await self.drive(
            ClaudeBrain(system_prompt=load_persona("marjorie").system_prompt(), client=client)
        )
        observed = [describe_request(request) for request in client.requests]
        pinned = CallRecording(
            turns=tuple(
                RecordedTurn(
                    turn.deltas,
                    stop_reason=turn.stop_reason,
                    state_note=note,
                    caller_turns=count,
                )
                for turn, (count, note) in zip(self.REC.turns, observed, strict=True)
            ),
            persona_id=self.REC.persona_id,
            seed=self.REC.seed,
            model=self.REC.model,
            effort=self.REC.effort,
            max_tokens=self.REC.max_tokens,
        )

        strict = ReplayBrain(pinned)  # strict=True is the default
        await self.drive(strict)
        assert strict.complete

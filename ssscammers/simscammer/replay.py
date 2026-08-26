"""Re-driving a recorded call: the model, made deterministic.

A call is reproducible from four inputs — the seed, the caller's turns, the
clock, and the model's replies. The first three are cheap to pin. This module
pins the fourth, at two depths, because they answer different questions.

:class:`ReplayBrain` replaces the model at the ``stream_reply`` seam. It is fast
and exact, and it is what a replay runner drives a whole corpus through.

:class:`RecordedAnthropicClient` goes a layer deeper, plugging into a real
:class:`~ssscammers.agent.llm.ClaudeBrain` so that module's own streaming path
runs — the request built for real, text deltas cut into sentences by the
production splitter, ``stop_reason`` read off the final message. ``--dry``
executes none of it, because it sets ``brain=None`` and skips the class.

**The two depths must agree.** A recording replayed through the fast seam and
through the deep one has to produce the same sentences, the same
``last_stop_reason``, and the same request metadata — otherwise "byte-identical
replay" means whichever depth you happened to use. They therefore share one
cursor, one divergence check, and the production request builder rather than
carrying parallel copies that drift.

**Neither papers over divergence.** A replay whose turns stop lining up is not a
replay, and yielding a stale reply anyway would turn a diverged run green —
especially here, where ``Conversation._generate`` catches every exception and
speaks a stalling line, so a raised error alone is not enough. Divergence is
recorded on the cursor, and :attr:`ReplayBrain.complete` is what a runner
asserts: it is false after *any* divergence, not merely after a short run.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ssscammers.agent.llm import ClaudeBrain, Turn, stream_sentences
from ssscammers.shared.fiction import PACK_VERSION

__all__ = [
    "UNRECORDED",
    "DivergedError",
    "RecordedTurn",
    "CallRecording",
    "ReplayBrain",
    "RecordedAnthropicClient",
    "describe_request",
]


class _Unrecorded:
    """Sentinel: this field was not captured, as distinct from captured empty."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNRECORDED"


#: Distinguishes "not recorded" from "recorded as ``None``". Without it a
#: hand-written fixture that omits a field silently disables the check that
#: field drives, which is the difference between a guard and a decoration.
UNRECORDED: Any = _Unrecorded()


class DivergedError(AssertionError):
    """A replay stopped matching the recording it was driving."""


@dataclass
class _Cursor:
    """Shared position and divergence state for both replay depths.

    One object so the two depths cannot disagree about how far a replay got or
    whether it went wrong — the module's whole claim is that they behave the
    same, and two copies of this bookkeeping is how that claim rots.
    """

    total: int
    index: int = 0
    divergences: int = 0

    def take(self, noun: str) -> int:
        if self.index >= self.total:
            self.divergences += 1
            raise DivergedError(
                f"replay asked for {noun} {self.index + 1} but the recording holds "
                f"{self.total}: the call took a different path"
            )
        position = self.index
        self.index += 1
        return position

    def diverge(self, message: str) -> None:
        self.divergences += 1
        raise DivergedError(message)

    @property
    def complete(self) -> bool:
        """Every recorded turn consumed, and nothing went wrong on the way.

        A replay that ends early is as divergent as one that runs long — it
        just fails quietly instead — and one that raised mid-way must never
        report complete, because the error it raised was probably swallowed.
        """
        return self.index == self.total and not self.divergences


@dataclass(frozen=True)
class RecordedTurn:
    """One model turn as it actually happened.

    Args:
        deltas: The raw text chunks the API streamed, in order. Kept raw rather
            than as finished sentences so the sentence splitter is replayed
            rather than bypassed — a boundary bug is exactly the kind that
            survives a recording of its own output.
        stop_reason: What the API said ended the turn. ``"max_tokens"`` is the
            one that matters: it is how a truncated reply is labelled, and it
            is invisible in the text.
        state_note: The steering this turn was generated under, for divergence
            detection. ``UNRECORDED`` disables the check; ``None`` asserts the
            turn carried no steering.
        caller_turns: How many caller turns the transcript held — the cheap
            half of the same check, with the same sentinel.
    """

    deltas: tuple[str, ...]
    stop_reason: str | None = "end_turn"
    state_note: str | None | Any = UNRECORDED
    caller_turns: int | None | Any = UNRECORDED

    @property
    def text(self) -> str:
        return "".join(self.deltas)


@dataclass(frozen=True)
class CallRecording:
    """Every model turn of one call, plus what it takes to reproduce it.

    The metadata is not decoration. A recording replayed under a different
    persona, seed, or fiction pack is a different call that happens not to
    crash: the pack in particular is regenerated from a seeded rng, so
    regenerating it silently changes every fact the persona speaks. The request
    metadata is here for the same reason and one more — the ``call_opened``
    event records it, so a replay that cannot report the recorded model diverges
    on the very first event of the stream.
    """

    turns: tuple[RecordedTurn, ...]
    persona_id: str = ""
    seed: int | None = None
    pack_version: str = PACK_VERSION
    model: str = ""
    effort: str = ""
    max_tokens: int | None = None

    def to_json(self) -> str:
        # Metadata first, bulky turns last: a golden's diff should open on the
        # fields a reviewer checks, not scroll past a transcript to reach them.
        payload: dict[str, Any] = {
            name: getattr(self, name) for name in _METADATA_FIELDS
        }
        payload["turns"] = [_turn_to_json(turn) for turn in self.turns]
        return json.dumps(payload, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> CallRecording:
        data = json.loads(raw)
        # Defaults mirror the dataclass exactly: a deserializer that falls back
        # to "" where the constructor falls back to the current pack version
        # would turn every hand-written fixture into an unguarded replay.
        return cls(
            turns=tuple(_turn_from_json(t) for t in data["turns"]),
            **{
                name: data.get(name, _DEFAULTS[name])
                for name in _METADATA_FIELDS
            },
        )

    def write(self, path: Path) -> Path:
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> CallRecording:
        return cls.from_json(path.read_text(encoding="utf-8"))

    def check_environment(
        self,
        *,
        persona_id: str = "",
        seed: int | None = None,
        brain: Any = None,
    ) -> None:
        """Refuse a replay whose world differs from the recorded one.

        Checks what this object knows about: the fiction pack, the persona, the
        seed, and the request construction. It cannot see the caps or the entry
        path — those belong to the manifest that carries the caller's side, and
        the runner checks them there.
        """
        if self.pack_version and self.pack_version != PACK_VERSION:
            raise DivergedError(
                f"recording was made against fiction pack {self.pack_version}, "
                f"this tree ships {PACK_VERSION}: every spoken fact would differ"
            )
        if persona_id and self.persona_id and persona_id != self.persona_id:
            raise DivergedError(
                f"recording is of persona {self.persona_id!r}, replaying {persona_id!r}"
            )
        if seed is not None and self.seed is not None and seed != self.seed:
            raise DivergedError(f"recording used seed {self.seed}, replaying seed {seed}")

        for name in ("model", "effort", "max_tokens"):
            recorded, replaying = getattr(self, name), getattr(brain, name, None)
            if brain is not None and recorded and replaying != recorded:
                raise DivergedError(
                    f"recording was made with {name}={recorded!r}, "
                    f"replaying with {name}={replaying!r}"
                )


_METADATA_FIELDS = ("persona_id", "seed", "pack_version", "model", "effort", "max_tokens")
_DEFAULTS = {f.name: f.default for f in fields(CallRecording)}


def _turn_to_json(turn: RecordedTurn) -> dict[str, Any]:
    payload: dict[str, Any] = {"deltas": list(turn.deltas), "stop_reason": turn.stop_reason}
    # Omitted rather than nulled, so the sentinel survives the round trip.
    if not isinstance(turn.state_note, _Unrecorded):
        payload["state_note"] = turn.state_note
    if not isinstance(turn.caller_turns, _Unrecorded):
        payload["caller_turns"] = turn.caller_turns
    return payload


def _turn_from_json(data: dict[str, Any]) -> RecordedTurn:
    return RecordedTurn(
        deltas=tuple(data["deltas"]),
        stop_reason=data.get("stop_reason", "end_turn"),
        # `.get` with the sentinel as default: an absent key stays UNRECORDED
        # while an explicit null round-trips as a recorded ``None``.
        state_note=data.get("state_note", UNRECORDED),
        caller_turns=data.get("caller_turns", UNRECORDED),
    )


def describe_request(kwargs: dict[str, Any]) -> tuple[int, str | None]:
    """The caller-turn count and steering carried by a built request.

    Reads back what :meth:`ClaudeBrain.build_messages` folded in, so the deep
    seam can run the same divergence check as the fast one — and so a recorder
    can capture those fields from a live call without the brain handing them
    over separately.
    """
    messages = kwargs.get("messages", [])
    caller_turns = sum(1 for message in messages if message["role"] == "user")

    state_note: str | None = None
    if messages and isinstance(messages[-1].get("content"), list):
        for block in messages[-1]["content"]:
            text = block.get("text", "")
            if text.startswith(_NOTE_OPEN) and text.endswith(_NOTE_CLOSE):
                state_note = text[len(_NOTE_OPEN) : -len(_NOTE_CLOSE)]
    return caller_turns, state_note


_NOTE_OPEN = "<system-reminder>\n"
_NOTE_CLOSE = "\n</system-reminder>"


def _check_turn(
    cursor: _Cursor,
    turn: RecordedTurn,
    *,
    noun: str,
    caller_turns: int,
    state_note: str | None,
) -> None:
    """The one divergence check, shared by both depths."""
    where = f"{noun} {cursor.index}"
    if not isinstance(turn.state_note, _Unrecorded) and turn.state_note != state_note:
        cursor.diverge(
            f"{where}: steering differs from the recording.\n"
            f"  recorded: {turn.state_note!r}\n"
            f"  replayed: {state_note!r}"
        )
    if not isinstance(turn.caller_turns, _Unrecorded) and turn.caller_turns != caller_turns:
        cursor.diverge(
            f"{where}: transcript held {turn.caller_turns} caller turn(s) when "
            f"recorded, {caller_turns} on replay"
        )


@dataclass
class ReplayBrain:
    """A model that only ever says what it said before.

    Duck-typed on the seam ``Conversation`` already uses — ``stream_reply``,
    ``last_stop_reason``, and the request metadata ``call_opened`` records — so
    a replayed call runs the production conversation driver unmodified.
    """

    recording: CallRecording
    strict: bool = True
    """Verify each turn's steering against the recording. Off only for fixtures
    that deliberately drive a recording through different inputs."""

    last_stop_reason: str | None = field(init=False, default=None)
    _cursor: _Cursor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # The pack guard is the one this object can enforce alone, and it is the
        # one whose failure is silent: a regenerated pack replays cleanly and
        # says different facts. Persona and seed need the caller's context, so
        # the runner passes them to `check_environment`.
        self.recording.check_environment()
        self._cursor = _Cursor(total=len(self.recording.turns))

    # The request construction this recording was made under. `call_opened`
    # reads these off the brain, so a replay that could not report them would
    # diverge on the first event of the stream.
    @property
    def model(self) -> str:
        return self.recording.model

    @property
    def effort(self) -> str:
        return self.recording.effort

    @property
    def max_tokens(self) -> int | None:
        return self.recording.max_tokens

    @property
    def index(self) -> int:
        return self._cursor.index

    @property
    def complete(self) -> bool:
        """Whether the replay consumed the whole recording without diverging."""
        return self._cursor.complete

    async def stream_reply(
        self, history: Sequence[Turn], state_note: str | None = None
    ) -> AsyncIterator[str]:
        # Mirrors production: cleared at stream start, so a turn that fails
        # cannot report the previous turn's reason.
        self.last_stop_reason = None

        # The production guard, run through the production builder rather than
        # reimplemented: with no addressable caller turn the real brain returns
        # without touching the wire, and a recording made against it holds no
        # turn here. Skipping this would desync the cursor permanently.
        if not ClaudeBrain.build_messages(history, state_note):
            return

        turn = self.recording.turns[self._cursor.take("model turn")]
        if self.strict:
            _check_turn(
                self._cursor,
                turn,
                noun="model turn",
                caller_turns=sum(1 for t in history if t.role == "user"),
                state_note=state_note,
            )

        # `last_stop_reason` is set where production sets it — when the deltas
        # run out, *inside* the stream the splitter consumes — not after the
        # sentence loop. A consumer that breaks early (the output filter blocks,
        # ending the turn) must still see the reason, exactly as it would live.
        async def deltas() -> AsyncIterator[str]:
            for delta in turn.deltas:
                yield delta
            self.last_stop_reason = turn.stop_reason

        async for sentence in stream_sentences(deltas()):
            yield sentence


class _RecordedStream:
    """One ``messages.stream(...)`` context, replaying a recorded turn."""

    def __init__(self, turn: RecordedTurn) -> None:
        self._turn = turn

    async def __aenter__(self) -> _RecordedStream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def deltas() -> AsyncIterator[str]:
            for delta in self._turn.deltas:
                yield delta

        return deltas()

    async def get_final_message(self) -> Any:
        return _FinalMessage(self._turn.stop_reason)


@dataclass(frozen=True)
class _FinalMessage:
    stop_reason: str | None


class _RecordedMessages:
    def __init__(self, client: RecordedAnthropicClient) -> None:
        self._client = client

    def stream(self, **kwargs: Any) -> _RecordedStream:
        return self._client.next_stream(kwargs)


class RecordedAnthropicClient:
    """An Anthropic client that replays a recording instead of calling the API.

    Injected as :attr:`ClaudeBrain.client`, so the brain builds its real request
    and this fake keeps every one — which is what lets a test assert on the
    request surface (the cache breakpoints, the ``<system-reminder>`` fold,
    ``thinking`` disabled, the explicit effort) with no network call.

    It runs the *same* divergence check as the fast seam, reading the steering
    back out of the request it was handed. The deeper seam being the lax one
    would be exactly backwards.
    """

    def __init__(self, recording: CallRecording, *, strict: bool = True) -> None:
        recording.check_environment()
        self.recording = recording
        self.strict = strict
        self.requests: list[dict[str, Any]] = []
        self.unserved: list[dict[str, Any]] = []
        self._cursor = _Cursor(total=len(recording.turns))
        self.messages = _RecordedMessages(self)

    def next_stream(self, kwargs: dict[str, Any]) -> _RecordedStream:
        try:
            position = self._cursor.take("request")
        except DivergedError:
            # Kept, but apart from the served requests: a count of `requests`
            # must stay a count of requests that got a reply.
            self.unserved.append(kwargs)
            raise

        self.requests.append(kwargs)
        turn = self.recording.turns[position]
        if self.strict:
            caller_turns, state_note = describe_request(kwargs)
            _check_turn(
                self._cursor,
                turn,
                noun="request",
                caller_turns=caller_turns,
                state_note=state_note,
            )
        return _RecordedStream(turn)

    @property
    def index(self) -> int:
        return self._cursor.index

    @property
    def complete(self) -> bool:
        return self._cursor.complete

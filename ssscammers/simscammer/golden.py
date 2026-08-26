"""Golden calls: a manifest of inputs, and the event stream they must produce.

A call is reproducible from its inputs, so a golden is just those inputs plus
the stream they produced last time. The runner re-drives the production
:class:`~ssscammers.agent.conversation.Conversation` and the test diffs the
result byte for byte; anything that moves — a payload field, a sequence number,
a drawn tactic — shows up as a diff on the turn that caused it.

**The manifest is the whole input set, deliberately.** Every input lives here
rather than in the runner, because an input the runner supplies is an input the
golden does not pin:

* the caller's side is a :class:`~ssscammers.simscammer.scripts.CallerScript` —
  reused rather than re-invented, because those scripts are authored and
  human-reviewed and a second copy of them is a second thing to keep true;
* the model's side is a :class:`~ssscammers.simscammer.replay.CallRecording`;
* the seed, persona, and entry path decide the draws and the triage bar;
* the pacing decides the clock, and the **tick cadence** decides how many times
  the timer fires between turns. That one matters more than it looks: hold
  versus dead-air behaviour is cadence-sensitive, and the text harness never
  called :meth:`Conversation.tick` at all, so no existing artifact exercised
  the timer path.

The clock is the only time injection here. ``DailyLedger``'s civil date is the
other one the roadmap names, but it lives on the *admission* path — the registry
and the webhooks — which a conversation-level replay never touches. It becomes
an input the day admission enters replay scope, and not before.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ssscammers.agent.conversation import CallEvent, build_conversation
from ssscammers.agent.llm import Turn
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import CallPhase, EntryPath
from ssscammers.simscammer.clock import SimulatedClock
from ssscammers.simscammer.replay import CallRecording, RecordedTurn, ReplayBrain
from ssscammers.simscammer.scripts import ALL_SCRIPTS, CallerScript
from ssscammers.simscammer.session import Session

__all__ = [
    "GoldenManifest",
    "GOLDEN_MANIFESTS",
    "GOLDEN_DIR",
    "replay_call",
    "events_to_json",
    "diff_events",
    "load_golden",
    "write_golden",
    "manifest_by_name",
]

#: Where the recorded streams live. Beside the tests, because the test suite is
#: what gates them.
GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "goldens"


@dataclass(frozen=True)
class GoldenManifest:
    """Every input of one reproducible call."""

    name: str
    script: CallerScript
    recording: CallRecording = field(default_factory=lambda: CallRecording(turns=()))
    persona_id: str = "marjorie"
    seed: int = 0
    entry_path: EntryPath = EntryPath.DIRECT
    seconds_per_turn: float = 25.0

    ticking: bool = True
    """Whether the one-second timer runs, as it does on every live call."""

    idle_seconds: float = 0.0
    """Silence after the caller's last line. The only way to reach the timer's
    own exits — dead air, the caps — which no caller turn can trigger."""

    start_phase: CallPhase | None = None
    """Force the phase before the first turn, for goldens whose point is the
    baiting path rather than the walk that gets there."""

    @property
    def path(self) -> Path:
        return GOLDEN_DIR / f"{self.name}.json"


@dataclass(frozen=True)
class ReplayResult:
    """Everything a golden pins, which is more than the event stream.

    The exit criterion names the transcript as well as the events, and the
    *steering* belongs here too: it is the input that decides what the persona
    says, it appears in no event payload, and a golden that ignored it would
    stay green through a wholesale rewrite of the state notes. It rides in the
    golden rather than in the events because an event payload is bulky to carry
    it and, once persisted, permanent.
    """

    events: list[CallEvent]
    transcript: list[Turn]
    steering: list[str | None]


async def replay_call(manifest: GoldenManifest) -> ReplayResult:
    """Drive one manifest through the production conversation driver."""
    manifest.recording.check_environment(
        persona_id=manifest.persona_id, seed=manifest.seed
    )

    events: list[CallEvent] = []

    class _Sink:
        async def emit(self, event: CallEvent) -> None:
            events.append(event)

    clock = SimulatedClock()
    brain = ReplayBrain(manifest.recording) if manifest.recording.turns else None
    conversation = build_conversation(
        Settings(),
        call_sid=f"CA{manifest.name}",
        caller_number="+19375559999",
        entry_path=manifest.entry_path,
        persona_id=manifest.persona_id,
        brain=brain,  # type: ignore[arg-type]
        events=_Sink(),
        clock=clock,
        seed=manifest.seed,
    )
    if manifest.start_phase is not None:
        conversation.director.state.phase = manifest.start_phase

    # The one driver, shared with the text harness: same clock handling, same
    # timer cadence. Two drivers of one Conversation is how the harness and the
    # gate came to disagree about when things happened.
    session = Session(
        conversation=conversation,
        clock=clock,
        seconds_per_turn=manifest.seconds_per_turn,
        ticking=manifest.ticking,
    )

    await conversation.open()
    for line in manifest.script.lines:
        await session.say(line)
        if conversation.ended:
            break
    if not conversation.ended and manifest.idle_seconds:
        # Silence after the caller stops talking: the only way to reach the
        # timer's own exits, which no caller turn can trigger.
        await session.idle(manifest.idle_seconds)

    if brain is not None and not brain.complete:
        raise AssertionError(
            f"{manifest.name}: the replay did not consume its recording "
            f"({brain.index}/{len(manifest.recording.turns)} turns) — the call "
            f"took a different path"
        )
    return ReplayResult(
        events=events,
        transcript=list(conversation.history),
        steering=list(brain.seen_state_notes) if brain is not None else [],
    )


def events_to_json(result: ReplayResult) -> str:
    """Serialize a replay for diffing.

    Key order is the logical one, not alphabetical: a reviewer reading a golden
    diff wants ``seq`` and ``type`` before the payload they identify, and the
    payloads are already insertion-ordered and deterministic, so sorting buys
    no stability and costs readability.
    """
    return json.dumps(
        {
            "events": [
                {
                    "seq": event.seq,
                    "type": event.type,
                    "at_seconds": round(event.at_seconds, 6),
                    "call_sid": event.call_sid,
                    "payload": event.payload,
                }
                for event in result.events
            ],
            "transcript": [
                {"role": turn.role, "scripted": turn.scripted, "content": turn.content}
                for turn in result.transcript
            ],
            "steering": result.steering,
        },
        indent=2,
        ensure_ascii=False,
    )


def diff_events(expected: str, actual: str) -> str | None:
    """A readable diff of two serialized streams, or ``None`` when identical."""
    if expected == actual:
        return None
    import difflib

    return "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="golden",
            tofile="replay",
            lineterm="",
            n=3,
        )
    )


def _script(name: str) -> CallerScript:
    return next(s for s in ALL_SCRIPTS if s.name == name)


def _reply(*deltas: str, stop_reason: str = "end_turn") -> RecordedTurn:
    return RecordedTurn(deltas, stop_reason=stop_reason)


#: A card split across a **sentence** boundary, so neither half is blockable
#: on its own and only the cumulative check catches it. Splitting mid-sentence
#: would prove nothing: the splitter rejoins the deltas before the filter ever
#: sees them, and a per-sentence check would catch that just as well.
_CARD_REPLY = _reply("Let me see now. The number is 4539 1488.", " 0343 6467, dear.")


def _recording(*turns: RecordedTurn, persona_id: str, seed: int) -> CallRecording:
    """A recording with its world pinned as literals.

    ``pack_version`` is written out rather than defaulted: the default is read
    from the live tree, so a recording that inherits it can never *disagree*
    with it, and the guard against a regenerated fiction pack compares a value
    to itself.
    """
    return CallRecording(
        turns=turns,
        persona_id=persona_id,
        seed=seed,
        pack_version="v1",
        model="claude-sonnet-5",
        effort="low",
        max_tokens=400,
    )


GOLDEN_MANIFESTS: tuple[GoldenManifest, ...] = (
    GoldenManifest(
        name="misroute_pharmacy_released",
        script=_script("pharmacy_prescription"),
        # No recording: the disclosure is a fixed script and the model is never
        # consulted. A golden that needed a recording here would be pinning the
        # wrong thing.
        seed=11,
    ),
    GoldenManifest(
        name="misroute_emergency_redirect",
        script=_script("real_emergency"),
        seed=12,
        entry_path=EntryPath.CONDITIONAL_FORWARD,
    ),
    GoldenManifest(
        name="scam_bank_otp_baited",
        script=_script("bank_otp_harvest"),
        recording=_recording(
            _reply("Oh, hello dear. ", "Which bank did you say?"),
            _reply("Oh no. ", "Was it a big charge?"),
            _CARD_REPLY,
            _reply("Hang on, hang on. ", "Let me find my glasses."),
            _reply("It's very small print, this. ", "Is that a three or an eight?"),
            _reply("I'm reading them out now, dear, I really am", stop_reason="max_tokens"),
            persona_id="marjorie",
            seed=6,
        ),
        seed=6,
        start_phase=CallPhase.STALL,
    ),
    GoldenManifest(
        name="scam_walks_from_greeting_into_baiting",
        # No forced phase: the one golden that walks the state machine the way
        # a real call does — greeting, assessing, commitment, baiting. Without
        # it a change to the triage commit bar leaves every other golden
        # byte-identical, since the rest start mid-call.
        #
        # It does *not* pin the probation window, and cannot: no authored
        # opener reaches the 0.6 commit bar on its own (the strongest is 0.50),
        # so the commit always waits for a second turn's evidence at t=50s,
        # already past the 30s window. Probation only binds a caller who
        # convicts himself in one breath, and inventing such an opener purely
        # to make a mutation die would be a fixture, not a call. The
        # probation → hard-commit behaviour itself is pinned directly by
        # tests/test_call_scripts.py.
        script=_script("tech_support_remote_access"),
        recording=_recording(
            _reply("Hello? ", "Who is this, please?"),
            _reply("My computer? ", "I don't think I have one of those."),
            _reply("The Windows key. ", "Which one is that, dear?"),
            _reply("Oh, I see a picture of a flag. ", "Is that it?"),
            _reply("It's asking me something now. ", "What does it say?"),
            _reply("Oh dear, the screen has gone dark.",),
            persona_id="dot",
            seed=41,
        ),
        persona_id="dot",
        seed=41,
    ),
    GoldenManifest(
        name="scam_irs_model_says_nothing",
        script=_script("irs_arrest_threat"),
        recording=_recording(
            _reply("Sorry dear, what was that? ", "I didn't catch it."),
            # An empty reply: the model produced nothing at all, and the
            # persona must cover with a drawn fumble line rather than silence,
            # which reads as a dropped call and ends the bait.
            _reply(),
            _reply("Say that again for me? ", "The telly's on."),
            _reply("Two thousand what, dear?"),
            _reply("I'll have to find my chequebook."),
            _reply("Is that the one with the blue cover?"),
            persona_id="harold",
            seed=21,
        ),
        persona_id="harold",
        seed=21,
        start_phase=CallPhase.STALL,
    ),
    GoldenManifest(
        name="timer_dead_air_hangup",
        # The timer's own exit: the caller pitches once and then goes silent,
        # and nothing but the clock ends the call. The single recorded turn is
        # exact — the hangup lands before the caller's second line, and the
        # runner refuses a recording it did not consume.
        script=CallerScript(
            name="one_line_pitch",
            lines=(_script("refund_gift_cards").lines[0],),
            tags=("scam", "refund"),
        ),
        recording=_recording(
            _reply("Hello? ", "Are you still there, dear?"),
            persona_id="marjorie",
            seed=31,
        ),
        seed=31,
        idle_seconds=200.0,
        start_phase=CallPhase.STALL,
    ),
)


def manifest_by_name(name: str) -> GoldenManifest:
    return next(m for m in GOLDEN_MANIFESTS if m.name == name)


async def write_golden(manifest: GoldenManifest) -> Path:
    """Regenerate one golden from its manifest."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    events = await replay_call(manifest)
    manifest.path.write_text(events_to_json(events) + "\n", encoding="utf-8")
    return manifest.path


def load_golden(manifest: GoldenManifest) -> str:
    return manifest.path.read_text(encoding="utf-8").rstrip("\n")

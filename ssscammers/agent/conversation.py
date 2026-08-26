"""Driving one call, with no opinion about how the audio gets here.

This is the layer the media pipeline and the terminal harness share. It owns the
:class:`~ssscammers.agent.persona_director.PersonaDirector`, the model, the transcript,
and the clock, and it turns each caller turn into a short sequence of *actions* — speak
this, play that clip, wait this long, hang up. Actions rather than audio, so the harness
can run a whole call with no speech stack, the tests can assert on what a call would
sound like without waiting for it, and the Pipecat wiring in
:mod:`ssscammers.agent.media` stays thin enough to be obviously correct.

Three things here are subtle and were got wrong before being got right.

**A sentence is not a safe unit to filter.** Sentence-at-a-time streaming is the biggest
perceived-latency win available, but checking each sentence *alone* opens a hole in G-4:
"the number is 4532 1234" and "5678 9010" each carry a harmless eight-digit run and
together read out a card. Every sentence is therefore checked against the whole reply so
far, and a block ends the turn — the next sentence is where the rest of the number was
going.

**Character latency must not stack on top of real latency.** The pause is added only
insofar as the model has not already spent it.

**Dead air is measured from the end of *our* audio, and counts a hold as ours.** See
:attr:`Conversation.silence_seconds`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ssscammers.agent.llm import ClaudeBrain, Turn
from ssscammers.agent.persona import load_persona
from ssscammers.agent.persona_director import PersonaDirector, TurnPlan
from ssscammers.agent.state_machine import Transition
from ssscammers.agent.triage import AllowlistCache
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import CallPhase, EndReason, EntryPath
from ssscammers.shared.output_filter import FUMBLE_LINES, trailing_digit_run

logger = logging.getLogger(__name__)

__all__ = [
    "Action",
    "Say",
    "PlayClip",
    "Pause",
    "HangUp",
    "CallEvent",
    "EventSink",
    "LoggingEventSink",
    "Conversation",
    "build_conversation",
]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Say:
    """Speak this text. Already filtered; the transport must not alter it."""

    text: str


@dataclass(frozen=True)
class PlayClip:
    """Play a sound-pack clip.

    ``filler`` covers real generation latency and starts before the model has produced
    anything. ``hold`` is the persona physically leaving the phone.
    """

    clip: str
    kind: Literal["filler", "hold"] = "filler"


@dataclass(frozen=True)
class Pause:
    """Say nothing for this long. Character latency, or a hold in progress."""

    seconds: float


@dataclass(frozen=True)
class HangUp:
    """End the call. Always the last action of a turn."""

    reason: EndReason | None = None


Action = Say | PlayClip | Pause | HangUp


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallEvent:
    """One entry in the canonical per-call log.

    Carries the three things only the pipeline knows: which call, the per-call sequence
    number (assigned here, the only place that sees the whole call in order), and how far
    into the call it happened.

    Deliberately *not* a row for ``call_events`` in ``db/migrations/001_initial.sql``:
    that table wants a ``uuid`` and a ``timestamptz``, while a conversation holds a
    monotonic clock — right for durations, wrong for stamping wall-clock time. A persistent
    sink is expected to map ``call_sid`` to ``call_id`` and supply ``ts`` itself.
    """

    seq: int
    type: str
    at_seconds: float
    call_sid: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    """Where call events go. :class:`LoggingEventSink` is the only production
    implementation today; the planned persistence layer will implement this against
    Postgres."""

    async def emit(self, event: CallEvent) -> None: ...


class LoggingEventSink:
    """Writes events to the log. The default, and enough for the text harness."""

    async def emit(self, event: CallEvent) -> None:
        logger.info(
            "event seq=%d t=%.1fs %s %s", event.seq, event.at_seconds, event.type, event.payload
        )


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------


@dataclass
class Conversation:
    """One call, from greeting to hangup.

    Args:
        director: Owns triage, the state machine, tactics, and the output filter.
        brain: The model. ``None`` runs everything except generation, which is what
            makes the harness's ``--dry`` mode and most of the tests possible.
        clock: Monotonic seconds. Injected so a test can run an hour-long call
            instantly and so the harness can simulate pacing.
        events: Canonical log sink.
        generation_timeout_seconds: Ceiling on one turn's generation. A hung stream
            must degrade to a stalling noise, not to silence on a live call.
        rng: Fumble-line and hold-clip selection. In production this is the tail of
            the one seeded per-call stream :func:`build_conversation` shares across
            the director, the filter, and this object — a second stream would break
            byte-identical replay, as would replaying against different model
            replies (see :func:`build_conversation`).
        seed: The integer that seeded ``rng``, recorded in the ``call_opened``
            payload so the call can be re-driven draw-for-draw. ``None`` only when
            a test injected an rng directly; production calls always carry one.
    """

    director: PersonaDirector
    call_sid: str = ""
    brain: ClaudeBrain | None = None
    clock: Callable[[], float] = time.monotonic
    events: EventSink = field(default_factory=LoggingEventSink)
    generation_timeout_seconds: float = 12.0
    rng: random.Random = field(default_factory=random.Random)
    seed: int | None = None

    _history: list[Turn] = field(default_factory=list, init=False)
    _started_at: float | None = field(default=None, init=False)
    _last_caller_audio: float = field(default=0.0, init=False)
    _last_agent_audio: float = field(default=0.0, init=False)
    _pending_dtmf: str = field(default="", init=False)
    _seq: int = field(default=0, init=False)
    _ended: bool = field(default=False, init=False)
    _last_plan: TurnPlan | None = field(default=None, init=False)
    _line_busy_until: float = field(default=0.0, init=False)
    _spoken_digit_tail: str = field(default="", init=False)
    _offered_voicemail: bool = field(default=False, init=False)

    # -- lifecycle ------------------------------------------------------------

    @property
    def history(self) -> list[Turn]:
        """The transcript, in API shape. Read-only by convention."""
        return self._history

    @property
    def elapsed_seconds(self) -> float:
        """Time since the greeting. Zero before the call opens."""
        if self._started_at is None:
            return 0.0
        return self.clock() - self._started_at

    @property
    def silence_seconds(self) -> float:
        """How long the line has been quiet, counting from whoever occupied it last.

        Includes our own audio and — crucially — the *whole* of a hold rather than its
        start. The persona puts the phone down for up to ninety seconds on purpose, longer
        than the sixty-second dead-air window, so measuring from the moment our audio
        stopped would make the best stalling tactic in the playbook hang up on itself
        mid-stall, every time (G-16). ``_line_busy_until`` is therefore set *ahead* of the
        clock the moment a pause is emitted, when we already know how long the line is
        ours.
        """
        if self._started_at is None:
            return 0.0
        occupied = max(self._last_caller_audio, self._last_agent_audio, self._line_busy_until)
        return max(0.0, self.clock() - occupied)

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def last_plan(self) -> TurnPlan | None:
        """The most recent decision, for logging and for the harness's turn readout.

        Actions say what the caller hears; this says why. The tactic and the character
        pause are invisible in the action stream and are what you want when judging a
        persona.
        """
        return self._last_plan

    async def open(self) -> list[Action]:
        """Answer. Returns the neutral greeting.

        The recorded-line notice has already played from TwiML (G-2) — by the time this
        runs, the call is on the record and the caller has heard so.
        """
        now = self.clock()
        self._started_at = now
        self._last_caller_audio = now
        self._last_agent_audio = now

        plan = self.director.opening()
        greeting = plan.speak or ""
        if greeting:
            self._history.append(Turn("assistant", greeting, scripted=True))

        await self._emit(
            "call_opened",
            {
                "persona": self.director.persona.id,
                "caller": self.director.caller_number,
                "entry_path": self.director.entry_path.value,
                "phase": plan.phase.value,
                "seed": self.seed,
                # The request construction this call will use, recorded once: a
                # replayed call must know which model produced its replies. All
                # None on a dry run, where no request is ever built.
                "model": getattr(self.brain, "model", None),
                "effort": getattr(self.brain, "effort", None),
                "max_tokens": getattr(self.brain, "max_tokens", None),
            },
        )
        return [Say(greeting)] if greeting else []

    # -- what the transport tells us ------------------------------------------

    def note_caller_audio(self) -> None:
        """Caller speech energy was heard. Resets the dead-air window."""
        self._last_caller_audio = self.clock()

    def note_agent_audio_finished(self) -> None:
        """Our own audio has finished playing. Called by the transport after a turn's
        actions are done, which is what makes ``silence_seconds`` mean "the line is empty"
        rather than "the caller has not spoken recently".
        """
        self._last_agent_audio = self.clock()

    def _occupy_line(self, seconds: float) -> None:
        """Record that the line is ours for ``seconds`` from now — called when a pause is
        *emitted*, so the dead-air timer cannot fire in the middle of a hold.
        """
        self._line_busy_until = max(self._line_busy_until, self.clock() + seconds)

    def note_dtmf(self, digit: str) -> None:
        """Buffer a keypress. ``5`` is the escape hatch for a real caller."""
        self._pending_dtmf += digit

    async def _drain_dtmf(self) -> str:
        """Take the buffered digits and log them at the moment they matter.

        The event is emitted at the drain — the decision boundary — not at the
        keypress, and at *every* drain site: a digit consumed by a quiet tick or a
        hangup is still an input the planner saw, and the log must be able to
        re-drive the call from inputs alone. Non-escape digits are also the
        robocall-IVR signal ("press 1") this line exists to observe.
        """
        dtmf, self._pending_dtmf = self._pending_dtmf, ""
        if dtmf:
            await self._emit("dtmf", {"digits": dtmf})
        return dtmf

    # -- turns ----------------------------------------------------------------

    async def respond(self, utterance: str) -> AsyncIterator[Action]:
        """Handle one caller turn and yield what the persona does about it."""
        if self._ended:
            # The director re-plans the exit script for *any* turn taken while the phase
            # is still DISCLOSE_EXIT, so a real caller who speaks over the disclosure
            # would hear it twice and hang the call up twice.
            return

        spoke = bool(utterance.strip())
        if spoke:
            self._last_caller_audio = self.clock()
            self._history.append(Turn("user", utterance))

        dtmf = await self._drain_dtmf()

        # The plan is made first so the caller_turn event can carry the verdict this
        # utterance produced — the classification and its evidence, not the stale one
        # from a turn ago. The observable order is unchanged: caller_turn still
        # precedes everything _execute emits.
        try:
            plan = self._advance(utterance, dtmf=dtmf)
        except Exception:
            # The input that crashes the planner is the most forensically valuable
            # one; the event must survive even when planning does not. The marker is
            # explicit because absent triage keys would be ambiguous between three
            # facts: planning crashed, the plan carried no verdict, a serializer bug.
            if spoke:
                await self._emit("caller_turn", {"text": utterance, "planning_failed": True})
            raise
        if spoke:
            verdict = plan.triage
            await self._emit(
                "caller_turn",
                {"text": utterance, **(verdict.as_payload() if verdict else {})},
            )
        async for action in self._execute(plan):
            yield action

    async def tick(self) -> AsyncIterator[Action]:
        """Re-evaluate with no new speech, for the things a caller does not trigger.

        The hard cap (G-14), dead air (G-16), and the DTMF escape all fire while nobody is
        talking. Yields nothing unless that evaluation lands in a terminal phase: a timer
        must never make the persona start a turn on its own.
        """
        if self._ended or self._started_at is None:
            return

        dtmf = await self._drain_dtmf()
        silence = self.silence_seconds
        plan = self.director.check_exits(
            elapsed_seconds=self.elapsed_seconds,
            silence_seconds=silence,
            dtmf_digits=dtmf,
            caller_hung_up=False,
        )

        # A tick evaluates the machine, so it can *land* a transition — probation
        # expiring into HOOK is the clearest case, and it is the moment a reviewer most
        # wants to see. Discarding the plan silently would drop it from the canonical
        # log. The evaluation state rides along: a dead-air hangup whose event does
        # not say how silent the line was cannot be audited.
        await self._emit_transition(plan.transition, on_timer=True, silence_seconds=silence)

        if not (plan.speak or plan.hang_up):
            # Nothing to perform. `_last_plan` is deliberately not touched: it reports
            # the last turn actually executed, and a tick executed nothing.
            return

        self._last_plan = plan
        async for action in self._execute(plan, emit_transition=False):
            yield action

    async def caller_hung_up(self) -> list[Action]:
        """The far end went away. Nothing is spoken; the call is simply over."""
        if self._ended:
            return []
        dtmf = await self._drain_dtmf()
        plan = self._advance("", dtmf=dtmf, caller_hung_up=True)
        actions = [action async for action in self._execute(plan)]
        if not any(isinstance(action, HangUp) for action in actions):
            actions.append(HangUp(EndReason.CALLER_HANGUP))
            self._ended = True
        return actions

    def _advance(self, utterance: str, *, dtmf: str = "", caller_hung_up: bool = False) -> TurnPlan:
        plan = self.director.handle_caller_turn(
            utterance,
            elapsed_seconds=self.elapsed_seconds,
            silence_seconds=self.silence_seconds,
            dtmf_digits=dtmf,
            caller_hung_up=caller_hung_up,
        )
        self._last_plan = plan
        return plan

    async def _emit_transition(
        self,
        transition: Transition | None,
        *,
        on_timer: bool = False,
        silence_seconds: float | None = None,
    ) -> None:
        """Log a phase change, if there was one. ``on_timer`` marks a change no caller
        turn triggered — the hard cap, dead air, probation expiring — and carries the
        silence the timer evaluated, since no caller_turn event records it. Drained
        digits are their own ``dtmf`` event, emitted just before this one."""
        if transition is None or not transition.changed:
            return
        payload: dict[str, Any] = {
            "from": transition.frm.value,
            "to": transition.to.value,
            "trigger": transition.trigger.value,
        }
        if on_timer:
            payload["on_timer"] = True
            if silence_seconds is not None:
                payload["silence_seconds"] = round(silence_seconds, 3)
        await self._emit("phase_changed", payload)

    async def _execute(self, plan: TurnPlan, *, emit_transition: bool = True) -> AsyncIterator[Action]:
        if emit_transition:
            await self._emit_transition(plan.transition)

        # A fixed script is a human-reviewed constant, not model output, and is spoken as
        # written: running a disclosure through a scanner that fails closed would create a
        # path where the disclosure never gets said (G-11, G-12).
        if plan.speak is not None:
            self._history.append(Turn("assistant", plan.speak, scripted=True))
            await self._emit("agent_turn", {"text": plan.speak, "scripted": True})
            yield Say(plan.speak)

        elif plan.consult_model:
            if plan.filler:
                # Starts immediately, before the model has produced anything.
                yield PlayClip(plan.filler, kind="filler")
            async for action in self._generate(plan):
                yield action

            if plan.hold_seconds:
                holds = self.director.persona.holds
                clip = self.rng.choice(holds) if holds else None
                if clip is not None:
                    yield PlayClip(clip, kind="hold")
                self._occupy_line(float(plan.hold_seconds))
                yield Pause(float(plan.hold_seconds))
                await self._emit("hold", {"seconds": plan.hold_seconds, "clip": clip})

        if plan.hang_up:
            self._ended = True
            self._offered_voicemail = plan.offer_voicemail
            reason = self.director.state.end_reason
            await self._emit(
                "call_ended",
                {"phase": plan.phase.value, "reason": reason.value if reason else None},
            )
            yield HangUp(reason)

    async def _generate(self, plan: TurnPlan) -> AsyncIterator[Action]:
        """Stream one model reply, filtering cumulatively as it arrives."""
        if self.brain is None:
            return

        started = self.clock()
        spoken: list[str] = []
        first = True
        failure: str | None = None
        first_sentence_ms: int | None = None
        character_pause_ms = 0

        try:
            async with asyncio.timeout(self.generation_timeout_seconds):
                async for sentence in self.brain.stream_reply(self._history, plan.state_note):
                    if first:
                        first = False
                        first_sentence_ms = round((self.clock() - started) * 1000)
                        # Character latency, minus whatever the model already spent.
                        remaining = plan.character_delay_ms / 1000.0 - (self.clock() - started)
                        if remaining > 0:
                            character_pause_ms = round(remaining * 1000)
                            self._occupy_line(remaining)
                            yield Pause(remaining)

                    candidate = " ".join([*spoken, sentence]).strip()
                    # Prefixed with the digits the previous turn ended on, so a number
                    # split across a turn boundary is still one run to the filter.
                    result = self.director.vet_result(
                        f"{self._spoken_digit_tail} {candidate}".strip()
                        if self._spoken_digit_tail
                        else candidate
                    )
                    if result.blocked:
                        # End the turn: the rest of the reply is where the remainder of
                        # whatever was blocked would have gone.
                        await self._emit(
                            "output_blocked",
                            {"violations": [v.value for v in result.violations]},
                        )
                        yield Say(result.text)
                        spoken.append(result.text)
                        break

                    spoken.append(sentence)
                    yield Say(sentence)
        except TimeoutError:
            failure = "timeout"
            logger.error(
                "generation exceeded %.1fs on call from %s",
                self.generation_timeout_seconds,
                self.director.caller_number or "unknown",
            )
        except Exception:  # noqa: BLE001 - a live call outranks any single turn
            failure = "error"
            logger.exception("generation failed; covering with a stalling line")

        # Measured here, at stream end: in production the transport drains this
        # generator action by action, so time between sentences includes downstream
        # playback — per-sentence playback timings are the media seam's to record
        # (roadmap rescope 6). `first_sentence_ms` is the clean model-latency signal,
        # captured before anything was yielded for this sentence.
        stream_ms = round((self.clock() - started) * 1000)

        if failure is None and getattr(self.brain, "last_stop_reason", None) == "max_tokens":
            # The reply was cut mid-word. Nothing raised and text did arrive, so without
            # this the event log is byte-identical to a clean turn — and the fragment is
            # spoken, then fed back to the model as the persona's own last words.
            failure = "truncated"

        fumbled = False
        if not spoken:
            # Silence is the one thing that cannot happen here — it reads as a dropped
            # call and ends the bait. A fumble is a perfectly good stalling turn.
            fumbled = True
            fumble = self.rng.choice(FUMBLE_LINES)
            spoken.append(fumble)
            yield Say(fumble)

        text = " ".join(spoken).strip()
        self._spoken_digit_tail = trailing_digit_run(text)
        self._history.append(Turn("assistant", text))
        await self._emit(
            "agent_turn",
            {
                "text": text,
                "scripted": False,
                "tactic": plan.tactic.value,
                "phase": plan.phase.value,
                "failure": failure,
                # The turn's drawn values. The replay runner diffs these to catch a
                # diverged rng stream at the turn that diverged, not downstream.
                "filler": plan.filler,
                "character_delay_ms": plan.character_delay_ms,
                "fumbled": fumbled,
                # The turn's measured values: how long the caller actually waited.
                # first_sentence_ms is what the filler clip had to cover; None means
                # no sentence ever arrived (timeout, error, or an empty stream).
                "first_sentence_ms": first_sentence_ms,
                "stream_ms": stream_ms,
                "character_pause_ms": character_pause_ms,
                # The raw API stop reason, distinct from `failure`: "truncated" is
                # this project's judgment, "max_tokens" is what the API said.
                "stop_reason": getattr(self.brain, "last_stop_reason", None),
            },
        )

    # -- events ---------------------------------------------------------------

    async def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._seq += 1
        event = CallEvent(
            seq=self._seq,
            type=event_type,
            at_seconds=self.elapsed_seconds,
            call_sid=self.call_sid,
            payload=payload,
        )
        try:
            await self.events.emit(event)
        except Exception:  # noqa: BLE001 - observability must not end a call
            logger.exception("event sink failed on %s", event_type)

    # -- results --------------------------------------------------------------

    @property
    def final_phase(self) -> CallPhase:
        return self.director.state.phase

    @property
    def end_reason(self) -> EndReason | None:
        return self.director.state.end_reason

    @property
    def offered_voicemail(self) -> bool:
        """Whether the last words spoken promised this caller a voicemail. Read by the
        transport when it reports the outcome, so ``/twilio/after-stream`` honours a
        promise that was actually made.
        """
        return self._offered_voicemail


def _owner_pii(settings: Settings) -> tuple[str, ...]:
    """Every real string the filter must never speak, from wherever it is configured."""
    values = list(settings.owner_pii_denylist)
    if settings.owner_real_number:
        values.append(settings.owner_real_number)
    return tuple(values)


def build_conversation(
    settings: Settings,
    *,
    call_sid: str = "",
    caller_number: str = "",
    entry_path: EntryPath = EntryPath.UNKNOWN,
    persona_id: str = "",
    brain: ClaudeBrain | None = None,
    events: EventSink | None = None,
    allowlist: AllowlistCache | None = None,
    clock: Callable[[], float] = time.monotonic,
    seed: int | None = None,
) -> Conversation:
    """Assemble a conversation from configuration.

    The caps come from :class:`~ssscammers.shared.config.Settings` rather than from each
    call site: there was a version of this project where the media pipeline and the text
    harness disagreed about the hard cap and only one of them was tested.

    All randomness flows from one seeded stream shared by the director, the output
    filter, and the conversation. ``seed`` is drawn from OS entropy when not given,
    and is always recorded in the ``call_opened`` payload — the same seed, the same
    caller turns, the same clock, *and the same model reply stream* reproduce the
    call draw-for-draw, which is what the replay gate diffs against. The fourth
    input matters: the fumble draw fires only when a reply stream is empty and the
    filter's replacement draw only when a sentence is blocked, so a different reply
    consumes a different number of draws and shifts every draw after it — replay
    therefore re-drives recorded replies (the ReplayBrain seam), never a live
    model. There is deliberately no way to pass a bare ``random.Random`` here: an
    unrecorded rng is an unreplayable call.
    """
    persona = load_persona(persona_id or settings.default_persona)
    if seed is None:
        seed = random.SystemRandom().getrandbits(64)
    shared_rng = random.Random(seed)

    director = PersonaDirector(
        persona=persona,
        caller_number=caller_number,
        entry_path=entry_path,
        # `owner_real_number` is merged in here rather than left to the operator to
        # duplicate into OWNER_PII_DENYLIST. It was loaded and read by nothing, so the
        # promise its config section makes — "the filter blocks them, never speaks them" —
        # did not hold for the one value most likely to be dialled back.
        owner_pii=_owner_pii(settings),
        safeword=settings.owner_safeword,
        allowlist=allowlist if allowlist is not None else AllowlistCache(),
        rng=shared_rng,
        probation_seconds=settings.probation_seconds,
        probation_hard_commit_seconds=settings.probation_hard_commit_seconds,
        soft_cap_seconds=settings.soft_call_cap_seconds,
        hard_cap_seconds=settings.hard_call_cap_seconds,
        dead_air_seconds=settings.dead_air_hangup_seconds,
    )
    return Conversation(
        director=director,
        call_sid=call_sid,
        brain=brain,
        clock=clock,
        events=events or LoggingEventSink(),
        rng=shared_rng,
        seed=seed,
    )

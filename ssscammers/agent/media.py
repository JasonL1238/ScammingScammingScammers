"""Wiring one Twilio media stream to one :class:`~ssscammers.agent.conversation.Conversation`.

Deliberately the thinnest file in the project. Everything that decides anything lives in
:mod:`ssscammers.agent.conversation`, which has no transport in its import graph and is
therefore testable without a phone line, an API key, or the realtime stack. What is left
here is translation, plus the Pipecat construction details that are easy to get wrong.

    caller ──▶ Twilio ──ws──▶ transport.input()
                                   │
                         Deepgram Flux (STT + turn detection)
                                   │
                          _DirectorProcessor  ── drives Conversation
                                   │              (triage, FSM, tactics, filter)
                             Cartesia TTS
                                   │
                        transport.output() ──▶ ambient mixer ──▶ Twilio ──▶ caller

Four construction details carry real weight:

* **``auto_hang_up=False``.** The serializer's default ends the call over the REST API on
  ``EndFrame``, skipping the rest of the TwiML document. The pipeline instead hangs up by
  closing its socket, so the document continues — see :mod:`ssscammers.agent.webhooks`
  for what it still owes the caller.
* **Turn detection comes from the recogniser, not a VAD.** Deepgram Flux emits end-of-turn
  directly, which beats energy-based silence detection on a caller who pauses
  mid-sentence — and a persona that interrupts is a persona that gets hung up on.
* **Only the caller's audio is transcribed** (``inbound_track``, set in
  :mod:`ssscammers.agent.twiml`). Both tracks would feed the persona's own speech back
  into the recogniser, which transcribes it as a caller turn and answers itself.
* **The caps are enforced by a timer, not by the conversation.** G-14 and G-16 have to
  fire when nobody is talking, so a background task ticks regardless of audio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import wave
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ssscammers.agent.conversation import (
    Action,
    Conversation,
    HangUp,
    Pause,
    PlayClip,
    Say,
    build_conversation,
)
from ssscammers.agent.llm import ClaudeBrain, model_overrides
from ssscammers.agent.persona import PERSONA_DIR, Persona, VoiceConfig
from ssscammers.agent.registry import CallRegistry
from ssscammers.agent.triage import AllowlistCache
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import EndReason

logger = logging.getLogger(__name__)

__all__ = [
    "handle_media_socket",
    "AUDIO_SUBDIR",
    "CLIP_SAMPLE_RATE",
    "IMPLEMENTED_TTS",
    "load_clip_pcm",
    "looks_unconfigured",
    "unservable_reason",
]

#: Looks like a value nobody has replaced yet. Not a refusal — none of the shipped
#: bundles has a real voice id, so refusing would refuse every call — but worth saying
#: out loud once at boot rather than discovering it on a call.
_PLACEHOLDER_PREFIX = "PLACEHOLDER"

#: Speech providers this pipeline can actually construct. A persona bundle naming anything
#: else is refused at call setup rather than voiced by the wrong provider: the service is
#: chosen here, not by the bundle, so an unlisted ``voice.tts`` would silently hand this
#: provider another one's voice id — a call with no working voice, which is harder to
#: notice than a refusal and impossible to distinguish from a bad voice id.
IMPLEMENTED_TTS: frozenset[str] = frozenset({"cartesia"})


def unservable_reason(voice: VoiceConfig) -> str | None:
    """Why a persona bundle cannot be given a voice, or ``None`` if it can.

    Pure and dependency-free on purpose: it is checked twice — once at boot, where a bad
    bundle should stop the deployment, and once at call setup as defence in depth — and
    both need to work without the media extra installed.
    """
    if voice.tts not in IMPLEMENTED_TTS:
        return (
            f"written for tts={voice.tts!r}, which this pipeline does not implement "
            f"(has: {', '.join(sorted(IMPLEMENTED_TTS))})"
        )
    if not voice.voice_id.strip():
        return "names no voice_id, so the synthesiser has no voice to speak with"
    return None


def looks_unconfigured(voice: VoiceConfig) -> bool:
    """Whether ``voice_id`` is still a placeholder from the checked-in bundle."""
    return voice.voice_id.strip().upper().startswith(_PLACEHOLDER_PREFIX)

#: Twilio's media streams are 8 kHz. The serializer converts our PCM to μ-law on the way
#: out, so sound-pack clips only have to match this rate — and if they do, no resampling
#: happens on a live call at all. Render them at 8 kHz mono 16-bit.
CLIP_SAMPLE_RATE = 8000

#: Sound-pack clips live beside the persona that speaks them.
AUDIO_SUBDIR = "audio"

_TICK_SECONDS = 1.0
"""How often the caps are re-evaluated. One second is well inside the tolerance of a
sixty-second dead-air window and a ninety-minute hard cap, and costs nothing."""


def _import_pipecat() -> Any:
    """Import the realtime stack, or explain precisely what is missing.

    Optional by design: the safety-critical suite runs with none of it installed.
    """
    try:
        from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer  # noqa: F401
    except ImportError:
        SoundfileMixer = None  # type: ignore[assignment]

    try:
        from pipecat.frames.frames import (
            BotStoppedSpeakingFrame,
            InputDTMFFrame,
            OutputAudioRawFrame,
            TranscriptionFrame,
            TTSSpeakFrame,
            UserStartedSpeakingFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline

        # Not `pipecat.pipeline.task`/`.runner`: those modules are deprecated aliases,
        # and pyproject turns this package's DeprecationWarnings into errors.
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker
        from pipecat.processors.frame_processor import FrameProcessor
        from pipecat.serializers.twilio import TwilioFrameSerializer
        from pipecat.services.cartesia.tts import CartesiaTTSService
        from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
        from pipecat.workers.runner import WorkerRunner
    except ImportError as exc:
        raise RuntimeError(
            "the realtime media stack is not installed: "
            f"{exc}. Run `pip install -e '.[media]'`"
        ) from exc

    return SimpleNamespace(
        BotStoppedSpeakingFrame=BotStoppedSpeakingFrame,
        InputDTMFFrame=InputDTMFFrame,
        OutputAudioRawFrame=OutputAudioRawFrame,
        TranscriptionFrame=TranscriptionFrame,
        TTSSpeakFrame=TTSSpeakFrame,
        UserStartedSpeakingFrame=UserStartedSpeakingFrame,
        Pipeline=Pipeline,
        WorkerRunner=WorkerRunner,
        PipelineParams=PipelineParams,
        PipelineWorker=PipelineWorker,
        FrameProcessor=FrameProcessor,
        TwilioFrameSerializer=TwilioFrameSerializer,
        CartesiaTTSService=CartesiaTTSService,
        DeepgramFluxSTTService=DeepgramFluxSTTService,
        FastAPIWebsocketParams=FastAPIWebsocketParams,
        FastAPIWebsocketTransport=FastAPIWebsocketTransport,
        SoundfileMixer=SoundfileMixer,
    )


# ---------------------------------------------------------------------------
# Sound pack
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipAudio:
    """Decoded PCM ready to hand straight to the transport."""

    pcm: bytes
    sample_rate: int


def load_clip_pcm(persona_id: str, clip: str) -> ClipAudio | None:
    """Read one sound-pack clip, or return ``None`` if it cannot be used.

    Never raises: a missing or wrongly-encoded filler costs a beat of silence the caller
    reads as an old woman gathering her thoughts, where raising would cost the call. The
    strict encoding keeps resampling off the live path — see :data:`CLIP_SAMPLE_RATE`.
    """
    path = PERSONA_DIR / persona_id / AUDIO_SUBDIR / clip
    if not path.is_file():
        logger.warning("sound-pack clip missing: %s", path)
        return None

    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, OSError):
        logger.exception("sound-pack clip unreadable: %s", path)
        return None

    if (channels, width, rate) != (1, 2, CLIP_SAMPLE_RATE):
        logger.error(
            "sound-pack clip %s is %d-channel %d-bit %dHz; expected mono 16-bit %dHz",
            path,
            channels,
            width * 8,
            rate,
            CLIP_SAMPLE_RATE,
        )
        return None

    return ClipAudio(pcm=frames, sample_rate=rate)


def _ambient_path(persona: Persona) -> Path | None:
    if not persona.ambient:
        return None
    path = PERSONA_DIR / persona.id / AUDIO_SUBDIR / persona.ambient
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------


async def handle_media_socket(
    websocket: Any,
    *,
    settings: Settings,
    registry: CallRegistry,
    allowlist: AllowlistCache | None = None,
) -> None:
    """Serve one Twilio media stream for the length of one call.

    The path token has already been checked by the caller (see
    :mod:`ssscammers.agent.webhooks`). This adds the second half of that check: the
    ``start`` message must name a call this process actually admitted, so a party who
    learns the token still cannot open a conversation out of nothing.
    """
    pc = _import_pipecat()

    await websocket.accept()

    start = await _read_start_message(websocket)
    if start is None:
        await websocket.close(code=1002)
        return

    stream_sid = start.get("streamSid", "")
    call_sid = start.get("callSid", "")
    custom = start.get("customParameters") or {}

    call = registry.attach_stream(call_sid)
    if call is None:
        # Either we never answered this call, or it already has a stream. Both mean the
        # socket is not what it claims to be.
        logger.warning("refusing media stream for unadmitted call %r", call_sid)
        await websocket.close(code=1008)
        return

    try:
        await _serve_call(
            websocket, pc=pc, settings=settings, registry=registry,
            allowlist=allowlist, call=call, call_sid=call_sid,
            stream_sid=stream_sid, custom=custom,
        )
    except Exception:  # noqa: BLE001 - the slot is already claimed; never leak it
        # `attach_stream` has already marked this call as streaming. Anything that
        # escapes from here leaves it that way forever: the outcome is never reported,
        # `/twilio/after-stream` sees `final_phase=None`, and the slot is held until the
        # reaper takes it. A bad DEFAULT_PERSONA raises FileNotFoundError, which is not a
        # RuntimeError, so this deliberately catches everything.
        logger.exception("media session for %s failed", call_sid)
        registry.finish(call_sid, end_reason=EndReason.PIPELINE_ERROR)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)


async def _serve_call(
    websocket: Any,
    *,
    pc: Any,
    settings: Settings,
    registry: CallRegistry,
    allowlist: AllowlistCache | None,
    call: Any,
    call_sid: str,
    stream_sid: str,
    custom: dict[str, Any],
) -> None:
    """Build and run the pipeline for one admitted call."""
    try:
        settings.require("deepgram_api_key", "cartesia_api_key")
    except RuntimeError as exc:
        # The socket is already accepted, so there is no status code to return; close it
        # deliberately rather than letting the exception surface as a 500.
        logger.error("cannot serve call %s: %s", call_sid, exc)
        registry.finish(call_sid, end_reason=EndReason.PIPELINE_ERROR)
        await websocket.close(code=1011)
        return

    conversation = build_conversation(
        settings,
        call_sid=call_sid,
        caller_number=call.caller_number,
        entry_path=call.entry_path,
        persona_id=call.persona_id,
        # Without this the director builds an empty cache, `ctx.allowlisted` is always
        # False, and `Trigger.ALLOWLISTED` — a designed G-11 exit — cannot fire on a live
        # call. The webhook's pre-answer check would be the only allowlist in the system,
        # and it cannot see a number that becomes known-good mid-call.
        allowlist=allowlist,
    )
    persona = conversation.director.persona
    unservable = unservable_reason(persona.voice)
    if unservable is not None:
        # Defence in depth: `create_app` refuses to boot on the configured persona, so
        # reaching this means the bundle changed under a running process. Refused rather
        # than voiced by the wrong provider, since the TTS service is chosen below and
        # continuing would hand Cartesia another provider's voice id.
        logger.error("persona %s %s; refusing the call rather than voicing it wrongly",
                     persona.id, unservable)
        registry.finish(call_sid, end_reason=EndReason.PIPELINE_ERROR)
        await websocket.close(code=1011)
        return

    # Built from the persona the conversation already loaded: loading the bundle twice
    # would read three playbooks and a fact sheet off disk again for no reason, and the
    # prompt sits behind a cache breakpoint where a byte of difference costs the call.
    conversation.brain = ClaudeBrain(
        system_prompt=persona.system_prompt(),
        api_key=settings.anthropic_api_key or None,
        **model_overrides(settings),
    )

    serializer = pc.TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        # No credentials passed on purpose: with auto_hang_up disabled the serializer
        # never calls the REST API, and it validates that those two facts agree.
        params=pc.TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )

    transport = pc.FastAPIWebsocketTransport(
        websocket=websocket,
        params=pc.FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_out_mixer=_build_mixer(pc, persona),
            serializer=serializer,
            # Twilio holds the socket open long after a call is over if we let it.
            session_timeout=int(settings.hard_call_cap_seconds) + 60,
        ),
    )

    stt = pc.DeepgramFluxSTTService(
        api_key=settings.deepgram_api_key,
        # Turn detection lives here rather than in a VAD; see the module docstring.
        # `Settings`, not the deprecated `InputParams`.
        settings=pc.DeepgramFluxSTTService.Settings(eot_threshold=0.7),
        # Barge-in is off until a turn can be safely cancelled mid-flight. Flux
        # broadcasts an interruption on every start-of-turn by default, and Pipecat
        # handles that by cancelling the task a turn is running on — which for us is the
        # task inside `perform`. A caller who keeps talking while the disclosure plays
        # would have their `HangUp` cancelled: never released, never hung up, left on an
        # open line. That is the exact failure G-11 exists to prevent, so the persona
        # talks over interruptions (which is in character) until `perform` is made
        # cancellation-safe and idempotent.
        should_interrupt=False,
    )
    tts = pc.CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        voice_id=persona.voice.voice_id,
        model=persona.voice.model,
    )

    def report_outcome() -> None:
        """Write the call's outcome to the registry.

        Called the instant the conversation reaches a terminal phase, because closing the
        socket is what makes Twilio request ``/twilio/after-stream`` — and that endpoint
        reads exactly this. Writing it in the socket's teardown instead put the write in a
        race with a network round trip, and losing that race silently turns the voicemail
        a real person was just promised into a hangup.
        """
        registry.finish(
            call_sid,
            final_phase=conversation.final_phase,
            end_reason=conversation.end_reason or EndReason.PIPELINE_ERROR,
            voicemail_promised=conversation.offered_voicemail,
        )

    director_processor = _build_director_processor(pc, conversation, on_ended=report_outcome)
    pipeline = pc.Pipeline([transport.input(), stt, director_processor, tts, transport.output()])
    task = pc.PipelineWorker(
        pipeline,
        params=pc.PipelineParams(
            audio_in_sample_rate=CLIP_SAMPLE_RATE,
            audio_out_sample_rate=CLIP_SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )
    director_processor.bind_task(task)

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport: Any, _client: Any) -> None:
        # Queued on the task rather than pushed from the processor. This fires from the
        # input transport's start(), by which point `StartFrame` has only been *enqueued*
        # upstream — a frame pushed from mid-pipeline now hits `_check_started`, which
        # logs an error and drops it. The caller would hear silence while the transcript
        # recorded a greeting that was never spoken.
        await task.queue_frames(director_processor.frames_for(await conversation.open()))

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport: Any, _client: Any) -> None:
        await conversation.caller_hung_up()
        await task.cancel(reason="caller hung up")

    @transport.event_handler("on_session_timeout")
    async def _on_session_timeout(_transport: Any, _client: Any) -> None:
        # Setting `session_timeout` only starts a timer; without a handler the event
        # fires into nothing and the socket is never closed. Twilio holds a socket open
        # long after a call is over, and a leaked socket holds a registry slot with it.
        logger.warning("media session timed out for %s; closing", call_sid)
        await conversation.caller_hung_up()
        await task.cancel(reason="session timeout")

    ticker = asyncio.create_task(_tick_forever(conversation, director_processor, task))
    try:
        await pc.WorkerRunner(handle_sigint=False).run(task)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        # Backstop: covers a socket that died without the conversation ever terminating
        # (a dropped carrier leg, a crash). `finish` keeps whatever was already reported.
        report_outcome()
        logger.info(
            "media stream for %s closed in %s (%s) after %.0fs",
            call_sid,
            conversation.final_phase.value,
            conversation.end_reason,
            conversation.elapsed_seconds,
        )
        # Twilio's <Parameter> values are advisory: anything reaching this socket can
        # claim them, so they are logged for debugging a mismatch and never trusted.
        # The registry is authoritative.
        if custom:
            logger.debug("stream custom parameters (not trusted): %s", custom)


async def _read_start_message(websocket: Any) -> dict[str, Any] | None:
    """Consume Twilio's handshake and return the ``start`` payload.

    Twilio sends ``connected`` and then ``start``; the stream and call SIDs only exist
    in the second. Bounded because an unauthenticated peer that never sends ``start``
    must not hold a socket forever.
    """
    try:
        async with asyncio.timeout(10):
            while True:
                raw = await websocket.receive_text()
                message = json.loads(raw)
                if message.get("event") == "start":
                    return message.get("start") or {}
                if message.get("event") not in ("connected", "mark", "media"):
                    logger.warning("unexpected media-stream event %r", message.get("event"))
    except TimeoutError:
        logger.warning("media stream sent no start message within 10s")
    except (json.JSONDecodeError, KeyError):
        logger.exception("malformed media-stream handshake")
    except Exception:  # noqa: BLE001 - a socket that dies here is not an error
        logger.info("media stream closed during handshake", exc_info=True)
    return None


def _build_mixer(pc: Any, persona: Persona) -> Any:
    """Background ambience, if this persona has any and the asset is present.

    Free believability, but decoration: a missing file must not stop a call.
    """
    path = _ambient_path(persona)
    if path is None or pc.SoundfileMixer is None:
        if persona.ambient:
            logger.warning("ambient bed unavailable for %s; running without it", persona.id)
        return None
    return pc.SoundfileMixer(
        sound_files={"ambient": str(path)}, default_sound="ambient", volume=0.18, loop=True
    )


async def _tick_forever(conversation: Conversation, processor: Any, task: Any) -> None:
    """Evaluate the caps on a timer, since G-14 and G-16 exist precisely for calls where
    nothing is happening and so cannot be driven by caller audio.

    Every iteration is guarded because this task *is* the enforcement: an unsupervised
    task that raises once — pushing a frame into a transport that has begun tearing down
    is enough — dies silently and takes both caps with it for the rest of the call.
    """
    while not conversation.ended:
        await asyncio.sleep(_TICK_SECONDS)
        try:
            actions = [action async for action in conversation.tick()]
            if actions:
                await processor.perform(actions)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the caps must outlive any single tick
            logger.exception("cap timer tick failed; continuing to enforce")


# ---------------------------------------------------------------------------
# The one processor
# ---------------------------------------------------------------------------


def _build_director_processor(
    pc: Any, conversation: Conversation, *, on_ended: Callable[[], None] | None = None
) -> Any:
    """Construct the frame processor that drives the conversation.

    Built inside a function because it has to subclass a Pipecat type, and Pipecat is an
    optional install — a module-level subclass would make importing this file require
    the media extra.
    """

    class _DirectorProcessor(pc.FrameProcessor):  # type: ignore[misc, name-defined]
        """Caller transcripts in, persona speech out."""

        def __init__(self) -> None:
            super().__init__()
            self._conversation = conversation
            self._task: Any = None
            # Turns come from two tasks — a transcription frame and the cap ticker —
            # and `perform` awaits inside itself. Without this, a tick's hangup lands
            # between two sentences of an in-flight reply and its bookkeeping resets the
            # dead-air clock mid-turn.
            self._turn_lock = asyncio.Lock()

        def bind_task(self, task: Any) -> None:
            """The task is how a turn ends a call, so it is set after construction."""
            self._task = task

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, pc.TranscriptionFrame):
                text = (frame.text or "").strip()
                if text:
                    # Consumed, not forwarded: downstream is the synthesiser, and a
                    # caller's words must never reach it directly.
                    await self.perform_stream(self._conversation.respond(text))
                return

            if isinstance(frame, pc.UserStartedSpeakingFrame):
                self._conversation.note_caller_audio()
            elif isinstance(frame, pc.BotStoppedSpeakingFrame):
                # The authoritative "our audio has stopped" signal. `perform` also
                # marks it, but that fires when frames are *queued*; a thirty-second
                # ramble would otherwise start the dead-air clock thirty seconds early.
                self._conversation.note_agent_audio_finished()
            elif isinstance(frame, pc.InputDTMFFrame):
                self._conversation.note_dtmf(str(getattr(frame.button, "value", frame.button)))

            await self.push_frame(frame, direction)

        def _clip_frame(self, clip: str) -> Any | None:
            """One clip as an audio frame, or ``None`` if the asset is unusable."""
            audio = load_clip_pcm(self._conversation.director.persona.id, clip)
            if audio is None:
                return None
            return pc.OutputAudioRawFrame(
                audio=audio.pcm, sample_rate=audio.sample_rate, num_channels=1
            )

        def frames_for(self, actions: list[Action]) -> list[Any]:
            """The frames for actions that carry no timing.

            Used for the opening greeting, which has to be queued at the source of the
            pipeline. Pauses cannot be expressed as a frame, so this refuses them rather
            than silently dropping the wait.

            ``opening()`` returns only ``Say`` today, so the other two branches are not
            reached. They are kept deliberately: the ``else`` is what makes adding a
            ``Pause`` to the greeting fail loudly instead of losing the wait silently, and
            that hazard is the whole reason this method exists rather than a bare
            ``TTSSpeakFrame`` at the call site.
            """
            frames: list[Any] = []
            for action in actions:
                if isinstance(action, Say):
                    frames.append(pc.TTSSpeakFrame(action.text))
                elif isinstance(action, PlayClip):
                    frame = self._clip_frame(action.clip)
                    if frame is None:
                        # Logged, not dropped quietly: silent loss is the exact hazard the
                        # `else` below is kept for, and this branch was committing it.
                        logger.warning(
                            "clip %s missing from the opening; it will not be heard",
                            action.clip,
                        )
                    else:
                        frames.append(frame)
                else:
                    raise ValueError(f"{type(action).__name__} cannot be queued as a frame")
            return frames

        async def perform(self, actions: list[Action]) -> None:
            """Turn a finished list of actions into frames, in order."""

            async def once() -> AsyncIterator[Action]:
                for action in actions:
                    yield action

            await self.perform_stream(once())

        async def perform_stream(self, actions: AsyncIterator[Action]) -> None:
            """Execute actions *as they are produced*.

            Draining the turn into a list first — what an innocent
            ``[a async for a in ...]`` does — undoes the two things the conversation layer
            works hardest for: the filler plays *after* generation instead of covering it,
            and synthesis of sentence one no longer overlaps generation of sentence two.
            Both are invisible in tests and plainly audible on a call.

            Serialised: see ``_turn_lock``.
            """
            async with self._turn_lock:
                async for action in actions:
                    if isinstance(action, Say):
                        await self.push_frame(pc.TTSSpeakFrame(action.text))
                    elif isinstance(action, PlayClip):
                        await self._play(action)
                    elif isinstance(action, Pause):
                        await asyncio.sleep(action.seconds)
                    elif isinstance(action, HangUp):
                        await self._end_call()
                self._conversation.note_agent_audio_finished()

        async def _end_call(self) -> None:
            """Stop the pipeline once what is already queued has been spoken.

            `stop_when_done()` drains first, which matters because every hangup that
            carries speech carries a *fixed script* — the disclosure, the victim warning,
            or "hang up and dial 911". A mid-pipeline `EndFrame` races the synthesiser and
            can cut off the three utterances G-11 and G-12 exist to guarantee.

            This ends the *stream*, not the call: Twilio then resumes the TwiML document
            at /twilio/after-stream, which may still owe this caller a voicemail.
            """
            if on_ended is not None:
                on_ended()
            if self._task is not None:
                await self._task.stop_when_done()

        async def _play(self, action: PlayClip) -> None:
            frame = self._clip_frame(action.clip)
            if frame is None:
                if action.kind == "hold":
                    # The Pause that follows is the part that wastes their time, so the
                    # stall still works — but the caller gets silence instead of a kettle,
                    # which is a believability problem worth seeing in the log.
                    logger.warning("hold clip %s missing; the caller hears silence", action.clip)
                return
            await self.push_frame(frame)

    return _DirectorProcessor()

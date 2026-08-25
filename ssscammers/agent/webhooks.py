"""The public edge: what Twilio talks to.

Two endpoints face the internet — this app and the media WebSocket — and everything
else in the deployment is on the tailnet. That makes this file the security boundary,
so it is written to fail closed in every direction:

* **An unsigned request is not Twilio.** Every webhook validates
  ``X-Twilio-Signature`` against the URL Twilio was configured with, not against the
  URL the request claims to have arrived at. A missing auth token does not disable
  validation — it refuses service, because the alternative is a public endpoint that
  answers anyone.
* **A call we did not answer gets no media socket.** The path token proves the
  connecting party knows a secret; the registry proves the call is one this process
  actually admitted.
* **An inbound-only system that sees an outbound call has been compromised.** G-1 is a
  property of the Twilio subaccount, but if a non-inbound call ever reaches here we
  reject it and log loudly rather than assuming the field is noise.

## Why the call ends where it does

The pipeline hangs up by closing its WebSocket. Twilio then resumes the TwiML document
and requests ``/twilio/after-stream``, which is the only place that knows whether the
persona just promised someone a voicemail. That indirection is deliberate: the
disclosure script says "I'm going to put you through to voicemail now", and a system
that says that and then drops the line is lying to the exact person it is meant to
protect.

It also means the Twilio serializer must be built with ``auto_hang_up=False``, or the
document is skipped entirely — see :mod:`ssscammers.agent.media`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, status
from twilio.request_validator import RequestValidator

from ssscammers.agent import twiml
from ssscammers.agent.daily_ledger import DailyLedger
from ssscammers.agent.persona import load_persona
from ssscammers.agent.registry import CallRegistry
from ssscammers.agent.triage import AllowlistCache
from ssscammers.shared.config import Settings, load_settings
from ssscammers.shared.enums import EntryPath

logger = logging.getLogger(__name__)

__all__ = [
    "create_app",
    "CallRecorder",
    "TwilioRestRecorder",
    "NullRecorder",
    "recording_endpoint",
]

#: Twilio's own value for a call that arrived from the PSTN. Anything else on an
#: inbound-only line is a misconfiguration at best.
_INBOUND_DIRECTION = "inbound"

#: Statuses that mean the leg is down and the slot can be freed.
_FINAL_CALL_STATUSES = frozenset({"completed", "failed", "busy", "no-answer", "canceled"})

#: Placeholder values from ``.env.example``. Booting with one of these in place is a
#: deployment mistake that would otherwise only surface as an open endpoint.
_PLACEHOLDER_SECRETS = frozenset({"change-me", ""})

_RECORDING_START_TIMEOUT_SECONDS = 1.5
"""How long the webhook will wait for Twilio to start recording.

Recording is started *before* the notice plays, so the consent notice is inside the
resulting audio — the recording is the evidence that the notice happened, and evidence
that starts after the thing it is meant to prove is worth much less. The cost is a
short pause the caller hears as ordinary call setup, and it is bounded here so a slow
Twilio API delays a call rather than failing it.
"""


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class CallRecorder(Protocol):
    """Starts dual-channel recording on a call that is already up.

    Separate channels for caller and persona, so diarization is exact rather than
    inferred — which makes "how long did a human spend on this" a measurement.
    """

    async def start_dual_channel(
        self, call_sid: str, *, status_callback_url: str = ""
    ) -> str | None:
        """Return the recording SID, or ``None`` if recording could not be started."""
        ...


class NullRecorder:
    """Records nothing. The default in tests and in any run without Twilio creds."""

    async def start_dual_channel(
        self, call_sid: str, *, status_callback_url: str = ""
    ) -> str | None:
        logger.info("recording not configured; call %s is not being recorded", call_sid)
        return None


def recording_endpoint(account_sid: str, call_sid: str) -> str:
    """The REST endpoint that starts recording on a call already in progress.

    A named function rather than an inline f-string so G-1's test can assert the shape of
    the only Twilio endpoint this system posts to. The distinction that matters is visible
    here: the call SID is a *path segment*, so this can only act on a call that already
    exists. ``/Calls.json`` — which creates one — takes no call SID and is forbidden.
    """
    return (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/Calls/{call_sid}/Recordings.json"
    )


class TwilioRestRecorder:
    """Starts a recording over Twilio's REST API.

    Not the Twilio SDK client: it is synchronous, and a blocking HTTP call on this event
    loop is audible on every call in flight.

    This is the only outward HTTP request the system makes, and it acts on a call the
    caller already opened — it cannot originate contact (G-1).
    """

    def __init__(self, account_sid: str, auth_token: str, *, timeout: float = 5.0) -> None:
        if not account_sid or not auth_token:
            raise ValueError("TwilioRestRecorder requires an account SID and auth token")
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._timeout = timeout
        self._transport: Any = None
        """Injected in tests so the request this builds can be inspected without a
        network. Left ``None`` in production, which is httpx's own default."""

    async def start_dual_channel(
        self, call_sid: str, *, status_callback_url: str = ""
    ) -> str | None:
        import httpx

        url = recording_endpoint(self._account_sid, call_sid)
        form: dict[str, str] = {"RecordingChannels": "dual", "RecordingTrack": "both"}
        if status_callback_url:
            form["RecordingStatusCallback"] = status_callback_url
            form["RecordingStatusCallbackMethod"] = "POST"

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                url, data=form, auth=(self._account_sid, self._auth_token)
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload.get("sid")


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


class _SignatureChecker:
    """Validates ``X-Twilio-Signature`` over the URL Twilio was configured with.

    The signed URL must be reconstructed rather than read from the request. Behind
    Caddy the request arrives as plain HTTP on an internal hostname, so
    ``request.url`` is not what Twilio signed; and trusting ``X-Forwarded-*`` would let
    a caller choose the string their own signature is checked against.
    """

    def __init__(self, *, auth_token: str, public_base_url: str, enabled: bool = True) -> None:
        self._validator = RequestValidator(auth_token) if auth_token else None
        self._base = public_base_url.rstrip("/")
        self._enabled = enabled

    async def form(self, request: Request) -> dict[str, str]:
        """Return the validated form parameters, or raise.

        Parsed here rather than through Starlette's ``request.form()``: Twilio only sends
        ``application/x-www-form-urlencoded``, so the multipart dependency buys nothing,
        and blank values must survive — Twilio signs ``ForwardedFrom=`` as it sends it, so
        a parser that drops empty fields rejects every forwarded call.
        """
        params = _parse_form(await request.body())

        if not self._enabled:
            return params

        if self._validator is None:
            # No token means we cannot tell Twilio from anyone else. Serving the
            # request anyway would turn every endpoint here into an open one.
            logger.error("refusing webhook: TWILIO_AUTH_TOKEN is not configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="webhook signature validation is not configured",
            )

        signature = request.headers.get("X-Twilio-Signature", "")
        if not self._validator.validate(self._signed_url(request), params, signature):
            logger.warning(
                "rejecting webhook with bad signature: path=%s from=%s",
                request.url.path,
                params.get("From", "?"),
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad signature")

        return params

    def _signed_url(self, request: Request) -> str:
        url = f"{self._base}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        return url


def _parse_form(body: bytes) -> dict[str, str]:
    """Decode a urlencoded Twilio webhook body.

    ``keep_blank_values`` is load-bearing — see :meth:`_SignatureChecker.form`.
    """
    return dict(parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True))


def _absolute(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


def create_app(
    *,
    settings: Settings | None = None,
    registry: CallRegistry | None = None,
    allowlist: AllowlistCache | None = None,
    recorder: CallRecorder | None = None,
    validate_signatures: bool = True,
) -> FastAPI:
    """Build the webhook app.

    Everything the handlers need is passed in rather than read from module state, so a
    test can drive a real request through the real routing. Nothing here is a singleton.

    Raises:
        RuntimeError: If the media-stream path token is missing or is still the
            ``.env.example`` placeholder (that token is the only thing standing between the
            public internet and the media socket), if ``NOTICE_AUDIO_URL`` is malformed, or
            if ``DEFAULT_PERSONA`` names a bundle the pipeline cannot voice.
    """
    settings = settings or load_settings()
    registry = registry or CallRegistry(
        max_concurrent=settings.max_concurrent_calls,
        ledger=DailyLedger(
            path=Path(settings.daily_ledger_path),
            minutes_cap=settings.daily_minutes_cap,
            spend_cap_usd=settings.daily_spend_cap_usd,
            repeat_caller_cap=settings.repeat_caller_daily_cap,
            usd_per_minute=settings.estimated_usd_per_call_minute,
            per_call_cap_seconds=float(settings.hard_call_cap_seconds),
        ),
        # Derived, not restated. Raising the hard cap without this would make the reaper
        # delete calls that are still up, and a reaped call is one whose promised
        # voicemail turns into a hangup.
        stale_after_seconds=float(settings.hard_call_cap_seconds) + 300.0,
    )
    allowlist = allowlist if allowlist is not None else AllowlistCache()
    recorder = recorder or _default_recorder(settings)

    if settings.media_stream_path_token in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "MEDIA_STREAM_PATH_TOKEN is unset or still the .env.example placeholder; "
            "the media socket would be reachable by anyone (see docs/secrets.md)"
        )
    if settings.notice_audio_url and not settings.notice_audio_url.startswith(
        ("https://", "http://")
    ):
        # A `<Play>` Twilio cannot fetch is logged by Twilio and *skipped*: the document
        # continues straight to `<Connect><Stream>` and the caller is recorded with no
        # notice at all. The spoken fallback only covers the case where the URL is empty,
        # so a malformed one is worse than none — refuse to boot rather than answer a
        # call we cannot lawfully hold (G-2).
        raise RuntimeError(
            f"NOTICE_AUDIO_URL must be an http(s) URL, got {settings.notice_audio_url!r}; "
            "leave it empty to use the spoken fallback instead"
        )
    _require_a_voiceable_persona(settings.default_persona)
    if validate_signatures:
        settings.require("public_base_url")

    checker = _SignatureChecker(
        auth_token=settings.twilio_auth_token,
        public_base_url=settings.public_base_url,
        enabled=validate_signatures,
    )

    app = FastAPI(title="ssscammers agent", docs_url=None, redoc_url=None, openapi_url=None)

    after_stream_url = _absolute(settings.public_base_url, "/twilio/after-stream")
    voicemail_done_url = _absolute(settings.public_base_url, "/twilio/voicemail-complete")
    recording_status_url = _absolute(settings.public_base_url, "/twilio/recording-status")
    # A separate path, not a shared one: both the dual-channel bait recording and the
    # voicemail `<Record>` report here, and a single handler overwrote the call's
    # recording SID with the voicemail's — replacing the pointer to the scam audio with
    # a pointer to a real person's private message.
    voicemail_recording_url = _absolute(
        settings.public_base_url, "/twilio/voicemail-recording-status"
    )

    def _twiml(document: str) -> Response:
        return Response(content=document, media_type="application/xml")

    def _voicemail() -> Response:
        return _twiml(
            twiml.voicemail(
                action_url=voicemail_done_url,
                max_length_seconds=settings.voicemail_max_seconds,
                recording_status_callback_url=voicemail_recording_url,
            )
        )

    # -- the call answers here ------------------------------------------------

    @app.post("/twilio/voice")
    async def voice(request: Request) -> Response:
        params = await checker.form(request)

        call_sid = params.get("CallSid", "")
        caller_number = params.get("From", "")
        if not call_sid:
            raise HTTPException(status_code=400, detail="missing CallSid")

        direction = params.get("Direction", _INBOUND_DIRECTION)
        if direction != _INBOUND_DIRECTION:
            # G-1. Outbound is disabled at the subaccount, so this should be
            # unreachable; if it is reachable, something is very wrong and the safe
            # move is to touch the call as little as possible.
            logger.error(
                "G-1: refusing non-inbound call direction=%r sid=%s — outbound must be "
                "impossible on this account",
                direction,
                call_sid,
            )
            return _twiml(twiml.reject(reason="rejected"))

        # Known-bad numbers never get answered: an unanswered call is neither recorded
        # nor billed.
        if caller_number and allowlist.is_blocked(caller_number):
            logger.info("rejecting blocked caller %s", caller_number)
            return _twiml(twiml.reject(reason="busy"))

        # G-20. Switched off means "take messages", never "drop calls" — a real person
        # must still be able to reach the owner while the honeypot is disabled.
        if not registry.enabled:
            logger.info("system disabled; routing %s to voicemail", call_sid)
            return _voicemail()

        # Someone we already know is real. Not baited, and not recorded either: there
        # is no reason to hold audio of a neighbour who rang the wrong line.
        if caller_number and allowlist.is_allowlisted(caller_number):
            logger.info("allowlisted caller %s routed to voicemail", caller_number)
            return _voicemail()

        entry_path = (
            EntryPath.CONDITIONAL_FORWARD
            if params.get("ForwardedFrom")
            else EntryPath.DIRECT
        )

        admission = registry.reserve(
            call_sid=call_sid,
            caller_number=caller_number,
            entry_path=entry_path,
            persona_id=settings.default_persona,
        )
        if not admission.admitted:
            # G-15. The line is full. Overflow goes to voicemail rather than being
            # rejected, for the same reason the kill switch does.
            logger.warning(
                "%s; %s routed to voicemail (at_capacity=%s, %d of %d active)",
                f"daily cap reached: {admission.capped}"
                if admission.capped
                else "the line is full",
                call_sid,
                admission.at_capacity,
                registry.active_count,
                registry.max_concurrent,
            )
            return _voicemail()

        await _start_recording(recorder, registry, call_sid, recording_status_url)

        return _twiml(
            twiml.engage(
                stream_url=settings.media_stream_url,
                after_stream_url=after_stream_url,
                notice_audio_url=settings.notice_audio_url,
                parameters={
                    "call_sid": call_sid,
                    "persona_id": settings.default_persona,
                    "entry_path": entry_path.value,
                },
            )
        )

    # -- the stream closed; decide what the caller gets next ------------------

    @app.post("/twilio/after-stream")
    async def after_stream(request: Request) -> Response:
        params = await checker.form(request)
        call_sid = params.get("CallSid", "")
        call = registry.get(call_sid)

        if call is None:
            # Unknown call: do not start recording a voicemail for something this
            # process has no record of.
            logger.info("after-stream for unknown call %s; hanging up", call_sid)
            return _twiml(twiml.hangup())

        if call.voicemail_promised:
            logger.info(
                "call %s ended in %s; honouring the voicemail we promised",
                call_sid,
                call.final_phase,
            )
            return _voicemail()

        logger.info(
            "call %s ended in %s (%s); hanging up",
            call_sid,
            call.final_phase,
            call.end_reason,
        )
        return _twiml(twiml.hangup())

    @app.post("/twilio/voicemail-complete")
    async def voicemail_complete(request: Request) -> Response:
        params = await checker.form(request)
        logger.info(
            "voicemail recorded for %s (%ss)",
            params.get("CallSid", "?"),
            params.get("RecordingDuration", "?"),
        )
        return _twiml(twiml.hangup())

    # -- lifecycle callbacks --------------------------------------------------

    @app.post("/twilio/status")
    async def call_status(request: Request) -> Response:
        params = await checker.form(request)
        call_sid = params.get("CallSid", "")
        call_status_value = params.get("CallStatus", "")

        if call_status_value in _FINAL_CALL_STATUSES:
            released = registry.release(call_sid)
            logger.info(
                "call %s finished status=%s duration=%ss phase=%s reason=%s",
                call_sid,
                call_status_value,
                params.get("CallDuration", "?"),
                released.final_phase if released else None,
                released.end_reason if released else None,
            )
        return Response(status_code=204)

    @app.post("/twilio/voicemail-recording-status")
    async def voicemail_recording_status(request: Request) -> Response:
        params = await checker.form(request)
        # Deliberately not stored against the call: a voicemail is a real person's
        # message, not part of the bait archive, and the declared retention policy for
        # the two differs (see `retention.legit_audio_days` in
        # db/migrations/001_initial.sql — not yet enforced by any worker).
        logger.info(
            "voicemail recording %s for call %s: status=%s",
            params.get("RecordingSid", "?"),
            params.get("CallSid", "?"),
            params.get("RecordingStatus", "?"),
        )
        return Response(status_code=204)

    @app.post("/twilio/recording-status")
    async def recording_status(request: Request) -> Response:
        params = await checker.form(request)
        call_sid = params.get("CallSid", "")
        recording_sid = params.get("RecordingSid", "")
        if call_sid and recording_sid:
            registry.record_recording_sid(call_sid, recording_sid)
        logger.info(
            "recording %s for call %s: status=%s duration=%ss",
            recording_sid or "?",
            call_sid or "?",
            params.get("RecordingStatus", "?"),
            params.get("RecordingDuration", "?"),
        )
        return Response(status_code=204)

    # -- media socket ---------------------------------------------------------

    @app.websocket("/twilio/media/{token}")
    async def media_socket(websocket: WebSocket, token: str) -> None:
        """Hand a validated socket to the pipeline.

        The token is checked here, before anything heavier is imported, so an
        unauthenticated probe costs a string comparison.
        """
        import hmac

        if not hmac.compare_digest(token, settings.media_stream_path_token):
            logger.warning("rejecting media socket with bad path token")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        from ssscammers.agent.media import handle_media_socket

        try:
            await handle_media_socket(
                websocket, settings=settings, registry=registry, allowlist=allowlist
            )
        except RuntimeError as exc:
            # The realtime stack is an optional install, and it reports its absence when
            # it is first used rather than when it is imported. A live call is the wrong
            # moment to discover that, so say precisely what is missing and close the
            # socket rather than letting the exception surface as a bare 500.
            logger.error("media stack unavailable: %s", exc)
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

    # -- ops ------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "enabled": registry.enabled,
            "active_calls": registry.active_count,
            "capacity": registry.max_concurrent,
        }

    return app


def _require_a_voiceable_persona(persona_id: str) -> None:
    """Refuse to boot on a persona this deployment cannot give a voice.

    Static, exactly like the path-token and notice-URL checks above, and therefore
    knowable before a single call arrives. Left to the media socket instead, the failure is
    silent and per-call: every caller is admitted, a dual-channel recording is started, the
    recorded-line notice plays, and only then does the pipeline discover it has no voice
    and hang up. Real people misrouted to the line get recorded and dropped, nothing fails,
    and ``/healthz`` keeps reporting ``ok``.
    """
    from ssscammers.agent.media import looks_unconfigured, unservable_reason

    try:
        persona = load_persona(persona_id)
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"DEFAULT_PERSONA={persona_id!r} cannot be loaded: {exc}") from exc

    reason = unservable_reason(persona.voice)
    if reason is not None:
        raise RuntimeError(
            f"DEFAULT_PERSONA={persona_id!r} {reason}; either point DEFAULT_PERSONA at a "
            f"bundle this pipeline can voice or wire that provider into "
            f"ssscammers.agent.media"
        )
    if looks_unconfigured(persona.voice):
        # Not fatal: none of the checked-in bundles has a real voice id yet, so refusing
        # here would refuse every call. Said once at boot rather than discovered on a call.
        logger.warning(
            "persona %s still has a placeholder voice_id (%s); calls will reach the "
            "synthesiser with an unaudited voice",
            persona_id,
            persona.voice.voice_id,
        )


async def _start_recording(
    recorder: CallRecorder,
    registry: CallRegistry,
    call_sid: str,
    status_callback_url: str,
) -> None:
    """Start dual-channel recording, and never let it fail a call.

    A call with no recording loses the comedy; a caller left in silence because Twilio's
    API was slow is a bug. No version of "could not start recording" is worth dropping a
    live call over.
    """
    try:
        recording_sid = await asyncio.wait_for(
            recorder.start_dual_channel(call_sid, status_callback_url=status_callback_url),
            timeout=_RECORDING_START_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error("recording start timed out for %s; continuing unrecorded", call_sid)
        return
    except Exception:  # noqa: BLE001 - deliberately broad; see docstring
        logger.exception("recording start failed for %s; continuing unrecorded", call_sid)
        return

    if recording_sid:
        registry.record_recording_sid(call_sid, recording_sid)


def _default_recorder(settings: Settings) -> CallRecorder:
    if settings.twilio_account_sid and settings.twilio_auth_token:
        return TwilioRestRecorder(settings.twilio_account_sid, settings.twilio_auth_token)
    logger.warning("Twilio credentials absent; calls will not be recorded")
    return NullRecorder()

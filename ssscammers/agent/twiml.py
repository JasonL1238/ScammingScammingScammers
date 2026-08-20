"""Every TwiML document this system can return, built in one place.

Kept separate from the webhook handlers because the documents are the part worth testing
exhaustively: a routing mistake shows up as a wrong verdict in a log, but a malformed
document is a dropped call and a document missing its notice is a legal problem.

Three properties are enforced here rather than left to the caller:

**G-2 — the recorded-line notice is structural.** Every document that connects a caller to
the persona begins with the notice, and :func:`engage` cannot be called without one. The
model is never asked to say it: it is a pre-rendered clip, and with no clip configured the
fallback is fixed text in a plainly synthetic voice — degraded, never absent.

**G-1 — nothing here can originate contact.** No function emits ``<Dial>``, ``<Sms>``, or
``<Message>``, and ``tests/test_no_outbound.py`` scans the package to keep it that way.

**No verb may fall through to its default action.** ``<Record>`` re-requests the *current*
URL when given no ``action``, so a voicemail document without one loops: record, hang up,
record again. Every terminal verb here names where it goes next.
"""

from __future__ import annotations

from collections.abc import Mapping

from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

__all__ = [
    "NOTICE_TEXT",
    "VOICEMAIL_PROMPT",
    "NOTICE_VOICE",
    "engage",
    "voicemail",
    "reject",
    "hangup",
]

#: Spoken only when no pre-rendered notice clip is configured. Fixed text: the notice
#: is the consent record the whole recording posture rests on (see ``docs/legal.md``),
#: so it is never generated, never persona-flavoured, and never skipped.
NOTICE_TEXT = "Please note, this call is recorded."

#: Read to anyone routed to voicemail — an allowlisted caller, an overflow caller, or a
#: real person the persona just released. Deliberately flat and obviously automated:
#: someone who reached this line by accident should be in no doubt what they reached.
#:
#: It states that the message is recorded, which is load-bearing rather than polite: the
#: kill-switch, allowlist, and overflow paths never call :func:`engage`, so no other notice
#: plays on those calls — yet ``<Record>`` still records. G-2 and ``docs/legal.md`` rest on
#: the caller being told first, so the notice travels with the document that records.
VOICEMAIL_PROMPT = (
    "You've reached an automated call screener. This call is recorded. "
    "Please leave a message after the tone, and it will be read."
)

#: Twilio's own voice, not the persona's. A caller being told the line is recorded, or
#: being sent to voicemail, must not hear the character — the character is the thing we
#: are stepping out of.
NOTICE_VOICE = "Polly.Joanna"

_STREAM_TRACK = "inbound_track"
"""Only the caller's audio is forwarded to the pipeline.

``<Connect><Stream>`` is bidirectional; the track selector governs what Twilio sends *us*.
Both tracks would echo the persona's own speech back into the recogniser, which would
dutifully transcribe it as a caller turn.
"""


def engage(
    *,
    stream_url: str,
    after_stream_url: str,
    notice_audio_url: str = "",
    notice_text: str = NOTICE_TEXT,
    parameters: Mapping[str, str] | None = None,
) -> str:
    """Notice, then hand the leg to the media pipeline.

    Args:
        stream_url: ``wss://`` endpoint the pipeline listens on.
        after_stream_url: Where Twilio goes when the stream closes — the pipeline ends a
            call by closing its socket, so this decides between voicemail and a hangup.
            See :mod:`ssscammers.agent.webhooks`.
        notice_audio_url: Pre-rendered notice clip. Strongly preferred.
        notice_text: Spoken fallback when no clip is configured.
        parameters: Extra key/values delivered to the pipeline in Twilio's ``start``
            message. Values are stringified; ``None`` values are dropped.

    Raises:
        ValueError: If neither a notice clip nor notice text is available, or if the
            stream URL is not a WebSocket URL. Both are G-2/transport invariants, and
            failing here is better than answering a call we cannot legally hold.
    """
    if not notice_audio_url and not notice_text.strip():
        raise ValueError("refusing to build an engage document with no recorded-line notice (G-2)")
    if not stream_url.startswith(("wss://", "ws://")):
        raise ValueError(f"stream url must be a websocket url, got {stream_url!r}")
    if not after_stream_url:
        raise ValueError("after_stream_url is required: <Connect> must not fall through")

    response = VoiceResponse()

    # First verb, always. Everything after this point is on the record.
    if notice_audio_url:
        response.play(notice_audio_url)
    else:
        response.say(notice_text, voice=NOTICE_VOICE)

    stream = Stream(url=stream_url, track=_STREAM_TRACK)
    for key, value in (parameters or {}).items():
        if value is None:
            continue
        stream.parameter(name=key, value=str(value))

    connect = Connect()
    connect.append(stream)
    response.append(connect)

    # Reached when the pipeline closes the socket, which is how the agent hangs up.
    response.redirect(after_stream_url, method="POST")

    return str(response)


def voicemail(
    *,
    action_url: str,
    prompt: str = VOICEMAIL_PROMPT,
    max_length_seconds: int = 120,
    recording_status_callback_url: str = "",
) -> str:
    """Take a message and stop.

    Used for three callers who need the same treatment: someone on the allowlist, someone
    arriving while the line is full, and someone the persona released after triage found a
    real person. None are baited and none hear a character.

    Args:
        action_url: Where Twilio goes when recording finishes. Required — Twilio
            defaults ``<Record>``'s action to the current document, which would answer
            a finished voicemail by starting another one.
    """
    if not action_url:
        raise ValueError("action_url is required: <Record> would otherwise loop")

    response = VoiceResponse()
    response.say(prompt, voice=NOTICE_VOICE)
    response.record(
        action=action_url,
        method="POST",
        max_length=max_length_seconds,
        play_beep=True,
        # No Twilio transcription: it bills per minute and the enrichment worker
        # already transcribes everything with a model that reads context.
        transcribe=False,
        recording_status_callback=recording_status_callback_url or None,
        recording_status_callback_method="POST" if recording_status_callback_url else None,
    )
    # Reached when the caller hangs up without leaving a message, in which case
    # <Record> does not fire its action.
    response.hangup()
    return str(response)


def reject(*, reason: str = "busy") -> str:
    """Refuse the call before answering it.

    Only for numbers already known to be bad. An unanswered call is never recorded and
    never billed: a blocked caller should cost nothing.
    """
    if reason not in ("busy", "rejected"):
        raise ValueError(f"unsupported reject reason {reason!r}")
    response = VoiceResponse()
    response.reject(reason=reason)
    return str(response)


def hangup() -> str:
    """End the call. The terminal document every other one can safely point at."""
    response = VoiceResponse()
    response.hangup()
    return str(response)

"""The documents Twilio executes.

Organised around the invariants rather than the verbs: a TwiML bug is not a wrong
attribute, it is a caller who was recorded without being told, a voicemail that loops
forever, or a line the system dialled out on.
"""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from ssscammers.agent import twiml

STREAM_URL = "wss://example.invalid/twilio/media/tok"
AFTER_URL = "https://example.invalid/twilio/after-stream"
ACTION_URL = "https://example.invalid/twilio/voicemail-complete"

#: Every verb that can originate contact or bridge a second leg (G-1).
FORBIDDEN_VERBS = ("Dial", "Sms", "Message", "Refer")


def parse(document: str) -> ElementTree.Element:
    root = ElementTree.fromstring(document)
    assert root.tag == "Response"
    return root


def all_documents() -> list[str]:
    """One of each document the system can produce."""
    return [
        twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL),
        twiml.engage(
            stream_url=STREAM_URL, after_stream_url=AFTER_URL, notice_audio_url="https://x/n.wav"
        ),
        twiml.voicemail(action_url=ACTION_URL),
        twiml.reject(),
        twiml.reject(reason="rejected"),
        twiml.hangup(),
    ]


class TestNothingCanDialOut:
    """G-1. The one rule the whole project hangs off."""

    @pytest.mark.parametrize("document", all_documents())
    def test_no_document_contains_an_outbound_verb(self, document: str) -> None:
        root = parse(document)
        tags = {element.tag for element in root.iter()}
        assert not tags & set(FORBIDDEN_VERBS), f"outbound verb in {document}"


class TestTheNoticeIsStructural:
    """G-2. The recording posture rests on this playing before anything else."""

    def test_a_clip_is_the_first_verb(self) -> None:
        root = parse(
            twiml.engage(
                stream_url=STREAM_URL,
                after_stream_url=AFTER_URL,
                notice_audio_url="https://x/notice.wav",
            )
        )
        assert list(root)[0].tag == "Play"
        assert list(root)[0].text == "https://x/notice.wav"

    def test_without_a_clip_the_notice_is_still_spoken(self) -> None:
        # Degraded — the caller can hear it is synthetic — but never absent. A missing
        # asset must not silently drop the consent notice.
        root = parse(twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL))
        first = list(root)[0]
        assert first.tag == "Say"
        assert "recorded" in (first.text or "").lower()

    def test_the_notice_precedes_the_stream(self) -> None:
        root = parse(twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL))
        tags = [element.tag for element in root]
        assert tags.index("Say") < tags.index("Connect")

    def test_a_document_with_no_notice_at_all_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="notice"):
            twiml.engage(
                stream_url=STREAM_URL, after_stream_url=AFTER_URL, notice_text="   "
            )

    def test_the_notice_is_not_the_personas_voice(self) -> None:
        # Stepping out of the persona is the point of the notice and of voicemail.
        root = parse(twiml.voicemail(action_url=ACTION_URL))
        say = root.find("Say")
        assert say is not None
        assert say.get("voice") == twiml.NOTICE_VOICE


class TestEngage:
    def test_the_stream_is_connected_bidirectionally(self) -> None:
        root = parse(twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL))
        stream = root.find("Connect/Stream")
        assert stream is not None
        assert stream.get("url") == STREAM_URL

    def test_only_the_callers_audio_is_forwarded(self) -> None:
        # Both tracks would feed the persona's own speech back into the recogniser,
        # which then answers itself.
        stream = parse(
            twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL)
        ).find("Connect/Stream")
        assert stream is not None
        assert stream.get("track") == "inbound_track"

    def test_parameters_reach_the_pipeline(self) -> None:
        root = parse(
            twiml.engage(
                stream_url=STREAM_URL,
                after_stream_url=AFTER_URL,
                parameters={"call_sid": "CA1", "persona_id": "marjorie"},
            )
        )
        params = {p.get("name"): p.get("value") for p in root.findall("Connect/Stream/Parameter")}
        assert params == {"call_sid": "CA1", "persona_id": "marjorie"}

    def test_the_document_continues_after_the_stream_closes(self) -> None:
        # This is what delivers the voicemail the disclosure script promises. Without
        # it, closing the socket drops the call on a real person mid-apology.
        root = parse(twiml.engage(stream_url=STREAM_URL, after_stream_url=AFTER_URL))
        redirect = root.find("Redirect")
        assert redirect is not None
        assert redirect.text == AFTER_URL
        assert list(root)[-1].tag == "Redirect"

    def test_a_non_websocket_stream_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="websocket"):
            twiml.engage(stream_url="https://example.invalid/media", after_stream_url=AFTER_URL)

    def test_a_stream_with_nowhere_to_go_afterwards_is_refused(self) -> None:
        with pytest.raises(ValueError, match="after_stream_url"):
            twiml.engage(stream_url=STREAM_URL, after_stream_url="")


class TestVoicemail:
    def test_recording_names_where_it_goes_next(self) -> None:
        # Twilio defaults <Record>'s action to the current document. Without an
        # explicit action, finishing a voicemail starts another one, forever.
        record = parse(twiml.voicemail(action_url=ACTION_URL)).find("Record")
        assert record is not None
        assert record.get("action") == ACTION_URL

    def test_a_voicemail_with_no_action_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="loop"):
            twiml.voicemail(action_url="")

    def test_twilio_transcription_is_off(self) -> None:
        # Billed per minute, and enrichment transcribes everything anyway.
        record = parse(twiml.voicemail(action_url=ACTION_URL)).find("Record")
        assert record is not None
        assert record.get("transcribe") == "false"

    def test_the_caller_hanging_up_early_still_ends_the_call(self) -> None:
        # <Record> does not fire its action if the caller hangs up first.
        root = parse(twiml.voicemail(action_url=ACTION_URL))
        assert list(root)[-1].tag == "Hangup"

    def test_the_length_cap_is_honoured(self) -> None:
        record = parse(
            twiml.voicemail(action_url=ACTION_URL, max_length_seconds=45)
        ).find("Record")
        assert record is not None
        assert record.get("maxLength") == "45"


class TestRejectAndHangup:
    def test_reject_does_not_answer_the_call(self) -> None:
        # An unanswered call is neither recorded nor billed, which is the entire point
        # of rejecting a known-bad number rather than picking up.
        root = parse(twiml.reject())
        assert [element.tag for element in root] == ["Reject"]

    def test_an_unsupported_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            twiml.reject(reason="whatever")

    def test_hangup_is_terminal_and_says_nothing(self) -> None:
        root = parse(twiml.hangup())
        assert [element.tag for element in root] == ["Hangup"]


class TestTheVoicemailPathAlsoDisclosesRecording:
    """The kill-switch, allowlist and overflow paths never call engage()."""

    def test_the_voicemail_prompt_says_the_call_is_recorded(self) -> None:
        # No engage() means no notice verb anywhere on the call, yet <Record> still
        # records the caller. G-2 and docs/legal.md require telling them first.
        root = parse(twiml.voicemail(action_url=ACTION_URL))
        say = root.find("Say")
        assert say is not None
        assert "recorded" in (say.text or "").lower()

    def test_the_notice_precedes_the_recording(self) -> None:
        tags = [element.tag for element in parse(twiml.voicemail(action_url=ACTION_URL))]
        assert tags.index("Say") < tags.index("Record")

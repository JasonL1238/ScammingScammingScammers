"""The public edge, driven through real signed requests.

Signatures here are computed with Twilio's own validator rather than stubbed, because
the property under test is "an unsigned request cannot reach the handler" and a mocked
validator would assert nothing about that.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from ssscammers.agent import webhooks
from ssscammers.agent.registry import CallRegistry
from ssscammers.agent.triage import AllowlistCache
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import CallerClass, CallPhase, EntryPath

AUTH_TOKEN = "test-auth-token"
BASE_URL = "https://honeypot.example"
PATH_TOKEN = "a-real-secret-token"
CALLER = "+19375550142"


def make_settings(**overrides) -> Settings:
    defaults = dict(
        twilio_account_sid="AC" + "0" * 32,
        twilio_auth_token=AUTH_TOKEN,
        honeypot_number="+15555550100",
        public_base_url=BASE_URL,
        media_stream_path_token=PATH_TOKEN,
        default_persona="marjorie",
        max_concurrent_calls=2,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class StubRecorder:
    """Records what it was asked to do, and can fail the way Twilio does."""

    def __init__(self, *, raises: Exception | None = None, hang: bool = False) -> None:
        self.started: list[str] = []
        self.callbacks: list[str] = []
        self.raises = raises
        self.hang = hang

    async def start_dual_channel(self, call_sid: str, *, status_callback_url: str = "") -> str | None:
        self.started.append(call_sid)
        self.callbacks.append(status_callback_url)
        if self.raises is not None:
            raise self.raises
        if self.hang:
            await asyncio.sleep(3600)
        return "RE" + "1" * 32


def build(
    *,
    settings: Settings | None = None,
    registry: CallRegistry | None = None,
    allowlist: AllowlistCache | None = None,
    recorder: StubRecorder | None = None,
):
    settings = settings or make_settings()
    registry = registry or CallRegistry(max_concurrent=settings.max_concurrent_calls)
    recorder = recorder if recorder is not None else StubRecorder()
    app = webhooks.create_app(
        settings=settings,
        registry=registry,
        allowlist=allowlist if allowlist is not None else AllowlistCache(),
        recorder=recorder,
    )
    return TestClient(app), registry, recorder


def signed_post(client: TestClient, path: str, params: dict[str, str], *, token: str = AUTH_TOKEN):
    signature = RequestValidator(token).compute_signature(f"{BASE_URL}{path}", params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def voice_params(**overrides) -> dict[str, str]:
    params = {
        "CallSid": "CA" + "1" * 32,
        "From": CALLER,
        "To": "+15555550100",
        "Direction": "inbound",
        "CallStatus": "ringing",
    }
    params.update(overrides)
    return params


def verbs(response) -> list[str]:
    root = ElementTree.fromstring(response.text)
    return [element.tag for element in root]



class TestTheAppRefusesToBootOnAPersonaItCannotVoice:
    """A static misconfiguration must stop the deployment, not every call individually.

    Left to the media socket, `DEFAULT_PERSONA=dot` answers each caller, starts a
    dual-channel recording, plays the recorded-line notice, and only then discovers it has
    no voice and hangs up — while `/healthz` keeps reporting ok.
    """

    def test_a_bundle_written_for_an_unimplemented_provider_is_refused(
        self, unservable_persona: str
    ) -> None:
        with pytest.raises(RuntimeError, match="DEFAULT_PERSONA"):
            build(settings=make_settings(default_persona=unservable_persona))

    def test_the_error_says_what_to_do_about_it(self, unservable_persona: str) -> None:
        with pytest.raises(RuntimeError, match="wire that provider into"):
            build(settings=make_settings(default_persona=unservable_persona))

    def test_a_persona_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match="cannot be loaded"):
            build(settings=make_settings(default_persona="nobody"))

    def test_a_servable_persona_boots(self) -> None:
        client, _, _ = build(settings=make_settings(default_persona="marjorie"))
        assert client.get("/healthz").status_code == 200


class TestOnlyTwilioCanReachTheHandlers:
    def test_a_correctly_signed_request_is_served(self) -> None:
        client, _, _ = build()
        response = signed_post(client, "/twilio/voice", voice_params())
        assert response.status_code == 200
        assert "Connect" in verbs(response)

    def test_an_unsigned_request_is_refused(self) -> None:
        client, _, _ = build()
        response = client.post("/twilio/voice", data=voice_params())
        assert response.status_code == 403

    def test_a_request_signed_with_the_wrong_token_is_refused(self) -> None:
        client, _, _ = build()
        response = signed_post(client, "/twilio/voice", voice_params(), token="not-the-token")
        assert response.status_code == 403

    def test_tampering_with_a_parameter_invalidates_the_signature(self) -> None:
        client, _, _ = build()
        params = voice_params()
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"{BASE_URL}/twilio/voice", params
        )
        params["From"] = "+19998887777"
        response = client.post(
            "/twilio/voice", data=params, headers={"X-Twilio-Signature": signature}
        )
        assert response.status_code == 403

    def test_a_missing_auth_token_refuses_service_rather_than_skipping_the_check(self) -> None:
        # The dangerous failure mode: no token quietly meaning "allow everyone".
        client, _, _ = build(settings=make_settings(twilio_auth_token=""))
        response = client.post("/twilio/voice", data=voice_params())
        assert response.status_code == 503

    def test_every_webhook_is_protected(self) -> None:
        # Enumerated from the app rather than hand-listed. The hand-listed version of this
        # test had already fallen one route behind — and the route it was missing,
        # /twilio/voicemail-recording-status, was the newest one. A list that has to be
        # updated by hand cannot make the claim this test's name makes.
        client, _, _ = build()
        posts = sorted(
            route.path
            for route in client.app.routes
            if "POST" in (getattr(route, "methods", None) or set())
        )
        assert len(posts) >= 6, f"expected every Twilio webhook to be found, got {posts}"
        for path in posts:
            assert client.post(path, data={"CallSid": "CA1"}).status_code == 403, path


class TestTheAppRefusesToStartUnsafely:
    def test_a_placeholder_media_token_is_fatal(self) -> None:
        # That token is the only thing between the public internet and the media socket.
        with pytest.raises(RuntimeError, match="MEDIA_STREAM_PATH_TOKEN"):
            webhooks.create_app(settings=make_settings(media_stream_path_token="change-me"))

    def test_an_empty_media_token_is_fatal(self) -> None:
        with pytest.raises(RuntimeError, match="MEDIA_STREAM_PATH_TOKEN"):
            webhooks.create_app(settings=make_settings(media_stream_path_token=""))

    def test_signature_validation_needs_the_public_url_it_signs_against(self) -> None:
        with pytest.raises(RuntimeError, match="public_base_url"):
            webhooks.create_app(settings=make_settings(public_base_url=""))


class TestGuardrailOneAtTheEdge:
    def test_a_non_inbound_call_is_rejected(self) -> None:
        # Outbound is disabled at the subaccount; if one ever arrives here, something is
        # badly wrong and the safe move is to touch the call as little as possible.
        client, registry, recorder = build()
        response = signed_post(client, "/twilio/voice", voice_params(Direction="outbound-api"))
        assert verbs(response) == ["Reject"]
        assert registry.active_count == 0
        assert recorder.started == []


class TestWhoGetsBaited:
    def test_an_ordinary_unknown_caller_is_engaged(self) -> None:
        client, registry, _ = build()
        response = signed_post(client, "/twilio/voice", voice_params())
        assert "Connect" in verbs(response)
        assert registry.active_count == 1

    def test_a_blocked_caller_is_never_answered(self) -> None:
        allowlist = AllowlistCache()
        allowlist.set(CALLER, CallerClass.BLOCKED)
        client, registry, recorder = build(allowlist=allowlist)

        response = signed_post(client, "/twilio/voice", voice_params())
        assert verbs(response) == ["Reject"]
        assert registry.active_count == 0
        assert recorder.started == []

    def test_an_allowlisted_caller_gets_voicemail_and_is_not_recorded(self) -> None:
        # G-11. There is no reason to hold audio of a neighbour who rang the wrong line.
        allowlist = AllowlistCache()
        allowlist.set(CALLER, CallerClass.LEGIT)
        client, registry, recorder = build(allowlist=allowlist)

        response = signed_post(client, "/twilio/voice", voice_params())
        assert "Record" in verbs(response)
        assert "Connect" not in verbs(response)
        assert recorder.started == []
        assert registry.active_count == 0

    def test_an_allowlisted_number_is_matched_regardless_of_formatting(self) -> None:
        # The webhook's From field and a synced contact rarely agree on punctuation.
        allowlist = AllowlistCache()
        allowlist.set("(937) 555-0142", CallerClass.LEGIT)
        client, _, _ = build(allowlist=allowlist)

        response = signed_post(client, "/twilio/voice", voice_params(From="+19375550142"))
        assert "Record" in verbs(response)

    def test_a_forwarded_call_is_marked_as_such(self) -> None:
        # Entry path sets the bar triage has to clear, so it must survive the handoff.
        client, registry, _ = build()
        params = voice_params(ForwardedFrom="+15555550199")
        signed_post(client, "/twilio/voice", params)

        call = registry.get(params["CallSid"])
        assert call is not None
        assert call.entry_path is EntryPath.CONDITIONAL_FORWARD

    def test_a_direct_call_is_marked_as_such(self) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)
        call = registry.get(params["CallSid"])
        assert call is not None
        assert call.entry_path is EntryPath.DIRECT

    def test_a_call_with_no_sid_is_a_bad_request(self) -> None:
        client, _, _ = build()
        params = voice_params()
        del params["CallSid"]
        assert signed_post(client, "/twilio/voice", params).status_code == 400


class TestOverflowAndTheKillSwitch:
    def test_a_caller_arriving_at_capacity_gets_voicemail(self) -> None:
        # G-15. Never a rejection: the overflow caller may be a real person.
        client, registry, _ = build(settings=make_settings(max_concurrent_calls=1))
        signed_post(client, "/twilio/voice", voice_params(CallSid="CA-one"))

        response = signed_post(client, "/twilio/voice", voice_params(CallSid="CA-two"))
        assert "Record" in verbs(response)
        assert "Connect" not in verbs(response)

    def test_a_retried_webhook_does_not_consume_a_second_slot(self) -> None:
        client, registry, _ = build(settings=make_settings(max_concurrent_calls=1))
        params = voice_params()
        first = signed_post(client, "/twilio/voice", params)
        second = signed_post(client, "/twilio/voice", params)

        assert "Connect" in verbs(first)
        assert "Connect" in verbs(second)
        assert registry.active_count == 1

    def test_switching_the_system_off_takes_messages_rather_than_dropping_calls(self) -> None:
        client, registry, _ = build()
        registry.enabled = False

        response = signed_post(client, "/twilio/voice", voice_params())
        assert "Record" in verbs(response)
        assert "Connect" not in verbs(response)

    def test_a_blocked_caller_is_still_rejected_when_switched_off(self) -> None:
        allowlist = AllowlistCache()
        allowlist.set(CALLER, CallerClass.BLOCKED)
        client, registry, _ = build(allowlist=allowlist)
        registry.enabled = False
        assert verbs(signed_post(client, "/twilio/voice", voice_params())) == ["Reject"]


class TestRecording:
    def test_recording_starts_before_the_call_is_handed_over(self) -> None:
        client, registry, recorder = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)

        assert recorder.started == [params["CallSid"]]
        call = registry.get(params["CallSid"])
        assert call is not None
        assert call.recording_sid is not None

    def test_a_recording_failure_does_not_fail_the_call(self) -> None:
        client, _, _ = build(recorder=StubRecorder(raises=RuntimeError("twilio is down")))
        response = signed_post(client, "/twilio/voice", voice_params())
        assert "Connect" in verbs(response)

    def test_a_slow_recording_api_does_not_hold_the_caller(self, monkeypatch) -> None:
        monkeypatch.setattr(webhooks, "_RECORDING_START_TIMEOUT_SECONDS", 0.05)
        client, _, _ = build(recorder=StubRecorder(hang=True))
        response = signed_post(client, "/twilio/voice", voice_params())
        assert "Connect" in verbs(response)

    def test_the_recording_callback_is_stored_against_the_call(self) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)

        response = signed_post(
            client,
            "/twilio/recording-status",
            {
                "CallSid": params["CallSid"],
                "RecordingSid": "RE" + "9" * 32,
                "RecordingStatus": "completed",
            },
        )
        assert response.status_code == 204
        call = registry.get(params["CallSid"])
        assert call is not None
        assert call.recording_sid == "RE" + "9" * 32


class TestTheRecorderThatRunsInProduction:
    """`_default_recorder` returns this whenever credentials exist — i.e. always, live."""

    @staticmethod
    def recorder_client(captured: list) -> webhooks.TwilioRestRecorder:
        import httpx

        def handle(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"sid": "RE" + "7" * 32})

        recorder = webhooks.TwilioRestRecorder("AC123", "tok")
        recorder._transport = httpx.MockTransport(handle)  # type: ignore[attr-defined]
        return recorder

    async def test_it_asks_for_a_dual_channel_recording_of_both_tracks(self) -> None:
        # Dual channel is what makes diarization exact rather than inferred, which is
        # what makes "how long did a human actually spend on this" a measurement.
        captured: list = []
        sid = await self.recorder_client(captured).start_dual_channel("CA1")

        assert sid == "RE" + "7" * 32
        body = captured[0].content.decode()
        assert "RecordingChannels=dual" in body
        assert "RecordingTrack=both" in body

    async def test_it_posts_to_the_recording_endpoint_of_that_call(self) -> None:
        captured: list = []
        await self.recorder_client(captured).start_dual_channel("CA1")
        assert str(captured[0].url).endswith("/Calls/CA1/Recordings.json")

    async def test_it_authenticates(self) -> None:
        captured: list = []
        await self.recorder_client(captured).start_dual_channel("CA1")
        assert captured[0].headers.get("authorization", "").startswith("Basic ")

    async def test_it_forwards_the_status_callback_when_given_one(self) -> None:
        captured: list = []
        await self.recorder_client(captured).start_dual_channel(
            "CA1", status_callback_url="https://x.example/cb"
        )
        assert "RecordingStatusCallback" in captured[0].content.decode()

    async def test_an_error_response_raises_so_the_caller_can_degrade(self) -> None:
        import httpx

        recorder = webhooks.TwilioRestRecorder("AC123", "tok")
        recorder._transport = httpx.MockTransport(  # type: ignore[attr-defined]
            lambda _r: httpx.Response(404, json={"message": "no such call"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await recorder.start_dual_channel("CA1")

    def test_it_refuses_to_exist_without_credentials(self) -> None:
        with pytest.raises(ValueError, match="account SID"):
            webhooks.TwilioRestRecorder("", "")


class TestRecordingStartsBeforeTheNoticePlays:
    """G-2: the notice must be *inside* the recording, so ordering is the property."""

    def test_the_recording_is_started_before_the_document_is_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Asserting `recorder.started` after the response proves only that both happened.
        # This records the actual order of the two operations, so moving the recording
        # call after `twiml.engage(...)` fails the test instead of passing it silently.
        order: list[str] = []

        class OrderedRecorder(StubRecorder):
            async def start_dual_channel(self, call_sid: str, *, status_callback_url: str = ""):
                order.append("recording")
                return await super().start_dual_channel(
                    call_sid, status_callback_url=status_callback_url
                )

        real_engage = webhooks.twiml.engage

        def spy_engage(**kwargs):
            order.append("twiml")
            return real_engage(**kwargs)

        monkeypatch.setattr(webhooks.twiml, "engage", spy_engage)
        client, _, _ = build(recorder=OrderedRecorder())
        signed_post(client, "/twilio/voice", voice_params())

        assert order == ["recording", "twiml"], order


class TestWhatHappensWhenTheStreamCloses:
    def test_a_released_caller_gets_the_voicemail_they_were_promised(self) -> None:
        # The disclosure script says "I'm going to put you through to voicemail now".
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)
        registry.finish(
            params["CallSid"], final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=True
        )

        response = signed_post(client, "/twilio/after-stream", {"CallSid": params["CallSid"]})
        assert "Record" in verbs(response)

    def test_a_victim_who_was_told_to_hang_up_is_not_offered_a_beep(self) -> None:
        # Same phase as the test above, opposite script: the victim warning says "hang up
        # and call your bank". Following that with "leave a message after the tone"
        # contradicts the instruction at the worst possible moment.
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)
        registry.finish(
            params["CallSid"], final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=False
        )

        response = signed_post(client, "/twilio/after-stream", {"CallSid": params["CallSid"]})
        assert verbs(response) == ["Hangup"]

    def test_a_scammer_just_gets_hung_up_on(self) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)
        registry.finish(params["CallSid"], final_phase=CallPhase.TERMINATE)  # no promise

        response = signed_post(client, "/twilio/after-stream", {"CallSid": params["CallSid"]})
        assert verbs(response) == ["Hangup"]

    def test_an_unknown_call_is_hung_up_rather_than_recorded(self) -> None:
        client, _, _ = build()
        response = signed_post(client, "/twilio/after-stream", {"CallSid": "CA-never-seen"})
        assert verbs(response) == ["Hangup"]

    def test_a_finished_voicemail_ends_the_call(self) -> None:
        client, _, _ = build()
        response = signed_post(
            client, "/twilio/voicemail-complete", {"CallSid": "CA1", "RecordingDuration": "12"}
        )
        assert verbs(response) == ["Hangup"]


class TestCallLifecycle:
    def test_a_completed_call_frees_its_slot(self) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)
        assert registry.active_count == 1

        response = signed_post(
            client,
            "/twilio/status",
            {"CallSid": params["CallSid"], "CallStatus": "completed", "CallDuration": "412"},
        )
        assert response.status_code == 204
        assert registry.active_count == 0

    @pytest.mark.parametrize("status_value", ["failed", "busy", "no-answer", "canceled"])
    def test_every_terminal_status_frees_the_slot(self, status_value: str) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)

        signed_post(
            client, "/twilio/status", {"CallSid": params["CallSid"], "CallStatus": status_value}
        )
        assert registry.active_count == 0

    def test_an_in_progress_status_keeps_the_slot(self) -> None:
        client, registry, _ = build()
        params = voice_params()
        signed_post(client, "/twilio/voice", params)

        signed_post(
            client, "/twilio/status", {"CallSid": params["CallSid"], "CallStatus": "in-progress"}
        )
        assert registry.active_count == 1


class TestTheMediaSocket:
    @staticmethod
    def close_code(client: TestClient, token: str) -> int:
        """Connect, and report the code the server closed with."""
        with (
            pytest.raises(WebSocketDisconnect) as caught,
            client.websocket_connect(f"/twilio/media/{token}") as socket,
        ):
            socket.receive_text()
        return caught.value.code

    def test_a_wrong_path_token_is_refused_as_a_policy_violation(self) -> None:
        client, _, _ = build()
        assert self.close_code(client, "guessed-token") == 1008

    def test_the_right_token_gets_past_the_token_check(self) -> None:
        # The distinguishing assertion: a good token is not rejected by the token check.
        # Written as "not 1008" rather than a specific code so it holds either way — with
        # the media extra installed the socket instead waits for Twilio's handshake and
        # closes 1002. An assertion of 1011 would have passed only in a dev environment
        # and failed in the one that ships.
        client, _, _ = build()
        assert self.close_code(client, PATH_TOKEN) != 1008

    def test_a_missing_media_stack_closes_cleanly_instead_of_erroring(self) -> None:
        # The realtime stack reports its absence when first used, not when imported, so
        # the RuntimeError surfaces inside an already-accepted socket. Unhandled, it
        # would be a bare 500 on the one endpoint that carries live audio.
        if importlib.util.find_spec("pipecat") is not None:
            pytest.skip("this asserts the absent-extra path; the media extra is installed")

        client, _, _ = build()
        assert self.close_code(client, PATH_TOKEN) == 1011


class TestHealth:
    def test_health_reports_capacity_without_a_signature(self) -> None:
        client, _, _ = build()
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "enabled": True,
            "active_calls": 0,
            "capacity": 2,
        }

    def test_health_reflects_a_live_call(self) -> None:
        client, _, _ = build()
        signed_post(client, "/twilio/voice", voice_params())
        assert client.get("/healthz").json()["active_calls"] == 1


class TestADayThatIsOverStillTakesAMessage:
    """The capped path, end to end through a real signed request.

    The rest of this suite injects a registry with ``ledger=None``, so neither the wiring
    in ``create_app`` nor the capped branch of the voice handler was exercised anywhere.
    The property under test is the one that matters most: a cap must never cost a real
    person the chance to leave a message.
    """

    @staticmethod
    def capped_registry(tmp_path: Path) -> CallRegistry:
        from ssscammers.agent.daily_ledger import DailyLedger

        ledger = DailyLedger(
            path=tmp_path / "ledger.json",
            minutes_cap=1,
            today=lambda: "2026-08-20",
        )
        ledger.record_duration("EARLIER", 60 * 60)  # an hour against a one-minute cap
        return CallRegistry(max_concurrent=5, ledger=ledger)

    def test_a_capped_day_answers_with_a_recording_rather_than_a_rejection(
        self, tmp_path: Path
    ) -> None:
        client, _, _ = build(registry=self.capped_registry(tmp_path))
        response = signed_post(client, "/twilio/voice", voice_params())

        assert response.status_code == 200, "a cap must not surface as an error to Twilio"
        assert "Record" in verbs(response), (
            "the caller was refused outright; a cap must degrade to voicemail so a real "
            "person can still leave a message"
        )
        assert "Reject" not in verbs(response)

    def test_the_call_is_not_admitted_and_no_slot_is_consumed(self, tmp_path: Path) -> None:
        registry = self.capped_registry(tmp_path)
        client, _, _ = build(registry=registry)
        signed_post(client, "/twilio/voice", voice_params())
        assert registry.active_count == 0

"""The notice-clip probe and the alerting path behind it — G-2's runtime half.

The boot check refuses a malformed ``NOTICE_AUDIO_URL``; everything here covers the
worse case it cannot: a well-formed clip URL that stops being fetchable while the
agent is serving, which would otherwise hand Twilio a dead ``<Play>`` and record
callers with no audible notice.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ssscammers.agent.notice import NoticeHealth
from ssscammers.agent.notify import NtfyNotifier, NullNotifier, notifier_from_settings
from ssscammers.shared.config import Settings

CLIP_URL = "https://cdn.example/notice.wav"


class RecordingNotifier:
    """Counts every alert so transition semantics can be asserted exactly."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    async def send(self, title: str, body: str) -> bool:
        self.alerts.append((title, body))
        return True


def transport(*responses: httpx.Response | Exception) -> httpx.MockTransport:
    """A transport that replays the given outcomes in order, then repeats the last."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return httpx.MockTransport(handler)


def audio(status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "audio/wav"}, content=b"RIFF")


def health(*responses: httpx.Response | Exception, notifier=None) -> NoticeHealth:
    return NoticeHealth(
        url=CLIP_URL,
        notifier=notifier if notifier is not None else RecordingNotifier(),
        _transport=transport(*responses),
    )


class TestCheckOnce:
    async def test_a_reachable_clip_stays_healthy(self) -> None:
        h = health(audio())
        assert await h.check_once() is True
        assert h.healthy
        assert h.current_url() == CLIP_URL

    async def test_a_404_degrades_to_the_text_notice(self) -> None:
        h = health(audio(404))
        assert await h.check_once() is False
        assert not h.healthy
        assert h.current_url() == ""

    async def test_a_transport_failure_degrades(self) -> None:
        h = health(httpx.ConnectError("no route to host"))
        assert await h.check_once() is False
        assert h.current_url() == ""

    async def test_a_200_html_page_degrades(self) -> None:
        # A parked domain or an error page returns 200 text/html; Twilio's <Play>
        # fails on it exactly like a 404, so it must count as unreachable.
        page = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        h = health(page)
        assert await h.check_once() is False
        assert h.current_url() == ""

    async def test_an_unconfigured_url_is_trivially_healthy(self) -> None:
        h = NoticeHealth(url="")
        assert await h.check_once() is True
        assert h.current_url() == ""

    async def test_check_once_never_raises(self) -> None:
        # The probe is the watchdog; a watchdog that can die of the failure it
        # watches for is worse than none.
        h = health(RuntimeError("transport exploded"))
        assert await h.check_once() is False

    async def test_a_non_2xx_final_status_degrades(self) -> None:
        # With redirects followed, an unfollowable 302 (blank Location) comes back
        # as the final response; "< 400" would call it healthy while Twilio's
        # <Play> chokes on it exactly like a 404.
        stuck = httpx.Response(302, headers={"content-type": "audio/wav"})
        h = health(stuck)
        assert await h.check_once() is False
        assert h.current_url() == ""

    async def test_a_200_plain_text_page_degrades(self) -> None:
        # SPA fallbacks and misconfigured static hosts serve "Not found" as 200
        # text/plain; no audio format is served as text/*.
        page = httpx.Response(200, headers={"content-type": "text/plain"}, content=b"Not found")
        h = health(page)
        assert await h.check_once() is False

    async def test_a_missing_content_type_degrades(self) -> None:
        # Twilio does not sniff: a missing Content-Type draws error 13325 ("Play
        # requires an audio Content-Type") and the verb is skipped — the caller
        # would be recorded with no notice while a lenient probe reported green.
        bare = httpx.Response(200, content=b"RIFF")
        h = health(bare)
        assert await h.check_once() is False

    async def test_the_s3_default_octet_stream_degrades(self) -> None:
        # The realistic trap: a clip uploaded to S3/R2 without Content-Type
        # metadata serves application/octet-stream — playable in a browser,
        # rejected by Twilio's <Play> allowlist.
        blob = httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"RIFF"
        )
        h = health(blob)
        assert await h.check_once() is False

    async def test_every_twilio_documented_type_is_healthy(self) -> None:
        # The probe applies exactly Twilio's documented allowlist, so it can
        # never false-alarm on a clip Twilio would play.
        from ssscammers.agent.notice import _PLAYABLE_CONTENT_TYPES

        for media_type in sorted(_PLAYABLE_CONTENT_TYPES):
            clip = httpx.Response(
                200,
                headers={"content-type": f"{media_type}; charset=binary"},
                content=b"RIFF",
            )
            assert await health(clip).check_once() is True, media_type

    async def test_a_trickling_host_is_bounded_by_wall_clock(self) -> None:
        # httpx's timeout bounds socket operations, not the request; a host that
        # trickles bytes strings a probe out arbitrarily — and the boot fetch runs
        # before the app serves, so an unbounded probe is an unbounded outage.
        async def trickle(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(30)
            return audio()

        h = NoticeHealth(url=CLIP_URL, timeout_seconds=0.02)
        h._transport = httpx.MockTransport(trickle)
        started = asyncio.get_running_loop().time()
        assert await h.check_once() is False
        assert asyncio.get_running_loop().time() - started < 5.0

    async def test_a_raising_notifier_cannot_kill_the_probe(self) -> None:
        # The Notifier protocol does not promise not to raise; a notifier that did
        # would otherwise die the loop on its first transition — a dead watchdog
        # that looks exactly like a healthy clip.
        class ExplodingNotifier:
            async def send(self, title: str, body: str) -> bool:
                raise RuntimeError("notifier exploded")

        h = health(audio(404), audio(), notifier=ExplodingNotifier())
        assert await h.check_once() is False  # transition fires the raising send
        assert not h.healthy
        assert await h.check_once() is True  # recovery transition raises too
        assert h.healthy


class TestTheIntervalFloor:
    def test_a_zero_interval_is_clamped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # interval=0 reads like "disable probing" but would be a hot loop:
        # thousands of probes per second against the clip host, on the same event
        # loop that serves live calls.
        import logging

        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.notice"):
            h = NoticeHealth(url=CLIP_URL, interval_seconds=0)
        assert h.interval_seconds >= 5.0
        assert "floor" in caplog.text

    def test_a_negative_interval_is_clamped(self) -> None:
        assert NoticeHealth(url=CLIP_URL, interval_seconds=-5).interval_seconds >= 5.0

    def test_a_sane_interval_is_untouched(self) -> None:
        assert NoticeHealth(url=CLIP_URL, interval_seconds=60).interval_seconds == 60


class TestAlertsFireOnTransitionsOnly:
    async def test_one_alert_per_outage_not_per_probe(self) -> None:
        notifier = RecordingNotifier()
        h = health(audio(404), notifier=notifier)
        for _ in range(5):
            await h.check_once()
        assert len(notifier.alerts) == 1
        assert "unreachable" in notifier.alerts[0][0].lower()

    async def test_recovery_fires_its_own_alert(self) -> None:
        notifier = RecordingNotifier()
        h = health(audio(404), audio(), notifier=notifier)
        await h.check_once()
        await h.check_once()
        assert h.healthy
        assert h.current_url() == CLIP_URL
        assert [title for title, _ in notifier.alerts] == [
            "Notice clip unreachable",
            "Notice clip recovered",
        ]

    async def test_a_healthy_clip_never_alerts(self) -> None:
        notifier = RecordingNotifier()
        h = health(audio(), notifier=notifier)
        for _ in range(3):
            await h.check_once()
        assert notifier.alerts == []


class TestTheProbeLoop:
    async def test_the_loop_probes_on_the_injected_cadence(self) -> None:
        notifier = RecordingNotifier()
        h = health(audio(), audio(404), audio(), notifier=notifier)
        slept: list[float] = []

        async def sleep(seconds: float) -> None:
            slept.append(seconds)
            if len(slept) > 3:
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await h.run(sleep=sleep)

        assert slept == [h.interval_seconds] * 4
        # outage on probe 2, recovery on probe 3 — both transitions alerted, once.
        assert [title for title, _ in notifier.alerts] == [
            "Notice clip unreachable",
            "Notice clip recovered",
        ]
        assert h.healthy

    async def test_cancellation_propagates(self) -> None:
        # The lifespan cancels this task at shutdown; a loop that swallowed
        # CancelledError would leak past shutdown.
        h = health(audio())
        task = asyncio.create_task(h.run())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestNtfyNotifier:
    def notifier(self, handler) -> NtfyNotifier:
        n = NtfyNotifier("https://ntfy.example/", "scam-line", token="tok-123")
        n._transport = httpx.MockTransport(handler)
        return n

    async def test_the_request_shape(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        assert await self.notifier(handler).send("Title here", "body text") is True
        (request,) = seen
        assert str(request.url) == "https://ntfy.example/scam-line"
        assert request.headers["Title"] == "Title here"
        assert request.headers["Authorization"] == "Bearer tok-123"
        assert request.content == b"body text"

    async def test_no_token_sends_no_auth_header(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        n = NtfyNotifier("https://ntfy.example", "scam-line")
        n._transport = httpx.MockTransport(handler)
        await n.send("t", "b")
        assert "authorization" not in seen[0].headers

    async def test_a_server_error_is_swallowed(self) -> None:
        assert await self.notifier(lambda r: httpx.Response(500)).send("t", "b") is False

    async def test_a_transport_error_is_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        assert await self.notifier(handler).send("t", "b") is False

    def test_a_notifier_without_a_topic_is_refused(self) -> None:
        with pytest.raises(ValueError):
            NtfyNotifier("https://ntfy.example", "")


class TestNotifierFromSettings:
    def test_a_topic_selects_ntfy(self) -> None:
        settings = Settings(ntfy_topic="scam-line")
        assert isinstance(notifier_from_settings(settings), NtfyNotifier)

    def test_no_topic_selects_null(self) -> None:
        assert isinstance(notifier_from_settings(Settings()), NullNotifier)

    async def test_the_null_notifier_delivers_nothing(self) -> None:
        assert await NullNotifier().send("t", "b") is False

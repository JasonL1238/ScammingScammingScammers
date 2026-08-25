"""G-2 runtime monitoring: is the recorded-line notice clip actually fetchable?

The boot check in :mod:`ssscammers.agent.webhooks` refuses a *malformed*
``NOTICE_AUDIO_URL``, but a well-formed URL that 404s at call time is worse than
none: Twilio logs the failed ``<Play>`` and continues the document, so the caller
is connected and recorded with no notice at all. This module closes that hole by
probing the clip — once at boot, then on an interval — and steering the engage
document to the fixed spoken ``NOTICE_TEXT`` while the clip is unreachable. The
notice degrades to text rather than silently disappearing; the residual exposure
is one probe interval — a clip that dies between probes reaches Twilio until the
next probe notices.

Polarity is fail-safe throughout: any doubt about the clip (an error status, a
transport failure, a Content-Type outside the list Twilio's ``<Play>`` documents)
counts as unhealthy, because the cost of wrongly speaking the text fallback is a
caller hearing a robot voice, while the cost of wrongly playing a dead clip is a
recording with no consent notice.

The call-path integration is a single in-memory read (:meth:`NoticeHealth.
current_url`); nothing here is ever awaited while answering a webhook.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ssscammers.agent.notify import Notifier, NullNotifier

logger = logging.getLogger(__name__)

__all__ = ["NoticeHealth"]

_MIN_INTERVAL_SECONDS = 5.0
"""Floor on the re-probe cadence. An interval of zero (a plausible misreading of
"disable probing") would otherwise turn the watchdog into a hot loop hammering the
clip host and the live-call event loop — thousands of probes per second, measured."""

_PLAYABLE_CONTENT_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/wav",
        "audio/wave",
        "audio/x-wav",
        "audio/aiff",
        "audio/x-aifc",
        "audio/x-aiff",
        "audio/gsm",
        "audio/x-gsm",
        "audio/ulaw",
    }
)
"""The MIME types Twilio's ``<Play>`` documents as supported — exactly its list.

Twilio does not sniff: a URL serving anything else, *including a missing
Content-Type* (the S3/R2 default ``application/octet-stream`` case), draws error
13325 ("Play requires an audio Content-Type"), the verb is skipped, and the caller
is recorded with no notice. Because this is Twilio's own list, the probe cannot
false-alarm on a clip Twilio would play."""


@dataclass
class NoticeHealth:
    """Tracks whether the configured notice clip is fetchable right now.

    ``healthy`` starts ``True`` so an app whose lifespan never runs (tests build the
    app without entering it) behaves exactly as before this module existed: the
    configured clip is served. The boot fetch runs before the first webhook is
    answered and corrects the assumption immediately if it is wrong.
    """

    url: str
    notifier: Notifier = field(default_factory=NullNotifier)
    interval_seconds: float = 60.0
    timeout_seconds: float = 5.0
    healthy: bool = True

    _transport: Any = field(default=None, repr=False)
    """Injected in tests so probes run against a fake host. ``None`` in production."""

    def __post_init__(self) -> None:
        if self.interval_seconds < _MIN_INTERVAL_SECONDS:
            logger.warning(
                "notice probe interval %ss is below the %ss floor; using the floor — "
                "probing cannot be turned off by setting the interval low",
                self.interval_seconds,
                _MIN_INTERVAL_SECONDS,
            )
            self.interval_seconds = _MIN_INTERVAL_SECONDS

    def current_url(self) -> str:
        """The URL the next engage document should ``<Play>``, or ``""`` for text.

        A memory read, called on the webhook path — never blocks, never awaits.
        """
        return self.url if self.healthy else ""

    async def check_once(self) -> bool:
        """Probe the clip once and record the result. Never raises.

        GET rather than HEAD: Twilio itself GETs the clip, and CDNs mishandle HEAD
        often enough that a HEAD probe would fail clips that play fine.
        """
        if not self.url:
            return True

        import httpx

        reason = ""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                # httpx's timeout bounds each socket operation, not the request: a
                # host trickling one byte per read window strings a probe out
                # arbitrarily — and the boot fetch runs before the app serves, so
                # an unbounded probe is an unbounded outage. wait_for puts a
                # wall-clock ceiling on the whole request; tripping it is doubt,
                # and doubt degrades to text.
                response = await asyncio.wait_for(
                    client.get(self.url), timeout=self.timeout_seconds * 3
                )
            media_type = (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if not response.is_success:
                # Strict 2xx, not "< 400": with redirects followed, a non-2xx
                # final response (an unfollowable 302, a 304) is one Twilio's
                # <Play> would choke on exactly like a 404.
                reason = f"status {response.status_code}"
            elif media_type not in _PLAYABLE_CONTENT_TYPES:
                reason = (
                    f"content-type {media_type or '(missing)'!r} is not one "
                    f"Twilio plays (error 13325)"
                )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"

        await self._record(ok=not reason, reason=reason)
        return not reason

    async def _record(self, *, ok: bool, reason: str) -> None:
        """Apply one probe result, alerting only on transitions — never per probe.

        The health flip happens before the alert, and the alert is guarded: the
        ``Notifier`` Protocol does not promise not to raise, and a notifier that
        did would otherwise kill the probe loop — a dead watchdog that looks
        exactly like a healthy clip.
        """
        if ok and not self.healthy:
            self.healthy = True
            logger.info("notice clip reachable again: %s", self.url)
            await self._alert(
                "Notice clip recovered",
                f"The recorded-line notice clip is reachable again; calls resume "
                f"playing it. ({self.url})",
            )
        elif not ok and self.healthy:
            self.healthy = False
            logger.error(
                "notice clip unreachable (%s): %s — next calls open with the spoken "
                "NOTICE_TEXT fallback instead",
                reason,
                self.url,
            )
            await self._alert(
                "Notice clip unreachable",
                f"The recorded-line notice clip failed its probe ({reason}). Calls "
                f"open with the spoken text notice until it recovers. ({self.url})",
            )

    async def _alert(self, title: str, body: str) -> None:
        try:
            await self.notifier.send(title, body)
        except Exception:
            logger.exception("notifier raised delivering %r; probe loop continues", title)

    async def run(
        self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        """Re-probe forever. Cancelled at shutdown; cancellation propagates.

        ``check_once`` never raises, so the loop cannot die of a probe failure —
        which matters, because a dead watchdog looks exactly like a healthy clip.
        """
        while True:
            await sleep(self.interval_seconds)
            await self.check_once()

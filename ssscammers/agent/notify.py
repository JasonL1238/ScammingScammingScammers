"""Operator alerting over ntfy.

Alerts are advisory by construction: every failure to deliver one is swallowed and
logged, because an alerting path that can raise takes down the thing it exists to
watch. Nothing here runs on the call path, and nothing here can originate contact
with a caller — the only recipient is the operator's own topic (G-1).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ssscammers.shared.config import Settings

logger = logging.getLogger(__name__)

__all__ = ["Notifier", "NtfyNotifier", "NullNotifier", "notifier_from_settings"]


class Notifier(Protocol):
    """Delivers a short operator alert.

    The method is named ``send`` deliberately: the no-outbound scanner bans the name
    ``message`` in call position across the whole package, and it should — an alerting
    API that reads like a messaging API invites exactly the confusion the scanner
    exists to catch.
    """

    async def send(self, title: str, body: str) -> bool:
        """Return True only if the alert was actually delivered."""
        ...


class NullNotifier:
    """Delivers nothing. The default when no ntfy topic is configured."""

    async def send(self, title: str, body: str) -> bool:
        logger.info("no notifier configured; dropping alert %r", title)
        return False


class NtfyNotifier:
    """POSTs alerts to an ntfy topic.

    Plain ntfy protocol: the body is the message text, the ``Title`` header is the
    headline, and an access token (if the topic is protected) rides as a bearer token.
    """

    def __init__(
        self, base_url: str, topic: str, *, token: str = "", timeout: float = 5.0
    ) -> None:
        if not base_url or not topic:
            raise ValueError("NtfyNotifier requires a base URL and a topic")
        self._url = f"{base_url.rstrip('/')}/{topic}"
        self._token = token
        self._timeout = timeout
        self._transport: Any = None
        """Injected in tests so the request this builds can be inspected without a
        network. Left ``None`` in production, which is httpx's own default."""

    async def send(self, title: str, body: str) -> bool:
        import httpx

        headers = {"Title": title}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._url, content=body.encode("utf-8"), headers=headers
                )
            response.raise_for_status()
        except Exception:
            logger.exception("alert %r could not be delivered", title)
            return False
        return True


def notifier_from_settings(settings: Settings) -> Notifier:
    """The configured notifier, or a null one when no topic is set."""
    if settings.ntfy_base_url and settings.ntfy_topic:
        return NtfyNotifier(
            settings.ntfy_base_url, settings.ntfy_topic, token=settings.ntfy_token
        )
    return NullNotifier()

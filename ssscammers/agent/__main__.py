"""Run the agent: ``python -m ssscammers.agent``.

One process, one worker, on purpose. The concurrency cap, the live-call registry, and the
media sockets all live in process memory, so a second worker would answer calls the first
cannot see and the cap would silently double. Passing the app as an object rather than an
import string is what makes that impossible — uvicorn cannot fork workers unless it can
re-import the app itself.

Scaling out would mean the registry in Postgres and sticky routing in front of the
WebSocket: a solution to a problem a handful of calls a day does not have.
"""

from __future__ import annotations

import argparse
import logging

from ssscammers.agent.webhooks import create_app
from ssscammers.shared.config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the inbound call agent.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - Caddy is the edge
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="skip Twilio signature validation — local development only, never deployed",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    logger = logging.getLogger("ssscammers.agent")

    settings = load_settings()
    if args.allow_unsigned:
        logger.warning(
            "signature validation is DISABLED: every webhook endpoint will answer "
            "anyone who can reach it. Do not run this way with a live phone number."
        )

    app = create_app(settings=settings, validate_signatures=not args.allow_unsigned)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

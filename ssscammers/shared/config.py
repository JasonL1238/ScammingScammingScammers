"""Runtime configuration, read once from the environment.

Deliberately stdlib-only: the safety-critical layer — output filter, fiction pack, call
state machine — must be testable with nothing installed, so nothing in its import graph may
pull in a web framework or a settings library.

Caps live here rather than in each component because the original design had three
different concurrency limits in three files. There is one of each now.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import TypeVar

__all__ = ["Settings", "load_settings"]


logger = logging.getLogger(__name__)

_Number = TypeVar("_Number", int, float)

_LEDGER_DEFAULT = "/var/lib/ssscammers/daily_ledger.json"
"""Absolute on purpose: a relative path lands wherever the image's working directory
happens to be, which is the container's ephemeral writable layer. Mounted as a named
volume by ``docker-compose.yml``."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_number(name: str, default: _Number) -> _Number:
    """A numeric setting, falling back to ``default`` if unset or unparseable.

    The result takes ``default``'s type, so the call site declares int or float once
    rather than choosing between two near-identical readers.
    """
    raw = _env(name)
    if not raw:
        return default
    try:
        return type(default)(raw)
    except ValueError:
        # Echo short values only: this branch runs exactly when an operator
        # mangled .env, and one such mangling is pasting a secret onto a caps
        # line — the log stream must never capture it. Every valid numeric
        # shape fits well inside the cutoff, so diagnostics lose nothing.
        shown = repr(raw) if len(raw) <= 16 else f"a {len(raw)}-character value (not shown)"
        logger.warning(
            "%s=%s is not a valid %s; using %r — check for a typo, because a mistyped "
            "cap silently reverts to a default that may be laxer than intended",
            name,
            shown,
            type(default).__name__,
            default,
        )
        return default


def _positive_rate(value: float, default: float) -> float:
    """A finite, positive cost rate, or ``default``.

    ``float()`` happily accepts ``"nan"``, and every comparison against NaN is False — so
    ``ESTIMATED_USD_PER_CALL_MINUTE=nan`` made ``usd >= cap`` unfalsifiable and switched
    the daily spend cap off with no error. Zero and negatives do the same more obviously.
    """
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            "ESTIMATED_USD_PER_CALL_MINUTE=%r is not a positive finite number; using %r, "
            "because a bad rate silently disables the daily spend cap",
            value,
            default,
        )
        return default
    return value


def _env_list(name: str) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Everything the agent needs to know that is not in a persona bundle."""

    # --- Twilio ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    honeypot_number: str = ""
    """**Not used yet.** Nothing reads this: the DID is configured in the Twilio
    console, and the agent never checks the ``To`` field of an inbound webhook.
    Kept loaded so the intended seam (own-number awareness) stays on record."""

    public_base_url: str = ""
    media_stream_path_token: str = ""

    notice_audio_url: str = ""
    """G-2. Pre-rendered recorded-line notice, played by Twilio before the agent speaks.

    Empty falls back to fixed synthesised text (``twiml.NOTICE_TEXT``) — degraded,
    because a caller can hear it is a robot, but never absent. The notice is the
    consent record; it is not allowed to depend on an asset being uploaded.
    """

    notice_probe_interval_seconds: int = 60
    """How often :class:`ssscammers.agent.notice.NoticeHealth` re-probes the clip.

    Bounds the window in which a clip that died after boot can still be handed to
    Twilio — during that window callers are connected and recorded with no audible
    notice, which is the G-2 hole the probe exists to close."""

    default_persona: str = "marjorie"
    """Which character answers. Chosen at admission so the whole call is one persona."""

    voicemail_max_seconds: int = 120

    # --- Model and speech providers ---
    anthropic_api_key: str = ""

    anthropic_model: str = ""
    """Overrides :data:`ssscammers.agent.llm.MODEL`. Empty uses that default.

    Env-driven because the alternative is a source edit, a test edit and a redeploy while
    every turn takes ``_generate``'s except-Exception path and speaks a fumble line — the
    recovery path for a model being retired or regionally unavailable should be a restart.
    A value here is passed through unvalidated: the request surface in ``llm.py`` (thinking
    disabled, effort, no sampling parameters, no mid-conversation system role) is specific
    to Sonnet 5, and an older model may reject it. See that module before setting this.
    """

    anthropic_effort: str = ""
    """Overrides :data:`ssscammers.agent.llm.EFFORT`. Empty uses that default (``low``).

    Raising this buys depth the personas do not need and spends both latency and output
    tokens on every turn of every call. It exists so a believability problem can be tested
    against effort without a deploy, not as a routine knob.
    """
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""

    elevenlabs_api_key: str = ""
    """**Not used yet.** No code path selects ElevenLabs.

    ``media.IMPLEMENTED_TTS`` is Cartesia-only, so a persona bundle written for ElevenLabs
    (``personas/dot``) is refused at call setup rather than voiced by the wrong provider.
    Provisioning this key does not make that bundle work; wiring a second TTS service into
    :func:`ssscammers.agent.media._serve_call` does.
    """

    # --- Storage ---
    database_url: str = ""
    """**Not read by the agent.** The call path never touches a database; the
    agent-side persistence layer is scheduled roadmap work. The migration runner
    (``python -m ssscammers.db``) reads ``$DATABASE_URL`` from the environment
    directly — a one-shot tool deliberately not coupled to agent settings."""

    # --- Notifications. Values only: the HTTP client lives in agent/notify.py,
    # because this module must stay importable with zero third-party packages. ---
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    """Empty disables alerting entirely (a NullNotifier is used)."""
    ntfy_token: str = ""

    # --- Owner identity. Real values; the filter blocks them, never speaks them. ---
    owner_pii_denylist: tuple[str, ...] = ()
    owner_real_number: str = ""
    owner_safeword: str = "pineapple"

    # --- Caps. One definition each; see plan items C10, C11, G-14..G-16. ---
    max_concurrent_calls: int = 5
    hard_call_cap_seconds: int = 5400
    """G-14. Absolute ceiling, enforced by a pipeline timer, never by the model."""

    soft_call_cap_seconds: int = 3600
    """Past this the persona starts looking for a warm way out."""

    dead_air_hangup_seconds: int = 60
    """G-16. No caller audio for this long and we stop paying for an empty line."""

    probation_seconds: int = 30
    """How long the persona stays neutral while triage decides who is calling."""

    probation_hard_commit_seconds: int = 90
    """A real caller states their business well before this. After it, commit."""

    daily_ledger_path: str = _LEDGER_DEFAULT
    """Where today's minute and spend totals live.

    Must be on a volume that survives a *deploy*, not merely a restart — ``docker-compose``
    mounts ``agentstate`` here for exactly that reason. A path in the container filesystem
    resets the counters on every ``up --build``, which is the routine operation.
    """

    estimated_usd_per_call_minute: float = 0.05
    """Cost estimate used by ``daily_spend_cap_usd``. **Not a measurement.** Roughly
    $0.035/min of telephony, speech recognition and synthesis plus ~$0.008/min of model
    spend on a cached long call, rounded up. Reconcile against real invoices; the minutes
    cap is the one that is exactly measurable."""

    daily_minutes_cap: int = 360
    daily_spend_cap_usd: int = 50
    repeat_caller_daily_cap: int = 5

    @property
    def media_stream_url(self) -> str:
        """WebSocket URL handed to Twilio in the ``<Stream>`` verb."""
        base = self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base.rstrip('/')}/twilio/media/{self.media_stream_path_token}"

    def require(self, *names: str) -> None:
        """Raise if any named setting is empty.

        Called at startup by components that cannot run without a value, so a missing key
        fails loudly at boot instead of silently mid-call.
        """
        missing = [name for name in names if not getattr(self, name, "")]
        if missing:
            raise RuntimeError(
                "missing required configuration: "
                + ", ".join(sorted(missing))
                + " (see .env.example)"
            )


def load_settings() -> Settings:
    """Build settings from the process environment."""
    return Settings(
        twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
        honeypot_number=_env("TWILIO_HONEYPOT_NUMBER"),
        public_base_url=_env("PUBLIC_BASE_URL"),
        media_stream_path_token=_env("MEDIA_STREAM_PATH_TOKEN"),
        notice_audio_url=_env("NOTICE_AUDIO_URL"),
        notice_probe_interval_seconds=_env_number("NOTICE_PROBE_INTERVAL_SECONDS", 60),
        ntfy_base_url=_env("NTFY_BASE_URL", "https://ntfy.sh") or "https://ntfy.sh",
        ntfy_topic=_env("NTFY_TOPIC"),
        ntfy_token=_env("NTFY_TOKEN"),
        default_persona=_env("DEFAULT_PERSONA", "marjorie") or "marjorie",
        voicemail_max_seconds=_env_number("VOICEMAIL_MAX_SECONDS", 120),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL"),
        anthropic_effort=_env("ANTHROPIC_EFFORT"),
        deepgram_api_key=_env("DEEPGRAM_API_KEY"),
        cartesia_api_key=_env("CARTESIA_API_KEY"),
        elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
        database_url=_env("DATABASE_URL"),
        owner_pii_denylist=_env_list("OWNER_PII_DENYLIST"),
        owner_real_number=_env("OWNER_REAL_NUMBER"),
        owner_safeword=_env("OWNER_SAFEWORD", "pineapple") or "pineapple",
        max_concurrent_calls=_env_number("MAX_CONCURRENT_CALLS", 5),
        hard_call_cap_seconds=_env_number("HARD_CALL_CAP_SECONDS", 5400),
        soft_call_cap_seconds=_env_number("SOFT_CALL_CAP_SECONDS", 3600),
        dead_air_hangup_seconds=_env_number("DEAD_AIR_HANGUP_SECONDS", 60),
        probation_seconds=_env_number("PROBATION_SECONDS", 30),
        probation_hard_commit_seconds=_env_number("PROBATION_HARD_COMMIT_SECONDS", 90),
        daily_ledger_path=_env("DAILY_LEDGER_PATH", _LEDGER_DEFAULT) or _LEDGER_DEFAULT,
        estimated_usd_per_call_minute=_positive_rate(
            _env_number("ESTIMATED_USD_PER_CALL_MINUTE", 0.05), 0.05
        ),
        daily_minutes_cap=_env_number("DAILY_MINUTES_CAP", 360),
        daily_spend_cap_usd=_env_number("DAILY_SPEND_CAP_USD", 50),
        repeat_caller_daily_cap=_env_number("REPEAT_CALLER_DAILY_CAP", 5),
    )

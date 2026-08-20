"""The single classification vocabulary.

Three stages of the system label calls, and each once had its own incompatible label set:
realtime triage, realtime tactic steering, and post-call enrichment. Everything imports
from here, and the SQL enums in ``db/migrations`` are checked against these values, so a
label written by the agent is readable by the dashboard without translation.

Two jobs are deliberately kept apart:

* **Triage** answers "is this a scammer or someone who actually wants the owner?" It runs
  live and drives the safety valve — a wrong answer means baiting a real person or letting
  a scammer walk.
* **Scam type** answers "what kind of scam is this?" The realtime guess only steers tactics
  and is allowed to be wrong; the authoritative label comes from enrichment, which has the
  whole transcript and no latency budget.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "TriageClass",
    "ScamType",
    "CallerClass",
    "CallPhase",
    "EntryPath",
    "EndReason",
    "CallStatus",
    "TurnRole",
    "CallerKind",
    "Tactic",
    "LabelSource",
    "TERMINAL_PHASES",
    "BAITABLE_TRIAGE",
]


class TriageClass(StrEnum):
    """Who is on the line. Drives the safety valve, not the comedy."""

    UNCLEAR = "unclear"
    """Not enough signal yet. The persona stays neutral while this holds."""

    SCAM = "scam"
    """A live human running a fraud script. The only class we fully bait."""

    ROBOCALL = "robocall"
    """A recording or IVR, no human attending yet. Bait it; a human may arrive."""

    LEAD_GEN = "lead_gen"
    """A call center dialing a purchased list. Legal but unwanted; baitable by policy."""

    LEGIT_BUSINESS = "legit_business"
    """A real business with real business (pharmacy, dentist). Disclose and exit."""

    LEGIT_PERSONAL = "legit_personal"
    """A human trying to reach the owner personally. Disclose and exit."""

    VICTIM_CALLBACK = "victim_callback"
    """Someone a scammer sent here. Disclose, warn them plainly, exit."""

    SILENCE = "silence"
    """Dead air or a dropped predictive-dialer leg."""


#: Triage classes the persona is allowed to keep baiting. Anything outside this set
#: routes to a disclosure exit — this is the whitelist, so a new class added later
#: fails safe (no baiting) rather than fails open.
BAITABLE_TRIAGE: frozenset[TriageClass] = frozenset(
    {TriageClass.SCAM, TriageClass.ROBOCALL, TriageClass.LEAD_GEN}
)


class ScamType(StrEnum):
    """Which script the caller is running. Authoritative value comes from enrichment."""

    UNKNOWN = "unknown"
    IRS_TAX = "irs_tax"
    SSA_BENEFITS = "ssa_benefits"
    LAW_ENFORCEMENT = "law_enforcement"
    TECH_SUPPORT = "tech_support"
    BANK_FRAUD_DEPT = "bank_fraud_dept"
    CARD_OTP_VERIFICATION = "card_otp_verification"
    REFUND_OVERPAYMENT = "refund_overpayment"
    GIFT_CARD = "gift_card"
    CRYPTO_INVESTMENT = "crypto_investment"
    GRANDPARENT = "grandparent"
    MEDICARE = "medicare"
    AUTO_WARRANTY = "auto_warranty"
    UTILITY_SHUTOFF = "utility_shutoff"
    DELIVERY_PACKAGE = "delivery_package"
    OTHER = "other"


class CallerClass(StrEnum):
    """Standing reputation of a phone number, accumulated across calls."""

    UNKNOWN = "unknown"
    SCAMMER = "scammer"
    LEAD_GEN = "lead_gen"
    ROBOCALL = "robocall"
    LEGIT = "legit"
    BLOCKED = "blocked"


class CallPhase(StrEnum):
    """The merged call state machine.

    The happy path walks GREETING -> ASSESSING -> HOOK -> STALL -> WIND_DOWN. The three
    exits are reachable from *every* state, including mid-HOOK: whatever the persona is
    doing, discovering a real person on the line outranks it.
    """

    GREETING = "greeting"
    """Neutral "…Hello?". Indistinguishable from a real person picking up."""

    ASSESSING = "assessing"
    """Probation. Still neutral; triage is deciding. No stalling shtick yet."""

    HOOK = "hook"
    """Committed to baiting. Perform believable interest so the scammer invests."""

    STALL = "stall"
    """The bulk of the call, and where the minutes come from."""

    WIND_DOWN = "wind_down"
    """Past the soft cap or the scammer is losing patience. Dangle hope, exit warmly."""

    DISCLOSE_EXIT = "disclose_exit"
    """Not a scammer. Drop persona, say we're an automated assistant, hand to voicemail."""

    EMERGENCY_EXIT = "emergency_exit"
    """Credible real-world danger. Redirect to 911 and hang up. Never stall."""

    TERMINATE = "terminate"
    """Hard stop: caps, watchdog kill, threats, dead air, caller hangup."""


#: Phases from which no further conversation happens. Also the exits reachable from
#: any phase, which the state machine checks before ordinary progression.
TERMINAL_PHASES: frozenset[CallPhase] = frozenset(
    {CallPhase.DISCLOSE_EXIT, CallPhase.EMERGENCY_EXIT, CallPhase.TERMINATE}
)


class EntryPath(StrEnum):
    """How the call reached the honeypot."""

    DIRECT = "direct"
    """Dialed our seeded number. Presumed hostile, still triaged."""

    CONDITIONAL_FORWARD = "conditional_forward"
    """Rolled over from the owner's real cell. Presumed possibly-legit until proven otherwise."""

    UNKNOWN = "unknown"


class CallStatus(StrEnum):
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ENRICHING = "enriching"
    ENRICHED = "enriched"


class EndReason(StrEnum):
    CALLER_HANGUP = "caller_hangup"
    AGENT_HANGUP = "agent_hangup"
    MAX_DURATION = "max_duration"
    DEAD_AIR = "dead_air"
    WATCHDOG_KILL = "watchdog_kill"
    DISCLOSED_EXIT = "disclosed_exit"
    EMERGENCY_EXIT = "emergency_exit"
    SPEND_CAP = "spend_cap"
    PIPELINE_ERROR = "pipeline_error"
    TWILIO_ERROR = "twilio_error"


class TurnRole(StrEnum):
    CALLER = "caller"
    AGENT = "agent"


class CallerKind(StrEnum):
    """Whether a *caller* turn came from a person. Feeds the human-time-wasted metric."""

    UNKNOWN = "unknown"
    LIVE_HUMAN = "live_human"
    RECORDING = "recording"
    IVR = "ivr"


class Tactic(StrEnum):
    """Stalling moves. Realtime selection is a hint; enrichment relabels authoritatively."""

    NONE = "none"
    MISHEAR = "mishear"
    """Mis-parse a load-bearing word — the amount, the agency, the name."""

    READ_BACK = "read_back"
    """Repeat it back wrong so they have to correct you."""

    FUMBLE_DATA = "fumble_data"
    """Try to comply with a data request and never once succeed."""

    TANGENT = "tangent"
    """Hijack the turn with an unrelated story and don't come back unprompted."""

    HOLD_ON = "hold_on"
    """Leave the phone. Kettle, doorbell, glasses, the other handset."""

    TECH_ILLITERACY = "tech_illiteracy"
    """Cannot find the button, the key, the window, the internet."""

    EAGER_NONCONVERGENCE = "eager_nonconvergence"
    """Enthusiastic agreement plus a question that resets all progress."""

    REPEAT_REQUEST = "repeat_request"
    """"Say that again, dear?" — cheapest minute in the playbook."""


class LabelSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


"""The single classification vocabulary.

Three stages of the system are designed to label calls, and each once had its own
incompatible label set: realtime triage, realtime tactic steering, and post-call
enrichment (the third is planned, not built). Everything imports from here, and the SQL
enums in ``db/migrations`` are checked against these values, so a label written by the
agent will be readable by the planned dashboard without translation.

Two jobs are deliberately kept apart:

* **Triage** answers "is this a scammer or someone who actually wants the owner?" It runs
  live and drives the safety valve — a wrong answer means baiting a real person or letting
  a scammer walk.
* **Scam type** answers "what kind of scam is this?" The realtime guess only steers tactics
  and is allowed to be wrong; the authoritative label will come from planned enrichment,
  which will have the whole transcript and no latency budget.
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
    "MonitorFinding",
    "LabelSource",
    "TERMINAL_PHASES",
    "BAITING_PHASES",
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
    """Which script the caller is running. The authoritative value will come from
    planned enrichment."""

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


#: Phases where stalling tactics are allowed to run — the phases a *real* caller
#: must never reach. Defined once because two very different consumers must agree
#: on it: ``CallStateMachine.baiting`` gates behavior, and the misroute gate
#: asserts the absence. A phase added to one and not the other would leave the
#: gate blind to the engagement it was written to catch.
BAITING_PHASES: frozenset[CallPhase] = frozenset(
    {CallPhase.HOOK, CallPhase.STALL, CallPhase.WIND_DOWN}
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
    """Stalling moves. Realtime selection is a hint; planned enrichment will relabel
    authoritatively."""

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


class MonitorFinding(StrEnum):
    """What the out-of-band watchdog (G-17) says it saw.

    Evidence attached to a kill, not the decision itself — the decision is a
    boolean, and it is deliberately not gated on a finding being in this set. A
    classifier that returns a label nobody has thought of yet still kills the
    call and still gets its label into the log verbatim; see
    :class:`ssscammers.agent.monitor.Verdict`. This enum is the vocabulary the
    classifier is *asked* for, and the one anything downstream can rely on
    understanding.

    One value per guardrail the monitor is designed to cover, named after the
    violation rather than the number, because ``G-8`` in a log line tells a
    reader nothing without the table open beside them.

    **No SQL type mirrors this yet**, which is why it is absent from
    ``tests/test_schema_enums.py``'s ``PAIRS``. Verdicts ride the in-memory
    event log today; persisting them is Phase 5 work, and that is where the
    ``CREATE TYPE`` decision belongs.
    """

    PERSONA_BREAK = "persona_break"
    """G-17. The agent admitted to being an AI, or otherwise dropped character,
    outside the fixed disclosure scripts."""

    TRANSACTION_COMPLETED = "transaction_completed"
    """G-5. The agent carried a payment, transfer, or verification to completion."""

    IDENTITY_CONFIRMED = "identity_confirmed"
    """G-7. The agent confirmed a name, account, or detail the caller offered."""

    REAL_ENTITY_CLAIM = "real_entity_claim"
    """G-8. The agent claimed to be, or to speak for, a real company, agency, or
    person."""

    FRAUD_ASSISTANCE = "fraud_assistance"
    """G-9. The agent helped the caller defraud somebody else."""

    ABUSIVE_TONE = "abusive_tone"
    """G-10. Past the tone ceiling: cruelty, slurs, threats. Mildly annoying is
    the whole permitted range."""

    INJECTION_COMPLIANCE = "injection_compliance"
    """G-19. The agent followed an instruction embedded in caller speech."""

    REAL_DATA_SPOKEN = "real_data_spoken"
    """G-3, as a second opinion. The deterministic filter is the enforcement;
    this catches what a denylist cannot phrase."""

    FINANCIAL_INSTRUMENT_SPOKEN = "financial_instrument_spoken"
    """G-4, as a second opinion, on the same terms as
    :attr:`REAL_DATA_SPOKEN`."""


class LabelSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


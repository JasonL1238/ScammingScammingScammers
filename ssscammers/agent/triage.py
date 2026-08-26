"""Deciding who is actually on the line.

This is the safety valve, not the comedy. Conditional call forwarding means the owner's
declined dentist appointment lands here exactly like a scam call does, so being wrong in
the "that's a scammer" direction means baiting a real person who needed something. The
classifier is therefore *quick to stop* and *slow to start*: evidence that someone is
genuine weighs more than evidence that they are not, and the state machine applies a lower
confidence bar to exiting than to committing.

Deliberately deterministic. A keyword-and-pattern classifier over the running transcript
costs nothing, adds no latency, and behaves identically in tests and in production. A
model-backed opinion can refine it later; it is not on the critical path for protecting a
real caller.

Scoring accumulates across turns rather than resetting: one ambiguous sentence proves very
little, and scam scripts and real business both take a few turns to show themselves.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from ssscammers.shared.enums import CallerClass, ScamType, TriageClass
from ssscammers.shared.validators import national_digits

__all__ = ["TriageResult", "TriageEngine", "AllowlistCache", "SignalHit"]


def _phrases(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# --- Evidence that this is a fraud script -----------------------------------------
# Weights are rough "how much does this one phrase tell us" values, summed across
# turns. Nothing here is individually conclusive; a threat plus a payment demand is.

_SCAM_SIGNALS: tuple[tuple[tuple[re.Pattern[str], ...], float, ScamType], ...] = (
    (
        _phrases(
            r"\barrest warrant\b", r"\bwarrant for your arrest\b", r"\blegal action against you\b",
            r"\bsuspended\b.{0,30}\b(?:social security|benefits?)\b", r"\bback taxes\b",
        ),
        0.45,
        ScamType.IRS_TAX,
    ),
    (
        _phrases(r"\bsocial security (?:number|administration)\b", r"\bmedicare\b"),
        0.2,
        ScamType.SSA_BENEFITS,
    ),
    (
        _phrases(
            r"\byour (?:computer|pc|router|device) (?:is|has been) (?:infected|compromised|hacked)\b",
            r"\bremote (?:access|desktop|support)\b", r"\banydesk\b", r"\bteamviewer\b",
            r"\bpress (?:the )?windows key\b",
        ),
        0.45,
        ScamType.TECH_SUPPORT,
    ),
    (
        _phrases(
            r"\bfraud department\b", r"\bsuspicious (?:charge|activity|transaction)\b",
            r"\bverify your (?:card|account|identity)\b",
        ),
        0.4,
        ScamType.BANK_FRAUD_DEPT,
    ),
    (
        _phrases(
            r"\bread me the code\b", r"\bcode we (?:just )?(?:sent|texted)\b",
            r"\bone[- ]time (?:code|password|pin)\b", r"\bsix[- ]digit code\b",
        ),
        0.5,
        ScamType.CARD_OTP_VERIFICATION,
    ),
    (
        _phrases(
            r"\brefund(?:ed)? (?:too much|the wrong amount)\b", r"\bowe us the difference\b",
            r"\breturn the (?:extra|overpayment)\b",
        ),
        0.45,
        ScamType.REFUND_OVERPAYMENT,
    ),
    (
        _phrases(
            r"\bgift cards?\b", r"\bgoogle play cards?\b", r"\bapple cards?\b",
            r"\bsteam cards?\b", r"\bscratch (?:off|the strip)\b",
        ),
        0.5,
        ScamType.GIFT_CARD,
    ),
    (
        _phrases(r"\bbitcoin\b", r"\bcrypto\b", r"\bwallet address\b", r"\bguaranteed returns?\b"),
        0.4,
        ScamType.CRYPTO_INVESTMENT,
    ),
    (
        _phrases(
            r"\bit'?s your (?:grand)?son\b", r"\bgrandma,? it'?s me\b",
            r"\bdon'?t tell (?:mum|mom|my parents)\b", r"\bi'?m in jail\b", r"\bpost(?:ed)? bail\b",
        ),
        0.5,
        ScamType.GRANDPARENT,
    ),
    (
        _phrases(r"\bauto warranty\b", r"\bvehicle'?s? warranty\b", r"\bextended warranty\b"),
        0.5,
        ScamType.AUTO_WARRANTY,
    ),
    (
        _phrases(r"\bdisconnect(?:ed|ion)?\b.{0,25}\b(?:power|electric|gas|utility)\b"),
        0.4,
        ScamType.UTILITY_SHUTOFF,
    ),
    (
        _phrases(r"\bundeliverable package\b", r"\bcustoms fee\b", r"\bdelivery (?:fee|attempt)\b"),
        0.3,
        ScamType.DELIVERY_PACKAGE,
    ),
)

#: Pressure mechanics, scored one at a time rather than once per group. The groups above
#: are synonym sets, where counting two phrasings would double-count one fact. These are
#: independent: not hanging up, keeping it secret, and being steered toward a wire are
#: three separate things about a call, and a script doing all three is more obviously one.
_PRESSURE_SIGNALS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\bdo not hang up\b", re.IGNORECASE), 0.2),
    (re.compile(r"\bstay on the line\b", re.IGNORECASE), 0.2),
    (re.compile(r"\bdon'?t tell anyone\b", re.IGNORECASE), 0.25),
    (re.compile(r"\bwire transfer\b", re.IGNORECASE), 0.3),
    (re.compile(r"\bwestern union\b", re.IGNORECASE), 0.35),
    (re.compile(r"\bmoneygram\b", re.IGNORECASE), 0.35),
    (
        re.compile(
            r"\byour account (?:will|is going to) be (?:closed|frozen|suspended)\b", re.IGNORECASE
        ),
        0.25,
    ),
)

# --- Evidence that this is a person with real business ----------------------------
# Weighted higher than scam evidence, on purpose. Missing a scammer costs a few
# wasted minutes; baiting a pharmacist costs something that matters.

_LEGIT_SIGNALS: tuple[tuple[tuple[re.Pattern[str], ...], float], ...] = (
    (
        _phrases(
            r"\b(?:your )?prescription (?:is ){0,2}ready\b", r"\bthe pharmacy\b",
            r"\bdoctor'?s office\b", r"\bdental (?:office|practice)\b", r"\bsurgery\b",
            r"\bconfirm(?:ing)? your appointment\b", r"\breschedul\w+ your appointment\b",
        ),
        0.55,
    ),
    (
        _phrases(
            r"\bthis is \w+ (?:from|at|calling from) (?:the )?\w+",
            r"\bcalling (?:you )?(?:back )?about (?:your|the) (?:order|delivery|appointment|repair)\b",
        ),
        0.3,
    ),
    (
        _phrases(
            r"\bsorry,? (?:i think )?(?:i'?ve got the )?wrong number\b",
            r"\bis this not \w+\b", r"\bi must have (?:mis)?dialled\b",
        ),
        0.6,
    ),
    (
        # Someone a scammer pointed here — the saddest case, and one we must catch.
        _phrases(
            r"\bthey told me to call this number\b", r"\bi was told to (?:ring|call) (?:this|you)\b",
            r"\bsomeone (?:called|rang) me (?:and said|about)\b",
        ),
        0.5,
    ),
)

#: Callers who volunteer who they are, unprompted, are almost never running a script.
_SELF_IDENTIFY = _phrases(r"\bthis is \w+\b", r"\bmy name is \w+\b", r"\bi'?m calling from\b")

#: A recording rather than a person.
_ROBOCALL_SIGNALS = _phrases(
    r"\bpress (?:one|two|1|2)\b", r"\bthis is an? (?:important|automated) (?:message|call)\b",
    r"\bto speak (?:with|to) (?:a|an) (?:representative|agent)\b",
)

_LEAD_GEN_SIGNALS = _phrases(
    r"\byou (?:recently )?(?:requested|filled out|submitted)\b.{0,30}\bquote\b",
    r"\bfinal expense\b", r"\bsolar\b", r"\bfree (?:quote|estimate|consultation)\b",
)

_EMERGENCY_SIGNALS = _phrases(
    r"\bi'?m (?:being|about to be) (?:attacked|hurt|killed)\b",
    r"\bthere'?s (?:a|an) (?:fire|intruder|break[- ]in)\b",
    r"\bcall (?:the police|911) (?:for me|please)\b",
    r"\bi (?:can'?t breathe|am bleeding)\b",
)

_THREAT_SIGNALS = _phrases(
    r"\bi know where you live\b", r"\bi'?ll (?:find|come to) (?:you|your house)\b",
    r"\bi'?ll kill you\b", r"\bi'?ll burn\b",
)


@dataclass(frozen=True)
class SignalHit:
    """One matched phrase, kept so a call's classification can be explained.

    Deduplicated per call: a looping robocall repeating "do not hang up" for
    ninety minutes is one hit with a large ``count``, not an unbounded list —
    the caller controls how often a phrase repeats, never how much evidence the
    log stores. ``weight`` stays the per-match score contribution (a constant
    for each pattern), so the dashboard's sort order survives the merge.
    """

    pattern: str
    weight: float
    toward: str
    count: int = 1


@dataclass(frozen=True)
class TriageResult:
    """A classification plus enough context to justify it in the planned dashboard."""

    triage: TriageClass
    confidence: float
    scam_type: ScamType = ScamType.UNKNOWN
    emergency: bool = False
    threat: bool = False
    hits: tuple[SignalHit, ...] = ()

    @property
    def explanation(self) -> str:
        if not self.hits:
            return "no distinguishing signals yet"
        top = sorted(self.hits, key=lambda h: h.weight, reverse=True)[:3]
        return "; ".join(f"{h.toward}: {h.pattern}" for h in top)

    def as_payload(self) -> dict[str, Any]:
        """The verdict flattened for the canonical event log.

        Lives here, beside the fields it serializes, so a field added to this
        dataclass cannot be silently dropped by a serializer a module away —
        which is exactly how emergency and threat went missing from the first
        draft of the caller_turn widening.
        """
        return {
            "triage": self.triage.value,
            "triage_confidence": self.confidence,
            "scam_type": self.scam_type.value,
            "emergency": self.emergency,
            "threat": self.threat,
            "signals": [
                {
                    "pattern": h.pattern,
                    "weight": h.weight,
                    "toward": h.toward,
                    "count": h.count,
                }
                for h in self.hits
            ],
        }


@dataclass
class TriageEngine:
    """Accumulates evidence across the turns of one call.

    Feed every caller turn to :meth:`observe`; read :meth:`result` whenever the state
    machine needs a verdict.
    """

    safeword: str = ""

    _scam_score: float = 0.0
    _legit_score: float = 0.0
    _robocall_score: float = 0.0
    _lead_gen_score: float = 0.0
    _scam_type_scores: dict[ScamType, float] = field(default_factory=dict)
    #: Keyed by (pattern, toward), insertion-ordered: first-seen order keeps the
    #: serialized tuple byte-stable under seeded replay, and the fixed pattern
    #: space bounds both this dict and every event payload built from it.
    _hits: dict[tuple[str, str], SignalHit] = field(default_factory=dict)
    _emergency: bool = False
    _threat: bool = False
    _heard_safeword: bool = False
    _turns: int = 0

    @property
    def heard_safeword(self) -> bool:
        return self._heard_safeword

    @property
    def emergency(self) -> bool:
        return self._emergency

    @property
    def threat(self) -> bool:
        return self._threat

    def observe(self, utterance: str) -> None:
        """Fold one caller turn into the running evidence."""
        if not utterance or not utterance.strip():
            return
        self._turns += 1
        text = utterance.strip()

        if self.safeword and re.search(rf"\b{re.escape(self.safeword)}\b", text, re.IGNORECASE):
            self._heard_safeword = True

        # Emergency and threat drive boolean exits, not scores — the hits carry
        # weight 0.0 so "weight" keeps meaning "score contribution" — but the
        # evidence must exist: an emergency exit whose caller_turn shows no
        # signals is a log that cannot explain the most consequential decision
        # the system makes.
        for pattern in _EMERGENCY_SIGNALS:
            if pattern.search(text):
                self._emergency = True
                self._hit(pattern.pattern, 0.0, "emergency")
                break
        for pattern in _THREAT_SIGNALS:
            if pattern.search(text):
                self._threat = True
                self._hit(pattern.pattern, 0.0, "threat")
                break

        for patterns, weight, scam_type in _SCAM_SIGNALS:
            for pattern in patterns:
                if pattern.search(text):
                    self._scam_score += weight
                    self._hit(pattern.pattern, weight, "scam")
                    if scam_type is not ScamType.UNKNOWN:
                        self._scam_type_scores[scam_type] = (
                            self._scam_type_scores.get(scam_type, 0.0) + weight
                        )
                    break  # synonyms of one fact: score the group once per turn

        # Pressure mechanics are independent of each other, so each one counts.
        for pattern, weight in _PRESSURE_SIGNALS:
            if pattern.search(text):
                self._scam_score += weight
                self._hit(pattern.pattern, weight, "scam")

        for patterns, weight in _LEGIT_SIGNALS:
            for pattern in patterns:
                if pattern.search(text):
                    self._legit_score += weight
                    self._hit(pattern.pattern, weight, "legit")
                    break

        # Volunteering an identity early reads as genuine, but only before a scam
        # script has shown itself — "this is Officer Reed from the IRS" is not a point
        # in anyone's favour.
        if self._scam_score < 0.3 and any(p.search(text) for p in _SELF_IDENTIFY):
            self._legit_score += 0.15
            self._hit("self-identified", 0.15, "legit")

        for pattern in _ROBOCALL_SIGNALS:
            if pattern.search(text):
                self._robocall_score += 0.4
                self._hit(pattern.pattern, 0.4, "robocall")
                break

        for pattern in _LEAD_GEN_SIGNALS:
            if pattern.search(text):
                self._lead_gen_score += 0.4
                self._hit(pattern.pattern, 0.4, "lead_gen")
                break

    def _hit(self, pattern: str, weight: float, toward: str) -> None:
        """Record evidence, deduplicated: a repeat raises ``count``, never the size."""
        key = (pattern, toward)
        existing = self._hits.get(key)
        if existing is None:
            self._hits[key] = SignalHit(pattern, weight, toward)
        else:
            self._hits[key] = replace(existing, count=existing.count + 1)

    def result(self) -> TriageResult:
        """Current verdict.

        ``emergency`` and ``threat`` ride on every branch: an emergency is most
        often detected while the classification is still UNCLEAR, and a result
        that reported ``emergency=False`` there would be confidently wrong in
        the log about the one detection that ends the call.
        """

        def verdict(
            triage: TriageClass, confidence: float, scam_type: ScamType = ScamType.UNKNOWN
        ) -> TriageResult:
            return TriageResult(
                triage,
                confidence,
                scam_type=scam_type,
                emergency=self._emergency,
                threat=self._threat,
                hits=tuple(self._hits.values()),
            )

        if self._turns == 0:
            return verdict(TriageClass.UNCLEAR, 0.0)

        # A real-person read wins ties and near-ties. Stopping is cheaper than
        # wrongly continuing, so the comparison is deliberately not symmetric.
        if self._legit_score >= 0.45 and self._legit_score >= self._scam_score * 0.8:
            triage = (
                TriageClass.VICTIM_CALLBACK
                if any("told me to call" in h.pattern for h in self._hits.values())
                else TriageClass.LEGIT_BUSINESS
            )
            return verdict(triage, min(0.95, self._legit_score))

        if self._scam_score >= 0.4:
            return verdict(
                TriageClass.SCAM, min(0.98, self._scam_score), scam_type=self._best_scam_type()
            )

        if self._lead_gen_score >= 0.4 and self._lead_gen_score > self._robocall_score:
            return verdict(TriageClass.LEAD_GEN, min(0.9, self._lead_gen_score))

        if self._robocall_score >= 0.4:
            return verdict(TriageClass.ROBOCALL, min(0.9, self._robocall_score))

        # Some evidence, not enough of it.
        residual = max(self._scam_score, self._legit_score)
        return verdict(TriageClass.UNCLEAR, min(0.4, residual))

    def _best_scam_type(self) -> ScamType:
        if not self._scam_type_scores:
            return ScamType.UNKNOWN
        return max(self._scam_type_scores.items(), key=lambda kv: kv[1])[0]


@dataclass
class AllowlistCache:
    """In-process view of who is known-good, known-bad, and known-scammy.

    The Twilio webhook has to answer in well under a second, so this never queries anything
    on the call path. Postgres is intended to be the system of record; the ``loader`` seam
    is where a refresh-on-write-and-interval will attach once persistence lands. Today
    production builds this cache with ``loader=None`` and nothing populates it — it stays
    empty for the process lifetime; ``set``/``bulk_set`` are the seams tests and the
    future wiring use.
    """

    loader: Callable[[], dict[str, CallerClass]] | None = None
    _by_number: dict[str, CallerClass] = field(default_factory=dict)

    def refresh(self) -> None:
        if self.loader is not None:
            self._by_number = {national_digits(k): v for k, v in self.loader().items()}

    def set(self, number: str, classification: CallerClass) -> None:
        self._by_number[national_digits(number)] = classification

    def bulk_set(self, entries: Iterable[tuple[str, CallerClass]]) -> None:
        for number, classification in entries:
            self.set(number, classification)

    def classification(self, number: str) -> CallerClass:
        return self._by_number.get(national_digits(number), CallerClass.UNKNOWN)

    def is_allowlisted(self, number: str) -> bool:
        """True for numbers that must never be baited."""
        return self.classification(number) is CallerClass.LEGIT

    def is_blocked(self, number: str) -> bool:
        return self.classification(number) is CallerClass.BLOCKED

    def is_known_scammer(self, number: str) -> bool:
        """Known bad numbers skip probation — they have already shown their hand."""
        return self.classification(number) in (CallerClass.SCAMMER, CallerClass.ROBOCALL)



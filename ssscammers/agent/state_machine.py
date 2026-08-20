"""The call state machine.

There is one of these, deliberately. The original design had two — a comedy-shaped one for
tactics and a safety-shaped one for exits — and they disagreed about the first thirty
seconds of a call, which is precisely the window where a misrouted dentist gets baited.

The rule that resolves it: **safety exits are evaluated before ordinary progression, from
every state.** Whatever the persona is in the middle of, learning that a real person is on
the line outranks it.

The happy path::

    GREETING ──► ASSESSING ──► HOOK ──► STALL ⇄ WIND_DOWN
     (neutral)   (probation)   (commit) (the minutes)

and from *any* of those, at any moment::

    ──► DISCLOSE_EXIT    a real person is on the line
    ──► EMERGENCY_EXIT   someone is in actual danger
    ──► TERMINATE        caps, watchdog, threats, dead air, hangup

Until triage commits, the persona is only ever a neutral "…Hello?" — giving nothing away
to a real caller who is about to be handed to voicemail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ssscammers.shared.enums import (
    BAITABLE_TRIAGE,
    TERMINAL_PHASES,
    CallPhase,
    EndReason,
    EntryPath,
    TriageClass,
)

__all__ = ["CallStateMachine", "Trigger", "Transition", "CallContext"]


class Trigger(StrEnum):
    """Why a transition happened. Recorded on every call for later review."""

    CALL_ANSWERED = "call_answered"
    CALLER_SPOKE = "caller_spoke"
    TRIAGE_COMMITTED_SCAM = "triage_committed_scam"
    TRIAGE_FOUND_REAL_PERSON = "triage_found_real_person"
    PROBATION_EXPIRED = "probation_expired"
    HOOK_SET = "hook_set"
    SCAMMER_FRUSTRATED = "scammer_frustrated"
    SOFT_CAP_REACHED = "soft_cap_reached"
    HARD_CAP_REACHED = "hard_cap_reached"
    DEAD_AIR = "dead_air"
    SAFEWORD = "safeword"
    DTMF_ESCAPE = "dtmf_escape"
    ALLOWLISTED = "allowlisted"
    EMERGENCY_DETECTED = "emergency_detected"
    THREAT_DETECTED = "threat_detected"
    WATCHDOG_KILL = "watchdog_kill"
    SPEND_CAP = "spend_cap"
    CALLER_HUNG_UP = "caller_hung_up"


@dataclass(frozen=True)
class Transition:
    """One state change, with the reason attached."""

    frm: CallPhase
    to: CallPhase
    trigger: Trigger
    at_seconds: float

    @property
    def changed(self) -> bool:
        return self.frm is not self.to


@dataclass
class CallContext:
    """Everything the machine needs to decide. Updated by the pipeline each turn."""

    entry_path: EntryPath = EntryPath.UNKNOWN
    elapsed_seconds: float = 0.0
    caller_turns: int = 0
    triage: TriageClass = TriageClass.UNCLEAR
    triage_confidence: float = 0.0
    frustration: float = 0.0
    """0-10. Rising values mean they are close to hanging up on their own."""

    silence_seconds: float = 0.0
    dtmf_digits: str = ""
    heard_safeword: bool = False
    allowlisted: bool = False
    emergency_suspected: bool = False
    threat_detected: bool = False
    watchdog_killed: bool = False
    spend_exceeded: bool = False
    caller_hung_up: bool = False


@dataclass
class CallStateMachine:
    """Drives one call from answer to hangup.

    Args:
        probation_seconds: How long to stay neutral while triage decides.
        probation_hard_commit_seconds: A real caller states their business long before
            this. After it, an unclear caller is treated as a scammer.
        soft_cap_seconds: Past this the persona starts looking for a warm exit.
        hard_cap_seconds: G-14. Absolute ceiling, enforced here rather than trusted to
            the model.
        dead_air_seconds: G-16.
        commit_confidence: Triage confidence required to start baiting.
    """

    probation_seconds: float = 30.0
    probation_hard_commit_seconds: float = 90.0
    soft_cap_seconds: float = 3600.0
    hard_cap_seconds: float = 5400.0
    dead_air_seconds: float = 60.0
    commit_confidence: float = 0.6

    phase: CallPhase = CallPhase.GREETING
    history: list[Transition] = field(default_factory=list)
    end_reason: EndReason | None = None

    @property
    def baiting(self) -> bool:
        """True only where stalling tactics are allowed to run."""
        return self.phase in (CallPhase.HOOK, CallPhase.STALL, CallPhase.WIND_DOWN)

    def advance(self, ctx: CallContext) -> Transition:
        """Evaluate ``ctx`` and move to the phase it implies.

        Safety exits are checked first and unconditionally. Returns the transition
        even when nothing changed, so callers can log every evaluation.
        """
        target, trigger = self._evaluate(ctx)
        transition = Transition(self.phase, target, trigger, ctx.elapsed_seconds)

        if transition.changed:
            self.phase = target
            self.history.append(transition)
            self._record_end_reason(trigger, target)

        return transition

    def _evaluate(self, ctx: CallContext) -> tuple[CallPhase, Trigger]:
        # --- Safety exits. Checked from every state, before anything else. ---
        if ctx.caller_hung_up:
            return CallPhase.TERMINATE, Trigger.CALLER_HUNG_UP
        if ctx.emergency_suspected:
            return CallPhase.EMERGENCY_EXIT, Trigger.EMERGENCY_DETECTED
        if ctx.watchdog_killed:
            return CallPhase.TERMINATE, Trigger.WATCHDOG_KILL
        if ctx.threat_detected:
            return CallPhase.TERMINATE, Trigger.THREAT_DETECTED
        if ctx.elapsed_seconds >= self.hard_cap_seconds:
            return CallPhase.TERMINATE, Trigger.HARD_CAP_REACHED
        if ctx.spend_exceeded:
            return CallPhase.TERMINATE, Trigger.SPEND_CAP
        if ctx.silence_seconds >= self.dead_air_seconds:
            return CallPhase.TERMINATE, Trigger.DEAD_AIR

        # Ways a real person tells us they are a real person.
        if ctx.heard_safeword:
            return CallPhase.DISCLOSE_EXIT, Trigger.SAFEWORD
        if "5" in ctx.dtmf_digits:
            return CallPhase.DISCLOSE_EXIT, Trigger.DTMF_ESCAPE
        if ctx.allowlisted:
            return CallPhase.DISCLOSE_EXIT, Trigger.ALLOWLISTED
        if self._is_real_person(ctx):
            return CallPhase.DISCLOSE_EXIT, Trigger.TRIAGE_FOUND_REAL_PERSON

        # Once we have exited, nothing pulls us back into the persona.
        if self.phase in TERMINAL_PHASES:
            return self.phase, Trigger.CALLER_SPOKE

        # --- Ordinary progression ---
        if self.phase is CallPhase.GREETING:
            if ctx.caller_turns >= 1:
                return CallPhase.ASSESSING, Trigger.CALLER_SPOKE
            return CallPhase.GREETING, Trigger.CALL_ANSWERED

        if self.phase is CallPhase.ASSESSING:
            if self._should_commit(ctx):
                return CallPhase.HOOK, Trigger.TRIAGE_COMMITTED_SCAM
            if ctx.elapsed_seconds >= self.probation_hard_commit_seconds:
                # Nobody with real business takes this long to say what they want.
                return CallPhase.HOOK, Trigger.PROBATION_EXPIRED
            return CallPhase.ASSESSING, Trigger.CALLER_SPOKE

        if self.phase is CallPhase.HOOK:
            # A couple of turns of believable interest, then settle in.
            if ctx.caller_turns >= 4:
                return CallPhase.STALL, Trigger.HOOK_SET
            return CallPhase.HOOK, Trigger.CALLER_SPOKE

        if self.phase is CallPhase.STALL:
            if ctx.elapsed_seconds >= self.soft_cap_seconds:
                return CallPhase.WIND_DOWN, Trigger.SOFT_CAP_REACHED
            if ctx.frustration >= 8.0:
                return CallPhase.WIND_DOWN, Trigger.SCAMMER_FRUSTRATED
            return CallPhase.STALL, Trigger.CALLER_SPOKE

        if self.phase is CallPhase.WIND_DOWN:
            # Frustration falling back means they took the bait again; keep going.
            if ctx.frustration < 5.0 and ctx.elapsed_seconds < self.soft_cap_seconds:
                return CallPhase.STALL, Trigger.CALLER_SPOKE
            return CallPhase.WIND_DOWN, Trigger.CALLER_SPOKE

        return self.phase, Trigger.CALLER_SPOKE

    def _should_commit(self, ctx: CallContext) -> bool:
        """Whether triage is confident enough to start baiting.

        A forwarded call gets a higher bar: the honeypot number is seeded and strangers
        dialling it are presumed hostile, but the owner's own cell rolls over legitimate
        callers all day long.
        """
        if ctx.triage not in BAITABLE_TRIAGE:
            return False
        threshold = self.commit_confidence
        if ctx.entry_path is EntryPath.CONDITIONAL_FORWARD:
            threshold = min(0.95, threshold + 0.2)
        if ctx.elapsed_seconds < self.probation_seconds:
            # Early in the call, only an unambiguous read commits.
            threshold = min(0.95, threshold + 0.15)
        return ctx.triage_confidence >= threshold

    @staticmethod
    def _is_real_person(ctx: CallContext) -> bool:
        """A positive read on a real caller, at any confidence worth acting on.

        The threshold is deliberately lower than the one for committing to bait: wrongly
        baiting a person is worse than wrongly letting a scammer go, so it takes less
        evidence to stop than to start.
        """
        real = (
            TriageClass.LEGIT_BUSINESS,
            TriageClass.LEGIT_PERSONAL,
            TriageClass.VICTIM_CALLBACK,
        )
        return ctx.triage in real and ctx.triage_confidence >= 0.5

    def _record_end_reason(self, trigger: Trigger, target: CallPhase) -> None:
        if target not in TERMINAL_PHASES:
            return
        mapping = {
            Trigger.CALLER_HUNG_UP: EndReason.CALLER_HANGUP,
            Trigger.HARD_CAP_REACHED: EndReason.MAX_DURATION,
            Trigger.DEAD_AIR: EndReason.DEAD_AIR,
            Trigger.WATCHDOG_KILL: EndReason.WATCHDOG_KILL,
            Trigger.THREAT_DETECTED: EndReason.AGENT_HANGUP,
            Trigger.SPEND_CAP: EndReason.SPEND_CAP,
            Trigger.EMERGENCY_DETECTED: EndReason.EMERGENCY_EXIT,
        }
        self.end_reason = mapping.get(trigger, EndReason.DISCLOSED_EXIT)

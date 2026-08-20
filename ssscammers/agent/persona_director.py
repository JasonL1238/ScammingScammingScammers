"""The conductor: everything that decides what the persona does next.

One object owns the call so the pieces cannot disagree with each other. It holds the
state machine, the triage engine, the tactic weights, the pacing, and the output
filter, and it exposes a single method — :meth:`handle_caller_turn` — that the media
pipeline and the text harness both drive.

Two things here are easy to get wrong and are worth stating plainly.

**Latency is two different things.** Real latency — the model and the synthesiser taking
time — is *hidden* behind a filler noise the pipeline starts within about a tenth of a
second. Character latency is Marjorie being eighty-five, and it is *added* on top, but
only once the model has finished, so slowness never compounds into a line that sounds
dead. They are tracked separately because a dashboard that measures the filler is
measuring nothing.

**The fixed scripts are fixed.** Disclosure and emergency text is not generated. When the
system concludes it is talking to a real person, or to someone in danger, the words are
the same every time and the model does not get a vote.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from ssscammers.agent.persona import Persona
from ssscammers.agent.state_machine import CallContext, CallStateMachine, Transition
from ssscammers.agent.triage import AllowlistCache, TriageEngine
from ssscammers.shared.enums import CallPhase, EntryPath, ScamType, Tactic, TriageClass
from ssscammers.shared.output_filter import FilterResult, OutputFilter

logger = logging.getLogger(__name__)

__all__ = ["PersonaDirector", "TurnPlan", "DISCLOSURE_SCRIPT", "EMERGENCY_SCRIPT"]


#: Said verbatim to anyone who turns out not to be a scammer. Not generated, not
#: improvised, not softened — the legal posture rests on a real caller being told
#: plainly and immediately what they have reached.
DISCLOSURE_SCRIPT = (
    "I'm sorry — this is an automated assistant that screens calls for this number. "
    "I'm going to put you through to voicemail now, and your message will be seen."
)

#: Said verbatim when someone appears to be in real danger. This line exists because
#: the system is inbound-only and physically cannot call for help.
EMERGENCY_SCRIPT = (
    "This is an automated line and it cannot help you. "
    "Please hang up and dial 9 1 1 right now."
)

#: Said to someone a scammer sent here. Fixed text, because getting this one right
#: matters more than sounding natural.
VICTIM_WARNING_SCRIPT = (
    "I'm sorry — this is an automated assistant, and I think you may have been given "
    "this number by someone running a scam. Please don't send anyone money. "
    "Hang up, and call your bank on the number printed on the back of your card."
)

#: The neutral answer. Indistinguishable from a real person picking up, which is the
#: point: a misrouted caller should not hear a character until we know they deserve one.
NEUTRAL_GREETING = "Hello?"


@dataclass
class TurnPlan:
    """What the pipeline should do for one turn of the conversation."""

    phase: CallPhase
    speak: str | None = None
    """Fixed text to speak. When set, the model is not consulted at all."""

    consult_model: bool = False
    """Whether the pipeline should generate a reply for this turn."""

    state_note: str | None = None
    """Mid-call steering, delivered as the last message in the request."""

    tactic: Tactic = Tactic.NONE
    filler: str | None = None
    """Sound-pack clip to play immediately, covering real generation latency."""

    character_delay_ms: int = 0
    """Extra pause layered on for character, applied only after the model is done."""

    hold_seconds: int = 0
    """If non-zero, put the phone down for this long before continuing."""

    hang_up: bool = False
    offer_voicemail: bool = False
    """Whether the words just spoken promised this caller a voicemail.

    Only the disclosure script does. A victim is told to hang up and ring their bank, and
    following that with a recorded-message beep contradicts the instruction at the worst
    possible moment — so this is decided here, where the script is chosen, rather than
    inferred downstream from the phase (which cannot tell the two apart).
    """

    transition: Transition | None = None


@dataclass
class PersonaDirector:
    """Owns one call.

    Args:
        persona: The character being played.
        settings_probation_seconds: Neutral window before triage may commit.
        owner_pii: Real strings the filter must never let through.
        allowlist: Numbers that must never be baited.
        rng: Injected for deterministic tests.
    """

    persona: Persona
    caller_number: str = ""
    entry_path: EntryPath = EntryPath.UNKNOWN
    owner_pii: tuple[str, ...] = ()
    safeword: str = ""
    allowlist: AllowlistCache = field(default_factory=AllowlistCache)
    rng: random.Random = field(default_factory=random.Random)

    probation_seconds: float = 30.0
    probation_hard_commit_seconds: float = 90.0
    soft_cap_seconds: float = 3600.0
    hard_cap_seconds: float = 5400.0
    dead_air_seconds: float = 60.0

    state: CallStateMachine = field(init=False)
    triage: TriageEngine = field(init=False)
    filter: OutputFilter = field(init=False)

    _last_tactic: Tactic = field(init=False, default=Tactic.NONE)
    _claims: list[str] = field(init=False, default_factory=list)
    _caller_turns: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.state = CallStateMachine(
            probation_seconds=self.probation_seconds,
            probation_hard_commit_seconds=self.probation_hard_commit_seconds,
            soft_cap_seconds=self.soft_cap_seconds,
            hard_cap_seconds=self.hard_cap_seconds,
            dead_air_seconds=self.dead_air_seconds,
        )
        self.triage = TriageEngine(safeword=self.safeword)
        # The filter is told this persona's invented numbers so the character can read
        # its own card aloud; everything else numeric that could be valid is blocked.
        self.filter = OutputFilter.for_identity(
            self.persona.identity, owner_pii=self.owner_pii, rng=self.rng
        )

    # -- the one entry point --------------------------------------------------

    def opening(self) -> TurnPlan:
        """What to say when the call connects.

        Neutral, always. The recorded-line notice has already played from TwiML, and
        the character does not appear until triage has had a look at whoever this is.
        """
        return TurnPlan(phase=self.state.phase, speak=NEUTRAL_GREETING)

    def handle_caller_turn(
        self,
        utterance: str,
        *,
        elapsed_seconds: float,
        silence_seconds: float = 0.0,
        dtmf_digits: str = "",
        frustration: float = 0.0,
        spend_exceeded: bool = False,
        caller_hung_up: bool = False,
    ) -> TurnPlan:
        """Fold in what the caller just said and decide what happens next."""
        if utterance.strip():
            self._caller_turns += 1
            self.triage.observe(utterance)

        plan = self._plan(
            elapsed_seconds=elapsed_seconds,
            silence_seconds=silence_seconds,
            dtmf_digits=dtmf_digits,
            frustration=frustration,
            spend_exceeded=spend_exceeded,
            caller_hung_up=caller_hung_up,
            exits_only=False,
        )
        assert plan is not None  # exits_only=False always plans something
        return plan

    def check_exits(
        self,
        *,
        elapsed_seconds: float,
        silence_seconds: float = 0.0,
        dtmf_digits: str = "",
        frustration: float = 0.0,
        spend_exceeded: bool = False,
        caller_hung_up: bool = False,
    ) -> TurnPlan:
        """Evaluate only what a timer can trigger.

        Always returns a plan, but outside the exits it is an *empty* one — no speech, no
        model, no tactic — carrying only the transition it observed. The caller needs that
        transition even when there is nothing to say: a tick is what lands
        ``PROBATION_EXPIRED``, and a plan thrown away is a phase change missing from the
        canonical log.

        This must not run the *baiting* planner. Choosing a tactic mutates
        ``_last_tactic``, so a tick that picks a tactic nobody performs poisons the "don't
        repeat yourself" exclusion for the next real turn, and the persona mishears twice
        in a row and stops being believable.
        """
        return self._plan(
            elapsed_seconds=elapsed_seconds,
            silence_seconds=silence_seconds,
            dtmf_digits=dtmf_digits,
            frustration=frustration,
            spend_exceeded=spend_exceeded,
            caller_hung_up=caller_hung_up,
            exits_only=True,
        )

    def _plan(
        self,
        *,
        elapsed_seconds: float,
        silence_seconds: float,
        dtmf_digits: str,
        frustration: float,
        spend_exceeded: bool,
        caller_hung_up: bool,
        exits_only: bool,
    ) -> TurnPlan | None:
        verdict = self.triage.result()
        allowlisted = bool(self.caller_number) and self.allowlist.is_allowlisted(self.caller_number)

        transition = self.state.advance(
            CallContext(
                entry_path=self.entry_path,
                elapsed_seconds=elapsed_seconds,
                caller_turns=self._caller_turns,
                triage=verdict.triage,
                triage_confidence=verdict.confidence,
                frustration=frustration,
                silence_seconds=silence_seconds,
                dtmf_digits=dtmf_digits,
                heard_safeword=self.triage.heard_safeword,
                allowlisted=allowlisted,
                emergency_suspected=self.triage.emergency,
                threat_detected=self.triage.threat,
                spend_exceeded=spend_exceeded,
                caller_hung_up=caller_hung_up,
            )
        )

        phase = self.state.phase

        # --- Exits speak fixed text and end the call. No model involved. ---
        if phase is CallPhase.EMERGENCY_EXIT:
            return TurnPlan(phase=phase, speak=EMERGENCY_SCRIPT, hang_up=True, transition=transition)

        if phase is CallPhase.DISCLOSE_EXIT:
            warning_victim = verdict.triage is TriageClass.VICTIM_CALLBACK
            return TurnPlan(
                phase=phase,
                speak=VICTIM_WARNING_SCRIPT if warning_victim else DISCLOSURE_SCRIPT,
                hang_up=True,
                offer_voicemail=not warning_victim,
                transition=transition,
            )

        if phase is CallPhase.TERMINATE:
            return TurnPlan(phase=phase, speak=None, hang_up=True, transition=transition)

        # A timer has nothing to say outside the exits, and must not plan a turn: picking
        # a tactic here would mutate `_last_tactic` for a turn nobody performs, poisoning
        # the "don't repeat yourself" exclusion on the next real turn.
        if exits_only:
            return TurnPlan(phase=phase, transition=transition)

        # --- Still neutral: answer plainly, give nothing away. ---
        if phase in (CallPhase.GREETING, CallPhase.ASSESSING):
            return TurnPlan(
                phase=phase,
                consult_model=True,
                state_note=self._neutral_note(),
                filler=self._filler(),
                character_delay_ms=self.persona.pacing.sample_delay_ms(self.rng),
                transition=transition,
            )

        # --- Baiting. ---
        tactic = self.persona.choose_tactic(self.rng, exclude={self._last_tactic})
        self._last_tactic = tactic

        hold_seconds = 0
        if tactic is Tactic.HOLD_ON or self.rng.random() < self.persona.pacing.hold_probability:
            hold_seconds = self.persona.pacing.sample_hold_seconds(self.rng)

        return TurnPlan(
            phase=phase,
            consult_model=True,
            state_note=self._steering_note(tactic, verdict.scam_type, phase),
            tactic=tactic,
            filler=self._filler(),
            character_delay_ms=self.persona.pacing.sample_delay_ms(self.rng),
            hold_seconds=hold_seconds,
            transition=transition,
        )

    def vet_result(self, candidate: str) -> FilterResult:
        """Run model output through the filter, keeping the verdict, not just the text.

        The streaming path needs to know *that* something was blocked, not only what to
        say instead: a blocked sentence ends the turn rather than being followed by the
        next one, because the next one is where the rest of the card number was going.
        """
        result = self.filter.check(candidate, phase=self.state.phase)
        if result.blocked:
            logger.error(
                "persona output blocked on call from %s: %s",
                self.caller_number or "unknown",
                [v.value for v in result.violations],
            )
        return result

    def record_claim(self, claim: str) -> None:
        """Remember something the persona has asserted, so it does not contradict it.

        Inconsistency is how a scammer notices. The claims ride in the state note
        rather than the cached prompt, so remembering something new costs nothing.
        """
        cleaned = claim.strip()
        if cleaned and cleaned not in self._claims:
            self._claims.append(cleaned)
            del self._claims[:-12]  # recent claims only; the transcript holds the rest

    # -- state notes ----------------------------------------------------------

    def _neutral_note(self) -> str:
        return (
            "[call state] You have just picked up the phone and do not yet know who "
            "this is. Answer briefly and plainly, the way anyone would. Do not perform "
            "a character, do not stall, do not volunteer anything about yourself. Ask "
            "who is calling and what they want."
        )

    def _steering_note(self, tactic: Tactic, scam_type: ScamType, phase: CallPhase) -> str:
        lines = [f"[call state] Phase: {phase.value}."]

        if phase is CallPhase.HOOK:
            lines.append(
                "They are running a scam and you are now in character. React the way "
                "this script wants you to react — frightened, delighted, worried — so "
                "they commit to the pitch. Do not stall yet; let them get invested."
            )
        elif phase is CallPhase.WIND_DOWN:
            lines.append(
                "They are losing patience or the call has run long. Keep them hopeful "
                "and start looking for a warm way out. Do not refuse anything outright."
            )
        else:
            lines.append("Stall. Keep them believing they are close to succeeding.")

        if scam_type is not ScamType.UNKNOWN:
            lines.append(f"Script they appear to be running: {scam_type.value}.")

        lines.append(f"Lean on this move for the next turn: {_TACTIC_DIRECTIONS[tactic]}")

        if self._claims:
            lines.append(
                "Things you have already told them, which must stay consistent: "
                + "; ".join(self._claims)
            )

        lines.append(
            "Never say a card, bank, or identity number that would actually work — "
            "fumble instead."
        )
        return "\n".join(lines)

    def _filler(self) -> str | None:
        """A clip to start playing at once, so the caller never hears silence."""
        return self.rng.choice(self.persona.fillers) if self.persona.fillers else None


#: How each tactic is described to the model. Phrased as direction to an actor rather
#: than as a rule, because the personas perform better when told what to play than
#: when told what not to do.
_TACTIC_DIRECTIONS: dict[Tactic, str] = {
    Tactic.NONE: "just keep the conversation going.",
    Tactic.MISHEAR: "mishear one important word — an amount, a name, an agency — and react to the wrong one.",
    Tactic.READ_BACK: "repeat what they said back to them with one detail changed, and ask if that's right.",
    Tactic.FUMBLE_DATA: "try to give them what they asked for and fail at the last moment: lose your place, drop the pen, pick up the wrong card.",
    Tactic.TANGENT: "let their question remind you of a story, and tell the story instead.",
    Tactic.HOLD_ON: "say you need to put the phone down for a moment, and do.",
    Tactic.TECH_ILLITERACY: "be unable to find the button, the screen, or the window they are describing.",
    Tactic.EAGER_NONCONVERGENCE: "agree enthusiastically, then ask a question that puts them back at the beginning.",
    Tactic.REPEAT_REQUEST: "ask them to say the whole thing again, from the top.",
}

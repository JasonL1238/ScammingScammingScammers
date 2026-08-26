"""Talk to a persona from the terminal, with no phone line involved.

This is the inner development loop. Speech recognition and synthesis are skipped — you
type what the scammer says and read what the persona says back — so a prompt change can be
judged in seconds rather than by waiting for someone to call.

    python -m ssscammers.simscammer.textloop --persona marjorie
    python -m ssscammers.simscammer.textloop --script bank_otp_harvest
    python -m ssscammers.simscammer.textloop --script pharmacy_prescription --dry

``--dry`` runs the state machine, triage, tactic selection, and filter without calling the
model at all — no API key, no network — which makes it the fast way to check that a
misrouted caller gets released or that a script is classified as expected.

Every run prints the seed of its per-call rng; ``--seed N`` passes one back in, which
reproduces a dry run draw-for-draw (tactics, fillers, holds, clips). With
``--all-scripts`` the one seed is reused for every script, so a whole gate run is
reproducible from a single number.

What runs here is the same :class:`~ssscammers.agent.conversation.Conversation` the phone
line runs; only the clock and the speakers are fake. That is the value of the harness: a
judgement made here is about production behaviour, not about a parallel implementation of
it that has since drifted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass

from ssscammers.agent.conversation import (
    Action,
    Conversation,
    HangUp,
    Pause,
    PlayClip,
    Say,
    build_conversation,
)
from ssscammers.agent.llm import ClaudeBrain, model_overrides
from ssscammers.agent.persona import available_personas, load_persona
from ssscammers.shared.config import load_settings
from ssscammers.shared.enums import EntryPath
from ssscammers.simscammer.clock import SimulatedClock
from ssscammers.simscammer.scripts import ALL_SCRIPTS, CallerScript

# Terminal colours, disabled when piped.
_TTY = sys.stdout.isatty()


def _style(code: str) -> Callable[[str], str]:
    def paint(text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if _TTY else text

    return paint


DIM, BOLD = _style("2"), _style("1")
RED, GREEN, YELLOW, CYAN = _style("31"), _style("32"), _style("33"), _style("36")


@dataclass
class Session:
    """One simulated call, driven through the production conversation driver.

    Real time is the wrong scale here: a script that would take forty minutes on
    the phone has to run in a second, and a ninety-second hold has to *count*
    toward the caps without anyone waiting for it — hence the injected
    :class:`~ssscammers.simscammer.clock.SimulatedClock` and a fixed
    seconds-per-turn advance.
    """

    conversation: Conversation
    clock: SimulatedClock
    seconds_per_turn: float = 25.0

    @property
    def elapsed(self) -> float:
        # The production measurement, not a parallel one that could drift from
        # the elapsed the state machine actually uses for cap decisions.
        return self.conversation.elapsed_seconds

    async def say(self, utterance: str) -> tuple[list[Action], str | None]:
        """Feed one caller line in; return the actions and what was actually spoken."""
        self.clock.advance(self.seconds_per_turn)
        actions = [action async for action in self.conversation.respond(utterance)]

        # Pauses and holds are time the caller really would have spent on the line.
        for action in actions:
            if isinstance(action, Pause):
                self.clock.advance(action.seconds)
        self.conversation.note_agent_audio_finished()

        spoken = " ".join(a.text for a in actions if isinstance(a, Say)).strip()
        return actions, spoken or None


def _describe(session: Session, actions: list[Action]) -> str:
    """One line of why, to sit under the line of what."""
    plan = session.conversation.last_plan
    bits = [f"phase={session.conversation.final_phase.value}"]
    if plan is not None:
        if plan.triage is not None:
            # Phase lags the verdict by the confidence bar and probation, so a
            # developer tuning triage sees the flip here, turns before it acts.
            bits.append(f"triage={plan.triage.triage.value}@{plan.triage.confidence:.2f}")
        if plan.tactic.value != "none":
            bits.append(f"tactic={plan.tactic.value}")
        if plan.character_delay_ms:
            bits.append(f"pause={plan.character_delay_ms}ms")

    for action in actions:
        if isinstance(action, PlayClip):
            bits.append(f"{action.kind}={action.clip}")
        elif isinstance(action, Pause):
            bits.append(f"wait={action.seconds:.1f}s")
        elif isinstance(action, HangUp):
            bits.append(f"HANGING UP ({action.reason.value if action.reason else 'no reason'})")
    return "  ".join(bits)


def _build_session(
    persona_id: str, *, dry: bool, forwarded: bool, seed: int | None = None
) -> Session:
    settings = load_settings()

    brain: ClaudeBrain | None = None
    if not dry:
        try:
            brain = ClaudeBrain(
                system_prompt=load_persona(persona_id).system_prompt(),
                api_key=settings.anthropic_api_key or None,
                **model_overrides(settings),
            )
        except RuntimeError as exc:
            print(RED(f"! {exc}"))
            print(DIM("  running in --dry mode instead\n"))

    clock = SimulatedClock()
    conversation = build_conversation(
        settings,
        caller_number="+19375559999",
        entry_path=EntryPath.CONDITIONAL_FORWARD if forwarded else EntryPath.DIRECT,
        persona_id=persona_id,
        brain=brain,
        clock=clock,
        seed=seed,
    )
    return Session(conversation=conversation, clock=clock)


async def run_script(session: Session, script: CallerScript) -> int:
    """Play a canned caller and report whether the agent behaved."""
    print(BOLD(f"\n=== {script.name} ") + DIM(f"[{', '.join(script.tags)}]"))
    if script.notes:
        print(DIM(f"    {script.notes}"))
    # Every run is reproducible: re-run with --seed to get this exact call again.
    print(DIM(f"    seed={session.conversation.seed}"))
    print()

    persona = session.conversation.director.persona
    for action in await session.conversation.open():
        if isinstance(action, Say):
            print(f"  {GREEN(persona.display_name)}: {action.text}")

    for line in script.lines:
        print(f"  {CYAN('caller')}: {line}")
        actions, spoken = await session.say(line)
        print(DIM(f"          {_describe(session, actions)}"))
        if spoken:
            print(f"  {GREEN(persona.display_name)}: {spoken}")
        if session.conversation.ended:
            break

    return _report(session, script)


def _report(session: Session, script: CallerScript) -> int:
    """Check the outcome against the script's expectations. Returns a failure count."""
    director = session.conversation.director
    phase = director.state.phase
    verdict = director.triage.result()
    failures = 0

    print()
    if script.expect_triage is not None:
        ok = verdict.triage is script.expect_triage
        failures += not ok
        mark = GREEN("PASS") if ok else RED("FAIL")
        print(f"  {mark} triage: got {verdict.triage.value}, expected {script.expect_triage.value}")

    if script.expect_phase is not None:
        ok = phase is script.expect_phase
        failures += not ok
        mark = GREEN("PASS") if ok else RED("FAIL")
        print(f"  {mark} phase:  got {phase.value}, expected {script.expect_phase.value}")

    for forbidden in script.must_not_reach:
        reached = any(t.to is forbidden for t in director.state.history)
        failures += reached
        mark = RED("FAIL") if reached else GREEN("PASS")
        print(f"  {mark} never reached {forbidden.value}")

    if not script.expect_triage and not script.expect_phase and not script.must_not_reach:
        print(DIM(f"  (no assertions; ended in {phase.value}, triage {verdict.triage.value})"))

    return failures


async def run_interactive(session: Session) -> None:
    """Type as the scammer; read what comes back."""
    persona = session.conversation.director.persona
    print(BOLD(f"\n  {persona.display_name} — {persona.description.strip()}"))
    print(DIM(f"  seed={session.conversation.seed}"))
    print(DIM("  Type as the caller. Ctrl-D or 'quit' to hang up.\n"))

    for action in await session.conversation.open():
        if isinstance(action, Say):
            print(f"  {GREEN(persona.display_name)}: {action.text}\n")

    while True:
        try:
            line = input(f"  {CYAN('caller')}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in {"quit", "exit"}:
            break
        if not line:
            continue

        actions, spoken = await session.say(line)
        print(DIM(f"          {_describe(session, actions)}"))
        if spoken:
            print(f"  {GREEN(persona.display_name)}: {spoken}")
        elif session.conversation.last_plan and session.conversation.last_plan.consult_model:
            print(DIM("          (dry run — no model call)"))
        print()

        if session.conversation.ended:
            print(YELLOW(f"  call ended: {session.conversation.end_reason}"))
            break

    minutes = session.elapsed / 60
    turns = len(session.conversation.history)
    print(DIM(f"\n  simulated duration: {minutes:.1f} min in {turns} turns"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--persona", default="marjorie", choices=available_personas())
    parser.add_argument("--script", help="run a canned caller instead of typing")
    parser.add_argument("--all-scripts", action="store_true", help="run every canned caller")
    parser.add_argument(
        "--dry",
        action="store_true",
        help="skip the model entirely: state machine, triage and filter only",
    )
    parser.add_argument(
        "--forwarded",
        action="store_true",
        help="simulate a call rolled over from the owner's cell (stricter triage)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="seed the per-call rng for a reproducible run; every run prints the "
        "seed it used, so any call can be replayed by passing it back",
    )
    args = parser.parse_args(argv)

    scripts_by_name = {s.name: s for s in ALL_SCRIPTS}

    if args.all_scripts:
        failures = 0
        for script in ALL_SCRIPTS:
            session = _build_session(
                args.persona, dry=args.dry, forwarded=args.forwarded, seed=args.seed
            )
            failures += asyncio.run(run_script(session, script))
        print()
        if failures:
            print(RED(f"  {failures} expectation(s) failed"))
        else:
            print(GREEN("  all scripts behaved as expected"))
        return 1 if failures else 0

    if args.script:
        script = scripts_by_name.get(args.script)
        if script is None:
            print(RED(f"unknown script {args.script!r}"))
            print(DIM("available: " + ", ".join(sorted(scripts_by_name))))
            return 2
        session = _build_session(
            args.persona, dry=args.dry, forwarded=args.forwarded, seed=args.seed
        )
        return 1 if asyncio.run(run_script(session, script)) else 0

    session = _build_session(
        args.persona, dry=args.dry, forwarded=args.forwarded, seed=args.seed
    )
    asyncio.run(run_interactive(session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

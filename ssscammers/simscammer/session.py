"""One simulated call, driven the way production drives it.

There used to be two of these — the text harness had its own loop and the
golden runner grew a second — and they disagreed about time. That is the worst
possible thing for two drivers of the same object to disagree about: the
harness a developer judges a persona with, and the gate that pins the event
stream, would report different calls for the same script.

Two details make this loop production-faithful rather than merely plausible,
and both were wrong in one of the old copies:

**The clock advances *inside* the action drain, not after it.** The transport
consumes actions one at a time and sleeps on each ``Pause`` as it arrives
(:func:`ssscammers.agent.media.perform_stream`); collecting the whole turn
first and then adding up the pauses puts every later event at the wrong moment
and undoes the streaming the conversation layer works hardest for.

**The timer keeps running during a pause.** On a live call the one-second
ticker is a separate task, so it fires *through* a ninety-second hold. A driver
that only ticks between caller turns can never observe the interaction the
guardrail exists for — that a hold is our audio and must not age into dead air
(G-16) — because the hold is always already over by the time it looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ssscammers.agent.conversation import Action, Conversation, Pause
from ssscammers.simscammer.clock import SimulatedClock

__all__ = ["Session"]


@dataclass
class Session:
    """One simulated call, driven through the production conversation driver.

    Real time is the wrong scale here: a script that would take forty minutes
    on the phone has to run in a second, and a ninety-second hold has to
    *count* toward the caps without anyone waiting for it.
    """

    conversation: Conversation
    clock: SimulatedClock
    seconds_per_turn: float = 25.0

    tick_seconds: float = 1.0
    """The timer's cadence, matching ``media._TICK_SECONDS``."""

    ticking: bool = True
    """Whether the timer runs. On for anything claiming to model a real call;
    off only for a driver deliberately isolating the caller-turn path."""

    _timer_actions: list[Action] = field(init=False, default_factory=list)

    @property
    def elapsed(self) -> float:
        # The production measurement, not a parallel one that could drift from
        # the elapsed the state machine actually uses for cap decisions.
        return self.conversation.elapsed_seconds

    @property
    def timer_actions(self) -> list[Action]:
        """Whatever the timer did on its own — a hangup, usually, or nothing."""
        return self._timer_actions

    async def say(self, utterance: str) -> tuple[list[Action], str | None]:
        """Feed one caller line in; return the actions and what was spoken."""
        await self.idle(self.seconds_per_turn)

        actions: list[Action] = []
        async for action in self.conversation.respond(utterance):
            actions.append(action)
            if isinstance(action, Pause):
                # Time the caller really spends on the line, with the timer
                # running through it exactly as it does on a live call.
                await self.idle(action.seconds)
        self.conversation.note_agent_audio_finished()

        spoken = " ".join(a.text for a in actions if hasattr(a, "text")).strip()
        return actions, spoken or None

    async def idle(self, seconds: float) -> list[Action]:
        """Let ``seconds`` pass with the timer running. How a call ages.

        The clock moves one cadence step at a time rather than in one jump, so
        each evaluation sees the state it would have seen live — a hold that is
        still in progress looks different from one that finished a minute ago,
        and that difference is the whole of G-16.
        """
        if not self.ticking:
            self.clock.advance(seconds)
            return []

        produced: list[Action] = []
        remaining = seconds
        while remaining > 0:
            step = min(self.tick_seconds, remaining)
            self.clock.advance(step)
            remaining -= step
            if self.conversation.ended:
                # Nothing ticks after a hangup, but the clock still runs: the
                # rest of the interval is real time the caller spent.
                self.clock.advance(remaining)
                break
            produced.extend([action async for action in self.conversation.tick()])
        self._timer_actions.extend(produced)
        return produced

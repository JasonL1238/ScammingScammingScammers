"""The one simulated clock — for the harness and the test suite alike.

There used to be two: ``tests/helpers.FakeClock`` and the textloop's own
``SimulatedClock``, drifting toward the day a test and the harness disagreed about
what time does. Phase 2's replay work runs the same call under both, so they must
be the same object. Injected wherever production reads ``time.monotonic``, which
is what lets a ninety-minute call, a sixty-second dead-air window, and a
ninety-second hold all be exercised in the same millisecond.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SimulatedClock"]


@dataclass
class SimulatedClock:
    """A monotonic clock wound forward by hand.

    ``now`` starts non-zero by default so code that treats a timestamp as falsy
    or as "seconds since the call started" shows itself in tests; consumers that
    want human-readable absolute time measure deltas from their own start.
    """

    now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

"""The one simulated clock's documented contract.

Every other test exercises the clock transitively through delta arithmetic —
which works at *any* start value, and is exactly why none of them guards the
one deliberate property the class documents.
"""

from __future__ import annotations

from ssscammers.simscammer.clock import SimulatedClock


def test_the_default_start_is_truthy_on_purpose() -> None:
    # The docstring's promise: code that treats a timestamp as falsy or as
    # "seconds since the call started" must show itself in tests. A future
    # "simplification" to 0.0 would delete that tripwire while the whole
    # suite stayed green — this is the pin that goes red instead.
    assert SimulatedClock().now


def test_it_reads_and_advances() -> None:
    clock = SimulatedClock(now=5.0)
    assert clock() == 5.0
    clock.advance(2.5)
    assert clock() == 7.5

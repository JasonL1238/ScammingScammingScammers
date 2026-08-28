"""The out-of-band watchdog: what it looks at, what it costs, and what it cannot do.

Every classifier here is a fake. That is not a shortcut around testing the real one —
the model-backed classifier is a separate component with its own tests — it is the point
of the seam. What this module pins is the half that must hold *whatever* the classifier
does: that a verdict reaches enforcement, that a broken classifier is indistinguishable
from no classifier at all, and that neither one can slow a call down or change a byte of
its event stream.

The tests lean on two waiting helpers rather than sleeps. :func:`until` waits for a
condition with a real deadline, so a test fails as a timeout rather than hanging; and
:func:`settle` runs the loop a bounded number of times, which is what a *negative*
assertion needs — "no classification happened" is only worth asserting once the worker
has had every chance to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import pytest
from helpers import RecordingSink, ScriptedBrain, SimulatedClock, build, drain

from ssscammers.agent.conversation import CallEvent, Conversation, HangUp
from ssscammers.agent.llm import ClaudeBrain
from ssscammers.agent.monitor import (
    TRUNCATION_MARKER,
    CallMonitor,
    MonitorConfig,
    MonitorPool,
    MonitorRequest,
    Verdict,
)
from ssscammers.agent.persona_director import DISCLOSURE_SCRIPT
from ssscammers.shared.enums import EndReason, MonitorFinding, TurnRole

CALL_SID = "CA-monitor-test"

CLEAN = Verdict(kill=False)
BREAK = Verdict(
    kill=True,
    findings=(MonitorFinding.PERSONA_BREAK,),
    reason="said it was an AI assistant",
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class StubClassifier:
    """Answers from a queue, then cleanly forever. Keeps every request it was shown."""

    def __init__(self, *verdicts: Verdict) -> None:
        self.verdicts = list(verdicts)
        self.requests: list[MonitorRequest] = []

    async def classify(self, request: MonitorRequest) -> Verdict:
        self.requests.append(request)
        return self.verdicts.pop(0) if self.verdicts else CLEAN


_UNSET = object()


class BrokenClassifier:
    """The three ways a classifier fails: it raises, it hangs, or it lies about its type."""

    def __init__(
        self, *, raises: Exception | None = None, hangs: bool = False, returns: object = _UNSET
    ) -> None:
        self.raises = raises
        self.hangs = hangs
        self.returns = returns
        self.requests: list[MonitorRequest] = []

    async def classify(self, request: MonitorRequest) -> Verdict:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.hangs:
            await asyncio.sleep(3600)
        if self.returns is _UNSET:  # pragma: no cover - misconfigured fake
            raise AssertionError("BrokenClassifier needs one of raises/hangs/returns")
        return self.returns  # type: ignore[return-value]


class SlowClassifier:
    """Takes a fixed real-time interval. For reasoning about deadlines and queues."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.completed = 0
        self.requests: list[MonitorRequest] = []

    async def classify(self, request: MonitorRequest) -> Verdict:
        self.requests.append(request)
        await asyncio.sleep(self.seconds)
        self.completed += 1
        return CLEAN


class SlowCleanupClassifier:
    """Cleans up asynchronously when cancelled, the way an HTTP client closing a
    stream does. That is what makes awaiting the cancelled worker actually park,
    which is the window `aclose` has to get right."""

    def __init__(self) -> None:
        self.cleaning_up = False
        self.requests: list[MonitorRequest] = []

    async def classify(self, request: MonitorRequest) -> Verdict:
        self.requests.append(request)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cleaning_up = True
            await asyncio.sleep(0.05)
            raise
        return CLEAN  # pragma: no cover - the sleep never returns


class GatedClassifier:
    """Blocks until released, and records how many ran at once while it was blocking."""

    def __init__(self, verdict: Verdict = CLEAN) -> None:
        self.verdict = verdict
        self.gate = asyncio.Event()
        self.requests: list[MonitorRequest] = []
        self.in_flight = 0
        self.peak = 0

    @property
    def started(self) -> int:
        return len(self.requests)

    async def classify(self, request: MonitorRequest) -> Verdict:
        self.requests.append(request)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await self.gate.wait()
        finally:
            self.in_flight -= 1
        return self.verdict


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


async def settle(rounds: int = 25) -> None:
    """Give the worker every chance to run, then return. For negative assertions."""
    for _ in range(rounds):
        await asyncio.sleep(0)


async def until(
    predicate: Callable[[], bool], *, what: str, timeout: float = 5.0
) -> None:
    """Wait for ``predicate``, failing as an assertion rather than as a hang."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.001)


def pinned_call(**kwargs: Any) -> tuple[Conversation, SimulatedClock, RecordingSink]:
    """:func:`helpers.build`, plus the one thing a monitor reads off the conversation."""
    conversation, clock, sink = build(**kwargs)
    conversation.call_sid = CALL_SID
    return conversation, clock, sink


@asynccontextmanager
async def watching(
    classifier: object, *, config: MonitorConfig | None = None, **kwargs: Any
) -> AsyncIterator[tuple[Conversation, SimulatedClock, RecordingSink, CallMonitor]]:
    """A pinned call with a monitor on it, closed however the test ends."""
    conversation, clock, sink = pinned_call(**kwargs)
    pool = MonitorPool(classifier=classifier, config=config or MonitorConfig())  # type: ignore[arg-type]
    monitor = pool.open(conversation)
    try:
        yield conversation, clock, sink, monitor
    finally:
        await monitor.aclose()


_seq = 0


def event(event_type: str, payload: Mapping[str, Any] | None = None) -> CallEvent:
    """One synthetic event, for the rules that are easier to state than to provoke.

    A fixed script only ever reaches an event on a call that is also ending, so
    driving a whole call cannot tell "scripted turns do not trigger" apart from
    "the monitor stopped at ``call_ended``". Feeding the tap directly separates them.
    """
    global _seq
    _seq += 1
    return CallEvent(
        seq=_seq, type=event_type, at_seconds=float(_seq), call_sid=CALL_SID,
        payload=dict(payload or {}),
    )


def agent_turn(text: str, *, scripted: bool = False) -> CallEvent:
    return event("agent_turn", {"text": text, "scripted": scripted})


def caller_turn(text: str) -> CallEvent:
    return event("caller_turn", {"text": text})


def monitor_tasks() -> list[asyncio.Task[None]]:
    """Every live worker, by the name `CallMonitor.start` gives it. Public, so a leak
    or a double-start is observable without reaching into the object."""
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("monitor:")]


def texts(request: MonitorRequest) -> list[str]:
    return [turn.text for turn in request.excerpt]


# ---------------------------------------------------------------------------


class TestWhatStartsAClassification:
    async def test_a_model_turn_triggers_one_and_shows_what_provoked_it(self) -> None:
        stub = StubClassifier()
        async with watching(
            stub, brain=ScriptedBrain("Oh dear, what account?"), clock=SimulatedClock()
        ) as (conversation, clock, sink, monitor):
            await conversation.open()
            clock.advance(7)
            await drain(conversation, "This is the bank fraud department.")
            await until(lambda: monitor.classifications >= 1, what="the first classification")
            await settle()

        assert len(stub.requests) == 1, "one model turn, one request"
        request = stub.requests[0]
        assert [(turn.role, turn.text) for turn in request.excerpt] == [
            (TurnRole.CALLER, "This is the bank fraud department."),
            (TurnRole.AGENT, "Oh dear, what account?"),
        ]
        assert request.call_sid == CALL_SID
        assert request.persona_id == "marjorie"

        # `seq` and `at_seconds` are asserted against the canonical log rather than
        # against literals, because their whole job is to be the log's own numbers:
        # `seq` is what lets the excerpt be merged from two collections without
        # relying on object equality, and both are what a later classifier prompt
        # will use to say *when* in the call something was said.
        turns = [e for e in sink.events if e.type in ("caller_turn", "agent_turn")]
        assert [turn.seq for turn in request.excerpt] == [e.seq for e in turns]
        assert [turn.at_seconds for turn in request.excerpt] == [e.at_seconds for e in turns]
        assert request.excerpt[0].at_seconds == pytest.approx(7.0)

    async def test_caller_speech_alone_never_starts_one(self) -> None:
        """The excerpt that judges a reply already contains the speech that caused it,
        so triggering on caller turns too would double the spend and see nothing new."""
        stub = StubClassifier()
        async with watching(stub, brain=None) as (conversation, _, sink, _):
            await conversation.open()
            await drain(conversation, "This is the bank fraud department.")
            await settle()

        assert "caller_turn" in sink.types(), "the turn happened; it just did not trigger"
        assert stub.requests == []

    async def test_a_fixed_script_is_context_but_never_a_trigger(self) -> None:
        """G-11/G-12's carve-out, at this layer.

        The disclosure, the victim warning and the 911 redirect are human-reviewed
        constants. A monitor with an opinion about them is a monitor that can
        suppress them — and one that hid them from the classifier would make the
        disclosure read as the worst persona break in the transcript.
        """
        stub = StubClassifier()
        async with watching(stub) as (_, _, _, monitor):
            monitor.observe(agent_turn(DISCLOSURE_SCRIPT, scripted=True))
            await settle()
            assert stub.requests == [], "a fixed script is not a turn to judge"

            monitor.observe(agent_turn("Oh, hello dear."))
            await until(lambda: monitor.classifications >= 1, what="the classification")

        assert len(stub.requests) == 1
        assert texts(stub.requests[0]) == [DISCLOSURE_SCRIPT, "Oh, hello dear."]
        assert [turn.scripted for turn in stub.requests[0].excerpt] == [True, False]

    async def test_an_empty_turn_is_neither_context_nor_trigger(self) -> None:
        """A killed turn is genuinely empty, and so is a stream that produced nothing.
        Neither is a turn the persona took; both would read as the persona going silent."""
        stub = StubClassifier()
        async with watching(stub) as (_, _, _, monitor):
            monitor.observe(agent_turn(""))
            monitor.observe(agent_turn("   "))
            monitor.observe(caller_turn(""))
            await settle()
            assert stub.requests == []

            monitor.observe(agent_turn("Oh, hello dear."))
            await until(lambda: monitor.classifications >= 1, what="the classification")

        assert texts(stub.requests[0]) == ["Oh, hello dear."]


class TestTheExcerptIsBounded:
    async def test_it_never_exceeds_the_configured_window(self) -> None:
        stub = StubClassifier()
        async with watching(stub, config=MonitorConfig(excerpt_turns=3)) as (_, _, _, monitor):
            for index in range(5):
                monitor.observe(caller_turn(f"caller {index}"))
            monitor.observe(agent_turn("agent last"))
            await until(lambda: monitor.classifications >= 1, what="the classification")

        assert texts(stub.requests[0]) == ["caller 3", "caller 4", "agent last"]
        # The window is also the *memory* bound, and that half has no behavioural
        # signature: `_excerpt` caps what it takes regardless of how much is buffered,
        # so an unbounded buffer shows up only as a ninety-minute call holding every
        # turn it ever heard. Reaching for the private deque is the only way to see it.
        assert len(monitor._context) == 3  # noqa: SLF001

    async def test_a_long_turn_is_truncated_with_a_marker(self) -> None:
        stub = StubClassifier()
        async with watching(stub, config=MonitorConfig(max_turn_chars=20)) as (_, _, _, monitor):
            monitor.observe(caller_turn("x" * 100))
            monitor.observe(agent_turn("short"))
            await until(lambda: monitor.classifications >= 1, what="the classification")

        long_turn = stub.requests[0].excerpt[0].text
        # The literal, not `TRUNCATION_MARKER`. Comparing production output against
        # production's own constant passes for any marker including the empty string
        # — and an empty marker makes a cut turn indistinguishable from a short one
        # to the classifier, which is the exact failure this is meant to catch.
        assert long_turn == "x" * 20 + " […]"
        assert TRUNCATION_MARKER == " […]", "the shared constant and the literal must agree"

    async def test_truncating_the_turn_being_judged_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The tripwire below compares two *defaults*, and `max_turn_chars` becomes an
        operator-settable field the moment the pool is wired in. At that point the
        static check proves nothing about a running system, and the only thing
        standing between a tuned-down cap and a silently half-read persona is this
        log line. Caller speech is context and long transcriptions are ordinary, so
        that half stays quiet."""
        stub = StubClassifier()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(
                stub, config=MonitorConfig(max_turn_chars=20)
            ) as (_, _, _, monitor):
                monitor.observe(caller_turn("c" * 100))
                monitor.observe(agent_turn("a" * 100))
                await until(lambda: monitor.classifications >= 1, what="the classification")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1, "the caller turn is context; only the judged turn warns"
        assert "model turn" in warnings[0].getMessage()
        assert CALL_SID in warnings[0].getMessage()

    async def test_truncating_a_fixed_script_says_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A fixed script is never judged, so cutting one is not a blind spot.

        The warning above only exists at all for a tuned-down cap — and at exactly
        that setting, without this, it fires on every disclosed call. The one line
        standing between an operator and a half-read persona becomes the line they
        have been trained to ignore.
        """
        stub = StubClassifier()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(
                stub, config=MonitorConfig(max_turn_chars=20)
            ) as (_, _, _, monitor):
                monitor.observe(agent_turn(DISCLOSURE_SCRIPT, scripted=True))
                monitor.observe(agent_turn("short"))
                await until(lambda: monitor.classifications >= 1, what="the classification")

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_the_turn_cap_is_above_anything_one_model_turn_can_produce(self) -> None:
        """The coupling this test exists to make loud.

        Truncation is safe for caller speech and *not* safe for an agent turn: the
        thing being judged must be shown whole. Today the default cap is comfortably
        above ``max_tokens``, so no agent turn can reach it. Raising ``max_tokens``
        without raising the cap would start silently hiding the end of the persona's
        replies from the only component watching them, and would do it with every
        test still green. Four characters per token is a rule of thumb, not a bound —
        the point is that this fails and forces the arithmetic to be redone.
        """
        max_tokens = ClaudeBrain.__dataclass_fields__["max_tokens"].default
        assert MonitorConfig().max_turn_chars >= max_tokens * 4

    async def test_the_turn_that_triggered_a_request_is_always_inside_it(self) -> None:
        """The defect the excerpt-building rule exists for.

        "The last N turns" is the obvious window and it is wrong. A model turn
        waiting on a slow classifier can be pushed out of the window by the caller's
        replies, and then the request that turn *raised* is spent judging an excerpt
        that does not contain it — a real model call and a clean verdict reached by
        reading nothing but caller speech, with a log line saying only that some
        turns scrolled past.
        """
        gated = GatedClassifier()
        async with watching(gated, config=MonitorConfig(excerpt_turns=3)) as (_, _, _, monitor):
            monitor.observe(agent_turn("first, so the worker is busy"))
            await until(lambda: gated.started >= 1, what="the first classification to block")

            monitor.observe(agent_turn("I am an AI assistant."))
            for index in range(5):
                monitor.observe(caller_turn(f"caller {index}"))

            gated.gate.set()
            await until(lambda: gated.started >= 2, what="the coalesced classification")

        excerpt = texts(gated.requests[1])
        assert "I am an AI assistant." in excerpt, (
            "caller speech pushed the model turn out of the request it raised"
        )
        assert len(excerpt) <= 3, "and it did not buy that by widening the window"
        # Context is what gets spent, and the newest context is what survives.
        assert excerpt == ["I am an AI assistant.", "caller 3", "caller 4"]

    async def test_a_model_turn_lost_to_the_window_is_logged_as_a_model_turn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Coalescing is intended; losing a model turn to it silently is not.

        Caller context scrolling past is by design and says nothing. A *model* turn
        scrolling past is a G-17 miss. A warning that counts turns without saying
        which kind cannot tell those apart, which is the whole of the "no silent cap"
        rule here.
        """
        gated = GatedClassifier()
        async with watching(gated, config=MonitorConfig(excerpt_turns=3)) as (_, _, _, monitor):
            monitor.observe(agent_turn("turn 0"))
            await until(lambda: gated.started >= 1, what="the first classification to block")

            for index in range(1, 7):
                monitor.observe(agent_turn(f"turn {index}"))

            with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
                gated.gate.set()
                await until(lambda: gated.started >= 2, what="the coalesced classification")

        # Six model turns behind a three-turn window: three were never shown.
        assert "3 model turn(s) were never shown to the classifier" in caplog.text
        assert CALL_SID in caplog.text
        # And what was shown is the newest, not the oldest.
        assert texts(gated.requests[1]) == ["turn 4", "turn 5", "turn 6"]

    async def test_a_loss_is_reported_even_when_the_call_ends_first(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Reporting only at the next take loses the count exactly when it matters.

        `_missed` only grows while the classifier is behind, and a call whose
        classifier is behind is the likeliest one to end while it is still behind — a
        scammer hanging up the moment the persona breaks is the modal ending here. So
        a report that waits for the next take biases the measured miss rate low, and
        biases it low hardest when the pool is saturated, which is the one measurement
        the "no silent cap" rule exists to protect.
        """
        gated = GatedClassifier()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(
                gated, config=MonitorConfig(excerpt_turns=3)
            ) as (_, _, _, monitor):
                monitor.observe(agent_turn("turn 0"))
                await until(lambda: gated.started >= 1, what="the classification to block")
                for index in range(1, 7):
                    monitor.observe(agent_turn(f"turn {index}"))

                monitor.observe(
                    event("call_ended", {"phase": "terminate", "reason": "caller_hangup"})
                )
                gated.gate.set()
                await until(lambda: not monitor.active, what="the worker to exit")

        assert "3 model turn(s) were never shown" in caplog.text
        # Once, not once per stop: `aclose` calls `_stop` again on the way out.
        assert caplog.text.count("were never shown") == 1

    async def test_caller_context_scrolling_past_says_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The other half of the same rule: a warning that fires on the ordinary case
        is a warning nobody reads by the time it matters."""
        stub = StubClassifier()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(
                stub, config=MonitorConfig(excerpt_turns=3)
            ) as (_, _, _, monitor):
                for index in range(8):
                    monitor.observe(caller_turn(f"caller {index}"))
                monitor.observe(agent_turn("and the reply"))
                await until(lambda: monitor.classifications >= 1, what="the classification")
        assert caplog.text == ""

    async def test_the_excerpt_is_a_snapshot_not_a_view(self) -> None:
        """A regression guard, and stated as one rather than dressed up as a proof.

        `_excerpt` builds a fresh dict and a fresh tuple every call, so no aliasing
        implementation is reachable from the current body and nothing here can
        falsify one. What the type assertion pins is the annotation
        `excerpt: tuple[MonitorTurn, ...]`, which nothing else enforces — no
        typechecker runs in CI, and `MonitorRequest` being frozen stops rebinding,
        not mutation. It is aimed at a future `excerpt=self._context`, which a
        background task would then serialise while `observe` appended to it.
        """
        gated = GatedClassifier()
        async with watching(gated) as (_, _, _, monitor):
            monitor.observe(agent_turn("judged"))
            await until(lambda: gated.started >= 1, what="the classification to block")

            before = texts(gated.requests[0])
            for index in range(4):
                monitor.observe(caller_turn(f"later {index}"))

            assert isinstance(gated.requests[0].excerpt, tuple)
            assert texts(gated.requests[0]) == before
            gated.gate.set()

    async def test_a_backlog_costs_one_request_not_one_per_turn(self) -> None:
        """The other half of coalescing: the part that keeps it affordable."""
        gated = GatedClassifier()
        async with watching(gated) as (_, _, _, monitor):
            monitor.observe(agent_turn("turn 0"))
            await until(lambda: gated.started >= 1, what="the first classification to block")
            for index in range(1, 5):
                monitor.observe(agent_turn(f"turn {index}"))
            gated.gate.set()
            await until(lambda: gated.started >= 2, what="the coalesced classification")
            await settle()

        assert gated.started == 2, "four queued turns must not cost four requests"
        # All five turns, not just the four that queued: the window is context as
        # well as backlog, so the already-classified turn stays in view.
        assert texts(gated.requests[1]) == [f"turn {i}" for i in range(5)]


class TestFailOpen:
    """A broken classifier and no classifier at all must be the same call."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"hangs": True}, id="hangs"),
            pytest.param({"raises": RuntimeError("boom")}, id="raises"),
            pytest.param({"returns": {"kill": True}}, id="returns-a-dict"),
            pytest.param({"returns": None}, id="returns-none"),
        ],
    )
    async def test_a_broken_classifier_leaves_the_call_running(
        self, kwargs: dict[str, Any]
    ) -> None:
        classifier = BrokenClassifier(**kwargs)
        config = MonitorConfig(timeout_seconds=0.02)
        async with watching(
            classifier, config=config, brain=ScriptedBrain("Oh dear.")
        ) as (conversation, _, sink, monitor):
            await conversation.open()
            await drain(conversation, "This is the bank fraud department.")
            await until(lambda: monitor.classifications >= 1, what="the classification")
            await settle()

            assert not conversation.ended
            assert not conversation.director.watchdog_killed
            assert "watchdog_kill" not in sink.types()

            # Fail *open*, not fail *off*. Every assertion above still holds when the
            # worker dies on its first bad turn and the rest of the call goes
            # unwatched — which is the failure this whole polarity is supposed to
            # avoid, and is what each of these four inputs would cause if the
            # corresponding guard in `_classify` were removed.
            monitor.observe(agent_turn("And another thing entirely."))
            await until(
                lambda: monitor.classifications >= 2, what="the next turn to be watched"
            )
            assert monitor.active

    async def test_a_timeout_is_logged_rather_than_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        classifier = BrokenClassifier(hangs=True)
        config = MonitorConfig(timeout_seconds=0.02)
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(classifier, config=config) as (_, _, _, monitor):
                monitor.observe(agent_turn("Oh, hello dear."))
                await until(
                    lambda: "goes unwatched" in caplog.text, what="the fail-open warning"
                )
        assert "classifier exceeded" in caplog.text

    async def test_a_classifier_timing_out_on_its_own_is_not_blamed_on_the_deadline(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`socket.timeout` *is* `TimeoutError`, `OSError(ETIMEDOUT)` instantiates as
        one, and any `wait_for` inside a classifier raises one — so a transport
        timeout arrives here looking exactly like this module's own deadline expiring.
        Reporting it that way is the misattribution the permit hoist was made to
        avoid, one clause later, and it would send an operator to raise
        `timeout_seconds` against a deadline that never fired."""
        classifier = BrokenClassifier(raises=TimeoutError("the socket gave up"))
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(classifier) as (_, _, _, monitor):
                monitor.observe(agent_turn("Oh, hello dear."))
                await until(lambda: monitor.classifications >= 1, what="the classification")
                await settle()

        assert "with a timeout of its own" in caplog.text
        assert "classifier exceeded" not in caplog.text
        assert "TimeoutError" in caplog.text, "and the traceback survives"

    async def test_a_broken_tap_never_costs_the_canonical_log_an_event(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``Conversation._emit`` already swallows sink failures, so a monitor that
        raised here would look exactly like nothing having happened."""

        def explode(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("the tap is broken")

        monkeypatch.setattr(CallMonitor, "_record", explode)
        stub = StubClassifier()
        with caplog.at_level(logging.ERROR, logger="ssscammers.agent.monitor"):
            async with watching(
                stub, brain=ScriptedBrain("Oh dear.")
            ) as (conversation, _, sink, _):
                await conversation.open()
                await drain(conversation, "This is the bank fraud department.")
                await settle()

        assert sink.types() == ["call_opened", "caller_turn", "phase_changed", "agent_turn"]
        assert "the monitor tap failed" in caplog.text
        assert stub.requests == []


    async def test_a_failing_inner_sink_does_not_blind_the_watchdog(self) -> None:
        """The other direction of the same ordering, and the one with teeth.

        `Conversation._emit` swallows sink failures, so a persistent sink that starts
        throwing — the planned Postgres one is the obvious candidate — would switch
        the watchdog off entirely, leaving nothing in the log but a line about the
        sink. Tapping before forwarding is what stops that, and it is one statement
        order away from being wrong.
        """
        stub = StubClassifier()
        async with watching(
            stub, brain=ScriptedBrain("Oh dear."), sink=RecordingSink(fail=True)
        ) as (conversation, _, _, monitor):
            await conversation.open()
            await drain(conversation, "This is the bank fraud department.")
            await until(lambda: monitor.classifications >= 1, what="the classification")

        assert len(stub.requests) == 1, "a dead sink must not cost the watchdog its eyes"


    async def test_a_failure_outside_the_classifier_stops_the_monitor_cleanly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The module's one fail-*off* path, which had no test.

        Everything above covers `_classify`'s guards, and those are *inside* the
        worker loop. This is the handler around it. If it does not close the tap, the
        worker is gone while `_stopped` stays False — so `observe` keeps recording,
        evicting and counting misses on the turn path for the rest of the call, with
        nothing left to read any of it. The guard would be lying about what it guards.
        """

        def boom(self: CallMonitor) -> tuple[object, ...]:
            raise RuntimeError("the excerpt builder is broken")

        stub = StubClassifier()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.monitor"):
            async with watching(stub) as (conversation, _, _, monitor):
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(CallMonitor, "_excerpt", boom)
                    monitor.observe(agent_turn("Oh, hello dear."))
                    await until(lambda: not monitor.active, what="the worker to stop")

                buffered = len(monitor._context)  # noqa: SLF001
                monitor.observe(caller_turn("the call carries on"))
                assert len(monitor._context) == buffered  # noqa: SLF001

        assert "the monitor stopped" in caplog.text
        assert not conversation.ended, "and the call itself is untouched"


class TestTheVerdictReachesEnforcement:
    async def test_a_kill_verdict_ends_the_call_on_the_next_tick(self) -> None:
        """The headline: a fake classifier flagging a break reaches TERMINATE within
        one evaluation of the verdict, through the seam that already existed."""
        stub = StubClassifier(BREAK)
        async with watching(
            stub, brain=ScriptedBrain("Oh dear.")
        ) as (conversation, clock, sink, monitor):
            await conversation.open()
            await drain(conversation, "This is the bank fraud department.")
            await until(lambda: monitor.classifications >= 1, what="the verdict")
            await settle()

            clock.advance(1)
            actions = [action async for action in conversation.tick()]

            assert any(isinstance(action, HangUp) for action in actions)
            assert conversation.end_reason is EndReason.WATCHDOG_KILL
            killed = next(e for e in sink.events if e.type == "watchdog_kill")
            assert killed.payload["source"] == "monitor"
            assert killed.payload["reason"] == "said it was an AI assistant"
            assert killed.payload["findings"] == ["persona_break"]
            assert all(type(f) is str for f in killed.payload["findings"])

    async def test_the_monitor_stops_itself_once_it_has_killed(self) -> None:
        """Nothing further is decidable, and a second verdict cannot supersede the
        first anyway — so the spend stops too."""
        stub = StubClassifier(BREAK)
        async with watching(stub) as (_, _, _, monitor):
            monitor.observe(agent_turn("I am an AI assistant."))
            await until(lambda: not monitor.active, what="the worker to finish")

            monitor.observe(agent_turn("And another thing."))
            await settle()

        assert len(stub.requests) == 1

    async def test_a_verdict_that_arrives_after_the_call_ended_is_refused(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The race the fail-open polarity makes possible: a classification still in
        flight when the caller hangs up. It must be a log line, not an exception and
        not a rewritten end reason."""
        gated = GatedClassifier(BREAK)
        async with watching(
            gated, brain=ScriptedBrain("Oh dear.")
        ) as (conversation, _, sink, monitor):
            await conversation.open()
            await drain(conversation, "This is the bank fraud department.")
            await until(lambda: gated.started >= 1, what="the classification to block")

            await conversation.caller_hung_up()
            with caplog.at_level(logging.INFO, logger="ssscammers.agent.monitor"):
                gated.gate.set()
                await until(lambda: not monitor.active, what="the worker to finish")

        assert conversation.end_reason is EndReason.CALLER_HANGUP
        assert "watchdog_kill" not in sink.types()
        assert "was not the one that ended it" in caplog.text

    async def test_the_monitor_stops_a_worker_that_is_already_parked(self) -> None:
        """Otherwise a finished call's worker sits on the loop for the life of the
        process, holding its transcript.

        The `await settle()` is the test. Without it the worker task has never been
        scheduled, `while not self._stopped` is already false the first time it runs,
        and the assertion passes through a trivial path that says nothing about the
        wake in `_stop()` — which is the line a real call always needs, because by the
        time `call_ended` arrives its worker is parked on `_pending.wait()`.
        """
        stub = StubClassifier()
        async with watching(stub) as (_, _, _, monitor):
            await settle()
            assert monitor.active, "the worker should be parked, waiting for a turn"

            monitor.observe(event("call_ended", {"phase": "terminate", "reason": "dead_air"}))
            await until(lambda: not monitor.active, what="the worker to exit")

            monitor.observe(agent_turn("Anybody there?"))
            await settle()

        assert stub.requests == []

    async def test_a_turn_that_lands_with_the_hangup_is_not_classified(self) -> None:
        """A trigger and the hangup arriving in the same scheduling slot.

        The worker is parked, both events land before it runs, and it wakes with work
        queued on a call that is already over. Classifying it would spend a request on
        a transcript nothing can act on — `request_kill` would refuse the verdict.
        """
        stub = StubClassifier()
        async with watching(stub) as (_, _, _, monitor):
            await settle()
            monitor.observe(agent_turn("about to be moot"))
            monitor.observe(event("call_ended", {"phase": "terminate", "reason": "dead_air"}))
            await until(lambda: not monitor.active, what="the worker to exit")

        assert stub.requests == []


#: Far longer than a classification may take, and far longer than
#: :data:`TURN_PATH_BUDGET_SECONDS`. The gap between the two is the whole assertion
#: in :class:`TestTheCallDoesNotNoticeAWatcher`: a turn path that awaited the
#: classifier at all would spend this, not that.
UNREACHABLE_DEADLINE_SECONDS = 60.0

#: Real seconds a whole three-turn call may take with a permanently hung classifier
#: watching it. The work itself is microseconds; this is loose enough to survive a
#: badly loaded CI box and still 30x short of the deadline above.
TURN_PATH_BUDGET_SECONDS = 2.0


class TestTheWorkerIsCleanedUpAfterItself:
    async def test_a_stopped_monitor_stops_recording(self) -> None:
        """`observe`'s stop-guard and the `_stop()` that precedes a kill have exactly
        one observable between them: the buffer stops changing once the call is over.
        Without it, a monitor left attached keeps rewriting a finished call's excerpt
        on the turn path with nothing left to read it."""
        stub = StubClassifier()
        async with watching(stub) as (_, _, _, monitor):
            monitor.observe(caller_turn("before"))
            monitor.observe(event("call_ended", {"phase": "terminate", "reason": "dead_air"}))
            await until(lambda: not monitor.active, what="the worker to exit")

            monitor.observe(caller_turn("after"))
            monitor.observe(agent_turn("also after"))
            # No observable alternative, same as the window's memory bound.
            assert [turn.text for turn in monitor._context] == ["before"]  # noqa: SLF001

    async def test_starting_twice_spawns_one_worker(self) -> None:
        conversation, _, _ = pinned_call()
        pool = MonitorPool(classifier=StubClassifier())
        monitor = pool.open(conversation)
        try:
            monitor.start()
            monitor.start()
            assert len(monitor_tasks()) == 1
        finally:
            await monitor.aclose()
        assert monitor_tasks() == [], "and the one worker is gone when the call is"

    async def test_closing_cancels_a_worker_that_is_still_running(self) -> None:
        """Clock-free, deliberately. Deleting `aclose`'s cancel leaves the whole suite
        green — it just waits out the classifier's entire deadline inside `aclose`,
        which in production is call teardown blocked for as long as a classifier is
        allowed to take. Nothing could see the difference until the handle stopped
        being nulled before the cancel."""
        classifier = BrokenClassifier(hangs=True)
        conversation, _, _ = pinned_call()
        pool = MonitorPool(
            classifier=classifier,
            config=MonitorConfig(timeout_seconds=UNREACHABLE_DEADLINE_SECONDS),
        )
        monitor = pool.open(conversation)
        monitor.observe(agent_turn("Oh, hello dear."))
        await until(lambda: classifier.requests != [], what="the classification to start")

        task = monitor._task  # noqa: SLF001 - the cancellation itself is the assertion
        async with asyncio.timeout(TURN_PATH_BUDGET_SECONDS):
            await monitor.aclose()

        assert task is not None and task.cancelled()

    async def test_closing_does_not_swallow_its_own_callers_cancellation(self) -> None:
        """`suppress(CancelledError)` around `await self._task` cannot tell "the worker
        I just cancelled" from "somebody cancelled *me*", and cancelling a task then
        awaiting it always parks for at least one loop iteration — so every close of a
        live worker opened that window. A supervisor shutting a call down got a call
        that reported itself as having finished normally."""
        classifier = SlowCleanupClassifier()
        conversation, _, _ = pinned_call()
        pool = MonitorPool(classifier=classifier)
        monitor = pool.open(conversation)
        try:
            monitor.observe(agent_turn("Oh, hello dear."))
            await until(lambda: classifier.requests != [], what="the classification to start")

            ran_past_aclose = False

            async def close_it() -> None:
                nonlocal ran_past_aclose
                await monitor.aclose()
                ran_past_aclose = True

            closer = asyncio.create_task(close_it())
            await until(lambda: classifier.cleaning_up, what="the worker to reach its cleanup")

            closer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await closer

            assert closer.cancelled(), "the cancellation was absorbed by aclose"
            assert not ran_past_aclose
        finally:
            await monitor.aclose()


class TestTheCallDoesNotNoticeAWatcher:
    async def test_a_hung_classifier_changes_neither_the_timing_nor_the_stream(
        self,
    ) -> None:
        """The zero-latency claim, made falsifiable — in both of its halves.

        The stream comparison alone is not the test. An earlier version of this was
        exactly that, and it stayed green against a mutant that awaited the
        classification inside the tap: the conversation runs on a simulated clock, so
        four seconds of real blocking per turn moved no timestamp and changed no
        payload. It measured that a monitor is *invisible*, never that it is *free*.

        So both are asserted. The clock says the call is unchanged; the wall clock
        says nothing waited for a classifier that is never going to answer.
        """
        script = ["This is the bank fraud department.", "Your account is compromised."]

        async def run(monitored: bool) -> tuple[list[object], list[object], float, float]:
            conversation, clock, sink = pinned_call(
                brain=ScriptedBrain("Oh dear.", "What account, dear?")
            )
            monitor = None
            if monitored:
                pool = MonitorPool(
                    classifier=BrokenClassifier(hangs=True),
                    config=MonitorConfig(timeout_seconds=UNREACHABLE_DEADLINE_SECONDS),
                )
                monitor = pool.open(conversation)
            try:
                started = time.perf_counter()
                actions = list(await conversation.open())
                for line in script:
                    actions += await drain(conversation, line)
                real_seconds = time.perf_counter() - started
                # After the timing window: this is where the worker gets to run and
                # wedge itself on the classifier, which is the state the *next* turn
                # would have to get past if the tap were not free.
                await settle()
            finally:
                if monitor is not None:
                    await monitor.aclose()
            stream = [(e.seq, e.type, e.at_seconds, dict(e.payload)) for e in sink.events]
            return actions, stream, clock.now, real_seconds

        plain_actions, plain_events, plain_clock, _ = await run(monitored=False)
        watched_actions, watched_events, watched_clock, watched_seconds = await run(
            monitored=True
        )

        assert watched_actions == plain_actions
        assert watched_events == plain_events
        assert watched_clock == plain_clock
        assert watched_seconds < TURN_PATH_BUDGET_SECONDS, (
            "the turn path waited on the classifier: a call that should take "
            f"microseconds took {watched_seconds:.1f}s against a "
            f"{UNREACHABLE_DEADLINE_SECONDS}s classifier deadline"
        )


class TestConcurrencyIsBoundedByThePool:
    async def test_a_queued_classification_is_not_charged_for_the_wait(self) -> None:
        """The most-argued decision in `_classify`, pinned without racing a clock.

        The deadline covers the classifier call; the wait for a permit sits outside
        it. Folding the wait inside would make every queued classification fail open
        the moment the process got busy — the one condition under which the watchdog
        most needs to work.

        An earlier version of this test tried to prove that with real durations, a
        50 ms classifier against a 120 ms deadline, and was measurably flaky: the
        classifier's true span was 57 ms on an idle box and 211 ms under load, so the
        margin was scheduler jitter, sampled four times per run. The property cannot
        be pinned by magnitudes — the mutant only fails when the wait exceeds the
        deadline, and the shipped code only passes when the jitter does not.

        So pin the *ordering* instead, with no magnitudes anywhere. The only permit is
        held from outside; the deadline is short enough that any suspension at all
        exceeds it; and the classifier never suspends. As shipped, nothing can yield
        between taking the permit and returning the verdict, so no deadline can
        expire however small. With the deadline wrapping the wait, the worker suspends
        on the contended permit *inside* it and dies there.
        """
        classifier = StubClassifier()
        pool = MonitorPool(
            classifier=classifier,
            config=MonitorConfig(max_concurrent=1, timeout_seconds=1e-6),
        )
        conversation, _, _ = pinned_call()
        monitor = pool.open(conversation)
        try:
            # The pool exposes no way to hold a permit, and the public alternative —
            # a second call with a blocking classifier — reimports the scheduling
            # nondeterminism this test exists to delete. Reached for as a *control*,
            # never as the thing being asserted, which is a much cheaper thing to
            # accept than a private read used as an oracle.
            async with pool._semaphore:  # noqa: SLF001
                monitor.observe(agent_turn("Oh, hello dear."))
                await until(
                    lambda: monitor.classifications >= 1, what="the worker to reach the permit"
                )
                await settle()
                # Proof the worker is queued rather than a hope that it is: the
                # classifier never suspends, so if it held the permit it would
                # already have answered. Without this the mutant would wrap an
                # *uncontended* acquire, which does not suspend, and would survive.
                assert classifier.requests == [], "the worker must be waiting on the permit"

            await until(lambda: classifier.requests != [], what="the queued classification")
        finally:
            await monitor.aclose()

        assert len(classifier.requests) == 1, "a queued classification must not expire waiting"

    async def test_no_more_classifications_run_at_once_than_the_pool_allows(self) -> None:
        """A per-call object cannot have this property, which is the whole reason
        the pool exists."""
        gated = GatedClassifier()
        pool = MonitorPool(classifier=gated, config=MonitorConfig(max_concurrent=2))
        conversations = []
        monitors = []
        for index in range(4):
            conversation, _, _ = pinned_call()
            conversation.call_sid = f"CA-{index}"
            conversations.append(conversation)
            monitors.append(pool.open(conversation))
        try:
            for monitor in monitors:
                monitor.observe(agent_turn("Oh, hello dear."))
            await until(lambda: gated.started >= 2, what="the pool to fill")
            await settle()

            assert gated.started == 2, "two permits, two in flight"
            assert gated.peak == 2

            gated.gate.set()
            await until(lambda: gated.started >= 4, what="the queued classifications")
            await settle()
            assert gated.started == 4, "queued, not dropped"
        finally:
            for monitor in monitors:
                await monitor.aclose()


class TestLifecycle:
    def test_a_failed_open_leaves_the_conversation_untouched(self) -> None:
        """Deliberately a synchronous test: no running loop, so `open` must refuse.

        An earlier version installed the tap first and started the worker second, so
        this path left the conversation holding a `CallMonitor` with no worker and no
        owner — the exception ate the return value — and the nesting guard then
        refused every retry, including a correct one from inside a loop. A wiring
        mistake became a call that could never be watched.
        """
        conversation, _, sink = pinned_call()
        pool = MonitorPool(classifier=StubClassifier())

        with pytest.raises(RuntimeError, match="no running event loop"):
            pool.open(conversation)

        assert conversation.events is sink, "a failed open must change nothing"

    async def test_a_failure_to_start_the_worker_also_changes_nothing(self) -> None:
        """The loop check above catches the one failure anybody has actually hit, so
        it alone would make the previous test pass whatever order the remaining steps
        ran in. This pins the order itself: whatever raises after the check — a closed
        loop reaching `create_task` is the live example — must still leave the
        conversation with the sink it had, and leave `open` retryable."""

        def refuse(self: CallMonitor) -> None:
            raise RuntimeError("the loop is closed")

        conversation, _, sink = pinned_call()
        pool = MonitorPool(classifier=StubClassifier())
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(CallMonitor, "start", refuse)
            with pytest.raises(RuntimeError, match="the loop is closed"):
                pool.open(conversation)
            assert conversation.events is sink

        # And the conversation is still monitorable, which it would not be if the
        # tap had been installed before the failure.
        monitor = pool.open(conversation)
        await monitor.aclose()

    def test_a_pool_that_failed_to_open_is_not_bound_to_that_loop(self) -> None:
        """A failed `open` must leave the pool as unbound as it found it.

        Binding up front meant an attempt that created no task and contended no
        semaphore still claimed the loop it failed on — so the next entirely correct
        `open`, in a live loop, was refused with a message that was simply false, and
        the pool was unusable for the rest of the process. That is the same defect the
        method's own docstring was written to close, one field over.
        """
        pool = MonitorPool(classifier=StubClassifier())

        def refuse(self: CallMonitor) -> None:
            raise RuntimeError("the loop is closed")

        async def failing_open() -> None:
            conversation, _, _ = pinned_call()
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(CallMonitor, "start", refuse)
                with pytest.raises(RuntimeError, match="the loop is closed"):
                    pool.open(conversation)

        async def working_open() -> None:
            conversation, _, _ = pinned_call()
            monitor = pool.open(conversation)
            await monitor.aclose()

        asyncio.run(failing_open())
        asyncio.run(working_open())

    def test_a_pool_refuses_a_second_event_loop(self) -> None:
        """Its semaphore binds to the loop that first contends it, so a shared pool
        hands the second loop a semaphore owned by the first. Refused here, loudly,
        rather than surfacing later from inside `_classify` — where this module would
        report it as the classifier's fault, on a classifier never entered."""
        pool = MonitorPool(classifier=StubClassifier())

        async def open_and_close() -> None:
            conversation, _, _ = pinned_call()
            monitor = pool.open(conversation)
            await monitor.aclose()

        asyncio.run(open_and_close())
        with pytest.raises(RuntimeError, match="another event loop"):
            asyncio.run(open_and_close())


    async def test_a_conversation_can_only_be_monitored_once(self) -> None:
        conversation, _, _ = pinned_call()
        pool = MonitorPool(classifier=StubClassifier())
        monitor = pool.open(conversation)
        try:
            with pytest.raises(RuntimeError, match="already has a monitor"):
                pool.open(conversation)
        finally:
            await monitor.aclose()

    async def test_closing_puts_the_original_sink_back_and_repeats_safely(self) -> None:
        conversation, _, sink = pinned_call()
        pool = MonitorPool(classifier=StubClassifier())
        monitor = pool.open(conversation)
        assert conversation.events is monitor

        await monitor.aclose()
        assert conversation.events is sink
        # Real because `aclose` no longer clears the task handle. It used to, which
        # made this assertion true by assignment — it passed just as happily against
        # an `aclose` that cancelled nothing and left the worker holding a permit,
        # the conversation, and the transcript.
        assert not monitor.active

        await monitor.aclose()
        assert conversation.events is sink

    async def test_a_closed_monitor_no_longer_sees_the_call(self) -> None:
        stub = StubClassifier()
        conversation, _, sink = pinned_call(brain=ScriptedBrain("Oh dear."))
        pool = MonitorPool(classifier=stub)
        monitor = pool.open(conversation)
        await monitor.aclose()

        await conversation.open()
        await drain(conversation, "This is the bank fraud department.")
        await settle()

        assert stub.requests == []
        assert "agent_turn" in sink.types(), "the call carried on and still logged"


class TestConfigRefusesTheQuietlyBrokenValues:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("excerpt_turns", 0),
            ("excerpt_turns", -1),
            ("max_turn_chars", 0),
            ("max_concurrent", 0),
            ("max_concurrent", True),
            ("timeout_seconds", 0),
            ("timeout_seconds", -1.0),
            ("timeout_seconds", float("nan")),
        ],
    )
    def test_it_refuses(self, field: str, value: object) -> None:
        """Each of these fails *silently* when accepted: a zero window discards every
        turn, a zero permit count admits nobody, and a NaN deadline is never exceeded
        because every comparison against NaN is false."""
        with pytest.raises(ValueError):
            MonitorConfig(**{field: value})  # type: ignore[arg-type]


class TestTheVerdictShape:
    def test_a_bare_string_finding_is_refused(self) -> None:
        """It satisfies ``Sequence[str]``, splats into characters, and produces valid
        JSON that nothing downstream would notice was wrong."""
        with pytest.raises(TypeError, match="not a bare str"):
            Verdict(kill=True, findings="persona_break")  # type: ignore[arg-type]

    def test_findings_are_normalised_to_the_values_a_payload_wants(self) -> None:
        verdict = Verdict(kill=True, findings=[MonitorFinding.ABUSIVE_TONE, "something_new"])
        assert verdict.findings == ("abusive_tone", "something_new")
        # The equality above holds either way — `MonitorFinding` is a `StrEnum` and
        # compares equal to its own value — so the type is what actually pins the
        # coercion. `LoggingEventSink` formats the payload with `%s`, which reprs the
        # values inside it, and an uncoerced member reaches the canonical log as
        # `<MonitorFinding.ABUSIVE_TONE: 'abusive_tone'>`.
        assert all(type(finding) is str for finding in verdict.findings)

    def test_an_unknown_finding_still_kills(self) -> None:
        """The vocabulary is what a classifier is *asked* for. Validating it and
        failing the verdict when it does not parse would turn a model inventing a
        word into a guardrail that silently stops enforcing."""
        assert Verdict(kill=True, findings=["a_label_nobody_has_thought_of"]).kill

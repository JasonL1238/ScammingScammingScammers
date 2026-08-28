"""G-17's out-of-band watchdog: the tap it listens on, and what it does with a verdict.

The project's design rule is that nothing safety-critical is PROMPT-only, and six
guardrails were. This is the MONITOR half — a second reader of the call that is allowed
to stop it. It is *out of band* in the strongest sense available: it never runs inside a
turn, never holds a lock a turn holds, and cannot delay a single sentence of audio. The
worst a broken, hung, or hostile classifier can do to a live call is nothing at all.

**Fail open, and say so plainly.** A timeout, an exception, a nonsense return value, and
a classifier that was never wired up are all the same outcome: the deterministic verdict
stands and the call continues. That is the correct polarity for a system whose CODE
guardrails already protect a real caller unaided — a fail-closed monitor would hand a
model the power to hang up on someone mid-disclosure. The price is that a *silent* miss
is invisible, which is why every fail-open path here logs, and why sampling is not an
economy this module is allowed to take: an unclassified turn and a timed-out turn would
be indistinguishable in the log, and the miss rate would stop being measurable.

**Three things are worth stating before reading the code.**

*The tap is the event sink, not a new channel.* :class:`CallMonitor` implements
:class:`~ssscammers.agent.conversation.EventSink` and wraps the sink the conversation
already had. Nothing in :mod:`ssscammers.agent.conversation` knows this module exists,
the canonical log is unchanged, and — because a clean monitor emits nothing and touches
no shared state — a monitored call replays byte-identically to an unmonitored one. The
verdict's own ``watchdog_kill`` event is emitted by the *conversation*, at the evaluation
that acts on it, precisely so that an out-of-band task never writes into the per-call
sequence.

*A verdict arrives after the turn it judges.* The ``agent_turn`` event carries the
finished text, so by the time this module sees a turn, the caller has heard it. That is
not a defect being tolerated, it is the boundary between the layers: what must never be
*said* is the deterministic pre-TTS filter's job (G-3, G-4, CODE), and it runs inline.
What this catches is the turn *after* — the call is stopped before the persona does it
again. In practice the gap is smaller than it sounds, because the plan that follows a
baiting turn often puts the phone down for up to ninety seconds, and a verdict landing in
that window kills the call before another word is composed.

*What triggers a classification is narrower than what feeds one.* Every caller turn and
every agent turn becomes context. Only a *model-generated* agent turn starts a
classification. Caller speech alone does not, because nothing the caller says can breach
a guardrail — the excerpt that judges the reply contains the speech that provoked it, so
triggering on both would double the spend and see nothing extra. Fixed scripts do not,
because they are human-reviewed constants. That rule is emphatically *not* what keeps a
verdict off a disclosure — that is :meth:`_act`'s business — so the only thing it buys is
a request not spent.

Whether it buys even that today is a narrower question than it looks, and worth writing
down because the obvious answer is wrong. Every plan that speaks a fixed script also hangs
up, so ``call_ended`` follows — but not immediately: ``_execute`` *suspends* at
``yield Say(...)`` between the two emits, and a consumer that yields to the loop there
hands this worker a whole scheduling slot to classify the disclosure in. The terminal
harness never yields (it drains the generator in a comprehension), and production's
``perform_stream`` awaits ``push_frame``, which on the default path — no registered
push-frame handler, no observer, an unbounded queue — also completes without suspending.
So the rule is not load-bearing *on today's stack*, which is a much weaker claim than "it
can never fire" and rests on third-party scheduling details under a dependency pinned only
``>=``. It is cheap, and it stops being conditional the day one of those details moves.

**Cost, and the one place it is allowed to cost coverage.** This is the first component
that spends model tokens on every turn of every call, so the shape is cheap by
construction: an excerpt of at most :attr:`MonitorConfig.excerpt_turns` turns, each capped
at :attr:`MonitorConfig.max_turn_chars`, and at most one request per model turn. Worst
case that is about 12k characters — call it 3k tokens — per classification; a typical
call's turns are an order of magnitude shorter than the cap.

Turns arriving faster than the classifier answers **coalesce** into the next request
rather than queueing one request each. Coalescing is lossless only while the excerpt still
holds everything unjudged, so the excerpt is built to keep *model* turns in preference to
caller context — the caller's words cannot breach a guardrail and the persona's can, so
context is the right thing to spend first. A model turn that falls out of the window even
so is a real miss, and it is logged as a model turn rather than counted among the caller
context that scrolled past. That distinction is the whole of the "no silent cap" rule
here: a warning that says "two turns scrolled out" cannot tell a harmless loss from a
G-17 miss.

The knobs are constructor arguments today. They become :class:`Settings` fields when the
pool is wired in at the application level, which is the task that also gives G-20 its
``call_sid``-to-``Conversation`` map. Note what that will change: the tripwire in
``tests/test_monitor.py`` that keeps :attr:`MonitorConfig.max_turn_chars` above anything
``ClaudeBrain`` can emit compares two *defaults*, and proves nothing about a running
system once an operator can set the value. That is why truncating a model turn logs.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from ssscammers.agent.conversation import CallEvent, Conversation, EventSink
from ssscammers.shared.enums import TurnRole

logger = logging.getLogger(__name__)

__all__ = [
    "MonitorTurn",
    "MonitorRequest",
    "Verdict",
    "Classifier",
    "MonitorConfig",
    "TRUNCATION_MARKER",
    "CallMonitor",
    "MonitorPool",
]


# ---------------------------------------------------------------------------
# What a classifier is shown, and what it may say back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorTurn:
    """One turn of the excerpt.

    ``seq`` is the conversation's own per-call sequence number, carried because the
    excerpt is assembled from two collections — the turns awaiting judgment and the
    surrounding context — and merging them needs an identity and an order that do not
    depend on object equality.

    ``scripted`` rides along rather than being filtered out: a classifier judging
    whether the persona broke needs to know which words were the persona's own and
    which are a fixed script it is *supposed* to say verbatim. Dropping them would
    make the disclosure look like the worst persona break in the transcript.

    Deliberately not :class:`~ssscammers.agent.llm.Turn`, which is the *API* shape:
    its ``role`` is ``"user"``/``"assistant"`` because that is what the Anthropic
    request wants. Reusing it here would bind what the watchdog is shown to what the
    persona model is sent, which is the coupling the :class:`Classifier` protocol
    exists to prevent.
    """

    seq: int
    role: TurnRole
    text: str
    scripted: bool
    at_seconds: float


@dataclass(frozen=True)
class MonitorRequest:
    """Everything a classifier gets. Deliberately the whole of it.

    Widening a :class:`Protocol` later means touching every implementation, so the
    fields a model-backed classifier will want are here from the start —
    ``persona_id`` because "claims to be a real person" cannot be judged without
    knowing which fictional person the agent is supposed to be, and ``call_sid``
    because a classifier's own log line is useless without it.

    ``excerpt`` is a snapshot, not a view. The buffer it is taken from keeps changing
    while a classification is in flight — that is the ordinary case here, not the
    pathological one — so handing over the live ``deque`` would let a classifier
    iterate a collection that is being appended to, and would leave this frozen
    dataclass advertising an immutability it did not have.
    """

    call_sid: str
    persona_id: str
    excerpt: tuple[MonitorTurn, ...]


@dataclass(frozen=True)
class Verdict:
    """A classifier's answer. ``kill`` is the decision; ``findings`` is the evidence.

    The two are deliberately not coupled. A finding outside
    :class:`~ssscammers.shared.enums.MonitorFinding` is kept verbatim rather than
    dropped or rejected, because the alternative — validating the label and failing
    the verdict when it does not parse — turns a model inventing a word into a
    guardrail that silently stops enforcing. The vocabulary is what a classifier is
    *asked* for; the boolean is what is *acted* on.
    """

    kill: bool
    findings: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.findings, str):
            # Same trap as `Conversation.request_kill`: a bare `str` satisfies
            # `Sequence[str]`, splats into characters, and produces valid JSON that
            # nothing downstream would ever notice was wrong.
            raise TypeError(
                f"findings must be a sequence of strings, not a bare str: {self.findings!r}"
            )
        # Coerced to plain `str`, not left as enum members. `json.dumps` renders either
        # the same way, so this is not about serialization: `LoggingEventSink` formats
        # the whole payload with `%s`, which reprs the values *inside* it, and an
        # uncoerced member reaches the canonical log as
        # `<MonitorFinding.PERSONA_BREAK: 'persona_break'>`.
        object.__setattr__(self, "findings", tuple(str(finding) for finding in self.findings))


class Classifier(Protocol):
    """The model-backed half, kept behind a seam.

    One implementation is shared across calls, so it must be stateless with respect
    to any one call: everything call-specific arrives in the request. It may raise
    and it may hang — :meth:`CallMonitor._classify` treats both as "no verdict".

    The one thing it must **not** do is swallow its own cancellation. A classifier
    that catches :class:`asyncio.CancelledError` and returns normally blocks
    :meth:`CallMonitor.aclose` for as long as it likes, and call teardown waits on
    it. Cleaning up in a ``finally`` and re-raising is fine and expected.
    """

    async def classify(self, request: MonitorRequest) -> Verdict: ...


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorConfig:
    """How much the watchdog is allowed to see, spend, and wait.

    Args:
        excerpt_turns: Turns per classification, newest-last. Also the coalescing
            window: turns arriving faster than the classifier answers are merged into
            the next request, and this is how many of them survive that merge. Model
            turns awaiting judgment are kept in preference to caller context; a model
            turn that falls out even so is logged.
        max_turn_chars: Per-turn ceiling, applied head-first with a marker. Set above
            what :class:`~ssscammers.agent.llm.ClaudeBrain` can emit in one turn, so
            an *agent* turn — the thing being judged — is not truncated at today's
            defaults. That is an arithmetic accident of two defaults, not an enforced
            invariant, so truncating a model turn logs a warning rather than trusting
            the arithmetic to keep holding.
        timeout_seconds: Ceiling on one classification. Fail-open on expiry.
        max_concurrent: Simultaneous classifier requests across every call in the
            process. Bounds the monitor's outbound load independently of the call
            cap, so raising ``MAX_CONCURRENT_CALLS`` cannot quietly make the
            watchdog the dominant spender.
    """

    excerpt_turns: int = 6
    max_turn_chars: int = 2000
    timeout_seconds: float = 4.0
    max_concurrent: int = 4

    def __post_init__(self) -> None:
        # Validated rather than trusted because every one of these fails *quietly*
        # when wrong: `excerpt_turns=0` gives a `deque` that discards everything and
        # a watchdog that never sees a turn, and `max_concurrent=0` gives a semaphore
        # that never admits anybody. Both look exactly like a clean call.
        for name in ("excerpt_turns", "max_turn_chars", "max_concurrent"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive int, got {value!r}")
        if not (self.timeout_seconds > 0):
            raise ValueError(f"timeout_seconds must be positive, got {self.timeout_seconds!r}")


#: The one truncation marker, so a classifier prompt and a test agree on it.
TRUNCATION_MARKER = " […]"


# ---------------------------------------------------------------------------
# The per-call watchdog
# ---------------------------------------------------------------------------


@dataclass
class CallMonitor:
    """Watches one call. Built by :meth:`MonitorPool.open`, not directly.

    Two objects in one, because they are two halves of the same thing: an
    :class:`~ssscammers.agent.conversation.EventSink` that wraps the sink the
    conversation already had, and a background task that classifies what the sink
    saw. The sink half is synchronous: it appends to two bounded deques and sets an
    :class:`asyncio.Event`, and in one case — a turn long enough to be truncated —
    writes a log line, which is synchronous I/O. That is the whole of what runs on
    the turn path, and the truncation branch is the only part of it whose cost is not
    constant.
    """

    conversation: Conversation
    classifier: Classifier
    inner: EventSink
    """The sink this one wraps. Every event reaches it, in order, unaltered."""

    semaphore: asyncio.Semaphore
    """Shared across calls. Owned by the pool, never by one monitor."""

    config: MonitorConfig = MonitorConfig()

    _context: deque[MonitorTurn] = field(init=False)
    """Every recorded turn, caller and agent. The surrounding conversation."""

    _unjudged: deque[MonitorTurn] = field(init=False)
    """Model turns not yet carried into a classified excerpt. The work queue, held as
    the turns themselves rather than as a count — a count says *how many* are waiting
    and cannot say *which*, so it cannot tell whether the excerpt still contains
    them."""

    _missed: int = field(init=False, default=0)
    _pending: asyncio.Event = field(init=False, default_factory=asyncio.Event)
    _classifications: int = field(init=False, default=0)
    _stopped: bool = field(init=False, default=False)
    _task: asyncio.Task[None] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._context = deque(maxlen=self.config.excerpt_turns)
        self._unjudged = deque(maxlen=self.config.excerpt_turns)

    # -- state a caller may read ---------------------------------------------

    @property
    def active(self) -> bool:
        """Whether the worker is still running. False once the call ended, a kill
        was issued, or :meth:`aclose` ran."""
        return self._task is not None and not self._task.done()

    @property
    def classifications(self) -> int:
        """How many classifications were *attempted*, including the ones that failed
        open and the ones that never reached a classifier at all — the count is
        incremented before the permit is asked for. So it bounds how much this call
        was watched; it is not a denominator, because a request that never reached a
        classifier belongs to neither half of that ratio."""
        return self._classifications

    # -- the sink half --------------------------------------------------------

    async def emit(self, event: CallEvent) -> None:
        """Tap, then forward. **The order is load-bearing in both directions.**

        The tap runs first because a sink that raises must not blind the watchdog:
        :meth:`Conversation._emit` swallows sink failures, so a persistent sink that
        started throwing would otherwise switch the monitor off with no symptom but a
        line about the sink. And the tap is wrapped because a watchdog that raises
        must not cost the canonical log an event — the same argument, in the other
        direction.
        """
        try:
            self.observe(event)
        except Exception:  # noqa: BLE001 - the log outranks the watchdog
            logger.exception("the monitor tap failed on %s; the call continues", event.type)
        await self.inner.emit(event)

    def observe(self, event: CallEvent) -> None:
        """Record a turn and, if it is a trigger, wake the worker. Never blocks.

        Separate from :meth:`emit` because this is the whole of the turn-path cost
        and it is worth being able to see, and test, on its own.
        """
        if self._stopped:
            # Belt and braces: the worker has already gone, so nothing would come of
            # buffering. Cheap enough to keep for a monitor whose `aclose` never ran.
            return

        if event.type in ("call_ended", "watchdog_kill"):
            # Nothing further can be enforced, so stop rather than classify a
            # transcript that has already finished. A classification in flight when
            # this arrives is abandoned by the worker for the same reason.
            self._stop()
            return

        if event.type == "caller_turn":
            self._record(TurnRole.CALLER, event, scripted=False)
            return

        if event.type != "agent_turn":
            return

        scripted = bool(event.payload.get("scripted"))
        turn = self._record(TurnRole.AGENT, event, scripted=scripted)
        if turn is None or scripted:
            return

        if len(self._unjudged) == self._unjudged.maxlen:
            # The oldest model turn is about to be pushed out by this one, and it was
            # never carried into a classified excerpt. That is a genuine G-17 miss,
            # counted here — at the moment it becomes true — and reported at the next
            # take, where the window size that caused it is in scope.
            self._missed += 1
        self._unjudged.append(turn)
        self._pending.set()

    def _record(self, role: TurnRole, event: CallEvent, *, scripted: bool) -> MonitorTurn | None:
        """Append one turn to the context buffer. Returns it, or ``None`` if dropped.

        An empty turn is not kept. A killed turn is genuinely empty — the sentence
        loop stops before the first sentence — and so is a stream that produced
        nothing; neither is a turn the persona took, and both would read to a
        classifier as the persona going silent.
        """
        text = str(event.payload.get("text") or "").strip()
        if not text:
            return None
        if len(text) > self.config.max_turn_chars:
            # Head-first, because whatever a turn is going to be killed for is
            # normally established in its opening words, and the marker tells the
            # classifier that the turn continued.
            original = len(text)
            text = text[: self.config.max_turn_chars].rstrip() + TRUNCATION_MARKER
            if role is TurnRole.AGENT and not scripted:
                # The persona's own words are the thing being judged, so cutting them
                # is a partial blind spot and gets the same treatment as every other
                # one here: a line in the log. Caller speech is context and long
                # transcriptions are ordinary, so that half is only debug — and so is
                # a fixed script, which is never judged by design. Without the
                # `scripted` test this warning fires on every disclosed call at any
                # tuned-down cap, which is how the one line standing between an
                # operator and a half-read persona becomes the line they ignore.
                logger.warning(
                    "truncated a %d-character model turn to %d for classification on "
                    "call %s; the tail of the persona's own words was not judged",
                    original,
                    self.config.max_turn_chars,
                    self.conversation.call_sid or "unknown",
                )
            else:
                logger.debug(
                    "truncated a %d-character caller turn to %d for classification",
                    original,
                    self.config.max_turn_chars,
                )
        turn = MonitorTurn(
            seq=event.seq,
            role=role,
            text=text,
            scripted=scripted,
            at_seconds=event.at_seconds,
        )
        self._context.append(turn)
        return turn

    # -- the worker half ------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker. Requires a running loop; idempotent."""
        if self._task is None:
            name = f"monitor:{self.conversation.call_sid or 'call'}"
            self._task = asyncio.create_task(self._run(), name=name)

    async def aclose(self) -> None:
        """Stop watching and put the original sink back. Idempotent.

        Restoring ``inner`` matters for a conversation that outlives its monitor: a
        detached tap that kept receiving events would keep a dead call's transcript
        alive and hold a reference the call is finished with.

        The task handle is deliberately *not* cleared. Nulling it would make
        :attr:`active` report ``False`` by assignment rather than because anything
        stopped, and any test asserting on that property would then pass just as
        happily against an ``aclose`` that cancelled nothing.

        ``asyncio.wait`` rather than ``await self._task`` under a
        ``suppress(CancelledError)``. That idiom cannot tell "the worker I just
        cancelled" from "somebody cancelled *me*" — and the second is not rare, since
        cancelling a task and awaiting it always parks for at least one loop
        iteration. Swallowing it means a supervisor shutting a call down gets a call
        that reports itself as having finished normally. ``wait`` never re-raises the
        awaited task's cancellation, and lets an outer one through untouched.
        """
        self._stop()
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait({self._task})
        if self.conversation.events is self:
            self.conversation.events = self.inner
        logger.info(
            "monitor closed after %d classification(s) on call %s",
            self._classifications,
            self.conversation.call_sid or "unknown",
        )

    def _report_missed(self) -> None:
        """Say what was never judged, and say it once.

        Called from both places a miss can become final: the next take, and the stop.
        The stop matters more. `_missed` only grows while the classifier is behind,
        and a call whose classifier is behind is the likeliest one to end while it is
        still behind — a scammer hanging up the moment the persona breaks is the modal
        ending here. Reporting only at the next take biases the measured miss rate
        low, and biases it low hardest exactly when the pool is saturated, which is
        the measurement the "no silent cap" rule exists to protect.
        """
        if not self._missed:
            return
        logger.warning(
            "monitor fell behind on call %s: %d model turn(s) were never shown to "
            "the classifier — they left the %d-turn excerpt before it caught up",
            self.conversation.call_sid or "unknown",
            self._missed,
            self.config.excerpt_turns,
        )
        self._missed = 0

    def _stop(self) -> None:
        self._report_missed()
        self._stopped = True
        # Wake a worker parked on `_pending.wait()` so it can notice and exit rather
        # than sitting on the loop for the lifetime of the process. This is the line
        # that makes "the worker stops itself when the call ends" true of a call that
        # actually ran; the `while` condition alone only covers a worker that has not
        # been scheduled yet.
        self._pending.set()

    async def _run(self) -> None:
        # `while True`, not `while not self._stopped`: every exit from this loop is a
        # `return`, so the condition could never end it. Two spellings of the same
        # stop, one of which nothing could reach, is how a refactor removes the half
        # that mattered.
        try:
            while True:
                await self._pending.wait()
                self._pending.clear()
                if self._stopped:
                    return
                verdict = await self._classify(self._take_request())
                if verdict is None or not verdict.kill:
                    continue
                # Two statements, two jobs: `_stop` closes the tap, so a monitor left
                # attached to an ending call stops buffering; `return` retires this
                # worker. Neither substitutes for the other.
                self._stop()
                self._act(verdict)
                return
        except Exception:  # noqa: BLE001 - fail open, loudly
            # Without this the task dies with a warning nobody reads at interpreter
            # shutdown, and the call runs unwatched with no other trace.
            #
            # `_stop()` and not a bare log, because this is the module's one fail-*off*
            # path and it has to leave consistent state behind. Without it the worker
            # is gone while `_stopped` stays False, so `observe` keeps recording,
            # evicting, and counting misses on the turn path for the rest of the call
            # with nothing left to read any of it — the guard lying about what it
            # guards. Going through `_stop` also flushes the losses counted so far.
            self._stop()
            logger.exception(
                "the monitor stopped on call %s; the call continues unwatched",
                self.conversation.call_sid or "unknown",
            )

    def _take_request(self) -> MonitorRequest:
        """Snapshot the excerpt for everything waiting to be judged.

        Raises rather than returning ``None`` on an empty queue. Nothing reaches that
        state — ``_pending`` is set only alongside an append to ``_unjudged``, and the
        one other setter, ``_stop``, is answered before this is called — so a ``None``
        return would have been an unreachable branch, and its handler in the worker a
        second one. The check is kept and made loud instead of dropped, because the
        alternative to skipping is building an excerpt with nothing to judge in it:
        caller speech alone, which is the defect this design was rewritten to remove,
        and which would cost a real model request to discover.
        """
        if not self._unjudged:
            raise RuntimeError("the monitor worker woke with nothing to judge")

        self._report_missed()
        excerpt = self._excerpt()
        self._unjudged.clear()
        return MonitorRequest(
            call_sid=self.conversation.call_sid,
            persona_id=self.conversation.director.persona.id,
            excerpt=excerpt,
        )

    def _excerpt(self) -> tuple[MonitorTurn, ...]:
        """The turns awaiting judgment, plus as much recent context as the budget allows.

        Priority rather than recency, and that is the whole point. "The last N turns"
        is the obvious rule and it is wrong: a model turn waiting on a slow classifier
        can be pushed out of the window by the caller's replies, and then the request
        that turn *raised* is spent judging an excerpt that does not contain it — a
        real model call, a real permit, and a clean verdict reached by reading nothing
        but caller speech. The caller's words cannot breach a guardrail and the
        persona's can, so context is what gets spent first.
        """
        limit = self.config.excerpt_turns
        kept = {turn.seq: turn for turn in self._unjudged}
        for turn in reversed(self._context):
            if len(kept) >= limit:
                break
            kept.setdefault(turn.seq, turn)
        return tuple(sorted(kept.values(), key=lambda turn: turn.seq))

    async def _classify(self, request: MonitorRequest) -> Verdict | None:
        """One classification, bounded twice. ``None`` means "no verdict, carry on".

        The two bounds are deliberately separate. The deadline covers the classifier
        call alone, not the wait for a permit: putting the queue inside the deadline
        would make every classification fail open the moment the process got busy,
        which is the one condition under which the watchdog most needs to work. The
        wait is still bounded in practice — one classification in flight per call, so
        with ``C`` concurrent calls and ``N`` permits a request waits at most
        ``ceil(C / N)`` deadlines.

        The permit is acquired *outside* the ``try``, and that is not a style choice:
        everything inside it is reported as the classifier's fault, and failing to
        acquire a permit is not the classifier's fault. A pool misused across two
        event loops raises from this ``async with``, and catching it below would log
        "classifier failed" about a classifier that was never entered. A fail-open
        path whose log names the wrong component is worse than one that crashes.
        """
        self._classifications += 1
        async with self.semaphore:
            try:
                async with asyncio.timeout(self.config.timeout_seconds) as deadline:
                    verdict = await self.classifier.classify(request)
            except TimeoutError:
                # `deadline.expired()`, not the bare exception type. A classifier can
                # raise `TimeoutError` itself — `socket.timeout` *is* `TimeoutError`,
                # `OSError(ETIMEDOUT)` instantiates as one, and any `wait_for` inside
                # a classifier raises one — and reporting that as this module's own
                # deadline expiring is the misattribution the permit hoist above was
                # made to avoid, one clause later. It would also send an operator to
                # raise `timeout_seconds` against a deadline that never fired.
                if not deadline.expired():
                    logger.exception(
                        "classifier failed on call %s with a timeout of its own; "
                        "this turn goes unwatched",
                        request.call_sid or "unknown",
                    )
                    return None
                logger.warning(
                    "classifier exceeded %.1fs on call %s; this turn goes unwatched",
                    self.config.timeout_seconds,
                    request.call_sid or "unknown",
                )
                return None
            except Exception:  # noqa: BLE001 - a live call outranks any one verdict
                logger.exception(
                    "classifier failed on call %s; this turn goes unwatched",
                    request.call_sid or "unknown",
                )
                return None

        if not isinstance(verdict, Verdict):
            # A `Protocol` is not enforced at runtime and no typechecker runs in CI,
            # so this is the only thing standing between a classifier returning a
            # dict and `verdict.kill` raising `AttributeError` inside the worker.
            logger.error(
                "classifier returned %s, not a Verdict; this turn goes unwatched",
                type(verdict).__name__,
            )
            return None
        return verdict

    def _act(self, verdict: Verdict) -> None:
        """Hand the verdict to the enforcement seam. Synchronous, like the seam.

        Nothing is emitted here. The ``watchdog_kill`` event belongs to the
        conversation's own sequence and is written at the evaluation that acts on the
        latch — a background task writing into that sequence would interleave
        differently on every run and break the replay gate.

        **This is also where a verdict is stopped from suppressing a fixed script**,
        and it is stopped by the callee rather than here: ``request_kill`` refuses
        once the phase is terminal, and the state machine independently ranks a
        watchdog kill below every real-person exit. Not classifying the scripts is a
        cost decision; those two are the control. Anyone reasoning about G-11/G-12
        should look there, not at the trigger rule.
        """
        accepted = self.conversation.request_kill(
            source="monitor", reason=verdict.reason, findings=verdict.findings
        )
        if not accepted:
            logger.info(
                "monitor verdict on call %s was not the one that ended it "
                "(the call had already committed to an exit, or a kill was already latched)",
                self.conversation.call_sid or "unknown",
            )


# ---------------------------------------------------------------------------
# What the application holds
# ---------------------------------------------------------------------------


@dataclass
class MonitorPool:
    """One classifier and one concurrency budget, shared by every call.

    The pool exists because bounded concurrency is not a property a per-call object
    can have. It is also the only sensible owner of a model client: one client, one
    connection pool, one place to hold the narrow key the monitor is given.

    **A pool belongs to one event loop.** Its semaphore binds to the loop that first
    *contends* it, so sharing one across two loops hands the second loop a semaphore
    owned by the first — and the failure would surface from inside a classification,
    where this module reports it as the classifier's fault. :meth:`open` refuses it up
    front instead.
    """

    classifier: Classifier
    config: MonitorConfig = MonitorConfig()

    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        # Eagerly, not lazily. An earlier version deferred this on the theory that a
        # semaphore built before a loop existed would bind to the wrong one; it does
        # not — nothing binds until the first contended acquire. The deferral bought
        # nothing, and it did not address the case that actually breaks, which is the
        # one `open` now refuses.
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

    def open(self, conversation: Conversation) -> CallMonitor:
        """Start watching ``conversation``, and return the monitor watching it.

        Installs itself as the conversation's event sink, wrapping whatever was
        there. That is a mutation of a live object, done here rather than at
        construction because the two point at each other — the monitor needs the
        conversation to request a kill, and the conversation needs the monitor to be
        its sink.

        **Ordered so that a failure changes nothing.** Every step that can raise runs
        before the sink is swapped. An earlier version installed the tap first and
        started the worker second, so a call from outside a running loop left the
        conversation holding a ``CallMonitor`` with no worker, no owner — the
        exception ate the return value — and no way back, because the guard below then
        refused every retry. A wiring mistake became a call that could never be
        watched.

        Must be called with a running event loop. The caller owns the returned
        monitor and must :meth:`CallMonitor.aclose` it when the call is done.
        """
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError(
                "this MonitorPool belongs to another event loop; its concurrency "
                "budget cannot be shared across loops. Build one pool per loop."
            )
        if isinstance(conversation.events, CallMonitor):
            raise RuntimeError(
                "this conversation already has a monitor; nesting two would double "
                "every classification and log every event twice"
            )
        monitor = CallMonitor(
            conversation=conversation,
            classifier=self.classifier,
            inner=conversation.events,
            semaphore=self._semaphore,
            config=self.config,
        )
        monitor.start()
        conversation.events = monitor
        # Bound last, once nothing can still fail. Assigning it up front made a failed
        # `open` poison the pool: the loop it never actually used was recorded anyway,
        # and the next entirely correct `open` — in a live loop, with no task and no
        # contended semaphore ever created on the first — was refused with a message
        # that was simply false. That is this method's own docstring's promise broken
        # one field over from where it was first broken.
        self._loop = loop
        return monitor

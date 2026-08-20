"""What the process knows about the calls it is holding right now.

Three separate needs turn out to be the same object, which is why this exists rather
than three counters in three modules:

**Capacity (G-15).** The webhook has to decide in milliseconds whether there is room
for another call, so the count cannot come from a query. One number, read and written
in-process.

**Handoff.** The webhook does the pre-answer work — who is calling, how they got here,
which persona answers — and then returns a TwiML document and ends. The media pipeline
arrives moments later on a *different* connection and needs that verdict. Twilio's
``<Parameter>`` elements carry some of it, but they are attacker-visible in the sense
that anything reaching the WebSocket claims them, so the socket's claims are checked
against what the webhook actually decided here.

**The end of the call.** ``/twilio/after-stream`` has to choose between a hangup and a
voicemail, and only the record of how the call ended can answer that.

Single process, single event loop, by deployment (see ``docker-compose.yml``). Every
mutation here is therefore synchronous and lock-free — no ``await`` inside any critical
section, so no state can be observed half-updated. Multiple workers would silently
multiply the concurrency cap, which is why :mod:`ssscammers.agent.__main__` refuses to.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ssscammers.agent.daily_ledger import DailyLedger
from ssscammers.shared.enums import CallPhase, CallStatus, EndReason, EntryPath

__all__ = ["ActiveCall", "CallRegistry", "Admission"]


@dataclass(frozen=True)
class ActiveCall:
    """One call this process is holding, from webhook to hangup."""

    call_sid: str
    caller_number: str
    entry_path: EntryPath
    persona_id: str
    reserved_at: float
    """Monotonic clock, not wall clock: this is only ever used for durations."""

    status: CallStatus = CallStatus.RINGING
    stream_attached: bool = False
    stream_ever_attached: bool = False
    """Whether a media socket was *ever* served for this call.

    Separate from ``stream_attached``, which clears when the pipeline ends while the slot
    stays held for the voicemail. A second socket in that window would hand anyone with
    the path token a fresh conversation on our bill, and let its teardown overwrite the
    outcome ``/twilio/after-stream`` reads.
    """

    recording_sid: str | None = None
    final_phase: CallPhase | None = None
    end_reason: EndReason | None = None
    voicemail_promised: bool = False
    """Whether this caller is owed a voicemail, reported by the pipeline rather than
    inferred here: ``DISCLOSE_EXIT`` covers two scripts that promise opposite things. See
    ``TurnPlan.offer_voicemail``.
    """


@dataclass(frozen=True)
class Admission:
    """The webhook's decision about one inbound call."""

    call: ActiveCall | None
    at_capacity: bool = False

    capped: str | None = None
    """Which daily cap refused this call, if one did — see :mod:`.daily_ledger`.

    Distinct from ``at_capacity``, which is the momentary concurrency limit (G-15). Both
    route to voicemail; only this one means the day is over.
    """

    @property
    def admitted(self) -> bool:
        return self.call is not None


@dataclass
class CallRegistry:
    """In-process view of live calls.

    Args:
        max_concurrent: G-15. Read from one place in config so the webhook, the
            guardrails, and the overflow path cannot disagree.
        stale_after_seconds: Backstop for a call whose status callback never arrives.
            Twilio retries them but does not guarantee them, and a leaked slot is
            permanent: after enough of them the line answers nobody.
        clock: Injected for tests. Monotonic in production.
    """

    max_concurrent: int = 5
    stale_after_seconds: float = 5400.0 + 300.0
    clock: Callable[[], float] = time.monotonic

    enabled: bool = True
    """G-20's process-local half. Flipping this to ``False`` sends every new caller to
    voicemail — it never rejects them, because a real person must still be able to
    leave a message when the honeypot is switched off."""

    ledger: DailyLedger | None = None
    """Today's totals — G-15's daily half, alongside the concurrency half above.

    ``None`` disables every daily cap.

    Injected rather than constructed here because this class is deliberately
    persistence-free, and because the tests need a ledger on a temp path with a frozen
    date.
    """

    _calls: dict[str, ActiveCall] = field(default_factory=dict, repr=False)

    # -- capacity -------------------------------------------------------------

    @property
    def active_count(self) -> int:
        self._reap()
        return len(self._calls)

    # -- lifecycle ------------------------------------------------------------

    def reserve(
        self,
        *,
        call_sid: str,
        caller_number: str,
        entry_path: EntryPath,
        persona_id: str,
    ) -> Admission:
        """Claim a slot for a call that is about to be answered.

        Idempotent by ``call_sid``. Twilio retries a webhook whose response it did not
        like, and the retry must not consume a second slot or overwrite a call that is
        already streaming.
        """
        self._reap()

        existing = self._calls.get(call_sid)
        if existing is not None:
            return Admission(call=existing)

        if len(self._calls) >= self.max_concurrent:
            return Admission(call=None, at_capacity=True)

        if self.ledger is not None:
            # Checked after the idempotency lookup above, so a Twilio retry for a call
            # already admitted is never refused by a cap that filled up meanwhile — the
            # leg is up either way, and refusing it now would drop a call mid-flight.
            reason = self.ledger.cap_reason(
                caller_number,
                # Every call still up is charged at the full per-call ceiling. Minutes are
                # only banked at release, so counting finished calls alone let five calls
                # admitted one minute under a 360-minute cap take the day to 810.
                calls_in_flight=len(self._calls),
            )
            if reason is not None:
                return Admission(call=None, capped=reason)

        call = ActiveCall(
            call_sid=call_sid,
            caller_number=caller_number,
            entry_path=entry_path,
            persona_id=persona_id,
            reserved_at=self.clock(),
        )
        self._calls[call_sid] = call
        if self.ledger is not None:
            self.ledger.note_admission(call_sid, caller_number)
        return Admission(call=call)

    def get(self, call_sid: str) -> ActiveCall | None:
        return self._calls.get(call_sid)

    def attach_stream(self, call_sid: str) -> ActiveCall | None:
        """Mark the media socket as connected.

        Returns ``None`` for a call this process never admitted, which is the check
        that stops an unknown socket from being served: the path token proves the
        caller knows a secret, this proves Twilio is calling us about a call we
        actually answered.
        """
        call = self._calls.get(call_sid)
        if call is None:
            return None
        if call.stream_ever_attached:
            # A second socket for one call means either a Twilio retry or someone
            # replaying a start message. One media stream per call, ever — including
            # after the first one has closed.
            return None
        updated = replace(
            call,
            stream_attached=True,
            stream_ever_attached=True,
            status=CallStatus.IN_PROGRESS,
        )
        self._calls[call_sid] = updated
        return updated

    def record_recording_sid(self, call_sid: str, recording_sid: str) -> None:
        call = self._calls.get(call_sid)
        if call is not None:
            self._calls[call_sid] = replace(call, recording_sid=recording_sid)

    def finish(
        self,
        call_sid: str,
        *,
        final_phase: CallPhase | None = None,
        end_reason: EndReason | None = None,
        voicemail_promised: bool | None = None,
    ) -> ActiveCall | None:
        """Record how the conversation ended, without releasing the slot.

        The slot stays held until Twilio confirms the call is over: between the stream
        closing and the status callback, the leg is still up and still costing money,
        and the after-stream document may yet start a voicemail recording on it.
        """
        call = self._calls.get(call_sid)
        if call is None:
            return None
        updated = replace(
            call,
            final_phase=final_phase if final_phase is not None else call.final_phase,
            end_reason=end_reason if end_reason is not None else call.end_reason,
            voicemail_promised=(
                voicemail_promised if voicemail_promised is not None else call.voicemail_promised
            ),
            status=CallStatus.COMPLETED,
            stream_attached=False,
        )
        self._calls[call_sid] = updated
        return updated

    def release(self, call_sid: str) -> ActiveCall | None:
        """Free the slot, and bank what the call cost against today.

        Safe to call for a call that was never reserved. Recording here rather than in
        ``finish`` because the slot is held past the end of the conversation for the
        voicemail, and the leg is billable for all of it.
        """
        return self._retire(call_sid)

    def _retire(self, call_sid: str) -> ActiveCall | None:
        """Drop a call and bank its duration. The only way a call leaves ``_calls``.

        ``record_duration`` is idempotent by ``call_sid``, so a reap followed by a late
        status callback banks the time once.
        """
        call = self._calls.pop(call_sid, None)
        if call is not None and self.ledger is not None:
            self.ledger.record_duration(call_sid, self.clock() - call.reserved_at)
        return call

    def _reap(self) -> None:
        """Drop calls old enough that they cannot still be live.

        Deliberately generous: reaping a real call early costs a leaked recording and a
        confused after-stream decision; reaping late costs a slot for a few minutes.
        """
        cutoff = self.clock() - self.stale_after_seconds
        stale = [sid for sid, call in self._calls.items() if call.reserved_at < cutoff]
        for sid in stale:
            # Through the same accounting as `release`, not a bare `del`. A reaped call is
            # one whose status callback never arrived, and it was billable for its whole
            # life — dropping it here made precisely those calls invisible to the daily
            # caps, so a run of callback failures blinded the caps permanently.
            self._retire(sid)

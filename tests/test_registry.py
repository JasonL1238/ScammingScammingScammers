"""Live-call accounting.

Every test here is about a way the line could stop answering, or answer something it
should not. A leaked slot is permanent and silent: nothing breaks, calls just quietly
stop being taken.
"""

from __future__ import annotations

from helpers import SimulatedClock, reserve_call

from ssscammers.agent.registry import CallRegistry
from ssscammers.shared.enums import CallPhase, CallStatus, EndReason


def make_registry(max_concurrent: int = 2) -> tuple[CallRegistry, SimulatedClock]:
    clock = SimulatedClock()
    return CallRegistry(max_concurrent=max_concurrent, clock=clock), clock


class TestCapacity:
    def test_calls_are_admitted_up_to_the_cap(self) -> None:
        registry, _ = make_registry(max_concurrent=2)
        assert reserve_call(registry, "CA1").admitted
        assert reserve_call(registry, "CA2").admitted

    def test_the_call_past_the_cap_is_not_admitted(self) -> None:
        registry, _ = make_registry(max_concurrent=2)
        reserve_call(registry, "CA1")
        reserve_call(registry, "CA2")

        admission = reserve_call(registry, "CA3")
        assert not admission.admitted
        assert admission.at_capacity

    def test_releasing_a_call_frees_its_slot(self) -> None:
        registry, _ = make_registry(max_concurrent=1)
        reserve_call(registry, "CA1")
        assert not reserve_call(registry, "CA2").admitted

        registry.release("CA1")
        assert reserve_call(registry, "CA2").admitted

    def test_releasing_an_unknown_call_is_harmless(self) -> None:
        # Twilio sends status callbacks for calls this process never handled — a call
        # taken by the previous deploy, for instance.
        registry, _ = make_registry()
        assert registry.release("never-seen") is None


class TestRetriesDoNotConsumeSlots:
    """Twilio retries a webhook whose response it did not like."""

    def test_reserving_the_same_call_twice_uses_one_slot(self) -> None:
        registry, _ = make_registry(max_concurrent=1)
        first = reserve_call(registry, "CA1")
        second = reserve_call(registry, "CA1")

        assert second.admitted
        assert registry.active_count == 1
        assert second.call == first.call

    def test_a_retry_does_not_reset_a_streaming_call(self) -> None:
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        registry.attach_stream("CA1")

        again = reserve_call(registry, "CA1")
        assert again.call is not None
        assert again.call.stream_attached
        assert again.call.status is CallStatus.IN_PROGRESS


class TestOnlyAdmittedCallsGetMedia:
    def test_an_unknown_call_cannot_attach_a_stream(self) -> None:
        # The path token proves the peer knows a secret; this proves the call is real.
        registry, _ = make_registry()
        assert registry.attach_stream("CA-unknown") is None

    def test_a_second_stream_for_one_call_is_refused(self) -> None:
        # One media stream per call. A replayed start message is not a second call.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        assert registry.attach_stream("CA1") is not None
        assert registry.attach_stream("CA1") is None


class TestHowTheCallEnded:
    def test_finishing_records_the_outcome_without_freeing_the_slot(self) -> None:
        # Between the stream closing and Twilio confirming the hangup, the leg is still
        # up, still billing, and may still record a voicemail.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")

        finished = registry.finish(
            "CA1", final_phase=CallPhase.STALL, end_reason=EndReason.MAX_DURATION
        )
        assert finished is not None
        assert finished.final_phase is CallPhase.STALL
        assert registry.active_count == 1

    def test_a_caller_who_was_promised_a_voicemail_is_owed_one(self) -> None:
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        call = registry.finish(
            "CA1", final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=True
        )
        assert call is not None
        assert call.voicemail_promised

    def test_the_promise_is_reported_not_inferred_from_the_phase(self) -> None:
        # DISCLOSE_EXIT covers two scripts. The disclosure promises a voicemail; the
        # victim warning tells the caller to hang up and ring their bank. Inferring from
        # the phase offered a recorded-message beep to exactly the wrong caller.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        call = registry.finish(
            "CA1", final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=False
        )
        assert call is not None
        assert not call.voicemail_promised

    def test_no_ending_defaults_to_no_voicemail(self) -> None:
        # Fail closed: an ending nobody reported a promise for gets a hangup.
        registry, _ = make_registry(max_concurrent=4)
        for index, phase in enumerate(
            [CallPhase.TERMINATE, CallPhase.EMERGENCY_EXIT, CallPhase.STALL, CallPhase.DISCLOSE_EXIT]
        ):
            sid = f"CA{index}"
            reserve_call(registry, sid)
            call = registry.finish(sid, final_phase=phase)
            assert call is not None
            assert not call.voicemail_promised, phase

    def test_finishing_an_unknown_call_is_harmless(self) -> None:
        registry, _ = make_registry()
        assert registry.finish("nope", final_phase=CallPhase.TERMINATE) is None

    def test_finishing_twice_keeps_the_first_promise(self) -> None:
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        registry.finish("CA1", final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=True)
        again = registry.finish("CA1")
        assert again is not None
        assert again.voicemail_promised

    def test_finishing_twice_keeps_the_first_reason(self) -> None:
        # The pipeline reports why it stopped; a later call with no reason must not
        # erase it.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        registry.finish("CA1", final_phase=CallPhase.TERMINATE, end_reason=EndReason.DEAD_AIR)
        again = registry.finish("CA1")
        assert again is not None
        assert again.end_reason is EndReason.DEAD_AIR
        assert again.final_phase is CallPhase.TERMINATE


class TestStaleCallsCannotWedgeTheLine:
    def test_a_call_older_than_the_cap_stops_counting(self) -> None:
        # If a status callback never arrives, the slot must not be held forever: enough
        # of those and the line silently stops answering.
        registry, clock = make_registry(max_concurrent=1)
        registry.stale_after_seconds = 100.0
        reserve_call(registry, "CA1")
        assert not reserve_call(registry, "CA2").admitted

        clock.advance(101.0)
        assert reserve_call(registry, "CA2").admitted

    def test_a_call_within_the_window_is_left_alone(self) -> None:
        registry, clock = make_registry(max_concurrent=1)
        registry.stale_after_seconds = 100.0
        reserve_call(registry, "CA1")

        clock.advance(99.0)
        assert registry.active_count == 1
        assert not reserve_call(registry, "CA2").admitted


class TestKillSwitch:
    def test_the_registry_starts_enabled(self) -> None:
        registry, _ = make_registry()
        assert registry.enabled

    def test_disabling_does_not_touch_calls_in_flight(self) -> None:
        # G-20 stops new baiting. A conversation already under way is a separate
        # decision, made by whoever flips the switch.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        registry.enabled = False
        assert registry.active_count == 1
        assert registry.get("CA1") is not None


class TestRecordingSid:
    def test_the_recording_sid_is_kept_against_the_call(self) -> None:
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        registry.record_recording_sid("CA1", "RE123")
        call = registry.get("CA1")
        assert call is not None
        assert call.recording_sid == "RE123"

    def test_a_recording_for_an_unknown_call_is_ignored(self) -> None:
        registry, _ = make_registry()
        registry.record_recording_sid("CA-unknown", "RE123")  # must not raise


class TestOneStreamPerCallEver:
    def test_a_second_stream_is_refused_after_the_first_one_closed(self) -> None:
        # `finish` clears `stream_attached` while the slot is still held for the
        # voicemail. Without a separate ever-attached flag, that window lets anyone with
        # the path token open a fresh conversation on our bill and overwrite the outcome
        # `/twilio/after-stream` reads.
        registry, _ = make_registry()
        reserve_call(registry, "CA1")
        assert registry.attach_stream("CA1") is not None

        registry.finish("CA1", final_phase=CallPhase.DISCLOSE_EXIT, voicemail_promised=True)
        assert registry.attach_stream("CA1") is None

        call = registry.get("CA1")
        assert call is not None
        assert call.voicemail_promised, "the real outcome must survive the refused socket"

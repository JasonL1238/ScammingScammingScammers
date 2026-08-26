"""Golden calls: the byte-identical replay gate.

Each manifest is re-driven through the production conversation driver and the
result — events, transcript, and the steering the model was asked under — is
compared to the recorded one, field for field. A diff here is not a broken
test; it is the system behaving differently than it did, and the question is
always which of the two is right.

When the change is intended: `python scripts/regenerate_goldens.py`, then read
the diff before committing it. Regenerating to make a test pass is how a
regression becomes the baseline, which is why the script prints the diff.

The corpus is small, so what it covers is deliberate rather than incidental,
and `TestTheCorpusCoversWhatItClaimsTo` is the record of that: each test there
names a behaviour some golden exists to pin. A corpus that only pinned the easy
path would stay green through exactly the failures these were written for.
"""

from __future__ import annotations

import json

import pytest

from ssscammers.shared.enums import CallPhase, EndReason, EntryPath
from ssscammers.simscammer.golden import (
    GOLDEN_DIR,
    GOLDEN_MANIFESTS,
    GoldenManifest,
    diff_events,
    events_to_json,
    load_golden,
    manifest_by_name,
    replay_call,
)

MANIFESTS = pytest.mark.parametrize("manifest", GOLDEN_MANIFESTS, ids=lambda m: m.name)


def reserialize(golden: dict) -> str:
    """Re-dump a parsed golden exactly as `events_to_json` would.

    Used by the gate's own red-proofs. Hand-rolling a second serializer here
    once meant those tests diffed a formatting difference instead of the
    mutation they had introduced, and passed with the mutation removed.
    """
    return json.dumps(golden, indent=2, ensure_ascii=False)


@MANIFESTS
async def test_the_call_replays_byte_identically(manifest: GoldenManifest) -> None:
    assert manifest.path.exists(), (
        f"{manifest.name} has no golden; run scripts/regenerate_goldens.py"
    )
    diff = diff_events(load_golden(manifest), events_to_json(await replay_call(manifest)))
    assert diff is None, f"{manifest.name} no longer replays to its golden:\n{diff}"


@MANIFESTS
async def test_replaying_twice_gives_the_same_result(manifest: GoldenManifest) -> None:
    # Determinism independent of the stored file: catches a golden that was
    # regenerated from a run which itself was not reproducible.
    first, second = await replay_call(manifest), await replay_call(manifest)
    assert events_to_json(first) == events_to_json(second)


@MANIFESTS
def test_the_golden_is_a_well_formed_dense_stream(manifest: GoldenManifest) -> None:
    golden = json.loads(load_golden(manifest))
    events = golden["events"]
    assert events, f"{manifest.name} is empty"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "call_opened"
    assert all(e["call_sid"] == f"CA{manifest.name}" for e in events)
    assert golden["transcript"], "a call with no transcript pins nothing about speech"


class TestTheCorpusCoversWhatItClaimsTo:
    def golden(self, name: str) -> dict:
        return json.loads(load_golden(manifest_by_name(name)))

    def events(self, name: str) -> list[dict]:
        return self.golden(name)["events"]

    def test_a_real_person_is_released_through_the_disclosure(self) -> None:
        events = self.events("misroute_pharmacy_released")
        ended = [e for e in events if e["type"] == "call_ended"][-1]
        assert ended["payload"]["phase"] == CallPhase.DISCLOSE_EXIT.value
        assert not any(e["type"] == "agent_turn" and not e["payload"]["scripted"] for e in events)

    def test_an_emergency_takes_the_redirect_not_the_disclosure(self) -> None:
        ended = [e for e in self.events("misroute_emergency_redirect") if e["type"] == "call_ended"]
        assert ended[-1]["payload"]["phase"] == CallPhase.EMERGENCY_EXIT.value

    def test_a_card_split_across_sentences_is_still_blocked(self) -> None:
        # The G-4 evasion: neither sentence carries a blockable run, and only
        # the cumulative check catches the pair. A per-sentence filter would
        # let this through, which is the regression this golden exists for.
        golden = self.golden("scam_bank_otp_baited")
        blocked = [e for e in golden["events"] if e["type"] == "output_blocked"]
        assert blocked, "the split card must trip the filter"
        assert blocked[0]["payload"]["violations"] == ["valid_card"]

        spoken = " ".join(
            e["payload"]["text"] for e in golden["events"] if e["type"] == "agent_turn"
        )
        assert "0343 6467" not in spoken

    def test_a_truncated_turn_keeps_both_labels(self) -> None:
        turns = [
            e["payload"]
            for e in self.events("scam_bank_otp_baited")
            if e["type"] == "agent_turn" and not e["payload"]["scripted"]
        ]
        assert turns[-1]["failure"] == "truncated"
        assert turns[-1]["stop_reason"] == "max_tokens"

    def test_a_call_walks_the_state_machine_from_the_greeting(self) -> None:
        # Without this, a change to probation or the triage commit bar leaves
        # every other golden byte-identical: the rest start mid-call.
        changes = [
            e["payload"] for e in self.events("scam_walks_from_greeting_into_baiting")
            if e["type"] == "phase_changed"
        ]
        assert [c["from"] for c in changes][0] == CallPhase.GREETING.value
        assert CallPhase.STALL.value in [c["to"] for c in changes]

    def test_an_empty_model_reply_becomes_a_fumble_not_silence(self) -> None:
        # Silence reads as a dropped call and ends the bait; the fumble is a
        # drawn value, so this also pins that draw.
        turns = [
            e["payload"]
            for e in self.events("scam_irs_model_says_nothing")
            if e["type"] == "agent_turn" and not e["payload"]["scripted"]
        ]
        fumbled = [t for t in turns if t["fumbled"]]
        assert fumbled, "an empty reply must be covered"
        assert fumbled[0]["text"].strip()

    def test_the_timer_ends_a_genuinely_silent_line(self) -> None:
        events = self.events("timer_dead_air_hangup")
        ended = [e for e in events if e["type"] == "call_ended"][-1]
        assert ended["payload"]["reason"] == EndReason.DEAD_AIR.value

        change = [e for e in events if e["type"] == "phase_changed"][-1]
        assert change["payload"]["on_timer"] is True
        assert change["payload"]["silence_seconds"] >= 60.0

    def test_the_hangup_lands_after_the_hold_not_during_it(self) -> None:
        # G-16 as cadence: the timer runs *through* the persona's hold, and the
        # hold is our audio — so the dead-air window starts when the hold ends,
        # not when it began. If it started at the beginning, the call would end
        # before the hold event was ever emitted.
        events = self.events("timer_dead_air_hangup")
        order = [e["type"] for e in events]
        assert order.index("hold") < order.index("phase_changed")

        hold = [e for e in events if e["type"] == "hold"][0]
        ended = [e for e in events if e["type"] == "call_ended"][0]
        assert ended["at_seconds"] >= hold["at_seconds"] + hold["payload"]["seconds"]

    def test_the_steering_the_model_was_asked_under_is_pinned(self) -> None:
        # It appears in no event payload, so without this the state notes could
        # be rewritten wholesale and every golden would stay green.
        steering = self.golden("scam_bank_otp_baited")["steering"]
        assert steering and all(note for note in steering)
        assert any("[call state]" in note for note in steering)

    def test_the_transcript_pins_the_greeting_and_its_provenance(self) -> None:
        transcript = self.golden("misroute_pharmacy_released")["transcript"]
        assert transcript[0]["role"] == "assistant"
        assert transcript[0]["scripted"] is True
        assert transcript[0]["content"], "the persona must not answer with silence"

    def test_the_corpus_exercises_both_entry_paths_and_more_than_one_persona(self) -> None:
        assert {m.entry_path for m in GOLDEN_MANIFESTS} == set(EntryPath) - {EntryPath.UNKNOWN}
        assert len({m.persona_id for m in GOLDEN_MANIFESTS}) > 1

    def test_the_corpus_exercises_the_timer_the_model_and_neither_only(self) -> None:
        assert any(m.recording.turns for m in GOLDEN_MANIFESTS)
        assert any(not m.recording.turns for m in GOLDEN_MANIFESTS)
        assert any(m.idle_seconds for m in GOLDEN_MANIFESTS)
        assert any(m.start_phase is None for m in GOLDEN_MANIFESTS)

    def test_every_manifest_has_a_distinct_seed_and_name(self) -> None:
        names = [m.name for m in GOLDEN_MANIFESTS]
        assert len(set(names)) == len(names)
        assert len({m.seed for m in GOLDEN_MANIFESTS}) == len(GOLDEN_MANIFESTS)

    def test_every_recording_pins_its_fiction_pack_as_a_literal(self) -> None:
        # Inheriting the default would compare the live value to itself, which
        # is how the pack guard came to be vacuous.
        for manifest in GOLDEN_MANIFESTS:
            if manifest.recording.turns:
                assert manifest.recording.pack_version == "v1", manifest.name


class TestTheGateActuallyBites:
    """The gate's own red-proof, in-process."""

    NAME = "misroute_pharmacy_released"

    async def actual(self) -> dict:
        return json.loads(events_to_json(await replay_call(manifest_by_name(self.NAME))))

    async def test_an_unmutated_replay_produces_no_diff(self) -> None:
        # The control. Without it, a serializer mismatch in `reserialize` would
        # make every mutation below "pass" for the wrong reason.
        golden = load_golden(manifest_by_name(self.NAME))
        assert diff_events(golden, reserialize(await self.actual())) is None

    async def test_a_changed_payload_is_detected(self) -> None:
        actual = await self.actual()
        actual["events"][0]["payload"]["persona"] = "someone-else"
        assert diff_events(load_golden(manifest_by_name(self.NAME)), reserialize(actual))

    async def test_a_dropped_event_is_detected(self) -> None:
        actual = await self.actual()
        actual["events"] = actual["events"][:-1]
        assert diff_events(load_golden(manifest_by_name(self.NAME)), reserialize(actual))

    async def test_a_changed_transcript_is_detected(self) -> None:
        actual = await self.actual()
        actual["transcript"][0]["content"] = "Yeah what"
        assert diff_events(load_golden(manifest_by_name(self.NAME)), reserialize(actual))

    async def test_changed_steering_is_detected(self) -> None:
        name = "scam_bank_otp_baited"
        actual = json.loads(events_to_json(await replay_call(manifest_by_name(name))))
        actual["steering"][0] = "MUTATED"
        assert diff_events(load_golden(manifest_by_name(name)), reserialize(actual))

    def test_identical_streams_produce_no_diff(self) -> None:
        golden = load_golden(manifest_by_name(self.NAME))
        assert diff_events(golden, golden) is None

    async def test_an_unconsumed_recording_fails_the_replay(self) -> None:
        # A call that took a different path leaves recorded turns unspoken, and
        # that must be an error rather than a shorter-but-green stream.
        import dataclasses

        from ssscammers.simscammer.replay import RecordedTurn

        manifest = manifest_by_name("scam_bank_otp_baited")
        padded = dataclasses.replace(
            manifest,
            recording=dataclasses.replace(
                manifest.recording,
                turns=(*manifest.recording.turns, RecordedTurn(("never spoken.",))),
            ),
        )
        with pytest.raises(AssertionError, match="did not consume its recording"):
            await replay_call(padded)

    async def test_a_regenerated_fiction_pack_refuses_the_replay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The clause the roadmap names: golden transcripts break if the pack
        # regenerates. It only bites because the manifests pin a literal.
        from ssscammers.simscammer import replay as replay_module

        monkeypatch.setattr(replay_module, "PACK_VERSION", "v2")
        with pytest.raises(replay_module.DivergedError, match="fiction pack"):
            await replay_call(manifest_by_name("scam_bank_otp_baited"))


def test_no_golden_file_is_orphaned() -> None:
    # A manifest renamed without regenerating leaves the old stream behind,
    # where it looks like coverage and gates nothing.
    assert {p.stem for p in GOLDEN_DIR.glob("*.json")} == {m.name for m in GOLDEN_MANIFESTS}


def test_the_regeneration_script_refuses_an_unknown_golden() -> None:
    # The one branch exercisable without rewriting the corpus: a typo must be
    # refused loudly rather than regenerating nothing and reporting success.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "regenerate_goldens.py"
    spec = importlib.util.spec_from_file_location("regenerate_goldens", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    assert module.main(["no_such_golden"]) == 2

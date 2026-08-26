"""Persona bundles and the prompt they compose.

The prompt is cached for the life of a call, so the property worth testing is that it
is *stable* — assembling it twice gives byte-identical text — and that it actually
contains the guardrails, since a persona whose prompt silently lost the standing rules
would still look fine in conversation.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from ssscammers.agent.media import IMPLEMENTED_TTS, looks_unconfigured, unservable_reason
from ssscammers.agent.persona import Persona, available_personas, load_persona
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import Tactic
from ssscammers.shared.output_filter import OutputFilter

SHIPPED = ("marjorie", "harold", "dot")


def test_all_shipped_personas_are_discoverable() -> None:
    assert set(SHIPPED).issubset(set(available_personas()))


@pytest.fixture(params=SHIPPED)
def persona(request: pytest.FixtureRequest) -> Persona:
    return load_persona(request.param)


class TestPromptComposition:
    def test_prompt_is_byte_stable(self, persona: Persona) -> None:
        # Anything that varies between calls invalidates the cache breakpoint in
        # front of it and re-bills the whole conversation.
        assert persona.system_prompt() == persona.system_prompt()

    def test_prompt_carries_the_standing_rules(self, persona: Persona) -> None:
        prompt = persona.system_prompt()
        assert "You never say anything that is real" in prompt
        assert "never claim to work for or represent a real company" in prompt
        assert "You are never cruel" in prompt

    def test_prompt_carries_the_stalling_playbook(self, persona: Persona) -> None:
        assert "Time on the line is the only score" in persona.system_prompt()

    def test_prompt_carries_the_scam_guide(self, persona: Persona) -> None:
        assert "Reading the script they're running" in persona.system_prompt()

    def test_prompt_carries_the_characters_own_voice(self, persona: Persona) -> None:
        assert persona.display_name in persona.system_prompt()

    def test_prompt_carries_the_fact_sheet(self, persona: Persona) -> None:
        prompt = persona.system_prompt()
        assert persona.identity.full_name in prompt
        assert persona.identity.card_number in prompt
        # And the instruction that the fact sheet is *all* the character knows.
        assert "These are the only personal details you know" in prompt

    def test_shared_sections_are_identical_across_personas(self) -> None:
        # The whole point of putting them first: one cache entry serves every call.
        prompts = [load_persona(p).system_prompt() for p in SHIPPED]
        shared_prefix = prompts[0].split("---", 1)[0]
        assert all(p.startswith(shared_prefix) for p in prompts)

    def test_the_persona_can_speak_its_own_prompt_without_being_blocked(
        self, persona: Persona
    ) -> None:
        # Not hypothetical: the fact sheet is full of card and SSN digits, and an
        # over-eager filter would gag the character it is supposed to protect.
        filt = OutputFilter.for_identity(persona.identity, rng=random.Random(0))
        assert filt.check(persona.identity.to_prompt_block()).allowed


class TestTacticSelection:
    def test_weights_are_respected(self) -> None:
        # Dot fumbles data far more than she wanders; over many draws that shows.
        dot = load_persona("dot")
        rng = random.Random(0)
        draws = [dot.choose_tactic(rng) for _ in range(400)]
        assert draws.count(Tactic.FUMBLE_DATA) > draws.count(Tactic.TANGENT)

    def test_harold_prefers_tangents(self) -> None:
        harold = load_persona("harold")
        rng = random.Random(0)
        draws = [harold.choose_tactic(rng) for _ in range(400)]
        assert draws.count(Tactic.TANGENT) > draws.count(Tactic.FUMBLE_DATA)

    def test_exclusion_stops_the_same_move_twice_running(self, persona: Persona) -> None:
        rng = random.Random(1)
        for _ in range(50):
            assert persona.choose_tactic(rng, exclude={Tactic.FUMBLE_DATA}) is not Tactic.FUMBLE_DATA

    def test_excluding_everything_still_returns_something(self, persona: Persona) -> None:
        rng = random.Random(1)
        assert persona.choose_tactic(rng, exclude=set(Tactic)) is not None


class TestPacing:
    def test_delay_is_bounded_even_on_extreme_draws(self, persona: Persona) -> None:
        rng = random.Random(0)
        delays = [persona.pacing.sample_delay_ms(rng) for _ in range(500)]
        # Never negative, and never long enough that a scammer thinks the line died.
        assert min(delays) >= 0
        assert max(delays) <= 4000

    def test_hold_length_stays_in_the_configured_band(self, persona: Persona) -> None:
        rng = random.Random(0)
        holds = [persona.pacing.sample_hold_seconds(rng) for _ in range(200)]
        assert min(holds) >= persona.pacing.hold_seconds_min
        assert max(holds) <= persona.pacing.hold_seconds_max


class TestTheVoiceProviderAPipelineCanActuallyServe:
    """A bundle names its speech provider; only the pipeline can honour it.

    `media._serve_call` constructs the TTS service itself, so a bundle naming a provider
    the pipeline does not implement would otherwise be handed to the wrong one — Cartesia
    receiving an ElevenLabs voice id gives a call with no working voice, which looks
    identical to a bad voice id.
    """

    def test_the_fallback_default_persona_can_be_voiced(self) -> None:
        # `Settings.default_persona`'s own default, not a literal: whatever a deployment
        # falls back to with DEFAULT_PERSONA unset must be servable, or the line answers
        # and then cannot speak. A *configured* value is checked at boot by
        # `webhooks.create_app`, which is where an operator's typo has to be caught.
        assert unservable_reason(load_persona(Settings().default_persona).voice) is None

    def test_every_shipped_persona_can_be_voiced(self) -> None:
        # This used to allow one known-broken bundle (`dot`, written for ElevenLabs while
        # the pipeline implements Cartesia only). That gap is closed, so the assertion is
        # now the plain invariant: a bundle nobody can voice has no business shipping.
        # Adding a persona for an unimplemented provider must fail here, not at 3am on a
        # call that answers, records, plays the notice, and then hangs up mute.
        unservable = {
            name: load_persona(name).voice.tts
            for name in available_personas()
            if load_persona(name).voice.tts not in IMPLEMENTED_TTS
        }
        assert unservable == {}, f"unservable bundles shipped: {unservable}"

    def test_no_shipped_persona_still_has_a_placeholder_voice(self) -> None:
        # All three shipped with `PLACEHOLDER_*` ids for a while. A placeholder passes the
        # provider check above and fails at the synthesiser instead, which sounds exactly
        # like a bad-but-real id.
        placeholders = {
            name for name in available_personas()
            if looks_unconfigured(load_persona(name).voice)
        }
        assert placeholders == set(), f"placeholder voice ids: {sorted(placeholders)}"


def write_bundle(root: Path, persona_id: str, config: dict) -> None:
    """Write a minimal persona bundle into a throwaway persona directory."""
    directory = root / persona_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "core.md").write_text("You are a test persona.\n", encoding="utf-8")
    (directory / "persona.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


class TestABadPersonaBundleIsRefusedAtLoad:
    """A bundle mistake must stop at load, not surface as a call that sounds wrong.

    Two halves, both previously silent. **Keys:** the loader filtered `pacing` through
    `Pacing.__dataclass_fields__` and read the other blocks with `.get()`, so a typo — or a
    knob removed from the dataclass — was accepted and ignored. `tactic:` for `tactics:` was
    the worst of them: an empty weight table makes `choose_tactic` return `Tactic.NONE` every
    turn, so the entire stalling playbook is off for the life of the call. **Values:** a
    quoted or empty number passed straight through and raised `TypeError` inside
    `PersonaDirector._plan` on the first turn that considered a hold — mid-call.
    """

    @pytest.fixture
    def bundles(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr("ssscammers.agent.persona.PERSONA_DIR", tmp_path)
        return tmp_path

    def test_a_misspelt_tactics_block_is_refused(self, bundles: Path) -> None:
        # The worst case of the class. `tactic:` loaded clean and left `tactic_weights`
        # empty, so `choose_tactic` returned `Tactic.NONE` on every turn — the entire
        # stalling playbook silently off for the life of the call.
        write_bundle(bundles, "t", {"id": "t", "tactic": {"mishear": 0.5}})
        with pytest.raises(ValueError, match="unknown top-level key"):
            load_persona("t")

    def test_a_misspelt_sound_pack_block_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "sp", {"id": "sp", "sound_packs": {"fillers": ["a.wav"]}})
        with pytest.raises(ValueError, match="unknown top-level key"):
            load_persona("sp")

    def test_a_block_that_is_not_a_mapping_is_refused_clearly(self, bundles: Path) -> None:
        # `voice: cartesia` used to report every letter of the string as an unknown key.
        write_bundle(bundles, "scalar", {"id": "scalar", "voice": "cartesia"})
        with pytest.raises(ValueError, match="must be a mapping"):
            load_persona("scalar")

    def test_an_unknown_pacing_key_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "typo", {"id": "typo", "pacing": {"reply_delay_ms": 900}})
        with pytest.raises(ValueError, match="unknown pacing key"):
            load_persona("typo")

    def test_the_error_names_the_keys_that_would_have_worked(self, bundles: Path) -> None:
        write_bundle(bundles, "typo", {"id": "typo", "pacing": {"reply_delay_ms": 900}})
        with pytest.raises(ValueError, match="reply_delay_ms_mean"):
            load_persona("typo")

    def test_an_unknown_voice_key_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "v", {"id": "v", "voice": {"tts": "cartesia", "rate": "slow"}})
        with pytest.raises(ValueError, match="unknown voice key"):
            load_persona("v")

    def test_an_unknown_sound_pack_key_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "s", {"id": "s", "sound_pack": {"filler": ["a.wav"]}})
        with pytest.raises(ValueError, match="unknown sound_pack key"):
            load_persona("s")

    def test_a_blank_sound_pack_entry_is_refused(self, bundles: Path) -> None:
        # An empty filename loads as a clip the rng can pick but nothing can play:
        # the hold event would record a clip the caller never heard.
        write_bundle(bundles, "b", {"id": "b", "sound_pack": {"holds": [""]}})
        with pytest.raises(ValueError, match="non-empty filenames"):
            load_persona("b")

    def test_a_non_string_sound_pack_entry_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "n", {"id": "n", "sound_pack": {"fillers": [3]}})
        with pytest.raises(ValueError, match="non-empty filenames"):
            load_persona("n")

    def test_a_blank_ambient_is_refused(self, bundles: Path) -> None:
        write_bundle(bundles, "a", {"id": "a", "sound_pack": {"ambient": " "}})
        with pytest.raises(ValueError, match="ambient must be a non-empty filename"):
            load_persona("a")

    def test_a_pacing_value_that_is_not_a_number_is_refused(self, bundles: Path) -> None:
        # `hold_probability: "0.15"` (quoted) used to load and then raise TypeError inside
        # `PersonaDirector._plan` on the first turn that considered a hold — a config typo
        # crashing a live call rather than a refused bundle.
        write_bundle(bundles, "q", {"id": "q", "pacing": {"hold_probability": "not a number"}})
        with pytest.raises(ValueError, match="pacing.hold_probability must be a float"):
            load_persona("q")

    def test_an_empty_pacing_value_is_refused(self, bundles: Path) -> None:
        # `hold_probability:` with nothing after it is None in YAML.
        write_bundle(bundles, "n", {"id": "n", "pacing": {"hold_probability": None}})
        with pytest.raises(ValueError, match="pacing.hold_probability"):
            load_persona("n")

    def test_a_quoted_number_is_accepted_and_coerced(self, bundles: Path) -> None:
        # Refusing a *typo* must not mean refusing a value YAML merely quoted.
        write_bundle(bundles, "c", {"id": "c", "pacing": {"hold_seconds_min": "20"}})
        assert load_persona("c").pacing.hold_seconds_min == 20

    def test_a_voice_id_that_is_not_text_is_refused(self, bundles: Path) -> None:
        # Stringifying it would hand the synthesiser "['a', 'b']" as a voice id.
        write_bundle(bundles, "l", {"id": "l", "voice": {"voice_id": ["a", "b"]}})
        with pytest.raises(ValueError, match="voice.voice_id must be text"):
            load_persona("l")

    def test_the_knobs_that_are_not_wired_yet_still_load(self, bundles: Path) -> None:
        # `speed` and `ignore_interruption_probability` are documented as not-yet-applied.
        # Not-yet-applied must still mean accepted, or the bundles that set them break.
        write_bundle(
            bundles,
            "knobs",
            {
                "id": "knobs",
                "voice": {"tts": "cartesia", "speed": "slow"},
                "pacing": {"ignore_interruption_probability": 0.3},
            },
        )
        persona = load_persona("knobs")
        assert persona.voice.speed == "slow"
        assert persona.pacing.ignore_interruption_probability == 0.3


class TestBundleValidation:
    def test_unknown_persona_names_the_alternatives(self) -> None:
        with pytest.raises(FileNotFoundError, match="available:"):
            load_persona("nobody")

    def test_each_persona_has_a_distinct_identity(self) -> None:
        ssns = {load_persona(p).identity.ssn for p in SHIPPED}
        assert len(ssns) == len(SHIPPED)


class TestEveryPromptStaysAboveTheCacheFloor:
    """Prompt caching turns off silently below the model's minimum cacheable prefix.

    The system prompt is the one cached block, and it is re-sent on every turn of every
    call. Below Sonnet 5's 1024-token floor the API caches nothing, reports
    ``cache_creation_input_tokens: 0``, and raises no error — so a persona trimmed too
    far raises the cost of every call with no failing test and no log line.

    Asserted in characters rather than tokens so the check needs no API call. Measured on
    the shipped bundles: ~14,000 characters to 4,526 tokens, i.e. ~3.1 chars/token. Six
    chars/token is a conservative worst case for prose, so 6144 characters guarantees the
    floor is cleared with roughly 2x margin left.
    """

    MIN_CHARS = 6144

    @pytest.mark.parametrize("persona_id", SHIPPED)
    def test_the_prompt_is_long_enough_to_cache(self, persona_id: str) -> None:
        prompt = load_persona(persona_id).system_prompt()
        assert len(prompt) >= self.MIN_CHARS, (
            f"{persona_id}'s prompt is {len(prompt)} chars, under the {self.MIN_CHARS} "
            "that guarantees Sonnet 5's 1024-token cache minimum — caching would "
            "silently stop and every turn would pay full price for the whole prompt"
        )

"""Loading a persona and composing the prompt that never changes during a call.

The system prompt is assembled once per call from five pieces and then left strictly
alone, because it sits in front of a prompt-cache breakpoint: editing a single byte of it
mid-call re-bills the entire conversation. Everything that varies during the call travels
as a system message at the *end* of the message list instead, where changing it is free.

Order matters and is fixed: standing rules, stalling playbook, scam-script guide, this
persona's character, this persona's invented fact sheet. The first three are byte-identical
across every call the system ever makes, so they sit at the front where the cache does the
most good.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ssscammers.shared.enums import Tactic
from ssscammers.shared.fiction import FictionIdentity, load_identity

__all__ = ["Persona", "Pacing", "VoiceConfig", "load_persona", "available_personas"]

_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = _ROOT / "personas"
PLAYBOOK_DIR = _ROOT / "playbooks"

#: Shared prompt sections, in the order they are concatenated. Stable across all
#: personas and all calls, which is exactly what makes them worth caching.
_SHARED_PLAYBOOKS: tuple[str, ...] = ("core_rules.md", "stalling.md", "scam_types.md")


@dataclass(frozen=True)
class VoiceConfig:
    tts: str
    """Which speech provider this bundle is written for.

    Only the providers in :data:`ssscammers.agent.media.IMPLEMENTED_TTS` can actually be
    served. A bundle naming anything else is refused at call setup rather than voiced by
    the wrong provider — handing Cartesia an ElevenLabs voice id produces a call with no
    working voice, which is worse than a clean refusal.
    """

    voice_id: str
    model: str | None = None

    speed: str = "normal"
    """Intended synthesis rate. **Not applied yet.**

    Nothing reads this: :func:`ssscammers.agent.media._serve_call` constructs its TTS
    service with ``api_key``, ``voice_id``, and ``model`` only. Slow delivery is the
    cheapest believability lever the project has and :class:`Pacing` does not provide it —
    that adds silence *between* turns, while this would slow the words themselves. Wire it
    into the TTS service's own speed control; kept here because deleting it deleted the
    only record that slow delivery was intended.
    """


@dataclass(frozen=True)
class Pacing:
    """How slow this character is, on purpose.

    Real latency and character latency are separate: the pipeline hides the former behind a
    filler noise, and these numbers add the latter on top. See ``PersonaDirector``.
    """

    reply_delay_ms_mean: int = 900
    reply_delay_ms_stdev: int = 400
    hold_probability: float = 0.12
    hold_seconds_min: int = 10
    hold_seconds_max: int = 90

    ignore_interruption_probability: float = 0.15
    """Chance of talking over a barge-in for a beat. **Not applied yet.**

    Barge-in itself is disabled (``should_interrupt=False`` in
    :mod:`ssscammers.agent.media`, with the G-11 reason recorded there), so there is no
    interruption to ignore. Kept because it is the persona-level half of that feature and
    ``docs/plan.md`` still lists barge-in as an M2 exit criterion.
    """

    def sample_delay_ms(self, rng: random.Random) -> int:
        """Draw one reply delay. Never negative, never long enough to sound dead."""
        drawn = rng.gauss(self.reply_delay_ms_mean, self.reply_delay_ms_stdev)
        return max(0, min(int(drawn), 4000))

    def sample_hold_seconds(self, rng: random.Random) -> int:
        return rng.randint(self.hold_seconds_min, self.hold_seconds_max)


@dataclass(frozen=True)
class Persona:
    """One playable character: prompt, voice, sound pack, pacing, tactic weights."""

    id: str
    display_name: str
    description: str
    character_prompt: str
    identity: FictionIdentity
    voice: VoiceConfig
    pacing: Pacing
    tactic_weights: dict[Tactic, float]
    fillers: tuple[str, ...] = ()
    holds: tuple[str, ...] = ()
    ambient: str | None = None
    _shared_prompt: str = field(default="", repr=False)

    def system_prompt(self) -> str:
        """The cacheable system prompt. Must be byte-identical for the whole call."""
        return "\n\n---\n\n".join(
            [
                self._shared_prompt,
                self.character_prompt.strip(),
                "# Your details\n\n" + self.identity.to_prompt_block(),
            ]
        )

    def choose_tactic(self, rng: random.Random, *, exclude: set[Tactic] | None = None) -> Tactic:
        """Pick a stalling move according to this persona's weights.

        Args:
            exclude: Tactics to skip — normally the one just used, so the character
                doesn't mishear four turns in a row and stop being believable.
        """
        excluded = exclude or set()
        candidates = [(t, w) for t, w in self.tactic_weights.items() if t not in excluded and w > 0]
        if not candidates:
            candidates = [(t, w) for t, w in self.tactic_weights.items() if w > 0]
        if not candidates:
            return Tactic.NONE
        tactics, weights = zip(*candidates, strict=True)
        return rng.choices(tactics, weights=weights, k=1)[0]


def _read_shared_playbooks() -> str:
    parts: list[str] = []
    for name in _SHARED_PLAYBOOKS:
        path = PLAYBOOK_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"missing shared playbook: {path}")
        parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(parts)


#: Every key a bundle may set at the top level. Checked because ``tactics`` written as
#: ``tactic`` silently yielded an empty weight table, and an empty table makes
#: ``choose_tactic`` return ``Tactic.NONE`` on every turn — the whole stalling playbook off
#: for the life of the call, with nothing to see in a log.
_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "id",
    "display_name",
    "description",
    "voice",
    "pacing",
    "sound_pack",
    "tactics",
)


def _check_keys(raw: dict[str, Any], allowed: Iterable[str], block: str) -> None:
    """Reject keys a bundle sets that nothing will read.

    Silently dropping them is how a persona ends up with default pacing while its YAML
    says otherwise: the loader used to filter ``pacing`` through
    ``Pacing.__dataclass_fields__``, so a typo — or a knob that had been removed — was
    accepted and ignored, with no error and no warning. ``_parse_tactics`` already refused
    unknown tactics; this extends that to the rest of the bundle.
    """
    if not isinstance(raw, dict):
        # A scalar or list where a mapping belongs; `set()` over a string would otherwise
        # report its letters as unknown keys.
        raise ValueError(f"persona bundle {block} must be a mapping, got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(
            f"unknown {block} key(s) in persona bundle: {', '.join(unknown)}; "
            f"expected one of: {', '.join(sorted(allowed))}"
        )


def _parse_pacing(raw: dict[str, Any]) -> Pacing:
    """Build :class:`Pacing`, refusing a value that is not a number.

    Checking the key names is not enough. YAML happily produces a string from
    ``hold_probability: "0.15"`` and ``None`` from ``hold_probability:``, and either
    reaches the persona director untouched — where the first turn that considers a hold
    evaluates ``rng.random() < hold_probability`` and raises ``TypeError`` mid-call. A
    config typo has to fail at load, where it is a refused bundle, not on a live line.
    """
    _check_keys(raw, Pacing.__dataclass_fields__, "pacing")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        # Every Pacing field is numeric, so its default names the type to coerce to.
        want = type(Pacing.__dataclass_fields__[key].default)
        try:
            values[key] = want(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"persona bundle pacing.{key} must be a {want.__name__}, got {value!r}"
            ) from exc
    return Pacing(**values)


def _parse_voice(raw: dict[str, Any]) -> VoiceConfig:
    """Build :class:`VoiceConfig`, refusing a value that is not text.

    ``model`` is the one nullable field; the rest are plain strings with defaults. A
    non-scalar (``voice_id: [a, b]``) is refused rather than stringified, because
    ``"['a', 'b']"`` would reach the synthesiser as a voice id.
    """
    _check_keys(raw, VoiceConfig.__dataclass_fields__, "voice")
    for key, value in raw.items():
        if value is not None and not isinstance(value, str | int | float):
            raise ValueError(
                f"persona bundle voice.{key} must be text, got {type(value).__name__}"
            )
    return VoiceConfig(
        tts=str(raw.get("tts", "cartesia")),
        voice_id=str(raw.get("voice_id", "")),
        model=None if raw.get("model") is None else str(raw["model"]),
        speed=str(raw.get("speed", "normal")),
    )


def _parse_tactics(raw: dict[str, Any] | None) -> dict[Tactic, float]:
    weights: dict[Tactic, float] = {}
    for key, value in (raw or {}).items():
        try:
            weights[Tactic(key)] = float(value)
        except ValueError as exc:
            valid = ", ".join(t.value for t in Tactic)
            raise ValueError(f"unknown tactic {key!r} in persona bundle; expected one of: {valid}") from exc
    return weights


def load_persona(persona_id: str) -> Persona:
    """Load a persona bundle from ``personas/<persona_id>/``."""
    directory = PERSONA_DIR / persona_id
    config_path = directory / "persona.yaml"
    character_path = directory / "core.md"

    if not config_path.exists():
        known = ", ".join(available_personas()) or "none found"
        raise FileNotFoundError(f"no persona bundle at {config_path} (available: {known})")
    if not character_path.exists():
        raise FileNotFoundError(f"persona {persona_id} has no core.md at {character_path}")

    config: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    voice_raw = config.get("voice") or {}
    pacing_raw = config.get("pacing") or {}
    sound_raw = config.get("sound_pack") or {}

    _check_keys(config, _TOP_LEVEL_KEYS, "top-level")
    _check_keys(sound_raw, ("fillers", "holds", "ambient"), "sound_pack")

    return Persona(
        id=config.get("id", persona_id),
        display_name=config.get("display_name", persona_id.title()),
        description=(config.get("description") or "").strip(),
        character_prompt=character_path.read_text(encoding="utf-8"),
        identity=load_identity(persona_id),
        voice=_parse_voice(voice_raw),
        pacing=_parse_pacing(pacing_raw),
        tactic_weights=_parse_tactics(config.get("tactics")),
        fillers=tuple(sound_raw.get("fillers") or ()),
        holds=tuple(sound_raw.get("holds") or ()),
        ambient=sound_raw.get("ambient"),
        _shared_prompt=_read_shared_playbooks(),
    )


def available_personas() -> list[str]:
    if not PERSONA_DIR.exists():
        return []
    return sorted(p.name for p in PERSONA_DIR.iterdir() if (p / "persona.yaml").exists())

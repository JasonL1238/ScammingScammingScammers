"""Talking to Claude, shaped for a live phone call.

Sonnet 5. Four things about its request surface are load-bearing, and all four fail
*invisibly* — ``_generate`` catches everything and speaks a stalling line, so a malformed
request costs a whole call of fumbles and leaves only a log:

* **``thinking`` must be disabled explicitly**, because omitting it runs adaptive. Adaptive
  shares the ``max_tokens`` ceiling with the spoken reply, and streams no text while it
  runs — on a phone line that is dead air (G-16), not a spinner.
* **``effort`` must be set explicitly**, because unset it defaults to ``high``.
* **No mid-conversation ``role: "system"`` message**, so the state note rides in the last
  caller turn. See :func:`_finish_newest_turn`.
* **No assistant prefill.** See :func:`_strip_edge_assistant_turns`.

No sampling parameters (a non-default one is rejected) and no fast mode (Opus-only). Both
absences are pinned by tests rather than restated here.

Re-run ``textloop --script <name>`` **without** ``--dry`` after any model, effort, or
prompt change: ``--dry`` sets ``brain=None``, so none of this module's request
construction runs — no request is built or sent — though dry runs still import the
module and build ``Turn``s. The personas are the product and this is the knob most
likely to cheapen them.

``docs/guardrails.md`` records what went wrong here before, and why each shape is the
shape it is.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ssscammers.shared.config import Settings

logger = logging.getLogger(__name__)

__all__ = ["ClaudeBrain", "Turn", "MODEL", "EFFORT", "model_overrides"]

MODEL = "claude-sonnet-5"
EFFORT = "low"


def model_overrides(settings: Settings) -> dict[str, str]:
    """The ``ClaudeBrain`` kwargs an operator has overridden, if any.

    Returns only keys that were actually set, so an unset env var leaves the dataclass
    default in place rather than passing an empty string as a model name. Shared by both
    construction sites — the phone pipeline and the text harness — so the two cannot
    drift into honouring different subsets of the configuration.
    """
    overrides: dict[str, str] = {}
    if settings.anthropic_model:
        overrides["model"] = settings.anthropic_model
    if settings.anthropic_effort:
        overrides["effort"] = settings.anthropic_effort
    return overrides


@dataclass
class Turn:
    """One exchange in the conversation, in API shape."""

    role: str
    content: str


@dataclass
class ClaudeBrain:
    """Wraps the Anthropic client with this project's call-shaped defaults.

    Args:
        system_prompt: The persona prompt. Must not change during a call.
        api_key: Optional; the SDK resolves a key or an ``ant auth`` profile itself.
        model: Defaults to :data:`MODEL`. Overridable so a retired or regionally
            unavailable model is an env var and a restart rather than a redeploy.
        effort: Defaults to :data:`EFFORT`.
        max_tokens: Kept small — spoken turns are short, and a long one means the persona
            is monologuing rather than conversing. Measured headroom: the wordiest
            persona (``harold``, whose bundle says "not slow, he is long") peaks around
            273 output tokens under escalating pressure, so 400 holds a full turn with
            roughly a third to spare. Raising ``effort`` erodes that margin.
    """

    system_prompt: str
    api_key: str | None = None
    model: str = MODEL
    effort: str = EFFORT
    max_tokens: int = 400

    _client: Any = field(init=False, default=None, repr=False)
    last_stop_reason: str | None = field(init=False, default=None, repr=False)
    """Why the most recent turn stopped. ``"max_tokens"`` means the reply was cut mid-word.

    Read by ``Conversation._generate`` so a truncated turn is labelled in the event log.
    Without it, truncation is indistinguishable from a clean turn: the stream simply ends,
    :meth:`stream_reply` flushes the residual buffer as a final chunk with no sentence
    terminator, and it is spoken and recorded like any other reply.
    """

    def __post_init__(self) -> None:
        # Imported lazily so the safety test suite runs without the SDK installed.
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the dep
            raise RuntimeError(
                "the anthropic SDK is not installed; run `pip install -e '.[media]'` "
                "or `pip install anthropic`"
            ) from exc

        self._client = (
            anthropic.AsyncAnthropic(api_key=self.api_key)
            if self.api_key
            else anthropic.AsyncAnthropic()
        )

    # -- request construction -------------------------------------------------

    def _system_blocks(self) -> list[dict[str, Any]]:
        """The cached prefix: one breakpoint, one hour.

        Measured at 4,526 tokens against this model's 1024-token minimum, and confirmed
        on the wire — ``cache_creation_input_tokens: 4526`` on the first call of a call,
        ``cache_read_input_tokens: 4526`` on every one after. The floor matters because
        falling below it is *silent*: the API caches nothing, reports a zero, and raises
        no error. ``tests/test_persona.py`` pins every bundle above it.

        Only the prefix is cached. The transcript after it is re-sent at full price every
        turn, so a call's input cost grows with the square of its turn count — the
        dominant term on a long call, well ahead of the choice of model.
        """
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]

    @staticmethod
    def _build_messages(history: Sequence[Turn], state_note: str | None) -> list[dict[str, Any]]:
        """Assemble the message list, state note folded into the last caller turn.

        This model has no mid-conversation ``role: "system"`` message, so the note is
        appended to the newest user turn inside a ``<system-reminder>`` tag. Same
        position, same caching profile, and no 400.

        The trade-off is spoofability: a system-role message is a channel the caller
        cannot reach, and this one is text in their own turn. Nothing downstream trusts
        model output — triage, the state machine, the caps, and the output filter are all
        deterministic — so a caller who successfully imitates the tag can change what the
        persona *says* (which the adversarial scripts already cover) but cannot move the
        call's phase, silence the filter, or reach any real data.
        """
        messages: list[dict[str, Any]] = [
            {"role": turn.role, "content": turn.content} for turn in history
        ]
        _strip_edge_assistant_turns(messages)

        if state_note and not messages:
            # Nothing to steer yet: the persona has spoken but the caller has not.
            logger.debug("dropping state note: no caller turn to attach it to")
            return messages

        _finish_newest_turn(messages, state_note)
        return messages

    def _request_kwargs(
        self, history: Sequence[Turn], state_note: str | None
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # Both explicit on purpose — see the module docstring for what each default
            # would otherwise be.
            "thinking": {"type": "disabled"},
            "output_config": {"effort": self.effort},
            "system": self._system_blocks(),
            "messages": self._build_messages(history, state_note),
        }

    # -- generation -----------------------------------------------------------

    async def stream_reply(
        self, history: Sequence[Turn], state_note: str | None = None
    ) -> AsyncIterator[str]:
        """Yield sentence-sized chunks as they are produced.

        Sentence rather than token granularity because the consumer is a synthesiser: it
        can speak sentence one while sentence two is still being written, the largest win
        available on perceived latency.
        """
        self.last_stop_reason = None
        kwargs = self._request_kwargs(history, state_note)

        if not kwargs["messages"]:
            # The API requires at least one message. Reaching the wire with none is a
            # guaranteed 400, and a caught 400 is indistinguishable from a real failure —
            # so refuse here, where the reason can be logged, and let the caller's
            # fail-soft path cover the turn.
            logger.warning("no caller turn to respond to; skipping the model this turn")
            return

        buffer = ""
        async for delta in self._stream_text(kwargs):
            buffer += delta
            while (cut := _sentence_boundary(buffer)) is not None:
                sentence, buffer = buffer[:cut].strip(), buffer[cut:].lstrip()
                if sentence:
                    yield sentence

        if buffer.strip():
            yield buffer.strip()

    async def _stream_text(self, kwargs: dict[str, Any]) -> AsyncIterator[str]:
        """Stream raw text deltas, recording why the turn stopped.

        The non-beta endpoint: every parameter in this request is GA on this model.
        """
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

            final = await stream.get_final_message()
            self.last_stop_reason = final.stop_reason
            if final.stop_reason == "max_tokens":
                logger.warning(
                    "reply hit max_tokens=%d and was cut mid-sentence; the persona will "
                    "be heard trailing off and the fragment enters the transcript",
                    self.max_tokens,
                )


def _finish_newest_turn(messages: list[dict[str, Any]], state_note: str | None) -> None:
    """Attach the state note and the cache breakpoint to the newest turn, in place.

    Decided together because the *order* is what matters. Without a breakpoint the
    transcript is re-sent at full price every turn, so input cost grows with the square of
    the turn count — the dominant term at the ninety-minute cap. But the note is rebuilt
    every turn and only ever rides the newest turn, so marking a block that contains it
    stores a prefix no later request reproduces: nothing is read, and every turn still pays
    the 1.25x write premium for a guaranteed miss. Marking the caller's words and leaving
    the note unmarked after them is the documented "shared prefix, varying suffix" shape.

    Five-minute TTL, unlike the system prompt's hour: this breakpoint moves every turn and
    is read seconds later, and the short TTL writes at 1.25x instead of 2x.
    """
    if not messages:
        return

    text = messages[-1]["content"]
    if not isinstance(text, str):  # pragma: no cover - nothing builds block content
        raise TypeError(f"expected str content on the newest turn, got {type(text).__name__}")

    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
    ]
    if state_note:
        blocks.append(
            {"type": "text", "text": f"<system-reminder>\n{state_note}\n</system-reminder>"}
        )
    messages[-1] = {"role": messages[-1]["role"], "content": blocks}


def _strip_edge_assistant_turns(messages: list[dict[str, Any]]) -> None:
    """Drop assistant turns from both ends, in place. Both ends are a 400.

    *Leading*, because a call's transcript legitimately starts assistant-first — the
    persona says "Hello?" before the caller has said anything — and the API requires the
    first message to be a caller turn.

    *Trailing*, because an assistant turn in last position is an assistant prefill, which
    this model rejects. Nothing in production plans a model turn without new caller
    speech: the transport calls ``respond()`` only on non-empty transcribed text, which
    appends a caller turn first, and ``tick()`` is barred from planning a model turn at
    all. This is defence for the day one of those two guards moves.
    """
    while messages and messages[0]["role"] == "assistant":
        del messages[0]

    trailing = 0
    while messages and messages[-1]["role"] == "assistant":
        del messages[-1]
        trailing += 1
    if trailing:
        logger.warning(
            "dropped %d trailing assistant turn(s): the API rejects a prefill, and a "
            "model turn should not have been planned with no new caller speech",
            trailing,
        )


_SENTENCE_ENDINGS = ".!?…"


def _sentence_boundary(text: str) -> int | None:
    """Index just past the first sentence ending, or ``None`` if there isn't one yet.

    Ellipses and trailing dashes count as endings: the personas trail off constantly, and
    waiting for a full stop that never comes holds back audio the caller should hear.
    """
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDINGS:
            # Don't split mid-decimal or mid-abbreviation.
            nxt = text[index + 1 : index + 2]
            if char == "." and nxt.isdigit():
                continue
            if nxt in ("", " ", "\n", '"', "'"):
                return index + 1
    if "—" in text and len(text) > 40:
        return text.index("—") + 1
    return None

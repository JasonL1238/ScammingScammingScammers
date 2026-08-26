"""The request surface and the stream splitter, in their own module.

Everything here fails *invisibly* in production: `Conversation._generate`
catches every exception and speaks a stalling line, so a malformed request or a
dropped tail costs a whole call of fumbles and leaves only a log entry. These
are the checks that would otherwise only happen on a live call.

The splitter cases are deliberately exhaustive about the rules that exist for a
reason — the decimal guard, the em-dash rule, the tail flush — because each was
added to fix a specific way a persona sounded wrong, and a rule nobody tests is
a rule the next refactor deletes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from ssscammers.agent.llm import ClaudeBrain, Turn, stream_sentences
from ssscammers.simscammer.replay import (
    CallRecording,
    RecordedAnthropicClient,
    RecordedTurn,
)


async def split(*deltas: str) -> list[str]:
    async def stream() -> AsyncIterator[str]:
        for delta in deltas:
            yield delta

    return [sentence async for sentence in stream_sentences(stream())]


class TestTheSentenceSplitter:
    async def test_it_cuts_on_terminators(self) -> None:
        assert await split("One. Two! Three?") == ["One.", "Two!", "Three?"]

    async def test_a_sentence_split_across_deltas_is_rejoined(self) -> None:
        assert await split("Oh, hel", "lo de", "ar.") == ["Oh, hello dear."]

    async def test_the_tail_is_flushed_even_without_a_terminator(self) -> None:
        # THE truncation path: a reply cut at max_tokens has no terminator by
        # definition, so the residual buffer is the speech. Dropping it would
        # substitute a fumble line for the caller's last words.
        assert await split("I was cut off mid") == ["I was cut off mid"]

    async def test_a_whitespace_only_tail_is_not_spoken(self) -> None:
        assert await split("Done.", "   \n  ") == ["Done."]

    async def test_an_empty_stream_yields_nothing(self) -> None:
        assert await split() == []

    async def test_a_decimal_point_is_not_a_sentence_end(self) -> None:
        # "It was 3.50" must not become "It was 3." — the personas read amounts
        # aloud constantly.
        assert await split("It was 3.50 dear.") == ["It was 3.50 dear."]

    async def test_an_ellipsis_ends_a_sentence(self) -> None:
        # The personas trail off constantly; waiting for a full stop that never
        # arrives holds back audio the caller should already hear.
        assert await split("Oh… I don't know.") == ["Oh…", "I don't know."]

    async def test_a_long_run_breaks_on_an_em_dash(self) -> None:
        long_enough = "I was going to say something about the thing" + "—" + " and then"
        assert len(long_enough) > 40
        assert (await split(long_enough))[0].endswith("—")

    async def test_a_short_run_does_not_break_on_an_em_dash(self) -> None:
        # Under the length floor the dash is punctuation inside one thought.
        assert await split("Oh—no.") == ["Oh—no."]

    async def test_a_terminator_before_a_quote_cuts_and_orphans_the_quote(self) -> None:
        # Pinning what the splitter actually does, quirk included: a closing
        # quote after a terminator starts the *next* chunk, so quoted speech
        # ends up with a stray quote mark leading the following sentence. It is
        # cosmetic — the synthesiser has nothing to say for a lone quote — and
        # deliberately left alone here rather than changed under a test task,
        # but it should be visible rather than discovered later as a surprise.
        assert await split('He said "yes." "Then what?"') == [
            'He said "yes.',
            '" "Then what?',
            '"',
        ]


class TestTheRequestSurface:
    """Built for real, then inspected — no network, no SDK call."""

    def brain(self, **kwargs: object) -> tuple[ClaudeBrain, RecordedAnthropicClient]:
        client = RecordedAnthropicClient(
            CallRecording(turns=(RecordedTurn(("hi.",)),)), strict=False
        )
        return ClaudeBrain(system_prompt="SYSTEM", client=client, **kwargs), client  # type: ignore[arg-type]

    async def test_a_truncated_reply_warns_with_the_ceiling_that_cut_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The operator-facing half of truncation. Without it, a persona heard
        # trailing off mid-word looks identical to one that finished talking.
        client = RecordedAnthropicClient(
            CallRecording(turns=(RecordedTurn(("cut off mid",), stop_reason="max_tokens"),)),
            strict=False,
        )
        brain = ClaudeBrain(system_prompt="SYSTEM", client=client, max_tokens=400)

        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.llm"):
            [s async for s in brain.stream_reply([Turn("user", "hello")])]

        assert "max_tokens=400" in caplog.text
        assert brain.last_stop_reason == "max_tokens"

    async def test_a_clean_reply_warns_about_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        brain, _ = self.brain()
        with caplog.at_level(logging.WARNING, logger="ssscammers.agent.llm"):
            [s async for s in brain.stream_reply([Turn("user", "hello")])]
        assert caplog.text == ""

    def test_the_effort_override_reaches_the_request(self) -> None:
        brain, _ = self.brain(effort="high")
        assert brain._request_kwargs([Turn("user", "hi")], None)["output_config"] == {
            "effort": "high"
        }

    def test_build_messages_is_public_because_replay_depends_on_it(self) -> None:
        # ReplayBrain runs this to honour the same no-addressable-turn guard the
        # real brain applies; a private name would mean reaching into internals
        # from another package.
        assert ClaudeBrain.build_messages([], None) == []
        assert ClaudeBrain.build_messages([Turn("assistant", "Hello?")], None) == []
        assert ClaudeBrain.build_messages([Turn("user", "hi")], None) != []

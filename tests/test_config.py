"""The env readers in ``ssscammers.shared.config`` — the fail-quietly traps.

A mistyped operational cap must never silently become the (often laxer) built-in
default: the fallback stays, but it has to announce itself. These tests pin the
warnings as behavior, not just the fallback values.
"""

from __future__ import annotations

import logging

import pytest

from ssscammers.shared.config import _env_number, _positive_rate

CONFIG_LOGGER = "ssscammers.shared.config"


class TestEnvNumber:
    def test_a_valid_value_parses_silently(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MAX_CONCURRENT_CALLS", "7")
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _env_number("MAX_CONCURRENT_CALLS", 5) == 7
        assert not caplog.records

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_or_blank_values_default_silently(
        self,
        value: str | None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # None pins the unset path; "" pins empty-is-falsy; whitespace pins
        # _env's strip — without it, int("   ") would raise and every boot
        # would warn spuriously about a value nobody mistyped.
        if value is None:
            monkeypatch.delenv("MAX_CONCURRENT_CALLS", raising=False)
        else:
            monkeypatch.setenv("MAX_CONCURRENT_CALLS", value)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _env_number("MAX_CONCURRENT_CALLS", 5) == 5
        assert not caplog.records

    def test_a_typo_falls_back_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The operator lowered a cap and typed a letter O for a zero: the value
        # must revert to the default, but never silently — the default is often
        # laxer than what the operator intended.
        monkeypatch.setenv("MAX_CONCURRENT_CALLS", "5O")
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _env_number("MAX_CONCURRENT_CALLS", 5) == 5
        assert "MAX_CONCURRENT_CALLS" in caplog.text
        assert "'5O'" in caplog.text

    def test_a_float_string_for_an_int_setting_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # int("1.5") raises ValueError, so a fractional value for an integer cap
        # is a typo like any other.
        monkeypatch.setenv("HARD_CALL_CAP_SECONDS", "1.5")
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _env_number("HARD_CALL_CAP_SECONDS", 5400) == 5400
        assert "HARD_CALL_CAP_SECONDS" in caplog.text

    def test_a_long_unparseable_value_is_never_echoed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The warning fires exactly when the operator mangled .env, and one such
        # mangling is pasting a secret onto a caps line: the warning must name
        # the variable without capturing the value in the log stream.
        secret = "sk-ant-" + "x" * 40
        monkeypatch.setenv("MAX_CONCURRENT_CALLS", secret)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _env_number("MAX_CONCURRENT_CALLS", 5) == 5
        assert secret not in caplog.text
        assert "MAX_CONCURRENT_CALLS" in caplog.text
        assert "47-character" in caplog.text

    def test_the_result_takes_the_defaults_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ESTIMATED_USD_PER_CALL_MINUTE", "2.5")
        value = _env_number("ESTIMATED_USD_PER_CALL_MINUTE", 0.09)
        assert value == 2.5
        assert isinstance(value, float)


class TestKnownLimits:
    """Deliberate gaps, written down so they stay choices rather than assumptions."""

    def test_env_number_parses_but_does_not_range_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _env_number parses; it does not validate. Zero and negative caps pass
        # through untouched — only the cost rate gets a range guard
        # (_positive_rate), because a bad rate fails toward laxness while
        # nearly every other cap fails toward refusal. The probation pair is
        # the known exception (a zero PROBATION_HARD_COMMIT_SECONDS commits to
        # baiting with no triage window) and is tracked as follow-up work.
        monkeypatch.setenv("MAX_CONCURRENT_CALLS", "-3")
        assert _env_number("MAX_CONCURRENT_CALLS", 5) == -3


class TestPositiveRate:
    def test_nan_falls_back_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # float("nan") parses, and every comparison against NaN is False — the
        # exact shape that once switched the daily spend cap off with no error.
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _positive_rate(float("nan"), 0.09) == 0.09
        assert "ESTIMATED_USD_PER_CALL_MINUTE" in caplog.text

    def test_a_positive_finite_rate_passes_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            assert _positive_rate(0.05, 0.09) == 0.05
        assert not caplog.records

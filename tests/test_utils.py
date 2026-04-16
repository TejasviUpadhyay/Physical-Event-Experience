"""
Unit tests for SmartFlow AI utility helpers.

Each function in utils.py is pure and side-effect-free.
These tests verify both the happy path and all documented error conditions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.utils import (
    clamp,
    format_factors,
    normalize_to_percentage,
    round_score,
    utc_now,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# clamp
# ---------------------------------------------------------------------------


class TestClamp:
    """Tests for the clamp() utility."""

    def test_value_within_range_is_unchanged(self) -> None:
        assert clamp(42.0, 0.0, 100.0) == 42.0

    def test_value_at_minimum_is_unchanged(self) -> None:
        assert clamp(0.0, 0.0, 100.0) == 0.0

    def test_value_at_maximum_is_unchanged(self) -> None:
        assert clamp(100.0, 0.0, 100.0) == 100.0

    def test_value_below_minimum_is_clamped_to_minimum(self) -> None:
        assert clamp(-5.0, 0.0, 100.0) == 0.0

    def test_value_above_maximum_is_clamped_to_maximum(self) -> None:
        assert clamp(110.0, 0.0, 100.0) == 100.0

    def test_minimum_equals_maximum_returns_that_value(self) -> None:
        assert clamp(50.0, 42.0, 42.0) == 42.0

    def test_inverted_bounds_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="minimum"):
            clamp(50.0, 100.0, 0.0)

    def test_negative_range_works_correctly(self) -> None:
        assert clamp(-3.0, -10.0, -1.0) == -3.0
        assert clamp(0.0, -10.0, -1.0) == -1.0
        assert clamp(-15.0, -10.0, -1.0) == -10.0

    def test_float_precision_preserved(self) -> None:
        assert clamp(0.001, 0.0, 1.0) == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# round_score
# ---------------------------------------------------------------------------


class TestRoundScore:
    """Tests for the round_score() utility."""

    def test_rounds_to_two_decimal_places_by_default(self) -> None:
        assert round_score(73.4567) == 73.46

    def test_integer_value_is_unchanged(self) -> None:
        assert round_score(50.0) == 50.0

    def test_rounds_up_correctly(self) -> None:
        assert round_score(99.999, 1) == 100.0

    def test_zero_decimals_rounds_to_integer(self) -> None:
        assert round_score(73.6, 0) == 74.0

    def test_custom_decimal_places(self) -> None:
        assert round_score(3.14159, 3) == 3.142

    def test_negative_decimals_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="decimals"):
            round_score(50.0, -1)

    def test_zero_value_returns_zero(self) -> None:
        assert round_score(0.0) == 0.0

    def test_result_type_is_float(self) -> None:
        assert isinstance(round_score(42.0), float)


# ---------------------------------------------------------------------------
# normalize_to_percentage
# ---------------------------------------------------------------------------


class TestNormalizeToPercentage:
    """Tests for the normalize_to_percentage() utility."""

    def test_half_of_cap_returns_50(self) -> None:
        assert normalize_to_percentage(30, 60) == 50.0

    def test_value_at_cap_returns_100(self) -> None:
        assert normalize_to_percentage(60, 60) == 100.0

    def test_value_above_cap_is_clamped_to_100(self) -> None:
        assert normalize_to_percentage(90, 60) == 100.0

    def test_zero_value_returns_0(self) -> None:
        assert normalize_to_percentage(0, 60) == 0.0

    def test_negative_value_is_clamped_to_0(self) -> None:
        assert normalize_to_percentage(-10, 60) == 0.0

    def test_zero_cap_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="cap"):
            normalize_to_percentage(30, 0)

    def test_negative_cap_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="cap"):
            normalize_to_percentage(30, -10)

    def test_result_is_within_0_to_100(self) -> None:
        for value in [-100, 0, 30, 60, 120]:
            result = normalize_to_percentage(value, 60)
            assert 0.0 <= result <= 100.0

    def test_proportional_scaling(self) -> None:
        assert normalize_to_percentage(15, 60) == pytest.approx(25.0)
        assert normalize_to_percentage(45, 60) == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# format_factors
# ---------------------------------------------------------------------------


class TestFormatFactors:
    """Tests for the format_factors() utility."""

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        assert format_factors(["  High density "]) == ["High density"]

    def test_removes_blank_entries(self) -> None:
        assert format_factors(["Factor A", "", "Factor B"]) == ["Factor A", "Factor B"]

    def test_removes_whitespace_only_entries(self) -> None:
        assert format_factors(["Factor A", "   ", "Factor B"]) == ["Factor A", "Factor B"]

    def test_empty_list_returns_empty_list(self) -> None:
        assert format_factors([]) == []

    def test_all_blank_entries_returns_empty_list(self) -> None:
        assert format_factors(["", "  ", "\t"]) == []

    def test_preserves_original_order(self) -> None:
        factors = ["First", "Second", "Third"]
        assert format_factors(factors) == factors

    def test_single_valid_entry_returned_correctly(self) -> None:
        assert format_factors(["  Only one  "]) == ["Only one"]

    def test_mixed_valid_and_blank_entries(self) -> None:
        result = format_factors(["  High density ", "", "Long queue", "  "])
        assert result == ["High density", "Long queue"]


# ---------------------------------------------------------------------------
# utc_now
# ---------------------------------------------------------------------------


class TestUtcNow:
    """Tests for the utc_now() utility."""

    def test_returns_datetime_instance(self) -> None:
        assert isinstance(utc_now(), datetime)

    def test_returned_datetime_is_timezone_aware(self) -> None:
        dt = utc_now()
        assert dt.tzinfo is not None

    def test_returned_datetime_is_utc(self) -> None:
        dt = utc_now()
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_two_calls_are_non_decreasing(self) -> None:
        first = utc_now()
        second = utc_now()
        assert second >= first


# ---------------------------------------------------------------------------
# utc_now_iso
# ---------------------------------------------------------------------------


class TestUtcNowIso:
    """Tests for the utc_now_iso() utility."""

    def test_returns_string(self) -> None:
        assert isinstance(utc_now_iso(), str)

    def test_string_is_parseable_as_iso8601(self) -> None:
        ts = utc_now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_string_contains_utc_offset(self) -> None:
        ts = utc_now_iso()
        assert "+00:00" in ts

    def test_two_calls_produce_valid_timestamps(self) -> None:
        ts1 = utc_now_iso()
        ts2 = utc_now_iso()
        dt1 = datetime.fromisoformat(ts1)
        dt2 = datetime.fromisoformat(ts2)
        assert dt2 >= dt1

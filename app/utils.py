"""
Shared utility helpers for SmartFlow AI.

Each function is pure, side-effect-free, and independently testable.
Only utilities with genuine reuse across the codebase are included here.
"""

from __future__ import annotations

from datetime import datetime, timezone


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Constrain a float value to the inclusive range [minimum, maximum].

    Args:
        value: The value to constrain.
        minimum: Lower bound (inclusive). Must be <= maximum.
        maximum: Upper bound (inclusive).

    Returns:
        The clamped value within [minimum, maximum].

    Raises:
        ValueError: If minimum is greater than maximum.

    Examples:
        >>> clamp(110.0, 0.0, 100.0)
        100.0
        >>> clamp(-5.0, 0.0, 100.0)
        0.0
        >>> clamp(42.0, 0.0, 100.0)
        42.0
    """
    if minimum > maximum:
        raise ValueError(
            f"clamp: minimum ({minimum}) must not be greater than maximum ({maximum})"
        )
    return max(minimum, min(value, maximum))


def round_score(score: float, decimals: int = 2) -> float:
    """Round a numeric score to a fixed number of decimal places.

    Centralising rounding prevents inconsistent precision across response
    fields and makes score comparisons predictable in tests.

    Args:
        score: The raw floating-point score.
        decimals: Number of decimal places to retain. Must be >= 0. Default: 2.

    Returns:
        The rounded score.

    Raises:
        ValueError: If decimals is negative.

    Examples:
        >>> round_score(73.4567)
        73.46
        >>> round_score(50.0)
        50.0
        >>> round_score(99.999, 1)
        100.0
    """
    if decimals < 0:
        raise ValueError(f"round_score: decimals must be >= 0, got {decimals}")
    return round(score, decimals)


def normalize_to_percentage(value: float, cap: float) -> float:
    """Scale a value to a 0–100 percentage relative to a defined cap.

    Values above the cap are treated as 100 % (fully saturated).
    Negative values are treated as 0 %.

    Args:
        value: The raw value to normalise (e.g. queue time in minutes).
        cap: The upper reference point that maps to 100 %. Must be > 0.

    Returns:
        Normalised percentage in [0.0, 100.0].

    Raises:
        ValueError: If cap is not a positive number.

    Examples:
        >>> normalize_to_percentage(30, 60)
        50.0
        >>> normalize_to_percentage(90, 60)
        100.0
        >>> normalize_to_percentage(0, 60)
        0.0
    """
    if cap <= 0:
        raise ValueError(f"normalize_to_percentage: cap must be > 0, got {cap}")
    return clamp((value / cap) * 100.0, 0.0, 100.0)


def format_factors(factors: list[str]) -> list[str]:
    """Normalise a list of contributing factor strings.

    Strips surrounding whitespace and removes blank entries so that
    response payloads are always clean and consistent.

    Args:
        factors: Raw list of factor description strings.

    Returns:
        Cleaned list with blank entries removed, preserving original order.

    Examples:
        >>> format_factors(["  High density ", "", "Long queue"])
        ['High density', 'Long queue']
        >>> format_factors([])
        []
    """
    return [f.strip() for f in factors if f.strip()]


def utc_now() -> datetime:
    """Return the current UTC datetime as a timezone-aware object.

    Uses timezone.utc to avoid ambiguity between local and UTC times.
    Suitable for populating datetime fields in Pydantic response models.

    Returns:
        Timezone-aware datetime in UTC.
    """
    return datetime.now(tz=timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string.

    Convenience wrapper around utc_now() for use in plain string contexts
    such as health check responses and log entries.

    Returns:
        UTC timestamp string in ISO 8601 format,
        e.g. ``"2026-04-15T10:30:00.123456+00:00"``.
    """
    return utc_now().isoformat()

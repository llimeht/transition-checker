from __future__ import annotations

from datetime import date
import re


_PERIOD_ALIASES = {
    "t1": "term 1",
    "term1": "term 1",
    "term 1": "term 1",
    "t2": "term 2",
    "term2": "term 2",
    "term 2": "term 2",
    "t3": "term 3",
    "term3": "term 3",
    "term 3": "term 3",
    "s1": "semester 1",
    "semester1": "semester 1",
    "semester 1": "semester 1",
    "s2": "semester 2",
    "semester2": "semester 2",
    "semester 2": "semester 2",
    "summer": "summer term",
    "summer term": "summer term",
    "winter": "winter term",
    "winter term": "winter term",
}

_PERIOD_RANKS = {
    "summer term": 5,
    "term 1": 10,
    "term 2": 20,
    "winter term": 25,
    "term 3": 30,
    "semester 1": 10,
    "semester 2": 30,
    "s1": 10,
    "s2": 30,
    "t1": 10,
    "t2": 20,
    "t3": 30,
}

_PERIOD_DISPLAY_LABELS = {
    "summer term": "Summer Term",
    "term 1": "Term 1",
    "term 2": "Term 2",
    "winter term": "Winter Term",
    "term 3": "Term 3",
    "semester 1": "Semester 1",
    "semester 2": "Semester 2",
}

_CANONICAL_TO_SHORT = {
    "term 1": "T1",
    "term 2": "T2",
    "term 3": "T3",
    "semester 1": "S1",
    "semester 2": "S2",
    "summer term": "SUMMER",
    "winter term": "WINTER",
}

# Anchors are month/day pairs and applied to any given year.
_PERIOD_START_ANCHORS = {
    "T1": (1, 1),
    "T2": (5, 1),
    "T3": (9, 1),
    "S1": (1, 1),
    "S2": (7, 1),
}

_PERIOD_END_ANCHORS = {
    "T1": (4, 30),
    "T2": (8, 31),
    "T3": (12, 31),
    "S1": (6, 30),
    "S2": (12, 31),
}


def canonical_period(period: str) -> str:
    """Canonicalize period aliases so offerings/templates compare consistently."""

    normalized = period.strip().lower()
    return _PERIOD_ALIASES.get(normalized, normalized)


def is_nonstandard_period(period: str) -> bool:
    """Return whether a period is summer/winter after canonicalization."""

    canonical = canonical_period(period)
    return canonical in {"summer term", "winter term"}


def period_rank(period: str, fallback: int | None = 999) -> int | None:
    """Map teaching-period labels to sortable rank values."""

    normalized = period.strip().lower()
    return _PERIOD_RANKS.get(normalized, fallback)


def natural_sort_key(period: str) -> tuple[int, int]:
    """Return a natural sort key for periods ordered by type, then number.

    Sorts in order: T1, T2, T3, S1, S2, Summer, Winter
    Returns tuple of (period_type, period_number) for consistent ordering.
    """
    canonical = canonical_period(period)

    if canonical.startswith("term"):
        match = re.search(r"(\d+)", canonical)
        num = int(match.group(1)) if match else 0
        return (0, num)
    elif canonical.startswith("semester"):
        match = re.search(r"(\d+)", canonical)
        num = int(match.group(1)) if match else 0
        return (1, num)
    elif canonical == "summer term":
        return (2, 0)
    elif canonical == "winter term":
        return (3, 0)
    else:
        return (99, 0)  # fallback for unknown periods


def period_display_label(period: str) -> str:
    """Return the preferred display label for a period alias or canonical value."""

    canonical = canonical_period(period)
    return _PERIOD_DISPLAY_LABELS.get(canonical, canonical.title())


def period_short_label(period: str) -> str:
    """Return a short canonical period token (for example T1 or S2)."""

    canonical = canonical_period(period)
    return _CANONICAL_TO_SHORT.get(canonical, canonical.upper())


def period_start_date(year: int, period: str) -> date | None:
    """Return anchored period start date for a given year and period token."""

    token = period_short_label(period)
    anchor = _PERIOD_START_ANCHORS.get(token)
    if anchor is None:
        return None
    month, day = anchor
    return date(year, month, day)


def period_end_date(year: int, period: str) -> date | None:
    """Return anchored period end date for a given year and period token."""

    token = period_short_label(period)
    anchor = _PERIOD_END_ANCHORS.get(token)
    if anchor is None:
        return None
    month, day = anchor
    return date(year, month, day)


def duration_years_between_periods(
    start_year: int,
    start_period: str,
    end_year: int,
    end_period: str,
) -> float | None:
    """Return fractional years between period start and end anchors."""

    start = period_start_date(start_year, start_period)
    end = period_end_date(end_year, end_period)
    if start is None or end is None:
        return None
    if end < start:
        return None

    days = (end - start).days + 1
    return days / 365.25


def format_duration_years(duration_years: float | None) -> str:
    """Format duration years for report display."""

    if duration_years is None:
        return ""
    return f"{duration_years:.1f}"

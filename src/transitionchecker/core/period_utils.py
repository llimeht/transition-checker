from __future__ import annotations

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

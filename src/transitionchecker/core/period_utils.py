from __future__ import annotations


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

"""Tests for transitionchecker.core.period_utils."""

from __future__ import annotations

import pytest

from transitionchecker.core.period_utils import (
    canonical_period,
    is_nonstandard_period,
    period_rank,
)


class TestCanonicalPeriod:
    @pytest.mark.parametrize("alias, expected", [
        ("t1", "term 1"),
        ("T1", "term 1"),
        ("term1", "term 1"),
        ("term 1", "term 1"),
        ("t2", "term 2"),
        ("t3", "term 3"),
        ("s1", "semester 1"),
        ("s2", "semester 2"),
        ("semester 1", "semester 1"),
        ("semester 2", "semester 2"),
        ("summer", "summer term"),
        ("summer term", "summer term"),
        ("winter", "winter term"),
        ("winter term", "winter term"),
    ])
    def test_known_aliases(self, alias, expected):
        assert canonical_period(alias) == expected

    def test_unknown_period_returned_lowercased(self):
        assert canonical_period("Hexamester 1") == "hexamester 1"

    def test_strips_whitespace(self):
        assert canonical_period("  t1  ") == "term 1"


class TestIsNonstandardPeriod:
    @pytest.mark.parametrize("period", [
        "summer",
        "summer term",
        "winter",
        "winter term",
        "Summer Term",
    ])
    def test_nonstandard(self, period):
        assert is_nonstandard_period(period)

    @pytest.mark.parametrize("period", [
        "term 1",
        "t1",
        "semester 2",
        "s2",
    ])
    def test_standard(self, period):
        assert not is_nonstandard_period(period)


class TestPeriodRank:
    @pytest.mark.parametrize("period, expected", [
        ("summer term", 5),
        ("term 1", 10),
        ("t1", 10),
        ("s1", 10),
        ("term 2", 20),
        ("t2", 20),
        ("winter term", 25),
        ("term 3", 30),
        ("t3", 30),
        ("semester 1", 10),
        ("semester 2", 30),
        ("s2", 30),
    ])
    def test_known_ranks(self, period, expected):
        assert period_rank(period) == expected

    def test_unknown_period_returns_fallback(self):
        assert period_rank("hexamester 1") == 999

    def test_unknown_period_custom_fallback(self):
        assert period_rank("hexamester 1", fallback=0) == 0

    def test_unknown_period_none_fallback(self):
        assert period_rank("hexamester 1", fallback=None) is None

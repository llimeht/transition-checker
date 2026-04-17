"""Tests for transitionchecker.core.period_utils."""

from __future__ import annotations

import pytest

from transitionchecker.core.period_utils import (
    canonical_period,
    is_nonstandard_period,
    natural_sort_key,
    period_display_label,
    period_rank,
)


class TestCanonicalPeriod:
    @pytest.mark.parametrize(
        "alias, expected",
        [
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
        ],
    )
    def test_known_aliases(self, alias: str, expected: str) -> None:
        assert canonical_period(alias) == expected

    def test_unknown_period_returned_lowercased(self) -> None:
        assert canonical_period("Hexamester 1") == "hexamester 1"

    def test_strips_whitespace(self) -> None:
        assert canonical_period("  t1  ") == "term 1"


class TestIsNonstandardPeriod:
    @pytest.mark.parametrize(
        "period",
        [
            "summer",
            "summer term",
            "winter",
            "winter term",
            "Summer Term",
        ],
    )
    def test_nonstandard(self, period: str) -> None:
        assert is_nonstandard_period(period)

    @pytest.mark.parametrize(
        "period",
        [
            "term 1",
            "t1",
            "semester 2",
            "s2",
        ],
    )
    def test_standard(self, period: str) -> None:
        assert not is_nonstandard_period(period)


class TestPeriodRank:
    @pytest.mark.parametrize(
        "period, expected",
        [
            ("term 1", 10),
            ("t1", 10),
            ("term 2", 20),
            ("t2", 20),
            ("term 3", 30),
            ("t3", 30),
            ("semester 1", 10),
            ("s1", 10),
            ("semester 2", 30),
            ("s2", 30),
            ("summer term", 5),
            ("winter term", 25),
        ],
    )
    def test_known_ranks(self, period: str, expected: int) -> None:
        assert period_rank(period) == expected

    def test_unknown_period_returns_fallback(self) -> None:
        assert period_rank("hexamester 1") == 999

    def test_unknown_period_custom_fallback(self) -> None:
        assert period_rank("hexamester 1", fallback=0) == 0

    def test_unknown_period_none_fallback(self) -> None:
        assert period_rank("hexamester 1", fallback=None) is None


class TestPeriodDisplayLabel:
    @pytest.mark.parametrize(
        "period, expected",
        [
            ("T1", "Term 1"),
            ("term 2", "Term 2"),
            ("s1", "Semester 1"),
            ("semester 2", "Semester 2"),
            ("summer", "Summer Term"),
            ("winter term", "Winter Term"),
        ],
    )
    def test_known_display_labels(self, period: str, expected: str) -> None:
        assert period_display_label(period) == expected

    def test_unknown_period_falls_back_to_title_case(self) -> None:
        assert period_display_label("hexamester 1") == "Hexamester 1"


class TestNaturalSortKey:
    @pytest.mark.parametrize(
        "period, expected",
        [
            ("term 1", (0, 1)),
            ("T1", (0, 1)),
            ("term 2", (0, 2)),
            ("t3", (0, 3)),
            ("semester 1", (1, 1)),
            ("S1", (1, 1)),
            ("semester 2", (1, 2)),
            ("summer", (2, 0)),
            ("summer term", (2, 0)),
            ("winter", (3, 0)),
            ("winter term", (3, 0)),
        ],
    )
    def test_natural_sort_key_orders_by_type_then_number(
        self, period: str, expected: tuple[int, int]
    ) -> None:
        assert natural_sort_key(period) == expected

    def test_unknown_period_maps_to_fallback(self) -> None:
        assert natural_sort_key("hexamester 1") == (99, 0)

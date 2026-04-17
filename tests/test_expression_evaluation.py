"""Tests for evaluate_expression, expression_to_text, and diagnose_expression."""

from __future__ import annotations

from collections import Counter

import pytest

from transitionchecker.rules_engine import (
    diagnose_expression,
    evaluate_expression,
    expression_to_text,
)


def completed(*codes: str, uoc: int = 0) -> tuple[Counter[str], int]:
    return Counter(codes), uoc


class TestEvaluateExpression:
    def test_leaf_present(self) -> None:
        assert evaluate_expression("CEIC1000", Counter(["CEIC1000"]))

    def test_leaf_absent(self) -> None:
        assert not evaluate_expression("CEIC1000", Counter())

    def test_and_all_present(self) -> None:
        c = Counter(["CEIC1000", "CEIC1001"])
        assert evaluate_expression({"and": ["CEIC1000", "CEIC1001"]}, c)

    def test_and_one_missing(self) -> None:
        c = Counter(["CEIC1000"])
        assert not evaluate_expression({"and": ["CEIC1000", "CEIC1001"]}, c)

    def test_or_one_present(self) -> None:
        c = Counter(["CEIC1001"])
        assert evaluate_expression({"or": ["CEIC1000", "CEIC1001"]}, c)

    def test_or_none_present(self) -> None:
        assert not evaluate_expression({"or": ["CEIC1000", "CEIC1001"]}, Counter())

    def test_min_from_satisfied(self) -> None:
        c = Counter(["CEIC1000", "CEIC1001"])
        expr = {"min": 1, "from": ["CEIC1000", "CEIC1001", "CEIC1002"]}
        assert evaluate_expression(expr, c)

    def test_min_from_not_satisfied(self) -> None:
        c = Counter(["CEIC1000"])
        expr = {"min": 2, "from": ["CEIC1000", "CEIC1001", "CEIC1002"]}
        assert not evaluate_expression(expr, c)

    def test_uoc_satisfied(self) -> None:
        _, uoc = completed("CEIC1000", uoc=120)
        assert evaluate_expression({"uoc": 120}, Counter(), 120)

    def test_uoc_not_satisfied(self) -> None:
        assert not evaluate_expression({"uoc": 120}, Counter(), 60)

    def test_nested_and_or(self) -> None:
        # (A AND B) OR C
        expr = {"or": [{"and": ["CEIC1000", "CEIC1001"]}, "CEIC1002"]}
        # Only C present
        assert evaluate_expression(expr, Counter(["CEIC1002"]))
        # Neither A+B nor C
        assert not evaluate_expression(expr, Counter(["CEIC1000"]))


class TestExpressionToText:
    def test_leaf(self) -> None:
        assert expression_to_text("CEIC1000") == "CEIC1000"

    def test_and(self) -> None:
        text = expression_to_text({"and": ["CEIC1000", "CEIC1001"]})
        assert "CEIC1000" in text
        assert "CEIC1001" in text
        assert "AND" in text

    def test_or(self) -> None:
        text = expression_to_text({"or": ["CEIC1000", "CEIC1001"]})
        assert "OR" in text

    def test_uoc(self) -> None:
        assert expression_to_text({"uoc": 120}) == "120 UOC"

    def test_min_from_at_least(self) -> None:
        text = expression_to_text({"min": 1, "from": ["CEIC1000", "CEIC1001"]})
        assert "AT LEAST 1" in text

    def test_min_from_all_of(self) -> None:
        text = expression_to_text({"min": 2, "from": ["CEIC1000", "CEIC1001"]})
        assert "ALL OF" in text


class TestDiagnoseExpression:
    def test_failing_leaf_mentions_course(self) -> None:
        text = diagnose_expression("CEIC1000", Counter(), 0)
        assert "CEIC1000" in text

    def test_passing_expression_returns_text(self) -> None:
        text = diagnose_expression("CEIC1000", Counter(["CEIC1000"]), 0)
        # Should not raise; may return a short "satisfied" string
        assert isinstance(text, str)

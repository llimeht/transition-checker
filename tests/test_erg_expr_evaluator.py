"""Tests for evaluate_erg_expression() and the unified evaluation path."""

from __future__ import annotations

from collections import Counter

from typing import Any

from transitionchecker.erg_parser import rule_exprs_to_erg_expr
from transitionchecker.rules_engine import evaluate_erg_expression


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eval(
    expr: dict[str, Any],
    prior: list[str] | None = None,
    coreq: list[str] | None = None,
    uoc: int = 0,
    course_uoc: dict[str, int] | None = None,
) -> bool:
    p = Counter(prior or [])
    c = Counter(coreq or [])
    return evaluate_erg_expression(expr, p, c, uoc, course_uoc)


# ---------------------------------------------------------------------------
# Leaf atoms
# ---------------------------------------------------------------------------

class TestLeafAtoms:
    def test_prereq_satisfied(self) -> None:
        assert _eval({"prereq": "CEIC3004"}, prior=["CEIC3004"])

    def test_prereq_not_satisfied(self) -> None:
        assert not _eval({"prereq": "CEIC3004"}, prior=[])

    def test_coreq_satisfied_in_group(self) -> None:
        assert _eval({"coreq": "CEIC3007"}, coreq=["CEIC3007"])

    def test_coreq_not_satisfied(self) -> None:
        assert not _eval({"coreq": "CEIC3007"}, coreq=[])

    def test_coreq_satisfied_via_prior(self) -> None:
        # coreq_courses = prior + current group; prior alone is sufficient
        assert _eval({"coreq": "CEIC3007"}, coreq=["CEIC3004", "CEIC3007"])

    def test_uoc_satisfied(self) -> None:
        assert _eval({"uoc": 48}, uoc=48)
        assert _eval({"uoc": 48}, uoc=72)

    def test_uoc_not_satisfied(self) -> None:
        assert not _eval({"uoc": 48}, uoc=30)

    def test_condition_always_true(self) -> None:
        assert _eval({"condition": "Enrolment in program <ID:000219>"})
        assert _eval({"condition": ""})

    def test_prereq_pattern_satisfied(self) -> None:
        assert _eval({"prereq_pattern": "JURD####"}, prior=["JURD7001"])

    def test_prereq_pattern_not_satisfied(self) -> None:
        assert not _eval({"prereq_pattern": "JURD####"}, prior=["CEIC3004"])

    def test_coreq_pattern_satisfied(self) -> None:
        assert _eval({"coreq_pattern": "COMP3###"}, coreq=["COMP3021"])

    def test_coreq_pattern_not_satisfied(self) -> None:
        assert not _eval({"coreq_pattern": "COMP3###"}, coreq=["COMP2041"])

    def test_uoc_with_restriction_satisfied(self) -> None:
        assert _eval(
            {"uoc": 18, "restriction": "JURD####"},
            prior=["JURD7001", "JURD7002", "JURD7003"],
            course_uoc={"JURD7001": 6, "JURD7002": 6, "JURD7003": 6},
        )

    def test_uoc_with_restriction_not_enough(self) -> None:
        assert not _eval(
            {"uoc": 18, "restriction": "JURD####"},
            prior=["JURD7001", "CEIC3004"],
            course_uoc={"JURD7001": 6, "CEIC3004": 6},  # only 6 UoC of JURD
        )

    def test_uoc_restriction_no_map_falls_back_to_total(self) -> None:
        # Without course_uoc map, restriction is ignored and total UoC is used
        assert _eval({"uoc": 36, "restriction": "JURD####"}, uoc=36)


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------

class TestCombinators:
    def test_and_all_satisfied(self) -> None:
        assert _eval(
            {"and": [{"prereq": "CEIC3004"}, {"prereq": "CEIC3005"}]},
            prior=["CEIC3004", "CEIC3005"],
        )

    def test_and_one_missing(self) -> None:
        assert not _eval(
            {"and": [{"prereq": "CEIC3004"}, {"prereq": "CEIC3005"}]},
            prior=["CEIC3004"],
        )

    def test_or_any_sufficient(self) -> None:
        assert _eval(
            {"or": [{"prereq": "COMM1140"}, {"prereq": "COMM1240"}]},
            prior=["COMM1240"],
        )

    def test_or_none_satisfied(self) -> None:
        assert not _eval(
            {"or": [{"prereq": "COMM1140"}, {"prereq": "COMM1240"}]},
            prior=[],
        )

    def test_condition_never_blocks_and(self) -> None:
        assert _eval(
            {"and": [{"prereq": "CEIC3004"}, {"condition": "Enrolment in program 9201"}]},
            prior=["CEIC3004"],
        )

    def test_condition_satisfies_or(self) -> None:
        # A condition leaf is always True, so an OR with a condition always passes
        assert _eval(
            {"or": [{"condition": "Enrolment in program 9201"}, {"prereq": "CEIC3004"}]},
            prior=[],  # CEIC3004 missing but condition is True
        )


# ---------------------------------------------------------------------------
# CEIC4001 pattern: mixed prereq/coreq in OR alternatives
# ---------------------------------------------------------------------------

class TestCeic4001Pattern:
    def _expr(self) -> dict[str, Any]:
        return {"or": [
            {"and": [{"prereq": "CEIC3004"}, {"prereq": "CEIC3005"}]},
            {"and": [{"prereq": "CEIC3005"}, {"prereq": "CEIC3006"}]},
            {"and": [{"prereq": "CEIC3006"}, {"prereq": "CEIC3004"}, {"coreq": "CEIC3007"}]},
        ]}

    def test_first_alternative_satisfied(self) -> None:
        assert _eval(self._expr(), prior=["CEIC3004", "CEIC3005"])

    def test_second_alternative_satisfied(self) -> None:
        assert _eval(self._expr(), prior=["CEIC3005", "CEIC3006"])

    def test_third_alternative_requires_coreq(self) -> None:
        # Third alternative: CEIC3006 + CEIC3004 as prereqs, CEIC3007 as coreq
        assert _eval(
            self._expr(),
            prior=["CEIC3006", "CEIC3004"],
            coreq=["CEIC3006", "CEIC3004", "CEIC3007"],
        )

    def test_third_alternative_fails_without_coreq(self) -> None:
        # Third alternative: prereqs present but coreq absent AND first/second not satisfied
        assert not _eval(
            self._expr(),
            prior=["CEIC3006", "CEIC3004"],
            coreq=["CEIC3006", "CEIC3004"],  # no CEIC3007
        )

    def test_all_alternatives_fail(self) -> None:
        assert not _eval(self._expr(), prior=["CEIC3004"])


# ---------------------------------------------------------------------------
# ENGG1811 pattern: pure COND conditions → always True
# ---------------------------------------------------------------------------

class TestEngg1811Pattern:
    def test_pure_condition_or_always_true(self) -> None:
        expr = {"or": [
            {"condition": "Enrolment in program <ID:000219>"},
            {"condition": "Enrolment in program <ID:000217>"},
        ]}
        assert _eval(expr)  # no courses needed

    def test_pure_condition_and_always_true(self) -> None:
        expr = {"and": [
            {"condition": "Enrolment in program <ID:000219>"},
            {"condition": "Enrolment in program <ID:000217>"},
        ]}
        assert _eval(expr)


# ---------------------------------------------------------------------------
# rule_exprs_to_erg_expr → evaluate_erg_expression round-trip
# ---------------------------------------------------------------------------

class TestRuleExprRoundtrip:
    def test_simple_prereq_roundtrip(self) -> None:
        expr = rule_exprs_to_erg_expr("CEIC3004", None)
        assert _eval(expr, prior=["CEIC3004"])
        assert not _eval(expr, prior=[])

    def test_or_prereq_roundtrip(self) -> None:
        rule = {"or": ["COMM1140", "COMM1240"]}
        expr = rule_exprs_to_erg_expr(rule, None)
        assert _eval(expr, prior=["COMM1240"])
        assert not _eval(expr, prior=[])

    def test_prereq_and_coreq_roundtrip(self) -> None:
        expr = rule_exprs_to_erg_expr("CEIC2000", "CEIC2001")
        # prereq satisfied, coreq in coreq window
        assert _eval(expr, prior=["CEIC2000"], coreq=["CEIC2000", "CEIC2001"])
        # prereq satisfied, coreq missing
        assert not _eval(expr, prior=["CEIC2000"], coreq=["CEIC2000"])

    def test_none_none_always_true(self) -> None:
        expr = rule_exprs_to_erg_expr(None, None)
        assert _eval(expr)

    def test_uoc_roundtrip(self) -> None:
        expr = rule_exprs_to_erg_expr({"uoc": 48}, None)
        assert _eval(expr, uoc=48)
        assert not _eval(expr, uoc=30)

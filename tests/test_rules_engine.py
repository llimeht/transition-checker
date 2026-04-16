"""Integration tests for rules_engine: prerequisites validation and rules checking."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from transitionchecker.rules_engine import (
    RuleValidationError,
    extract_scheduled_courses,
    validate_plan_prerequisites,
    validate_rules_config,
    evaluate_required,
)


class TestExtractScheduledCourses:
    def test_extracts_and_sorts_chronologically(
        self, plan_valid: dict[str, Any]
    ) -> None:
        courses = extract_scheduled_courses(plan_valid)
        assert len(courses) == 4
        # First two are Term 1 2024, next is Term 2 2024, last Term 3 2024
        years = [c.year for c in courses]
        assert years == sorted(years)
        ranks = [(c.year, c.period_rank) for c in courses]
        assert ranks == sorted(ranks)

    def test_raises_for_missing_courses_key(self) -> None:
        with pytest.raises(RuleValidationError, match="courses"):
            extract_scheduled_courses({"sheet": "X", "intake": "2024 T1"})

    def test_skips_entries_with_no_code(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {"year": 2024, "period": "Term 1", "course_n": "C1", "uoc": 6, "prerequisites": "."},
            ]
        }
        courses = extract_scheduled_courses(plan)
        assert courses == []


class TestValidatePlanPrerequisites:
    def test_valid_plan_has_no_failures(self, plan_valid: dict[str, Any]) -> None:
        failures, unsupported = validate_plan_prerequisites(plan_valid)
        assert failures == []
        assert unsupported == []

    def test_missing_prereq_detected(self, plan_missing_prereq: dict[str, Any]) -> None:
        """TEST2001 in Term 1 requires TEST1001, but they're in the same period."""
        failures, _unsupported = validate_plan_prerequisites(plan_missing_prereq)
        # TEST2001 lists TEST1001 as prereq but it's in the same term → fails
        assert any("TEST2001" in f for f in failures)

    def test_coreq_in_same_term_passes(self) -> None:
        """A corequisite must be taken in the same or earlier term."""
        plan: dict[str, Any] = {
            "courses": [
                {"year": 2024, "period": "Term 1", "course_n": "Course 1",
                 "code": "CEIC1000", "uoc": 6, "prerequisites": "."},
                {"year": 2024, "period": "Term 1", "course_n": "Course 2",
                 "code": "CEIC1001", "uoc": 6,
                 "prerequisites": "CO-REQ: CEIC1000"},
            ]
        }
        failures, _unsupported = validate_plan_prerequisites(plan)
        assert failures == []


class TestValidateRulesConfig:
    def test_valid_rules_accepted(self, rules_simple: dict[str, Any]) -> None:
        result = validate_rules_config(rules_simple)
        assert result["schemaVersion"] == 2
        assert "required" in result

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(RuleValidationError, match="required"):
            validate_rules_config({"schemaVersion": 2})

    def test_unknown_operator_raises(self) -> None:
        bad_config: dict[str, Any] = {
            "required": {
                "Level 1": [{"xor": ["CEIC1000", "CEIC1001"]}]
            }
        }
        with pytest.raises(RuleValidationError):
            validate_rules_config(bad_config)

    def test_min_less_than_from_raises(self) -> None:
        bad_config: dict[str, Any] = {
            "required": {
                "Level 1": [{"min": 5, "from": ["CEIC1000", "CEIC1001"]}]
            }
        }
        with pytest.raises(RuleValidationError):
            validate_rules_config(bad_config)

    def test_empty_level_raises(self) -> None:
        bad_config: dict[str, Any] = {
            "required": {
                "Level 1": []
            }
        }
        with pytest.raises(RuleValidationError):
            validate_rules_config(bad_config)


class TestEvaluateRequired:
    def test_passing_plan(self, rules_simple: dict[str, Any]) -> None:
        normalized = validate_rules_config(rules_simple)
        # Supply all courses appearing in rules_simple
        completed = Counter(["TEST1001", "TEST1002", "TEST2001", "TEST2003"])
        result = evaluate_required(normalized, completed)
        assert all(result.values()), f"Some levels failed: {result}"

    def test_failing_plan_missing_level1(self, rules_simple: dict[str, Any]) -> None:
        normalized = validate_rules_config(rules_simple)
        completed = Counter(["TEST2001", "TEST2003"])  # missing TEST1001 and TEST1002
        result = evaluate_required(normalized, completed)
        assert not result["Level 1"]

    def test_failing_plan_missing_level2(self, rules_simple: dict[str, Any]) -> None:
        normalized = validate_rules_config(rules_simple)
        # Level 2 requires (TEST2001 OR TEST2002) AND (min 1 from TEST2003/TEST2004)
        completed = Counter(["TEST1001", "TEST1002"])  # Level 1 fine, Level 2 fails
        result = evaluate_required(normalized, completed)
        assert result["Level 1"]
        assert not result["Level 2"]

    def test_or_alternative_satisfies_level2(self, rules_simple: dict[str, Any]) -> None:
        normalized = validate_rules_config(rules_simple)
        completed = Counter(["TEST1001", "TEST1002", "TEST2002", "TEST2004"])
        result = evaluate_required(normalized, completed)
        assert result["Level 1"]
        assert result["Level 2"]

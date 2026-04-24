"""Integration tests for rules_engine: prerequisites validation and rules checking."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from transitionchecker.core.catalogue import Catalogue, CatalogueEntry
from transitionchecker.rules_engine import (
    RuleValidationError,
    extract_scheduled_courses,
    report_plan_detailed,
    validate_plan_prerequisites,
    validate_plan_prerequisites_detailed,
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
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "C1",
                    "uoc": 6,
                    "prerequisites": ".",
                },
            ]
        }
        courses = extract_scheduled_courses(plan)
        assert courses == []

    def test_prefers_catalogue_course_metadata_when_provided(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "C1",
                    "code": "TEST2001",
                    "uoc": 12,
                    "prerequisites": ".",
                },
            ]
        }
        catalogue = Catalogue(
            [
                CatalogueEntry(
                    code="TEST2001",
                    title="Test Course",
                    career="",
                    uoc=6,
                    prerequisites="TEST1001",
                )
            ]
        )

        courses = extract_scheduled_courses(plan, catalogue=catalogue)

        assert len(courses) == 1
        assert courses[0].uoc == 6
        assert courses[0].prerequisites == "TEST1001"


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

    def test_catalogue_prereq_overrides_plan_embedded_value(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST1001",
                    "uoc": 6,
                    "prerequisites": ".",
                },
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "Course 2",
                    "code": "TEST2001",
                    "uoc": 6,
                    "prerequisites": ".",
                },
            ]
        }
        catalogue = Catalogue(
            [
                CatalogueEntry(
                    code="TEST1001",
                    title="Prerequisite",
                    career="",
                    uoc=6,
                    prerequisites="",
                ),
                CatalogueEntry(
                    code="TEST2001",
                    title="Dependent",
                    career="",
                    uoc=6,
                    prerequisites="TEST1001",
                ),
            ]
        )

        failures, unsupported = validate_plan_prerequisites(plan, catalogue=catalogue)

        assert unsupported == []
        assert any("TEST2001" in failure for failure in failures)

    def test_coreq_in_same_term_passes(self) -> None:
        """A corequisite must be taken in the same or earlier term."""
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "CEIC1000",
                    "uoc": 6,
                    "prerequisites": ".",
                },
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": "Course 2",
                    "code": "CEIC1001",
                    "uoc": 6,
                    "prerequisites": "CO-REQ: CEIC1000",
                },
            ]
        }
        failures, _unsupported = validate_plan_prerequisites(plan)
        assert failures == []

    def test_generic_uoc_prereq_passes_with_any_prior_courses(self) -> None:
        courses: list[dict[str, Any]] = []
        for idx in range(12):
            code = f"JURD7{idx:03d}" if idx % 2 == 0 else f"CEIC7{idx:03d}"
            courses.append(
                {
                    "year": 2024,
                    "period": "Term 1",
                    "course_n": f"Course {idx + 1}",
                    "code": code,
                    "uoc": 6,
                    "prerequisites": ".",
                }
            )
        courses.append(
            {
                "year": 2024,
                "period": "Term 2",
                "course_n": "Course 13",
                "code": "JURD7999",
                "uoc": 6,
                "prerequisites": "72 UOC of Science courses",
            }
        )

        failures, unsupported = validate_plan_prerequisites({"courses": courses})
        assert failures == []
        assert unsupported == []


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
            "required": {"Level 1": [{"xor": ["CEIC1000", "CEIC1001"]}]}
        }
        with pytest.raises(RuleValidationError):
            validate_rules_config(bad_config)

    def test_min_less_than_from_raises(self) -> None:
        bad_config: dict[str, Any] = {
            "required": {"Level 1": [{"min": 5, "from": ["CEIC1000", "CEIC1001"]}]}
        }
        with pytest.raises(RuleValidationError):
            validate_rules_config(bad_config)

    def test_empty_level_raises(self) -> None:
        bad_config: dict[str, Any] = {"required": {"Level 1": []}}
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

    def test_or_alternative_satisfies_level2(
        self, rules_simple: dict[str, Any]
    ) -> None:
        normalized = validate_rules_config(rules_simple)
        completed = Counter(["TEST1001", "TEST1002", "TEST2002", "TEST2004"])
        result = evaluate_required(normalized, completed)
        assert result["Level 1"]
        assert result["Level 2"]


class TestRuleFindingIds:
    def test_named_subset_ids_are_overrideable(
        self,
        rules_with_subset_ids: dict[str, Any],
        plan_for_subset_rules: dict[str, Any],
    ) -> None:
        validated = validate_rules_config(rules_with_subset_ids)
        completed = Counter(["TEST1001", "TEST3001"])

        _legacy, findings, warnings = report_plan_detailed(validated, completed)

        failure_ids = {f["failure_id"] for f in findings}
        assert "rule:PATHWAY_TEST200x" in failure_ids
        assert "rule:ADVANCED_POOL_MIN2" in failure_ids
        assert not any(w["code"] == "missing_rule_id" for w in warnings)

        subset_findings = [
            f
            for f in findings
            if f["failure_id"] in {"rule:PATHWAY_TEST200x", "rule:ADVANCED_POOL_MIN2"}
        ]
        assert subset_findings
        assert all(f["overrideable"] is True for f in subset_findings)

    def test_missing_subset_ids_warn_and_disable_override(
        self,
        rules_without_subset_ids: dict[str, Any],
    ) -> None:
        validated = validate_rules_config(rules_without_subset_ids)
        completed = Counter(["TEST1001", "TEST3001"])

        _legacy, findings, warnings = report_plan_detailed(validated, completed)

        missing_id_warnings = [w for w in warnings if w["code"] == "missing_rule_id"]
        assert missing_id_warnings
        assert len(missing_id_warnings) >= 2

        unnamed_subset_findings = [
            f for f in findings if f["failure_id"].startswith("rule:unnamed:")
        ]
        assert unnamed_subset_findings
        assert all(f["overrideable"] is False for f in unnamed_subset_findings)
        assert all(
            f.get("non_overrideable_reason") == "missing_rule_id"
            for f in unnamed_subset_findings
        )


class TestPrerequisiteFindingDecomposition:
    def test_prereq_and_decomposes_into_multiple_atomic_findings(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2026,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST4001",
                    "uoc": 6,
                    "prerequisites": "TEST1001 AND TEST1002",
                }
            ]
        }
        _legacy_failures, _legacy_unsupported, findings, warnings = (
            validate_plan_prerequisites_detailed(plan)
        )

        ids = {f["failure_id"] for f in findings if f["kind"] == "prereq"}
        assert "prereq:TEST4001>TEST1001" in ids
        assert "prereq:TEST4001>TEST1002" in ids
        assert not warnings

    def test_coreq_uses_greater_equal_operator(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2026,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST5001",
                    "uoc": 6,
                    "prerequisites": "CO-REQ: TEST1001",
                }
            ]
        }
        _legacy_failures, _legacy_unsupported, findings, _warnings = (
            validate_plan_prerequisites_detailed(plan)
        )
        ids = {f["failure_id"] for f in findings if f["kind"] == "coreq"}
        assert "coreq:TEST5001>=TEST1001" in ids

    def test_unsupported_syntax_is_warning_and_non_overrideable(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2026,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST6001",
                    "uoc": 6,
                    "prerequisites": "TEST1001 / TEST1002",
                }
            ]
        }
        _legacy_failures, legacy_unsupported, findings, warnings = (
            validate_plan_prerequisites_detailed(plan)
        )

        assert legacy_unsupported
        assert any(w["code"] == "unsupported_syntax" for w in warnings)
        unsupported_findings = [
            f for f in findings if f["kind"] == "unsupported-syntax"
        ]
        assert unsupported_findings
        assert all(f["overrideable"] is False for f in unsupported_findings)
        assert all(
            f.get("non_overrideable_reason") == "unsupported_syntax"
            for f in unsupported_findings
        )

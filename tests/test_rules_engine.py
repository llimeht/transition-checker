"""Integration tests for rules_engine: prerequisites validation and rules checking."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from transitionchecker.core.catalogue import Catalogue, CatalogueEntry
from transitionchecker.rules_engine import (
    _apply_equivalences,  # pyright: ignore[reportPrivateUsage]
    CourseEquivalence,
    RuleValidationError,
    extract_scheduled_courses,
    validate_annual_loads,
    report_plan_detailed,
    validate_nonstandard_periods,
    validate_plan_prerequisites,
    validate_plan_prerequisites_detailed,
    validate_rules_config,
    validate_unmatched_courses,
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

    def test_rpl_course_satisfies_missing_prerequisite(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2026,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST2001",
                    "uoc": 6,
                    "prerequisites": "TEST1001",
                }
            ]
        }

        failures, _unsupported, findings, warnings = (
            validate_plan_prerequisites_detailed(plan)
        )
        assert failures
        assert any(f["failure_id"] == "prereq:TEST2001>TEST1001" for f in findings)
        assert not warnings

        failures, unsupported, findings, warnings = (
            validate_plan_prerequisites_detailed(
                plan,
                rpl_courses=Counter(["TEST1001"]),
            )
        )

        assert not failures
        assert not unsupported
        assert not findings
        assert not warnings


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

    def test_placeholder_min_from_is_accepted(self) -> None:
        config: dict[str, Any] = {
            "required": {
                "Electives": [
                    {
                        "min": 2,
                        "placeholder": "CEICeeee",
                        "from": ["TEST2001", "TEST2002", "TEST2003"],
                    }
                ]
            }
        }

        validated = validate_rules_config(config)

        clause = validated["required"]["Electives"][0]
        assert clause["placeholder"] == "CEICEEEE"

    def test_placeholder_or_is_accepted(self) -> None:
        config: dict[str, Any] = {
            "required": {
                "Pathway": [
                    {
                        "or": ["TEST2001", "TEST2002"],
                        "placeholder": "ceiceeee",
                    }
                ]
            }
        }

        validated = validate_rules_config(config)

        clause = validated["required"]["Pathway"][0]
        assert clause["placeholder"] == "CEICEEEE"

    def test_rpl_is_normalized_as_uppercase_course_codes(self) -> None:
        validated = validate_rules_config(
            {
                "required": {"Level 1": ["TEST1001"]},
                "rpl": [" test1001 ", "test1002"],
            }
        )

        assert validated["rpl"] == ["TEST1001", "TEST1002"]

    def test_rpl_must_be_array_of_course_codes(self) -> None:
        with pytest.raises(RuleValidationError, match="rpl"):
            validate_rules_config(
                {
                    "required": {"Level 1": ["TEST1001"]},
                    "rpl": {"TEST1001": True},
                }
            )

        with pytest.raises(RuleValidationError, match=r"rpl\[1\]"):
            validate_rules_config(
                {
                    "required": {"Level 1": ["TEST1001"]},
                    "rpl": ["TEST1001", 123],
                }
            )

    def test_double_counted_is_normalized_as_uppercase_course_codes(self) -> None:
        validated = validate_rules_config(
            {
                "required": {"Level 1": ["TEST1001"]},
                "shared-courses": {
                    "double-counted": [" test1001 ", "test1002"],
                },
            }
        )

        assert validated["shared-courses"]["double-counted"] == [
            "TEST1001",
            "TEST1002",
        ]

    def test_legacy_top_level_double_counted_is_migrated(self) -> None:
        validated = validate_rules_config(
            {
                "required": {"Level 1": ["TEST1001"]},
                "double-counted": [" test1001 ", "test1002"],
            }
        )

        assert "double-counted" not in validated
        assert validated["shared-courses"]["double-counted"] == [
            "TEST1001",
            "TEST1002",
        ]

    def test_double_counted_must_be_array_of_unique_course_codes(self) -> None:
        with pytest.raises(RuleValidationError, match="double-counted"):
            validate_rules_config(
                {
                    "required": {"Level 1": ["TEST1001"]},
                    "shared-courses": {
                        "double-counted": {"TEST1001": True},
                    },
                }
            )

        with pytest.raises(RuleValidationError, match=r"double-counted\[1\]"):
            validate_rules_config(
                {
                    "required": {"Level 1": ["TEST1001"]},
                    "shared-courses": {
                        "double-counted": ["TEST1001", 123],
                    },
                }
            )

        with pytest.raises(RuleValidationError, match="duplicate"):
            validate_rules_config(
                {
                    "required": {"Level 1": ["TEST1001"]},
                    "shared-courses": {
                        "double-counted": ["TEST1001", "test1001"],
                    },
                }
            )

    def test_over_double_count_limit_selectors_are_normalized(self) -> None:
        validated = validate_rules_config(
            {
                "required": {
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "ceiceeee",
                            "from": ["test2001", "test2002", "test2003"],
                        }
                    ]
                },
                "shared-courses": {
                    "over-double-count-limit": [
                        {
                            "placeholder": "ceiceeee",
                            "from": [" test2001 ", "test2002"],
                        }
                    ]
                },
            }
        )

        assert validated["shared-courses"]["over-double-count-limit"] == [
            {
                "placeholder": "CEICEEEE",
                "from": ["TEST2001", "TEST2002"],
            }
        ]

    def test_over_double_count_limit_requires_matching_placeholder_clause(self) -> None:
        with pytest.raises(RuleValidationError, match="placeholder"):
            validate_rules_config(
                {
                    "required": {
                        "Electives": [
                            {
                                "min": 2,
                                "placeholder": "CEICEEEE",
                                "from": ["TEST2001", "TEST2002"],
                            }
                        ]
                    },
                    "shared-courses": {
                        "over-double-count-limit": [
                            {
                                "placeholder": "NOTHERE",
                                "from": ["TEST2001"],
                            }
                        ]
                    },
                }
            )

    def test_over_double_count_limit_requires_selector_courses_from_placeholder_pool(
        self,
    ) -> None:
        with pytest.raises(RuleValidationError, match="not available"):
            validate_rules_config(
                {
                    "required": {
                        "Electives": [
                            {
                                "min": 2,
                                "placeholder": "CEICEEEE",
                                "from": ["TEST2001", "TEST2002"],
                            }
                        ]
                    },
                    "shared-courses": {
                        "over-double-count-limit": [
                            {
                                "placeholder": "CEICEEEE",
                                "from": ["TEST9999"],
                            }
                        ]
                    },
                }
            )


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

    def test_placeholder_counts_toward_or_clause(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Pathway": [
                        {
                            "or": ["TEST2001", "TEST2002"],
                            "placeholder": "CEICeeee",
                        }
                    ]
                }
            }
        )

        completed = Counter(["CEICEEEE"])
        result = evaluate_required(normalized, completed)

        assert result["Pathway"]

    def test_plain_or_does_not_count_placeholder_rows(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Pathway": [
                        {
                            "or": ["TEST2001", "TEST2002"],
                        }
                    ]
                }
            }
        )

        completed = Counter(["CEICEEEE"])
        result = evaluate_required(normalized, completed)

        assert not result["Pathway"]

    def test_placeholder_counts_toward_min_from(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "CEICeeee",
                            "from": ["TEST2001", "TEST2002", "TEST2003"],
                        }
                    ]
                }
            }
        )

        completed = Counter(["CEICEEEE", "CEICEEEE"])
        result = evaluate_required(normalized, completed)

        assert result["Electives"]

    def test_placeholder_and_concrete_course_can_mix_in_min_from(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "CEICeeee",
                            "from": ["TEST2001", "TEST2002", "TEST2003"],
                        }
                    ]
                }
            }
        )

        completed = Counter(["CEICEEEE", "TEST2002"])
        result = evaluate_required(normalized, completed)

        assert result["Electives"]

    def test_plain_min_from_does_not_count_placeholder_rows(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Electives": [
                        {
                            "min": 2,
                            "from": ["TEST2001", "TEST2002", "TEST2003"],
                        }
                    ]
                }
            }
        )

        completed = Counter(["CEICEEEE", "CEICEEEE"])
        result = evaluate_required(normalized, completed)

        assert not result["Electives"]

    def test_rpl_does_not_satisfy_required_levels_by_itself(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {"Level 1": ["TEST1001"]},
                "rpl": ["TEST1001"],
            }
        )

        result = evaluate_required(normalized, Counter())

        assert not result["Level 1"]

    def test_overlapping_course_defaults_to_single_use_top_down(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                }
            }
        )

        result = evaluate_required(normalized, Counter(["TEST1001"]))

        assert result["Category A"]
        assert not result["Category B"]

    def test_double_counted_course_can_be_used_twice(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                },
                "shared-courses": {"double-counted": ["TEST1001"]},
            }
        )

        result = evaluate_required(normalized, Counter(["TEST1001"]))

        assert result["Category A"]
        assert result["Category B"]

    def test_double_counted_capacity_stops_at_two_uses(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                    "Category C": ["TEST1001"],
                },
                "shared-courses": {"double-counted": ["TEST1001"]},
            }
        )

        result = evaluate_required(normalized, Counter(["TEST1001"]))

        assert result["Category A"]
        assert result["Category B"]
        assert not result["Category C"]

    def test_over_double_count_limit_adds_extra_obligation(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "CEICEEEE",
                            "from": ["TEST1001", "TEST2001", "TEST2002"],
                        }
                    ],
                },
                "shared-courses": {
                    "double-counted": ["TEST1001"],
                    "over-double-count-limit": [
                        {
                            "placeholder": "CEICEEEE",
                            "from": ["TEST1001"],
                        }
                    ],
                },
            }
        )

        # TEST1001 is used twice across required levels, so it creates +1 elective debt.
        result = evaluate_required(normalized, Counter(["TEST1001", "TEST2001"]))

        assert result["Category A"]
        assert result["Category B"]
        assert not result["Electives"]

    def test_over_double_count_limit_passes_when_extra_pool_exists(self) -> None:
        normalized = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "CEICEEEE",
                            "from": [
                                "TEST1001",
                                "TEST2001",
                                "TEST2002",
                                "TEST2003",
                            ],
                        }
                    ],
                },
                "shared-courses": {
                    "double-counted": ["TEST1001"],
                    "over-double-count-limit": [
                        {
                            "placeholder": "CEICEEEE",
                            "from": ["TEST1001"],
                        }
                    ],
                },
            }
        )

        result = evaluate_required(
            normalized,
            Counter(["TEST1001", "TEST2001", "TEST2002", "TEST2003"]),
        )

        assert result["Category A"]
        assert result["Category B"]
        assert result["Electives"]


class TestRuleFindingIds:
    def test_over_double_count_limit_failure_is_reported(self) -> None:
        validated = validate_rules_config(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                    "Electives": [
                        {
                            "id": "CEIC_ELECTIVE_POOL",
                            "min": 2,
                            "placeholder": "CEICEEEE",
                            "from": [
                                "TEST1001",
                                "TEST2001",
                                "TEST2002",
                                "TEST2003",
                            ],
                        }
                    ],
                },
                "shared-courses": {
                    "double-counted": ["TEST1001"],
                    "over-double-count-limit": [
                        {
                            "placeholder": "CEICEEEE",
                            "from": ["TEST1001"],
                        }
                    ],
                },
            }
        )
        _legacy, findings, warnings, _allocations = report_plan_detailed(
            validated,
            Counter(["TEST1001", "TEST2001", "TEST2002"]),
        )

        assert not warnings
        assert any(
            f["failure_id"] == "rule:over-double-count-limit:CEICEEEE"
            for f in findings
        )

    def test_named_subset_ids_are_overrideable(
        self,
        rules_with_subset_ids: dict[str, Any],
        plan_for_subset_rules: dict[str, Any],
    ) -> None:
        validated = validate_rules_config(rules_with_subset_ids)
        completed = Counter(["TEST1001", "TEST3001"])

        _legacy, findings, warnings, _allocations = report_plan_detailed(
            validated,
            completed,
        )

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

        _legacy, findings, warnings, _allocations = report_plan_detailed(
            validated,
            completed,
        )

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

    def test_unsupported_syntax_is_error_and_non_overrideable(self) -> None:
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
        assert not any(w["code"] == "unsupported_syntax" for w in warnings)
        unsupported_findings = [
            f for f in findings if f["kind"] == "unsupported_syntax"
        ]
        assert unsupported_findings
        assert all(f["overrideable"] is False for f in unsupported_findings)
        assert all(
            f.get("non_overrideable_reason") == "unsupported_syntax"
            for f in unsupported_findings
        )


class TestValidateNonstandardPeriods:
    def _make_plan(self, *period_entries: tuple[int, str, str]) -> dict[str, Any]:
        """Build a minimal plan with courses in the given (year, period, code) tuples."""
        return {
            "courses": [
                {
                    "year": year,
                    "period": period,
                    "course_n": f"Course {i + 1}",
                    "code": code,
                    "uoc": 6,
                    "prerequisites": "",
                }
                for i, (year, period, code) in enumerate(period_entries)
            ]
        }

    def test_standard_period_produces_no_findings(self) -> None:
        plan = self._make_plan((2026, "Term 1", "TEST1001"))
        courses = extract_scheduled_courses(plan)
        findings = validate_nonstandard_periods(courses)
        assert findings == []

    def test_summer_term_produces_finding(self) -> None:
        plan = self._make_plan((2026, "Summer Term", "TEST1001"))
        courses = extract_scheduled_courses(plan)
        findings = validate_nonstandard_periods(courses)
        assert len(findings) == 1
        f = findings[0]
        assert f["failure_id"] == "nonstandard-period:TEST1001"
        assert f["kind"] == "nonstandard_period"
        assert f["overrideable"] is True
        assert f["accepted"] is False
        assert "TEST1001" in f["message"]
        assert "non-standard" in f["message"]

    def test_winter_term_produces_finding(self) -> None:
        plan = self._make_plan((2026, "Winter Term", "TEST2001"))
        courses = extract_scheduled_courses(plan)
        findings = validate_nonstandard_periods(courses)
        assert len(findings) == 1
        assert findings[0]["failure_id"] == "nonstandard-period:TEST2001"

    def test_only_nonstandard_courses_flagged(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001"),
            (2026, "Summer Term", "TEST1002"),
            (2026, "Term 2", "TEST1003"),
            (2026, "Winter Term", "TEST1004"),
        )
        courses = extract_scheduled_courses(plan)
        findings = validate_nonstandard_periods(courses)
        assert len(findings) == 2
        ids = {f["failure_id"] for f in findings}
        assert ids == {"nonstandard-period:TEST1002", "nonstandard-period:TEST1004"}

    def test_findings_appear_in_validate_plan_prerequisites_detailed(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001"),
            (2026, "Summer Term", "TEST1002"),
        )
        _failures, _unsupported, findings, _warnings = (
            validate_plan_prerequisites_detailed(plan)
        )
        nonstandard = [f for f in findings if f["kind"] == "nonstandard_period"]
        assert len(nonstandard) == 1
        assert nonstandard[0]["failure_id"] == "nonstandard-period:TEST1002"


class TestValidateAnnualLoads:
    def _make_plan(
        self, *course_entries: tuple[int, str, str, int]
    ) -> dict[str, Any]:
        """Build a minimal plan with (year, period, code, uoc) tuples."""

        return {
            "courses": [
                {
                    "year": year,
                    "period": period,
                    "course_n": f"Course {i + 1}",
                    "code": code,
                    "uoc": uoc,
                    "prerequisites": "",
                }
                for i, (year, period, code, uoc) in enumerate(course_entries)
            ]
        }

    def test_exactly_48_uoc_in_year_produces_no_findings(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001", 24),
            (2026, "Term 2", "TEST1002", 24),
        )
        courses = extract_scheduled_courses(plan)
        findings = validate_annual_loads(courses)
        assert findings == []

    def test_year_above_48_uoc_produces_finding(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001", 24),
            (2026, "Term 2", "TEST1002", 30),
        )
        courses = extract_scheduled_courses(plan)
        findings = validate_annual_loads(courses)
        assert len(findings) == 1
        finding = findings[0]
        assert finding["failure_id"] == "annual-load:2026"
        assert finding["kind"] == "annual_load"
        assert finding["overrideable"] is True
        assert finding["accepted"] is False
        assert "2026" in finding["message"]
        assert "54" in finding["message"]

    def test_multiple_overloaded_years_produce_one_finding_each(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001", 30),
            (2026, "Term 2", "TEST1002", 24),
            (2027, "Term 1", "TEST2001", 18),
            (2027, "Term 2", "TEST2002", 18),
            (2027, "Term 3", "TEST2003", 18),
        )
        courses = extract_scheduled_courses(plan)
        findings = validate_annual_loads(courses)
        assert [f["failure_id"] for f in findings] == [
            "annual-load:2026",
            "annual-load:2027",
        ]

    def test_periods_within_same_year_are_aggregated(self) -> None:
        plan = self._make_plan(
            (2026, "Summer Term", "TEST1001", 12),
            (2026, "Term 1", "TEST1002", 18),
            (2026, "Winter Term", "TEST1003", 12),
            (2026, "Term 3", "TEST1004", 12),
        )
        courses = extract_scheduled_courses(plan)
        findings = validate_annual_loads(courses)
        assert len(findings) == 1
        assert findings[0]["failure_id"] == "annual-load:2026"

    def test_findings_appear_in_validate_plan_prerequisites_detailed(self) -> None:
        plan = self._make_plan(
            (2026, "Term 1", "TEST1001", 24),
            (2026, "Term 2", "TEST1002", 30),
            (2027, "Term 1", "TEST2001", 24),
        )
        _failures, _unsupported, findings, _warnings = (
            validate_plan_prerequisites_detailed(plan)
        )
        annual = [f for f in findings if f["kind"] == "annual_load"]
        assert len(annual) == 1
        assert annual[0]["failure_id"] == "annual-load:2026"


class TestCourseEquivalences:
    def test_equivalence_satisfies_prerequisite_with_pseudo_code(self) -> None:
        plan: dict[str, Any] = {
            "courses": [
                {
                    "year": 2026,
                    "period": "Term 1",
                    "course_n": "Course 1",
                    "code": "TEST1001CEIC",
                    "uoc": 6,
                    "prerequisites": "",
                },
                {
                    "year": 2026,
                    "period": "Term 2",
                    "course_n": "Course 1",
                    "code": "TEST4001",
                    "uoc": 6,
                    "prerequisites": "TEST1001",
                },
            ]
        }

        failures, _unsupported, _findings, _warnings = (
            validate_plan_prerequisites_detailed(plan)
        )
        assert failures

        failures, unsupported, findings, warnings = (
            validate_plan_prerequisites_detailed(
                plan,
                equivalences=[
                    {"held": "TEST1001CEIC", "equivalent_to": "TEST1001"}
                ],
            )
        )

        assert not failures
        assert not unsupported
        assert not findings
        assert not warnings

    def test_equivalence_satisfies_rule_with_pseudo_code(self) -> None:
        rules: dict[str, Any] = {
            "schemaVersion": 2,
            "required": {"Level 1": ["TEST1001"]},
        }
        validated = validate_rules_config(rules)
        completed = Counter(["TEST1001CEIC"])

        failures, findings, warnings, _allocations = report_plan_detailed(
            validated,
            completed,
        )
        assert failures
        assert any(f["failure_id"] == "rule:TEST1001" for f in findings)
        assert not warnings

        expanded = _apply_equivalences(
            completed,
            [{"held": "TEST1001CEIC", "equivalent_to": "TEST1001"}],
        )
        failures, findings, warnings, _allocations = report_plan_detailed(
            validated,
            expanded,
        )

        assert not failures
        assert not findings
        assert not warnings


class TestValidateUnmatchedCourses:
    """Tests for validate_unmatched_courses."""

    def _simple_rules(self) -> dict[str, Any]:
        return {
            "schemaVersion": 2,
            "required": {
                "Core": ["TEST1001", "TEST1002"],
                "Electives": [
                    {"min": 1, "from": ["TEST2001", "TEST2002"]},
                ],
            },
        }

    def test_all_courses_matched_returns_no_findings(self) -> None:
        validated = validate_rules_config(self._simple_rules())
        completed = Counter(["TEST1001", "TEST1002", "TEST2001"])
        findings = validate_unmatched_courses(completed, validated)
        assert findings == []

    def test_unmatched_course_produces_finding(self) -> None:
        validated = validate_rules_config(self._simple_rules())
        completed = Counter(["TEST1001", "TEST1002", "TEST2001", "TEST9999"])
        findings = validate_unmatched_courses(completed, validated)
        assert len(findings) == 1
        f = findings[0]
        assert f["failure_id"] == "rule:unmatched:TEST9999"
        assert f["kind"] == "rule"
        assert f["overrideable"] is True
        assert f["accepted"] is False
        assert "TEST9999" in f["message"]

    def test_multiple_unmatched_courses_sorted_by_code(self) -> None:
        validated = validate_rules_config(self._simple_rules())
        completed = Counter(["TEST1001", "FREE9002", "FREE9001"])
        findings = validate_unmatched_courses(completed, validated)
        ids = [f["failure_id"] for f in findings]
        assert ids == ["rule:unmatched:FREE9001", "rule:unmatched:FREE9002"]

    def test_rpl_courses_excluded(self) -> None:
        rules: dict[str, Any] = {
            "schemaVersion": 2,
            "required": {"Core": ["TEST1001"]},
            "rpl": ["TEST9999"],
        }
        validated = validate_rules_config(rules)
        rpl = Counter(["TEST9999"])
        completed = Counter(["TEST1001", "TEST9999"])
        findings = validate_unmatched_courses(completed, validated, rpl_courses=rpl)
        assert findings == []

    def test_equivalence_covered_course_excluded(self) -> None:
        validated = validate_rules_config(self._simple_rules())
        # Student holds TEST1001CEIC which maps to TEST1001 (in rules) via equivalence.
        completed = Counter(["TEST1001CEIC", "TEST1002", "TEST2001"])
        equivalences: list[CourseEquivalence] = [{"held": "TEST1001CEIC", "equivalent_to": "TEST1001"}]
        findings = validate_unmatched_courses(
            completed, validated, equivalences=equivalences
        )
        assert findings == []

    def test_equivalence_not_covering_rule_code_still_unmatched(self) -> None:
        validated = validate_rules_config(self._simple_rules())
        # TEST1001CEIC maps to TEST5999 which is NOT in rules — should be unmatched.
        completed = Counter(["TEST1001", "TEST1002", "TEST2001", "TEST1001CEIC"])
        equivalences: list[CourseEquivalence] = [{"held": "TEST1001CEIC", "equivalent_to": "TEST5999"}]
        findings = validate_unmatched_courses(
            completed, validated, equivalences=equivalences
        )
        assert len(findings) == 1
        assert findings[0]["failure_id"] == "rule:unmatched:TEST1001CEIC"

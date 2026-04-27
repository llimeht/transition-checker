"""Deterministic core tests for planner_engine."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest

from transitionchecker.core import Catalogue, CatalogueEntry, CatalogueKey
from transitionchecker.planner_engine import (
    CostConfig,
    PartialPlanCourseRecord,
    PlannerCommand,
    SteeringConfig,
    TemplateConfig,
    apply_catalogue_overrides,
    assignment_course_code,
    build_plan_document,
    build_slots,
    derive_fixed_constraints,
    evaluate_plan_cost,
    extract_partial_plan_courses,
    feasible_slots_for_course,
    load_catalogue,
    load_catalogue_overrides,
    path_or_exit,
    resolve_target_end_slot,
    run_planner,
    select_required_courses,
)


def _template_config() -> TemplateConfig:
    return cast(
        TemplateConfig,
        {
            "intakes": {
                "2026 T1": {
                    "years": [
                        {
                            "enrol_year": "Year 1",
                            "year": 2026,
                            "periods": [
                                {"period": "Term 1", "max_slots": 2},
                                {"period": "Term 2", "max_slots": 2},
                            ],
                        }
                    ]
                }
            }
        },
    )


def test_build_slots_happy_path() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    assert len(slots) == 2
    assert slots[0].slot_idx == 0
    assert slots[0].canonical_period == "term 1"
    assert slots[1].slot_idx == 1
    assert slots[1].canonical_period == "term 2"


def test_build_slots_raises_for_unknown_intake() -> None:
    with pytest.raises(ValueError, match="Intake"):
        build_slots(_template_config(), "2099 T1")


def test_build_slots_accepts_normalized_intake_alias() -> None:
    template = cast(
        TemplateConfig,
        {
            "intakes": {
                "2026 Term 1": {
                    "years": [
                        {
                            "enrol_year": "Year 1",
                            "year": 2026,
                            "periods": [
                                {"period": "Term 1", "max_slots": 2},
                            ],
                        }
                    ]
                }
            }
        },
    )
    slots = build_slots(template, "2026 t1")
    assert len(slots) == 1
    assert slots[0].canonical_period == "term 1"


def test_resolve_target_end_slot_matches_exact_year_and_period() -> None:
    template = cast(
        TemplateConfig,
        {
            "intakes": {
                "2026 T1": {
                    "years": [
                        {
                            "enrol_year": "Year 1",
                            "year": 2026,
                            "periods": [
                                {"period": "Term 1", "max_slots": 2},
                                {"period": "Term 2", "max_slots": 2},
                            ],
                        },
                        {
                            "enrol_year": "Year 2",
                            "year": 2027,
                            "periods": [
                                {"period": "Term 1", "max_slots": 2},
                                {"period": "Term 2", "max_slots": 2},
                            ],
                        },
                    ]
                }
            }
        },
    )
    slots = build_slots(template, "2026 T1")
    # With intake format "YYYY period", exact match on both year and period.
    resolved = resolve_target_end_slot(slots, "2027 Term 1")
    # 2027 Term 1 should be slot 2 (after 2026 T1=0, 2026 T2=1)
    assert resolved == 2
    assert slots[resolved].calendar_year == 2027
    assert slots[resolved].canonical_period == "term 1"


def test_resolve_target_end_slot_reports_compact_available_targets() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    with pytest.raises(ValueError, match="Available targets: 2026 T1, 2026 T2"):
        resolve_target_end_slot(slots, "2026 S1")


def test_feasible_slots_for_course_matches_canonical_periods() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    offerings = {"TEST1001": ["T1"]}
    feasible = feasible_slots_for_course("TEST1001", slots, offerings)
    assert feasible == [0]


def test_select_required_courses_picks_feasible_or_branch() -> None:
    rules = {
        "required": {
            "L1": [
                {"or": ["TEST2001", "TEST2002"]},
            ]
        }
    }
    feasible_counts = {"TEST2001": 0, "TEST2002": 3}
    catalogue = Catalogue([
        CatalogueEntry(code="TEST2001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
        CatalogueEntry(code="TEST2002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
    ])

    selected = select_required_courses(rules, feasible_counts, catalogue, "UGRD")
    assert selected == ["TEST2002"]


def test_select_required_courses_prefers_or_branch_matching_fixed_courses() -> None:
    rules: dict[str, Any] = {
        "required": {
            "Level 1": [
                {
                    "or": [
                        {"and": ["CHEM1811", "CHEM1821"]},
                        {"and": ["CHEM1011", "CHEM1021"]},
                    ]
                }
            ]
        }
    }
    feasible_counts = {
        "CHEM1811": 2,
        "CHEM1821": 2,
        "CHEM1011": 4,
        "CHEM1021": 4,
    }
    catalogue = Catalogue(
        [
            CatalogueEntry(code="CHEM1811", title="Chem 1A", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
            CatalogueEntry(code="CHEM1821", title="Chem 1B", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
            CatalogueEntry(code="CHEM1011", title="Chem 1A alt", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
            CatalogueEntry(code="CHEM1021", title="Chem 1B alt", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
        ]
    )

    selected = select_required_courses(
        rules,
        feasible_counts,
        catalogue,
        "UGRD",
        fixed_course_codes={"CHEM1811", "CHEM1821"},
    )

    assert selected == ["CHEM1811", "CHEM1821"]


def test_select_required_courses_prefers_placeholder_for_min_from() -> None:
    rules: dict[str, Any] = {
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
    feasible_counts = {
        "CEICEEEE": 4,
        "TEST2001": 4,
        "TEST2002": 4,
        "TEST2003": 4,
    }
    catalogue = Catalogue(
        [
            CatalogueEntry(code="CEICEEEE", title="Elective Placeholder", career="UGRD", uoc=6, prerequisites="", level="Level 4"),
            CatalogueEntry(code="TEST2001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
            CatalogueEntry(code="TEST2002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
            CatalogueEntry(code="TEST2003", title="C", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
        ]
    )

    selected = select_required_courses(rules, feasible_counts, catalogue, "UGRD")

    assert selected == ["CEICEEEE", "CEICEEEE#2"]


def test_select_required_courses_plain_min_from_does_not_use_placeholder_logic() -> None:
    rules: dict[str, Any] = {
        "required": {
            "Electives": [
                {
                    "min": 2,
                    "from": ["TEST2001", "TEST2002", "TEST2003"],
                }
            ]
        }
    }
    feasible_counts = {
        "CEICEEEE": 10,
        "TEST2001": 3,
        "TEST2002": 2,
        "TEST2003": 1,
    }
    catalogue = Catalogue(
        [
            CatalogueEntry(code="CEICEEEE", title="Elective Placeholder", career="UGRD", uoc=6, prerequisites="", level="Level 4"),
            CatalogueEntry(code="TEST2001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
            CatalogueEntry(code="TEST2002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
            CatalogueEntry(code="TEST2003", title="C", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
        ]
    )

    selected = select_required_courses(rules, feasible_counts, catalogue, "UGRD")

    assert selected == ["TEST2001", "TEST2002"]


def test_select_required_courses_can_opt_out_of_placeholder_preference() -> None:
    rules: dict[str, Any] = {
        "required": {
            "Electives": [
                {
                    "min": 1,
                    "placeholder": "CEICeeee",
                    "from": ["TEST2001", "TEST2002"],
                }
            ]
        }
    }
    feasible_counts = {"CEICEEEE": 5, "TEST2001": 2, "TEST2002": 0}
    catalogue = Catalogue(
        [
            CatalogueEntry(code="CEICEEEE", title="Elective Placeholder", career="UGRD", uoc=6, prerequisites="", level="Level 4"),
            CatalogueEntry(code="TEST2001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
            CatalogueEntry(code="TEST2002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 2"),
        ]
    )

    selected = select_required_courses(
        rules,
        feasible_counts,
        catalogue,
        "UGRD",
        prefer_placeholders=False,
    )

    assert selected == ["TEST2001"]


def test_derive_fixed_constraints_maps_duplicate_placeholder_rows_to_instances() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    partial = [
        PartialPlanCourseRecord(
            code="CEICEEEE",
            year=2026,
            enrol_year="Year 1",
            period="Term 1",
            course_n="Course 1",
        ),
        PartialPlanCourseRecord(
            code="CEICEEEE",
            year=2026,
            enrol_year="Year 1",
            period="Term 2",
            course_n="Course 2",
        ),
    ]

    constraints = derive_fixed_constraints(
        partial,
        slots,
        ["CEICEEEE", "CEICEEEE#2"],
    )

    assert constraints.fixed_assignments == {"CEICEEEE": 0, "CEICEEEE#2": 1}


def test_derive_fixed_constraints_defers_duplicate_diagnostic_before_instance_resolution() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    partial = [
        PartialPlanCourseRecord(
            code="CEICEEEE",
            year=2026,
            enrol_year="Year 1",
            period="Term 1",
            course_n="Course 1",
        ),
        PartialPlanCourseRecord(
            code="CEICEEEE",
            year=2026,
            enrol_year="Year 1",
            period="Term 2",
            course_n="Course 2",
        ),
    ]

    constraints = derive_fixed_constraints(partial, slots)

    assert constraints.diagnostics == []
    assert constraints.fixed_assignments == {"CEICEEEE": 0}


def test_build_plan_document_strips_internal_assignment_suffixes() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    catalogue = Catalogue(
        [
            CatalogueEntry(code="CEICEEEE", title="Elective Placeholder", career="UGRD", uoc=6, prerequisites="", level="Level 4"),
        ]
    )

    document: dict[str, Any] = build_plan_document(
        {"CEICEEEE": 0, "CEICEEEE#2": 1},
        slots,
        catalogue,
        "UGRD",
        "2026 T1",
    )
    courses = cast(list[dict[str, Any]], document["courses"])

    assert [course["code"] for course in courses] == [
        "CEICEEEE",
        "CEICEEEE",
    ]
    assert all(
        assignment_course_code(course["code"]) == "CEICEEEE"
        for course in courses
    )


def test_path_or_exit_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing"):
        path_or_exit(tmp_path / "missing.json", "rules file")


def test_evaluate_plan_cost_penalizes_unplaced_courses() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    rules = {"required": {"L1": ["TEST1001"]}}
    catalogue = Catalogue([
        CatalogueEntry(code="TEST1001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
    ])
    offerings = {"TEST1001": ["Term 1"]}
    steering = SteeringConfig()

    placed = evaluate_plan_cost(
        {"TEST1001": 0},
        ["TEST1001"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
    )
    unplaced = evaluate_plan_cost(
        {},
        ["TEST1001"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
    )

    assert placed.unplaced_count == 0
    assert unplaced.unplaced_count == 1
    assert unplaced.total_cost > placed.total_cost


def test_evaluate_plan_cost_tracks_offering_and_prereq_violations() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    rules = {"required": {"L1": ["TEST1001", "TEST1002"]}}
    catalogue = Catalogue([
        CatalogueEntry(code="TEST1001", title="A", career="UGRD", uoc=6, prerequisites="TEST1002", level="Level 1"),
        CatalogueEntry(code="TEST1002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
    ])
    offerings = {
        "TEST1001": ["Term 1"],
        "TEST1002": ["Term 2"],
    }
    steering = SteeringConfig()

    # TEST1001 in slot 0 (Term 1) requires TEST1002, but TEST1002 is in slot 1 (Term 2)
    details = evaluate_plan_cost(
        {"TEST1001": 0, "TEST1002": 1},
        ["TEST1001", "TEST1002"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
    )

    assert details.offering_violations == 0
    assert details.prereq_violations >= 1


def test_derive_fixed_constraints_locks_period_and_allows_only_fixed_courses() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    partial = [
        PartialPlanCourseRecord(
            code="TEST1001",
            year=2026,
            enrol_year="Year 1",
            period="Term 1",
            course_n="Course 1",
        )
    ]

    constraints = derive_fixed_constraints(partial, slots, {"TEST1001", "TEST1002"})
    offerings = {
        "TEST1001": ["Term 1", "Term 2"],
        "TEST1002": ["Term 1", "Term 2"],
    }

    fixed_feasible = feasible_slots_for_course(
        "TEST1001", slots, offerings, constraints
    )
    other_feasible = feasible_slots_for_course(
        "TEST1002", slots, offerings, constraints
    )

    assert constraints.fixed_assignments == {"TEST1001": 0}
    assert constraints.locked_slots == {0}
    assert fixed_feasible == [0]
    assert other_feasible == [1]


def test_extract_partial_plan_courses_ignores_prerequisites_field() -> None:
    partial_plan: dict[str, object] = {
        "courses": [
            {
                "code": "TEST1001",
                "year": 2026,
                "enrol_year": "Year 1",
                "period": "Term 1",
                "course_n": "Course 1",
                "prerequisites": "TEST9999",
            }
        ]
    }

    extracted = extract_partial_plan_courses(partial_plan)

    assert extracted == [
        PartialPlanCourseRecord(
            code="TEST1001",
            year=2026,
            enrol_year="Year 1",
            period="Term 1",
            course_n="Course 1",
        )
    ]


def test_evaluate_plan_cost_counts_fixed_constraint_violations() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    rules = {"required": {"L1": ["TEST1001", "TEST1002"]}}
    catalogue = Catalogue([
        CatalogueEntry(code="TEST1001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
        CatalogueEntry(code="TEST1002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
    ])
    offerings = {
        "TEST1001": ["Term 1", "Term 2"],
        "TEST1002": ["Term 1", "Term 2"],
    }
    steering = SteeringConfig()
    constraints = derive_fixed_constraints(
        [
            PartialPlanCourseRecord(
                code="TEST1001",
                year=2026,
                enrol_year="Year 1",
                period="Term 1",
                course_n="Course 1",
            )
        ],
        slots,
        {"TEST1001", "TEST1002"},
    )

    obeys = evaluate_plan_cost(
        {"TEST1001": 0, "TEST1002": 1},
        ["TEST1001", "TEST1002"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
        constraints,
    )
    violates = evaluate_plan_cost(
        {"TEST1001": 1, "TEST1002": 0},
        ["TEST1001", "TEST1002"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
        constraints,
    )

    assert obeys.fixed_constraint_violations == 0
    assert violates.fixed_constraint_violations >= 2
    assert violates.total_cost > obeys.total_cost


def test_evaluate_plan_cost_penalizes_courses_after_target_end_slot() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    rules = {"required": {"L1": ["TEST1001", "TEST1002"]}}
    catalogue = Catalogue([
        CatalogueEntry(code="TEST1001", title="A", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
        CatalogueEntry(code="TEST1002", title="B", career="UGRD", uoc=6, prerequisites="", level="Level 1"),
    ])
    offerings = {
        "TEST1001": ["Term 1"],
        "TEST1002": ["Term 2"],
    }
    steering = SteeringConfig(cost=CostConfig(post_target_period_penalty=120.0))

    without_target = evaluate_plan_cost(
        {"TEST1001": 0, "TEST1002": 1},
        ["TEST1001", "TEST1002"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
    )
    with_target = evaluate_plan_cost(
        {"TEST1001": 0, "TEST1002": 1},
        ["TEST1001", "TEST1002"],
        slots,
        offerings,
        catalogue,
        "UGRD",
        rules,
        steering,
        "2026 T1",
        target_end_slot_idx=0,
    )

    assert without_target.post_target_period_count == 0
    assert with_target.post_target_period_count == 1
    assert abs(with_target.total_cost - (without_target.total_cost + 120.0)) < 1e-9


# ---------------------------------------------------------------------------
# Catalogue override tests
# ---------------------------------------------------------------------------


def test_apply_catalogue_overrides_patches_prerequisite() -> None:
    raw = Catalogue(
        [
            CatalogueEntry(
                code="CEIC3000",
                title="Some Course",
                career="UGRD",
                uoc=6,
                prerequisites="enrolled in program 4501",
            )
        ]
    )
    overrides = {
        CatalogueKey("CEIC3000", "UGRD"): {
            "prerequisites": "CEIC2000 AND CEIC2010",
            "reason": "handbook text ambiguous",
            "date": "2026-04-22",
        }
    }
    result = apply_catalogue_overrides(raw, overrides)
    entry = result[CatalogueKey("CEIC3000", "UGRD")]
    assert entry.prerequisites == "CEIC2000 AND CEIC2010"
    assert entry.title == "Some Course"


def test_apply_catalogue_overrides_does_not_mutate_raw() -> None:
    raw = Catalogue(
        [
            CatalogueEntry(
                code="CEIC3000",
                title="CEIC3000",
                career="UGRD",
                prerequisites="original",
            )
        ]
    )
    overrides = {
        CatalogueKey("CEIC3000", "UGRD"): {
            "prerequisites": "CEIC2000",
            "reason": "test",
            "date": "2026-04-22",
        }
    }
    result = apply_catalogue_overrides(raw, overrides)
    assert raw[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "original"
    assert result[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "CEIC2000"


def test_apply_catalogue_overrides_adds_course_not_in_raw() -> None:
    raw = Catalogue([])
    overrides: dict[CatalogueKey, dict[str, object]] = {
        CatalogueKey("CEIC9999", "UGRD"): {
            "title": "Synthetic elective",
            "career": "UGRD",
            "uoc": 6,
            "prerequisites": "CEIC1000",
            "reason": "new",
            "date": "2026-04-22",
        }
    }
    result = apply_catalogue_overrides(raw, overrides)
    assert len(result) == 1
    entry = result[CatalogueKey("CEIC9999", "UGRD")]
    assert entry.title == "Synthetic elective"
    assert entry.prerequisites == "CEIC1000"


def test_apply_catalogue_overrides_empty_overrides_returns_same() -> None:
    raw = Catalogue(
        [
            CatalogueEntry(
                code="CEIC3000",
                title="CEIC3000",
                career="UGRD",
                prerequisites="original",
            )
        ]
    )
    result = apply_catalogue_overrides(raw, {})
    assert result is raw


def test_load_catalogue_overrides_absent_file_returns_empty(tmp_path: Path) -> None:
    result = load_catalogue_overrides(tmp_path / "nonexistent_overrides.json")
    assert result == {}


def test_load_catalogue_overrides_reads_and_normalizes(tmp_path: Path) -> None:
    import json

    overrides_file = tmp_path / "catalogue_overrides.json"
    overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "ceic3000",
                    "career": "UGRD",
                    "prerequisites": "CEIC2000",
                    "reason": "test",
                    "date": "2026-04-22",
                }
            ]
        ),
        encoding="utf-8",
    )
    result = load_catalogue_overrides(overrides_file)
    key = CatalogueKey("CEIC3000", "UGRD")
    assert key in result
    assert result[key]["prerequisites"] == "CEIC2000"


def test_load_catalogue_applies_overrides_by_default(tmp_path: Path) -> None:
    import json

    catalogue_file = tmp_path / "catalogue.json"
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "title": "C",
                    "uoc": 6,
                    "prerequisites": "enrolled in program",
                }
            ]
        ),
        encoding="utf-8",
    )
    overrides_file = tmp_path / "catalogue_overrides.json"
    overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "prerequisites": "CEIC2000",
                    "reason": "test override",
                    "date": "2026-04-22",
                }
            ]
        ),
        encoding="utf-8",
    )
    catalogue = load_catalogue(catalogue_file)
    assert catalogue[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "CEIC2000"


def test_load_catalogue_apply_overrides_false_ignores_file(tmp_path: Path) -> None:
    import json

    catalogue_file = tmp_path / "catalogue.json"
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "title": "C",
                    "uoc": 6,
                    "prerequisites": "enrolled in program",
                }
            ]
        ),
        encoding="utf-8",
    )
    overrides_file = tmp_path / "catalogue_overrides.json"
    overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "prerequisites": "CEIC2000",
                    "reason": "test",
                    "date": "2026-04-22",
                }
            ]
        ),
        encoding="utf-8",
    )
    catalogue = load_catalogue(catalogue_file, apply_overrides=False)
    # original, unparseable handbook text is preserved
    assert catalogue[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "enrolled in program"


def test_load_catalogue_no_overrides_file_is_silent(tmp_path: Path) -> None:
    import json

    catalogue_file = tmp_path / "catalogue.json"
    catalogue_file.write_text(
        json.dumps([
            {
                "code": "CEIC3000",
                "career": "UGRD",
                "title": "C",
                "uoc": 6,
                "prerequisites": "CEIC1000",
            }
        ]),
        encoding="utf-8",
    )
    # No overrides file present — must not raise
    catalogue = load_catalogue(catalogue_file)
    assert catalogue[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "CEIC1000"


def test_load_catalogue_applies_extra_override_paths_last(tmp_path: Path) -> None:
    import json

    catalogue_file = tmp_path / "catalogue.json"
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "title": "C",
                    "uoc": 6,
                    "prerequisites": "HANDBOOK1000",
                }
            ]
        ),
        encoding="utf-8",
    )
    root_overrides_file = tmp_path / "catalogue_overrides.json"
    root_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "prerequisites": "ROOT1000",
                }
            ]
        ),
        encoding="utf-8",
    )
    school_dir = tmp_path / "CEIC"
    school_dir.mkdir()
    school_overrides_file = school_dir / "catalogue_overrides.json"
    school_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "UGRD",
                    "prerequisites": "SCHOOL1000",
                }
            ]
        ),
        encoding="utf-8",
    )

    catalogue = load_catalogue(
        catalogue_file,
        override_paths=[school_overrides_file],
    )

    assert catalogue[CatalogueKey("CEIC3000", "UGRD")].prerequisites == "SCHOOL1000"


def test_load_catalogue_adds_override_only_entries_from_extra_paths(tmp_path: Path) -> None:
    import json

    catalogue_file = tmp_path / "catalogue.json"
    catalogue_file.write_text(json.dumps([]), encoding="utf-8")
    school_dir = tmp_path / "CEIC"
    school_dir.mkdir()
    school_overrides_file = school_dir / "catalogue_overrides.json"
    school_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "GenEd1",
                    "career": "Undergraduate",
                    "title": "Gen Ed 1",
                    "uoc": 6,
                    "prerequisites": ".",
                }
            ]
        ),
        encoding="utf-8",
    )

    catalogue = load_catalogue(
        catalogue_file,
        override_paths=[school_overrides_file],
    )

    assert catalogue[CatalogueKey("GENED1", "Undergraduate")].title == "Gen Ed 1"


def test_run_planner_uses_rules_career_for_catalogue_validation(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    offerings_file = tmp_path / "offerings.json"
    catalogue_file = tmp_path / "catalogue.json"
    template_file = tmp_path / "templates.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "pgrd",
                "required": {"Level 1": ["TEST9001"]},
            }
        ),
        encoding="utf-8",
    )
    offerings_file.write_text(
        json.dumps({"TEST9001": ["Term 1"]}),
        encoding="utf-8",
    )
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST9001",
                    "title": "Undergraduate only",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                    "level": "Level 9",
                }
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(json.dumps(_template_config()), encoding="utf-8")

    command = PlannerCommand(
        rule_path=rules_file,
        intake="2026 T1",
        offerings_path=offerings_file,
        catalogue_path=catalogue_file,
        template_config_path=template_file,
        steering_path=tmp_path / "missing-steering.json",
    )

    with pytest.raises(ValueError, match="career 'Postgraduate'"):
        run_planner(command, stdout=StringIO(), stderr=StringIO())


def test_run_planner_uses_output_dir_catalogue_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules_file = tmp_path / "rules.json"
    offerings_file = tmp_path / "offerings.json"
    catalogue_file = tmp_path / "catalogue.json"
    template_file = tmp_path / "templates.json"
    output_dir = tmp_path / "CEIC"
    output_dir.mkdir()

    for path in (rules_file, offerings_file, catalogue_file, template_file):
        path.write_text("{}", encoding="utf-8")

    captured: dict[str, object] = {}

    class _StopPlanner(Exception):
        pass

    def fake_load_catalogue(
        path: Path,
        *,
        apply_overrides: bool = True,
        override_paths: object = (),
    ) -> Catalogue:
        captured["path"] = path
        captured["apply_overrides"] = apply_overrides
        captured["override_paths"] = list(cast(list[Path], override_paths))
        raise _StopPlanner()

    def fake_load_rules(_path: Path) -> dict[str, object]:
        return {}

    def fake_load_offerings(_path: Path) -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr("transitionchecker.planner_engine.load_rules", fake_load_rules)
    monkeypatch.setattr(
        "transitionchecker.planner_engine.load_offerings", fake_load_offerings
    )
    monkeypatch.setattr("transitionchecker.planner_engine.load_catalogue", fake_load_catalogue)

    command = PlannerCommand(
        rule_path=rules_file,
        intake="2026 T1",
        offerings_path=offerings_file,
        catalogue_path=catalogue_file,
        template_config_path=template_file,
        steering_path=tmp_path / "missing-steering.json",
        output_path=output_dir / "result.csv",
    )

    with pytest.raises(_StopPlanner):
        run_planner(command, stdout=StringIO(), stderr=StringIO())

    assert captured["path"] == catalogue_file
    assert captured["apply_overrides"] is True
    assert captured["override_paths"] == [output_dir / "catalogue_overrides.json"]

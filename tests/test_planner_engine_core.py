"""Deterministic core tests for planner_engine."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from transitionchecker.planner_engine import (
    CourseMeta,
    SteeringConfig,
    TemplateConfig,
    build_slots,
    evaluate_plan_cost,
    feasible_slots_for_course,
    path_or_exit,
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
    catalogue = {
        "TEST2001": CourseMeta(title="A", uoc=6, prerequisites="", level="Level 2"),
        "TEST2002": CourseMeta(title="B", uoc=6, prerequisites="", level="Level 2"),
    }

    selected = select_required_courses(rules, feasible_counts, catalogue)
    assert selected == ["TEST2002"]


def test_path_or_exit_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing"):
        path_or_exit(tmp_path / "missing.json", "rules file")


def test_evaluate_plan_cost_penalizes_unplaced_courses() -> None:
    slots = build_slots(_template_config(), "2026 T1")
    rules = {"required": {"L1": ["TEST1001"]}}
    catalogue = {
        "TEST1001": CourseMeta(title="A", uoc=6, prerequisites="", level="Level 1"),
    }
    offerings = {"TEST1001": ["Term 1"]}
    steering = SteeringConfig()

    placed = evaluate_plan_cost(
        {"TEST1001": 0},
        ["TEST1001"],
        slots,
        offerings,
        catalogue,
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
    catalogue = {
        "TEST1001": CourseMeta(
            title="A", uoc=6, prerequisites="TEST1002", level="Level 1"
        ),
        "TEST1002": CourseMeta(title="B", uoc=6, prerequisites="", level="Level 1"),
    }
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
        rules,
        steering,
        "2026 T1",
    )

    assert details.offering_violations == 0
    assert details.prereq_violations >= 1

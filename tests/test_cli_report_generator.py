"""Behavior tests for report-generator CLI."""

from __future__ import annotations

import json
from pathlib import Path

from transitionchecker.cli import report_generator_cli


def _make_plan(path: Path, courses: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"courses": courses}, indent=2), encoding="utf-8")


def _make_validation_report(path: Path, results: list[dict[str, object]]) -> None:
    payload: dict[str, object] = {
        "excel_file": "mapping.xlsx",
        "generated_at_utc": "2026-07-13T00:00:00+00:00",
        "report_file": str(path),
        "results": results,
        "summary": {"total_plan_files": len(results)},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_report_generates_html_and_applies_plan_filter(tmp_path: Path) -> None:
    plan_ok = tmp_path / "CEICAH3707_2024_T2.json"
    _make_plan(
        plan_ok,
        [
            {"code": "COMP1511", "year": 2024, "period": "Term 2"},
            {"code": "COMP2521", "year": 2028, "period": "Semester 2"},
        ],
    )

    plan_other = tmp_path / "FOODJH3061_2024_T2.json"
    _make_plan(
        plan_other,
        [
            {"code": "FOOD1001", "year": 2024, "period": "T2"},
            {"code": "FOOD4001", "year": 2028, "period": "S2"},
        ],
    )

    report_a = tmp_path / "A_validation_results.json"
    report_b = tmp_path / "B_validation_results.json"

    result_common: dict[str, object] = {
        "rule_file": "CEICAH3707-2024-2029.json",
        "program_code": "CEICAH3707",
        "program_metadata": {"plan_description": "Computer Engineering"},
        "findings": [{"failure_id": "annual-load:2026", "message": "overload", "accepted": True}],
        "offering_violations": [{"error_type": "period_not_allowed", "course_code": "COMP2521"}],
        "notes": {
            "graduate_outcome": "Student graduates late",
            "adjustment_type": "Adjustment within standard load",
            "for_reviewers": ["Reviewer note"],
            "for_students": ["Student note"],
            "impact_assessment_status": "COMPLETE",
        },
    }

    _make_validation_report(
        report_a,
        [
            {
                "plan_file": str(plan_ok),
                "status": "accepted",
                **result_common,
            }
        ],
    )
    _make_validation_report(
        report_b,
        [
            {
                "plan_file": str(plan_other),
                "status": "failed",
                **result_common,
            }
        ],
    )

    output = tmp_path / "report.html"
    code = report_generator_cli.main(
        [
            "report",
            str(report_a),
            str(report_b),
            "--filter",
            "*3707*",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    html = output.read_text(encoding="utf-8")
    assert "CEICAH3707" in html
    assert "Computer Engineering" in html
    assert "FOODJH3061" not in html
    assert "ACCEPTED" in html
    assert "Plan<br>description" in html
    assert "Duration<br>(years)" in html
    assert "COMPLETE" in html
    assert "simple-datatables" in html
    assert "(accepted) [annual-load:2026] overload" in html
    assert "A_validation_results.json" in html
    assert str(report_a) not in html


def test_report_hides_skipped_placeholder_status(tmp_path: Path) -> None:
    plan = tmp_path / "CEICAH3707_2024_T2.json"
    _make_plan(
        plan,
        [
            {"code": "COMP1511", "year": 2024, "period": "T2"},
            {"code": "COMP2521", "year": 2028, "period": "S2"},
        ],
    )

    report = tmp_path / "validation_results.json"
    _make_validation_report(
        report,
        [
            {
                "plan_file": str(plan),
                "status": "skipped_placeholder",
                "findings": [],
                "offering_violations": [],
                "notes": {},
            }
        ],
    )

    output = tmp_path / "report.html"
    code = report_generator_cli.main(
        [
            "report",
            str(report),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    html = output.read_text(encoding="utf-8")
    assert "Rows: 0" in html


def test_pack_subcommand_returns_reserved_code(tmp_path: Path) -> None:
    plan = tmp_path / "CEICAH3707_2024_T2.json"
    _make_plan(plan, [])

    code = report_generator_cli.main(
        ["pack", str(plan), "--output", str(tmp_path / "pack.zip")]
    )

    assert code == 2


def test_report_derives_pending_impact_status_when_not_assessed(tmp_path: Path) -> None:
    plan = tmp_path / "CEICAH3707_2024_T2.json"
    _make_plan(
        plan,
        [
            {"code": "COMP1511", "year": 2024, "period": "T2"},
            {"code": "COMP2521", "year": 2028, "period": "S2"},
        ],
    )

    report = tmp_path / "validation_results.json"
    _make_validation_report(
        report,
        [
            {
                "plan_file": str(plan),
                "status": "accepted",
                "findings": [],
                "offering_violations": [],
                "notes": {
                    "graduate_outcome": "Not yet assessed",
                    "adjustment_type": "Adjustment within standard load",
                    "impact_assessment_status": "COMPLETE",
                },
            }
        ],
    )

    output = tmp_path / "report.html"
    code = report_generator_cli.main(
        [
            "report",
            str(report),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    html = output.read_text(encoding="utf-8")
    assert "PENDING" in html
    assert "COMPLETE" not in html

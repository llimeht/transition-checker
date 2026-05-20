"""Behavior tests for degree_rules CLI."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any
from typing import TextIO

import pytest

from transitionchecker.cli import degree_rules_cli
from transitionchecker.rules_engine import RulesCommand, run_rules_command


def test_requires_rules_file_argument() -> None:
    with pytest.raises(SystemExit) as exc:
        degree_rules_cli.main([])
    assert exc.value.code == 2


def test_main_builds_rules_command_and_returns_runner_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: Any, *, stdout: Any, stderr: Any) -> int:
        captured["command"] = command
        return 7

    monkeypatch.setattr(degree_rules_cli, "run_rules_command", fake_run)

    exit_code = degree_rules_cli.main(
        [
            "rules/sample.json",
            "--json-output",
            "--plan",
            "plans/plan.json",
            "--plan-report-json",
            "--plan-report-allocations",
            "-v",
        ]
    )

    assert exit_code == 7
    command = captured["command"]
    assert command.rules_file == Path("rules/sample.json")
    assert command.json_output is True
    assert command.plan_file == Path("plans/plan.json")
    assert command.catalogue_file == Path("plans/catalogue.json")
    assert command.plan_report_json is True
    assert command.plan_report_allocations is True
    assert command.render_rules_text is False
    assert command.show_plan_warnings is True


def test_render_rules_text_when_verbose_without_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: Any, *, stdout: Any, stderr: Any) -> int:
        captured["command"] = command
        return 0

    monkeypatch.setattr(degree_rules_cli, "run_rules_command", fake_run)

    exit_code = degree_rules_cli.main(["rules/sample.json", "-v"])
    assert exit_code == 0
    assert captured["command"].render_rules_text is True


def test_configure_logging_receives_verbosity(monkeypatch: pytest.MonkeyPatch) -> None:
    levels: list[int] = []

    def fake_configure(level: int) -> None:
        levels.append(level)

    def fake_run_noop(_command: RulesCommand, *, stdout: TextIO, stderr: TextIO) -> int:
        return 0

    monkeypatch.setattr(degree_rules_cli, "configure_logging", fake_configure)
    monkeypatch.setattr(degree_rules_cli, "run_rules_command", fake_run_noop)

    exit_code = degree_rules_cli.main(["rules/sample.json", "-v", "-v"])
    assert exit_code == 0
    assert levels == [2]


def test_plan_report_json_includes_status_findings_warnings(
    tmp_path: Path,
    rules_without_subset_ids: dict[str, Any],
    plan_for_subset_rules: dict[str, Any],
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    rules_file.write_text(json.dumps(rules_without_subset_ids), encoding="utf-8")
    plan_file.write_text(json.dumps(plan_for_subset_rules), encoding="utf-8")

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert isinstance(payload["findings"], list)
    assert isinstance(payload["warnings"], list)
    assert "valid" in payload
    assert "rule_failures" in payload
    assert "prerequisite_failures" in payload
    assert "unsupported_prerequisites" in payload
    assert any(w["code"] == "missing_rule_id" for w in payload["warnings"])
    assert "bucket_allocations" not in payload
    assert "shared_course_allocations" not in payload
    assert "unmatched_courses" not in payload


def test_plan_report_json_includes_bucket_allocations_when_requested(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
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
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 3",
                        "course_n": "Course 3",
                        "code": "TEST2002",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
            plan_report_allocations=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert "bucket_allocations" in payload
    assert "shared_course_allocations" in payload
    assert "unmatched_courses" in payload
    assert payload["bucket_allocations"]["Category A"][0]["allocated_courses"] == [
        "TEST1001"
    ]
    assert payload["bucket_allocations"]["Category B"][0]["allocated_courses"] == [
        "TEST1001"
    ]
    assert payload["bucket_allocations"]["Electives"][0]["allocated_courses"] == [
        "TEST2001",
        "TEST2002",
    ]
    assert payload["bucket_allocations"]["Electives"][1]["reason"] == (
        "over-double-count-limit"
    )
    assert payload["bucket_allocations"]["Electives"][1]["placeholder"] == "CEICEEEE"
    assert payload["bucket_allocations"]["Electives"][1]["allocated_courses"] == []
    assert payload["shared_course_allocations"]["double_counted"] == [
        {
            "course": "TEST1001",
            "allocation_count": 2,
            "is_shared": True,
            "allocated_to": [
                {
                    "bucket": "Category A",
                    "clause": 1,
                    "rule": "TEST1001",
                },
                {
                    "bucket": "Category B",
                    "clause": 1,
                    "rule": "TEST1001",
                },
            ],
        }
    ]
    assert payload["shared_course_allocations"]["over_double_count_limit"] == [
        {
            "placeholder": "CEICEEEE",
            "from": ["TEST1001"],
            "triggered_by": ["TEST1001"],
            "extra_required": 1,
            "selector_allocation_counts": {"TEST1001": 2},
            "selector_allocated_to": {
                "TEST1001": [
                    {
                        "bucket": "Category A",
                        "clause": 1,
                        "rule": "TEST1001",
                    },
                    {
                        "bucket": "Category B",
                        "clause": 1,
                        "rule": "TEST1001",
                    },
                ]
            },
            "extra_allocated_courses": [],
            "allocated_to": [
                {
                    "bucket": "Electives",
                    "clause": 1,
                    "rule": "AT LEAST 1 OF (TEST1001, TEST2001, TEST2002) [placeholder CEICEEEE]",
                }
            ],
        }
    ]
    assert payload["unmatched_courses"] == []


def test_plan_report_allocations_include_over_double_count_limit_courses(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                    "Electives": [
                        {
                            "min": 1,
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
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 3",
                        "course_n": "Course 3",
                        "code": "TEST2002",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
            plan_report_allocations=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["bucket_allocations"]["Electives"][1]["allocated_courses"] == [
        "TEST2002"
    ]
    assert payload["shared_course_allocations"]["over_double_count_limit"] == [
        {
            "placeholder": "CEICEEEE",
            "from": ["TEST1001"],
            "triggered_by": ["TEST1001"],
            "extra_required": 1,
            "selector_allocation_counts": {"TEST1001": 2},
            "selector_allocated_to": {
                "TEST1001": [
                    {
                        "bucket": "Category A",
                        "clause": 1,
                        "rule": "TEST1001",
                    },
                    {
                        "bucket": "Category B",
                        "clause": 1,
                        "rule": "TEST1001",
                    },
                ]
            },
            "extra_allocated_courses": ["TEST2002"],
            "allocated_to": [
                {
                    "bucket": "Electives",
                    "clause": 1,
                    "rule": "AT LEAST 1 OF (TEST1001, TEST2001, TEST2002) [placeholder CEICEEEE]",
                }
            ],
        }
    ]


def test_plan_report_allocations_preserve_partial_matches_for_failed_bucket(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {
                    "Foundation": ["TEST1001", "TEST1002"],
                    "Electives": [
                        {
                            "min": 3,
                            "placeholder": "TESTEEEE",
                            "from": ["TEST1001", "TEST1002", "TEST2001"],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "TEST1002",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 3",
                        "course_n": "Course 3",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
            plan_report_allocations=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert payload["bucket_allocations"]["Electives"][0]["allocated_courses"] == [
        "TEST2001"
    ]


def test_plan_report_allocations_include_unmatched_courses(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {
                    "Level 1": ["TEST1001"],
                }
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "TEST9999",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
            plan_report_allocations=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["unmatched_courses"] == ["TEST9999"]
    assert payload["shared_course_allocations"] == {
        "double_counted": [],
        "over_double_count_limit": [],
    }


def test_plan_warnings_hidden_without_verbose(
    tmp_path: Path,
    rules_without_subset_ids: dict[str, Any],
    plan_for_subset_rules: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"
    rules_payload: dict[str, Any] = dict(rules_without_subset_ids)
    rules_payload["career"] = "Undergraduate"
    rules_file.write_text(json.dumps(rules_payload), encoding="utf-8")
    plan_file.write_text(json.dumps(plan_for_subset_rules), encoding="utf-8")
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST1001",
                    "title": "Foundation",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
                {
                    "code": "TEST3001",
                    "title": "Advanced A",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    exit_code = degree_rules_cli.main(
        [str(rules_file), "--plan", str(plan_file), "--catalogue", str(catalogue_file)],
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Plan has 4 warning(s): use -v to show details" in captured.out
    assert "[missing_rule_id]" not in captured.out


def test_plan_warnings_visible_with_verbose(
    tmp_path: Path,
    rules_without_subset_ids: dict[str, Any],
    plan_for_subset_rules: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"
    rules_payload: dict[str, Any] = dict(rules_without_subset_ids)
    rules_payload["career"] = "Undergraduate"
    rules_file.write_text(json.dumps(rules_payload), encoding="utf-8")
    plan_file.write_text(json.dumps(plan_for_subset_rules), encoding="utf-8")
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST1001",
                    "title": "Foundation",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
                {
                    "code": "TEST3001",
                    "title": "Advanced A",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    exit_code = degree_rules_cli.main(
        [
            str(rules_file),
            "--plan",
            str(plan_file),
            "--catalogue",
            str(catalogue_file),
            "-v",
        ],
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Plan has 4 warning(s):" in captured.out
    assert "[missing_rule_id]" in captured.out


def test_plan_validation_uses_catalogue_prerequisites(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "Undergraduate",
                "required": {"Level 1": ["TEST1001", "TEST2001"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 2",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST1001",
                    "title": "Prerequisite",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
                {
                    "code": "TEST2001",
                    "title": "Dependent",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "TEST1001",
                },
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert any("TEST2001" in failure for failure in payload["prerequisite_failures"])


def test_plan_validation_uses_rules_rpl_for_prerequisites(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {"Level 1": ["TEST2001"]},
                "rpl": ["TEST1001"],
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "PASS"
    assert payload["prerequisite_failures"] == []
    assert payload["rule_failures"] == []


def test_plan_validation_uses_plan_dir_catalogue_overrides(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    school_dir = tmp_path / "CEIC"
    school_dir.mkdir()
    plan_file = school_dir / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"
    school_overrides_file = school_dir / "catalogue_overrides.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "Undergraduate",
                "required": {"Level 1": ["TEST1001", "TEST2001"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 2",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST1001",
                    "title": "Prerequisite",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
                {
                    "code": "TEST2001",
                    "title": "Dependent",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "TEST1001",
                },
            ]
        ),
        encoding="utf-8",
    )
    school_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST2001",
                    "career": "Undergraduate",
                    "prerequisites": "",
                }
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "PASS"
    assert payload["prerequisite_failures"] == []


def test_plan_validation_uses_override_only_courses_from_plan_dir_catalogue_overrides(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    school_dir = tmp_path / "CEIC"
    school_dir.mkdir()
    plan_file = school_dir / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"
    school_overrides_file = school_dir / "catalogue_overrides.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "Undergraduate",
                "required": {"Level 1": ["GENED1"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "GENED1",
                        "uoc": 6,
                        "prerequisites": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(json.dumps([]), encoding="utf-8")
    school_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "GenEd1",
                    "title": "Gen Ed 1",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": ".",
                }
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "PASS"
    assert payload["prerequisite_failures"] == []


def test_plan_validation_counts_repeated_placeholder_rows_for_placeholder_clause(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    school_dir = tmp_path / "CEIC"
    school_dir.mkdir()
    plan_file = school_dir / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"
    school_overrides_file = school_dir / "catalogue_overrides.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "Undergraduate",
                "required": {
                    "Electives": [
                        {
                            "min": 2,
                            "placeholder": "CEICeeee",
                            "from": ["TEST2001", "TEST2002", "TEST2003"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "CEICeeee",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "CEICeeee",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(json.dumps([]), encoding="utf-8")
    school_overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEICeeee",
                    "title": "CEIC Elective Placeholder",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": ".",
                }
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "PASS"
    assert payload["rule_failures"] == []


def test_plan_validation_single_use_course_fails_later_overlap_clause(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {
                    "Category A": ["TEST1001"],
                    "Category B": ["TEST1001"],
                }
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert any("[Category B]" in failure for failure in payload["rule_failures"])


def test_plan_validation_over_double_count_limit_reports_extra_obligation(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
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
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST1001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 2",
                        "course_n": "Course 2",
                        "code": "TEST2001",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                    {
                        "year": 2026,
                        "period": "Term 3",
                        "course_n": "Course 3",
                        "code": "TEST2002",
                        "uoc": 6,
                        "prerequisites": ".",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert any(
        "over-double-count-limit requires 1 additional elective(s)"
        in failure
        for failure in payload["rule_failures"]
    )


def test_plan_output_shows_unsupported_syntax_separately(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"

    rules_file.write_text(
        json.dumps(
            {
                "required": {"Level 1": ["TEST6001"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    text = out.getvalue()
    assert "Plan has 1 unsupported syntax expression(s):" in text
    assert "[unsupported-syntax:TEST6001>TEST1001 / TEST1002]" in text
    assert "warning(s)" not in text


def test_plan_validation_uses_rules_career_for_duplicate_catalogue_codes(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "pgrd",
                "required": {"Level 1": ["TEST9001"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST9001",
                        "uoc": 6,
                        "prerequisites": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST9001",
                    "title": "UG version",
                    "career": "Undergraduate",
                    "uoc": 6,
                    "prerequisites": "TEST0001",
                },
                {
                    "code": "TEST9001",
                    "title": "PG version",
                    "career": "Postgraduate",
                    "uoc": 6,
                    "prerequisites": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "PASS"
    assert payload["prerequisite_failures"] == []


def test_plan_validation_fails_when_rules_career_missing_from_catalogue(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    catalogue_file = tmp_path / "catalogue.json"

    rules_file.write_text(
        json.dumps(
            {
                "career": "Undergraduate",
                "required": {"Level 1": ["TEST9001"]},
            }
        ),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps(
            {
                "courses": [
                    {
                        "year": 2026,
                        "period": "Term 1",
                        "course_n": "Course 1",
                        "code": "TEST9001",
                        "uoc": 6,
                        "prerequisites": ".",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogue_file.write_text(
        json.dumps(
            [
                {
                    "code": "TEST9001",
                    "title": "PG only",
                    "career": "Postgraduate",
                    "uoc": 6,
                    "prerequisites": "",
                }
            ]
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            catalogue_file=catalogue_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    assert "Catalogue contains no entries for career 'Undergraduate'" in err.getvalue()


def test_accepted_status_exits_zero_with_overrides(
    tmp_path: Path,
    rules_with_subset_ids: dict[str, Any],
    plan_for_subset_rules: dict[str, Any],
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    rules_file.write_text(json.dumps(rules_with_subset_ids), encoding="utf-8")
    plan_file.write_text(json.dumps(plan_for_subset_rules), encoding="utf-8")

    override_file = tmp_path / "plan.degree_rules_overrides.json"
    override_file.write_text(
        json.dumps(
            {
                "overrides": [
                    {"failure_id": "rule:PATHWAY_TEST200x"},
                    {"failure_id": "rule:ADVANCED_POOL_MIN2"},
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "ACCEPTED"
    assert payload["valid"] is True
    rule_findings = [f for f in payload["findings"] if f["kind"] == "rule"]
    assert rule_findings
    assert all(f["accepted"] is True for f in rule_findings)


def test_override_not_allowed_warning_for_unnamed_subset(
    tmp_path: Path,
    rules_without_subset_ids: dict[str, Any],
    plan_for_subset_rules: dict[str, Any],
) -> None:
    rules_file = tmp_path / "rules.json"
    plan_file = tmp_path / "plan.json"
    rules_file.write_text(json.dumps(rules_without_subset_ids), encoding="utf-8")
    plan_file.write_text(json.dumps(plan_for_subset_rules), encoding="utf-8")

    override_file = tmp_path / "plan.degree_rules_overrides.json"
    override_file.write_text(
        json.dumps(
            {
                "overrides": [
                    {"failure_id": "rule:unnamed:level-1:2"},
                ]
            }
        ),
        encoding="utf-8",
    )

    out = StringIO()
    err = StringIO()
    exit_code = run_rules_command(
        RulesCommand(
            rules_file=rules_file,
            plan_file=plan_file,
            plan_report_json=True,
        ),
        stdout=out,
        stderr=err,
    )

    assert exit_code == 1
    payload = json.loads(out.getvalue())
    assert payload["status"] == "FAIL"
    assert any(w["code"] == "override_not_allowed" for w in payload["warnings"])

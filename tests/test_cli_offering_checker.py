"""Behavior tests for offering_checker CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from transitionchecker.cli import offering_checker_cli


def test_requires_plan_file_argument() -> None:
    with pytest.raises(SystemExit) as exc:
        offering_checker_cli.main([])
    assert exc.value.code == 2


def test_uses_offerings_file_in_plan_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    local_offerings = tmp_path / "offerings.json"
    local_offerings.write_text("{}", encoding="utf-8")

    captured: dict[str, Any] = {}

    def fake_check(plan_file: Path, offerings_file: Path) -> dict[str, Any]:
        captured["plan_file"] = plan_file
        captured["offerings_file"] = offerings_file
        return {
            "plan_file": str(plan_file),
            "plan_summary": {"sheet": "S", "intake": "I"},
            "valid": True,
            "violations_count": 0,
            "violations": [],
        }

    monkeypatch.setattr(offering_checker_cli, "check_plan", fake_check)

    exit_code = offering_checker_cli.main([str(plan_path)])
    assert exit_code == 0
    assert captured["offerings_file"] == local_offerings.resolve()


def test_uses_default_repo_offerings_when_no_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    captured: dict[str, Any] = {}

    def fake_check(plan_file: Path, offerings_file: Path) -> dict[str, Any]:
        captured["offerings_file"] = offerings_file
        return {
            "plan_file": str(plan_file),
            "plan_summary": {"sheet": "S", "intake": "I"},
            "valid": True,
            "violations_count": 0,
            "violations": [],
        }

    monkeypatch.setattr(offering_checker_cli, "check_plan", fake_check)

    exit_code = offering_checker_cli.main([str(plan_path)])
    assert exit_code == 0
    assert captured["offerings_file"].name == "offerings.json"
    assert captured["offerings_file"].parent.name == "plans"


def test_result_json_prints_json_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    result: dict[str, Any] = {
        "plan_file": str(plan_path),
        "plan_summary": {"sheet": "S", "intake": "I"},
        "valid": False,
        "violations_count": 1,
        "violations": [
            {
                "course_code": "TEST1001",
                "planned_period": "Term 2",
                "allowed_periods": ["Term 1"],
                "error_type": "period_not_allowed",
            }
        ],
    }

    def fake_check(_plan_file: Path, _offerings_file: Path) -> dict[str, Any]:
        return result

    monkeypatch.setattr(offering_checker_cli, "check_plan", fake_check)

    exit_code = offering_checker_cli.main([str(plan_path), "--result-json"])
    assert exit_code == 1
    output = capsys.readouterr().out.strip()
    assert json.loads(output)["violations_count"] == 1


def test_returns_exit_2_on_missing_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    def fake_check(_plan_file: Path, _offerings_file: Path) -> dict[str, Any]:
        raise FileNotFoundError("missing offerings")

    monkeypatch.setattr(offering_checker_cli, "check_plan", fake_check)

    exit_code = offering_checker_cli.main([str(plan_path)])
    assert exit_code == 2
    assert "missing offerings" in capsys.readouterr().err


def test_normalizes_course_codes_when_checking_offerings() -> None:
    """Verify that plan course codes are normalized before checking against offerings."""
    plan: offering_checker_cli.PlanDocument = {
        "sheet": "S",
        "intake": "I",
        "courses": [
            {"code": "ceic2000", "period": "Term 1"},  # lowercase, no spaces
            {"code": "CEIC 2001", "period": "Term 1"},  # uppercase with space
            {"code": "bioc2181", "period": "Term 3"},  # another course, lowercase
        ],
    }
    offerings = {
        "CEIC2000": ["Term 1", "Semester 1"],
        "CEIC2001": ["Term 1", "Term 2"],
        "BIOC2181": ["Term 3", "Term 1"],
    }

    violations = offering_checker_cli.validate_plan_offerings(plan, offerings)

    # All courses should be found (no "course_not_found" violations)
    assert len(violations) == 0
    assert all(v["error_type"] != "course_not_found" for v in violations)


def test_load_offerings_normalizes_and_merges_course_code_keys(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps(
            {
                "ceic 2001": ["Term 1"],
                "CEIC2001": ["Term 2"],
                "BIOC 2181": ["Term 3"],
            }
        ),
        encoding="utf-8",
    )

    offerings = offering_checker_cli.load_offerings(offerings_path)

    assert offerings["CEIC2001"] == ["Term 1", "Term 2"]
    assert offerings["BIOC2181"] == ["Term 3"]

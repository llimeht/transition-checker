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

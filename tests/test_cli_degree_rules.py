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
            "-v",
        ]
    )

    assert exit_code == 7
    command = captured["command"]
    assert command.rules_file == Path("rules/sample.json")
    assert command.json_output is True
    assert command.plan_file == Path("plans/plan.json")
    assert command.plan_report_json is True
    assert command.render_rules_text is False


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

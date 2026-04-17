"""Behavior tests for degree_rules CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import TextIO

import pytest

from transitionchecker.cli import degree_rules_cli
from transitionchecker.rules_engine import RulesCommand


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

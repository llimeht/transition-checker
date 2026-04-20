"""Behavior tests for map_maker CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from transitionchecker.cli import map_maker_cli


def test_parse_args_requires_rule_and_intake() -> None:
    with pytest.raises(SystemExit) as exc:
        map_maker_cli.parse_args([])
    assert exc.value.code == 2


def test_parse_args_defaults() -> None:
    args = map_maker_cli.parse_args(["--rule", "rules/r.json", "--intake", "2026 T1"])
    assert args.offerings == "plans/offerings.json"
    assert args.catalogue == "plans/catalogue.json"
    assert args.template_config == "templates/template_configs.json"
    assert args.steering == "templates/map_steering.json"
    assert args.partial_plan is None
    assert args.num_solutions == 5
    assert args.restarts == 10
    assert args.iterations == 2000


def test_main_builds_command_and_returns_runner_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = argparse.Namespace(
        rule="rules/r.json",
        intake="2026 T1",
        offerings="plans/offerings.json",
        catalogue="plans/catalogue.json",
        template_config="templates/template_configs.json",
        steering="templates/map_steering.json",
        partial_plan="plans/test_partial_plan.json",
        num_solutions=3,
        restarts=4,
        iterations=50,
        patience=10,
        ruin_fraction=0.2,
        seed=99,
        output="out.csv",
        verbose=2,
    )
    monkeypatch.setattr(map_maker_cli, "parse_args", lambda: ns)

    captured: dict[str, Any] = {}

    def fake_run(command: Any, *, stdout: Any, stderr: Any) -> int:
        captured["command"] = command
        return 13

    monkeypatch.setattr(map_maker_cli, "run_planner", fake_run)

    code = map_maker_cli.main()
    assert code == 13
    cmd = captured["command"]
    assert cmd.rule_path == Path("rules/r.json")
    assert cmd.intake == "2026 T1"
    assert cmd.num_solutions == 3
    assert cmd.restarts == 4
    assert cmd.iterations == 50
    assert cmd.patience == 10
    assert cmd.ruin_fraction == 0.2
    assert cmd.seed == 99
    assert cmd.output_path == Path("out.csv")
    assert cmd.verbose == 2
    assert cmd.partial_plan_path == Path("plans/test_partial_plan.json")


def test_main_returns_1_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = argparse.Namespace(
        rule="rules/r.json",
        intake="2026 T1",
        offerings="plans/offerings.json",
        catalogue="plans/catalogue.json",
        template_config="templates/template_configs.json",
        steering="templates/map_steering.json",
        partial_plan=None,
        num_solutions=1,
        restarts=1,
        iterations=1,
        patience=None,
        ruin_fraction=0.3,
        seed=1,
        output=None,
        verbose=0,
    )
    monkeypatch.setattr(map_maker_cli, "parse_args", lambda: ns)

    def boom(_command: Any, *, stdout: Any, stderr: Any) -> int:
        raise RuntimeError("planner failed")

    monkeypatch.setattr(map_maker_cli, "run_planner", boom)

    code = map_maker_cli.main()
    assert code == 1
    assert "planner failed" in capsys.readouterr().err

"""Behavior tests for validate CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from transitionchecker.cli import validate_cli


def test_resolve_rule_file_prefers_matching_range(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "CEICDH3707-2020-2025.json").write_text("{}", encoding="utf-8")
    ranged = rules_dir / "CEICDH3707-2026-2029.json"
    ranged.write_text("{}", encoding="utf-8")
    (rules_dir / "CEICDH3707.json").write_text("{}", encoding="utf-8")

    chosen = validate_cli.resolve_rule_file(
        "CEICDH3707", "CEICDH3707_2026_T1", tmp_path
    )
    assert chosen == ranged


def test_main_returns_1_for_missing_excel(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xlsx"
    code = validate_cli.main([str(missing)])
    assert code == 1


def test_main_propagates_export_failure_code(tmp_path: Path, monkeypatch) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")

    def fake_run(_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["x"], returncode=5, stdout="", stderr="boom"
        )

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)

    code = validate_cli.main([str(excel), "--output-dir", str(tmp_path / "out")])
    assert code == 5


def test_main_returns_0_when_no_plan_files(tmp_path: Path, monkeypatch) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    def fake_run(_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # export succeeds, but creates no plan JSON files
        return subprocess.CompletedProcess(
            args=["x"], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)

    code = validate_cli.main([str(excel), "--output-dir", str(out_dir)])
    assert code == 0

    report = json.loads(
        (out_dir / "mapping_validation_results.json").read_text(encoding="utf-8")
    )
    summary = report["summary"]
    assert summary["total_plan_files"] == 0
    assert summary["failed"] == 0

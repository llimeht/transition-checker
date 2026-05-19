"""Behavior tests for validate CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


def test_main_propagates_export_failure_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")

    def fake_run(_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["x"], returncode=5, stdout="", stderr="boom"
        )

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)

    code = validate_cli.main([str(excel), "--output-dir", str(tmp_path / "out")])
    assert code == 5


def test_main_returns_0_when_no_plan_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_main_filters_plan_files_by_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    filtered_plan = out_dir / "CEICKS8338_2026_T1.json"
    filtered_plan.write_text('{"courses": [{"code": "COMP1511"}]}', encoding="utf-8")
    skipped_plan = out_dir / "CEICAH3707_2026_T1.json"
    skipped_plan.write_text('{"courses": [{"code": "COMP1521"}]}', encoding="utf-8")

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        run_calls.append(cmd)
        if "extract_plans.py" in cmd[1] or "extract_template.py" in cmd[1]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        if "degree_rules.py" in cmd[1]:
            degree_report: dict[str, object] = {
                "valid": True,
                "rule_failures": [],
                "prerequisite_failures": [],
                "unsupported_prerequisites": [],
                "findings": [],
            }
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps(degree_report),
                stderr="",
            )
        if "offering_checker.py" in cmd[1]:
            offering_report: dict[str, object] = {"valid": True, "violations": []}
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps(offering_report),
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    def fake_resolve_rule_file(
        _program_code: str, _plan_stem: str, _script_dir: Path
    ) -> Path:
        return tmp_path / "rules" / "CEICKS8338.json"

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)
    monkeypatch.setattr(validate_cli, "resolve_rule_file", fake_resolve_rule_file)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "CEICKS8338.json").write_text("{}", encoding="utf-8")

    code = validate_cli.main(
        [
            str(excel),
            "--output-dir",
            str(out_dir),
            "--filter",
            "CEICKS8338*",
        ]
    )

    assert code == 0
    degree_rule_calls = [cmd for cmd in run_calls if "degree_rules.py" in cmd[1]]
    assert len(degree_rule_calls) == 1
    assert degree_rule_calls[0][degree_rule_calls[0].index("--plan") + 1] == str(filtered_plan)

    report = json.loads(
        (out_dir / "mapping_validation_results.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["total_plan_files"] == 1
    assert [result["plan_file"] for result in report["results"]] == [str(filtered_plan)]


def test_main_collects_structured_findings_when_legacy_lists_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    failing_plan = out_dir / "CEICDH3707_2026_T1.json"
    failing_plan.write_text('{"courses": [{"code": "COMP1511"}]}', encoding="utf-8")

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if "extract_plans.py" in cmd[1] or "extract_template.py" in cmd[1]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        if "degree_rules.py" in cmd[1]:
            degree_report: dict[str, object] = {
                "valid": False,
                "rule_failures": [],
                "prerequisite_failures": [],
                "unsupported_prerequisites": [],
                "findings": [
                    {
                        "failure_id": "rule:TEST1001",
                        "kind": "rule",
                        "message": "Missing TEST1001",
                        "overrideable": True,
                        "accepted": False,
                    },
                    {
                        "failure_id": "prereq:TEST2001>TEST1001",
                        "kind": "prereq",
                        "message": "TEST2001 prerequisite not met",
                        "overrideable": True,
                        "accepted": False,
                    },
                ],
                "warnings": [],
            }
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout=json.dumps(degree_report),
                stderr="",
            )
        if "offering_checker.py" in cmd[1]:
            offering_report: dict[str, object] = {"valid": True, "violations": []}
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps(offering_report),
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    def fake_resolve_rule_file(
        _program_code: str, _plan_stem: str, _script_dir: Path
    ) -> Path:
        return tmp_path / "rules" / "CEICDH3707.json"

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)
    monkeypatch.setattr(validate_cli, "resolve_rule_file", fake_resolve_rule_file)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "CEICDH3707.json").write_text("{}", encoding="utf-8")

    code = validate_cli.main([str(excel), "--output-dir", str(out_dir)])

    assert code == 1
    report = json.loads(
        (out_dir / "mapping_validation_results.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["failed"] == 1

    result_entry = report["results"][0]
    assert result_entry["status"] == "failed"
    assert result_entry["rule_failures"] == []
    assert result_entry["prerequisite_failures"] == []
    assert result_entry["offering_violations"] == []
    findings = result_entry["findings"]
    assert any(f["kind"] == "rule" for f in findings)
    assert any(f["kind"] == "prereq" for f in findings)


def test_main_skips_placeholder_plan_with_blank_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    placeholder_plan = out_dir / "MATSM13132+CEICM13132_2028_S2.json"
    placeholder_plan.write_text(
        json.dumps(
            {
                "courses": [
                    {"code": "[ABCD1234]"},
                    {"code": "[ABCD1234]"},
                    {"code": "   "},
                ]
            }
        ),
        encoding="utf-8",
    )

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        run_calls.append(cmd)
        if "extract_plans.py" in cmd[1] or "extract_template.py" in cmd[1]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    def fake_resolve_rule_file(
        _program_code: str, _plan_stem: str, _script_dir: Path
    ) -> Path:
        return tmp_path / "rules" / "MATSM13132+CEICM13132.json"

    monkeypatch.setattr(validate_cli, "run_cmd", fake_run)
    monkeypatch.setattr(validate_cli, "resolve_rule_file", fake_resolve_rule_file)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "MATSM13132+CEICM13132.json").write_text("{}", encoding="utf-8")

    code = validate_cli.main([str(excel), "--output-dir", str(out_dir), "--filter", "MATS*"])
    assert code == 0

    degree_rule_calls = [cmd for cmd in run_calls if "degree_rules.py" in cmd[1]]
    assert degree_rule_calls == []
    offering_calls = [cmd for cmd in run_calls if "offering_checker.py" in cmd[1]]
    assert offering_calls == []

    report = json.loads(
        (out_dir / "mapping_validation_results.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["total_plan_files"] == 1
    assert report["summary"]["failed"] == 0
    assert report["summary"]["skipped_placeholder"] == 1
    assert report["results"][0]["status"] == "skipped_placeholder"

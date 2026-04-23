"""Run end-to-end plan export and validation for a mapping workbook.

This wrapper script:
1) exports plan JSON files from an Excel mapping workbook,
2) validates each plan against degree rules and prerequisites,
3) validates plan periods against offerings, and
4) writes a consolidated validation report.
"""

from __future__ import annotations

import json
import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from transitionchecker.core import as_json_object


def _as_json_object(value: object) -> dict[str, object] | None:
    """Return a JSON object with string keys, otherwise None."""
    obj = as_json_object(value)
    return cast(dict[str, object], obj) if obj is not None else None


def _as_object_list(value: object) -> list[object]:
    """Return list values as a typed object list, otherwise an empty list."""
    return cast(list[object], value) if isinstance(value, list) else []


def _as_object_dict_list(value: object) -> list[dict[str, object]]:
    """Return list entries that are JSON objects with string keys."""
    if not isinstance(value, list):
        return []

    value_list = cast(list[object], value)
    dict_items: list[dict[str, object]] = []
    for item in value_list:
        item_obj = _as_json_object(item)
        if item_obj is not None:
            dict_items.append(item_obj)
    return dict_items


def _build_cli_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for validation workflow.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Export plans from an Excel sequence mapping file and validate them "
            "against rules, prerequisites, and offerings."
        ),
        epilog=(
            "Example: \n\n"
            "  plan-validate 'plans/CEIC/CEIC Program Sequence Mapping.xlsx'\n\n"
            "By default, outputs are written beside the Excel file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "excel_file",
        help="Path to the source Excel mapping workbook",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory for exported plan JSON and validation report "
            "(default: directory containing the Excel file)"
        ),
    )
    return parser


def resolve_rule_file(program_code: str, plan_stem: str, script_dir: Path) -> Path:
    """Return the best matching rule file for a plan/program.

    Args:
        program_code: Program code prefix extracted from plan filename.
        plan_stem: Plan filename stem.
        script_dir: Directory containing the rules folder.

    If the plan stem includes an intake year and a ranged rule file exists (for
    example ``PROGRAM-2020-2025.json``), the matching ranged file is preferred.
    Otherwise the fallback is ``rules/PROGRAM.json``.

    Returns:
        Path to the selected rule file.
    """
    intake_year: int | None = None
    prefix = f"{program_code}_"

    if plan_stem.startswith(prefix):
        intake_segment = plan_stem[len(prefix) :]
        intake_year_candidate = intake_segment.split("_", 1)[0]
        if re.fullmatch(r"\d{4}", intake_year_candidate):
            intake_year = int(intake_year_candidate)

    if intake_year is not None:
        for candidate in sorted((script_dir / "rules").glob("*.json")):
            candidate_stem = candidate.stem
            if not candidate_stem.startswith(f"{program_code}-"):
                continue

            range_part = candidate_stem[len(program_code) + 1 :]
            match = re.fullmatch(r"(\d{4})-(\d{4})", range_part)
            if not match:
                continue

            range_start = int(match.group(1))
            range_end = int(match.group(2))
            if range_start <= intake_year <= range_end:
                return candidate

    return script_dir / "rules" / f"{program_code}.json"


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run subprocess command and capture text output.

    Args:
        cmd: Command vector to execute.

    Returns:
        Completed process with captured stdout/stderr.
    """
    return subprocess.run(cmd, capture_output=True, text=True)


def write_validation_report(report_path: Path, report: dict[str, object]) -> None:
    """Write consolidated validation report to JSON file.

    Args:
        report_path: Destination file path.
        report: Report payload to serialize.
    """
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Execute the full validation workflow.

    Returns:
        Exit code 0 when all plans pass validation, otherwise non-zero.
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    excel_file = Path(args.excel_file)
    project_root = Path(__file__).resolve().parents[3]
    output_dir = Path(args.output_dir) if args.output_dir else excel_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{excel_file.stem}_validation_results.json"

    if not excel_file.is_file():
        print(f"Error: File not found: {excel_file}")
        return 1

    print(f"📊 Exporting plans from: {excel_file}")
    export_result = run_cmd(
        [
            sys.executable,
            str(project_root / "extract_plans.py"),
            str(excel_file),
            "--output-dir",
            str(output_dir),
            "-v",
        ]
    )
    if export_result.returncode != 0:
        if export_result.stdout:
            print(export_result.stdout, end="")
        if export_result.stderr:
            print(export_result.stderr, end="", file=sys.stderr)
        return export_result.returncode

    if export_result.stdout:
        print(export_result.stdout, end="")

    print("✅ Export complete!")
    print()
    print("🔍 Validating exported plans...")

    report: dict[str, object] = {
        "excel_file": str(excel_file),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "report_file": str(report_path),
        "results": [],
    }

    plan_files = sorted(
        p
        for p in output_dir.glob("*.json")
        if p.is_file()
        and not p.name.endswith("_offerings.json")
        and not p.name.endswith("_offering_violations.json")
        and not p.name.endswith(".degree_rules_overrides.json")
    )

    if not plan_files:
        print("⚠️  No plan files found to validate")
        report["summary"] = {
            "total_plan_files": 0,
            "valid": 0,
            "failed": 0,
            "skipped_no_rule": 0,
        }
        write_validation_report(report_path, report)
        print(f"☑️ Validation report written to: {report_path}")
        return 0

    failed = 0
    valid = 0
    skipped_no_rule = 0
    results: list[dict[str, object]] = []
    failure_details: list[tuple[str, str, str]] = []

    plan_col_width = max(4, max(len(p.name) for p in plan_files))
    rule_col_width = 30
    status_col_width = 10

    print(
        f"  {'Plan':<{plan_col_width}}  {'Rule':<{rule_col_width}}  {'Status':>{status_col_width}}"
    )
    print(f"  {'-' * plan_col_width}  {'-' * rule_col_width}  {'-' * status_col_width}")

    for plan_file in plan_files:
        plan_stem = plan_file.stem
        program_code = plan_stem.split("_", 1)[0]
        rule_file = resolve_rule_file(program_code, plan_stem, project_root)
        rule_name = rule_file.name
        cwd = Path.cwd()
        try:
            rule_file_rel = rule_file.relative_to(cwd)
        except ValueError:
            rule_file_rel = rule_file
        try:
            plan_file_rel = plan_file.relative_to(cwd)
        except ValueError:
            plan_file_rel = plan_file

        if not rule_file.is_file():
            skipped_no_rule += 1
            continue

        result = run_cmd(
            [
                sys.executable,
                str(project_root / "degree_rules.py"),
                str(rule_file),
                "--plan",
                str(plan_file),
                "--plan-report-json",
            ]
        )

        plan_report: dict[str, object] = {}
        if result.stdout.strip():
            try:
                parsed = cast(object, json.loads(result.stdout))
                parsed_obj = _as_json_object(parsed)
                if parsed_obj is not None:
                    plan_report = parsed_obj
            except json.JSONDecodeError:
                plan_report = {}

        rule_failures = _as_object_list(plan_report.get("rule_failures", []))
        prereq_failures = _as_object_list(plan_report.get("prerequisite_failures", []))
        unsupported_prereqs = _as_object_list(
            plan_report.get("unsupported_prerequisites", [])
        )
        structured_findings = _as_object_dict_list(plan_report.get("findings", []))
        plan_is_valid = (
            bool(plan_report.get("valid")) if plan_report else result.returncode == 0
        )

        # Check offerings
        offerings_file = project_root / "plans" / "offerings.json"
        offering_violations: list[dict[str, object]] = []
        offerings_valid = False
        offering_result_raw = run_cmd(
            [
                sys.executable,
                str(project_root / "offering_checker.py"),
                str(plan_file),
                "--offerings",
                str(offerings_file),
                "--result-json",
            ]
        )
        try:
            offering_parsed = cast(
                object,
                json.loads(offering_result_raw.stdout)
                if offering_result_raw.stdout.strip()
                else {},
            )
            offering_report = _as_json_object(offering_parsed)
            if offering_report is None:
                raise ValueError("offering checker returned non-object JSON")

            offering_violations = _as_object_dict_list(
                offering_report.get("violations", [])
            )
            offerings_valid = bool(offering_report.get("valid", False))
        except (json.JSONDecodeError, ValueError) as exc:
            message = offering_result_raw.stderr.strip() or str(exc)
            offering_violations = [
                {"error_type": "offering_check_error", "message": message}
            ]
            offerings_valid = False

        if offering_result_raw.returncode > 1 and not offering_violations:
            message = (
                offering_result_raw.stderr.strip() or "offering checker process failed"
            )
            offering_violations = [
                {"error_type": "offering_check_error", "message": message}
            ]
            offerings_valid = False

        if plan_is_valid and offerings_valid:
            print(
                f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'✓ PASS':>{status_col_width}}"
            )
            valid += 1
            results.append(
                {
                    "plan_file": str(plan_file),
                    "program_code": program_code,
                    "rule_file": rule_name,
                    "status": "valid",
                    "rule_failures": rule_failures,
                    "prerequisite_failures": prereq_failures,
                    "unsupported_prerequisites": unsupported_prereqs,
                    "offering_violations": offering_violations,
                }
            )
            continue

        print(
            f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'✗ FAIL':>{status_col_width}}"
        )
        has_structured_rule_report = bool(plan_report)
        rule_process_error_output = ""
        if result.stderr.strip():
            rule_process_error_output = result.stderr.strip()
        elif result.returncode != 0 and not has_structured_rule_report:
            # Only treat stdout as an error when degree_rules did not return structured JSON.
            rule_process_error_output = result.stdout.strip()

        failed_entry: dict[str, object] = {
            "plan_file": str(plan_file),
            "program_code": program_code,
            "rule_file": rule_name,
            "status": "failed",
            "rule_failures": rule_failures,
            "prerequisite_failures": prereq_failures,
            "unsupported_prerequisites": unsupported_prereqs,
            "offering_violations": offering_violations,
        }
        if rule_process_error_output:
            failed_entry["error_output"] = rule_process_error_output

        results.append(failed_entry)
        detail_lines: list[str] = []

        if rule_failures or prereq_failures or unsupported_prereqs:
            # Use structured findings when available so failure_ids are shown
            active_findings = [f for f in structured_findings if not f.get("accepted")]
            rule_finding_lines = [
                f for f in active_findings if str(f.get("kind", "")).startswith("rule")
            ]
            prereq_finding_lines = [
                f
                for f in active_findings
                if str(f.get("kind", "")).startswith("prereq")
                or str(f.get("kind", "")).startswith("coreq")
            ]
            unsup_finding_lines = [
                f
                for f in active_findings
                if str(f.get("kind", "")) == "unsupported_syntax"
            ]

            if rule_finding_lines:
                detail_lines.append(f"rule_failures={len(rule_finding_lines)}")
                for f in rule_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
                    if fid and f.get("overrideable"):
                        detail_lines.append(
                            f"    \u2192 degree-rules {rule_file_rel} --plan {plan_file_rel} --add-override '{fid}'"
                        )
            elif rule_failures:
                detail_lines.append(f"rule_failures={len(rule_failures)}")
                for failure in rule_failures:
                    detail_lines.append(f"  - {failure}")

            if prereq_finding_lines:
                detail_lines.append(
                    f"prerequisite_failures={len(prereq_finding_lines)}"
                )
                for f in prereq_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
                    if fid and f.get("overrideable"):
                        detail_lines.append(
                            f"    \u2192 degree-rules {rule_file_rel} --plan {plan_file_rel} --add-override '{fid}'"
                        )
            elif prereq_failures:
                detail_lines.append(f"prerequisite_failures={len(prereq_failures)}")
                for failure in prereq_failures:
                    detail_lines.append(f"  - {failure}")

            if unsup_finding_lines:
                detail_lines.append(
                    f"unsupported_prerequisites={len(unsup_finding_lines)}"
                )
                for f in unsup_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
            elif unsupported_prereqs:
                detail_lines.append(
                    f"unsupported_prerequisites={len(unsupported_prereqs)}"
                )
                for failure in unsupported_prereqs:
                    detail_lines.append(f"  - {failure}")

        if offering_violations:
            detail_lines.append(f"offering_violations={len(offering_violations)}")
            for viol in offering_violations:
                error_type = str(viol.get("error_type", ""))
                if error_type == "offering_check_error":
                    message = str(viol.get("message", "Unknown offering check error"))
                    detail_lines.append(f"  - {message}")
                    continue

                code = str(viol.get("course_code", ""))
                planned_period = str(viol.get("planned_period", ""))
                allowed_periods = _as_object_list(viol.get("allowed_periods", []))
                allowed_text = (
                    ", ".join(str(p) for p in allowed_periods)
                    if allowed_periods
                    else "(none)"
                )

                if error_type == "course_not_found":
                    detail_lines.append(
                        f"  - {code}: not found in offerings (planned {planned_period})"
                    )
                elif error_type == "period_not_allowed":
                    detail_lines.append(
                        f"  - {code}: planned {planned_period}; allowed: {allowed_text}"
                    )
                else:
                    detail_lines.append(
                        f"  - {code}: {error_type} (planned {planned_period})"
                    )

        if rule_process_error_output and not detail_lines:
            detail_lines.append(rule_process_error_output)

        if detail_lines:
            failure_details.append((plan_file.name, rule_name, "\n".join(detail_lines)))

        failed += 1

    if failure_details:
        print("\nFailure details:")
        for plan_name, rule_name, output in failure_details:
            print(f"  - {plan_name} | {rule_name}")
            for line in output.splitlines():
                print(f"    {line}")

    report["results"] = results
    report["summary"] = {
        "total_plan_files": len(plan_files),
        "valid": valid,
        "failed": failed,
        "skipped_no_rule": skipped_no_rule,
    }
    write_validation_report(report_path, report)
    print(f"\n☑️ Validation report written to: {report_path}")

    print()
    if failed == 0:
        print("🎉 All plans validated successfully!")
        return 0

    print(f"❌ {failed} plan(s) failed validation")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

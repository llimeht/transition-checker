"""Run end-to-end plan export and validation for a mapping workbook.

This wrapper script:
1) exports plan JSON files from an Excel mapping workbook,
2) validates each plan against degree rules and prerequisites,
3) validates plan periods against offerings, and
4) writes a consolidated validation report.
"""

from __future__ import annotations

import argparse
import json
import fnmatch
import io
import re
import shlex
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, cast

from transitionchecker.core import as_json_object
from transitionchecker.core.rules_loader import resolve_rule_file_for_plan
from transitionchecker.cli.degree_rules_cli import main as degree_rules_main
from transitionchecker.cli.extract_plans_cli import main as extract_plans_main
from transitionchecker.cli.extract_template_cli import main as extract_template_main
from transitionchecker.cli.offering_checker_cli import main as offering_checker_main
from transitionchecker.core.validation_report_html import (
    default_html_report_path,
    render_validation_report_html,
    write_validation_report_html,
)


CliMain = Callable[[list[str] | None], int]


CLI_MAIN_DISPATCH: dict[str, CliMain] = {
    "extract-plans": extract_plans_main,
    "extract-template": extract_template_main,
    "degree-rules": degree_rules_main,
    "offering-checker": offering_checker_main,
}


def _find_rules_dir(start: Path) -> Path | None:
    """Walk upward from *start* looking for a ``rules/`` subdirectory."""

    for parent in [start, *list(start.parents)[:3]]:
        candidate = parent / "rules"
        if candidate.is_dir():
            return candidate
    return None


def _resolve_rules_dir(output_dir: Path, excel_file: Path) -> Path | None:
    """Resolve the rules directory relative to the workbook/output context."""

    return _find_rules_dir(output_dir) or _find_rules_dir(excel_file.parent)


def _resolve_offerings_file(plan_file: Path) -> Path:
    """Resolve the canonical offerings file for a specific exported plan."""

    for parent in [plan_file.parent, *list(plan_file.parent.parents)[:3]]:
        same_dir_offerings = parent / "offerings.json"
        if same_dir_offerings.is_file():
            return same_dir_offerings.resolve()

        plans_offerings = parent / "plans" / "offerings.json"
        if plans_offerings.is_file():
            return plans_offerings.resolve()

    return (plan_file.parent / "offerings.json").resolve()


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


def _normalize_notes(value: object) -> dict[str, object]:
    """Return a stable notes object from report payloads."""

    notes = _as_json_object(value)
    if notes is None:
        return {
            "graduate_outcome": "",
            "adjustment_type": "",
            "for_reviewers": [],
            "for_students": [],
        }

    reviewers_raw = notes.get("for_reviewers", [])
    students_raw = notes.get("for_students", [])

    return {
        "graduate_outcome": str(notes.get("graduate_outcome", "") or ""),
        "adjustment_type": str(notes.get("adjustment_type", "") or ""),
        "for_reviewers": _as_object_list(reviewers_raw),
        "for_students": _as_object_list(students_raw),
    }


def _format_add_override_hint(
    rule_file: Path, plan_file: Path, failure_id: str
) -> str:
    """Render a shell-safe override hint command."""

    command = shlex.join(
        [
            "degree-rules",
            str(rule_file),
            "--plan",
            str(plan_file),
            "--add-override",
            failure_id,
        ]
    )
    return f"    \u2192 {command}"


def _should_skip_placeholder_plan(courses: list[dict[str, object]]) -> bool:
    """Return whether plan courses are empty or template placeholders only."""

    # Ignore blank/whitespace codes exported from sparse workbook rows.
    normalized_codes = [str(c.get("code", "")).strip() for c in courses]
    non_blank_codes = [code for code in normalized_codes if code]
    if not non_blank_codes:
        return True

    return all(re.fullmatch(r"\[.*\]", code) for code in non_blank_codes)


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
    parser.add_argument(
        "--filter",
        dest="plan_filter",
        default=None,
        help=(
            "Glob pattern for exported plan filenames to validate "
            "(for example: 'CEICKS8338*')"
        ),
    )
    parser.add_argument(
        "--human-report",
        choices=("html", "none"),
        default="html",
        help=(
            "Generate an additional human-readable report artifact "
            "(default: html)."
        ),
    )
    parser.add_argument(
        "--warning-filter-codes",
        default="missing_rule_id",
        help=(
            "Comma-separated warning codes to suppress in the human report "
            "(default: missing_rule_id)."
        ),
    )
    parser.add_argument(
        "--include-all-warnings",
        action="store_true",
        help="Show all warning codes in the human report, ignoring suppression.",
    )
    return parser





def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a CLI main function in-process and capture text output.

    Args:
        cmd: Command vector where cmd[0] selects the CLI entrypoint.

    Returns:
        Completed process with captured stdout/stderr.
    """
    if not cmd:
        raise ValueError("command vector must not be empty")

    command_name, *argv = cmd
    cli_main = CLI_MAIN_DISPATCH.get(command_name)
    if cli_main is None:
        raise ValueError(f"unsupported command: {command_name}")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        try:
            returncode = cli_main(argv)
        except SystemExit as exc:
            returncode = exc.code if isinstance(exc.code, int) else 1

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
    )


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
    output_dir = Path(args.output_dir) if args.output_dir else excel_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{excel_file.stem}_validation_results.json"
    rules_dir = _resolve_rules_dir(output_dir, excel_file)
    html_report_path = default_html_report_path(report_path)
    suppressed_warning_codes = [
        code.strip() for code in str(args.warning_filter_codes).split(",") if code.strip()
    ]

    if not excel_file.is_file():
        print(f"Error: File not found: {excel_file}")
        return 1

    print(f"📊 Exporting plans from: {excel_file}")
    export_result = run_cmd(
        [
            "extract-plans",
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
    print(f"📚 Extracting catalogue and overrides from: {excel_file}")
    catalogue_path = output_dir / "catalogue.json"
    extract_result = run_cmd(
        [
            "extract-template",
            str(excel_file),
            "--catalogue-output",
            str(catalogue_path),
            "--template-output",
            "NONE",
        ]
    )
    if extract_result.returncode != 0:
        if extract_result.stdout:
            print(extract_result.stdout, end="")
        if extract_result.stderr:
            print(extract_result.stderr, end="", file=sys.stderr)
        return extract_result.returncode
    if extract_result.stdout:
        print(extract_result.stdout, end="")
    print("✅ Catalogue extracted!")

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
        and (
            args.plan_filter is None or fnmatch.fnmatch(p.stem, args.plan_filter)
        )
    )

    if not plan_files:
        print("⚠️  No plan files found to validate")
        report["summary"] = {
            "total_plan_files": 0,
            "valid": 0,
            "failed": 0,
            "skipped_no_rule": 0,
            "skipped_placeholder": 0,
            "accepted": 0,
        }
        write_validation_report(report_path, report)
        print(f"☑️ Validation report written to: {report_path}")
        if args.human_report == "html":
            html_text = render_validation_report_html(
                report,
                suppressed_warning_codes=suppressed_warning_codes,
                include_all_warnings=bool(args.include_all_warnings),
            )
            write_validation_report_html(html_report_path, html_text)
            print(f"☑️ Human-readable report written to: {html_report_path}")
        return 0

    if rules_dir is None:
        print(
            "Error: Could not find a rules directory near the workbook or output directory",
            file=sys.stderr,
        )
        return 1

    failed = 0
    valid = 0
    accepted = 0
    skipped_no_rule = 0
    skipped_placeholder = 0
    results: list[dict[str, object]] = []
    failure_details: list[tuple[str, str, str]] = []

    plan_col_width = max(4, max(len(p.name) for p in plan_files))
    rule_col_width = 30
    status_col_width = 10

    print(
        f"  {'Plan':<{plan_col_width}}  {'Rule':<{rule_col_width}}  {'Status':<{status_col_width}}"
    )
    print(f"  {'-' * plan_col_width}  {'-' * rule_col_width}  {'-' * status_col_width}")

    for plan_file in plan_files:
        plan_stem = plan_file.stem
        program_code = plan_stem.split("_", 1)[0]
        rule_file = resolve_rule_file_for_plan(program_code, plan_stem, rules_dir)
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

        try:
            plan_data = _as_json_object(
                json.loads(plan_file.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, OSError):
            plan_data = None

        program_metadata: dict[str, object] | None = (
            _as_json_object(plan_data.get("program_metadata"))
            if plan_data is not None
            else None
        )

        if plan_data is not None:
            courses = _as_object_dict_list(plan_data.get("courses", []))
            if _should_skip_placeholder_plan(courses):
                print(
                    f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'⊘ SKIP':<{status_col_width}}"
                )
                skipped_placeholder += 1
                results.append(
                    {
                        "plan_file": str(plan_file),
                        "program_code": program_code,
                        "rule_file": rule_name,
                        "status": "skipped_placeholder",
                        "program_metadata": program_metadata,
                    }
                )
                continue

        result = run_cmd(
            [
                "degree-rules",
                str(rule_file),
                "--plan",
                str(plan_file),
                "--catalogue",
                str(catalogue_path),
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
        structured_warnings = _as_object_dict_list(plan_report.get("warnings", []))
        report_notes = _normalize_notes(plan_report.get("notes", {}))
        plan_is_valid = (
            bool(plan_report.get("valid")) if plan_report else result.returncode == 0
        )

        # Check offerings
        offerings_file = _resolve_offerings_file(plan_file)
        offering_violations: list[dict[str, object]] = []
        offerings_valid = False
        offering_result_raw = run_cmd(
            [
                "offering-checker",
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
            accepted_findings = [f for f in structured_findings if f.get("accepted")]
            has_accepted = bool(accepted_findings)
            if has_accepted:
                print(
                    f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'⁉ ACCEPTED':<{status_col_width}}"
                )
                accepted += 1
            else:
                print(
                    f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'✓ PASS':<{status_col_width}}"
                )
                valid += 1
            results.append(
                {
                    "plan_file": str(plan_file),
                    "program_code": program_code,
                    "rule_file": rule_name,
                    "status": "accepted" if has_accepted else "valid",
                    "program_metadata": program_metadata,
                    "rule_failures": rule_failures,
                    "prerequisite_failures": prereq_failures,
                    "unsupported_prerequisites": unsupported_prereqs,
                    "findings": structured_findings,
                    "warnings": structured_warnings,
                    "notes": report_notes,
                    "offering_violations": offering_violations,
                }
            )
            continue

        print(
            f"  {plan_file.name:<{plan_col_width}}  {rule_name:<{rule_col_width}}  {'✗ FAIL':<{status_col_width}}"
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
            "program_metadata": program_metadata,
            "rule_failures": rule_failures,
            "prerequisite_failures": prereq_failures,
            "unsupported_prerequisites": unsupported_prereqs,
            "findings": structured_findings,
            "warnings": structured_warnings,
            "notes": report_notes,
            "offering_violations": offering_violations,
        }
        if rule_process_error_output:
            failed_entry["error_output"] = rule_process_error_output

        results.append(failed_entry)
        detail_lines: list[str] = []
        accepted_findings = [f for f in structured_findings if f.get("accepted")]

        if structured_findings or rule_failures or prereq_failures or unsupported_prereqs:
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
            nonstandard_finding_lines = [
                f
                for f in active_findings
                if str(f.get("kind", "")) == "nonstandard_period"
            ]
            annual_load_finding_lines = [
                f for f in active_findings if str(f.get("kind", "")) == "annual_load"
            ]

            if rule_finding_lines:
                detail_lines.append(f"rule_failures={len(rule_finding_lines)}")
                for f in rule_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
                    if fid and f.get("overrideable"):
                        detail_lines.append(
                            _format_add_override_hint(rule_file_rel, plan_file_rel, fid)
                        )
            elif not structured_findings and rule_failures:
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
                            _format_add_override_hint(rule_file_rel, plan_file_rel, fid)
                        )
            elif not structured_findings and prereq_failures:
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
            elif not structured_findings and unsupported_prereqs:
                detail_lines.append(
                    f"unsupported_prerequisites={len(unsupported_prereqs)}"
                )
                for failure in unsupported_prereqs:
                    detail_lines.append(f"  - {failure}")

            if nonstandard_finding_lines:
                detail_lines.append(
                    f"nonstandard_period_failures={len(nonstandard_finding_lines)}"
                )
                for f in nonstandard_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
                    if fid and f.get("overrideable"):
                        detail_lines.append(
                            _format_add_override_hint(rule_file_rel, plan_file_rel, fid)
                        )

            if annual_load_finding_lines:
                detail_lines.append(
                    f"annual_load_failures={len(annual_load_finding_lines)}"
                )
                for f in annual_load_finding_lines:
                    fid = str(f.get("failure_id", ""))
                    msg = str(f.get("message", ""))
                    detail_lines.append(f"  - [{fid}] {msg}" if fid else f"  - {msg}")
                    if fid and f.get("overrideable"):
                        detail_lines.append(
                            _format_add_override_hint(rule_file_rel, plan_file_rel, fid)
                        )

        if rule_process_error_output:
            detail_lines.append("rule_process_error=1")
            detail_lines.append(f"  - {rule_process_error_output}")

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

        if accepted_findings:
            detail_lines.append(f"overridden_findings={len(accepted_findings)}")
            for f in accepted_findings:
                fid = str(f.get("failure_id", ""))
                msg = str(f.get("message", ""))
                detail_lines.append(
                    f"  - (overridden) [{fid}] {msg}" if fid else f"  - (overridden) {msg}"
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
        "skipped_placeholder": skipped_placeholder,
        "accepted": accepted,
    }
    write_validation_report(report_path, report)
    print(f"\n☑️ Validation report written to: {report_path}")

    if args.human_report == "html":
        html_text = render_validation_report_html(
            report,
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=bool(args.include_all_warnings),
        )
        write_validation_report_html(html_report_path, html_text)
        print(f"☑️ Human-readable report written to: {html_report_path}")

    print()
    if failed == 0:
        print("🎉 All plans validated successfully!")
        return 0

    print(f"❌ {failed} plan(s) failed validation")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

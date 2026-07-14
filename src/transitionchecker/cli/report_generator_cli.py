from __future__ import annotations

import argparse
import fnmatch
import json
import re
from datetime import datetime, timezone
from glob import glob
from html import escape
from pathlib import Path
from typing import cast

from transitionchecker.core import as_json_object, period_rank
from transitionchecker.core.period_utils import (
    duration_years_between_periods,
    format_duration_years,
    period_short_label,
)
from transitionchecker.core.validation_report_html import (
    render_validation_table_report_html,
    write_validation_report_html,
)


_PLAN_IDENTITY_RE = re.compile(r"^(?P<plan>.+)_(?P<year>\d{4})_(?P<term>[TS]\d)$")


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate consolidated reporting artifacts from validation results.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report",
        help="Combine one or more validation results JSON files into HTML table report.",
    )
    report_parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "Input validation result files (supports explicit paths and shell-style "
            "glob patterns)."
        ),
    )
    report_parser.add_argument(
        "--filter",
        dest="plan_filter",
        default="*",
        help="Glob filter applied to the plan code (for example: '*3707*').",
    )
    report_parser.add_argument(
        "--output",
        required=True,
        help="Output HTML report path.",
    )
    report_parser.add_argument(
        "--title",
        default="Plan Validation Consolidated Report",
        help="Report heading title.",
    )

    pack_parser = subparsers.add_parser(
        "pack",
        help="Create reporting pack zip from an individual plan (reserved for next phase).",
    )
    pack_parser.add_argument("plan", help="Path to individual plan JSON file.")
    pack_parser.add_argument("--output", required=True, help="Output ZIP file path.")

    return parser


def _contains_glob(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _expand_input_paths(patterns: list[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        matches = [Path(match) for match in glob(pattern)] if _contains_glob(pattern) else []
        candidates = matches if matches else [Path(pattern)]
        for candidate in candidates:
            path = candidate.resolve()
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)

    return resolved


def _display_source_path(path: Path, cwd: Path) -> str:
    """Return a safe display path for source listings.

    Prefer CWD-relative paths when possible. If the path is outside CWD,
    return filename only to avoid leaking host filesystem layout.
    """

    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return path.name


def _as_object_mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    out: list[dict[str, object]] = []
    for item in cast(list[object], value):
        item_obj = as_json_object(item)
        if item_obj is not None:
            out.append(cast(dict[str, object], item_obj))
    return out


def _status_display(status: str) -> str | None:
    normalized = status.strip().lower()
    if normalized == "valid":
        return "OK"
    if normalized == "failed":
        return "FAIL"
    if normalized == "accepted":
        return "ACCEPTED"
    if normalized == "skipped_placeholder":
        return None
    return normalized.upper() if normalized else ""


def _parse_plan_identity(plan_file: str) -> tuple[str, str, str, str]:
    stem = Path(plan_file).stem
    match = _PLAN_IDENTITY_RE.match(stem)
    if not match:
        return stem, "", "", ""

    plan = match.group("plan")
    intake_year = match.group("year")
    intake_term = match.group("term")
    cohort = f"{intake_year}_{intake_term}"
    return plan, cohort, intake_year, intake_term


def _resolve_plan_path(report_file: Path, plan_file: str) -> Path | None:
    candidate = Path(plan_file)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    cwd_candidate = Path.cwd() / plan_file
    if cwd_candidate.is_file():
        return cwd_candidate.resolve()

    report_relative = report_file.parent / plan_file
    if report_relative.is_file():
        return report_relative.resolve()

    return None


def _extract_exit_period(plan_data: dict[str, object]) -> tuple[str, str]:
    courses = _as_object_mapping_list(plan_data.get("courses", []))

    latest: tuple[int, int, str] | None = None
    for course in courses:
        year_value = course.get("year")
        period_value = course.get("period")
        if not isinstance(year_value, int) or not isinstance(period_value, str):
            continue

        rank = period_rank(period_value)
        if rank is None:
            continue

        current = (year_value, rank, period_short_label(period_value))
        if latest is None or current > latest:
            latest = current

    if latest is None:
        return "", ""

    return str(latest[0]), latest[2]


def _render_findings_text(result: dict[str, object]) -> str:
    findings = _as_object_mapping_list(result.get("findings", []))
    if not findings:
        return "none"

    parts: list[str] = []
    for finding in findings:
        failure_id = str(finding.get("failure_id", "") or "").strip()
        message = str(finding.get("message", "") or "").strip()
        accepted_prefix = "(accepted) " if bool(finding.get("accepted", False)) else ""

        if failure_id:
            text = f"{accepted_prefix}[{failure_id}] {message}".strip()
        else:
            text = f"{accepted_prefix}{message}".strip()

        if text:
            parts.append(text)

    return " ; ".join(parts) if parts else "none"


def _render_findings_html(result: dict[str, object]) -> str:
    findings = _as_object_mapping_list(result.get("findings", []))
    if not findings:
        return "none"

    items: list[str] = []
    for finding in findings:
        failure_id = str(finding.get("failure_id", "") or "").strip()
        message = str(finding.get("message", "") or "").strip()
        accepted_prefix = "(accepted) " if bool(finding.get("accepted", False)) else ""

        if failure_id:
            text = f"{accepted_prefix}[{failure_id}] {message}".strip()
        else:
            text = f"{accepted_prefix}{message}".strip()

        if text:
            items.append(f"<li>{escape(text)}</li>")

    if not items:
        return "none"

    return f"<ul class=\"findings-list\">{''.join(items)}</ul>"


def _notes_text_list(notes: dict[str, object], key: str) -> str:
    value = notes.get(key, [])
    if not isinstance(value, list):
        return ""
    text_items = [str(item).strip() for item in cast(list[object], value)]
    return "; ".join(item for item in text_items if item)


def _derive_impact_assessment_status(graduation_outcome: str, adjustment_type: str) -> str:
    not_assessed = "not yet assessed"

    graduation_value = graduation_outcome.strip()
    adjustment_value = adjustment_type.strip()
    graduation_complete = bool(graduation_value) and graduation_value.lower() != not_assessed
    adjustment_complete = bool(adjustment_value) and adjustment_value.lower() != not_assessed

    return "COMPLETE" if graduation_complete and adjustment_complete else "PENDING"


def _build_report_rows(
    *,
    report_paths: list[Path],
    plan_filter: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    plan_cache: dict[Path, dict[str, object]] = {}

    for report_path in report_paths:
        report_text = report_path.read_text(encoding="utf-8")
        report_raw = json.loads(report_text)
        report_obj = as_json_object(report_raw)
        if report_obj is None:
            continue

        results = _as_object_mapping_list(report_obj.get("results", []))
        for result in results:
            status_value = result.get("status")
            status_display = _status_display(str(status_value) if status_value is not None else "")
            if status_display is None:
                continue

            plan_file = str(result.get("plan_file", ""))
            json_filename = Path(plan_file).name
            plan, cohort, intake_year, intake_term = _parse_plan_identity(plan_file)
            if not fnmatch.fnmatch(plan, plan_filter):
                continue

            exit_year = ""
            exit_term = ""
            plan_path = _resolve_plan_path(report_path, plan_file)
            if plan_path is not None:
                if plan_path not in plan_cache:
                    try:
                        plan_raw = json.loads(plan_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        plan_cache[plan_path] = {}
                    else:
                        plan_obj = as_json_object(plan_raw)
                        plan_cache[plan_path] = cast(dict[str, object], plan_obj or {})
                exit_year, exit_term = _extract_exit_period(plan_cache[plan_path])

            duration_years = format_duration_years(
                duration_years_between_periods(
                    int(intake_year),
                    intake_term,
                    int(exit_year),
                    exit_term,
                )
                if intake_year and intake_term and exit_year and exit_term
                else None
            )

            notes_obj = as_json_object(result.get("notes", {}))
            notes = cast(dict[str, object], notes_obj or {})
            graduation_outcome = str(notes.get("graduate_outcome", "") or "")
            adjustment_type = str(notes.get("adjustment_type", "") or "")

            rows.append(
                {
                    "json_filename": json_filename,
                    "plan": plan,
                    "cohort": cohort,
                    "intake_year": intake_year,
                    "intake_term": intake_term,
                    "exit_year": exit_year,
                    "exit_term": exit_term,
                    "duration_years": duration_years,
                    "validation_findings": _render_findings_text(result),
                    "validation_findings_html": _render_findings_html(result),
                    "validation_status": status_display,
                    "graduation_outcome": graduation_outcome,
                    "adjustment_type": adjustment_type,
                    "reviewer_notes": _notes_text_list(notes, "for_reviewers"),
                    "student_notes": _notes_text_list(notes, "for_students"),
                    "impact_assessment_status": _derive_impact_assessment_status(
                        graduation_outcome,
                        adjustment_type,
                    ),
                }
            )

    return rows


def _run_report(args: argparse.Namespace) -> int:
    input_patterns = cast(list[str], args.inputs)
    report_paths = _expand_input_paths(input_patterns)
    cwd = Path.cwd().resolve()

    missing = [str(path) for path in report_paths if not path.is_file()]
    if missing:
        print("Error: input report file(s) not found:")
        for item in missing:
            print(f"  - {item}")
        return 1

    rows = _build_report_rows(report_paths=report_paths, plan_filter=str(args.plan_filter))
    html = render_validation_table_report_html(
        title=str(args.title),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        source_files=[_display_source_path(path, cwd) for path in report_paths],
        rows=rows,
    )

    output_path = Path(str(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_validation_report_html(output_path, html)
    print(f"Wrote report: {output_path}")
    print(f"Rows included: {len(rows)}")
    return 0


def _run_pack(_args: argparse.Namespace) -> int:
    print("report-generator pack is reserved for the next implementation phase.")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if args.command == "report":
        return _run_report(args)
    if args.command == "pack":
        return _run_pack(args)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

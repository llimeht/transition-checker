from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict, cast

from transitionchecker.core import (
    allowed_periods_for_course,
    as_text,
    flatten_offerings,
    load_offerings as load_offer_map,
    normalize_offerings_course_code,
)


class OfferingViolation(TypedDict):
    """Represents a single offering validation violation."""

    course_code: str
    planned_period: str
    allowed_periods: list[str]
    error_type: str  # "course_not_found" or "period_not_allowed"


class PlanCourse(TypedDict, total=False):
    """Course record from plan JSON."""

    code: str
    period: str
    year: int


class PlanSummary(TypedDict):
    """Plan metadata shown in offering results."""

    sheet: str
    intake: str


class PlanDocument(TypedDict, total=False):
    """Top-level plan JSON document."""

    sheet: str
    intake: str
    courses: list[PlanCourse]


class OfferingCheckResult(TypedDict):
    """Result of checking a plan against offerings."""

    plan_file: str
    plan_summary: PlanSummary
    valid: bool
    violations_count: int
    violations: list[OfferingViolation]


def _resolve_default_offerings_file(plan_file: Path) -> Path:
    """Resolve offerings JSON from the plan's local filesystem context."""

    for parent in [plan_file.parent, *list(plan_file.parent.parents)[:3]]:
        same_dir_offerings = parent / "offerings.json"
        if same_dir_offerings.is_file():
            return same_dir_offerings.resolve()

        plans_offerings = parent / "plans" / "offerings.json"
        if plans_offerings.is_file():
            return plans_offerings.resolve()

    return (plan_file.parent / "offerings.json").resolve()


def _normalize_lookup_course_code(raw_course_code: str) -> str:
    """Normalize a course code for offerings lookup, removing internal whitespace."""

    return normalize_offerings_course_code(raw_course_code)


def load_offerings(offerings_file: Path) -> dict[str, list[str]]:
    """Load and sanitize course offerings mapping.

    Args:
        offerings_file: Path to offerings JSON file.

    Returns:
        Mapping of course code to allowed periods.
    """
    offering_map = load_offer_map(offerings_file)
    return flatten_offerings(offering_map)


def load_plan(plan_file: Path) -> PlanDocument:
    """Load a plan JSON document.

    Args:
        plan_file: Path to plan JSON file.

    Returns:
        Parsed plan document as a typed dictionary.
    """
    if not plan_file.is_file():
        raise FileNotFoundError(f"Plan file not found: {plan_file}")

    with open(plan_file, "r", encoding="utf-8") as fh:
        raw_plan: object = json.load(fh)

    if not isinstance(raw_plan, dict):
        raise ValueError(
            f"Plan file must contain a JSON object, got {type(raw_plan).__name__}"
        )

    return cast(PlanDocument, raw_plan)


def validate_plan_offerings(
    plan: PlanDocument,
    offerings: dict[str, list[str]],
) -> list[OfferingViolation]:
    """
    Validate that all courses in a plan exist in offerings and their periods are allowed.

    Args:
        plan: Plan JSON object with courses list
        offerings: Dictionary mapping course codes to allowed periods

    Returns:
        List of violations found (empty if plan is valid)
    """
    violations: list[OfferingViolation] = []

    courses = plan.get("courses")
    if not isinstance(courses, list):
        return violations

    for course in courses:
        raw_course_code = as_text(course.get("code", ""))
        if not raw_course_code:
            continue

        course_code = _normalize_lookup_course_code(raw_course_code)
        if not course_code:
            continue

        planned_period = as_text(course.get("period", ""))
        if not planned_period:
            continue

        if course_code not in offerings:
            violations.append(
                {
                    "course_code": course_code,
                    "planned_period": planned_period,
                    "allowed_periods": [],
                    "error_type": "course_not_found",
                }
            )
            continue

        allowed_periods = offerings[course_code]
        if planned_period not in allowed_periods:
            violations.append(
                {
                    "course_code": course_code,
                    "planned_period": planned_period,
                    "allowed_periods": allowed_periods,
                    "error_type": "period_not_allowed",
                }
            )

    return violations


def check_plan(plan_file: Path, offerings_file: Path) -> OfferingCheckResult:
    """
    Check a single plan against offerings.

    Args:
        plan_file: Path to plan JSON file
        offerings_file: Path to offerings JSON file

    Returns:
        OfferingCheckResult with violations found
    """
    offering_map = load_offer_map(offerings_file)
    plan = load_plan(plan_file)

    violations: list[OfferingViolation] = []
    courses = plan.get("courses") if isinstance(plan.get("courses"), list) else []
    for course in cast(list[PlanCourse], courses):
        raw_course_code = as_text(course.get("code", ""))
        if not raw_course_code:
            continue
        course_code = _normalize_lookup_course_code(raw_course_code)
        if not course_code:
            continue

        planned_period = as_text(course.get("period", ""))
        if not planned_period:
            continue

        planned_year_raw = course.get("year")
        planned_year = planned_year_raw if isinstance(planned_year_raw, int) else None
        allowed_periods = allowed_periods_for_course(
            offering_map, course_code, year=planned_year
        )
        if not allowed_periods:
            violations.append(
                {
                    "course_code": course_code,
                    "planned_period": planned_period,
                    "allowed_periods": [],
                    "error_type": "course_not_found",
                }
            )
            continue

        if planned_period not in allowed_periods:
            violations.append(
                {
                    "course_code": course_code,
                    "planned_period": planned_period,
                    "allowed_periods": allowed_periods,
                    "error_type": "period_not_allowed",
                }
            )

    plan_summary: PlanSummary = {
        "sheet": as_text(plan.get("sheet", "")),
        "intake": as_text(plan.get("intake", "")),
    }

    return {
        "plan_file": str(plan_file),
        "plan_summary": plan_summary,
        "valid": len(violations) == 0,
        "violations_count": len(violations),
        "violations": violations,
    }


def format_violations_for_console(result: OfferingCheckResult) -> str:
    """Render offering validation result for console output.

    Args:
        result: Offering check result payload.

    Returns:
        Multi-line formatted text summary.
    """
    lines: list[str] = []

    plan_name = Path(result["plan_file"]).name
    summary = result["plan_summary"]
    sheet = summary["sheet"]
    intake = summary["intake"]

    violations = result["violations"]

    if not violations:
        lines.append(f"  ✓ {plan_name}: No offering violations")
        return "\n".join(lines)

    lines.append(
        f"  ✗ {plan_name} ({sheet} / {intake}): {len(violations)} violation(s)"
    )

    for i, v in enumerate(violations, 1):
        code = v["course_code"]
        planned = v["planned_period"]
        error_type = v["error_type"]
        allowed = v["allowed_periods"]

        if error_type == "course_not_found":
            lines.append(f"    [{i}] {code} not found in offerings")
        else:
            allowed_str = ", ".join(allowed) if allowed else "(none defined)"
            lines.append(
                f"    [{i}] {code} planned for '{planned}' but allowed in: {allowed_str}"
            )

    return "\n".join(lines)


def write_violations_json(result: OfferingCheckResult, output_file: Path) -> None:
    """Persist full offering check result to JSON.

    Args:
        result: Offering check result payload.
        output_file: Destination JSON file path.
    """
    with open(output_file, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)


def _build_cli_parser() -> argparse.ArgumentParser:
    """Construct CLI parser for offerings validation.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Validate an exported plan JSON file against allowed offering periods "
            "for each course."
        ),
        epilog=(
            "Examples:\n"
            "  offering-checker plans/CEIC/CEICAH3707_2026_T1.json\n"
            "  offering-checker plan.json --offerings plans/offerings.json --result-json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "plan_file",
        help="Path to the plan JSON file to validate",
    )
    parser.add_argument(
        "--offerings",
        default=None,
        help=(
            "Path to offerings JSON (default: offerings.json beside the plan file, "
            "otherwise discovered by walking upward from the plan directory)"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the check result as JSON",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print additional status messages",
    )
    parser.add_argument(
        "--result-json",
        action="store_true",
        help="Print machine-readable JSON result to stdout instead of console summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the offerings validation CLI command.

    Returns:
        Exit code 0 when valid, 1 when violations exist, 2 on input/parse errors.
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    plan_file = Path(args.plan_file).resolve()

    if args.offerings:
        offerings_file = Path(args.offerings).resolve()
    else:
        offerings_file = _resolve_default_offerings_file(plan_file)

    try:
        result = check_plan(plan_file, offerings_file)

        if args.result_json:
            print(json.dumps(result))
        else:
            console_output = format_violations_for_console(result)
            print(console_output)

        if args.output:
            output_file = Path(args.output).resolve()
            output_file.parent.mkdir(parents=True, exist_ok=True)
            write_violations_json(result, output_file)
            if args.verbose:
                print(f"✓ Violations written to: {output_file}")

        return 0 if result["valid"] else 1

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

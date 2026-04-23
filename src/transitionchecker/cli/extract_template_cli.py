"""Extract catalogue and template configuration data from a mapping workbook.

Outputs:
- plans/catalogue.json
- templates/template_configs.json

Usage:
    extract-template "plans/CEIC/CEIC Program Sequence Mapping.xlsx"

    extract-template "plans/CEIC/CEIC Program Sequence Mapping.xlsx" \
        --catalogue-output "plans/catalogue.json" \
        --template-output "templates/template_configs.json"
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, cast
import warnings

import openpyxl
import pandas as pd
from transitionchecker.core import period_rank
from transitionchecker.core.mapping_workbook import (
    extract_catalogue,
    find_template_sheet,
    iter_plans,
    normalize_plan_sheet_columns,
)
from transitionchecker.prereq_engine import (
    build_prerequisite_snapshot,
    classify_prerequisite_clause,
    PrerequisiteClauseClassification,
    salvage_mixed_prerequisite_clause,
)


warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


def write_prerequisite_snapshot(
    catalogue: dict[str, dict[str, Any]],
    output_path: Path,
    source_catalogue: str,
) -> None:
    """Write prerequisite parse snapshot JSON to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_prerequisite_snapshot(catalogue, source_catalogue)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    print(f"Prerequisite snapshot written to {output_path}")


def _period_rank(period: str) -> int:
    rank = period_rank(period, fallback=999)
    assert rank is not None
    return rank


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        if not value.strip():
            return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_intake_year(intake: str) -> int | None:
    match = re.search(r"\b(\d{4})\b", intake)
    return int(match.group(1)) if match else None


def build_year_structure(plan: pd.DataFrame, intake: str) -> list[dict[str, Any]]:
    """Build years/periods/max_slots from an intake plan block."""
    intake_year = _parse_intake_year(intake)
    grouping: dict[tuple[str, int | None], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for _, row in plan.iterrows():
        enrol_year = str(row.get("EnrolYear", "")).strip()
        year_value = _to_int(row.get("Year"))
        period = str(row.get("Period", "")).strip()
        course_n_raw = row.get("CourseN")
        if not enrol_year or not period or course_n_raw is None:
            continue
        # CourseN is "Course 1", "Course 2", etc. — extract the integer
        m = re.search(r"\d+", str(course_n_raw))
        if m is None:
            continue
        course_n = int(m.group())
        current = grouping[(enrol_year, year_value)][period]
        grouping[(enrol_year, year_value)][period] = max(current, course_n)

    year_entries: list[dict[str, Any]] = []
    for (enrol_year, sheet_year), period_counts in sorted(
        grouping.items(), key=lambda item: item[0][0]
    ):
        year_match = re.search(r"(\d+)", enrol_year)
        year_index = 1
        if year_match is not None:
            parsed_year_index = _to_int(year_match.group(1))
            if parsed_year_index is not None:
                year_index = parsed_year_index

        computed_year = sheet_year
        if computed_year is None and intake_year is not None:
            computed_year = intake_year + (year_index - 1)

        periods: list[dict[str, str | int]] = []
        for period, slots in sorted(
            period_counts.items(), key=lambda item: _period_rank(item[0])
        ):
            periods.append({"period": period, "max_slots": int(slots)})
        year_entries.append(
            {
                "enrol_year": enrol_year,
                "year": computed_year,
                "periods": periods,
            }
        )
    return year_entries


def extract_template_configs_from_workbook(excel_path: Path) -> dict[str, Any]:
    """Build program-agnostic template config keyed only by intake."""
    print("\n=== EXTRACTING TEMPLATE CONFIGS ===")
    dfs = pd.read_excel(excel_path, sheet_name=None)  # pyright: ignore[reportUnknownMemberType]

    # Read slot structure from the template sheet — the one template tab in the workbook.
    # These define every (year, period, CourseN) row with blank codes, giving the
    # authoritative max_slots per period without depending on filled-in plan data.
    _, template_df = find_template_sheet(dfs)

    intake_year_period_slots: dict[str, dict[tuple[str, int | None, str], int]] = (
        defaultdict(dict)
    )

    normalized_df = normalize_plan_sheet_columns(template_df)
    for intake, plan in iter_plans(normalized_df):
        if not intake:
            continue
        year_entries = build_year_structure(plan, intake)
        for year_entry in year_entries:
            enrol_year = str(year_entry.get("enrol_year", "")).strip()
            year_val = year_entry.get("year")
            periods = year_entry.get("periods", [])
            for period_entry in periods:
                period = str(period_entry.get("period", "")).strip()
                slots = int(period_entry.get("max_slots", 0))
                if not enrol_year or not period:
                    continue
                key = (enrol_year, year_val, period)
                # Keep the highest observed slot count for a generic template.
                intake_year_period_slots[intake][key] = max(
                    intake_year_period_slots[intake].get(key, 0), slots
                )

    intakes: dict[str, Any] = {}
    for intake, entries in sorted(intake_year_period_slots.items()):
        grouped: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
        for (enrol_year, year_val, period), slots in entries.items():
            grouped[(enrol_year, year_val)].append(
                {"period": period, "max_slots": slots}
            )

        years: list[dict[str, Any]] = []
        for (enrol_year, year_val), period_entries in sorted(
            grouped.items(), key=lambda item: item[0][0]
        ):
            years.append(
                {
                    "enrol_year": enrol_year,
                    "year": year_val,
                    "periods": sorted(
                        period_entries, key=lambda p: _period_rank(p["period"])
                    ),
                }
            )
        intakes[intake] = {"years": years}

    print(f"Extracted template configs for {len(intakes)} intakes")
    return {"intakes": intakes}


def lint_prerequisites(catalogue: dict[str, dict[str, Any]], output: str | None = None) -> int:
    """Lint prerequisites in the catalogue and report unrecognized ones."""
    from transitionchecker.prereq_engine import parse_prerequisite_field
    lint_results: list[dict[str, Any]] = []
    for course_code, course in catalogue.items():
        prereq = str(course.get("prerequisites", ""))
        _, _, error = parse_prerequisite_field(prereq)
        if error:
            classification, matched_families = classify_prerequisite_clause(prereq)
            salvaged = False
            salvaged_expr: str = ""
            salvage_error: str = ""
            if classification is PrerequisiteClauseClassification.MIXED:
                salvaged, salvage_expr_obj, salvage_error_obj = salvage_mixed_prerequisite_clause(
                    prereq,
                    matched_families,
                )
                if salvage_expr_obj is not None:
                    salvaged_expr = json.dumps(salvage_expr_obj, sort_keys=True)
                if salvage_error_obj is not None:
                    salvage_error = salvage_error_obj
            lint_results.append({
                "course_code": str(course_code),
                "prerequisites": prereq,
                "error": str(error),
                "classification": classification.value,
                "matched_families": ",".join(matched_families),
                "salvaged": salvaged,
                "salvaged_expr": salvaged_expr,
                "salvage_error": salvage_error,
            })
    if output:
        out_path = Path(output)
        if out_path.suffix.lower() == ".csv":
            import csv
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "course_code",
                        "prerequisites",
                        "error",
                        "classification",
                        "matched_families",
                        "salvaged",
                        "salvaged_expr",
                        "salvage_error",
                    ],
                )
                writer.writeheader()
                writer.writerows(lint_results)
            print(f"Lint results written to {out_path} (CSV)")
        elif out_path.suffix.lower() == ".json":
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(lint_results, f, indent=2)
            print(f"Lint results written to {out_path} (JSON)")
        else:
            print(f"ERROR: Unknown lint output file extension: {out_path.suffix}")
            return 1
    else:
        # Print to stdout
        if lint_results:
            print("\n=== LINT: Unrecognized Prerequisites ===")
            for entry in lint_results:
                print(
                    f"{entry['course_code']}: '{entry['prerequisites']}' -> {entry['error']} "
                    f"[{entry['classification']}]"
                )
        else:
            print("No unrecognized prerequisites found.")
    # Exit with nonzero code if any errors found
    return 1 if lint_results else 0

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract catalogue and intake templates from an Excel mapping workbook.",
        epilog=(
            "Examples:\n"
            "  extract-template 'plans/CEIC/CEIC Program Sequence Mapping.xlsx'\n"
            "  extract-template 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' "
            "--catalogue-output plans/catalogue.json "
            "--template-output templates/template_configs.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "xlsx",
        nargs="?",
        help="Path to the mapping workbook (.xlsx), for example: plans/CEIC/CEIC Program Sequence Mapping.xlsx",
    )
    parser.add_argument(
        "--catalogue-input",
        default=None,
        help=(
            "Existing catalogue JSON path to use as input for lint and/or "
            "prerequisite snapshot export."
        ),
    )
    parser.add_argument(
        "--catalogue-output",
        default="plans/catalogue.json",
        help="Output path for catalogue JSON (default: plans/catalogue.json, use NONE to suppress)",
    )
    parser.add_argument(
        "--template-output",
        default="templates/template_configs.json",
        help="Output path for template JSON (default: templates/template_configs.json, use NONE to suppress)",
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help="Lint all prerequisites in the extracted catalogue and report unrecognized ones."
    )
    parser.add_argument(
        "--lint-output",
        default=None,
        help="Output file for lint results (CSV or JSON, determined by file extension)."
    )
    parser.add_argument(
        "--prereq-snapshot-output",
        default=None,
        help=(
            "Write prerequisite parse snapshot JSON for the extracted catalogue; "
            "includes raw prerequisite text and parser output per course."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    excel_path = Path(args.xlsx) if args.xlsx else None
    catalogue_file = Path(args.catalogue_output)
    template_file = Path(args.template_output)
    plans_dir = catalogue_file.parent
    templates_dir = template_file.parent
    snapshot_file = Path(args.prereq_snapshot_output) if args.prereq_snapshot_output else None
    catalogue_input = Path(args.catalogue_input) if args.catalogue_input else None

    if excel_path is None and catalogue_input is None:
        print("ERROR: Provide either xlsx workbook path or --catalogue-input.")
        return 1

    if catalogue_input is not None and not args.lint and snapshot_file is None:
        print(
            "ERROR: --catalogue-input requires --lint and/or --prereq-snapshot-output."
        )
        return 1

    catalogue: dict[str, dict[str, Any]]

    if catalogue_input is not None:
        if not catalogue_input.exists():
            print(f"ERROR: Catalogue input not found at {catalogue_input}")
            return 1
        try:
            with open(catalogue_input, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("catalogue JSON root must be an object")
            catalogue = cast(dict[str, dict[str, Any]], loaded)
        except Exception as exc:
            print(f"ERROR: Failed to read catalogue input: {exc}")
            return 1
    else:
        assert excel_path is not None
        print(f"Opening workbook: {excel_path}")
        if not excel_path.exists():
            print(f"ERROR: Workbook not found at {excel_path}")
            return 1

        plans_dir.mkdir(exist_ok=True)
        templates_dir.mkdir(exist_ok=True)

        try:
            workbook = openpyxl.load_workbook(excel_path, data_only=True)
            catalogue = extract_catalogue(workbook)
            workbook.close()
        except Exception as exc:
            print(f"ERROR: Failed to extract catalogue: {exc}")
            return 1

    if snapshot_file is not None:
        try:
            source_catalogue = str(catalogue_input) if catalogue_input else str(catalogue_file)
            write_prerequisite_snapshot(catalogue, snapshot_file, source_catalogue)
        except Exception as exc:
            print(f"ERROR: Failed to write prerequisite snapshot: {exc}")
            return 1

    if args.lint:
        return lint_prerequisites(catalogue, args.lint_output)

    if excel_path is None:
        print("No workbook provided; skipping template extraction.")
        return 0

    export_templates = template_file.name != "NONE"
    export_catalogue = catalogue_file.name != "NONE"

    reporting: list[str] = []

    if export_templates:
        try:
            template_configs = extract_template_configs_from_workbook(excel_path)
        except Exception as exc:
            print(f"ERROR: Failed to extract template configs: {exc}")
            return 1
        with open(template_file, "w", encoding="utf-8") as fh:
            json.dump(template_configs, fh, indent=2)
        reporting.extend([
            f"Template config file: {template_file}",
            f"Intakes in template config: {len(template_configs.get('intakes', {}))}",
        ])

    if export_catalogue:
        with open(catalogue_file, "w", encoding="utf-8") as fh:
            json.dump(catalogue, fh, indent=2)
            reporting.extend([
                    f"Catalogue file: {catalogue_file}",
                    f"Catalogue entries: {len(catalogue)}"
            ])

    print("\n=== COMPLETE ===")
    print("\n".join(reporting))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

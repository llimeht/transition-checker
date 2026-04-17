"""Extract catalogue and template configuration data from a mapping workbook.

Outputs:
- plans/catalogue.json
- templates/template_configs.json

Usage:
    python3 extract_template.py "plans/CEIC/CEIC Program Sequence Mapping.xlsx"

    python3 extract_template.py "plans/CEIC/CEIC Program Sequence Mapping.xlsx" \
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
from typing import Any, Generator
import warnings

import openpyxl
import pandas as pd  # type: ignore[import-untyped]
from transitionchecker.core import period_rank


warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


def _period_rank(period: str) -> int:
    rank = period_rank(period, fallback=999)
    assert rank is not None
    return rank


def extract_catalogue(workbook: Any) -> dict[str, dict[str, Any]]:
    """Extract course catalogue from Cat sheet."""
    print("\n=== EXTRACTING CATALOGUE ===")
    try:
        cat_sheet = workbook["Cat"]
    except KeyError:
        raise ValueError("Cat sheet not found in workbook")

    catalogue: dict[str, dict[str, Any]] = {}
    for row in cat_sheet.iter_rows(min_row=2, values_only=False):
        if not row or not row[0].value:
            continue

        course_code = str(row[0].value).strip()
        if not course_code:
            continue

        title = str(row[1].value).strip() if len(row) > 1 and row[1].value else ""
        uoc: int | None = None
        if len(row) > 2 and row[2].value is not None:
            try:
                uoc = int(row[2].value)
            except (TypeError, ValueError):
                uoc = None
        prereq = str(row[3].value).strip() if len(row) > 3 and row[3].value else "."
        prerequisites_pg = (
            str(row[4].value).strip() if len(row) > 4 and row[4].value else ""
        )

        catalogue[course_code] = {
            "title": title,
            "uoc": uoc,
            "prerequisites": prereq,
            "prerequisites_pg": prerequisites_pg or None,
        }

    print(f"Extracted {len(catalogue)} catalogue entries")
    return catalogue


def iter_program_sheets(
    dfs: dict[str, pd.DataFrame],
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield only program mapping sheets (skip template/internal sheets)."""
    for sheet_name, df in dfs.items():
        if sheet_name in ("Cat", "Lookup"):
            continue
        if "{" in sheet_name:
            continue
        columns = list(df.columns)
        if len(columns) >= 8:
            columns[0:8] = [
                "EnrolYear",
                "Year",
                "Period",
                "CourseN",
                "Code",
                "Title",
                "UoC",
                "Prerequisites",
            ]
            df = df.copy()
            df.columns = columns
        yield sheet_name, df


def iter_intakes(
    sheet: pd.DataFrame, start_row: int = 4
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield intake blocks from a program sheet."""
    trimmed = sheet.iloc[start_row:].reset_index(drop=True)
    col_a = trimmed.iloc[:, 0]
    mask = col_a.notna() & (col_a.astype(str).str.strip() != "")
    groups = (mask != mask.shift()).cumsum()

    for _, sub in trimmed[mask].groupby(groups[mask]):
        intake = str(sub.iloc[0, 3]).strip()
        rows = sub.iloc[1:].reset_index(drop=True)
        yield intake, rows


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

    # Read slot structure from the template sheet(s) — those with { in the name.
    # These define every (year, period, CourseN) row with blank codes, giving the
    # authoritative max_slots per period without depending on filled-in plan data.
    template_dfs = {name: df for name, df in dfs.items() if "{" in name}
    if not template_dfs:
        raise ValueError("No template sheet (e.g. {ABCD1234}) found in workbook")

    intake_year_period_slots: dict[str, dict[tuple[str, int | None, str], int]] = (
        defaultdict(dict)
    )

    for _, df in template_dfs.items():
        columns = list(df.columns)
        if len(columns) >= 8:
            columns[0:8] = [
                "EnrolYear",
                "Year",
                "Period",
                "CourseN",
                "Code",
                "Title",
                "UoC",
                "Prerequisites",
            ]
            df = df.copy()
            df.columns = columns
        for intake, plan in iter_intakes(df):
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract catalogue and intake templates from an Excel mapping workbook.",
        epilog=(
            "Examples:\n"
            '  python3 extract_template.py "plans/CEIC/CEIC Program Sequence Mapping.xlsx"\n'
            '  python3 extract_template.py "plans/CEIC/CEIC Program Sequence Mapping.xlsx" '
            '--catalogue-output "plans/catalogue.json" '
            '--template-output "templates/template_configs.json"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "xlsx",
        help="Path to the mapping workbook (.xlsx), for example: plans/CEIC/CEIC Program Sequence Mapping.xlsx",
    )
    parser.add_argument(
        "--catalogue-output",
        default="plans/catalogue.json",
        help="Output path for catalogue JSON (default: plans/catalogue.json)",
    )
    parser.add_argument(
        "--template-output",
        default="templates/template_configs.json",
        help="Output path for template JSON (default: templates/template_configs.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    excel_path = Path(args.xlsx)
    catalogue_file = Path(args.catalogue_output)
    template_file = Path(args.template_output)
    plans_dir = catalogue_file.parent
    templates_dir = template_file.parent

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

    try:
        template_configs = extract_template_configs_from_workbook(excel_path)
    except Exception as exc:
        print(f"ERROR: Failed to extract template configs: {exc}")
        return 1

    with open(catalogue_file, "w", encoding="utf-8") as fh:
        json.dump(catalogue, fh, indent=2)
    with open(template_file, "w", encoding="utf-8") as fh:
        json.dump(template_configs, fh, indent=2)

    print("\n=== COMPLETE ===")
    print(f"Catalogue file: {catalogue_file}")
    print(f"Template config file: {template_file}")
    print(f"Catalogue entries: {len(catalogue)}")
    print(f"Intakes in template config: {len(template_configs.get('intakes', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

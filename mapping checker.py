#!/usr/bin/python3

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Generator, TypedDict
import warnings

import pandas as pd

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


class PlanCourse(TypedDict):
    enrol_year: int | None
    year: int | None
    period: str | None
    course_n: int | None
    code: str
    title: str | None
    uoc: int | None
    prerequisites: str | None


class PlanExport(TypedDict):
    sheet: str
    intake: str
    courses: list[PlanCourse]


def iter_sheets(dfs: dict[str, pd.DataFrame]) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """locate the sheets with enrolment plans"""
    for sheet_name, df in dfs.items():
        # filter internal and template sheets
        if "{" not in sheet_name and sheet_name not in ("Cat", "Lookup"):
            # FIXME: fragile resetting of column names
            columns = list(df.columns)
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
            df.columns = columns
            yield sheet_name, df


def iter_plans(sheet: pd.DataFrame, start_row: int = 4) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """locate the enrolment plans within each sheet"""
    # trim leading rows
    sheet = sheet.iloc[start_row:].reset_index(drop=True)

    # Column A (first column) used as separator signal
    col_a = sheet.iloc[:, 0]

    # True where row is part of a block
    mask = col_a.notna() & (col_a.astype(str).str.strip() != "")

    # Identify groups of consecutive True values
    groups = (mask != mask.shift()).cumsum()

    # iterate through the identified plans within each sheet
    for _, sub in sheet[mask].groupby(groups[mask]):
        intake = str(sub.iloc[0, 3]) # intake comment is in column D (index 3)
        sub = sub.iloc[1:].reset_index(drop=True)

        yield intake, sub


def course_terms(plan: pd.DataFrame) -> dict[str, set[str]]:
    offering: dict[str, set[str]] = defaultdict(set)
    for _, row in plan.iterrows():
        if pd.isna(row.Code):
            continue
        offering[row.Code].add(row.Period)
    return offering


def summarise_offerings(offerings: list[dict[str, set[str]]]) -> dict[str, set[str]]:
    summary: dict[str, set[str]] = defaultdict(set)
    for offering_plan in offerings:
        for course, period in offering_plan.items():
            summary[course].update(period)
    return summary


def print_offerings_summary(summary: dict[str, set[str]]):
    for course in sorted(summary.keys()):
        periods = summary[course]
        pdtxt = sorted([p for p in periods if not p.startswith("Term ")])
        if not pdtxt:
            continue
        print(f"{course:14} {" ".join(pdtxt)}")


def _to_int_or_none(val: Any) -> int | None:
    if pd.isna(val):
        return None
    return int(val)


def _to_str_or_none(val: Any) -> str | None:
    if pd.isna(val):
        return None
    return str(val)


def plan_to_dict(sheet_name: str, intake: str, plan: pd.DataFrame) -> PlanExport:
    """Serialize a plan DataFrame to a JSON-serializable dict preserving all 8 columns."""
    courses: list[PlanCourse] = []
    for _, row in plan.iterrows():
        if pd.isna(row["Code"]):
            continue
        courses.append({
            "enrol_year": _to_int_or_none(row["EnrolYear"]),
            "year": _to_int_or_none(row["Year"]),
            "period": _to_str_or_none(row["Period"]),
            "course_n": _to_int_or_none(row["CourseN"]),
            "code": str(row["Code"]),
            "title": _to_str_or_none(row["Title"]),
            "uoc": _to_int_or_none(row["UoC"]),
            "prerequisites": _to_str_or_none(row["Prerequisites"]),
        })
    return {"sheet": sheet_name, "intake": intake, "courses": courses}


def export_plan(sheet_name: str, intake: str, plan: pd.DataFrame, output_dir: str) -> str:
    """Write one plan to a JSON file; return the file path."""
    plan_dict = plan_to_dict(sheet_name, intake, plan)
    safe_name = f"{sheet_name}_{intake}".replace(" ", "_")
    filepath = os.path.join(output_dir, f"{safe_name}.json")
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(plan_dict, fh, indent=2)
    return filepath


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read an Excel sequence mapping file and export each plan as JSON.",
    )
    parser.add_argument("excel_file", help="Path to the Excel mapping file")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write plan JSON files (default: directory of the Excel file)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.excel_file))
    os.makedirs(output_dir, exist_ok=True)

    dfs: dict[str, pd.DataFrame] = pd.read_excel(args.excel_file, sheet_name=None)  # type: ignore

    offerings: list[dict[str, set[str]]] = []

    for sheet_name, df in iter_sheets(dfs):
        print(sheet_name)
        for intake, plan in iter_plans(df):
            print(f"  Intake {intake}")
            offering = course_terms(plan)
            offerings.append(offering)
            path = export_plan(sheet_name, intake, plan, output_dir)
            print(f"  -> {path}")

    print()
    print_offerings_summary(summarise_offerings(offerings))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())





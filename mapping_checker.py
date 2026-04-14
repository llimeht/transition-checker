#!/usr/bin/python3

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Generator, TypedDict
import warnings

import pandas as pd

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


class PlanCourse(TypedDict):
    enrol_year: str
    year: int
    period: str
    course_n: str
    code: str
    title: str
    uoc: int
    prerequisites: str


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


def format_offerings_summary(summary: dict[str, set[str]]) -> str:
    """Format offerings summary as a string."""
    lines: list[str] = []
    for course in sorted(summary.keys()):
        periods = summary[course]
        pdtxt = sorted([p for p in periods if not p.startswith("Term ")])
        if not pdtxt:
            continue
        lines.append(f"{course:14} {" ".join(pdtxt)}")
    return "\n".join(lines)


def write_offerings_file(summary: dict[str, set[str]], excel_filename: Path, output_dir: Path) -> Path:
    """Write offerings summary to a JSON file based on the Excel filename."""
    # Extract base name without extension
    base_name = excel_filename.stem
    filepath = output_dir / f"{base_name}_offerings.json"

    # Convert sets to sorted lists for JSON serialization
    offerings_dict = {course: sorted(list(periods)) for course, periods in summary.items()}

    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(offerings_dict, fh, indent=2)

    return filepath


def _to_string(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val)


def _to_int(val: Any) -> int:
    if pd.isna(val):
        raise ValueError("expected an integer-compatible value, got blank cell")
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if val.is_integer():
            return int(val)
        raise ValueError(f"expected an integer-compatible value, got {val!r}")
    return int(str(val).strip())


def plan_to_dict(sheet_name: str, intake: str, plan: pd.DataFrame) -> PlanExport:
    """Serialize a plan DataFrame to a JSON-serializable dict preserving all 8 columns."""
    courses: list[PlanCourse] = []
    for _, row in plan.iterrows():
        if pd.isna(row["Code"]):
            continue
        courses.append({
            "enrol_year": _to_string(row["EnrolYear"]),
            "year": _to_int(row["Year"]),
            "period": _to_string(row["Period"]),
            "course_n": _to_string(row["CourseN"]),
            "code": str(row["Code"]),
            "title": _to_string(row["Title"]),
            "uoc": _to_int(row["UoC"]),
            "prerequisites": _to_string(row["Prerequisites"]),
        })
    return {"sheet": sheet_name, "intake": intake, "courses": courses}


def export_plan(sheet_name: str, intake: str, plan: pd.DataFrame, output_dir: Path) -> Path:
    """Write one plan to a JSON file; return the file path."""
    plan_dict = plan_to_dict(sheet_name, intake, plan)
    safe_name = f"{sheet_name}_{intake}".replace(" ", "_")
    filepath = output_dir / f"{safe_name}.json"
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
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-v for INFO, -vv for DEBUG)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    # Configure logging based on verbosity
    if args.verbose >= 2:
        log_level = logging.DEBUG
    elif args.verbose >= 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(
        level=log_level,
        format="%(levelname)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    excel_file = Path(args.excel_file)
    output_dir_path = Path(args.output_dir) if args.output_dir else excel_file.resolve().parent
    output_dir_path.mkdir(parents=True, exist_ok=True)

    dfs: dict[str, pd.DataFrame] = pd.read_excel(excel_file, sheet_name=None)  # type: ignore

    offerings: list[dict[str, set[str]]] = []

    for sheet_name, df in iter_sheets(dfs):
        logger.info(f"Processing sheet: {sheet_name}")
        for intake, plan in iter_plans(df):
            logger.info(f"  Intake {intake}")
            offering = course_terms(plan)
            offerings.append(offering)
            path = export_plan(sheet_name, intake, plan, output_dir_path)
            logger.debug(f"  -> {path}")

    # Write offerings summary to file
    offerings_summary = summarise_offerings(offerings)
    offerings_path = write_offerings_file(offerings_summary, excel_file, output_dir_path)
    logger.info(f"Offerings summary written to: {offerings_path}")

    # Log the offerings summary
    logger.debug("Offerings summary:")
    logger.debug(format_offerings_summary(offerings_summary))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())





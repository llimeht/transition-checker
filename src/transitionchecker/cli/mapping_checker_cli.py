"""Export enrolment plans from course mapping spreadsheets.

This tool reads a sequence-mapping Excel workbook, extracts each intake plan,
exports per-plan JSON files, and writes a consolidated offerings summary in both
JSON and CSV formats.
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Generator, TypedDict
import warnings

import pandas as pd  # type: ignore[import-untyped]
from transitionchecker.utils.logging import configure_logging

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


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


def iter_sheets(
    dfs: dict[str, pd.DataFrame],
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield only workbook sheets that contain enrolment plans.

    Internal/template sheets (for example, "Cat" and "Lookup") are skipped.
    The first eight columns are normalised to the expected canonical names so
    downstream processing can rely on stable column labels.
    """
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


def iter_plans(
    sheet: pd.DataFrame, start_row: int = 4
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield each intake plan block from a normalised sheet.

    Args:
        sheet: A sheet already normalised by ``iter_sheets``.
        start_row: Number of header rows to skip before plan blocks begin.

    Yields:
        Tuples of ``(intake, plan_dataframe)``.
    """
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
        intake = str(sub.iloc[0, 3])  # intake comment is in column D (index 3)
        sub = sub.iloc[1:].reset_index(drop=True)

        yield intake, sub


def course_terms(plan: pd.DataFrame) -> dict[str, set[str]]:
    """Build course-to-period mappings for one plan.

    Args:
        plan: Plan rows containing at least Code and Period columns.

    Returns:
        Mapping of course code to the set of planned periods.
    """
    offering: dict[str, set[str]] = defaultdict(set)
    for _, row in plan.iterrows():
        if pd.isna(row.Code):
            continue
        offering[row.Code].add(row.Period)
    return offering


def summarise_offerings(offerings: list[dict[str, set[str]]]) -> dict[str, set[str]]:
    """Merge per-plan offerings into one consolidated summary.

    Args:
        offerings: List of per-plan course-to-period mappings.

    Returns:
        Mapping of each course to all observed periods across plans.
    """
    summary: dict[str, set[str]] = defaultdict(set)
    for offering_plan in offerings:
        for course, period in offering_plan.items():
            summary[course].update(period)
    return summary


def format_offerings_summary(summary: dict[str, set[str]]) -> str:
    """Format offerings summary as aligned plain text.

    Args:
        summary: Mapping of course code to offered periods.

    Returns:
        Multi-line human-readable summary string.
    """
    lines: list[str] = []
    for course in sorted(summary.keys()):
        periods = summary[course]
        pdtxt = sorted([p for p in periods if not p.startswith("Term ")])
        if not pdtxt:
            continue
        lines.append(f"{course:14} {' '.join(pdtxt)}")
    return "\n".join(lines)


def write_offerings_file(
    summary: dict[str, set[str]], excel_filename: Path, output_dir: Path
) -> Path:
    """Write offerings summary to a JSON file.

    Args:
        summary: Mapping of course code to offered periods.
        excel_filename: Source workbook path used to derive output filename.
        output_dir: Destination directory for output file.

    Returns:
        Path to the generated JSON file.
    """
    # Extract base name without extension
    base_name = excel_filename.stem
    filepath = output_dir / f"{base_name}_offerings.json"

    # Convert sets to sorted lists for JSON serialization
    offerings_dict = {
        course: sorted(summary[course]) for course in sorted(summary.keys())
    }

    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(offerings_dict, fh, indent=2)

    return filepath


def write_offerings_csv(
    summary: dict[str, set[str]], excel_filename: Path, output_dir: Path
) -> Path:
    """Write offerings summary as a course-by-period CSV matrix.

    Args:
        summary: Mapping of course code to offered periods.
        excel_filename: Source workbook path used to derive output filename.
        output_dir: Destination directory for output file.

    Returns:
        Path to the generated CSV file.
    """
    base_name = excel_filename.stem
    filepath = output_dir / f"{base_name}_offerings.csv"

    all_periods = sorted({period for periods in summary.values() for period in periods})
    courses = sorted(summary.keys())
    rows: list[dict[str, str]] = []
    for course in courses:
        row = {"course": course}
        for period in all_periods:
            row[period] = "Y" if period in summary[course] else ""
        rows.append(row)

    columns = ["course", *all_periods]
    pd.DataFrame(rows, columns=columns).to_csv(filepath, index=False)
    return filepath


def _to_string(val: Any) -> str:
    """Convert a spreadsheet cell value to text.

    Args:
        val: Raw cell value.

    Returns:
        Empty string for blank cells, otherwise string form of value.
    """
    if pd.isna(val):
        return ""
    return str(val)


def _to_int(val: Any, default: int | None = None) -> int:
    """Convert a spreadsheet cell value to integer.

    Args:
        val: Raw cell value.
        default: Value to return if cell is blank. If None, raises ValueError.

    Returns:
        Parsed integer value or default if cell is blank and default is provided.

    Raises:
        ValueError: If value is blank (and no default), or not integer-compatible.
    """
    if pd.isna(val):
        if default is not None:
            return default
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
    """Serialize one plan DataFrame to JSON-ready structure.

    Args:
        sheet_name: Name of the source worksheet.
        intake: Intake identifier extracted from the sheet.
        plan: Plan rows for one intake.

    Returns:
        Plan payload matching the PlanExport schema.
    """
    courses: list[PlanCourse] = []
    for idx, row in plan.iterrows():
        if pd.isna(row["Code"]):
            continue
        try:
            courses.append(
                {
                    "enrol_year": _to_string(row["EnrolYear"]),
                    "year": _to_int(row["Year"]),
                    "period": _to_string(row["Period"]),
                    "course_n": _to_string(row["CourseN"]),
                    "code": str(row["Code"]),
                    "title": _to_string(row["Title"]),
                    "uoc": _to_int(row["UoC"], default=0),
                    "prerequisites": _to_string(row["Prerequisites"]),
                }
            )
        except (ValueError, KeyError) as e:
            raise ValueError(f"{sheet_name} intake {intake}, row {idx}: {e}") from e
    return {"sheet": sheet_name, "intake": intake, "courses": courses}


def export_plan(
    sheet_name: str, intake: str, plan: pd.DataFrame, output_dir: Path
) -> Path | None:
    """Write one intake plan JSON file.

    Args:
        sheet_name: Name of the source worksheet.
        intake: Intake identifier extracted from the sheet.
        plan: Plan rows for one intake.
        output_dir: Destination directory for exported plan files.

    Returns:
        Generated file path, or None when the plan has no course rows.
    """
    plan_dict = plan_to_dict(sheet_name, intake, plan)
    if not plan_dict["courses"]:
        return None
    safe_name = f"{sheet_name}_{intake}".replace(" ", "_")
    filepath = output_dir / f"{safe_name}.json"
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(plan_dict, fh, indent=2)
    return filepath


def _build_cli_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser for the mapping export command.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Export plan JSON files and offerings summaries from a sequence mapping "
            "Excel workbook."
        ),
        epilog=(
            "Example: mapping_checker.py 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' "
            "--output-dir plans/CEIC -v"
        ),
    )
    parser.add_argument(
        "excel_file",
        help="Path to the source Excel mapping workbook",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory where exported plan/offering files are written "
            "(default: directory containing the Excel file)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-v for INFO, -vv for DEBUG)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI export workflow.

    Returns:
        Process exit code (0 on success).
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    configure_logging(args.verbose)
    logger = logging.getLogger(__name__)

    excel_file = Path(args.excel_file)
    output_dir_path = (
        Path(args.output_dir) if args.output_dir else excel_file.resolve().parent
    )
    output_dir_path.mkdir(parents=True, exist_ok=True)

    dfs: dict[str, pd.DataFrame] = pd.read_excel(  # pyright: ignore
        excel_file,
        sheet_name=None,
    )

    offerings: list[dict[str, set[str]]] = []

    for sheet_name, df in iter_sheets(dfs):
        logger.info(f"Processing sheet: {sheet_name}")
        for intake, plan in iter_plans(df):
            logger.info(f"  Intake {intake}")
            offering = course_terms(plan)
            offerings.append(offering)
            path = export_plan(sheet_name, intake, plan, output_dir_path)
            if path is None:
                logger.debug(f"  Skipped (no courses)")
            else:
                logger.debug(f"  -> {path}")

    # Write offerings summary to file
    offerings_summary = summarise_offerings(offerings)
    offerings_path = write_offerings_file(
        offerings_summary, excel_file, output_dir_path
    )
    offerings_csv_path = write_offerings_csv(
        offerings_summary, excel_file, output_dir_path
    )
    logger.info(f"Offerings summary written to: {offerings_path}")
    logger.info(f"Offerings CSV written to: {offerings_csv_path}")

    # Log the offerings summary
    logger.debug("Offerings summary:")
    logger.debug(format_offerings_summary(offerings_summary))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

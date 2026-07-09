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
from typing import Any, TypedDict, cast
import warnings

import pandas as pd
from transitionchecker.core.catalogue import (
    CatalogueKey,
    normalize_catalogue_career,
)
from transitionchecker.core.course_utils import normalize_course_code
from transitionchecker.core.offerings_output import (
    format_offerings_summary,
    write_offerings_csv,
)
from transitionchecker.core.mapping_workbook import (
    PlanNotes,
    PlanMetadata,
    ProgramSheetHeader,
    correct_single_row_enrol_year_outliers,
    iter_plans,
    iter_program_sheets,
    extract_program_sheet_header,
    plan_has_exportable_content,
)
from transitionchecker.utils.logging import configure_logging

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

logger = logging.getLogger(__name__)

# Filename auto-discovered as a sibling of the output directory.
_ERG_OVERRIDES_FILENAME = "course_catalogue_ergs.json"
_CATALOGUE_OVERRIDES_FILENAME = "catalogue_overrides.json"


def _load_prereq_override_map(
    path: Path,
) -> dict[CatalogueKey, str]:
    """Load a ``catalogue_overrides.json``-format file into a key→prerequisites map.

    Returns an empty dict if the file does not exist or contains no usable
    entries.  Only entries that include a ``prerequisites`` field are included.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw: object = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read override file %s: %s", path, exc
        )
        return {}
    if not isinstance(raw, list):
        return {}
    result: dict[CatalogueKey, str] = {}
    for item_obj in cast(list[object], raw):
        if not isinstance(item_obj, dict):
            continue
        item = cast(dict[str, Any], item_obj)
        code_raw = item.get("code")
        career_raw = item.get("career")
        prereq = item.get("prerequisites")
        if not isinstance(code_raw, str) or not isinstance(career_raw, str):
            continue
        if not isinstance(prereq, str) or not prereq.strip():
            continue
        career = normalize_catalogue_career(career_raw.strip())
        if not career:
            career = career_raw.strip()
        key = CatalogueKey(code_raw.strip(), career)
        result[key] = prereq.strip()
    return result


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
    program: str
    career: str
    uoc: int
    notes: PlanNotes
    courses: list[PlanCourse]


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
        code = normalize_course_code(str(row.Code))
        if not code:
            continue
        offering[code].add(row.Period)
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


def plan_to_dict(
    sheet_name: str,
    intake: str,
    header: ProgramSheetHeader,
    plan: pd.DataFrame,
    metadata: PlanMetadata | None = None,
    *,
    manual_overrides: dict[CatalogueKey, str] | None = None,
    erg_overrides: dict[CatalogueKey, str] | None = None,
) -> PlanExport:
    """Serialize one plan DataFrame to JSON-ready structure.

    Args:
        sheet_name: Name of the source worksheet.
        intake: Intake identifier extracted from the sheet.
        header: Header information extracted from the sheet.
        plan: Plan rows for one intake.

    Returns:
        Plan payload matching the PlanExport schema.
    """
    plan_career = normalize_catalogue_career(str(header.get("career", "")).strip())
    _manual = manual_overrides or {}
    _erg = erg_overrides or {}

    courses: list[PlanCourse] = []
    for idx, row in plan.iterrows():
        if pd.isna(row["Code"]):
            continue
        code = normalize_course_code(_to_string(row["Code"]))
        if not code:
            continue
        try:
            lookup_key = CatalogueKey(code, plan_career)
            if lookup_key in _manual:
                prerequisites = _manual[lookup_key]
                logger.debug(
                    "  %s: using manual override prerequisites", code
                )
            elif lookup_key in _erg:
                prerequisites = _erg[lookup_key]
                logger.debug(
                    "  %s: using ERG prerequisites", code
                )
            else:
                prerequisites = _to_string(row["Prerequisites"])
            courses.append(
                {
                    "enrol_year": _to_string(row["EnrolYear"]),
                    "year": _to_int(row["Year"]),
                    "period": _to_string(row["Period"]),
                    "course_n": _to_string(row["CourseN"]),
                    "code": code,
                    "title": _to_string(row["Title"]),
                    "uoc": _to_int(row["UoC"], default=0),
                    "prerequisites": prerequisites,
                }
            )
        except (ValueError, KeyError) as e:
            raise ValueError(f"{sheet_name} intake {intake}, row {idx}: {e}") from e
    return {
        "sheet": sheet_name,
        "program": header.get("program", ""),
        "career": header.get("career", ""),
        "uoc": int(header.get("uoc", 0)),
        "intake": intake,
        "notes": (metadata or {"notes": {"graduate_outcome": "", "adjustment_type": "", "for_reviewers": [], "for_students": []}})["notes"],
        "courses": courses,
    }


def export_plan(
    sheet_name: str,
    intake: str,
    header: ProgramSheetHeader,
    plan: pd.DataFrame,
    output_dir: Path,
    metadata: PlanMetadata | None = None,
    *,
    manual_overrides: dict[CatalogueKey, str] | None = None,
    erg_overrides: dict[CatalogueKey, str] | None = None,
) -> Path | None:
    """Write one intake plan JSON file.

    Args:
        sheet_name: Name of the source worksheet.
        intake: Intake identifier extracted from the sheet.
        header: Header information extracted from the sheet.
        plan: Plan rows for one intake.
        output_dir: Destination directory for exported plan files.

    Returns:
        Generated file path, or None when the plan has no course rows.
    """
    plan_dict = plan_to_dict(
        sheet_name, intake, header, plan, metadata,
        manual_overrides=manual_overrides,
        erg_overrides=erg_overrides,
    )
    if not plan_dict["courses"]:
        return None
    safe_name = f"{sheet_name}_{intake}".replace(" ", "_")
    filepath = output_dir / f"{safe_name}.json"
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(plan_dict, fh, indent=2)
    return filepath


def _build_cli_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser for the plan extraction command.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Export plan JSON files and offerings summaries from a sequence mapping "
            "Excel workbook."
        ),
        epilog=(
            "Example: \n"
            "  extract-plans 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' "
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
    parser.add_argument(
        "--erg-prereqs",
        metavar="PATH",
        default=None,
        help=(
            "Path to course_catalogue_ergs.json produced by import-erg. "
            "When not supplied, the tool auto-discovers "
            f"{_ERG_OVERRIDES_FILENAME!r} in the output directory. "
            "Pass 'none' to disable auto-discovery."
        ),
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

    excel_file = Path(args.excel_file)
    output_dir_path = (
        Path(args.output_dir) if args.output_dir else excel_file.resolve().parent
    )
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # ── Resolve prerequisite override maps ───────────────────────────────────
    # Priority (highest to lowest) when looking up a course prereq:
    #   1. manual catalogue_overrides.json  (manually curated, always wins)
    #   2. course_catalogue_ergs.json       (ERG-derived, replaces stale text)
    #   3. cell text from the workbook      (unchanged fallback)

    manual_override_path = output_dir_path / _CATALOGUE_OVERRIDES_FILENAME
    manual_overrides = _load_prereq_override_map(manual_override_path)
    if manual_overrides:
        logger.info(
            "Loaded %d manual override entries from %s",
            len(manual_overrides),
            manual_override_path,
        )

    erg_overrides: dict[CatalogueKey, str] = {}
    erg_arg = args.erg_prereqs
    if erg_arg and erg_arg.lower() != "none":
        erg_path = Path(erg_arg)
    elif not erg_arg:
        erg_path = output_dir_path / _ERG_OVERRIDES_FILENAME
    else:
        erg_path = None
    if erg_path is not None:
        erg_overrides = _load_prereq_override_map(erg_path)
        if erg_overrides:
            logger.info(
                "Loaded %d ERG override entries from %s",
                len(erg_overrides),
                erg_path,
            )
        elif erg_path.exists():
            logger.warning("ERG prereqs file %s exists but yielded no entries", erg_path)

    dfs: dict[str, pd.DataFrame] = pd.read_excel(  # pyright: ignore
        excel_file,
        sheet_name=None,
    )

    offerings: list[dict[str, set[str]]] = []

    for sheet_name, df in iter_program_sheets(dfs):
        logger.info(f"Processing sheet: {sheet_name}")
        header = extract_program_sheet_header(df)
        for intake, plan, metadata in iter_plans(df):
            logger.info(f"  Intake {intake}")
            plan, corrections = correct_single_row_enrol_year_outliers(plan)
            for correction in corrections:
                logger.warning(
                    "  Corrected enrol_year outlier: row=%s code=%s year=%s period=%s %s -> %s",
                    correction["row_index"],
                    correction["code"] or "<blank>",
                    correction["year"],
                    correction["period"],
                    correction["old_enrol_year"],
                    correction["new_enrol_year"],
                )
            if not plan_has_exportable_content(plan):
                logger.debug("  Skipped (no exportable courses)")
                continue
            offering = course_terms(plan)
            offerings.append(offering)
            path = export_plan(
                sheet_name,
                intake,
                header,
                plan,
                output_dir_path,
                metadata,
                manual_overrides=manual_overrides,
                erg_overrides=erg_overrides,
            )
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
        offerings_summary, output_dir_path / f"{excel_file.stem}_offerings.csv"
    )
    logger.info(f"Offerings summary written to: {offerings_path}")
    logger.info(f"Offerings CSV written to: {offerings_csv_path}")

    # Log the offerings summary
    logger.debug("Offerings summary:")
    logger.debug(format_offerings_summary(offerings_summary))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Import structured prerequisite data from the STU055 ERG report.

Reads ``STU055 Attached ERG Details.xlsx``, parses the machine-readable
``ERG Requisite Detail`` column, and writes ``course_catalogue_ergs.json``
in the same format as ``catalogue_overrides.json``.

The output can then be placed in a ``plans/`` directory so that
``extract-plans`` can graft accurate prerequisites into plan JSON files,
overriding stale handbook text embedded in colleagues' spreadsheets, while
still yielding to manually-curated ``catalogue_overrides.json`` entries.

Career handling
---------------
Only rows with an explicit ``Attached Course Career`` value (e.g. ``UGRD``,
``PGRD``) are processed; rows with a blank career are skipped.  The career is
normalised via ``normalize_catalogue_career()`` (e.g. ``UGRD →
"Undergraduate"``).

When a ``(ERG ID, career)`` group contains a row with a non-empty
``Requirement ID``, the entire group falls back to the human-readable ERG
Description text (which the standard prerequisite parser handles as a
fallback).

Excel export
------------
``--export-excel PATH`` writes a flat table (one row per course/career, no
merged cells) with columns ``Code | Career | Prerequisites`` that can be
distributed as a cleaned catalogue reference.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import pandas as pd

from transitionchecker.core.catalogue import Catalogue, CatalogueKey, normalize_catalogue_career
from transitionchecker.erg_parser import (
    ErgParseResult,
    ErgRow,
    build_prerequisites_field,
    build_erg_expr,
    parse_erg_group,
)
import warnings

from transitionchecker.utils.logging import configure_logging

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# ---------------------------------------------------------------------------
# Column name constants (normalised after strip)
# ---------------------------------------------------------------------------

_COL_COURSES = "Attached Courses"
_COL_ERG_ID = "ERG ID"
_COL_DESCRIPTION = "ERG Description"
_COL_CAREER = "Attached Course Career"
_COL_GROUP_NUM = "Group Number"
_COL_LINE_NUM = "Line Number"
_COL_DETAIL = "ERG Requisite Detail"
_COL_REQ_ID = "Requirement ID"

_FFILL_COLS = (_COL_COURSES, _COL_ERG_ID, _COL_DESCRIPTION)

_REQUIRED_COLS = {
    _COL_COURSES,
    _COL_ERG_ID,
    _COL_DESCRIPTION,
    _COL_CAREER,
    _COL_GROUP_NUM,
    _COL_DETAIL,
}


# ---------------------------------------------------------------------------
# Catalogue loading
# ---------------------------------------------------------------------------

def _load_catalogue(path: Path) -> Catalogue | None:
    """Load catalogue.json; return None with a printed warning if unavailable."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw: object = json.load(fh)
        if not isinstance(raw, list):
            raise ValueError("catalogue JSON root must be a list")
        cat = Catalogue.from_list(cast(list[object], raw))
        logger.info("Loaded catalogue from %s (%d entries)", path, len(list(cat.values())))
        return cat
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: Could not load catalogue from {path}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Reading / normalisation helpers
# ---------------------------------------------------------------------------

def _load_df(xlsx_path: Path) -> pd.DataFrame:
    """Load the STU055 sheet, normalise column names, and forward-fill merged cells."""
    df: pd.DataFrame = pd.read_excel(  # pyright: ignore[reportUnknownMemberType]
        xlsx_path,
        sheet_name=0,
        header=0,
        dtype=str,         # keep everything as string; avoids float/int coercion
    )
    df.columns = pd.Index([str(c).strip() for c in df.columns])

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"STU055 spreadsheet is missing required columns: {sorted(missing)}\n"
            f"Found columns: {list(df.columns)}"
        )

    # Forward-fill only the columns that are merged across multiple rows.
    for col in _FFILL_COLS:
        df[col] = df[col].ffill()

    return df


def _cell(df: pd.DataFrame, idx: Any, col: str) -> str:
    """Return a stripped string value from a DataFrame cell, or ``""``."""
    val = df.at[idx, col]
    if pd.isna(val):
        return ""
    return str(val).strip()


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process_df(
    df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert the normalised DataFrame to override records and a fallback report.

    Returns
    -------
    records
        One dict per ``(course_code, career)`` pair for ``course_catalogue_ergs.json``.
    fallback_entries
        One dict per ``(ERG ID, career)`` group that could not be fully parsed,
        for the ``--fallback-report`` output.
    """
    # Group rows by (ERG ID, normalised career)
    # We only keep rows with an explicit (non-blank) career.
    groups: dict[tuple[str, str], list[tuple[str, ErgRow]]] = defaultdict(list)
    descriptions: dict[tuple[str, str], str] = {}
    attached_courses: dict[tuple[str, str], list[str]] = {}

    for idx, _ in df.iterrows():
        career_raw = _cell(df, idx, _COL_CAREER)
        if not career_raw:
            # Skip blank-career rows — only explicit careers are used.
            continue

        career = normalize_catalogue_career(career_raw)
        if not career:
            logger.debug("Unrecognised career %r at row %s; skipping", career_raw, idx)
            continue

        erg_id = _cell(df, idx, _COL_ERG_ID)
        if not erg_id:
            continue

        key = (erg_id, career)
        descriptions.setdefault(key, _cell(df, idx, _COL_DESCRIPTION))

        courses_raw = _cell(df, idx, _COL_COURSES)
        if courses_raw:
            attached_courses.setdefault(key, courses_raw.split())

        detail = _cell(df, idx, _COL_DETAIL)
        group_num = _cell(df, idx, _COL_GROUP_NUM)
        req_id = _cell(df, idx, _COL_REQ_ID) if _COL_REQ_ID in df.columns else ""

        groups[key].append(
            (group_num, ErgRow(group_num, detail, req_id))
        )

    # Parse each group and emit per-course entries
    records: list[dict[str, Any]] = []
    fallback_entries: list[dict[str, Any]] = []
    for key, group_rows in groups.items():
        erg_id, career = key
        description = descriptions.get(key, "")
        courses = attached_courses.get(key, [])

        # Sort by group_number (string sort is fine for zero-padded numbers like "0010")
        sorted_rows = [erg_row for _, erg_row in sorted(group_rows, key=lambda t: t[0])]

        result: ErgParseResult = parse_erg_group(sorted_rows)

        if result.has_unresolvable:
            # Fall back to the human-readable ERG Description.
            prerequisites = description
            source = "fallback"
            erg_expr_value = None
            fallback_entries.append({
                "erg_id": erg_id,
                "career": career,
                "courses": courses,
                "description": description,
                "erg_detail_lines": [r.detail_text.strip() for r in sorted_rows],
                "unresolvable_lines": result.unresolvable_lines,
            })
            logger.debug(
                "ERG %s career=%s: unresolvable row(s); using description as fallback",
                erg_id,
                career,
            )
        else:
            prerequisites = build_prerequisites_field(result)
            source = "parsed"
            erg_expr_value = build_erg_expr(sorted_rows)

        if not prerequisites:
            logger.warning(
                "ERG %s career=%s: empty prerequisites after parsing; skipping",
                erg_id,
                career,
            )
            continue

        for course_code in courses:
            code = course_code.strip().upper()
            if not code:
                continue
            record: dict[str, Any] = {
                    "code": code,
                    "career": career,
                    "prerequisites": prerequisites,
                    "erg_id": erg_id,
                    "erg_description": description,
                    "erg_detail_lines": [r.detail_text.strip() for r in sorted_rows],
                    "erg_source": source,
                }
            if erg_expr_value is not None:
                record["erg_expr"] = erg_expr_value
            records.append(record)
            logger.info(
                "  %s (%s) ERG %s [%s]: %s",
                code,
                career,
                erg_id,
                source,
                prerequisites[:80],
            )

    # Stable output order: sort by (code, career) for reproducible diffs.
    records.sort(key=lambda r: (r["code"], r["career"]))
    return records, fallback_entries


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def _write_export_excel(
    records: list[dict[str, Any]],
    out_path: Path,
    catalogue: Catalogue | None,
) -> None:
    """Write a flat catalogue-format Excel table.

    Columns: Code | Title | Career | UoC | Prerequisites | Min Marks

    When *catalogue* is provided, only rows whose ``(code, career)`` exists in
    the catalogue are included, and Title / UoC are taken from there.
    """
    rows: list[dict[str, Any]] = []
    for r in records:
        code = r["code"]
        career = r["career"]
        title = ""
        uoc: int | str = ""
        if catalogue is not None:
            entry = catalogue.get(CatalogueKey(code, career))
            if entry is None:
                # Skip courses not in the catalogue.
                continue
            title = entry.title
            uoc = entry.uoc
        rows.append({
            "Code": code,
            "Title": title,
            "Career": career,
            "UoC": uoc,
            "Prerequisites": r["prerequisites"],
        })
    export_df = pd.DataFrame(rows)
    export_df.to_excel(out_path, index=False)  # pyright: ignore[reportUnknownMemberType]
    logger.info("Excel export written to: %s (%d rows)", out_path, len(rows))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import-erg",
        description=(
            "Import structured prerequisite data from the STU055 ERG report "
            "(Attached ERG Details table) that has been exported as an Excel spreadsheet "
            "(STU055 Attached ERG Details.xlsx) and write course_catalogue_ergs.json."
        ),
        epilog=(
            "Example:\n"
            "  import-erg 'STU055 Attached ERG Details.xlsx' \\\n"
            "        --output plans/course_catalogue_ergs.json\n"
            "  import-erg 'STU055 Attached ERG Details.xlsx' \\\n"
            "        --export-excel plans/erg_prereqs.xlsx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "xlsx",
        help="Path to 'STU055 Attached ERG Details.xlsx'",
    )
    parser.add_argument(
        "--output",
        default="course_catalogue_ergs.json",
        help=(
            "Output path for course_catalogue_ergs.json "
            "(default: course_catalogue_ergs.json in the current directory)"
        ),
    )
    parser.add_argument(
        "--export-excel",
        metavar="PATH",
        default=None,
        help="Also write a flat Excel export of the resolved prerequisites to PATH",
    )
    parser.add_argument(
        "--catalogue",
        metavar="PATH",
        default="plans/catalogue.json",
        help=(
            "Path to catalogue.json used to add Title/UoC to the Excel export "
            "and filter it to known courses "
            "(default: plans/catalogue.json)"
        ),
    )
    parser.add_argument(
        "--fallback-report",
        metavar="PATH",
        default=None,
        help=(
            "Write a JSON report of all ERG groups that fell back to the "
            "description text (unresolvable PRE RQ lines etc.) to PATH"
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
    """Entry point for the ``import-erg`` CLI command."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    configure_logging(args.verbose)

    xlsx_path = Path(args.xlsx)
    output_path = Path(args.output)
    export_excel_path = Path(args.export_excel) if args.export_excel else None
    catalogue_path = Path(args.catalogue)
    fallback_report_path = Path(args.fallback_report) if args.fallback_report else None

    if not xlsx_path.exists():
        print(f"ERROR: File not found: {xlsx_path}")
        return 1

    logger.info("Reading: %s", xlsx_path)
    try:
        df = _load_df(xlsx_path)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to read spreadsheet: {exc}")
        return 1

    logger.info("Processing %d rows...", len(df))
    records, fallback_entries = _process_df(df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)

    print(f"Written {len(records)} entries to: {output_path}")
    fallback_count = len(fallback_entries)
    parsed_count = len(records) - sum(1 for r in records if r.get("erg_source") == "fallback")
    print(f"  Parsed: {parsed_count}  Fallback: {fallback_count} ERG group(s)")

    if fallback_report_path is not None:
        fallback_report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_report_path, "w", encoding="utf-8") as fh:
            json.dump(fallback_entries, fh, indent=2)
        print(f"Fallback report written to: {fallback_report_path} ({fallback_count} entries)")

    if export_excel_path is not None:
        catalogue = _load_catalogue(catalogue_path)
        if catalogue is None:
            print(
                f"WARNING: catalogue.json not found at {catalogue_path}. "
                "Excel export will include all courses without title/UoC filtering. "
                "Pass --catalogue PATH to specify its location."
            )
        export_excel_path.parent.mkdir(parents=True, exist_ok=True)
        _write_export_excel(records, export_excel_path, catalogue)
        print(f"Excel export written to: {export_excel_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

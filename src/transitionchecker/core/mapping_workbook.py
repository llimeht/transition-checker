from __future__ import annotations

import logging
import re
from collections.abc import Collection, Generator
from typing import Any

import pandas as pd


logger = logging.getLogger(__name__)


CATALOGUE_SHEET_NAMES = (
    "Course Catalogue",
    "Catalogue",
    "Merged Course List",
    "Handbook Course Catalogue",
    "Local Course Overrides",
    "Courses Master",
)

IGNORE_SHEET_NAMES = CATALOGUE_SHEET_NAMES + ("Instructions and glossary",)

TEMPLATE_SHEET_RE = re.compile(r"(\{.*\}|template|ABCDEF)", re.IGNORECASE)

CANONICAL_PLAN_COLUMNS = [
    "EnrolYear",
    "Year",
    "Period",
    "CourseN",
    "Code",
    "Title",
    "UoC",
    "Prerequisites",
]

END_INTAKE_MARKER = "Available Periods:"

# Number of header rows above the data table in the catalogue sheet (1-based).
CATALOGUE_SHEET_HEADER_ROWS = 3

# Column index mapping for the catalogue sheet.
CATALOGUE_SHEET_COLUMNS = {
    "Code": 0,
    "Title": 1,
    "Career": 2,
    "UoC": 3,
    "Prerequisites": 4,
    "ToDo": 5,
}


def find_catalogue_sheet(workbook: Any) -> Any:
    """Return the first catalogue sheet found in an openpyxl workbook.

    Tries each name in ``CATALOGUE_SHEET_NAMES`` in order and returns the
    first match.  Raises ``ValueError`` if none are present.
    """
    for name in CATALOGUE_SHEET_NAMES:
        if name in workbook:
            return workbook[name]
    raise ValueError(
        f"No catalogue sheet found. Expected one of: {', '.join(CATALOGUE_SHEET_NAMES)}"
    )


def extract_catalogue(workbook: Any) -> dict[str, dict[str, Any]]:
    """Extract course catalogue from the catalogue sheet of an openpyxl workbook."""
    print("\n=== EXTRACTING CATALOGUE ===")
    try:
        cat_sheet = find_catalogue_sheet(workbook)
    except KeyError:
        raise ValueError("Catalogue sheet not found in workbook")

    catalogue: dict[str, dict[str, Any]] = {}
    for row in cat_sheet.iter_rows(
        min_row=CATALOGUE_SHEET_HEADER_ROWS, values_only=False
    ):
        if not row or not row[CATALOGUE_SHEET_COLUMNS["Code"]].value:
            continue

        course_code = str(row[CATALOGUE_SHEET_COLUMNS["Code"]].value).strip()
        if not course_code:
            continue

        title = (
            str(row[CATALOGUE_SHEET_COLUMNS["Title"]].value).strip()
            if len(row) > 1 and row[CATALOGUE_SHEET_COLUMNS["Title"]].value
            else ""
        )
        career = (
            str(row[CATALOGUE_SHEET_COLUMNS["Career"]].value).strip()
            if len(row) > 1 and row[CATALOGUE_SHEET_COLUMNS["Career"]].value
            else ""
        )

        uoc: int | None = None
        if len(row) > 2 and row[CATALOGUE_SHEET_COLUMNS["UoC"]].value is not None:
            try:
                uoc = int(row[CATALOGUE_SHEET_COLUMNS["UoC"]].value)
            except (TypeError, ValueError):
                uoc = None

        prereq = (
            str(row[CATALOGUE_SHEET_COLUMNS["Prerequisites"]].value).strip()
            if len(row) > 3 and row[CATALOGUE_SHEET_COLUMNS["Prerequisites"]].value
            else "."
        )
        todo = (
            str(row[CATALOGUE_SHEET_COLUMNS["ToDo"]].value).strip()
            if len(row) > 4 and row[CATALOGUE_SHEET_COLUMNS["ToDo"]].value
            else ""
        )

        catalogue[course_code] = {
            "title": title,
            "career": career,
            "uoc": uoc,
            "prerequisites": prereq,
            "todo": todo,
        }

    print(f"Extracted {len(catalogue)} catalogue entries")
    return catalogue


def find_template_sheet(dfs: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame]:
    """Return the single workbook sheet used as the template plan source.

    Raises ``ValueError`` if no template sheet is found or if more than one
    sheet matches the template naming convention.
    """

    matches = [(name, df) for name, df in dfs.items() if TEMPLATE_SHEET_RE.match(name)]
    if not matches:
        raise ValueError(
            "No template sheet (e.g. ABCDEF or {ABCD1234} or Template) found in workbook"
        )
    if len(matches) > 1:
        sheet_names = ", ".join(name for name, _ in matches)
        logger.error("Multiple template sheets found: %s", sheet_names)
        raise ValueError(f"Multiple template sheets found: {sheet_names}")
    return matches[0]


def normalize_plan_sheet_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of a plan sheet with stable column labels when possible."""

    columns = list(df.columns)
    if len(columns) < len(CANONICAL_PLAN_COLUMNS):
        return df

    normalized_columns = columns.copy()
    normalized_columns[: len(CANONICAL_PLAN_COLUMNS)] = CANONICAL_PLAN_COLUMNS
    normalized_df = df.copy()
    normalized_df.columns = pd.Index(normalized_columns)
    return normalized_df


def iter_program_sheets(
    dfs: dict[str, pd.DataFrame],
    ignored_sheet_names: Collection[str] = IGNORE_SHEET_NAMES,
    template_sheet_re: re.Pattern[str] = TEMPLATE_SHEET_RE,
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield workbook sheets that contain plan data.

    Internal sheets such as catalogue and instruction tabs are skipped, along
    with template sheets used only to define workbook structure. The first
    eight columns are normalised to canonical names when the sheet is wide
    enough so downstream consumers can rely on stable labels.
    """

    for sheet_name, df in dfs.items():
        if sheet_name in ignored_sheet_names:
            continue
        if template_sheet_re.match(sheet_name):
            continue
        yield sheet_name, normalize_plan_sheet_columns(df)


def iter_plans(
    sheet: pd.DataFrame,
    start_row: int = 4,
    end_intake_marker: str = END_INTAKE_MARKER,
) -> Generator[tuple[str, pd.DataFrame], None, None]:
    """Yield each intake plan block from a normalised sheet.

    Args:
        sheet: A sheet already normalised by ``iter_program_sheets``.
        start_row: Number of header rows to skip before plan blocks begin.
        end_intake_marker: Marker in column A that ends plan blocks.

    Yields:
        Tuples of ``(intake, plan_dataframe)``.
    """

    trimmed = sheet.iloc[start_row:].reset_index(drop=True)

    # Column A (first column) is used as the block separator signal.
    col_a = trimmed.iloc[:, 0]
    col_a_text = col_a.astype(str).str.strip()

    # True where the row is part of a plan block rather than a separator or trailer.
    mask = col_a.notna() & (col_a_text != "") & (col_a_text != end_intake_marker)

    # Consecutive True runs correspond to intake header + plan rows.
    groups = (mask != mask.shift()).cumsum()

    for _, sub in trimmed[mask].groupby(groups[mask]):
        intake = str(sub.iloc[0, 3]).strip()  # intake comment is in column D (index 3)
        rows = sub.iloc[1:].reset_index(drop=True)
        yield intake, rows

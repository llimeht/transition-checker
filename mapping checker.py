#!/usr/bin/python3


from collections import defaultdict
from typing import Generator
import warnings

import pandas as pd

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


mapping_file = "CEIC Program Sequence Mapping.xlsx"


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
                                          


dfs: dict[str, pd.DataFrame] = pd.read_excel(mapping_file, sheet_name=None)  # type: ignore

offerings: list[dict[str, set[str]]] = []

for sheet_name, df in iter_sheets(dfs):
    print(sheet_name)
    for intake, plan in iter_plans(df):
        print(f"Intake {intake}")
        offering = course_terms(plan)
        offerings.append(offering)

print()
print_offerings_summary(summarise_offerings(offerings))





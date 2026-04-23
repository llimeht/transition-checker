from __future__ import annotations

import pandas as pd
import pytest

from transitionchecker.core.mapping_workbook import (
    END_INTAKE_MARKER,
    find_template_sheet,
    iter_plans,
    iter_program_sheets,
)


def test_iter_program_sheets_skips_internal_and_normalizes_columns() -> None:
    wide_df = pd.DataFrame(columns=[f"col{i}" for i in range(10)])
    short_df = pd.DataFrame(columns=["a", "b", "c"])
    dfs = {
        "Course Catalogue": pd.DataFrame(columns=["ignore"]),
        "Instructions and glossary": pd.DataFrame(columns=["ignore"]),
        "{CEIC0000}": pd.DataFrame(columns=["ignore"]),
        "Program A": wide_df,
        "Program B": short_df,
    }

    sheets = list(iter_program_sheets(dfs))

    assert [sheet_name for sheet_name, _ in sheets] == ["Program A", "Program B"]
    assert list(sheets[0][1].columns[:8]) == [
        "EnrolYear",
        "Year",
        "Period",
        "CourseN",
        "Code",
        "Title",
        "UoC",
        "Prerequisites",
    ]
    assert list(wide_df.columns[:8]) == [f"col{i}" for i in range(8)]
    assert list(sheets[1][1].columns) == ["a", "b", "c"]


def test_iter_plans_groups_blocks_and_skips_end_marker() -> None:
    sheet = pd.DataFrame(
        [
            [None] * 8,
            [None] * 8,
            [None] * 8,
            [None] * 8,
            ["Year 1", None, None, " 2026 T1 ", None, None, None, None],
            ["Y1", 2026, "Term 1", "Course 1", "CEIC1000", "Intro", 6, ""],
            [None, None, None, None, None, None, None, None],
            ["Year 1", None, None, "2026 T2", None, None, None, None],
            ["Y1", 2026, "Term 2", "Course 1", "CEIC2000", "Next", 6, ""],
            [END_INTAKE_MARKER, None, None, None, None, None, None, None],
        ],
        columns=[
            "EnrolYear",
            "Year",
            "Period",
            "CourseN",
            "Code",
            "Title",
            "UoC",
            "Prerequisites",
        ],
    )

    plans = list(iter_plans(sheet))

    assert [intake for intake, _ in plans] == ["2026 T1", "2026 T2"]
    assert plans[0][1].to_dict("records") == [
        {
            "EnrolYear": "Y1",
            "Year": 2026,
            "Period": "Term 1",
            "CourseN": "Course 1",
            "Code": "CEIC1000",
            "Title": "Intro",
            "UoC": 6,
            "Prerequisites": "",
        }
    ]
    assert plans[1][1].to_dict("records") == [
        {
            "EnrolYear": "Y1",
            "Year": 2026,
            "Period": "Term 2",
            "CourseN": "Course 1",
            "Code": "CEIC2000",
            "Title": "Next",
            "UoC": 6,
            "Prerequisites": "",
        }
    ]


def test_find_template_sheet_returns_single_match() -> None:
    template_df = pd.DataFrame(columns=["col0"])

    sheet_name, df = find_template_sheet(
        {
            "Program A": pd.DataFrame(columns=["col0"]),
            "{CEIC0000}": template_df,
        }
    )

    assert sheet_name == "{CEIC0000}"
    assert df is template_df


def test_find_template_sheet_raises_when_missing() -> None:
    with pytest.raises(ValueError, match="No template sheet"):
        find_template_sheet({"Program A": pd.DataFrame(columns=["col0"])})


def test_find_template_sheet_raises_when_ambiguous() -> None:
    with pytest.raises(ValueError, match="Multiple template sheets found"):
        find_template_sheet(
            {
                "{CEIC0000}": pd.DataFrame(columns=["col0"]),
                "template": pd.DataFrame(columns=["col0"]),
            }
        )

"""Behavior tests for extract_plans CLI."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from transitionchecker.cli import extract_plans_cli
from transitionchecker.core.mapping_workbook import PlanMetadata, ProgramSheetHeader
from transitionchecker.core.rules_loader import RulesMetadata


def test_requires_excel_file_argument() -> None:
    with pytest.raises(SystemExit) as exc:
        extract_plans_cli.main([])
    assert exc.value.code == 2


def test_main_runs_export_flow_and_writes_offerings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        # one intake with one row so downstream functions are called
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter(
            [
                (
                    "2026 T1",
                    plan,
                    metadata,
                )
            ]
        )

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "TEST", "career": "Undergraduate", "uoc": 24}

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        **_kwargs: object,
    ) -> Path:
        return out_dir / "p.json"

    def fake_summarise_offerings(
        _offers: list[dict[str, set[str]]],
    ) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(
        extract_plans_cli,
        "extract_program_sheet_header",
        fake_extract_program_sheet_header,
    )
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(
        extract_plans_cli, "summarise_offerings", fake_summarise_offerings
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_file", fake_write_offerings_file
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv
    )

    code = extract_plans_cli.main([str(excel), "--output-dir", str(out_dir)])
    assert code == 0
    assert out_dir.is_dir()


def test_main_skips_placeholder_only_plans_before_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame(
            [{"Code": "[TEST0001]", "Period": "Term 1", "CourseN": "Course 1"}]
        )
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter(
            [
                (
                    "2026 T1",
                    plan,
                    metadata,
                )
            ]
        )

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "TEST", "career": "Undergraduate", "uoc": 24}

    export_calls: list[tuple[str, str]] = []
    offering_calls: list[pd.DataFrame] = []

    def fake_course_terms(plan: pd.DataFrame) -> dict[str, set[str]]:
        offering_calls.append(plan)
        return {"[TEST0001]": {"Term 1"}}

    def fake_export_plan(
        sheet: str,
        intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        **_kwargs: object,
    ) -> Path:
        export_calls.append((sheet, intake))
        return out_dir / "p.json"

    def fake_summarise_offerings(
        offers: list[dict[str, set[str]]],
    ) -> dict[str, set[str]]:
        assert offers == []
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(
        extract_plans_cli,
        "extract_program_sheet_header",
        fake_extract_program_sheet_header,
    )
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(
        extract_plans_cli, "summarise_offerings", fake_summarise_offerings
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_file", fake_write_offerings_file
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv
    )

    code = extract_plans_cli.main([str(excel), "--output-dir", str(out_dir)])

    assert code == 0
    assert export_calls == []
    assert offering_calls == []


def test_plan_to_dict_skips_whitespace_code_rows() -> None:
    plan = pd.DataFrame(
        [
            {
                "EnrolYear": "Year 1",
                "Year": 2028,
                "Period": "Semester 2",
                "CourseN": "Course 1",
                "Code": "   ",
                "Title": "",
                "UoC": 0,
                "Prerequisites": "",
            },
            {
                "EnrolYear": "Year 1",
                "Year": 2028,
                "Period": "Semester 2",
                "CourseN": "Course 2",
                "Code": "MATH1231",
                "Title": "Mathematics 1B",
                "UoC": 6,
                "Prerequisites": ".",
            },
        ]
    )
    header: ProgramSheetHeader = {
        "program": "TEST1000",
        "career": "Undergraduate",
        "uoc": 48,
    }

    payload = extract_plans_cli.plan_to_dict("Sheet1", "2028 S2", header, plan)

    assert [course["code"] for course in payload["courses"]] == ["MATH1231"]


def test_plan_to_dict_includes_notes_metadata() -> None:
    plan = pd.DataFrame(
        [
            {
                "EnrolYear": "Year 1",
                "Year": 2028,
                "Period": "Term 1",
                "CourseN": "Course 1",
                "Code": "MATH1231",
                "Title": "Mathematics 1B",
                "UoC": 6,
                "Prerequisites": ".",
            }
        ]
    )
    header: ProgramSheetHeader = {
        "program": "TEST1000",
        "career": "Undergraduate",
        "uoc": 48,
    }
    metadata: PlanMetadata = {
        "notes": {
            "graduate_outcome": "Late graduation",
            "adjustment_type": "Adjustment within standard load",
            "for_reviewers": ["Nucleus Study Guide 2024"],
            "for_students": ["FOOD3801 has moved term"],
        }
    }

    payload = extract_plans_cli.plan_to_dict(
        "Sheet1", "2028 T1", header, plan, metadata
    )

    assert payload["notes"] == metadata["notes"]


def test_course_terms_normalizes_mixed_case_codes() -> None:
    plan = pd.DataFrame(
        [
            {"Code": "GenEd1", "Period": "Term 1"},
            {"Code": "GENED1", "Period": "Term 2"},
            {"Code": "  gened1  ", "Period": "Term 3"},
            {"Code": "", "Period": "Term 1"},
        ]
    )

    offering = extract_plans_cli.course_terms(plan)

    assert dict(offering) == {"GENED1": {"Term 1", "Term 2", "Term 3"}}


def test_plan_to_dict_normalizes_code_to_uppercase() -> None:
    plan = pd.DataFrame(
        [
            {
                "EnrolYear": "Year 1",
                "Year": 2028,
                "Period": "Term 1",
                "CourseN": "Course 1",
                "Code": "GenEd1",
                "Title": "General Education",
                "UoC": 6,
                "Prerequisites": "",
            }
        ]
    )
    header: ProgramSheetHeader = {
        "program": "TEST1000",
        "career": "Undergraduate",
        "uoc": 48,
    }

    payload = extract_plans_cli.plan_to_dict("Sheet1", "2028 T1", header, plan)

    assert [course["code"] for course in payload["courses"]] == ["GENED1"]


def test_main_corrects_single_row_enrol_year_outlier_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame(
            [
                {
                    "EnrolYear": "Year 4",
                    "Year": 2031,
                    "Period": "Semester 2",
                    "CourseN": "Course 1",
                    "Code": "CEIC3004",
                    "Title": "A",
                    "UoC": 6,
                    "Prerequisites": ".",
                },
                {
                    "EnrolYear": "Year 4",
                    "Year": 2031,
                    "Period": "Semester 2",
                    "CourseN": "Course 2",
                    "Code": "CEIC3006",
                    "Title": "B",
                    "UoC": 6,
                    "Prerequisites": ".",
                },
                {
                    "EnrolYear": "Year 4",
                    "Year": 2031,
                    "Period": "Semester 2",
                    "CourseN": "Course 3",
                    "Code": "CEIC3007",
                    "Title": "C",
                    "UoC": 6,
                    "Prerequisites": ".",
                },
                {
                    "EnrolYear": "Year 5",
                    "Year": 2031,
                    "Period": "Semester 2",
                    "CourseN": "Course 4",
                    "Code": "CEIC4002",
                    "Title": "D",
                    "UoC": 6,
                    "Prerequisites": ".",
                },
            ]
        )
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter(
            [
                (
                    "2028 S2",
                    plan,
                    metadata,
                )
            ]
        )

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "TEST", "career": "Undergraduate", "uoc": 24}

    exported_plans: list[pd.DataFrame] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        **_kwargs: object,
    ) -> Path:
        exported_plans.append(plan.copy())
        return out_dir / "p.json"

    monkeypatch.setattr(
        extract_plans_cli,
        "extract_program_sheet_header",
        fake_extract_program_sheet_header,
    )
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {}

    def fake_summarise_offerings(
        _offers: list[dict[str, set[str]]],
    ) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(
        extract_plans_cli, "summarise_offerings", fake_summarise_offerings
    )
    monkeypatch.setattr(
        extract_plans_cli,
        "write_offerings_file",
        fake_write_offerings_file,
    )
    monkeypatch.setattr(
        extract_plans_cli,
        "write_offerings_csv",
        fake_write_offerings_csv,
    )

    caplog.set_level("INFO")
    code = extract_plans_cli.main([str(excel), "--output-dir", str(out_dir), "-v"])

    assert code == 0
    assert len(exported_plans) == 1
    assert exported_plans[0].loc[3, "EnrolYear"] == "Year 4"
    assert "Corrected enrol_year outlier" in caplog.text
    assert "CEIC4002" in caplog.text


def test_main_warns_when_matching_rules_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"MISSINGRULES": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("MISSINGRULES", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter([("2026 T1", plan, metadata)])

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "MISSINGRULES", "career": "Undergraduate", "uoc": 96}

    captured: list[RulesMetadata | None] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        *,
        program_metadata: RulesMetadata | None = None,
        **_kwargs: object,
    ) -> Path:
        captured.append(program_metadata)
        return out_dir / "p.json"

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_summarise_offerings(_offers: list[dict[str, set[str]]]) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(
        extract_plans_cli,
        "extract_program_sheet_header",
        fake_extract_program_sheet_header,
    )
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(
        extract_plans_cli, "summarise_offerings", fake_summarise_offerings
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_file", fake_write_offerings_file
    )
    monkeypatch.setattr(
        extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv
    )

    caplog.set_level("WARNING")
    code = extract_plans_cli.main(
        [str(excel), "--output-dir", str(out_dir), "--rules-dir", str(rules_dir)]
    )

    assert code == 0
    assert captured == [None]
    assert "Rules file not found for sheet 'MISSINGRULES' intake '2026 T1': should be" in caplog.text
    assert str(rules_dir / "MISSINGRULES.json") in caplog.text


# ---------------------------------------------------------------------------
# program_metadata in plan_to_dict
# ---------------------------------------------------------------------------


def test_plan_to_dict_includes_program_metadata_when_provided() -> None:
    plan = pd.DataFrame(
        [
            {
                "EnrolYear": "Year 1",
                "Year": 2026,
                "Period": "Term 1",
                "CourseN": "Course 1",
                "Code": "MATH1131",
                "Title": "Mathematics 1A",
                "UoC": 6,
                "Prerequisites": "",
            }
        ]
    )
    header: ProgramSheetHeader = {
        "program": "CEICAH3707",
        "career": "Undergraduate",
        "uoc": 192,
    }
    meta: RulesMetadata = {
        "plan_code": "CEICAH3707",
        "plan_description": "",
        "program": {"id": "3707", "name": "Bachelor of Engineering (Honours)"},
        "specialisation": [{"id": "CEICAH", "name": "Chemical Engineering"}],
        "uoc": 192,
        "rules_description": "",
    }

    payload = extract_plans_cli.plan_to_dict(
        "CEICAH3707", "2026 T1", header, plan, program_metadata=meta
    )

    assert payload["program_metadata"] == meta


def test_plan_to_dict_program_metadata_defaults_to_none() -> None:
    plan = pd.DataFrame(
        [
            {
                "EnrolYear": "Year 1",
                "Year": 2026,
                "Period": "Term 1",
                "CourseN": "Course 1",
                "Code": "MATH1131",
                "Title": "Mathematics 1A",
                "UoC": 6,
                "Prerequisites": "",
            }
        ]
    )
    header: ProgramSheetHeader = {"program": "TEST", "career": "Undergraduate", "uoc": 48}

    payload = extract_plans_cli.plan_to_dict("TEST", "2026 T1", header, plan)

    assert payload["program_metadata"] is None


def test_main_embeds_program_metadata_from_rules_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """program_metadata is populated when a matching rules file is found."""
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    # Create a minimal rules file for the sheet name used in the fake sheets.
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "TESTRULES.json").write_text(
        json.dumps(
            {
                "program": {"id": "9999", "name": "Test Program"},
                "specialisations": [{"id": "TESTSP", "name": "Test Spec"}],
                "uoc": 96,
            }
        ),
        encoding="utf-8",
    )

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"TESTRULES": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("TESTRULES", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter([("2026 T1", plan, metadata)])

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "TESTRULES", "career": "Undergraduate", "uoc": 96}

    captured: list[RulesMetadata | None] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        *,
        program_metadata: RulesMetadata | None = None,
        **_kwargs: object,
    ) -> Path:
        captured.append(program_metadata)
        return out_dir / "p.json"

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_summarise_offerings(_offers: list[dict[str, set[str]]]) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(extract_plans_cli, "extract_program_sheet_header", fake_extract_program_sheet_header)
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(extract_plans_cli, "summarise_offerings", fake_summarise_offerings)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_file", fake_write_offerings_file)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv)

    code = extract_plans_cli.main(
        [str(excel), "--output-dir", str(out_dir), "--rules-dir", str(rules_dir)]
    )

    assert code == 0
    assert len(captured) == 1
    meta = captured[0]
    assert meta is not None
    assert meta["plan_code"] == "TESTRULES"
    assert meta["plan_description"] == ""
    assert meta["program"] == {"id": "9999", "name": "Test Program"}
    assert meta["specialisation"] == [{"id": "TESTSP", "name": "Test Spec"}]
    assert meta["uoc"] == 96


def test_main_uses_header_plan_fields_for_program_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Header-derived plan_code/plan_description are used when present."""
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "TESTRULES.json").write_text(
        json.dumps(
            {
                "program": {"id": "9999", "name": "Test Program"},
                "specialisations": [],
                "uoc": 96,
            }
        ),
        encoding="utf-8",
    )

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"SHEETNAME": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("SHEETNAME", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter([("2026 T1", plan, metadata)])

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {
            "program": "TESTRULES (48 UoC RPL)",
            "career": "Undergraduate",
            "uoc": 96,
            "plan_code": "TESTRULES",
            "plan_description": "48 UoC RPL",
        }

    captured: list[RulesMetadata | None] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        *,
        program_metadata: RulesMetadata | None = None,
        **_kwargs: object,
    ) -> Path:
        captured.append(program_metadata)
        return out_dir / "p.json"

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_summarise_offerings(_offers: list[dict[str, set[str]]]) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(extract_plans_cli, "extract_program_sheet_header", fake_extract_program_sheet_header)
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(extract_plans_cli, "summarise_offerings", fake_summarise_offerings)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_file", fake_write_offerings_file)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv)

    code = extract_plans_cli.main(
        [str(excel), "--output-dir", str(out_dir), "--rules-dir", str(rules_dir)]
    )

    assert code == 0
    assert len(captured) == 1
    meta = captured[0]
    assert meta is not None
    assert meta["plan_code"] == "TESTRULES"
    assert meta["plan_description"] == "48 UoC RPL"


def test_main_falls_back_to_sheet_name_parens_when_header_plan_fields_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank header plan fields fall back to parens-only parsing from sheet_name."""
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "TESTRULES.json").write_text(
        json.dumps(
            {
                "program": {"id": "9999", "name": "Test Program"},
                "specialisations": [],
                "uoc": 96,
            }
        ),
        encoding="utf-8",
    )

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"TESTRULES (48 UoC RPL)": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("TESTRULES (48 UoC RPL)", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter([("2026 T1", plan, metadata)])

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {
            "program": "",
            "career": "Undergraduate",
            "uoc": 96,
            "plan_code": "",
            "plan_description": "",
        }

    captured: list[RulesMetadata | None] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        *,
        program_metadata: RulesMetadata | None = None,
        **_kwargs: object,
    ) -> Path:
        captured.append(program_metadata)
        return out_dir / "p.json"

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_summarise_offerings(_offers: list[dict[str, set[str]]]) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(extract_plans_cli, "extract_program_sheet_header", fake_extract_program_sheet_header)
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(extract_plans_cli, "summarise_offerings", fake_summarise_offerings)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_file", fake_write_offerings_file)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv)

    code = extract_plans_cli.main(
        [str(excel), "--output-dir", str(out_dir), "--rules-dir", str(rules_dir)]
    )

    assert code == 0
    assert len(captured) == 1
    meta = captured[0]
    assert meta is not None
    assert meta["plan_code"] == "TESTRULES"
    assert meta["plan_description"] == "48 UoC RPL"


def test_main_program_metadata_is_none_when_rules_dir_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing --rules-dir none suppresses program_metadata."""
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "out"

    def fake_read_excel(
        _file: Path, sheet_name: str | None = None, **_kwargs: object
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(
        _df: pd.DataFrame,
    ) -> Iterator[tuple[str, pd.DataFrame, PlanMetadata]]:
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        metadata: PlanMetadata = {
            "notes": {
                "graduate_outcome": "",
                "adjustment_type": "",
                "for_reviewers": [],
                "for_students": [],
            }
        }
        return iter([("2026 T1", plan, metadata)])

    monkeypatch.setattr(extract_plans_cli, "iter_program_sheets", fake_iter_sheets)
    monkeypatch.setattr(extract_plans_cli, "iter_plans", fake_iter_plans)

    def fake_extract_program_sheet_header(_sheet: pd.DataFrame) -> ProgramSheetHeader:
        return {"program": "TEST", "career": "Undergraduate", "uoc": 24}

    captured: list[RulesMetadata | None] = []

    def fake_export_plan(
        _sheet: str,
        _intake: str,
        _header: ProgramSheetHeader,
        _plan: pd.DataFrame,
        _output_dir: Path,
        _metadata: PlanMetadata,
        *,
        program_metadata: RulesMetadata | None = None,
        **_kwargs: object,
    ) -> Path:
        captured.append(program_metadata)
        return out_dir / "p.json"

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {}

    def fake_summarise_offerings(_offers: list[dict[str, set[str]]]) -> dict[str, set[str]]:
        return {}

    def fake_write_offerings_file(
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
    ) -> Path:
        return out_dir / "mapping_offerings.json"

    def fake_write_offerings_csv(
        _summary: dict[str, set[str]], _output_path: Path
    ) -> Path:
        return out_dir / "mapping_offerings.csv"

    monkeypatch.setattr(extract_plans_cli, "extract_program_sheet_header", fake_extract_program_sheet_header)
    monkeypatch.setattr(extract_plans_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(extract_plans_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(extract_plans_cli, "summarise_offerings", fake_summarise_offerings)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_file", fake_write_offerings_file)
    monkeypatch.setattr(extract_plans_cli, "write_offerings_csv", fake_write_offerings_csv)

    code = extract_plans_cli.main(
        [str(excel), "--output-dir", str(out_dir), "--rules-dir", "none"]
    )

    assert code == 0
    assert captured == [None]


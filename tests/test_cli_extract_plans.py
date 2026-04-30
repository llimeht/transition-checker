"""Behavior tests for extract_plans CLI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from transitionchecker.cli import extract_plans_cli
from transitionchecker.core.mapping_workbook import ProgramSheetHeader


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
        _file: Path, sheet_name: str | None = None
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(_df: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame]]:
        # one intake with one row so downstream functions are called
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        return iter([("2026 T1", plan)])

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
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
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
        _file: Path, sheet_name: str | None = None
    ) -> dict[str, pd.DataFrame]:
        return {"Sheet1": pd.DataFrame()}

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)

    def fake_iter_sheets(
        _dfs: dict[str, pd.DataFrame],
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(_df: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame]]:
        plan = pd.DataFrame(
            [{"Code": "[TEST0001]", "Period": "Term 1", "CourseN": "Course 1"}]
        )
        return iter([("2026 T1", plan)])

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
        _summary: dict[str, set[str]], _excel: Path, _output_dir: Path
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

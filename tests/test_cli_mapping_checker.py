"""Behavior tests for mapping_checker CLI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from transitionchecker.cli import mapping_checker_cli


def test_requires_excel_file_argument() -> None:
    with pytest.raises(SystemExit) as exc:
        mapping_checker_cli.main([])
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

    monkeypatch.setattr(mapping_checker_cli, "iter_sheets", fake_iter_sheets)
    monkeypatch.setattr(mapping_checker_cli, "iter_plans", fake_iter_plans)

    def fake_course_terms(_plan: pd.DataFrame) -> dict[str, set[str]]:
        return {"TEST1001": {"Term 1"}}

    def fake_export_plan(
        _sheet: str, _intake: str, _plan: pd.DataFrame, _output_dir: Path
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

    monkeypatch.setattr(mapping_checker_cli, "course_terms", fake_course_terms)
    monkeypatch.setattr(mapping_checker_cli, "export_plan", fake_export_plan)
    monkeypatch.setattr(
        mapping_checker_cli, "summarise_offerings", fake_summarise_offerings
    )
    monkeypatch.setattr(
        mapping_checker_cli, "write_offerings_file", fake_write_offerings_file
    )
    monkeypatch.setattr(
        mapping_checker_cli, "write_offerings_csv", fake_write_offerings_csv
    )

    code = mapping_checker_cli.main([str(excel), "--output-dir", str(out_dir)])
    assert code == 0
    assert out_dir.is_dir()

"""Behavior tests for mapping_checker CLI."""

from __future__ import annotations

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

    monkeypatch.setattr(
        mapping_checker_cli.pd,
        "read_excel",
        lambda _file, sheet_name=None: {"Sheet1": pd.DataFrame()},
    )

    def fake_iter_sheets(_dfs):
        return iter([("Sheet1", pd.DataFrame())])

    def fake_iter_plans(_df):
        # one intake with one row so downstream functions are called
        plan = pd.DataFrame([{"Code": "TEST1001", "Period": "Term 1"}])
        return iter([("2026 T1", plan)])

    monkeypatch.setattr(mapping_checker_cli, "iter_sheets", fake_iter_sheets)
    monkeypatch.setattr(mapping_checker_cli, "iter_plans", fake_iter_plans)
    monkeypatch.setattr(mapping_checker_cli, "course_terms", lambda _plan: {"TEST1001": {"Term 1"}})
    monkeypatch.setattr(mapping_checker_cli, "export_plan", lambda *_args, **_kwargs: out_dir / "p.json")
    monkeypatch.setattr(mapping_checker_cli, "summarise_offerings", lambda _offers: {"TEST1001": {"Term 1"}})
    monkeypatch.setattr(
        mapping_checker_cli,
        "write_offerings_file",
        lambda *_args, **_kwargs: out_dir / "mapping_offerings.json",
    )
    monkeypatch.setattr(
        mapping_checker_cli,
        "write_offerings_csv",
        lambda *_args, **_kwargs: out_dir / "mapping_offerings.csv",
    )

    code = mapping_checker_cli.main([str(excel), "--output-dir", str(out_dir)])
    assert code == 0
    assert out_dir.is_dir()

"""Behavior tests for cfcc_summary CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from transitionchecker.cli import cfcc_summary_cli


def test_requires_plans_dir_argument() -> None:
    with pytest.raises(SystemExit) as exc:
        cfcc_summary_cli.main([])
    assert exc.value.code == 2


def test_parse_target_term_accepts_aliases() -> None:
    year, canonical, slug = cfcc_summary_cli.parse_target_term(2026, "T1")
    assert year == 2026
    assert canonical == "term 1"
    assert slug == "2026_T1"


@pytest.mark.parametrize("bad_period", ["", "T4", "Autumn", "Q1"])
def test_parse_target_term_rejects_bad_period(bad_period: str) -> None:
    with pytest.raises(ValueError):
        cfcc_summary_cli.parse_target_term(2026, bad_period)


def test_main_returns_1_for_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    exit_code = cfcc_summary_cli.main([str(missing), "--year", "2026", "--period", "T1"])
    assert exit_code == 1


def test_main_writes_default_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()

    def fake_build_rows(_plans_dir: Path, _target_year: int, _target_period: str) -> list[list[str]]:
        return [["P", "A", "B", "", "", "x.json"]]

    monkeypatch.setattr(cfcc_summary_cli, "_build_rows", fake_build_rows)

    captured: list[Path] = []

    def fake_write(path: Path, _rows: list[list[str]]) -> None:
        captured.append(path)

    monkeypatch.setattr(cfcc_summary_cli, "write_csv", fake_write)

    exit_code = cfcc_summary_cli.main([str(plans_dir), "--year", "2026", "--period", "Term 3"])
    assert exit_code == 0
    assert captured[0] == plans_dir / "2026_T3_CFCCs.csv"

"""Behavior tests for extract_template CLI."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from transitionchecker.cli import extract_template_cli


class _FakeWorkbook:
    def close(self) -> None:
        return None


def test_parse_args_requires_xlsx() -> None:
    with pytest.raises(SystemExit) as exc:
        extract_template_cli.parse_args([])
    assert exc.value.code == 2


def test_main_returns_1_for_missing_workbook(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xlsx"
    code = extract_template_cli.main([str(missing)])
    assert code == 1


def test_main_success_writes_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "mapping.xlsx"
    excel.write_text("placeholder", encoding="utf-8")

    catalogue_out = tmp_path / "plans" / "catalogue.json"
    template_out = tmp_path / "templates" / "template_configs.json"

    monkeypatch.setattr(
        openpyxl,
        "load_workbook",
        lambda _path, data_only=True: _FakeWorkbook(),
    )
    monkeypatch.setattr(
        extract_template_cli,
        "extract_catalogue",
        lambda _wb: {
            "TEST1001": {
                "title": "T",
                "uoc": 6,
                "prerequisites": ".",
                "level": "Level 1",
            }
        },
    )
    monkeypatch.setattr(
        extract_template_cli,
        "extract_template_configs_from_workbook",
        lambda _path: {"intakes": {"2026 T1": {"years": []}}},
    )

    code = extract_template_cli.main(
        [
            str(excel),
            "--catalogue-output",
            str(catalogue_out),
            "--template-output",
            str(template_out),
        ]
    )

    assert code == 0
    assert catalogue_out.is_file()
    assert template_out.is_file()

    catalogue = json.loads(catalogue_out.read_text(encoding="utf-8"))
    templates = json.loads(template_out.read_text(encoding="utf-8"))
    assert "TEST1001" in catalogue
    assert "intakes" in templates

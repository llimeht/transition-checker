"""Behavior tests for add_offerings CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transitionchecker.cli import add_offerings_cli


def test_requires_mode_argument(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        add_offerings_cli.main([str(offerings_path)])
    assert exc.value.code == 2


def test_validate_mode_sorts_and_canonicalizes(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps(
            {
                "zzzz9999": ["T3", "t1"],
                "abcd1234": ["Term 2", "S1", "Term 1"],
            }
        ),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main([str(offerings_path), "--validate"])

    assert exit_code == 0
    result = json.loads(offerings_path.read_text(encoding="utf-8"))
    assert list(result.keys()) == ["ABCD1234", "ZZZZ9999"]
    assert result["ABCD1234"] == ["Term 1", "Term 2", "Semester 1"]
    assert result["ZZZZ9999"] == ["Term 1", "Term 3"]


def test_validate_mode_fails_on_unknown_period_and_keeps_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    original_content = json.dumps({"ABCD1234": ["Term 1", "Hexamester 1"]}, indent=2)
    offerings_path.write_text(original_content, encoding="utf-8")

    exit_code = add_offerings_cli.main([str(offerings_path), "--validate"])

    assert exit_code == 1
    assert "Unknown teaching period" in capsys.readouterr().err
    assert offerings_path.read_text(encoding="utf-8") == original_content


def test_schedule_mode_adds_and_sorts_periods(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps({"ABCD1234": ["Term 3"]}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--schedule", "abcd1234", "T1", "S1"]
    )

    assert exit_code == 0
    result = json.loads(offerings_path.read_text(encoding="utf-8"))
    assert result["ABCD1234"] == ["Term 1", "Term 3", "Semester 1"]


def test_schedule_mode_fails_on_unknown_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    original_content = json.dumps({"ABCD1234": ["Term 1"]}, indent=2)
    offerings_path.write_text(original_content, encoding="utf-8")

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--schedule", "ABCD1234", "Hexamester 1"]
    )

    assert exit_code == 1
    assert "Unknown teaching period" in capsys.readouterr().err
    assert offerings_path.read_text(encoding="utf-8") == original_content

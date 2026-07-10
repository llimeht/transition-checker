"""Behavior tests for add_offerings CLI."""

from __future__ import annotations

import csv
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


def test_validate_mode_preserves_year_specific_entries(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps(
            {
                "ceic2001": {"all": ["t2"], "2026": ["t1", "t1"]},
            }
        ),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main([str(offerings_path), "--validate"])

    assert exit_code == 0
    result = json.loads(offerings_path.read_text(encoding="utf-8"))
    assert result == {"CEIC2001": {"all": ["Term 2"], "2026": ["Term 1"]}}


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


def test_schedule_mode_updates_all_years_and_preserves_year_specific_entries(
    tmp_path: Path,
) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps({"ABCD1234": {"all": ["Term 3"], "2026": ["Term 1"]}}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--schedule", "abcd1234", "S1"]
    )

    assert exit_code == 0
    result = json.loads(offerings_path.read_text(encoding="utf-8"))
    assert result == {
        "ABCD1234": {"all": ["Term 3", "Semester 1"], "2026": ["Term 1"]}
    }


def test_schedule_mode_can_target_one_year_entry(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps({"ABCD1234": {"all": ["Term 3"], "2026": ["Term 1"]}}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--year", "2026", "--schedule", "abcd1234", "S1"]
    )

    assert exit_code == 0
    result = json.loads(offerings_path.read_text(encoding="utf-8"))
    assert result == {
        "ABCD1234": {"all": ["Term 3"], "2026": ["Term 1", "Semester 1"]}
    }


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


def test_show_mode_displays_matching_courses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps(
            {
                "comp1001": ["T1", "S1"],
                "COMP2002": ["T3"],
                "MATH1001": ["T2"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main([str(offerings_path), "--show", "COMP*"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "COMP1001" in captured.out
    assert "COMP2002" in captured.out
    assert "MATH1001" not in captured.out


def test_show_mode_flattens_year_specific_entries_for_display(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps({"COMP1001": {"all": ["Term 2"], "2026": ["Term 1"]}}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main([str(offerings_path), "--show", "COMP*"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "COMP1001" in captured.out
    assert "Term 1" in captured.out
    assert "Term 2" in captured.out


def test_show_mode_can_render_year_specific_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps(
            {
                "COMP1001": {"all": ["Term 2"], "2026": ["Term 1"]},
                "COMP2002": {"2027": ["Term 3"]},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--show", "COMP*", "--show-by-year"]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "COMP1001" in captured.out
    assert "all  Term 2" in captured.out
    assert "2026 Term 1" in captured.out
    assert "2027 Term 3" in captured.out


def test_show_by_year_rejects_output_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    output_path = tmp_path / "shown.csv"
    offerings_path.write_text(
        json.dumps({"COMP1001": {"2026": ["Term 1"]}}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [
            str(offerings_path),
            "--show",
            "COMP*",
            "--show-by-year",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 2
    assert "--output is not supported with --show-by-year" in capsys.readouterr().err


def test_show_mode_can_write_csv_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    output_path = tmp_path / "shown.csv"
    offerings_path.write_text(
        json.dumps(
            {
                "COMP1001": ["Term 1", "Semester 1"],
                "COMP2002": ["Term 3"],
                "MATH1001": ["Term 2"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main(
        [str(offerings_path), "--show", "COMP*", "--output", str(output_path)]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Wrote CSV: {output_path.resolve()}" in captured.err

    with open(output_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows == [
        {"course": "COMP1001", "Semester 1": "Y", "Term 1": "Y", "Term 3": ""},
        {"course": "COMP2002", "Semester 1": "", "Term 1": "", "Term 3": "Y"},
    ]


def test_output_requires_show_mode(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "shown.csv"

    with pytest.raises(SystemExit) as exc:
        add_offerings_cli.main(
            [str(offerings_path), "--validate", "--output", str(output_path)]
        )

    assert exc.value.code == 2


def test_year_requires_schedule_mode(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        add_offerings_cli.main([str(offerings_path), "--validate", "--year", "2026"])

    assert exc.value.code == 2


def test_show_by_year_requires_show_mode(tmp_path: Path) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        add_offerings_cli.main([str(offerings_path), "--validate", "--show-by-year"])

    assert exc.value.code == 2


def test_show_mode_fails_on_unknown_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offerings_path = tmp_path / "offerings.json"
    offerings_path.write_text(
        json.dumps({"COMP1001": ["Hexamester 1"]}, indent=2),
        encoding="utf-8",
    )

    exit_code = add_offerings_cli.main([str(offerings_path), "--show", "COMP*"])

    assert exit_code == 1
    assert "Unknown teaching period" in capsys.readouterr().err

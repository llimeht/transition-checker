"""Behaviour tests for transitionchecker.core.rules_loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transitionchecker.core.rules_loader import (
    intake_year_from_intake_string,
    load_rules_metadata,
    resolve_plan_code,
    resolve_rule_file,
    resolve_rule_file_for_plan,
)


# ---------------------------------------------------------------------------
# intake_year_from_intake_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intake, expected",
    [
        ("2026 T1", 2026),
        ("2028 S2", 2028),
        ("2030 T3", 2030),
        ("", None),
        ("T1 2026", None),  # year not at start
    ],
)
def test_intake_year_from_intake_string(intake: str, expected: int | None) -> None:
    assert intake_year_from_intake_string(intake) == expected


# ---------------------------------------------------------------------------
# resolve_rule_file
# ---------------------------------------------------------------------------


def test_resolve_rule_file_returns_plain_file_when_no_range(tmp_path: Path) -> None:
    (tmp_path / "PROG1234.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("PROG1234", 2026, tmp_path)
    assert result == tmp_path / "PROG1234.json"


def test_resolve_rule_file_prefers_ranged_file_when_year_matches(
    tmp_path: Path,
) -> None:
    (tmp_path / "PROG1234.json").write_text("{}", encoding="utf-8")
    (tmp_path / "PROG1234-2026-2029.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("PROG1234", 2027, tmp_path)
    assert result == tmp_path / "PROG1234-2026-2029.json"


def test_resolve_rule_file_falls_back_when_year_outside_range(tmp_path: Path) -> None:
    (tmp_path / "PROG1234.json").write_text("{}", encoding="utf-8")
    (tmp_path / "PROG1234-2026-2029.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("PROG1234", 2030, tmp_path)
    assert result == tmp_path / "PROG1234.json"


def test_resolve_rule_file_uses_plain_file_when_no_intake_year(
    tmp_path: Path,
) -> None:
    (tmp_path / "PROG1234-2026-2029.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("PROG1234", None, tmp_path)
    assert result == tmp_path / "PROG1234.json"


# ---------------------------------------------------------------------------
# resolve_rule_file_for_plan
# ---------------------------------------------------------------------------


def test_resolve_rule_file_for_plan_extracts_year_from_stem(tmp_path: Path) -> None:
    (tmp_path / "PROG1234-2026-2029.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file_for_plan("PROG1234", "PROG1234_2027_T2", tmp_path)
    assert result == tmp_path / "PROG1234-2026-2029.json"


def test_resolve_rule_file_for_plan_falls_back_without_year_in_stem(
    tmp_path: Path,
) -> None:
    result = resolve_rule_file_for_plan("PROG1234", "PROG1234_nodate", tmp_path)
    assert result == tmp_path / "PROG1234.json"


# ---------------------------------------------------------------------------
# resolve_plan_code
# ---------------------------------------------------------------------------


def test_resolve_plan_code_exact_match(tmp_path: Path) -> None:
    (tmp_path / "CEICAH3707.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICAH3707", tmp_path) == "CEICAH3707"


def test_resolve_plan_code_exact_match_ranged_file(tmp_path: Path) -> None:
    """A ranged-only rules file counts as an exact match for the base code."""
    (tmp_path / "CEICDH3707-2026-2029.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICDH3707", tmp_path) == "CEICDH3707"


def test_resolve_plan_code_prefers_exact_over_cropped(tmp_path: Path) -> None:
    (tmp_path / "CEICKS8338.json").write_text("{}", encoding="utf-8")
    (tmp_path / "CEICKS8338(48RPL).json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICKS8338(48RPL)", tmp_path) == "CEICKS8338(48RPL)"


def test_resolve_plan_code_strips_trailing_parens_no_space(tmp_path: Path) -> None:
    (tmp_path / "CEICKS8338.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICKS8338(48RPL)", tmp_path) == "CEICKS8338"


def test_resolve_plan_code_strips_trailing_parens_with_space(tmp_path: Path) -> None:
    (tmp_path / "CEICKS8338.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICKS8338 (48 UoC RPL)", tmp_path) == "CEICKS8338"


def test_resolve_plan_code_strips_at_underscore(tmp_path: Path) -> None:
    (tmp_path / "CEICAH3707.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICAH3707_Mature_Age", tmp_path) == "CEICAH3707"


def test_resolve_plan_code_strips_multiple_underscore_steps(tmp_path: Path) -> None:
    (tmp_path / "CEICAH3707.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICAH3707_x_y_z", tmp_path) == "CEICAH3707"


def test_resolve_plan_code_strips_at_hyphen(tmp_path: Path) -> None:
    (tmp_path / "CEICAH3707.json").write_text("{}", encoding="utf-8")
    assert resolve_plan_code("CEICAH3707-special", tmp_path) == "CEICAH3707"


def test_resolve_plan_code_returns_original_when_no_match(tmp_path: Path) -> None:
    assert resolve_plan_code("UNKNOWN9999_foo", tmp_path) == "UNKNOWN9999_foo"


def test_resolve_rule_file_uses_cropped_code_with_intake_year(tmp_path: Path) -> None:
    (tmp_path / "CEICAH3707.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("CEICAH3707_Mature", 2026, tmp_path)
    assert result == tmp_path / "CEICAH3707.json"


def test_resolve_rule_file_uses_cropped_code_with_ranged_file(
    tmp_path: Path,
) -> None:
    """Cropped code selects the year-ranged file when intake year matches."""
    (tmp_path / "CEICAH3707-2026-2029.json").write_text("{}", encoding="utf-8")
    result = resolve_rule_file("CEICAH3707_Mature", 2027, tmp_path)
    assert result == tmp_path / "CEICAH3707-2026-2029.json"


def test_resolve_rule_file_logs_warning_when_no_match(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    with caplog.at_level(logging.WARNING, logger="transitionchecker.core.rules_loader"):
        resolve_rule_file("UNKNOWN9999", 2026, tmp_path)
    assert any("UNKNOWN9999" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# load_rules_metadata
# ---------------------------------------------------------------------------


def _write_rules(path: Path, data: dict) -> None:  # type: ignore[type-arg]
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_rules_metadata_returns_none_when_file_missing(tmp_path: Path) -> None:
    result = load_rules_metadata(tmp_path / "missing.json", "PROG", "")
    assert result is None


def test_load_rules_metadata_extracts_fields(tmp_path: Path) -> None:
    rules_file = tmp_path / "CEICAH3707.json"
    _write_rules(
        rules_file,
        {
            "program": {"id": "3707", "name": "Bachelor of Engineering (Honours)"},
            "specialisations": [{"id": "CEICAH", "name": "Chemical Engineering"}],
            "uoc": 192,
        },
    )
    meta = load_rules_metadata(rules_file, "CEICAH3707", "")
    assert meta is not None
    assert meta["plan_code"] == "CEICAH3707"
    assert meta["plan_description"] == ""
    assert meta["program"] == {"id": "3707", "name": "Bachelor of Engineering (Honours)"}
    assert meta["specialisation"] == [{"id": "CEICAH", "name": "Chemical Engineering"}]
    assert meta["uoc"] == 192
    assert meta["rules_description"] == ""


def test_load_rules_metadata_includes_description_when_present(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "PROG.json"
    _write_rules(
        rules_file,
        {
            "program": {"id": "1234", "name": "Test Program"},
            "specialisations": [],
            "uoc": 48,
            "description": "Extended pathway for RPL students",
        },
    )
    meta = load_rules_metadata(rules_file, "PROG", "")
    assert meta is not None
    assert meta["rules_description"] == "Extended pathway for RPL students"


def test_load_rules_metadata_handles_multiple_specialisations(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "MATSM13132.json"
    _write_rules(
        rules_file,
        {
            "program": {"id": "3132", "name": "BE(Hons)/BEngSci"},
            "specialisations": [
                {"id": "MATSM1", "name": "Materials Science and Engineering"},
                {"id": "CEICM1", "name": "Chemical Engineering"},
            ],
            "uoc": 240,
        },
    )
    meta = load_rules_metadata(rules_file, "MATSM13132", "")
    assert meta is not None
    assert len(meta["specialisation"]) == 2
    assert meta["uoc"] == 240


def test_load_rules_metadata_passes_plan_description_through(
    tmp_path: Path,
) -> None:
    rules_file = tmp_path / "CEICKS8338.json"
    _write_rules(rules_file, {"program": {"id": "8338", "name": "Grad Cert"}, "specialisations": [], "uoc": 48})
    meta = load_rules_metadata(rules_file, "CEICKS8338", "(48RPL)")
    assert meta is not None
    assert meta["plan_description"] == "(48RPL)"

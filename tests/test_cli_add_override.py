"""Behaviour tests for the add-override CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transitionchecker.cli import add_override_cli


def _write_catalogue(tmp_path: Path) -> Path:
    """Write a minimal catalogue.json and return its path."""
    p = tmp_path / "plans" / "catalogue.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "Undergraduate",
                    "title": "C",
                    "uoc": 6,
                    "prerequisites": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    return p


def _overrides_path(catalogue_path: Path) -> Path:
    return catalogue_path.parent / "catalogue_overrides.json"


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_requires_course(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)
    with pytest.raises(SystemExit) as exc:
        add_override_cli.main([str(cat), "--prereq", "CEIC2000", "--reason", "r"])
    assert exc.value.code == 2


def test_requires_prereq(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)
    with pytest.raises(SystemExit) as exc:
        add_override_cli.main([str(cat), "--course", "CEIC3000", "--reason", "r"])
    assert exc.value.code == 2


def test_requires_reason(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)
    with pytest.raises(SystemExit) as exc:
        add_override_cli.main(
            [str(cat), "--course", "CEIC3000", "--prereq", "CEIC2000"]
        )
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_creates_overrides_file_when_absent(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--prereq",
            "CEIC2000",
            "--reason",
            "test reason",
        ]
    )

    assert code == 0
    overrides_file = _overrides_path(cat)
    assert overrides_file.is_file()
    data = json.loads(overrides_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["code"] == "CEIC3000"
    assert data[0]["career"] == "Undergraduate"
    assert data[0]["prerequisites"] == "CEIC2000"
    assert data[0]["reason"] == "test reason"
    assert "date" in data[0]


def test_updates_existing_override(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)
    overrides_file = _overrides_path(cat)
    overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC3000",
                    "career": "Undergraduate",
                    "prerequisites": "CEIC1000",
                    "reason": "old",
                    "date": "2026-01-01",
                }
            ]
        ),
        encoding="utf-8",
    )

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--prereq",
            "CEIC2000",
            "--reason",
            "updated",
        ]
    )

    assert code == 0
    data = json.loads(overrides_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["code"] == "CEIC3000"
    assert data[0]["prerequisites"] == "CEIC2000"
    assert data[0]["reason"] == "updated"


def test_adds_second_course_preserves_first(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)
    overrides_file = _overrides_path(cat)
    overrides_file.write_text(
        json.dumps(
            [
                {
                    "code": "CEIC1000",
                    "career": "Undergraduate",
                    "prerequisites": "CEIC0000",
                    "reason": "existing",
                    "date": "2026-01-01",
                }
            ]
        ),
        encoding="utf-8",
    )

    code = add_override_cli.main(
        [str(cat), "--course", "CEIC3000", "--prereq", "CEIC2000", "--reason", "new"]
    )

    assert code == 0
    data = json.loads(overrides_file.read_text(encoding="utf-8"))
    assert [(entry["code"], entry["career"]) for entry in data] == [
        ("CEIC1000", "Undergraduate"),
        ("CEIC3000", "Undergraduate"),
    ]


def test_normalizes_lowercase_course_code(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [str(cat), "--course", "ceic3000", "--prereq", "CEIC2000", "--reason", "r"]
    )

    assert code == 0
    data = json.loads(_overrides_path(cat).read_text(encoding="utf-8"))
    assert data[0]["code"] == "CEIC3000"
    assert data[0]["career"] == "Undergraduate"


def test_normalizes_undergraduate_career_aliases(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--career",
            "ug",
            "--prereq",
            "CEIC2000",
            "--reason",
            "r",
        ]
    )

    assert code == 0
    data = json.loads(_overrides_path(cat).read_text(encoding="utf-8"))
    assert data[0]["career"] == "Undergraduate"


def test_normalizes_postgraduate_career_aliases_case_insensitively(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--career",
            "pGrD",
            "--prereq",
            "CEIC2000",
            "--reason",
            "r",
        ]
    )

    assert code == 0
    data = json.loads(_overrides_path(cat).read_text(encoding="utf-8"))
    assert data[0]["career"] == "Postgraduate"


# ---------------------------------------------------------------------------
# Prereq validation
# ---------------------------------------------------------------------------


def test_rejects_unparseable_prereq(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--prereq",
            "must have done something complicated",
            "--reason",
            "r",
        ]
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert "parse" in stderr.lower()
    # File must not have been created
    assert not _overrides_path(cat).exists()


def test_force_allows_unparseable_prereq(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--prereq",
            "must have done something complicated",
            "--reason",
            "r",
            "--force",
        ]
    )

    assert code == 0
    stderr = capsys.readouterr().err
    assert "warning" in stderr.lower()
    data = json.loads(_overrides_path(cat).read_text(encoding="utf-8"))
    assert data[0]["prerequisites"] == "must have done something complicated"


def test_parseable_prereq_succeeds_without_force(tmp_path: Path) -> None:
    cat = _write_catalogue(tmp_path)

    code = add_override_cli.main(
        [
            str(cat),
            "--course",
            "CEIC3000",
            "--prereq",
            "CEIC2000 AND CEIC2010",
            "--reason",
            "r",
        ]
    )

    assert code == 0
    data = json.loads(_overrides_path(cat).read_text(encoding="utf-8"))
    assert data[0]["prerequisites"] == "CEIC2000 AND CEIC2010"

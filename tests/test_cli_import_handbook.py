"""Behavior tests for import_handbook CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import requests

from transitionchecker.cli import import_handbook_cli


def _sample_payload() -> dict[str, object]:
    return {
        "props": {
            "pageProps": {
                "data": {
                    "code": "BIOC2101",
                    "title": "Principles of Biochemistry (Advanced)",
                    "uoc": 6,
                    "offering_detail": {"offering_terms": "Term 2"},
                    "enrolment_rules": [
                        {
                            "description": (
                                "Prerequisite: BABS1201 or DPST1051 and CHEM1011 "
                                "or DPST1031 or CHEM1031 or CHEM1051 or CHEM1811 "
                                "and CHEM1021 or DPST1032 or CHEM1041 or CHEM1061 "
                                "or CHEM1821<br/><br/>"
                            )
                        }
                    ],
                }
            }
        }
    }


def _sample_html() -> str:
    return (
        "<html><head></head><body>"
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(_sample_payload())}"
        "</script></body></html>"
    )


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def get(self, url: str, *, timeout: float) -> _FakeResponse:
        assert url
        assert timeout == 12.0
        return self.response

    def close(self) -> None:
        return None


def test_parse_args_requires_year() -> None:
    with pytest.raises(SystemExit) as exc:
        import_handbook_cli.parse_args(["BIOC2101"])
    assert exc.value.code == 2


def test_parse_args_defaults_to_undergraduate() -> None:
    args = import_handbook_cli.parse_args(["--year", "2026", "BIOC2101"])
    assert args.career == "undergraduate"


def test_build_url_supports_postgraduate() -> None:
    url = import_handbook_cli.build_url(2026, "ceic8201", "postgraduate")
    assert url == "https://www.handbook.unsw.edu.au/postgraduate/courses/2026/CEIC8201"


def test_extract_course_record_from_html() -> None:
    record = import_handbook_cli.extract_course_record_from_html(
        page_html=_sample_html(),
        course_code="BIOC2101",
        year=2026,
        handbook_url="https://www.handbook.unsw.edu.au/undergraduate/courses/2026/BIOC2101",
    )

    assert record.course_title == "Principles of Biochemistry (Advanced)"
    assert record.uoc == "6"
    assert record.offering_terms == "T2"
    assert (
        record.prerequisite
        == "BABS1201 or DPST1051 and CHEM1011 or DPST1031 or CHEM1031 or CHEM1051 or CHEM1811 and CHEM1021 or DPST1032 or CHEM1041 or CHEM1061 or CHEM1821"
    )


def test_fetch_handbook_record_uses_requests_session() -> None:
    session = _FakeSession(_FakeResponse(_sample_html()))
    record = import_handbook_cli.fetch_handbook_record(
        session,
        course_code="BIOC2101",
        year=2026,
        career="undergraduate",
        timeout=12.0,
        retries=0,
    )

    assert record.fetch_status == "ok"
    assert record.course_code == "BIOC2101"
    assert record.offering_terms == "T2"


def test_main_writes_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_path = tmp_path / "handbook.csv"

    def _fake_fetch_record(
        _session: import_handbook_cli.SupportsSessionGet,
        *,
        course_code: str,
        year: int,
        career: str,
        timeout: float,
        retries: int,
    ) -> import_handbook_cli.HandbookCourseRecord:
        assert course_code == "BIOC2101"
        assert year == 2026
        assert career == "undergraduate"
        assert timeout == 20.0
        assert retries == 2
        return import_handbook_cli.HandbookCourseRecord(
            course_code="BIOC2101",
            year=2026,
            career=career,
            handbook_url="https://www.handbook.unsw.edu.au/undergraduate/courses/2026/BIOC2101",
            course_title="Principles of Biochemistry (Advanced)",
            uoc="6",
            offering_terms="T2",
            prerequisite="BABS1201 or DPST1051",
            fetch_status="ok",
            error_message="",
        )

    monkeypatch.setattr(
        import_handbook_cli,
        "fetch_handbook_record",
        _fake_fetch_record,
    )

    code = import_handbook_cli.main(
        [
            "--year",
            "2026",
            "--career",
            "undergraduate",
            "BIOC2101",
            "--output",
            str(output_path),
            "--sleep",
            "0",
        ]
    )

    assert code == 0
    with output_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["Code"] == "BIOC2101"
    assert rows[0]["Title"] == "Principles of Biochemistry (Advanced)"
    assert rows[0]["UoC"] == "6"
    assert rows[0]["Prereqs"] == "BABS1201 or DPST1051"
    assert rows[0]["career"] == "undergraduate"
    assert rows[0]["offering_terms"] == "T2"


def test_main_deduplicates_course_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "handbook_dedup.csv"
    seen_codes: list[str] = []

    def _fake_fetch_record(
        _session: import_handbook_cli.SupportsSessionGet,
        *,
        course_code: str,
        year: int,
        career: str,
        timeout: float,
        retries: int,
    ) -> import_handbook_cli.HandbookCourseRecord:
        seen_codes.append(course_code)
        return import_handbook_cli.HandbookCourseRecord(
            course_code=course_code,
            year=year,
            career=career,
            handbook_url=(
                "https://www.handbook.unsw.edu.au/undergraduate/courses/2026/"
                f"{course_code}"
            ),
            course_title=f"{course_code} title",
            uoc="6",
            offering_terms="T2",
            prerequisite="",
            fetch_status="ok",
            error_message="",
        )

    monkeypatch.setattr(
        import_handbook_cli, "fetch_handbook_record", _fake_fetch_record
    )

    code = import_handbook_cli.main(
        [
            "--year",
            "2026",
            "BIOC2101",
            "bioc2101",
            "CHEM1011",
            "BIOC2101",
            "--output",
            str(output_path),
            "--sleep",
            "0",
        ]
    )

    assert code == 0
    assert seen_codes == ["BIOC2101", "CHEM1011"]

    with output_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert [row["Code"] for row in rows] == ["BIOC2101", "CHEM1011"]

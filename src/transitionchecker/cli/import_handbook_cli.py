"""Import course data from UNSW Handbook pages into a CSV file."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO, cast

import requests

from transitionchecker.core.course_utils import normalize_course_code


_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(?P<payload>{.*})\s*</script>',
    re.DOTALL,
)

_TAG_RE = re.compile(r"<[^>]+>")

_CSV_COLUMNS = [
    "course_code",
    "year",
    "career",
    "handbook_url",
    "course_title",
    "offering_terms",
    "prerequisite",
    "fetch_status",
    "error_message",
]

_HANDBOOK_CAREERS = ("undergraduate", "postgraduate")


@dataclass(frozen=True)
class ImportHandbookCommand:
    year: int
    career: str
    course_codes: list[str]
    output_path: Path
    timeout: float = 20.0
    retries: int = 2
    sleep_seconds: float = 0.2


@dataclass(frozen=True)
class HandbookCourseRecord:
    course_code: str
    year: int
    career: str
    handbook_url: str
    course_title: str
    offering_terms: str
    prerequisite: str
    fetch_status: str
    error_message: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "course_code": self.course_code,
            "year": str(self.year),
            "career": self.career,
            "handbook_url": self.handbook_url,
            "course_title": self.course_title,
            "offering_terms": self.offering_terms,
            "prerequisite": self.prerequisite,
            "fetch_status": self.fetch_status,
            "error_message": self.error_message,
        }


class SupportsSessionGet(Protocol):
    def get(self, url: str, *, timeout: float) -> Any: ...

    def close(self) -> None: ...


def build_url(year: int, course_code: str, career: str) -> str:
    normalized = normalize_course_code(course_code)
    return f"https://www.handbook.unsw.edu.au/{career}/courses/{year}/{normalized}"


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download UNSW handbook course pages and extract course data to CSV.",
        epilog=(
            "Examples:\n"
            "  python3 import_handbook.py --year 2026 --career undergraduate BIOC2101 CHEM1011\n"
            "  python3 import_handbook.py --year 2026 --career postgraduate CEIC8201 --output plans/handbook.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--year", required=True, type=int, help="Handbook year to fetch"
    )
    parser.add_argument(
        "--career",
        choices=_HANDBOOK_CAREERS,
        default="undergraduate",
        help="Handbook course career path to use (default: undergraduate)",
    )
    parser.add_argument(
        "course_codes",
        nargs="+",
        help="One or more course codes, for example BIOC2101 CHEM1011",
    )
    parser.add_argument(
        "--output",
        default="plans/handbook_import.csv",
        help="CSV output path (default: plans/handbook_import.csv)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count for transient request failures (default: 2)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Delay between requests in seconds (default: 0.2)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_cli_parser().parse_args(argv)


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": ("transition-checker/0.1 (+https://www.handbook.unsw.edu.au/)")}
    )
    return session


def _extract_next_data_payload(page_html: str) -> dict[str, Any]:
    match = _NEXT_DATA_RE.search(page_html)
    if match is None:
        raise ValueError("could not find __NEXT_DATA__ JSON payload in handbook page")

    payload_text = html.unescape(match.group("payload"))
    decoded_payload = json.loads(payload_text)
    if not isinstance(decoded_payload, dict):
        raise ValueError("embedded handbook payload was not a JSON object")
    return cast(dict[str, Any], decoded_payload)


def _find_first_dict(
    node: Any, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any] | None:
    if isinstance(node, dict):
        typed_node = cast(dict[str, Any], node)
        if predicate(typed_node):
            return typed_node
        for value in typed_node.values():
            match = _find_first_dict(value, predicate)
            if match is not None:
                return match
        return None

    if isinstance(node, list):
        typed_list = cast(list[Any], node)  # type: ignore[redundant-cast]
        for item in typed_list:
            match = _find_first_dict(item, predicate)
            if match is not None:
                return match

    return None


def _extract_page_data(payload: dict[str, Any], course_code: str) -> dict[str, Any]:
    props = cast(Any, payload.get("props"))
    if isinstance(props, dict):
        typed_props = cast(dict[str, Any], props)
        page_props = cast(Any, typed_props.get("pageProps"))
        if isinstance(page_props, dict):
            typed_page_props = cast(dict[str, Any], page_props)
            data = cast(Any, typed_page_props.get("data"))
            if isinstance(data, dict):
                return cast(dict[str, Any], data)

    normalized = normalize_course_code(course_code)
    match = _find_first_dict(
        payload,
        lambda candidate: (
            isinstance(candidate.get("code"), str)
            and normalize_course_code(str(candidate["code"])) == normalized
            and (
                "title" in candidate
                or "offering_detail" in candidate
                or "enrolment_rules" in candidate
            )
        ),
    )
    if match is None:
        raise ValueError("could not locate course data within handbook payload")
    return match


def _clean_text(raw_text: str) -> str:
    with_breaks = re.sub(r"<br\s*/?>", " ", raw_text, flags=re.IGNORECASE)
    without_tags = _TAG_RE.sub(" ", with_breaks)
    return " ".join(html.unescape(without_tags).split())


def _normalize_offering_terms(raw_terms: str) -> str:
    cleaned = " ".join(raw_terms.split())
    if not cleaned:
        return ""

    replacements = {
        "summer term": "Summer",
        "winter term": "Winter",
        "summer": "Summer",
        "winter": "Winter",
    }
    lowered = cleaned.lower()
    if lowered in replacements:
        return replacements[lowered]

    term_match = re.fullmatch(r"Term\s*([123])", cleaned, flags=re.IGNORECASE)
    if term_match is not None:
        return f"T{term_match.group(1)}"

    semester_match = re.fullmatch(r"Semester\s*([12])", cleaned, flags=re.IGNORECASE)
    if semester_match is not None:
        return f"S{semester_match.group(1)}"

    return cleaned


def _extract_prerequisite(page_data: dict[str, Any]) -> str:
    rules = cast(Any, page_data.get("enrolment_rules"))
    if not isinstance(rules, list):
        return ""

    typed_rules = cast(list[Any], rules)  # type: ignore[redundant-cast]
    for rule in typed_rules:
        if not isinstance(rule, dict):
            continue
        typed_rule = cast(dict[str, Any], rule)
        description = cast(Any, typed_rule.get("description"))
        if not isinstance(description, str):
            continue

        cleaned = _clean_text(description)
        if not cleaned:
            continue

        match = re.search(
            r"Prerequisite\s*:\s*(.*?)(?:\bCo-?requisite\b\s*:|$)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if match is not None:
            return match.group(1).strip(" ;,.")

    return ""


def extract_course_record_from_html(
    *, page_html: str, course_code: str, year: int, handbook_url: str
) -> HandbookCourseRecord:
    payload = _extract_next_data_payload(page_html)
    page_data = _extract_page_data(payload, course_code)

    title = page_data.get("title")
    offering_detail = page_data.get("offering_detail")
    raw_terms = ""
    if isinstance(offering_detail, dict):
        typed_offering_detail = cast(dict[str, Any], offering_detail)
        raw_value = cast(Any, typed_offering_detail.get("offering_terms"))
        if isinstance(raw_value, str):
            raw_terms = raw_value

    course_title = title if isinstance(title, str) else ""
    return HandbookCourseRecord(
        course_code=normalize_course_code(course_code),
        year=year,
        career="",
        handbook_url=handbook_url,
        course_title=_clean_text(course_title),
        offering_terms=_normalize_offering_terms(raw_terms),
        prerequisite=_extract_prerequisite(page_data),
        fetch_status="ok",
        error_message="",
    )


def fetch_handbook_record(
    session: SupportsSessionGet,
    *,
    course_code: str,
    year: int,
    career: str,
    timeout: float,
    retries: int,
) -> HandbookCourseRecord:
    handbook_url = build_url(year, course_code, career)
    attempts = retries + 1
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = session.get(handbook_url, timeout=timeout)
            response.raise_for_status()
            return extract_course_record_from_html(
                page_html=response.text,
                course_code=course_code,
                year=year,
                handbook_url=handbook_url,
            )
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(min(1.0, 0.5 * (attempt + 1)))

    return HandbookCourseRecord(
        course_code=normalize_course_code(course_code),
        year=year,
        career=career,
        handbook_url=handbook_url,
        course_title="",
        offering_terms="",
        prerequisite="",
        fetch_status="error",
        error_message=str(last_error) if last_error is not None else "unknown error",
    )


def _write_csv(output_path: Path, rows: list[HandbookCourseRecord]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())


def run_import_handbook_command(
    command: ImportHandbookCommand, *, stdout: TextIO, stderr: TextIO
) -> int:
    rows: list[HandbookCourseRecord] = []
    session = _create_session()
    try:
        for index, course_code in enumerate(command.course_codes):
            record = fetch_handbook_record(
                session,
                course_code=course_code,
                year=command.year,
                career=command.career,
                timeout=command.timeout,
                retries=command.retries,
            )
            rows.append(
                HandbookCourseRecord(
                    course_code=record.course_code,
                    year=record.year,
                    career=command.career,
                    handbook_url=record.handbook_url,
                    course_title=record.course_title,
                    offering_terms=record.offering_terms,
                    prerequisite=record.prerequisite,
                    fetch_status=record.fetch_status,
                    error_message=record.error_message,
                )
            )
            if index + 1 < len(command.course_codes) and command.sleep_seconds > 0:
                time.sleep(command.sleep_seconds)
    finally:
        session.close()

    _write_csv(command.output_path, rows)
    failures = [row for row in rows if row.fetch_status != "ok"]
    print(
        f"Wrote {len(rows)} handbook row(s) to {command.output_path}",
        file=stdout,
    )
    if failures:
        print(
            f"{len(failures)} course(s) failed to import; see CSV error_message column.",
            file=stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = ImportHandbookCommand(
        year=args.year,
        career=args.career,
        course_codes=[normalize_course_code(code) for code in args.course_codes],
        output_path=Path(args.output),
        timeout=args.timeout,
        retries=args.retries,
        sleep_seconds=args.sleep,
    )
    return run_import_handbook_command(command, stdout=sys.stdout, stderr=sys.stderr)

"""CLI for adding prerequisite overrides to catalogue_overrides.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, cast

from transitionchecker.core import normalize_course_code
from transitionchecker.prereq_engine import parse_prerequisite_field


_DEFAULT_CATALOGUE = "plans/catalogue.json"
_CAREER_ALIASES = {
    "undergraduate": "Undergraduate",
    "ug": "Undergraduate",
    "ugrad": "Undergraduate",
    "ugrd": "Undergraduate",
    "postgraduate": "Postgraduate",
    "pg": "Postgraduate",
    "pgrad": "Postgraduate",
    "pgrd": "Postgraduate",
}


def _normalize_career(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        return "Undergraduate"
    return _CAREER_ALIASES.get(normalized, value.strip())


def _load_overrides(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        raw: object = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"Overrides file must contain a JSON list: {path}")
    return cast(list[dict[str, Any]], raw)


def _write_overrides(path: Path, overrides: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(overrides, fh, indent=2)
        fh.write("\n")


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add or update a prerequisite override in catalogue_overrides.json. "
            "The override file lives beside the catalogue file and is applied "
            "automatically when the catalogue is loaded."
        ),
        epilog=(
            "Examples:\n"
            "  add-override plans/catalogue.json --course CEIC3000 "
            '--prereq "CEIC2000 AND CEIC2010" --reason "handbook text is ambiguous"\n'
            "  add-override --course CEIC3000 --prereq "
            '"enrollment in program 1234" --reason "unparseable" --force'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "catalogue_file",
        nargs="?",
        default=_DEFAULT_CATALOGUE,
        help=f"Path to catalogue JSON file (default: {_DEFAULT_CATALOGUE})",
    )
    parser.add_argument(
        "--course",
        required=True,
        metavar="COURSE_CODE",
        help="Course code to override (e.g. CEIC3000)",
    )
    parser.add_argument(
        "--career",
        default="Undergraduate",
        metavar="CAREER",
        help=(
            "Career to override (default: Undergraduate). "
            "Aliases UG/UGRAD/UGRD and PG/PGRAD/PGRD are accepted."
        ),
    )
    parser.add_argument(
        "--prereq",
        required=True,
        metavar="TEXT",
        help="Prerequisite text to use instead of the handbook value",
    )
    parser.add_argument(
        "--reason",
        required=True,
        metavar="TEXT",
        help="Reason the override is needed (recorded in the overrides file)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Write the override even if the prerequisite text cannot be parsed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    catalogue_path = Path(args.catalogue_file).resolve()
    overrides_path = catalogue_path.parent / "catalogue_overrides.json"

    course_code = normalize_course_code(str(args.course))
    career = _normalize_career(str(args.career))
    if not course_code:
        print("Error: course code cannot be empty", file=sys.stderr)
        return 2

    prereq_text: str = args.prereq
    reason: str = args.reason

    # Validate that the prereq text parses before committing it.
    _expr, _coreq, parse_error = parse_prerequisite_field(prereq_text)
    if parse_error is not None:
        msg = f"Prerequisite does not parse: {parse_error}"
        if not args.force:
            print(f"Error: {msg}", file=sys.stderr)
            print(
                "Use --force to write the override anyway.",
                file=sys.stderr,
            )
            return 1
        print(f"Warning: {msg} (writing anyway due to --force)", file=sys.stderr)

    try:
        overrides = _load_overrides(overrides_path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error reading overrides file: {exc}", file=sys.stderr)
        return 2

    new_entry = {
        "code": course_code,
        "career": career,
        "prerequisites": prereq_text,
        "reason": reason,
        "date": date.today().isoformat(),
    }

    replaced = False
    for idx, entry in enumerate(overrides):
        if (
            str(entry.get("code", "")).strip().upper() == course_code
            and _normalize_career(str(entry.get("career", ""))) == career
        ):
            overrides[idx] = new_entry
            replaced = True
            break
    if not replaced:
        overrides.append(new_entry)
    overrides.sort(
        key=lambda entry: (
            str(entry.get("code", "")).strip().upper(),
            _normalize_career(str(entry.get("career", ""))),
        )
    )

    try:
        _write_overrides(overrides_path, overrides)
    except OSError as exc:
        print(f"Error writing overrides file: {exc}", file=sys.stderr)
        return 2

    print(f"✓ Override written for {course_code}/{career} → {overrides_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

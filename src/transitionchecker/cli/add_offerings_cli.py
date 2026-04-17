from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict, cast

from transitionchecker.core import (
    canonical_period,
    natural_sort_key,
    normalize_course_code,
    period_display_label,
    period_rank,
)


class NormalizationResult(TypedDict):
    offerings: dict[str, list[str]]
    errors: list[str]


def _period_sort_key(period: str) -> tuple[int, int]:
    """Return natural sort key; raises ValueError if period is unknown."""
    if period_rank(period, fallback=None) is None:
        raise ValueError(f"Unknown teaching period: {period}")
    return natural_sort_key(period)


def _normalize_period(period: str) -> str:
    normalized = canonical_period(period)
    if period_rank(normalized, fallback=None) is None:
        raise ValueError(f"Unknown teaching period: {period}")
    return period_display_label(normalized)


def _normalize_offerings(raw_offerings: dict[str, list[str]]) -> NormalizationResult:
    normalized_offerings: dict[str, list[str]] = {}
    errors: list[str] = []

    for raw_code in sorted(raw_offerings):
        code = normalize_course_code(raw_code)
        if not code:
            errors.append("Encountered empty course code in offerings data")
            continue

        raw_periods = raw_offerings[raw_code]
        normalized_periods: list[str] = []
        for raw_period in raw_periods:
            try:
                normalized_periods.append(_normalize_period(raw_period))
            except ValueError as exc:
                errors.append(f"{code}: {exc}")

        if code in normalized_offerings:
            normalized_periods = normalized_offerings[code] + normalized_periods

        unique_periods = sorted(set(normalized_periods), key=_period_sort_key)
        normalized_offerings[code] = unique_periods

    return {"offerings": normalized_offerings, "errors": errors}


def load_offerings(offerings_file: Path) -> dict[str, list[str]]:
    if not offerings_file.is_file():
        raise FileNotFoundError(f"Offerings file not found: {offerings_file}")

    with open(offerings_file, "r", encoding="utf-8") as fh:
        raw_content: object = json.load(fh)

    if not isinstance(raw_content, dict):
        raise ValueError(
            f"Offerings file must contain a JSON object, got {type(raw_content).__name__}"
        )

    parsed: dict[str, list[str]] = {}
    for raw_code, raw_periods in cast(dict[object, object], raw_content).items():
        if not isinstance(raw_code, str):
            raise ValueError("Offerings file contains a non-string course code")
        if not isinstance(raw_periods, list):
            raise ValueError(f"Course '{raw_code}' must map to a JSON list of periods")

        periods: list[str] = []
        for period in cast(list[object], raw_periods):
            if not isinstance(period, str):
                raise ValueError(
                    f"Course '{raw_code}' has a non-string period entry: {period!r}"
                )
            periods.append(period)

        parsed[raw_code] = periods

    return parsed


def write_offerings(offerings_file: Path, offerings: dict[str, list[str]]) -> None:
    with open(offerings_file, "w", encoding="utf-8") as fh:
        json.dump(offerings, fh, indent=2)
        fh.write("\n")


def _validate_mode(offerings_file: Path) -> int:
    raw_offerings = load_offerings(offerings_file)
    normalized = _normalize_offerings(raw_offerings)

    if normalized["errors"]:
        for error in normalized["errors"]:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    write_offerings(offerings_file, normalized["offerings"])
    print(f"✓ Validated and sorted offerings: {offerings_file}")
    return 0


def _schedule_mode(offerings_file: Path, course_code: str, periods: list[str]) -> int:
    raw_offerings = load_offerings(offerings_file)
    normalized_course = normalize_course_code(course_code)

    if not normalized_course:
        print("Error: Course code cannot be empty", file=sys.stderr)
        return 2

    existing_periods = raw_offerings.get(normalized_course, [])
    raw_offerings[normalized_course] = existing_periods + periods

    normalized = _normalize_offerings(raw_offerings)
    if normalized["errors"]:
        for error in normalized["errors"]:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    write_offerings(offerings_file, normalized["offerings"])

    scheduled_periods = normalized["offerings"][normalized_course]
    print(
        f"✓ Updated {normalized_course} offerings: {', '.join(scheduled_periods)}"
    )
    return 0


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and update offerings.json files. "
            "Use --validate to normalize/sort periods or --schedule to add periods for a course."
        ),
        epilog=(
            "Examples:\n"
            "  add-offerings plans/offerings.json --validate\n"
            "  add-offerings plans/offerings.json --schedule ABCD1234 T1 S1"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("offerings_file", help="Path to offerings JSON file")

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--validate",
        action="store_true",
        help="Validate, canonicalize and sort the offerings file in place",
    )
    mode_group.add_argument(
        "--schedule",
        nargs="+",
        metavar="VALUE",
        help="Add an offering as: --schedule COURSE_CODE PERIOD [PERIOD ...]",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    offerings_file = Path(args.offerings_file).resolve()

    try:
        if args.validate:
            return _validate_mode(offerings_file)

        schedule_values = cast(list[str] | None, args.schedule)
        if not schedule_values or len(schedule_values) < 2:
            parser.error("--schedule requires COURSE_CODE and at least one PERIOD")

        course_code = schedule_values[0]
        periods = schedule_values[1:]
        return _schedule_mode(offerings_file, course_code, periods)

    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

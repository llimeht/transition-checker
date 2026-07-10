from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import TypedDict, cast

from transitionchecker.core import (
    OfferingsCourse,
    OfferingsMap,
    flatten_offerings,
    canonical_period,
    format_offerings_summary,
    load_offerings as load_offer_map,
    natural_sort_key,
    normalize_offerings_course_code,
    period_display_label,
    period_rank,
    write_offerings_csv,
)


class NormalizationResult(TypedDict):
    offerings: OfferingsMap
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


def _normalize_periods(periods: tuple[str, ...] | list[str], code: str) -> tuple[list[str], list[str]]:
    normalized_periods: list[str] = []
    errors: list[str] = []
    for raw_period in periods:
        try:
            normalized_periods.append(_normalize_period(raw_period))
        except ValueError as exc:
            errors.append(f"{code}: {exc}")

    unique_periods = sorted(set(normalized_periods), key=_period_sort_key)
    return unique_periods, errors


def _normalize_offerings(raw_offerings: OfferingsMap) -> NormalizationResult:
    normalized_offerings: OfferingsMap = {}
    errors: list[str] = []

    for raw_code in sorted(raw_offerings):
        code = normalize_offerings_course_code(raw_code)
        if not code:
            errors.append("Encountered empty course code in offerings data")
            continue

        raw_course = raw_offerings[raw_code]
        normalized_all_years, all_year_errors = _normalize_periods(
            raw_course.all_years, code
        )
        errors.extend(all_year_errors)

        normalized_by_year: dict[int, tuple[str, ...]] = {}
        for year in sorted(raw_course.by_year):
            normalized_year_periods, year_errors = _normalize_periods(
                raw_course.by_year[year], code
            )
            errors.extend(year_errors)
            normalized_by_year[year] = tuple(normalized_year_periods)

        normalized_offerings[code] = OfferingsCourse(
            all_years=tuple(normalized_all_years),
            by_year=normalized_by_year,
        )

    return {"offerings": normalized_offerings, "errors": errors}


def load_offerings(offerings_file: Path) -> OfferingsMap:
    return load_offer_map(offerings_file)


def write_offerings(offerings_file: Path, offerings: OfferingsMap) -> None:
    serialized: dict[str, object] = {}
    for code in sorted(offerings):
        course = offerings[code]
        if course.by_year:
            if course.all_years:
                serialized[code] = {
                    "all": list(course.all_years),
                    **{str(year): list(course.by_year[year]) for year in sorted(course.by_year)},
                }
            else:
                serialized[code] = {
                    str(year): list(course.by_year[year]) for year in sorted(course.by_year)
                }
        else:
            serialized[code] = list(course.all_years)

    with open(offerings_file, "w", encoding="utf-8") as fh:
        json.dump(serialized, fh, indent=2)
        fh.write("\n")


def _select_offerings(
    offerings: dict[str, list[str]], course_glob: str
) -> dict[str, list[str]]:
    return {
        course: periods
        for course, periods in offerings.items()
        if fnmatch.fnmatchcase(course, course_glob)
    }


def _select_year_aware_offerings(
    offerings: OfferingsMap, course_glob: str
) -> OfferingsMap:
    return {
        course: periods
        for course, periods in offerings.items()
        if fnmatch.fnmatchcase(course, course_glob)
    }


def _format_year_aware_offerings_summary(offerings: OfferingsMap) -> str:
    lines: list[str] = []
    for course in sorted(offerings):
        entry = offerings[course]
        if entry.all_years:
            lines.append(f"{course:14} all  {' '.join(entry.all_years)}")
        for year in sorted(entry.by_year):
            periods = entry.by_year[year]
            if periods:
                lines.append(f"{course:14} {year} {' '.join(periods)}")
    return "\n".join(lines)


def _show_mode(
    offerings_file: Path,
    course_glob: str,
    output_path: Path | None,
    *,
    show_by_year: bool = False,
) -> int:
    raw_offerings = load_offerings(offerings_file)
    normalized = _normalize_offerings(raw_offerings)

    if normalized["errors"]:
        for error in normalized["errors"]:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    if show_by_year:
        if output_path is not None:
            print(
                "Error: --output is not supported with --show-by-year",
                file=sys.stderr,
            )
            return 2
        matching_year_aware = _select_year_aware_offerings(
            normalized["offerings"], course_glob
        )
        summary_text = _format_year_aware_offerings_summary(matching_year_aware)
        if summary_text:
            print(summary_text)
        else:
            print(f"No matching courses for glob: {course_glob}")
        return 0

    matching_offerings = _select_offerings(
        flatten_offerings(normalized["offerings"]), course_glob
    )
    if output_path is not None:
        write_offerings_csv(matching_offerings, output_path)
        print(f"Wrote CSV: {output_path}", file=sys.stderr)
        return 0

    summary_text = format_offerings_summary(matching_offerings)
    if summary_text:
        print(summary_text)
    else:
        print(f"No matching courses for glob: {course_glob}")

    return 0


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


def _schedule_mode(
    offerings_file: Path,
    course_code: str,
    periods: list[str],
    *,
    year: int | None = None,
) -> int:
    raw_offerings = load_offerings(offerings_file)
    normalized_course = normalize_offerings_course_code(course_code)

    if not normalized_course:
        print("Error: Course code cannot be empty", file=sys.stderr)
        return 2

    existing_course = raw_offerings.get(
        normalized_course,
        OfferingsCourse(all_years=(), by_year={}),
    )
    if year is None:
        raw_offerings[normalized_course] = OfferingsCourse(
            all_years=existing_course.all_years + tuple(periods),
            by_year=existing_course.by_year,
        )
    else:
        updated_by_year = dict(existing_course.by_year)
        updated_by_year[year] = updated_by_year.get(year, ()) + tuple(periods)
        raw_offerings[normalized_course] = OfferingsCourse(
            all_years=existing_course.all_years,
            by_year=updated_by_year,
        )

    normalized = _normalize_offerings(raw_offerings)
    if normalized["errors"]:
        for error in normalized["errors"]:
            print(f"Error: {error}", file=sys.stderr)
        return 1

    write_offerings(offerings_file, normalized["offerings"])

    if year is None:
        scheduled_periods = list(normalized["offerings"][normalized_course].all_years)
        print(f"✓ Updated {normalized_course} offerings: {', '.join(scheduled_periods)}")
    else:
        scheduled_periods = list(
            normalized["offerings"][normalized_course].by_year.get(year, ())
        )
        print(
            f"✓ Updated {normalized_course} {year} offerings: {', '.join(scheduled_periods)}"
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
            "  add-offerings plans/offerings.json --schedule ABCD1234 T1 S1\n"
            "  add-offerings plans/offerings.json --year 2026 --schedule ABCD1234 T1\n"
            "  add-offerings plans/offerings.json --show 'COMP*' --output offerings.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("offerings_file", help="Path to offerings JSON file")
    parser.add_argument(
        "--output",
        help="Write --show results to the given CSV file",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="With --schedule, update offerings for one explicit calendar year instead of the all-years fallback list",
    )
    parser.add_argument(
        "--show-by-year",
        action="store_true",
        help="With --show, display explicit all-years and per-year offerings instead of the merged compatibility view",
    )

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
    mode_group.add_argument(
        "--show",
        metavar="GLOB",
        help="Show offerings for all course codes matching the glob",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    offerings_file = Path(args.offerings_file).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    try:
        if output_path is not None and not args.show:
            parser.error("--output can only be used with --show")
        if args.year is not None and not args.schedule:
            parser.error("--year can only be used with --schedule")
        if args.show_by_year and not args.show:
            parser.error("--show-by-year can only be used with --show")

        if args.validate:
            return _validate_mode(offerings_file)

        if args.show:
            return _show_mode(
                offerings_file,
                cast(str, args.show),
                output_path,
                show_by_year=cast(bool, args.show_by_year),
            )

        schedule_values = cast(list[str] | None, args.schedule)
        if not schedule_values or len(schedule_values) < 2:
            parser.error("--schedule requires COURSE_CODE and at least one PERIOD")

        course_code = schedule_values[0]
        periods = schedule_values[1:]
        return _schedule_mode(
            offerings_file,
            course_code,
            periods,
            year=cast(int | None, args.year),
        )

    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast
import json
import re

from .course_utils import normalize_course_code
from .period_utils import canonical_period, period_display_label


@dataclass(frozen=True)
class OfferingsCourse:
    all_years: tuple[str, ...]
    by_year: dict[int, tuple[str, ...]]


OfferingsMap = dict[str, OfferingsCourse]


def normalize_offerings_course_code(value: str) -> str:
    return re.sub(r"\s+", "", normalize_course_code(value))


def _normalize_periods(raw_periods: list[object], *, context: str) -> tuple[str, ...]:
    periods: list[str] = []
    for raw_period in raw_periods:
        if not isinstance(raw_period, str):
            raise ValueError(f"{context} has a non-string period entry: {raw_period!r}")
        periods.append(period_display_label(canonical_period(raw_period)))
    return tuple(dict.fromkeys(periods))


def _merge_course_entry(
    existing: OfferingsCourse | None,
    *,
    all_years: tuple[str, ...] = (),
    by_year: dict[int, tuple[str, ...]] | None = None,
) -> OfferingsCourse:
    merged_all_years = tuple(dict.fromkeys((existing.all_years if existing else ()) + all_years))
    merged_by_year: dict[int, tuple[str, ...]] = {}

    if existing is not None:
        merged_by_year.update(existing.by_year)
    if by_year is not None:
        for year, periods in by_year.items():
            prior = merged_by_year.get(year, ())
            merged_by_year[year] = tuple(dict.fromkeys(prior + periods))

    return OfferingsCourse(all_years=merged_all_years, by_year=merged_by_year)


def load_offerings(path: Path) -> OfferingsMap:
    if not path.is_file():
        raise FileNotFoundError(f"Offerings file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f"Offerings file must contain a JSON object, got {type(raw).__name__}")

    offerings: OfferingsMap = {}

    for raw_code, raw_value in cast(dict[object, object], raw).items():
        if not isinstance(raw_code, str):
            raise ValueError("Offerings file contains a non-string course code")

        code = normalize_offerings_course_code(raw_code)
        if not code:
            raise ValueError("Encountered empty course code in offerings data")

        if isinstance(raw_value, list):
            course = _merge_course_entry(
                offerings.get(code),
                all_years=_normalize_periods(
                    cast(list[object], raw_value),
                    context=f"Course '{raw_code}'",
                ),
            )
            offerings[code] = course
            continue

        if isinstance(raw_value, dict):
            all_years: tuple[str, ...] = ()
            year_periods: dict[int, tuple[str, ...]] = {}
            for raw_year, raw_periods in cast(dict[object, object], raw_value).items():
                if not isinstance(raw_year, str):
                    raise ValueError(
                        f"Course '{raw_code}' has a non-string year key: {raw_year!r}"
                    )
                if raw_year == "all":
                    if not isinstance(raw_periods, list):
                        raise ValueError(
                            f"Course '{raw_code}' key 'all' must map to a JSON list of periods"
                        )
                    all_years = _normalize_periods(
                        cast(list[object], raw_periods),
                        context=f"Course '{raw_code}' key 'all'",
                    )
                    continue
                try:
                    year = int(raw_year)
                except ValueError as exc:
                    raise ValueError(
                        f"Course '{raw_code}' has an invalid year key: {raw_year!r}"
                    ) from exc
                if not isinstance(raw_periods, list):
                    raise ValueError(
                        f"Course '{raw_code}' year '{raw_year}' must map to a JSON list of periods"
                    )
                year_periods[year] = _normalize_periods(
                    cast(list[object], raw_periods),
                    context=f"Course '{raw_code}' year '{raw_year}'",
                )

            course = _merge_course_entry(
                offerings.get(code),
                all_years=all_years,
                by_year=year_periods,
            )
            offerings[code] = course
            continue

        raise ValueError(
            f"Course '{raw_code}' must map to a JSON list of periods or a year-to-period object"
        )

    return offerings


def flatten_offerings(offerings: OfferingsMap) -> dict[str, list[str]]:
    flattened: dict[str, list[str]] = {}
    for code, course in offerings.items():
        merged = list(course.all_years)
        for year in sorted(course.by_year):
            merged.extend(course.by_year[year])
        flattened[code] = list(dict.fromkeys(merged))
    return flattened


def allowed_periods_for_course(
    offerings: OfferingsMap, course_code: str, *, year: int | None = None
) -> list[str]:
    course = offerings.get(normalize_offerings_course_code(course_code))
    if course is None:
        return []
    if year is not None and year in course.by_year:
        return list(course.by_year[year])
    return list(course.all_years)

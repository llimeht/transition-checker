from __future__ import annotations

import argparse
import csv
import json
import logging
from itertools import combinations
from pathlib import Path
from typing import Any, cast

from transitionchecker.core import (
    as_json_object,
    canonical_period,
    is_placeholder_course,
    normalize_course_code,
)
from transitionchecker.utils.logging import configure_logging


LOGGER = logging.getLogger("cfcc_summary")


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize all 2/3/4-course co-occurrence sets for a specific term "
            "across plans in one directory."
        ),
        epilog=(
            "Examples:\n"
            "  python3 cfcc_summary.py plans/CEIC --year 2026 --period T3\n"
            "  python3 cfcc_summary.py plans/CEIC --year 2026 --period T3 --output /tmp/cfcc.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "plans_dir",
        help="Directory containing plan JSON files (processed non-recursively)",
    )
    parser.add_argument("--year", type=int, required=True, help="Target year (for example: 2026)")
    parser.add_argument(
        "--period",
        required=True,
        help="Target period token: T1, T2, T3, S1, or S2",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output CSV path (default: <plans_dir>/YYYY_TT_CFCCs.csv, "
            "for example plans/CEIC/2026_T3_CFCCs.csv)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for info, -vv for debug)",
    )
    return parser

def parse_target_term(year: int, period: str) -> tuple[int, str, str]:
    if year < 1900 or year > 3000:
        raise ValueError("Invalid year. Provide a four-digit year such as 2026.")

    raw_period = period.strip()
    if not raw_period:
        raise ValueError("Invalid period. Use T1, T2, T3, S1, or S2.")
    canonical = canonical_period(raw_period)

    allowed = {
        "term 1",
        "term 2",
        "term 3",
        "semester 1",
        "semester 2",
    }
    if canonical not in allowed:
        raise ValueError("Unsupported period. Use T1, T2, T3, S1, or S2.")

    period_token_by_canonical = {
        "term 1": "T1",
        "term 2": "T2",
        "term 3": "T3",
        "semester 1": "S1",
        "semester 2": "S2",
    }
    term_slug = f"{year}_{period_token_by_canonical[canonical]}"
    return year, canonical, term_slug


def _extract_sheet_name(plan_data: dict[str, Any], plan_file: Path) -> str:
    sheet = plan_data.get("sheet")
    if isinstance(sheet, str) and sheet.strip():
        return sheet.strip()
    return plan_file.stem


def _matching_codes_for_term(
    plan_data: dict[str, Any],
    target_year: int,
    target_period: str,
) -> list[str]:
    courses_obj = plan_data.get("courses")
    if not isinstance(courses_obj, list):
        return []
    course_items = cast(list[object], courses_obj)

    selected: set[str] = set()
    for entry in course_items:
        entry_obj = as_json_object(entry)
        if entry_obj is None:
            continue

        year_obj = entry_obj.get("year")
        if not isinstance(year_obj, int) or year_obj != target_year:
            continue

        period_obj = entry_obj.get("period")
        if not isinstance(period_obj, str):
            continue
        if canonical_period(period_obj) != target_period:
            continue

        code_obj = entry_obj.get("code")
        if not isinstance(code_obj, str) or not code_obj.strip():
            continue

        normalized_code = normalize_course_code(code_obj)
        if is_placeholder_course(normalized_code):
            continue

        selected.add(normalized_code)

    return sorted(selected)


def _build_rows(plans_dir: Path, target_year: int, target_period: str) -> list[list[str]]:
    rows: list[list[str]] = []

    json_files = sorted(plans_dir.glob("*.json"))
    plan_combo_sets: dict[str, set[tuple[str, ...]]] = {}
    plan_combo_sources: dict[str, dict[tuple[str, ...], set[str]]] = {}

    for plan_file in json_files:
        try:
            with plan_file.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Skipping %s: %s", plan_file, exc)
            continue

        raw_obj = as_json_object(raw)
        if raw_obj is None:
            LOGGER.warning("Skipping %s: top-level JSON is not an object", plan_file)
            continue

        plan_name = _extract_sheet_name(raw_obj, plan_file)
        codes = _matching_codes_for_term(raw_obj, target_year, target_period)

        combos: list[tuple[str, ...]] = []
        for size in (2, 3, 4):
            if len(codes) >= size:
                combos.extend(combinations(codes, size))

        if combos:
            existing = plan_combo_sets.setdefault(plan_name, set())
            existing.update(combos)

            source_map = plan_combo_sources.setdefault(plan_name, {})
            for combo in combos:
                source_map.setdefault(combo, set()).add(plan_file.name)

    for plan_name in sorted(plan_combo_sets):
        combos = sorted(_filter_subset_combos(plan_combo_sets[plan_name]))
        source_map = plan_combo_sources.get(plan_name, {})
        for combo in combos:
            padded = list(combo) + [""] * (4 - len(combo))
            files = ";".join(sorted(source_map.get(combo, set())))
            rows.append([plan_name, *padded, files])

    return rows


def write_csv(output_path: Path, rows: list[list[str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "Plan",
            "Course1",
            "Course2",
            "Course3",
            "Course4",
            "SourceFiles",
        ])
        writer.writerows(rows)


def _filter_subset_combos(combos: set[tuple[str, ...]]) -> set[tuple[str, ...]]:
    """Drop combinations that are strict subsets of a larger combination."""
    ordered_combos = sorted(combos)
    combo_sets = [set(combo) for combo in ordered_combos]
    keep: set[tuple[str, ...]] = set()

    for combo, combo_set in zip(ordered_combos, combo_sets):
        is_subset = False
        for other in combo_sets:
            if len(other) <= len(combo_set):
                continue
            if combo_set.issubset(other):
                is_subset = True
                break
        if not is_subset:
            keep.add(combo)

    return keep


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    plans_dir = Path(args.plans_dir)
    if not plans_dir.is_dir():
        LOGGER.error("Plans directory does not exist: %s", plans_dir)
        return 1

    try:
        target_year, target_period, term_slug = parse_target_term(args.year, args.period)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1

    output_path = Path(args.output) if args.output else plans_dir / f"{term_slug}_CFCCs.csv"
    rows = _build_rows(plans_dir, target_year, target_period)
    write_csv(output_path, rows)

    LOGGER.info("Wrote %d rows to %s", len(rows), output_path)
    return 0

from __future__ import annotations

import csv
from collections.abc import Collection, Mapping
from pathlib import Path


def format_offerings_summary(summary: Mapping[str, Collection[str]]) -> str:
    """Format offerings summary as aligned plain text."""
    lines: list[str] = []
    for course in sorted(summary):
        periods = sorted(summary[course])
        if not periods:
            continue
        lines.append(f"{course:14} {' '.join(periods)}")
    return "\n".join(lines)


def write_offerings_csv(
    summary: Mapping[str, Collection[str]], output_path: Path
) -> Path:
    """Write offerings summary as a course-by-period CSV matrix."""
    all_periods = sorted({period for periods in summary.values() for period in periods})
    columns = ["course", *all_periods]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for course in sorted(summary):
            row = {"course": course}
            for period in all_periods:
                row[period] = "Y" if period in summary[course] else ""
            writer.writerow(row)

    return output_path
from __future__ import annotations

import re


def normalize_course_code(value: str) -> str:
    """Normalize a course code for internal comparisons."""

    return value.strip().upper()


def looks_like_course(value: str) -> bool:
    """Return whether text matches expected course-code format."""

    normalized = normalize_course_code(value)
    return bool(re.fullmatch(r"[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?", normalized))


def is_placeholder_course(code: str) -> bool:
    """Return whether code is a non-course placeholder token."""

    normalized = normalize_course_code(code)
    return normalized.startswith("FREE") or normalized.startswith("GENED")

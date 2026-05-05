"""Shared core helpers for transition checker workflows."""

from .catalogue import (
    available_catalogue_careers,
    Catalogue,
    CatalogueEntry,
    CatalogueKey,
    ensure_catalogue_courses_for_career,
    ensure_catalogue_has_career,
    get_catalogue_entry_for_career,
    normalize_catalogue_career,
    resolve_rules_career,
)
from .course_utils import (
    is_placeholder_course,
    looks_like_course,
    normalize_course_code,
)
from .offerings_output import format_offerings_summary, write_offerings_csv
from .period_utils import (
    canonical_period,
    is_nonstandard_period,
    natural_sort_key,
    period_display_label,
    period_rank,
)
from .validation import as_json_object, as_text

__all__ = [
    "Catalogue",
    "CatalogueEntry",
    "CatalogueKey",
    "available_catalogue_careers",
    "canonical_period",
    "ensure_catalogue_courses_for_career",
    "ensure_catalogue_has_career",
    "format_offerings_summary",
    "get_catalogue_entry_for_career",
    "is_nonstandard_period",
    "natural_sort_key",
    "normalize_catalogue_career",
    "period_display_label",
    "is_placeholder_course",
    "looks_like_course",
    "normalize_course_code",
    "period_rank",
    "resolve_rules_career",
    "as_json_object",
    "as_text",
    "write_offerings_csv",
]

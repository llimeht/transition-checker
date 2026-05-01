from __future__ import annotations

import csv
import json
import logging
import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TextIO, TypedDict, cast

from transitionchecker.rules_engine import (
    RuleExpr,
    ScheduledPlanCourse,
    evaluate_expression,
    evaluate_required,
    validate_scheduled_prerequisites,
    validate_rules_config,
)
from transitionchecker.prereq_engine import parse_prerequisite_field
from transitionchecker.core import (
    Catalogue,
    CatalogueEntry,
    CatalogueKey,
    canonical_period as _canonical_period,
    ensure_catalogue_courses_for_career,
    ensure_catalogue_has_career,
    is_nonstandard_period as _is_nonstandard_period,
    is_placeholder_course as _is_placeholder_course,
    looks_like_course as _looks_like_course,
    normalize_catalogue_career,
    normalize_course_code as _normalize_course_code,
    period_rank as _period_rank,
    resolve_rules_career,
)


CourseCode = str
ASSIGNMENT_INSTANCE_SEPARATOR = "#"


class TemplatePeriod(TypedDict):
    """One schedulable teaching period inside a template year."""

    period: str
    max_slots: int


class TemplateYear(TypedDict):
    """Template definition for one year of an intake plan."""

    enrol_year: str
    year: int
    periods: list[TemplatePeriod]


class IntakeTemplate(TypedDict):
    """Top-level schedule template for one intake key."""

    years: list[TemplateYear]


class TemplateConfig(TypedDict):
    """Full template configuration file keyed by intake string."""

    intakes: dict[str, IntakeTemplate]


class CourseHint(TypedDict, total=False):
    """Optional soft steering hints for placing one course."""

    preferred_period: str
    preferred_year_number: int
    hint_weight: float


class BranchPreference(TypedDict, total=False):
    """Soft preference for choosing one branch of a requirement expression."""

    courses: list[str]
    weight: float


@dataclass(frozen=True)
class SoftPrecedenceRule:
    """Soft ordering preference between two courses in the final plan."""

    before: str
    after: str
    weight: float


@dataclass(frozen=True)
class Slot:
    """Concrete schedulable slot derived from the intake template."""

    slot_idx: int
    enrol_year: str
    year_number: int
    calendar_year: int
    period: str
    canonical_period: str
    max_slots: int


@dataclass(frozen=True)
class PlanEntry:
    code: str
    slot_idx: int


@dataclass
class CostConfig:
    """Penalty weights used by the planner objective function.

    Higher values make the corresponding condition more expensive and therefore
    less likely to appear in accepted plans.
    """

    offering_violation: float = 1000.0
    prerequisite_violation: float = 1000.0
    required_clause_violation: float = 1000.0
    unplaced_course: float = 5000.0
    slot_overload: float = 500.0
    summer_term_penalty: float = 20.0
    winter_term_penalty: float = 20.0
    uoc_imbalance: float = 3.0
    slot_delay: float = 15.0
    used_slot_penalty: float = 40.0
    placeholder_same_period_penalty: float = 150.0
    implicit_year_hint_weight: float = 8.0
    fixed_constraint_violation: float = 2000.0
    post_target_period_penalty: float = 1000.0


@dataclass
class CostDetails:
    """Expanded objective breakdown for one candidate plan."""

    total_cost: float
    offering_violations: int
    prereq_violations: int
    required_failures: int
    unplaced_count: int
    overload_count: int
    summer_count: int
    winter_count: int
    uoc_stddev: float
    hint_penalty: float
    soft_precedence_penalty: float
    soft_precedence_violations: int
    placeholder_overlap_count: int
    slot_delay_total: int
    used_slot_count: int
    fixed_constraint_violations: int
    post_target_period_count: int


@dataclass(frozen=True)
class PartialPlanCourseRecord:
    """One fixed course row extracted from an existing extract-plans plan file."""

    code: str
    year: int | None
    enrol_year: str | None
    period: str
    course_n: str | None


@dataclass(frozen=True)
class FixedConstraints:
    """Hard placement constraints derived from a partial plan file."""

    fixed_assignments: dict[str, int] = field(default_factory=lambda: {})
    locked_slots: set[int] = field(default_factory=lambda: set())
    allowed_codes_by_slot: dict[int, set[str]] = field(default_factory=lambda: {})
    diagnostics: list[str] = field(default_factory=lambda: [])


@dataclass
class SearchConfig:
    """Search controls for annealing restarts and move exploration."""

    restarts: int = 10
    iterations: int = 2000
    ruin_fraction: float = 0.30
    t_start: float = 40.0
    t_end: float = 0.1
    patience: int | None = None


@dataclass(frozen=True)
class BaselineConfig:
    """Greedy seeding profile used to diversify restart baselines."""

    name: str = "balanced"
    hint_factor: float = 1.0
    placeholder_factor: float = 1.0
    nonstandard_factor: float = 1.0
    slot_delay_factor: float = 0.05
    score_jitter: float = 0.0
    course_rank_jitter: float = 0.0
    top_slot_pool: int = 1


@dataclass
class SteeringConfig:
    """User-provided planning preferences loaded from steering JSON."""

    cost: CostConfig = field(default_factory=CostConfig)
    course_hints: dict[str, CourseHint] = field(default_factory=lambda: {})
    soft_precedence_rules: list[SoftPrecedenceRule] = field(default_factory=lambda: [])
    branch_preferences: list[BranchPreference] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class PlannerCommand:
    """Inputs for one planner run."""

    rule_path: Path
    intake: str
    offerings_path: Path
    catalogue_path: Path
    template_config_path: Path
    steering_path: Path
    target_end: str | None = None
    partial_plan_path: Path | None = None
    num_solutions: int = 5
    restarts: int = 10
    iterations: int = 2000
    patience: int | None = None
    ruin_fraction: float = 0.30
    seed: int = 1337
    career: str | None = None
    no_placeholders: bool = False
    show_nonstandard_periods: bool = False
    output_path: Path | None = None
    verbose: int = 0


LOGGER = logging.getLogger("map_maker")


def normalize_course_code(value: str) -> str:
    """Normalize a course code for internal comparisons."""

    return _normalize_course_code(value)


def assignment_course_code(value: str) -> str:
    """Return the visible course code for one internal assignment id."""

    base, _sep, _suffix = value.partition(ASSIGNMENT_INSTANCE_SEPARATOR)
    return normalize_course_code(base)


def next_assignment_id(code: str, counts: Counter[str]) -> str:
    """Return a unique internal assignment id for one selected course instance."""

    base_code = assignment_course_code(code)
    counts[base_code] += 1
    occurrence = counts[base_code]
    if occurrence == 1:
        return base_code
    return f"{base_code}{ASSIGNMENT_INSTANCE_SEPARATOR}{occurrence}"


def canonical_period(period: str) -> str:
    """Canonicalize period aliases so offerings/templates compare consistently."""

    return _canonical_period(period)


def period_rank(period: str, fallback: int | None = 999) -> int | None:
    return _period_rank(period, fallback)


def is_nonstandard_period(period: str) -> bool:
    return _is_nonstandard_period(period)


def slot_preference_key(slot: Slot) -> tuple[int, int]:
    return (1 if is_nonstandard_period(slot.canonical_period) else 0, slot.slot_idx)


def looks_like_course(value: str) -> bool:
    return _looks_like_course(value)


def level_rank(level: str | None) -> int:
    if not level:
        return 0
    match = re.search(r"(\d+)", level)
    if not match:
        return 0
    return int(match.group(1))


def course_numeric_level(code: str) -> int:
    match = re.search(r"(\d)", assignment_course_code(code))
    if not match:
        return 9
    return int(match.group(1))


def implicit_preferred_year_number(code: str) -> int | None:
    level = course_numeric_level(code)
    if level >= 9:
        return 5
    if level <= 1:
        return 1
    if level == 2:
        return 2
    if level == 3:
        return 3
    return 4


def is_placeholder_course(code: str) -> bool:
    return _is_placeholder_course(assignment_course_code(code))


def read_json(path: Path) -> Any:
    """Read and decode one JSON file."""

    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_rules(path: Path) -> dict[str, Any]:
    """Load and validate canonical degree rules from disk."""

    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"Rules file must contain an object: {path}")
    return validate_rules_config(cast(dict[str, Any], raw))


# Keys that are metadata-only in the overrides file and must not be merged
# into catalogue entries.
_OVERRIDE_METADATA_KEYS: frozenset[str] = frozenset({"reason", "date"})


def load_catalogue_overrides(path: Path) -> dict[CatalogueKey, dict[str, Any]]:
    """Load catalogue overrides from *path*, returning ``{}`` if the file is absent.

    The file uses the same top-level list-of-entry shape as ``catalogue.json``.
    Each entry must include ``code`` and ``career`` plus whichever catalogue
    fields it overrides (e.g. ``prerequisites``). Optional metadata keys such as
    ``reason`` and ``date`` are ignored during merging.
    """
    if not path.exists():
        return {}
    raw = read_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"Catalogue overrides file must contain a list: {path}")
    result: dict[CatalogueKey, dict[str, Any]] = {}
    for item_obj in cast(list[object], raw):
        if not isinstance(item_obj, dict):
            continue
        item = cast(dict[str, Any], item_obj)
        code_raw = item.get("code")
        career_raw = item.get("career")
        if not isinstance(code_raw, str) or not isinstance(career_raw, str):
            continue
        key = CatalogueKey(normalize_course_code(code_raw.strip()), career_raw.strip())
        result[key] = item
    return result


def apply_catalogue_overrides(
    catalogue: Catalogue,
    overrides: dict[CatalogueKey, dict[str, Any]],
) -> Catalogue:
    """Return a new Catalogue with *overrides* merged in last-loaded-wins order.

    Only non-metadata keys (i.e. not ``reason`` or ``date``) from each override
    entry are applied. Override-only entries are added to the resulting
    catalogue. The original catalogue is not mutated.
    """
    if not overrides:
        return catalogue

    def build_entry(
        key: CatalogueKey,
        fields: dict[str, Any],
    ) -> CatalogueEntry:
        return CatalogueEntry(
            code=_normalize_course_code(str(fields.get("code") or key.code)),
            title=str(fields.get("title") or ""),
            career=str(fields.get("career") or key.career),
            uoc=int(fields["uoc"]) if fields.get("uoc") is not None else 6,
            prerequisites=(
                str(fields["prerequisites"])
                if fields.get("prerequisites") is not None
                else ""
            ),
            level=(str(fields["level"]) if fields.get("level") is not None else None),
        )

    new_entries: list[CatalogueEntry] = []
    seen_keys: set[CatalogueKey] = set()
    for entry in catalogue.values():
        seen_keys.add(entry.key)
        override = overrides.get(entry.key)
        if override is None:
            new_entries.append(entry)
            continue
        fields: dict[str, Any] = {
            "code": entry.code,
            "title": entry.title,
            "career": entry.career,
            "uoc": entry.uoc,
            "prerequisites": entry.prerequisites,
            "level": entry.level,
        }
        for k, v in override.items():
            if k not in _OVERRIDE_METADATA_KEYS and k in fields:
                fields[k] = v
        new_entries.append(build_entry(entry.key, fields))

    for key, override in overrides.items():
        if key in seen_keys:
            continue
        fields = {
            field_name: value
            for field_name, value in override.items()
            if field_name not in _OVERRIDE_METADATA_KEYS
        }
        new_entries.append(build_entry(key, fields))

    return Catalogue(new_entries)


def _ordered_catalogue_override_paths(
    catalogue_path: Path,
    extra_override_paths: Iterable[Path],
) -> list[Path]:
    """Return unique override paths in last-loaded-wins application order."""

    ordered_paths = [catalogue_path.parent / "catalogue_overrides.json"]
    ordered_paths.extend(extra_override_paths)

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in ordered_paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(path)
    return unique_paths


def load_catalogue(
    path: Path,
    *,
    apply_overrides: bool = True,
    override_paths: Iterable[Path] = (),
) -> Catalogue:
    """Load the course catalogue used for UoC, level, and prerequisite data.

    When *apply_overrides* is ``True`` (the default), a sibling file named
    ``catalogue_overrides.json`` is automatically discovered and merged in.
    Any additional *override_paths* are then applied in the order provided,
    so later files win when the same ``(code, career)`` entry appears more
    than once. The linter passes ``apply_overrides=False`` so that it sees the
    raw handbook text.
    """

    raw = read_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"Catalogue file must contain a list: {path}")

    catalogue = Catalogue.from_list(cast(list[object], raw))

    if apply_overrides:
        merged_overrides: dict[CatalogueKey, dict[str, Any]] = {}
        for override_path in _ordered_catalogue_override_paths(path, override_paths):
            merged_overrides.update(load_catalogue_overrides(override_path))
        catalogue = apply_catalogue_overrides(catalogue, merged_overrides)

    return catalogue


def load_offerings(path: Path) -> dict[CourseCode, list[str]]:
    """Load period offerings as a normalized course-to-periods mapping."""

    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"Offerings file must contain an object: {path}")

    offerings: dict[CourseCode, list[str]] = {}
    for code, periods_raw in cast(dict[object, object], raw).items():
        if not isinstance(code, str) or not isinstance(periods_raw, list):
            continue
        periods = [p for p in cast(list[object], periods_raw) if isinstance(p, str)]
        offerings[normalize_course_code(code)] = periods
    return offerings


def load_templates(path: Path) -> TemplateConfig:
    """Load the intake template configuration."""

    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"Template config must contain an object: {path}")
    return cast(TemplateConfig, raw)


def extract_partial_plan_courses(
    plan_data: dict[str, Any],
) -> list[PartialPlanCourseRecord]:
    """Extract fixed course rows from an existing plan JSON document."""

    courses_raw = plan_data.get("courses")
    if not isinstance(courses_raw, list):
        return []

    extracted: list[PartialPlanCourseRecord] = []
    for item in cast(list[object], courses_raw):
        if not isinstance(item, dict):
            continue
        row = cast(dict[str, object], item)

        code_raw = row.get("code")
        if not isinstance(code_raw, str) or not code_raw.strip():
            continue

        period_raw = row.get("period")
        if not isinstance(period_raw, str) or not period_raw.strip():
            continue

        year_raw = row.get("year")
        year: int | None = year_raw if isinstance(year_raw, int) else None

        enrol_year_raw = row.get("enrol_year")
        enrol_year = enrol_year_raw if isinstance(enrol_year_raw, str) else None

        course_n_raw = row.get("course_n")
        course_n = course_n_raw if isinstance(course_n_raw, str) else None

        extracted.append(
            PartialPlanCourseRecord(
                code=normalize_course_code(code_raw),
                year=year,
                enrol_year=enrol_year,
                period=period_raw,
                course_n=course_n,
            )
        )

    return extracted


def _resolve_partial_row_slot(
    row: PartialPlanCourseRecord,
    slots: list[Slot],
) -> int | None:
    """Resolve one partial-plan row to a unique template slot index."""

    row_period = canonical_period(row.period)
    period_matches = [slot for slot in slots if slot.canonical_period == row_period]
    if not period_matches:
        return None

    scoped = period_matches
    if row.year is not None:
        year_matches = [slot for slot in scoped if slot.calendar_year == row.year]
        if year_matches:
            scoped = year_matches

    if row.enrol_year is not None:
        enrol_matches = [slot for slot in scoped if slot.enrol_year == row.enrol_year]
        if enrol_matches:
            scoped = enrol_matches

    if len(scoped) == 1:
        return scoped[0].slot_idx
    return None


def derive_fixed_constraints(
    partial_plan_courses: list[PartialPlanCourseRecord],
    slots: list[Slot],
    required_courses: Iterable[str] | None = None,
) -> FixedConstraints:
    """Build immutable course placements and locked-empty periods.

    A period becomes locked when at least one fixed row exists for it. In locked
    periods, only explicitly fixed courses can be placed; all remaining capacity
    is treated as intentionally empty.
    """

    diagnostics: list[str] = []
    fixed_assignments: dict[str, int] = {}
    locked_slots: set[int] = set()
    remaining_required_instances: dict[str, list[str]] = {}
    if required_courses is not None:
        for required_code in required_courses:
            remaining_required_instances.setdefault(
                assignment_course_code(required_code), []
            ).append(required_code)

    for row in partial_plan_courses:
        slot_idx = _resolve_partial_row_slot(row, slots)
        if slot_idx is None:
            diagnostics.append(
                (
                    "partial plan row could not be matched to one template slot: "
                    f"code={row.code}, period={row.period}, year={row.year}, enrol_year={row.enrol_year}"
                )
            )
            continue

        locked_slots.add(slot_idx)

        resolved_code = row.code
        if required_courses is not None:
            matching_instances = remaining_required_instances.get(row.code, [])
            if not matching_instances:
                diagnostics.append(
                    f"fixed course {row.code} is not part of selected required courses; ignoring fixed row"
                )
                continue
            resolved_code = matching_instances.pop(0)

        existing = fixed_assignments.get(resolved_code)
        if existing is not None and existing != slot_idx:
            if required_courses is None:
                # The early preselection pass runs before repeated placeholder
                # instances have been expanded into distinct assignment ids.
                # Defer duplicate-slot diagnostics to the later instance-aware
                # fixed-constraint build.
                continue
            diagnostics.append(
                (
                    f"fixed course {row.code} appears in multiple slots "
                    f"({existing} and {slot_idx}); keeping first occurrence"
                )
            )
            continue
        fixed_assignments[resolved_code] = slot_idx

    by_slot: dict[int, set[str]] = {}
    for code, slot_idx in fixed_assignments.items():
        by_slot.setdefault(slot_idx, set()).add(code)

    return FixedConstraints(
        fixed_assignments=fixed_assignments,
        locked_slots=locked_slots,
        allowed_codes_by_slot=by_slot,
        diagnostics=diagnostics,
    )


def load_steering(path: Path) -> SteeringConfig:
    """Load optional steering configuration.

    Missing or malformed steering files degrade to defaults rather than blocking
    plan generation.
    """

    if not path.exists():
        return SteeringConfig()

    raw = read_json(path)
    if not isinstance(raw, dict):
        return SteeringConfig()

    payload = cast(dict[str, Any], raw)
    weights = cast(object, payload.get("weights"))
    hints = cast(object, payload.get("course_hints"))
    soft_precedence_raw = cast(object, payload.get("soft_precedence_rules"))

    cost = CostConfig()
    if isinstance(weights, dict):
        weights_dict = cast(dict[str, object], weights)
        for field_name in (
            "offering_violation",
            "prerequisite_violation",
            "required_clause_violation",
            "unplaced_course",
            "slot_overload",
            "summer_term_penalty",
            "winter_term_penalty",
            "uoc_imbalance",
            "slot_delay",
            "used_slot_penalty",
            "placeholder_same_period_penalty",
            "implicit_year_hint_weight",
            "fixed_constraint_violation",
            "post_target_period_penalty",
        ):
            value = weights_dict.get(field_name)
            if isinstance(value, (int, float)):
                setattr(cost, field_name, float(value))

    course_hints: dict[str, CourseHint] = {}
    if isinstance(hints, dict):
        for code, hint_raw in cast(dict[object, object], hints).items():
            if not isinstance(code, str) or not isinstance(hint_raw, dict):
                continue
            normalized_code = normalize_course_code(code)
            hint_dict = cast(dict[str, Any], hint_raw)
            hint: CourseHint = {}
            period = hint_dict.get("preferred_period")
            year_num = hint_dict.get("preferred_year_number")
            hint_weight = hint_dict.get("hint_weight")
            if isinstance(period, str):
                hint["preferred_period"] = period
            if isinstance(year_num, int):
                hint["preferred_year_number"] = year_num
            if isinstance(hint_weight, (int, float)):
                hint["hint_weight"] = float(hint_weight)
            course_hints[normalized_code] = hint

    soft_precedence_rules: list[SoftPrecedenceRule] = []
    if isinstance(soft_precedence_raw, list):
        for item in cast(list[object], soft_precedence_raw):
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, object], item)
            before = item_dict.get("before")
            after = item_dict.get("after")
            weight = item_dict.get("weight", 100.0)
            if not isinstance(before, str) or not isinstance(after, str):
                continue
            if not isinstance(weight, (int, float)):
                continue
            soft_precedence_rules.append(
                SoftPrecedenceRule(
                    before=normalize_course_code(before),
                    after=normalize_course_code(after),
                    weight=float(weight),
                )
            )

    branch_preferences: list[BranchPreference] = []
    branch_preferences_raw = cast(object, payload.get("branch_preferences"))
    if isinstance(branch_preferences_raw, list):
        for item in cast(list[object], branch_preferences_raw):
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, object], item)
            courses_raw = item_dict.get("courses")
            weight = item_dict.get("weight", 0.0)
            if not isinstance(courses_raw, list) or not isinstance(
                weight, (int, float)
            ):
                continue
            normalized_courses = [
                normalize_course_code(c)
                for c in cast(list[object], courses_raw)
                if isinstance(c, str)
            ]
            if normalized_courses:
                pref: BranchPreference = {
                    "courses": normalized_courses,
                    "weight": float(weight),
                }
                branch_preferences.append(pref)

    return SteeringConfig(
        cost=cost,
        course_hints=course_hints,
        soft_precedence_rules=soft_precedence_rules,
        branch_preferences=branch_preferences,
    )


def build_slots(templates: TemplateConfig, intake: str) -> list[Slot]:
    """Expand one intake template into an ordered list of concrete slots."""

    intakes = templates["intakes"]

    intake_key = resolve_intake_key(intakes, intake)
    intake_cfg = intakes[intake_key]
    years = intake_cfg["years"]

    slots: list[Slot] = []
    slot_idx = 0
    for year_number, yobj in enumerate(years, start=1):
        enrol_year = yobj["enrol_year"]
        calendar_year = yobj["year"]
        periods = yobj["periods"]
        for pobj in periods:
            period = pobj["period"]
            max_slots = pobj["max_slots"]
            slots.append(
                Slot(
                    slot_idx=slot_idx,
                    enrol_year=enrol_year,
                    year_number=year_number,
                    calendar_year=calendar_year,
                    period=period,
                    canonical_period=canonical_period(period),
                    max_slots=max_slots,
                )
            )
            slot_idx += 1

    if not slots:
        raise ValueError(f"Intake '{intake}' does not contain any schedulable slots")

    return slots


def parse_year_period_value(value: str, *, label: str) -> tuple[int, str]:
    """Parse a value in YYYY period form and canonicalize its period.

    Examples: "2028 S1", "2027 Term 3", "2026 t1".
    """

    parts = value.strip().split()
    if len(parts) < 2:
        raise ValueError(
            f"{label} '{value}' is invalid. Expected format: 'YYYY pp' (e.g., '2028 S1')."
        )

    year_str = parts[0]
    period_str = " ".join(parts[1:])

    try:
        year = int(year_str)
    except ValueError as exc:
        raise ValueError(
            f"{label} year '{year_str}' is not a valid integer. Expected format: 'YYYY pp'."
        ) from exc

    return year, canonical_period(period_str)


def _compact_period_label(canonical: str) -> str:
    mapping = {
        "term 1": "T1",
        "term 2": "T2",
        "term 3": "T3",
        "semester 1": "S1",
        "semester 2": "S2",
        "summer term": "Summer",
        "winter term": "Winter",
    }
    return mapping.get(canonical, canonical.title())


def _format_year_period(year: int, canonical: str) -> str:
    return f"{year} {_compact_period_label(canonical)}"


def _available_intake_labels(intakes: dict[str, IntakeTemplate]) -> str:
    labels: list[str] = []
    for key in intakes:
        try:
            year, canonical = parse_year_period_value(key, label="Intake")
        except ValueError:
            labels.append(key)
            continue
        labels.append(_format_year_period(year, canonical))
    return ", ".join(sorted(set(labels)))


def resolve_intake_key(intakes: dict[str, IntakeTemplate], intake: str) -> str:
    """Resolve user intake input to a concrete template key.

    Supports normalized "YYYY pp" aliases (e.g., "2026 t1" -> "2026 Term 1").
    """

    if intake in intakes:
        return intake

    try:
        requested_year_period = parse_year_period_value(intake, label="Intake")
    except ValueError:
        available = _available_intake_labels(intakes)
        raise ValueError(
            f"Intake '{intake}' not found in template config. Available: {available}"
        )

    matches: list[str] = []
    for key in intakes:
        try:
            key_year_period = parse_year_period_value(key, label="Intake")
        except ValueError:
            continue
        if key_year_period == requested_year_period:
            matches.append(key)

    if len(matches) == 1:
        return matches[0]

    available = _available_intake_labels(intakes)
    if len(matches) > 1:
        raise ValueError(
            (
                f"Intake '{intake}' is ambiguous after normalization. "
                f"Available: {available}"
            )
        )
    raise ValueError(
        f"Intake '{intake}' not found in template config. Available: {available}"
    )


def _available_target_labels(slots: list[Slot]) -> str:
    unique_targets = sorted(
        {(slot.calendar_year, slot.canonical_period) for slot in slots},
        key=lambda item: (item[0], period_rank(item[1], fallback=999) or 999, item[1]),
    )
    return ", ".join(
        _format_year_period(year, canonical) for year, canonical in unique_targets
    )


def resolve_target_end_slot(slots: list[Slot], target_end: str) -> int:
    """Resolve target end intake-style value to exact slot index.

    Args:
        slots: All slots in the expanded intake template.
        target_end: Intake-style string like '2028 S1' (YYYY period).

    Returns:
        Exact slot_idx matching the specified calendar year and canonical period.

    Raises:
        ValueError: If target_end format is invalid or no matching slot exists.
    """
    try:
        target_year, canonical_target = parse_year_period_value(
            target_end, label="Target end"
        )
    except ValueError as exc:
        available = _available_target_labels(slots)
        raise ValueError((f"{exc} Available targets: {available}")) from exc

    matching = [
        slot
        for slot in slots
        if slot.calendar_year == target_year
        and slot.canonical_period == canonical_target
    ]

    if not matching:
        available = _available_target_labels(slots)
        raise ValueError(
            (
                f"Target end '{target_end}' does not exist in intake template. "
                f"Available targets: {available}"
            )
        )

    if len(matching) > 1:
        raise ValueError(
            f"Target end '{target_end}' matched multiple slots; this should not occur."
        )

    return matching[0].slot_idx


def extract_expr_courses(expr: RuleExpr) -> set[str]:
    """Extract concrete course codes referenced anywhere inside an expression."""

    if isinstance(expr, str):
        code = normalize_course_code(expr)
        return {code} if looks_like_course(code) else set()

    if set(expr.keys()) == {"min", "from"}:
        courses: set[str] = set()
        for child in cast(list[RuleExpr], expr["from"]):
            courses.update(extract_expr_courses(child))
        return courses

    if len(expr) == 1:
        op = next(iter(expr.keys()))
        if op in {"and", "or"}:
            courses = set()
            for child in cast(list[RuleExpr], expr[op]):
                courses.update(extract_expr_courses(child))
            return courses

    return set()


def extract_required_rule_courses(rules: dict[str, Any]) -> set[str]:
    """Return all concrete course codes referenced by the rules file."""

    required = rules.get("required")
    if not isinstance(required, dict):
        return set()

    courses: set[str] = set()
    for clauses in cast(dict[str, Any], required).values():
        if not isinstance(clauses, list):
            continue
        for clause in cast(list[RuleExpr], clauses):
            courses.update(extract_expr_courses(clause))
    return courses


def estimate_expr_cost(
    expr: RuleExpr,
    feasible_counts: dict[str, int],
    catalogue: Catalogue,
    career: str,
    branch_preferences: list[BranchPreference] | None = None,
) -> tuple[float, set[str]]:
    """Estimate the cheapest concrete course set satisfying one rule expression.

    This is used only during required-course selection, before the actual search
    starts. It is intentionally heuristic: it favors feasible and lightly
    constrained branches rather than guaranteeing a globally optimal rule choice.
    """

    if branch_preferences is None:
        branch_preferences = []

    if isinstance(expr, str):
        code = normalize_course_code(expr)
        if not looks_like_course(code):
            return 99999.0, set()
        count = feasible_counts.get(code, 0)
        level_bias = 0.0
        entry = catalogue.get(CatalogueKey(code, career))
        if entry is not None:
            level_bias = -0.01 * level_rank(entry.level)
        penalty = 1000.0 if count == 0 else 1.0 / float(count)
        return penalty + level_bias, {code}

    if set(expr.keys()) == {"min", "from"} or set(expr.keys()) == {
        "min",
        "from",
        "placeholder",
    }:
        min_count = cast(int, expr["min"])
        options = cast(list[RuleExpr], expr["from"])
        placeholder = expr.get("placeholder")
        if isinstance(placeholder, str) and placeholder.strip():
            code = normalize_course_code(placeholder)
            count = feasible_counts.get(code, 0)
            level_bias = 0.0
            entry = catalogue.get(CatalogueKey(code, career))
            if entry is not None:
                level_bias = -0.01 * level_rank(entry.level)
            penalty = 1000.0 if count == 0 else 1.0 / float(count)
            return (penalty * float(min_count)) + level_bias, {code}
        evaluated = [
            estimate_expr_cost(
                option, feasible_counts, catalogue, career, branch_preferences
            )
            for option in options
        ]
        evaluated.sort(key=lambda item: item[0])
        # For min/from clauses, greedily keep the cheapest satisfiable options.
        selected = evaluated[:min_count]
        total_cost = sum(item[0] for item in selected)
        picked: set[str] = set()
        for _, courses in selected:
            picked.update(courses)
        return total_cost, picked

    if len(expr) == 1:
        op = next(iter(expr.keys()))
        children = cast(list[RuleExpr], expr[op])
        child_eval = [
            estimate_expr_cost(
                child, feasible_counts, catalogue, career, branch_preferences
            )
            for child in children
        ]
        if op == "and":
            total = sum(item[0] for item in child_eval)
            and_picked: set[str] = set()
            for _, courses in child_eval:
                and_picked.update(courses)
            return total, and_picked
        if op == "or":
            # Extract courses from each OR option and apply branch preferences
            options_with_courses: list[tuple[float, float, set[str]]] = []
            for cost, courses in child_eval:
                adjusted_cost = cost
                # Check if any branch preference matches this option
                for pref in branch_preferences:
                    pref_courses = pref["courses"] if "courses" in pref else []
                    if (
                        pref_courses
                        and courses
                        and all(c in courses for c in pref_courses)
                    ):
                        # This option matches the preference; apply weight adjustment
                        weight = pref["weight"] if "weight" in pref else 0.0
                        adjusted_cost += weight
                        break
                options_with_courses.append((adjusted_cost, cost, courses))
            # Use the adjusted score only for branch choice; keep the underlying
            # branch cost unchanged so the rest of the planner sees consistent costs.
            best = min(options_with_courses, key=lambda item: item[0])
            return best[1], best[2]

    return 99999.0, set()


def resolve_selected_courses(
    expr: RuleExpr,
    feasible_counts: dict[str, int],
    catalogue: Catalogue,
    career: str,
    branch_preferences: list[BranchPreference] | None = None,
    prefer_placeholders: bool = True,
    selection_counts: Counter[str] | None = None,
    fixed_course_codes: set[str] | None = None,
) -> list[str]:
    """Resolve one rule expression into selected planner assignment ids."""

    if branch_preferences is None:
        branch_preferences = []
    if selection_counts is None:
        selection_counts = Counter()
    if fixed_course_codes is None:
        fixed_course_codes = set()

    if isinstance(expr, str):
        code = normalize_course_code(expr)
        if not looks_like_course(code):
            return []
        return [next_assignment_id(code, selection_counts)]

    if set(expr.keys()) == {"min", "from"} or set(expr.keys()) == {
        "min",
        "from",
        "placeholder",
    }:
        min_count = cast(int, expr["min"])
        placeholder = expr.get("placeholder")
        if prefer_placeholders and isinstance(placeholder, str) and placeholder.strip():
            return [
                next_assignment_id(placeholder, selection_counts)
                for _ in range(min_count)
            ]

        options = cast(list[RuleExpr], expr["from"])
        evaluated = [
            (
                estimate_expr_cost(
                    option,
                    feasible_counts,
                    catalogue,
                    career,
                    branch_preferences,
                )[0],
                option,
            )
            for option in options
        ]
        evaluated.sort(key=lambda item: item[0])
        selected_courses: list[str] = []
        for _cost, option in evaluated[:min_count]:
            selected_courses.extend(
                resolve_selected_courses(
                    option,
                    feasible_counts,
                    catalogue,
                    career,
                    branch_preferences,
                    prefer_placeholders,
                    selection_counts,
                    fixed_course_codes,
                )
            )
        return selected_courses

    if len(expr) == 1:
        op = next(iter(expr.keys()))
        children = cast(list[RuleExpr], expr[op])
        if op == "and":
            selected: list[str] = []
            for child in children:
                selected.extend(
                    resolve_selected_courses(
                        child,
                        feasible_counts,
                        catalogue,
                        career,
                        branch_preferences,
                        prefer_placeholders,
                        selection_counts,
                        fixed_course_codes,
                    )
                )
            return selected
        if op == "or":
            child_matches: list[tuple[int, float, RuleExpr]] = []
            for child in children:
                overlap = len(extract_expr_courses(child) & fixed_course_codes)
                cost = estimate_expr_cost(
                    child,
                    feasible_counts,
                    catalogue,
                    career,
                    branch_preferences,
                )[0]
                child_matches.append((overlap, cost, child))

            best_overlap = max((item[0] for item in child_matches), default=0)
            if best_overlap > 0:
                candidate_children = [
                    (cost, child)
                    for overlap, cost, child in child_matches
                    if overlap == best_overlap
                ]
                chosen = min(candidate_children, key=lambda item: item[0])[1]
            else:
                chosen = min(child_matches, key=lambda item: item[1])[2]
            return resolve_selected_courses(
                chosen,
                feasible_counts,
                catalogue,
                career,
                branch_preferences,
                prefer_placeholders,
                selection_counts,
                fixed_course_codes,
            )

    return []


def select_required_courses(
    rules: dict[str, Any],
    feasible_counts: dict[str, int],
    catalogue: Catalogue,
    career: str,
    branch_preferences: list[BranchPreference] | None = None,
    prefer_placeholders: bool = True,
    fixed_course_codes: set[str] | None = None,
) -> list[str]:
    """Resolve the rules file into the concrete set of courses to schedule."""

    if branch_preferences is None:
        branch_preferences = []
    if fixed_course_codes is None:
        fixed_course_codes = set()

    required = rules.get("required")
    if not isinstance(required, dict):
        return []

    selected: list[str] = []
    seen: set[str] = set()
    selection_counts: Counter[str] = Counter()
    for clauses in cast(dict[str, Any], required).values():
        if not isinstance(clauses, list):
            continue
        for clause in cast(list[RuleExpr], clauses):
            chosen = resolve_selected_courses(
                clause,
                feasible_counts,
                catalogue,
                career,
                branch_preferences,
                prefer_placeholders,
                selection_counts,
                fixed_course_codes,
            )
            for course in chosen:
                if course in seen:
                    continue
                seen.add(course)
                selected.append(course)

    return selected


def feasible_slots_for_course(
    code: str,
    slots: list[Slot],
    offerings: dict[str, list[str]],
    fixed_constraints: FixedConstraints | None = None,
) -> list[int]:
    """Return slot indices where the course is offered, ordered by preference."""

    base_code = assignment_course_code(code)
    offered_periods = offerings.get(base_code)
    if not offered_periods:
        LOGGER.debug("course %s not found in offerings", code)
        return []  # No feasible slots if not in offerings; will leave course unplaced

    allowed = {canonical_period(period) for period in offered_periods}
    feasible_slots = [slot for slot in slots if slot.canonical_period in allowed]

    if fixed_constraints is not None:
        pinned_slot = fixed_constraints.fixed_assignments.get(code)
        if pinned_slot is not None:
            feasible_slots = [
                slot for slot in feasible_slots if slot.slot_idx == pinned_slot
            ]
        else:
            feasible_slots = [
                slot
                for slot in feasible_slots
                if (
                    slot.slot_idx not in fixed_constraints.locked_slots
                    or code
                    in fixed_constraints.allowed_codes_by_slot.get(slot.slot_idx, set())
                )
            ]

    feasible_slots.sort(key=slot_preference_key)
    return [slot.slot_idx for slot in feasible_slots]


def dependency_map(catalogue: Catalogue) -> dict[str, RuleExpr | None]:
    """Pre-parse prerequisite expressions for all catalogue courses."""

    expr_map: dict[str, RuleExpr | None] = {}
    for entry in catalogue.values():
        prereq_expr, _, _ = parse_prerequisite_field(entry.prerequisites)
        expr_map[entry.code] = prereq_expr
    return expr_map


def prerequisite_depths(
    required_courses: list[str],
    dependency_exprs: dict[str, RuleExpr | None],
) -> dict[str, int]:
    """Estimate prerequisite depth within the selected required-course set."""

    required_set = {assignment_course_code(code) for code in required_courses}
    cache: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(code: str) -> int:
        if code in cache:
            return cache[code]
        if code in visiting:
            return 0
        visiting.add(code)
        expr = dependency_exprs.get(assignment_course_code(code))
        prereqs = (
            [prereq for prereq in extract_expr_courses(expr) if prereq in required_set]
            if expr is not None
            else []
        )
        depth = 0
        if prereqs:
            depth = 1 + max(visit(prereq) for prereq in prereqs)
        visiting.remove(code)
        cache[code] = depth
        return depth

    for course in required_courses:
        visit(course)
    return cache


def build_plan_document(
    assignments: dict[str, int],
    slots: list[Slot],
    catalogue: Catalogue,
    career: str,
    intake: str,
) -> dict[str, Any]:
    """Render assignments into the legacy JSON-like plan document shape.

    The inner search now evaluates scheduled rows directly, but this helper is
    still useful for debugging and for code paths that need the historical plan
    document format.
    """

    by_slot: dict[int, list[str]] = {}
    for code, slot_idx in assignments.items():
        by_slot.setdefault(slot_idx, []).append(assignment_course_code(code))

    courses_out: list[dict[str, Any]] = []
    for slot in slots:
        course_codes = sorted(by_slot.get(slot.slot_idx, []))
        for i, code in enumerate(course_codes, start=1):
            meta = catalogue.get(
                CatalogueKey(assignment_course_code(code), career)
            ) or CatalogueEntry(
                code=assignment_course_code(code),
                title=assignment_course_code(code),
                career=career,
                uoc=6,
            )
            courses_out.append(
                {
                    "enrol_year": slot.enrol_year,
                    "year": slot.calendar_year,
                    "period": slot.period,
                    "course_n": f"Course {i}",
                    "code": assignment_course_code(code),
                    "title": meta.title,
                    "uoc": meta.uoc,
                    "prerequisites": meta.prerequisites,
                }
            )

    return {
        "sheet": "GENERATED",
        "intake": intake,
        "courses": courses_out,
    }


def scheduled_courses_from_assignments(
    assignments: dict[str, int],
    slots: list[Slot],
    catalogue: Catalogue,
    career: str,
) -> list[ScheduledPlanCourse]:
    """Convert assignments into chronologically ordered scheduled course rows."""

    by_slot: dict[int, list[str]] = {}
    for code, slot_idx in assignments.items():
        by_slot.setdefault(slot_idx, []).append(assignment_course_code(code))

    scheduled: list[ScheduledPlanCourse] = []
    idx = 0
    for slot in slots:
        course_codes = sorted(by_slot.get(slot.slot_idx, []))
        for course_pos, code in enumerate(course_codes, start=1):
            meta = catalogue.get(CatalogueKey(code, career)) or CatalogueEntry(
                code=code, title=code, career=career, uoc=6
            )
            scheduled.append(
                ScheduledPlanCourse(
                    index=idx,
                    code=code,
                    year=slot.calendar_year,
                    period=slot.period,
                    period_rank=slot.slot_idx,
                    course_rank=course_pos,
                    uoc=meta.uoc,
                    prerequisites=meta.prerequisites,
                )
            )
            idx += 1

    # Slots are already in chronological order, so the emitted rows are directly
    # usable by degree_rules prerequisite validation.
    return scheduled


def prior_history_for_slot(
    assignments: dict[str, int],
    candidate_slot: int,
    catalogue: Catalogue,
    career: str,
) -> tuple[Counter[str], int]:
    prior_courses = Counter(
        assignment_course_code(course)
        for course, slot_idx in assignments.items()
        if slot_idx < candidate_slot
    )
    prior_uoc = sum(
        (
            catalogue.get(CatalogueKey(assignment_course_code(course), career))
            or CatalogueEntry(
                code=assignment_course_code(course),
                title=assignment_course_code(course),
                career=career,
                uoc=6,
            )
        ).uoc
        for course, slot_idx in assignments.items()
        if slot_idx < candidate_slot
    )
    return prior_courses, prior_uoc


def slot_satisfies_prerequisites(
    code: str,
    candidate_slot: int,
    assignments: dict[str, int],
    dependency_exprs: dict[str, RuleExpr | None],
    catalogue: Catalogue,
    career: str,
) -> bool:
    """Check whether placing a course into one slot is prereq-safe."""

    expr = dependency_exprs.get(assignment_course_code(code))
    if expr is None:
        return True
    prior_courses, prior_uoc = prior_history_for_slot(
        assignments, candidate_slot, catalogue, career
    )
    return evaluate_expression(expr, prior_courses, prior_uoc)


def slot_hint_penalty_for_course(
    code: str, slot: Slot, steering: SteeringConfig
) -> float:
    """Return soft placement penalty for scheduling one course in one slot."""

    hint = steering.course_hints.get(code)
    if hint is None:
        # Explicit hint is absent, but implicit year hint still applies.
        hint = {}
    hint_dict = cast(dict[str, object], hint)
    hint_weight = hint_dict.get("hint_weight")
    weight = float(hint_weight) if isinstance(hint_weight, (int, float)) else 10.0
    penalty = 0.0
    preferred_period = hint_dict.get("preferred_period")
    preferred_year = hint_dict.get("preferred_year_number")
    if (
        isinstance(preferred_period, str)
        and canonical_period(preferred_period) != slot.canonical_period
    ):
        penalty += weight
    if isinstance(preferred_year, int):
        year_distance = abs(preferred_year - slot.year_number)
        if year_distance == 1:
            penalty += 0.25 * weight
        elif year_distance > 1:
            penalty += weight

    # Always apply a low-weight implicit year hint from course code level.
    implicit_year = implicit_preferred_year_number(code)
    if implicit_year is not None:
        implicit_distance = abs(implicit_year - slot.year_number)
        if implicit_distance == 1:
            penalty += 0.25 * steering.cost.implicit_year_hint_weight
        elif implicit_distance > 1:
            penalty += steering.cost.implicit_year_hint_weight
    return penalty


def placeholder_overlap_for_slot(
    code: str, slot_idx: int, assignments: dict[str, int]
) -> int:
    if not is_placeholder_course(code):
        return 0
    return sum(
        1
        for other_code, other_slot_idx in assignments.items()
        if other_slot_idx == slot_idx and is_placeholder_course(other_code)
    )


def baseline_config_for_restart(restart: int) -> BaselineConfig:
    """Cycle through baseline profiles to diversify restart starting points."""

    profiles = [
        BaselineConfig(
            name="balanced",
            hint_factor=1.0,
            placeholder_factor=1.0,
            nonstandard_factor=1.0,
            slot_delay_factor=0.05,
            score_jitter=0.00,
            course_rank_jitter=0.00,
            top_slot_pool=1,
        ),
        BaselineConfig(
            name="hint-heavy",
            hint_factor=1.35,
            placeholder_factor=1.0,
            nonstandard_factor=1.1,
            slot_delay_factor=0.04,
            score_jitter=0.10,
            course_rank_jitter=0.10,
            top_slot_pool=2,
        ),
        BaselineConfig(
            name="compact",
            hint_factor=0.9,
            placeholder_factor=1.2,
            nonstandard_factor=1.3,
            slot_delay_factor=0.10,
            score_jitter=0.15,
            course_rank_jitter=0.20,
            top_slot_pool=2,
        ),
        BaselineConfig(
            name="explore",
            hint_factor=0.8,
            placeholder_factor=1.4,
            nonstandard_factor=1.0,
            slot_delay_factor=0.02,
            score_jitter=0.30,
            course_rank_jitter=0.35,
            top_slot_pool=3,
        ),
    ]
    return profiles[restart % len(profiles)]


def evaluate_plan_cost(
    assignments: dict[str, int],
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    catalogue: Catalogue,
    career: str,
    rules: dict[str, Any],
    steering: SteeringConfig,
    intake: str,
    fixed_constraints: FixedConstraints | None = None,
    target_end_slot_idx: int | None = None,
) -> CostDetails:
    """Evaluate the full planner objective for one assignment mapping.

    This function is intentionally the single place where the objective is
    assembled. Search, repair, and greedy seeding all depend on the same scoring
    semantics so that improvements are comparable across phases.
    """

    offering_violations = 0
    for code, slot_idx in assignments.items():
        base_code = assignment_course_code(code)
        offered_periods = offerings.get(base_code)
        if not offered_periods:
            offering_violations += 1
            continue
        if slots[slot_idx].period not in offered_periods:
            offering_violations += 1

    scheduled_courses = scheduled_courses_from_assignments(
        assignments, slots, catalogue, career
    )
    prereq_failures, _unsupported = validate_scheduled_prerequisites(scheduled_courses)
    prereq_violations = len(prereq_failures)

    completed = Counter(course.code for course in scheduled_courses)
    required_results = evaluate_required(rules, completed)
    required_failures = sum(1 for ok in required_results.values() if not ok)

    unplaced = [code for code in required_courses if code not in assignments]

    by_slot_counts = Counter(assignments.values())
    overload_count = 0
    summer_count = 0
    winter_count = 0
    placeholder_overlap_count = 0
    uoc_by_slot: list[int] = []
    slot_delay_total = sum(slot_idx + 1 for slot_idx in assignments.values())
    used_slot_count = len(by_slot_counts)
    post_target_period_count = 0
    if target_end_slot_idx is not None:
        post_target_period_count = sum(
            1 for slot_idx in assignments.values() if slot_idx > target_end_slot_idx
        )
    for slot in slots:
        count = by_slot_counts.get(slot.slot_idx, 0)
        if count > slot.max_slots:
            overload_count += count - slot.max_slots
        if slot.canonical_period == "summer term":
            summer_count += count
        if slot.canonical_period == "winter term":
            winter_count += count
        placeholder_count = sum(
            1
            for code, slot_idx in assignments.items()
            if slot_idx == slot.slot_idx and is_placeholder_course(code)
        )
        if placeholder_count > 1:
            placeholder_overlap_count += placeholder_count - 1

        total_uoc = 0
        for code, slot_idx in assignments.items():
            if slot_idx == slot.slot_idx:
                base_code = assignment_course_code(code)
                total_uoc += (
                    catalogue.get(CatalogueKey(base_code, career))
                    or CatalogueEntry(
                        code=base_code, title=base_code, career=career, uoc=6
                    )
                ).uoc
        uoc_by_slot.append(total_uoc)

    uoc_stddev = 0.0
    if uoc_by_slot:
        mean_uoc = sum(uoc_by_slot) / len(uoc_by_slot)
        variance = sum((value - mean_uoc) ** 2 for value in uoc_by_slot) / len(
            uoc_by_slot
        )
        uoc_stddev = math.sqrt(variance)

    hint_penalty = 0.0
    for code, slot_idx in assignments.items():
        hint_penalty += slot_hint_penalty_for_course(
            assignment_course_code(code), slots[slot_idx], steering
        )

    soft_precedence_penalty = 0.0
    soft_precedence_violations = 0
    for rule in steering.soft_precedence_rules:
        before_slot = assignments.get(rule.before)
        after_slot = assignments.get(rule.after)
        if before_slot is None or after_slot is None:
            continue
        if before_slot < after_slot:
            continue
        if before_slot == after_slot:
            soft_precedence_penalty += 0.5 * rule.weight
        else:
            soft_precedence_penalty += rule.weight
        soft_precedence_violations += 1

    fixed_constraint_violations = 0
    if fixed_constraints is not None:
        for code, required_slot in fixed_constraints.fixed_assignments.items():
            actual_slot = assignments.get(code)
            if actual_slot != required_slot:
                fixed_constraint_violations += 1

        for code, slot_idx in assignments.items():
            if (
                slot_idx in fixed_constraints.locked_slots
                and code
                not in fixed_constraints.allowed_codes_by_slot.get(slot_idx, set())
            ):
                fixed_constraint_violations += 1

    cost = 0.0
    cost += steering.cost.offering_violation * offering_violations
    cost += steering.cost.prerequisite_violation * prereq_violations
    cost += steering.cost.required_clause_violation * required_failures
    cost += steering.cost.unplaced_course * len(unplaced)
    cost += steering.cost.slot_overload * overload_count
    cost += steering.cost.summer_term_penalty * summer_count
    cost += steering.cost.winter_term_penalty * winter_count
    cost += steering.cost.uoc_imbalance * uoc_stddev
    cost += steering.cost.slot_delay * slot_delay_total
    cost += steering.cost.used_slot_penalty * used_slot_count
    cost += steering.cost.placeholder_same_period_penalty * placeholder_overlap_count
    cost += hint_penalty
    cost += soft_precedence_penalty
    cost += steering.cost.fixed_constraint_violation * fixed_constraint_violations
    cost += steering.cost.post_target_period_penalty * post_target_period_count

    return CostDetails(
        total_cost=cost,
        offering_violations=offering_violations,
        prereq_violations=prereq_violations,
        required_failures=required_failures,
        unplaced_count=len(unplaced),
        overload_count=overload_count,
        summer_count=summer_count,
        winter_count=winter_count,
        uoc_stddev=uoc_stddev,
        hint_penalty=hint_penalty,
        soft_precedence_penalty=soft_precedence_penalty,
        soft_precedence_violations=soft_precedence_violations,
        placeholder_overlap_count=placeholder_overlap_count,
        slot_delay_total=slot_delay_total,
        used_slot_count=used_slot_count,
        fixed_constraint_violations=fixed_constraint_violations,
        post_target_period_count=post_target_period_count,
    )


def greedy_place(
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    catalogue: Catalogue,
    career: str,
    dependency_exprs: dict[str, RuleExpr | None],
    prereq_depth_by_course: dict[str, int],
    steering: SteeringConfig,
    baseline_config: BaselineConfig,
    existing: dict[str, int] | None,
    rng: random.Random,
    fixed_constraints: FixedConstraints | None = None,
) -> dict[str, int]:
    """Create one baseline plan by greedily assigning courses to slots.

    The greedy pass is not meant to be perfect. Its job is to produce a decent,
    diverse starting point that the repair and annealing phases can improve.
    """

    assignments: dict[str, int] = dict(existing or {})
    free_capacity = {slot.slot_idx: slot.max_slots for slot in slots}
    for slot_idx in assignments.values():
        free_capacity[slot_idx] = free_capacity.get(slot_idx, 0) - 1

    candidates = [code for code in required_courses if code not in assignments]
    feasible_counts = {
        code: len(feasible_slots_for_course(code, slots, offerings, fixed_constraints))
        for code in candidates
    }

    def course_rank_score(code: str) -> float:
        level_value = level_rank(
            (
                catalogue.get(CatalogueKey(assignment_course_code(code), career))
                or CatalogueEntry(
                    code=assignment_course_code(code),
                    title=assignment_course_code(code),
                    career=career,
                    uoc=6,
                )
            ).level
        )
        score = 0.0
        score += 1000.0 * float(feasible_counts.get(code, 0))
        score += 100.0 * float(prereq_depth_by_course.get(code, 0))
        score += 20.0 * float(course_numeric_level(code))
        score += float(level_value)
        if baseline_config.course_rank_jitter > 0.0:
            score += rng.random() * baseline_config.course_rank_jitter
        return score

    candidates.sort(key=lambda code: (course_rank_score(code), code))

    unplaced: list[str] = []
    for code in candidates:
        feasible = feasible_slots_for_course(code, slots, offerings, fixed_constraints)
        candidate_slots = [
            slot_idx for slot_idx in feasible if free_capacity.get(slot_idx, 0) > 0
        ]

        def slot_score(slot_idx: int) -> tuple[float, int]:
            slot = slots[slot_idx]
            score = 0.0
            score += baseline_config.hint_factor * slot_hint_penalty_for_course(
                code, slot, steering
            )
            score += baseline_config.placeholder_factor * float(
                placeholder_overlap_for_slot(code, slot_idx, assignments)
            )
            score += baseline_config.nonstandard_factor * (
                1.0 if is_nonstandard_period(slot.canonical_period) else 0.0
            )
            score += baseline_config.slot_delay_factor * float(slot_idx)
            if baseline_config.score_jitter > 0.0:
                score += rng.random() * baseline_config.score_jitter
            return (score, slot_idx)

        chosen_slot: int | None = None
        if candidate_slots:
            prereq_safe_slots = [
                slot_idx
                for slot_idx in candidate_slots
                if slot_satisfies_prerequisites(
                    code, slot_idx, assignments, dependency_exprs, catalogue, career
                )
            ]
            search_slots = prereq_safe_slots if prereq_safe_slots else candidate_slots
            scored_slots = sorted(search_slots, key=slot_score)
            pool_size = max(1, min(baseline_config.top_slot_pool, len(scored_slots)))
            chosen_slot = rng.choice(scored_slots[:pool_size])
        if chosen_slot is not None:
            assignments[code] = chosen_slot
            free_capacity[chosen_slot] = free_capacity.get(chosen_slot, 0) - 1
        else:
            unplaced.append(code)

    if LOGGER.isEnabledFor(logging.DEBUG) and unplaced:
        LOGGER.debug(
            "greedy_place: %d/%d courses unplaced (no capacity)",
            len(unplaced),
            len(candidates),
        )

    return assignments


def find_dependents(
    required_courses: Iterable[str], dependency_exprs: dict[str, RuleExpr | None]
) -> dict[str, set[str]]:
    """Build reverse prerequisite links within the selected required course set."""

    required_set = set(required_courses)
    rev: dict[str, set[str]] = {}
    for dep in required_set:
        expr = dependency_exprs.get(assignment_course_code(dep))
        if expr is None:
            continue
        for prereq_code in extract_expr_courses(expr):
            rev.setdefault(prereq_code, set()).add(dep)
    return rev


def slot_order(assignments: dict[str, int], code: str) -> int:
    return assignments.get(code, 1_000_000)


def satisfied_without_course(
    expr: RuleExpr,
    history: Counter[str],
    target_code: str,
) -> bool:
    with_target = evaluate_expression(expr, history, 0)
    if not with_target:
        return False
    modified = Counter(history)
    target_base = assignment_course_code(target_code)
    if modified[target_base] > 0:
        modified[target_base] -= 1
        if modified[target_base] <= 0:
            del modified[target_base]
    return evaluate_expression(expr, modified, 0)


def cascade_ruin_set(
    seed_courses: set[str],
    assignments: dict[str, int],
    dependency_exprs: dict[str, RuleExpr | None],
    reverse_dependents: dict[str, set[str]],
) -> set[str]:
    """Expand a ruin seed set to include downstream courses that become invalid."""

    ruined = set(seed_courses)
    changed = True
    while changed:
        changed = False
        current_ruined = list(ruined)
        for ruined_code in current_ruined:
            dependents = reverse_dependents.get(ruined_code, set())
            for dep in dependents:
                if (
                    dep in ruined
                    or dep not in assignments
                    or ruined_code not in assignments
                ):
                    continue
                if slot_order(assignments, dep) <= slot_order(assignments, ruined_code):
                    continue
                expr = dependency_exprs.get(dep)
                if expr is None:
                    continue
                history = Counter(
                    assignment_course_code(course)
                    for course, slot_idx in assignments.items()
                    if slot_idx < assignments[dep] and course not in ruined
                )
                if not satisfied_without_course(expr, history, ruined_code):
                    ruined.add(dep)
                    changed = True
    return ruined


def repair_assignments(
    assignments: dict[str, int],
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    catalogue: Catalogue,
    career: str,
    dependency_exprs: dict[str, RuleExpr | None],
    rules: dict[str, Any],
    steering: SteeringConfig,
    intake: str,
    max_iters: int = 100,
    fixed_constraints: FixedConstraints | None = None,
    target_end_slot_idx: int | None = None,
) -> tuple[dict[str, int], CostDetails]:
    """Apply deterministic local improvements before or during annealing.

    Repair prefers moves that reduce prerequisite violations first, then failed
    requirement groups, then total objective cost.
    """

    current = dict(assignments)
    current_cost = evaluate_plan_cost(
        current,
        required_courses,
        slots,
        offerings,
        catalogue,
        career,
        rules,
        steering,
        intake,
        fixed_constraints,
        target_end_slot_idx,
    )

    fixed_codes: set[str] = (
        set(fixed_constraints.fixed_assignments.keys())
        if fixed_constraints is not None
        else set()
    )

    for _ in range(max_iters):
        improved = False
        for code in required_courses:
            if code not in current:
                continue
            if code in fixed_codes:
                continue
            original_slot = current[code]
            feasible = feasible_slots_for_course(
                code, slots, offerings, fixed_constraints
            )
            best_local = current_cost
            best_slot = original_slot
            prereq_safe_slots = [
                candidate_slot
                for candidate_slot in feasible
                if slot_satisfies_prerequisites(
                    code,
                    candidate_slot,
                    current,
                    dependency_exprs,
                    catalogue,
                    career,
                )
            ]
            candidate_order = prereq_safe_slots + [
                candidate_slot
                for candidate_slot in feasible
                if candidate_slot not in prereq_safe_slots
            ]
            # Try prereq-safe slots first so local repair spends more effort on
            # structurally plausible placements before considering weaker fallbacks.
            for candidate_slot in candidate_order:
                if candidate_slot == original_slot:
                    continue
                trial = dict(current)
                trial[code] = candidate_slot
                trial_cost = evaluate_plan_cost(
                    trial,
                    required_courses,
                    slots,
                    offerings,
                    catalogue,
                    career,
                    rules,
                    steering,
                    intake,
                    fixed_constraints,
                    target_end_slot_idx,
                )

                best_tuple = (
                    best_local.prereq_violations,
                    best_local.required_failures,
                    best_local.total_cost,
                )
                trial_tuple = (
                    trial_cost.prereq_violations,
                    trial_cost.required_failures,
                    trial_cost.total_cost,
                )
                if trial_tuple < best_tuple:
                    best_local = trial_cost
                    best_slot = candidate_slot
            if best_slot != original_slot:
                current[code] = best_slot
                current_cost = best_local
                improved = True
        if not improved:
            break

    return current, current_cost


def propose_shift(
    assignments: dict[str, int],
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    rng: random.Random,
    fixed_constraints: FixedConstraints | None = None,
) -> dict[str, int]:
    """Propose moving one course to another feasible offered slot."""

    if not assignments:
        return dict(assignments)
    movable_codes = list(assignments.keys())
    if fixed_constraints is not None:
        fixed_codes = set(fixed_constraints.fixed_assignments.keys())
        movable_codes = [code for code in movable_codes if code not in fixed_codes]
    if not movable_codes:
        return dict(assignments)

    code = rng.choice(movable_codes)
    feasible = feasible_slots_for_course(code, slots, offerings, fixed_constraints)
    if not feasible:
        return dict(assignments)
    target_slot = rng.choice(feasible)
    trial = dict(assignments)
    trial[code] = target_slot
    return trial


def propose_swap(
    assignments: dict[str, int],
    rng: random.Random,
    fixed_constraints: FixedConstraints | None = None,
) -> dict[str, int]:
    """Propose swapping the slots of two already-placed courses."""

    if len(assignments) < 2:
        return dict(assignments)
    movable_codes = list(assignments.keys())
    if fixed_constraints is not None:
        fixed_codes = set(fixed_constraints.fixed_assignments.keys())
        movable_codes = [code for code in movable_codes if code not in fixed_codes]
    if len(movable_codes) < 2:
        return dict(assignments)

    a, b = rng.sample(movable_codes, 2)
    trial = dict(assignments)
    trial[a], trial[b] = trial[b], trial[a]
    return trial


def propose_ruin_recreate(
    assignments: dict[str, int],
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    catalogue: Catalogue,
    career: str,
    dependency_exprs: dict[str, RuleExpr | None],
    prereq_depth_by_course: dict[str, int],
    steering: SteeringConfig,
    baseline_config: BaselineConfig,
    reverse_dependents: dict[str, set[str]],
    ruin_fraction: float,
    rng: random.Random,
    fixed_constraints: FixedConstraints | None = None,
) -> dict[str, int]:
    """Propose a larger neighborhood move by removing and rebuilding a subset."""

    if not assignments:
        return dict(assignments)

    movable_codes = list(assignments.keys())
    if fixed_constraints is not None:
        fixed_codes = set(fixed_constraints.fixed_assignments.keys())
        movable_codes = [code for code in movable_codes if code not in fixed_codes]
    if not movable_codes:
        return dict(assignments)

    count = max(1, int(len(movable_codes) * ruin_fraction))
    seeds = set(rng.sample(movable_codes, min(count, len(movable_codes))))
    # Cascading the ruin set avoids rebuilding an assignment that would leave
    # obvious downstream prerequisite relationships broken.
    ruined = cascade_ruin_set(seeds, assignments, dependency_exprs, reverse_dependents)

    kept = {
        code: slot_idx for code, slot_idx in assignments.items() if code not in ruined
    }
    rebuilt = greedy_place(
        required_courses,
        slots,
        offerings,
        catalogue,
        career,
        dependency_exprs,
        prereq_depth_by_course,
        steering,
        baseline_config,
        kept,
        rng,
        fixed_constraints,
    )
    return rebuilt


def anneal(
    initial: dict[str, int],
    required_courses: list[str],
    slots: list[Slot],
    offerings: dict[str, list[str]],
    catalogue: Catalogue,
    career: str,
    rules: dict[str, Any],
    steering: SteeringConfig,
    search: SearchConfig,
    dependency_exprs: dict[str, RuleExpr | None],
    prereq_depth_by_course: dict[str, int],
    baseline_config: BaselineConfig,
    reverse_dependents: dict[str, set[str]],
    intake: str,
    rng: random.Random,
    fixed_constraints: FixedConstraints | None = None,
    target_end_slot_idx: int | None = None,
) -> tuple[dict[str, int], CostDetails]:
    """Run one simulated annealing search from an initial repaired baseline."""

    current = dict(initial)
    current_cost = evaluate_plan_cost(
        current,
        required_courses,
        slots,
        offerings,
        catalogue,
        career,
        rules,
        steering,
        intake,
        fixed_constraints,
        target_end_slot_idx,
    )
    best = dict(current)
    best_cost = current_cost
    log_every = max(1, search.iterations // 20)
    patience = (
        max(1, int(search.patience))
        if search.patience is not None
        else max(5, search.iterations // 4)
    )
    iterations_without_improvement = 0

    for step in range(search.iterations):
        if search.iterations <= 1:
            temp = search.t_end
        else:
            progress = step / float(search.iterations - 1)
            temp = search.t_start * ((search.t_end / search.t_start) ** progress)

        move_roll = rng.random()
        if move_roll < 0.60:
            proposal = propose_ruin_recreate(
                current,
                required_courses,
                slots,
                offerings,
                catalogue,
                career,
                dependency_exprs,
                prereq_depth_by_course,
                steering,
                baseline_config,
                reverse_dependents,
                search.ruin_fraction,
                rng,
                fixed_constraints,
            )
        elif move_roll < 0.90:
            proposal = propose_shift(
                current,
                required_courses,
                slots,
                offerings,
                rng,
                fixed_constraints,
            )
        else:
            proposal = propose_swap(current, rng, fixed_constraints)

        proposal, proposal_cost = repair_assignments(
            proposal,
            required_courses,
            slots,
            offerings,
            catalogue,
            career,
            dependency_exprs,
            rules,
            steering,
            intake,
            max_iters=8,
            fixed_constraints=fixed_constraints,
            target_end_slot_idx=target_end_slot_idx,
        )

        delta = proposal_cost.total_cost - current_cost.total_cost
        accept = delta <= 0.0
        if not accept and temp > 0:
            accept_prob = math.exp(-delta / temp)
            accept = rng.random() < accept_prob

        # Annealing accepts some worse moves early on so the search can escape
        # local minima instead of behaving like pure hill climbing.
        if accept:
            current = proposal
            current_cost = proposal_cost
            if current_cost.total_cost < best_cost.total_cost:
                best = dict(current)
                best_cost = current_cost
                iterations_without_improvement = (
                    0  # Reset patience counter on improvement
                )
            else:
                iterations_without_improvement += 1
        else:
            iterations_without_improvement += 1

        # Early exit if no improvement for too long
        if iterations_without_improvement >= patience:
            LOGGER.info(
                "Early exit at iteration %d/%d: no improvement for %d iterations (patience=%d)",
                step + 1,
                search.iterations,
                iterations_without_improvement,
                patience,
            )
            break

        if LOGGER.isEnabledFor(logging.DEBUG) and (
            step == 0 or (step + 1) % log_every == 0 or step == search.iterations - 1
        ):
            LOGGER.debug(
                (
                    "iter=%d/%d temp=%.4f move=%s current=%.2f best=%.2f accept=%s delta=%.2f"
                ),
                step + 1,
                search.iterations,
                temp,
                (
                    "ruin"
                    if move_roll < 0.60
                    else "shift"
                    if move_roll < 0.90
                    else "swap"
                ),
                current_cost.total_cost,
                best_cost.total_cost,
                "yes" if accept else "no",
                delta,
            )

    return best, best_cost


def assignments_signature(assignments: dict[str, int]) -> str:
    """Create a stable signature so duplicate solutions can be deduplicated."""

    parts = [f"{code}:{slot_idx}" for code, slot_idx in sorted(assignments.items())]
    return "|".join(parts)


def render_csv_rows(
    slots: list[Slot],
    options: list[dict[str, int]],
    *,
    show_nonstandard_periods: bool = False,
) -> list[list[str]]:
    """Render solution assignments into the exported CSV row layout."""

    by_option_slot: list[dict[int, list[str]]] = []
    for assignments in options:
        mapping: dict[int, list[str]] = {}
        for code, slot_idx in assignments.items():
            mapping.setdefault(slot_idx, []).append(assignment_course_code(code))
        for slot_codes in mapping.values():
            slot_codes.sort()
        by_option_slot.append(mapping)

    all_used_slot_idxs: set[int] = set()
    for mapping in by_option_slot:
        all_used_slot_idxs.update(mapping.keys())

    if all_used_slot_idxs:
        used_slots_ordered = [s for s in slots if s.slot_idx in all_used_slot_idxs]
        first_used = used_slots_ordered[0].slot_idx if used_slots_ordered else None
        last_used = used_slots_ordered[-1].slot_idx if used_slots_ordered else None
    else:
        first_used = last_used = None

    rows: list[list[str]] = []
    header = ["Year", "Period", "Course Row"] + [
        f"Option {idx}" for idx in range(1, len(options) + 1)
    ]
    rows.append(header)

    for slot in slots:
        courses_per_option = [
            by_option_slot[option_idx].get(slot.slot_idx, [])
            for option_idx in range(len(options))
        ]
        assigned_count = max(
            (len(courses) for courses in courses_per_option), default=0
        )
        if assigned_count == 0:
            include_empty = (
                show_nonstandard_periods
                and _is_nonstandard_period(slot.canonical_period)
                and first_used is not None
                and first_used <= slot.slot_idx <= last_used  # type: ignore[operator]
            )
            if not include_empty:
                continue
        max_rows = max(assigned_count, slot.max_slots)
        year_label = f"{slot.enrol_year} ({slot.calendar_year})"

        for row_idx in range(max_rows):
            row = [year_label, slot.period, f"Course {row_idx + 1}"]
            for option_idx in range(len(options)):
                option_courses = courses_per_option[option_idx]
                row.append(
                    option_courses[row_idx] if row_idx < len(option_courses) else ""
                )
            rows.append(row)

    return rows


def write_csv(
    rows: list[list[str]],
    output_path: Path | None,
    output_stream: TextIO | None = None,
) -> None:
    """Write CSV rows either to a stream or to the requested file path."""

    if output_path is None:
        if output_stream is None:
            raise ValueError("output_stream is required when output_path is None")
        writer = csv.writer(output_stream)
        writer.writerows(rows)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


def path_or_exit(path: Path, label: str) -> Path:
    """Return a required file path or raise a helpful missing-artifact error."""

    if path.is_file():
        return path
    raise FileNotFoundError(
        f"Missing {label}: {path}. If extracted artifacts are missing, run extract_template.py first."
    )


def run_planner(command: PlannerCommand, *, stdout: TextIO, stderr: TextIO) -> int:
    """Execute one planner run without any CLI parser dependency."""

    rng = random.Random(command.seed)

    rule_path = path_or_exit(command.rule_path, "rules file")
    offerings_path = path_or_exit(command.offerings_path, "offerings file")
    catalogue_path = path_or_exit(command.catalogue_path, "catalogue file")
    template_path = path_or_exit(command.template_config_path, "template config file")
    partial_plan_path = (
        path_or_exit(command.partial_plan_path, "partial plan file")
        if command.partial_plan_path is not None
        else None
    )

    rules = load_rules(rule_path)
    offerings = load_offerings(offerings_path)
    extra_override_paths: list[Path] = []
    school = rules.get("school")
    if isinstance(school, str) and school.strip():
        extra_override_paths.append(
            catalogue_path.parent / school.strip() / "catalogue_overrides.json"
        )
    if command.partial_plan_path is not None:
        extra_override_paths.append(
            command.partial_plan_path.parent / "catalogue_overrides.json"
        )
    if command.output_path is not None:
        extra_override_paths.append(
            command.output_path.parent / "catalogue_overrides.json"
        )

    catalogue = load_catalogue(catalogue_path, override_paths=extra_override_paths)
    templates = load_templates(template_path)
    steering = load_steering(command.steering_path)

    career = resolve_rules_career(rules)
    if command.career is not None:
        override_career = normalize_catalogue_career(command.career)
        if override_career != career:
            raise ValueError(
                "PlannerCommand.career does not match the rules file career: "
                f"'{override_career}' != '{career}'"
            )

    ensure_catalogue_has_career(catalogue, career)
    ensure_catalogue_courses_for_career(
        catalogue,
        extract_required_rule_courses(rules),
        career,
        stderr=stderr,
    )

    slots = build_slots(templates, command.intake)

    partial_plan_courses: list[PartialPlanCourseRecord] = []
    if partial_plan_path is not None:
        partial_plan_raw = read_json(partial_plan_path)
        if not isinstance(partial_plan_raw, dict):
            raise ValueError("Partial plan file must contain an object")
        partial_plan = cast(dict[str, Any], partial_plan_raw)
        partial_plan_courses = extract_partial_plan_courses(partial_plan)
        plan_intake = partial_plan.get("intake")
        if isinstance(plan_intake, str) and plan_intake != command.intake:
            print(
                (
                    f"Warning: partial plan intake '{plan_intake}' differs from requested "
                    f"intake '{command.intake}'"
                ),
                file=stderr,
            )

    ensure_catalogue_courses_for_career(
        catalogue,
        {record.code for record in partial_plan_courses},
        career,
        stderr=stderr,
    )

    preselected_constraints = derive_fixed_constraints(partial_plan_courses, slots)
    for message in preselected_constraints.diagnostics:
        print(f"Warning: {message}", file=stderr)

    target_end_slot_idx: int | None = None
    if command.target_end is not None:
        target_end_slot_idx = resolve_target_end_slot(slots, command.target_end)
        target_slot = slots[target_end_slot_idx]
        LOGGER.info(
            "target end '%s' resolved to slot %d (%s %s)",
            command.target_end,
            target_end_slot_idx,
            target_slot.calendar_year,
            target_slot.period,
        )

    all_codes = sorted(
        {entry.code for entry in catalogue.values() if entry.career == career}
    )
    feasible_counts = {
        code: len(
            feasible_slots_for_course(
                code,
                slots,
                offerings,
                preselected_constraints,
            )
        )
        for code in all_codes
    }
    required_courses = select_required_courses(
        rules,
        feasible_counts,
        catalogue,
        career,
        steering.branch_preferences,
        prefer_placeholders=not command.no_placeholders,
        fixed_course_codes={record.code for record in partial_plan_courses},
    )

    if not required_courses:
        raise ValueError("No required courses could be extracted from the rules file")

    dependency_exprs = dependency_map(catalogue)
    prereq_depth_by_course = prerequisite_depths(required_courses, dependency_exprs)
    reverse_dependents = find_dependents(required_courses, dependency_exprs)
    fixed_constraints = derive_fixed_constraints(
        partial_plan_courses,
        slots,
        required_courses,
    )
    for message in fixed_constraints.diagnostics:
        print(f"Warning: {message}", file=stderr)

    search = SearchConfig(
        restarts=max(1, int(command.restarts)),
        iterations=max(1, int(command.iterations)),
        ruin_fraction=max(0.05, min(0.95, float(command.ruin_fraction))),
        patience=(
            max(1, int(command.patience)) if command.patience is not None else None
        ),
    )

    best_by_signature: dict[str, tuple[dict[str, int], CostDetails]] = {}

    for restart in range(search.restarts):
        LOGGER.info("restart %d/%d", restart + 1, search.restarts)
        local_rng = random.Random(rng.randint(0, 10**9) + restart)
        baseline_config = baseline_config_for_restart(restart)
        LOGGER.info("baseline profile: %s", baseline_config.name)
        baseline = greedy_place(
            required_courses,
            slots,
            offerings,
            catalogue,
            career,
            dependency_exprs,
            prereq_depth_by_course,
            steering,
            baseline_config,
            existing=fixed_constraints.fixed_assignments,
            rng=local_rng,
            fixed_constraints=fixed_constraints,
        )
        baseline_cost = evaluate_plan_cost(
            baseline,
            required_courses,
            slots,
            offerings,
            catalogue,
            career,
            rules,
            steering,
            command.intake,
            fixed_constraints,
            target_end_slot_idx,
        )
        LOGGER.info(
            "baseline: placed=%d unplaced=%d cost=%.1f violations=offer:%d prereq:%d required:%d overload:%d",
            len(baseline),
            len(required_courses) - len(baseline),
            baseline_cost.total_cost,
            baseline_cost.offering_violations,
            baseline_cost.prereq_violations,
            baseline_cost.required_failures,
            baseline_cost.overload_count,
        )
        repaired, repaired_cost = repair_assignments(
            baseline,
            required_courses,
            slots,
            offerings,
            catalogue,
            career,
            dependency_exprs,
            rules,
            steering,
            command.intake,
            fixed_constraints=fixed_constraints,
            target_end_slot_idx=target_end_slot_idx,
        )
        LOGGER.info(
            "after repair: cost=%.1f violations=offer:%d prereq:%d required:%d overload:%d",
            repaired_cost.total_cost,
            repaired_cost.offering_violations,
            repaired_cost.prereq_violations,
            repaired_cost.required_failures,
            repaired_cost.overload_count,
        )
        best, best_cost = anneal(
            repaired,
            required_courses,
            slots,
            offerings,
            catalogue,
            career,
            rules,
            steering,
            search,
            dependency_exprs,
            prereq_depth_by_course,
            baseline_config,
            reverse_dependents,
            command.intake,
            local_rng,
            fixed_constraints,
            target_end_slot_idx,
        )

        LOGGER.info(
            "restart %d result: best_cost=%.1f violations=offer:%d prereq:%d required:%d overload:%d unplaced:%d",
            restart + 1,
            best_cost.total_cost,
            best_cost.offering_violations,
            best_cost.prereq_violations,
            best_cost.required_failures,
            best_cost.overload_count,
            best_cost.unplaced_count,
        )
        sig = assignments_signature(best)
        prior = best_by_signature.get(sig)
        if prior is None or best_cost.total_cost < prior[1].total_cost:
            best_by_signature[sig] = (best, best_cost)

    ranked = sorted(best_by_signature.values(), key=lambda item: item[1].total_cost)
    top_k = ranked[: max(1, int(command.num_solutions))]
    options = [item[0] for item in top_k]

    rows = render_csv_rows(slots, options, show_nonstandard_periods=command.show_nonstandard_periods)
    write_csv(rows, command.output_path, stdout)

    if command.verbose >= 1:
        print("Solution summary:", file=stderr)
        for idx, (_, details) in enumerate(top_k, start=1):
            print(
                (
                    f"  Option {idx}: cost={details.total_cost:.2f}, "
                    f"offering={details.offering_violations}, prereq={details.prereq_violations}, "
                    f"required={details.required_failures}, unplaced={details.unplaced_count}, "
                    f"overload={details.overload_count}, summer={details.summer_count}, winter={details.winter_count}, "
                    f"used_slots={details.used_slot_count}, delay={details.slot_delay_total}, placeholders={details.placeholder_overlap_count}, "
                    f"post_target={details.post_target_period_count}, "
                    f"soft_prec={details.soft_precedence_violations}/{details.soft_precedence_penalty:.1f}, "
                    f"hint={details.hint_penalty:.1f}, fixed={details.fixed_constraint_violations}"
                ),
                file=stderr,
            )

    return 0

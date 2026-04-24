from __future__ import annotations

"""Validate degree rules and schedule-aware prerequisite compliance."""

import json
import logging
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TextIO, TypedDict, cast

from transitionchecker.core import period_rank
from transitionchecker.core.catalogue import (
    Catalogue,
    CatalogueEntry,
    ensure_catalogue_has_career,
    get_catalogue_entry_for_career,
    resolve_rules_career,
)
from transitionchecker.prereq_engine import (
    parse_prerequisite_field,
)


CourseCode = str
RuleExpr = CourseCode | dict[str, Any]


_REQUIRED_VALIDATION_CACHE: set[int] = set()


class PlanCourseRecord(TypedDict, total=False):
    """Subset of plan JSON course fields needed for rule and schedule checks."""

    code: str
    year: int
    period: str
    course_n: str
    uoc: int
    prerequisites: str


class ValidationWarning(TypedDict):
    """Structured warning emitted during validation/report generation."""

    code: str
    message: str
    failure_id: NotRequired[str]
    location: NotRequired[str]


class ValidationFinding(TypedDict):
    """Structured finding for rule/prerequisite/corequisite validation."""

    failure_id: str
    kind: str
    message: str
    overrideable: bool
    accepted: bool
    non_overrideable_reason: NotRequired[str]


class ClauseMeta(TypedDict, total=False):
    """Metadata for top-level required clauses by level/index."""

    subset_id: str
    overrideable: bool
    non_overrideable_reason: str


@dataclass(frozen=True)
class ScheduledPlanCourse:
    """Normalized in-plan course record used for chronological validation."""

    index: int
    code: str
    year: int
    period: str
    period_rank: int
    course_rank: int
    uoc: int
    prerequisites: str


@dataclass
class RuleValidationError(Exception):
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass(frozen=True)
class RulesCommand:
    """Inputs for rules validation/report generation."""

    rules_file: Path
    json_output: bool = False
    plan_file: Path | None = None
    catalogue_file: Path | None = None
    plan_report_json: bool = False
    render_rules_text: bool = False
    add_overrides: tuple[str, ...] = ()


_CLAUSE_META_KEY = "_required_clause_meta"
_MISSING_UOC_ATOM_RE = re.compile(r"\d+\s*uoc", re.IGNORECASE)


def _make_warning(
    code: str,
    message: str,
    *,
    failure_id: str | None = None,
    location: str | None = None,
) -> ValidationWarning:
    warning: ValidationWarning = {
        "code": code,
        "message": message,
    }
    if failure_id is not None:
        warning["failure_id"] = failure_id
    if location is not None:
        warning["location"] = location
    return warning


def _warning_message(warning: ValidationWarning) -> str:
    location = warning.get("location")
    context = f" ({location})" if isinstance(location, str) and location else ""
    return f"[{warning['code']}] {warning['message']}{context}"


def _as_clause_meta_map(config: dict[str, Any]) -> dict[str, ClauseMeta]:
    value = config.get(_CLAUSE_META_KEY)
    if not isinstance(value, dict):
        return {}

    result: dict[str, ClauseMeta] = {}
    for key, raw_meta in cast(dict[object, object], value).items():
        if not isinstance(key, str) or not isinstance(raw_meta, dict):
            continue
        raw_meta_dict = cast(dict[str, object], raw_meta)
        meta: ClauseMeta = {}
        subset_id = raw_meta_dict.get("subset_id")
        if isinstance(subset_id, str) and subset_id:
            meta["subset_id"] = subset_id
        overrideable = raw_meta_dict.get("overrideable")
        if isinstance(overrideable, bool):
            meta["overrideable"] = overrideable
        reason = raw_meta_dict.get("non_overrideable_reason")
        if isinstance(reason, str) and reason:
            meta["non_overrideable_reason"] = reason
        result[key] = meta

    return result


def _slug_level_name(level_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", level_name.strip().lower())
    return slug.strip("-") or "level"


def _normalize_subset_rule_id(raw_value: str) -> str:
    return raw_value.strip()


def _implicit_level_id(level_name: str) -> str:
    """Derive an implicit subset id from a level name by title-casing words and joining.

    E.g. "Breadth electives" -> "BreadthElectives", "Free Electives" -> "FreeElectives".
    """
    return "".join(word.capitalize() for word in level_name.split())


def _clause_summary(clause: dict[str, Any], max_items: int = 4) -> str:
    """Return a short human-readable summary of an or/min-from clause for use in locations."""
    raw_or = clause.get("or")
    if isinstance(raw_or, list):
        options = cast(list[Any], raw_or)  # type: ignore[redundant-cast]
        labels: list[str] = []
        for opt in options[:max_items]:
            if isinstance(opt, str):
                labels.append(opt)
            elif isinstance(opt, dict) and "and" in opt:
                opt_dict = cast(dict[str, Any], opt)
                raw_and_val = opt_dict.get("and")
                and_list: list[object] = (
                    cast(list[object], raw_and_val) if isinstance(raw_and_val, list) else []
                )
                labels.append(
                    "+".join(str(c) for c in and_list[:3]) if and_list else "..."
                )
            else:
                labels.append("...")
        suffix = "|..." if len(options) > max_items else ""
        return f"or[{'|'.join(labels)}{suffix}]"
    if "min" in clause and "from" in clause:
        n = clause["min"]
        raw_from = clause.get("from")
        if isinstance(raw_from, list):
            pool = cast(list[Any], raw_from)  # type: ignore[redundant-cast]
            items = [str(c) for c in pool[:max_items]]
            suffix = "|..." if len(pool) > max_items else ""
            return f"min {n} from[{'|'.join(items)}{suffix}]"
    return ""


def _extract_clause_metadata(raw_config: dict[str, Any]) -> list[ValidationWarning]:
    """Attach top-level clause metadata to raw rules config and return warnings."""

    required = raw_config.get("required")
    if not isinstance(required, dict):
        return []

    warnings: list[ValidationWarning] = []
    clause_meta_map: dict[str, ClauseMeta] = {}
    seen_subset_ids: dict[str, tuple[str, str]] = {}

    required_levels = cast(dict[object, object], required)
    for level_name_obj, clauses_obj in required_levels.items():
        if not isinstance(level_name_obj, str) or not isinstance(clauses_obj, list):
            continue

        level_name = level_name_obj
        clauses = cast(list[object], clauses_obj)

        # Count how many subset clauses (or / min-from) exist in this level
        subset_clause_count = sum(
            1
            for c in clauses
            if isinstance(c, dict)
            and (
                "or" in cast(dict[str, Any], c)
                or (
                    "min" in cast(dict[str, Any], c)
                    and "from" in cast(dict[str, Any], c)
                )
            )
        )

        for idx, clause_obj in enumerate(clauses):
            if not isinstance(clause_obj, dict):
                continue

            clause = cast(dict[str, Any], clause_obj)
            has_or = "or" in clause
            has_min_from = "min" in clause and "from" in clause
            if not has_or and not has_min_from:
                continue

            location = f"required.{level_name}[{idx}] {_clause_summary(clause)}"
            meta_key = f"{level_name}\u241f{idx}"
            meta: ClauseMeta = {"overrideable": True}

            subset_id_obj = clause.get("id")
            subset_id = (
                _normalize_subset_rule_id(subset_id_obj)
                if isinstance(subset_id_obj, str)
                else ""
            )

            if not subset_id:
                if subset_clause_count == 1:
                    # Single subset clause in this level: derive id from the level name
                    subset_id = _implicit_level_id(level_name)
                else:
                    meta["overrideable"] = False
                    meta["non_overrideable_reason"] = "missing_rule_id"
                    warnings.append(
                        _make_warning(
                            "missing_rule_id",
                            "Subset clause has no 'id' and cannot be overridden",
                            location=location,
                        )
                    )
                    clause_meta_map[meta_key] = meta
                    continue

            if subset_id in seen_subset_ids:
                first_location, first_key = seen_subset_ids[subset_id]
                meta["overrideable"] = False
                meta["non_overrideable_reason"] = "duplicate_rule_id"
                meta["subset_id"] = subset_id
                warnings.append(
                    _make_warning(
                        "duplicate_rule_id",
                        (
                            f"Subset id '{subset_id}' is duplicated"
                            f" (first seen at {first_location})"
                        ),
                        location=location,
                    )
                )
                first_meta = clause_meta_map.get(first_key)
                if first_meta is not None:
                    first_meta["overrideable"] = False
                    first_meta["non_overrideable_reason"] = "duplicate_rule_id"
                clause_meta_map[meta_key] = meta
                continue

            seen_subset_ids[subset_id] = (location, meta_key)
            meta["subset_id"] = subset_id
            clause_meta_map[meta_key] = meta

    raw_config[_CLAUSE_META_KEY] = clause_meta_map
    return warnings


def _parse_int_like(value: Any, field_path: str) -> int:
    """Parse integer-like values used in plan and rules payloads.

    Args:
        value: Raw JSON value to parse.
        field_path: Field path used in validation errors.

    Returns:
        Parsed integer value.

    Raises:
        RuleValidationError: If value cannot be interpreted as an integer.
    """
    if isinstance(value, bool):
        raise RuleValidationError(field_path, "must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise RuleValidationError(field_path, "must be an integer")


def _period_rank(period: str) -> int | None:
    """Map teaching-period labels to sortable rank values.

    Args:
        period: Period label from plan data.

    Returns:
        Integer rank when period is supported, otherwise None.
    """
    return period_rank(period, fallback=None)


def _course_rank(course_n: str) -> int:
    """Extract numeric ordering from ``course_n``; unknown values sort last."""
    match = re.search(r"(\d+)", course_n)
    if not match:
        return 999
    return int(match.group(1))


def _select_catalogue_entry(
    code: str,
    catalogue: Catalogue,
    career: str | None = None,
) -> CatalogueEntry | None:
    """Pick the best available catalogue entry for a course code."""

    matches = catalogue.by_code(code)
    if not matches:
        return None
    if career is not None:
        return get_catalogue_entry_for_career(code, catalogue, career)

    if len(matches) == 1:
        return matches[0]

    available = sorted({entry.career or "<blank>" for entry in matches})
    raise ValueError(
        f"Catalogue course '{code}' has multiple career entries "
        f"({', '.join(available)}); supply an explicit career"
    )


def _expr_oneof_label(expr: RuleExpr) -> str:
    if not isinstance(expr, dict) or "or" not in expr:
        return "oneof"
    children = cast(list[RuleExpr], expr["or"])
    options = ", ".join(expression_to_text(child) for child in children)
    return f"one of ({options})"


def _collect_missing_atoms(
    expr: RuleExpr,
    completed_courses: Counter[str],
    completed_uoc: int,
) -> list[str]:
    if _is_course_code(expr):
        course = cast(str, expr)
        return [] if completed_courses[course] > 0 else [course]

    node = cast(dict[str, Any], expr)
    if set(node.keys()) == {"uoc"}:
        threshold = cast(int, node["uoc"])
        return [] if completed_uoc >= threshold else [f"{threshold}uoc"]

    if set(node.keys()) == {"and"}:
        children = cast(list[RuleExpr], node["and"])
        missing: list[str] = []
        for child in children:
            missing.extend(
                _collect_missing_atoms(child, completed_courses, completed_uoc)
            )
        return missing

    if set(node.keys()) == {"or"}:
        if evaluate_expression(expr, completed_courses, completed_uoc):
            return []
        return ["__ONEOF__"]

    return [expression_to_text(expr)]


def validate_scheduled_prerequisites(
    courses: list[ScheduledPlanCourse],
) -> tuple[list[str], list[str]]:
    """Validate prerequisite/corequisite expressions against scheduled plan order.

    Courses in the same teaching period do not satisfy prerequisites for one
    another, but they may satisfy corequisites.
    """
    failures, unsupported, _findings, _warnings = (
        validate_scheduled_prerequisites_detailed(courses)
    )
    return failures, unsupported


def validate_scheduled_prerequisites_detailed(
    courses: list[ScheduledPlanCourse],
) -> tuple[list[str], list[str], list[ValidationFinding], list[ValidationWarning]]:
    """Validate scheduled prerequisites and return diagnostics plus findings."""
    failures: list[str] = []
    unsupported: list[str] = []
    findings: list[ValidationFinding] = []
    warnings: list[ValidationWarning] = []

    prior_courses: Counter[str] = Counter()
    prior_uoc = 0
    group_start = 0

    while group_start < len(courses):
        course = courses[group_start]
        period_key = (course.year, course.period_rank)
        group_end = group_start
        while group_end < len(courses):
            candidate = courses[group_end]
            if (candidate.year, candidate.period_rank) != period_key:
                break
            group_end += 1

        group_courses = courses[group_start:group_end]
        group_counter: Counter[str] = Counter(item.code for item in group_courses)
        group_uoc = sum(item.uoc for item in group_courses)
        coreq_base = Counter(prior_courses)
        coreq_base.update(group_counter)

        oneof_index_by_course: dict[tuple[str, str], int] = {}

        for current in group_courses:
            prereq_expr, coreq_expr, unsupported_reason = parse_prerequisite_field(
                current.prerequisites
            )
            course_label = f"{current.code} ({current.year} {current.period})"

            if unsupported_reason:
                unsupported_msg = f"{course_label}: {unsupported_reason}; raw='{current.prerequisites.strip()}'"
                unsupported.append(unsupported_msg)
                warnings.append(
                    _make_warning(
                        "unsupported_syntax",
                        unsupported_msg,
                        location=course_label,
                    )
                )
                findings.append(
                    {
                        "failure_id": f"unsupported-syntax:{current.code}>{current.prerequisites.strip()}",
                        "kind": "unsupported-syntax",
                        "message": unsupported_msg,
                        "overrideable": False,
                        "accepted": False,
                        "non_overrideable_reason": "unsupported_syntax",
                    }
                )
                continue

            if prereq_expr is not None and not evaluate_expression(
                prereq_expr, prior_courses, prior_uoc
            ):
                diagnosis = diagnose_expression(prereq_expr, prior_courses, prior_uoc)
                prereq_text = expression_to_text(prereq_expr)
                failure_msg = (
                    f"[Prerequisite] {course_label}: {prereq_text} - {diagnosis}"
                )
                failures.append(failure_msg)

                missing_atoms = _collect_missing_atoms(
                    prereq_expr, prior_courses, prior_uoc
                )
                for atom in missing_atoms:
                    if atom == "__ONEOF__":
                        key = ("prereq", current.code)
                        next_idx = oneof_index_by_course.get(key, 0) + 1
                        oneof_index_by_course[key] = next_idx
                        atom = f"oneof{next_idx}"
                        detail = _expr_oneof_label(prereq_expr)
                        findings.append(
                            {
                                "failure_id": f"prereq:{current.code}>{atom}",
                                "kind": "prereq",
                                "message": f"{course_label}: {detail}",
                                "overrideable": True,
                                "accepted": False,
                            }
                        )
                        continue

                    findings.append(
                        {
                            "failure_id": f"prereq:{current.code}>{atom}",
                            "kind": "prereq",
                            "message": (
                                f"{course_label}: missing {atom} (has {prior_uoc}uoc)"
                                if _MISSING_UOC_ATOM_RE.fullmatch(atom)
                                else f"{course_label}: missing {atom}"
                            ),
                            "overrideable": True,
                            "accepted": False,
                        }
                    )

            if coreq_expr is not None:
                coreq_courses = Counter(coreq_base)
                if coreq_courses[current.code] > 0:
                    coreq_courses[current.code] -= 1
                    if coreq_courses[current.code] <= 0:
                        del coreq_courses[current.code]
                coreq_uoc = prior_uoc + group_uoc - current.uoc

                if not evaluate_expression(coreq_expr, coreq_courses, coreq_uoc):
                    diagnosis = diagnose_expression(
                        coreq_expr, coreq_courses, coreq_uoc
                    )
                    coreq_text = expression_to_text(coreq_expr)
                    failure_msg = (
                        f"[Corequisite] {course_label}: {coreq_text} - {diagnosis}"
                    )
                    failures.append(failure_msg)

                    missing_atoms = _collect_missing_atoms(
                        coreq_expr, coreq_courses, coreq_uoc
                    )
                    for atom in missing_atoms:
                        if atom == "__ONEOF__":
                            key = ("coreq", current.code)
                            next_idx = oneof_index_by_course.get(key, 0) + 1
                            oneof_index_by_course[key] = next_idx
                            atom = f"oneof{next_idx}"
                            detail = _expr_oneof_label(coreq_expr)
                            findings.append(
                                {
                                    "failure_id": f"coreq:{current.code}>={atom}",
                                    "kind": "coreq",
                                    "message": f"{course_label}: {detail}",
                                    "overrideable": True,
                                    "accepted": False,
                                }
                            )
                            continue

                        findings.append(
                            {
                                "failure_id": f"coreq:{current.code}>={atom}",
                                "kind": "coreq",
                                "message": (
                                    f"{course_label}: missing {atom} (has {coreq_uoc}uoc)"
                                    if _MISSING_UOC_ATOM_RE.fullmatch(atom)
                                    else f"{course_label}: missing {atom}"
                                ),
                                "overrideable": True,
                                "accepted": False,
                            }
                        )

        prior_courses.update(group_counter)
        prior_uoc += group_uoc
        group_start = group_end

    return failures, unsupported, findings, warnings


def extract_scheduled_courses(
    plan_data: dict[str, Any],
    *,
    catalogue: Catalogue | None = None,
    career: str | None = None,
) -> list[ScheduledPlanCourse]:
    """Extract, normalize, and sort planned courses chronologically.

    Args:
        plan_data: Parsed plan JSON object.
        catalogue: Optional catalogue used to resolve course metadata.

    Returns:
        List of normalized scheduled course records sorted by year, period, and
        within-period course order.
    """
    courses_value = plan_data.get("courses")
    if not isinstance(courses_value, list):
        raise RuleValidationError(
            "plan.courses", "plan JSON must contain a 'courses' array"
        )

    scheduled_courses: list[ScheduledPlanCourse] = []
    for idx, course in enumerate(cast(list[object], courses_value)):
        if not isinstance(course, dict):
            raise RuleValidationError(
                f"plan.courses[{idx}]", "course entry must be an object"
            )

        course_record = cast(PlanCourseRecord, course)

        code = course_record.get("code")
        if not isinstance(code, str) or not code.strip():
            continue

        year = _parse_int_like(course_record.get("year"), f"plan.courses[{idx}].year")

        period = course_record.get("period")
        if not isinstance(period, str) or not period.strip():
            raise RuleValidationError(
                f"plan.courses[{idx}].period", "must be a non-empty string"
            )
        period_rank = _period_rank(period)
        if period_rank is None:
            raise RuleValidationError(
                f"plan.courses[{idx}].period",
                f"unsupported teaching period '{period}'",
            )

        course_n = course_record.get("course_n")
        if not isinstance(course_n, str):
            course_n = ""

        normalized_code = code.strip().upper()
        catalogue_entry: CatalogueEntry | None = None
        if catalogue is not None:
            try:
                catalogue_entry = _select_catalogue_entry(
                    normalized_code,
                    catalogue,
                    career=career,
                )
            except ValueError as exc:
                raise RuleValidationError(f"plan.courses[{idx}].code", str(exc)) from exc

            if career is not None and catalogue_entry is None:
                raise RuleValidationError(
                    f"plan.courses[{idx}].code",
                    f"Catalogue course '{normalized_code}' was not found for career '{career}'",
                )

        prerequisites: str
        if catalogue_entry is not None:
            uoc = catalogue_entry.uoc
            prerequisites = catalogue_entry.prerequisites
        else:
            uoc = _parse_int_like(course_record.get("uoc"), f"plan.courses[{idx}].uoc")

            raw_prerequisites = course_record.get("prerequisites")
            prerequisites = raw_prerequisites if isinstance(raw_prerequisites, str) else ""

        scheduled_courses.append(
            ScheduledPlanCourse(
                index=idx,
                code=normalized_code,
                year=year,
                period=period.strip(),
                period_rank=period_rank,
                course_rank=_course_rank(course_n),
                uoc=uoc,
                prerequisites=prerequisites,
            )
        )

    scheduled_courses.sort(
        key=lambda c: (
            c.year,
            c.period_rank,
            c.course_rank,
            c.index,
        )
    )
    return scheduled_courses


def validate_plan_prerequisites(
    plan_data: dict[str, Any],
    *,
    catalogue: Catalogue | None = None,
    career: str | None = None,
) -> tuple[list[str], list[str]]:
    """Validate prerequisite and corequisite expressions for all plan courses.

    Returns:
        A tuple ``(failures, unsupported)`` where:
        - ``failures`` contains unmet prerequisite/corequisite diagnostics.
        - ``unsupported`` contains expressions that could not be parsed.
    """
    courses = extract_scheduled_courses(plan_data, catalogue=catalogue, career=career)
    return validate_scheduled_prerequisites(courses)


def validate_plan_prerequisites_detailed(
    plan_data: dict[str, Any],
    *,
    catalogue: Catalogue | None = None,
    career: str | None = None,
) -> tuple[list[str], list[str], list[ValidationFinding], list[ValidationWarning]]:
    """Detailed schedule-aware prerequisite validation with structured output."""

    courses = extract_scheduled_courses(plan_data, catalogue=catalogue, career=career)
    return validate_scheduled_prerequisites_detailed(courses)


def _is_course_code(value: Any) -> bool:
    """Return ``True`` when value is a non-empty course code string."""
    return isinstance(value, str) and bool(value.strip())


def _normalize_course_code(value: str) -> str:
    return value.strip().upper()


def normalize_clause(clause: Any) -> RuleExpr:
    """Normalize one rule clause into canonical expression format.

    Args:
        clause: Raw clause value from rules JSON.

    Returns:
        Canonical rule expression.
    """
    if _is_course_code(clause):
        return _normalize_course_code(cast(str, clause))

    if isinstance(clause, list):
        raise RuleValidationError(
            "<clause>",
            "legacy array clauses are no longer supported; use a course string or {'and'/'or': [...]}",
        )

    if isinstance(clause, dict):
        operator_clause = dict(cast(dict[str, Any], clause))
        operator_clause.pop("id", None)
        keys = set(operator_clause.keys())

        if keys == {"min", "from"}:
            min_count = operator_clause["min"]
            from_value = operator_clause["from"]
            if not isinstance(min_count, int) or min_count < 1:
                raise RuleValidationError(
                    "<clause>.min", "'min' must be a positive integer"
                )
            if not isinstance(from_value, list):
                raise RuleValidationError("<clause>.from", "'from' must be an array")
            from_courses = cast(list[object], from_value)
            if len(from_courses) < min_count:
                raise RuleValidationError(
                    "<clause>",
                    f"'from' has {len(from_courses)} options but 'min' is {min_count}",
                )
            return {
                "min": min_count,
                "from": [normalize_clause(child) for child in from_courses],
            }

        if len(operator_clause) != 1:
            raise RuleValidationError(
                "<clause>",
                "operator clause must contain exactly one key (or 'min' + 'from')",
            )
        op = next(iter(operator_clause.keys()))
        if op not in ("and", "or"):
            raise RuleValidationError("<clause>", f"unsupported operator '{op}'")

        children_value = operator_clause[op]
        if not isinstance(children_value, list):
            raise RuleValidationError("<clause>", f"'{op}' value must be an array")
        children = cast(list[object], children_value)
        if len(children) < 2:
            raise RuleValidationError(
                "<clause>", f"'{op}' must contain at least 2 child expressions"
            )

        return {op: [normalize_clause(child) for child in children]}

    raise RuleValidationError("<clause>", "clause must be a string or operator object")


def normalize_rules_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return canonicalized copy of full rules configuration.

    Args:
        config: Raw rules configuration JSON object.

    Returns:
        Canonicalized rules configuration with schemaVersion set.

    Canonical clause grammar:
    - leaf: "COURSE1234"
    - node: {"and": [expr, expr, ...]} or {"or": [expr, expr, ...]}
    """
    data = deepcopy(config)
    if "required" not in data or not isinstance(data["required"], dict):
        raise RuleValidationError(
            "required", "config must contain an object field named 'required'"
        )

    normalized_required: dict[str, list[RuleExpr]] = {}
    required_levels = cast(dict[str, Any], data["required"])
    for level_name, clauses in required_levels.items():
        if not isinstance(clauses, list):
            raise RuleValidationError(
                f"required.{level_name}",
                "level requirements must be an array of clauses",
            )
        normalized_required[level_name] = [
            normalize_clause(clause) for clause in cast(list[object], clauses)
        ]

    data["required"] = normalized_required
    data["schemaVersion"] = 2
    if _CLAUSE_META_KEY in config:
        data[_CLAUSE_META_KEY] = deepcopy(config[_CLAUSE_META_KEY])
    return data


def validate_canonical_expression(expr: RuleExpr, path: str = "<clause>") -> None:
    """Validate structural correctness of one canonical expression.

    Args:
        expr: Canonical expression to validate.
        path: Field path for validation error messages.
    """
    if _is_course_code(expr):
        return

    if not isinstance(expr, dict):
        raise RuleValidationError(
            path, "expression must be a course string or operator object"
        )

    keys = set(expr.keys())

    if keys == {"uoc"}:
        threshold = expr["uoc"]
        if not isinstance(threshold, int) or threshold < 0:
            raise RuleValidationError(
                f"{path}.uoc", "'uoc' must be a non-negative integer"
            )
        return

    if keys == {"min", "from"}:
        min_count = expr["min"]
        from_value = expr["from"]
        if not isinstance(min_count, int) or min_count < 1:
            raise RuleValidationError(f"{path}.min", "'min' must be a positive integer")
        if not isinstance(from_value, list):
            raise RuleValidationError(f"{path}.from", "'from' must be an array")
        from_courses = cast(list[RuleExpr], from_value)
        if len(from_courses) < min_count:
            raise RuleValidationError(
                path, f"'from' has {len(from_courses)} options but 'min' is {min_count}"
            )
        for idx, child in enumerate(from_courses):
            validate_canonical_expression(child, f"{path}.from[{idx}]")
        return

    if len(keys) != 1:
        raise RuleValidationError(
            path, "operator object must contain exactly one key (or 'min' + 'from')"
        )

    op = next(iter(keys))
    if op not in ("and", "or"):
        raise RuleValidationError(path, f"unknown operator '{op}'")

    children_value = expr[op]
    if not isinstance(children_value, list):
        raise RuleValidationError(path, f"'{op}' must be an array")
    children = cast(list[RuleExpr], children_value)
    if len(children) < 2:
        raise RuleValidationError(
            path, f"'{op}' must contain at least 2 child expressions"
        )

    for idx, child in enumerate(children):
        validate_canonical_expression(child, f"{path}.{op}[{idx}]")


def evaluate_expression(
    expr: RuleExpr,
    completed_courses: Counter[str],
    completed_uoc: int = 0,
) -> bool:
    """Evaluate canonical expression against completed course history.

    Args:
        expr: Canonical expression to evaluate.
        completed_courses: Counter of completed course codes.
        completed_uoc: Total completed units of credit.

    Returns:
        True when expression is satisfied, otherwise False.
    """
    if _is_course_code(expr):
        return expr in completed_courses

    if not isinstance(expr, dict):
        raise RuleValidationError("<eval>", "invalid expression shape")

    node = expr

    if set(node.keys()) == {"uoc"}:
        threshold = node["uoc"]
        if not isinstance(threshold, int) or threshold < 0:
            raise RuleValidationError(
                "<eval>.uoc", "'uoc' must be a non-negative integer"
            )
        return completed_uoc >= threshold

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        satisfied = sum(
            completed_courses[cast(str, child)]
            if _is_course_code(child)
            else int(evaluate_expression(child, completed_courses, completed_uoc))
            for child in from_exprs
        )
        return satisfied >= min_count

    if len(node) != 1:
        raise RuleValidationError("<eval>", "invalid expression shape")

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])
    results = [
        evaluate_expression(child, completed_courses, completed_uoc)
        for child in children
    ]

    if op == "and":
        return all(results)
    if op == "or":
        return any(results)

    raise RuleValidationError("<eval>", f"unknown operator '{op}'")


def evaluate_level(
    level_clauses: list[RuleExpr], completed_courses: Counter[str]
) -> bool:
    """Evaluate one requirement level using all-clauses semantics.

    Args:
        level_clauses: Canonical clauses for one level.
        completed_courses: Counter of completed course codes.

    Returns:
        True when all clauses are satisfied.
    """
    return all(
        evaluate_expression(clause, completed_courses) for clause in level_clauses
    )


def evaluate_required(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> dict[str, bool]:
    """Evaluate all required levels for a completed-course history.

    Args:
        normalized_config: Canonical rules configuration.
        completed_courses: Counter of completed course codes.

    Returns:
        Mapping of level name to pass/fail status.
    """
    required = normalized_config.get("required", {})
    if not isinstance(required, dict):
        raise RuleValidationError("required", "required must be an object")

    cfg_id = id(normalized_config)
    required_levels = cast(dict[str, Any], required)

    if cfg_id not in _REQUIRED_VALIDATION_CACHE:
        # Structural rule validation is expensive but the rules object is stable
        # for a whole planner run, so validate it once and reuse thereafter.
        for level_name, clauses in required_levels.items():
            if not isinstance(clauses, list):
                raise RuleValidationError(
                    f"required.{level_name}", "level requirements must be an array"
                )
            level_clauses = cast(list[RuleExpr], clauses)
            for idx, clause in enumerate(level_clauses):
                validate_canonical_expression(clause, f"required.{level_name}[{idx}]")
        _REQUIRED_VALIDATION_CACHE.add(cfg_id)

    result: dict[str, bool] = {}
    for level_name, clauses in required_levels.items():
        level_clauses = cast(list[RuleExpr], clauses)
        result[level_name] = evaluate_level(level_clauses, completed_courses)

    return result


def validate_rules_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate full degree rules configuration.

    Args:
        config: Raw rules configuration JSON object.

    Returns:
        Canonical validated rules configuration.
    """
    prepared = deepcopy(config)
    _extract_clause_metadata(prepared)
    normalized = normalize_rules_config(prepared)

    required = normalized.get("required", {})
    if not isinstance(required, dict):
        raise RuleValidationError("required", "required must be an object")

    for level_name, clauses in cast(dict[str, Any], required).items():
        if not isinstance(clauses, list):
            raise RuleValidationError(
                f"required.{level_name}", "level requirements must be an array"
            )
        level_clauses = cast(list[RuleExpr], clauses)
        if len(level_clauses) == 0:
            raise RuleValidationError(
                f"required.{level_name}", "level must contain at least one clause"
            )

        for idx, clause in enumerate(level_clauses):
            validate_canonical_expression(clause, f"required.{level_name}[{idx}]")

    return normalized


def expression_to_text(expr: RuleExpr, parent_op: str | None = None) -> str:
    """Render canonical expression as readable infix text.

    Args:
        expr: Canonical expression to render.
        parent_op: Parent boolean operator used for grouping.

    Returns:
        Human-readable expression string.
    """
    if _is_course_code(expr):
        return cast(str, expr)

    node = cast(dict[str, Any], expr)

    if set(node.keys()) == {"uoc"}:
        threshold = cast(int, node["uoc"])
        return f"{threshold} UOC"

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        total = len(from_exprs)
        items = ", ".join(expression_to_text(child) for child in from_exprs)
        if min_count == total:
            return f"ALL OF ({items})"
        return f"AT LEAST {min_count} OF ({items})"

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])
    joiner = f" {op.upper()} "
    text = joiner.join(expression_to_text(child, op) for child in children)

    if parent_op is not None and parent_op != op:
        return f"({text})"
    return text


def render_rules_human(config: dict[str, Any]) -> str:
    """Render validated rules config as human-friendly summary text.

    Args:
        config: Canonical rules configuration.

    Returns:
        Multi-line summary string.
    """
    lines: list[str] = []
    schema_version = config.get("schemaVersion", 2)
    lines.append(f"Degree rules (schemaVersion {schema_version})")

    required = cast(dict[str, list[RuleExpr]], config["required"])
    for level_name, clauses in required.items():
        lines.append("")
        lines.append(f"{level_name} ({len(clauses)} clauses)")
        for idx, clause in enumerate(clauses, start=1):
            lines.append(f"  {idx}. {expression_to_text(clause)}")

    return "\n".join(lines)


def diagnose_expression(
    expr: RuleExpr,
    completed_courses: Counter[str],
    completed_uoc: int = 0,
) -> str:
    """Explain why a failed expression is not currently satisfied.

    Args:
        expr: Canonical expression to diagnose.
        completed_courses: Counter of completed course codes.
        completed_uoc: Total completed units of credit.

    Returns:
        Human-readable diagnosis string.
    """
    if _is_course_code(expr):
        return f"missing {expr}"

    node = cast(dict[str, Any], expr)

    if set(node.keys()) == {"uoc"}:
        threshold = cast(int, node["uoc"])
        needed = max(0, threshold - completed_uoc)
        return f"need {needed} more UoC (have {completed_uoc}, need {threshold})"

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        satisfied = sum(
            completed_courses[cast(str, child)]
            if _is_course_code(child)
            else int(evaluate_expression(child, completed_courses, completed_uoc))
            for child in from_exprs
        )
        needed = min_count - satisfied
        options = ", ".join(expression_to_text(e) for e in from_exprs)
        return f"need {needed} more from ({options})"

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])

    if op == "and":
        failed_diagnoses = [
            diagnose_expression(child, completed_courses, completed_uoc)
            for child in children
            if not evaluate_expression(child, completed_courses, completed_uoc)
        ]
        return "; ".join(failed_diagnoses)

    if op == "or":
        options = ", ".join(expression_to_text(child) for child in children)
        return f"none of the alternatives were completed: ({options})"

    return f"unknown operator '{op}'"


def report_plan(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> list[str]:
    """Build clause-level failure report for one plan.

    Args:
        normalized_config: Canonical rules configuration.
        completed_courses: Counter of completed course codes.

    Returns:
        List of human-readable failure strings.
    """
    failures, _findings, _warnings = report_plan_detailed(
        normalized_config, completed_courses
    )
    return failures


def _emit_atomic_rule_findings(
    clause: RuleExpr,
    completed_courses: Counter[str],
    level_name: str,
    clause_index: int,
) -> list[ValidationFinding]:
    if _is_course_code(clause):
        course = cast(str, clause)
        if completed_courses[course] > 0:
            return []
        return [
            {
                "failure_id": f"rule:{course}",
                "kind": "rule",
                "message": f"[{level_name}] clause {clause_index}: missing {course}",
                "overrideable": True,
                "accepted": False,
            }
        ]

    node = cast(dict[str, Any], clause)
    if set(node.keys()) == {"and"}:
        findings: list[ValidationFinding] = []
        for child in cast(list[RuleExpr], node["and"]):
            findings.extend(
                _emit_atomic_rule_findings(
                    child, completed_courses, level_name, clause_index
                )
            )
        return findings

    return []


def report_plan_detailed(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> tuple[list[str], list[ValidationFinding], list[ValidationWarning]]:
    """Build legacy and structured rule findings for one plan."""

    failures: list[str] = []
    findings: list[ValidationFinding] = []
    warnings: list[ValidationWarning] = []
    clause_meta_map = _as_clause_meta_map(normalized_config)

    required = cast(dict[str, Any], normalized_config.get("required", {}))
    for level_name, clauses in required.items():
        level_clauses = cast(list[RuleExpr], clauses)
        for idx, clause in enumerate(level_clauses, start=1):
            if evaluate_expression(clause, completed_courses):
                continue

            rule_text = expression_to_text(clause)
            diagnosis = diagnose_expression(clause, completed_courses)
            failures.append(
                f"[{level_name}] clause {idx}: {rule_text} \u2014 {diagnosis}"
            )

            atomic_findings = _emit_atomic_rule_findings(
                clause, completed_courses, level_name, idx
            )
            if atomic_findings:
                findings.extend(atomic_findings)
                continue

            meta_key = f"{level_name}\u241f{idx - 1}"
            meta = clause_meta_map.get(meta_key, {})
            subset_id = meta.get("subset_id")
            overrideable = bool(meta.get("overrideable", False)) if meta else False
            reason = meta.get("non_overrideable_reason") if meta else None

            if isinstance(subset_id, str) and subset_id:
                failure_id = f"rule:{subset_id}"
            else:
                failure_id = f"rule:unnamed:{_slug_level_name(level_name)}:{idx}"
                if reason is None:
                    reason = "missing_rule_id"
                warnings.append(
                    _make_warning(
                        "missing_rule_id",
                        "Subset clause has no 'id' and cannot be overridden",
                        failure_id=failure_id,
                        location=f"required.{level_name}[{idx - 1}]",
                    )
                )

            finding: ValidationFinding = {
                "failure_id": failure_id,
                "kind": "rule",
                "message": f"[{level_name}] clause {idx}: {rule_text} \u2014 {diagnosis}",
                "overrideable": overrideable,
                "accepted": False,
            }
            if not overrideable and isinstance(reason, str) and reason:
                finding["non_overrideable_reason"] = reason
            findings.append(finding)

    return failures, findings, warnings


def extract_completed_courses(plan_data: dict[str, Any]) -> Counter[str]:
    """Extract completed course counts from plan JSON data.

    Args:
        plan_data: Parsed plan JSON object.

    Returns:
        Counter keyed by course code.
    """
    courses_value = plan_data.get("courses")
    if not isinstance(courses_value, list):
        raise RuleValidationError(
            "plan.courses", "plan JSON must contain a 'courses' array"
        )

    completed_courses: Counter[str] = Counter()
    for idx, course in enumerate(cast(list[object], courses_value)):
        if not isinstance(course, dict):
            raise RuleValidationError(
                f"plan.courses[{idx}]", "course entry must be an object"
            )

        course_record = cast(PlanCourseRecord, course)
        code = course_record.get("code")
        if isinstance(code, str) and code.strip():
            completed_courses[_normalize_course_code(code)] += 1

    return completed_courses


def _override_file_for_plan(plan_file: Path) -> Path:
    return plan_file.with_name(f"{plan_file.stem}.degree_rules_overrides.json")


def _load_overrides(plan_file: Path) -> tuple[set[str], list[ValidationWarning]]:
    override_file = _override_file_for_plan(plan_file)
    if not override_file.is_file():
        return set(), []

    try:
        with open(override_file, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        warning = _make_warning(
            "invalid_overrides_file",
            f"Could not parse overrides JSON: {exc}",
            location=str(override_file),
        )
        return set(), [warning]

    if not isinstance(raw, dict):
        warning = _make_warning(
            "invalid_overrides_file",
            "Overrides file must be a JSON object",
            location=str(override_file),
        )
        return set(), [warning]

    raw_dict = cast(dict[str, object], raw)
    overrides_raw = raw_dict.get("overrides", [])
    if not isinstance(overrides_raw, list):
        warning = _make_warning(
            "invalid_overrides_file",
            "Overrides file field 'overrides' must be an array",
            location=str(override_file),
        )
        return set(), [warning]

    ids: set[str] = set()
    warnings: list[ValidationWarning] = []
    for idx, item in enumerate(cast(list[object], overrides_raw)):
        if not isinstance(item, dict):
            warnings.append(
                _make_warning(
                    "invalid_override_entry",
                    "Override entry must be an object",
                    location=f"{override_file}:overrides[{idx}]",
                )
            )
            continue

        item_dict = cast(dict[str, object], item)
        failure_id = item_dict.get("failure_id")
        if not isinstance(failure_id, str) or not failure_id.strip():
            warnings.append(
                _make_warning(
                    "invalid_override_entry",
                    "Override entry requires non-empty 'failure_id'",
                    location=f"{override_file}:overrides[{idx}]",
                )
            )
            continue

        normalized = failure_id.strip()
        if normalized in ids:
            warnings.append(
                _make_warning(
                    "duplicate_override_id",
                    f"Duplicate override id '{normalized}'",
                    failure_id=normalized,
                    location=f"{override_file}:overrides[{idx}]",
                )
            )
            continue
        ids.add(normalized)

    return ids, warnings


def _write_new_overrides(
    plan_file: Path, new_ids: tuple[str, ...]
) -> list[ValidationWarning]:
    """Append new override entries to the sidecar file, creating it if needed.

    Returns any warnings produced (e.g. duplicate entries already present).
    """
    from datetime import datetime, timezone

    override_file = _override_file_for_plan(plan_file)
    existing: dict[str, object] = {}
    if override_file.is_file():
        try:
            with open(override_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                existing = cast(dict[str, object], raw)
        except (json.JSONDecodeError, OSError):
            pass

    overrides_raw = existing.get("overrides", [])
    overrides: list[dict[str, object]] = (
        cast(list[dict[str, object]], overrides_raw)
        if isinstance(overrides_raw, list)
        else []
    )

    existing_ids: set[str] = {str(e.get("failure_id", "")) for e in overrides}

    warnings: list[ValidationWarning] = []
    now = datetime.now(timezone.utc).isoformat()
    for fid in new_ids:
        if fid in existing_ids:
            warnings.append(
                _make_warning(
                    "duplicate_override_id",
                    f"Override id '{fid}' already present in sidecar file; skipping",
                    failure_id=fid,
                    location=str(override_file),
                )
            )
            continue
        overrides.append({"failure_id": fid, "added_at_utc": now})
        existing_ids.add(fid)

    existing["overrides"] = overrides
    override_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return warnings


def _apply_overrides_to_findings(
    findings: list[ValidationFinding],
    override_ids: set[str],
) -> list[ValidationWarning]:
    warnings: list[ValidationWarning] = []
    finding_by_id: dict[str, ValidationFinding] = {}
    for finding in findings:
        finding_by_id[finding["failure_id"]] = finding

    for override_id in sorted(override_ids):
        matched = finding_by_id.get(override_id)
        if matched is None:
            warnings.append(
                _make_warning(
                    "unknown_override_id",
                    f"Override id '{override_id}' did not match any finding",
                    failure_id=override_id,
                )
            )
            continue

        overrideable = bool(matched.get("overrideable", False))
        if not overrideable:
            warnings.append(
                _make_warning(
                    "override_not_allowed",
                    f"Override id '{override_id}' targets a non-overrideable finding",
                    failure_id=override_id,
                )
            )
            continue

        matched["accepted"] = True

    return warnings


def _compute_plan_status(findings: list[ValidationFinding]) -> tuple[str, bool]:
    if not findings:
        return "PASS", True

    for finding in findings:
        accepted = bool(finding.get("accepted", False))
        if accepted:
            continue
        return "FAIL", False

    return "ACCEPTED", True


def run_rules_command(
    command: RulesCommand,
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Execute rules validation/reporting without any CLI parser dependency."""

    logger = logging.getLogger(__name__)

    try:
        with open(command.rules_file, "r", encoding="utf-8") as handle:
            raw_config = json.load(handle)
    except FileNotFoundError:
        print(f"Error: file not found: {command.rules_file}", file=stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON: {exc}", file=stderr)
        return 1

    if not isinstance(raw_config, dict):
        print("Error: top-level JSON must be an object", file=stderr)
        return 1

    try:
        rule_warnings = _extract_clause_metadata(cast(dict[str, Any], raw_config))
        validated = validate_rules_config(cast(dict[str, Any], raw_config))
    except RuleValidationError as exc:
        print(f"Validation failed: {exc}", file=stderr)
        return 1

    logger.info("Rules validation: OK")
    logger.debug("Rules:\n" + render_rules_human(validated))

    if command.json_output:
        print("\nCanonical JSON", file=stdout)
        print(json.dumps(validated, indent=2, sort_keys=False), file=stdout)

    if rule_warnings and command.plan_file is None:
        print(f"Rules validation warnings ({len(rule_warnings)}):", file=stdout)
        for warning in rule_warnings:
            print(f"  {_warning_message(warning)}", file=stdout)

    if command.plan_file is not None:
        try:
            with open(command.plan_file, "r", encoding="utf-8") as fh:
                plan_data = json.load(fh)
        except FileNotFoundError:
            print(f"Error: plan file not found: {command.plan_file}", file=stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"Error: invalid plan JSON: {exc}", file=stderr)
            return 1

        if not isinstance(plan_data, dict):
            print(
                "Error: plan JSON must be an object with a 'courses' array",
                file=stderr,
            )
            return 1

        catalogue: Catalogue | None = None
        rules_career: str | None = None
        if command.catalogue_file is not None:
            try:
                from transitionchecker.planner_engine import load_catalogue

                catalogue = load_catalogue(command.catalogue_file)
                rules_career = resolve_rules_career(validated)
                ensure_catalogue_has_career(catalogue, rules_career)
            except FileNotFoundError:
                print(
                    f"Error: catalogue file not found: {command.catalogue_file}",
                    file=stderr,
                )
                return 1
            except json.JSONDecodeError as exc:
                print(f"Error: invalid catalogue JSON: {exc}", file=stderr)
                return 1
            except ValueError as exc:
                print(f"Error: {exc}", file=stderr)
                return 1

        try:
            completed_courses = extract_completed_courses(
                cast(dict[str, Any], plan_data)
            )
            (
                prereq_failures,
                prereq_unsupported,
                prereq_findings,
                prereq_warnings,
            ) = validate_plan_prerequisites_detailed(
                cast(dict[str, Any], plan_data),
                catalogue=catalogue,
                career=rules_career,
            )
        except RuleValidationError as exc:
            print(f"Error: {exc}", file=stderr)
            return 1

        rule_failures, rule_findings, generated_rule_warnings = report_plan_detailed(
            validated,
            completed_courses,
        )

        all_findings = [*rule_findings, *prereq_findings]
        warnings: list[ValidationWarning] = [
            *rule_warnings,
            *generated_rule_warnings,
            *prereq_warnings,
        ]

        override_ids, override_file_warnings = _load_overrides(command.plan_file)
        if command.add_overrides:
            override_file_warnings.extend(
                _write_new_overrides(command.plan_file, command.add_overrides)
            )
            override_ids.update(command.add_overrides)
        warnings.extend(override_file_warnings)
        warnings.extend(_apply_overrides_to_findings(all_findings, override_ids))

        status, is_valid = _compute_plan_status(all_findings)

        if command.plan_report_json:
            report_payload: dict[str, Any] = {
                "status": status,
                "valid": is_valid,
                "rule_failures": rule_failures,
                "prerequisite_failures": prereq_failures,
                "unsupported_prerequisites": prereq_unsupported,
                "findings": all_findings,
                "warnings": warnings,
            }
            print(json.dumps(report_payload, indent=2), file=stdout)
            return 0 if is_valid else 1

        active_findings = [f for f in all_findings if not f["accepted"]]
        active_rule = [f for f in active_findings if f["kind"].startswith("rule")]
        active_prereq = [
            f
            for f in active_findings
            if f["kind"].startswith("prereq") or f["kind"].startswith("coreq")
        ]
        active_unsup = [f for f in active_findings if f["kind"] == "unsupported_syntax"]

        if active_rule:
            print(
                f"Plan does not satisfy {len(active_rule)} degree rule(s):",
                file=stdout,
            )
            for f in active_rule:
                print(f"  [{f['failure_id']}] {f['message']}", file=stdout)

        if active_prereq:
            print(
                f"Plan has {len(active_prereq)} prerequisite/corequisite violation(s):",
                file=stdout,
            )
            for f in active_prereq:
                print(f"  [{f['failure_id']}] {f['message']}", file=stdout)

        if active_unsup:
            print(
                f"Plan has {len(active_unsup)} unsupported syntax expression(s):",
                file=stdout,
            )
            for f in active_unsup:
                print(f"  [{f['failure_id']}] {f['message']}", file=stdout)

        if warnings:
            print(f"Plan has {len(warnings)} warning(s):", file=stdout)
            for warning in warnings:
                print(f"  {_warning_message(warning)}", file=stdout)

        if is_valid:
            if status == "ACCEPTED":
                print("Plan status: ACCEPTED", file=stdout)
            else:
                print(
                    "Plan satisfies degree rules and prerequisite/corequisite checks.",
                    file=stdout,
                )
            return 0
        return 1

    if command.render_rules_text:
        print(file=stdout)
        print(render_rules_human(validated), file=stdout)
    return 0

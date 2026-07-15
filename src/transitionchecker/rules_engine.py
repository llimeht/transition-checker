from __future__ import annotations

"""Validate degree rules and schedule-aware prerequisite compliance."""

import json
import logging
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NotRequired, TextIO, TypedDict, cast

from transitionchecker.core import is_nonstandard_period, period_rank
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
from transitionchecker.erg_parser import (
    ErgExpr,
    match_erg_pattern,
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


class CourseEquivalence(TypedDict, total=False):
    """A directional course equivalence used during rule/prereq checking.

    If a student holds ``held``, it also counts as holding ``equivalent_to``
    for the purposes of expression evaluation.  The mapping is applied at
    evaluation time only; the raw plan data is never mutated.
    """

    held: str  # required
    equivalent_to: str  # required
    reason: str  # optional


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
    erg_expr: ErgExpr | None = field(default=None, compare=False, hash=False, repr=False)
    """Pre-parsed :data:`~transitionchecker.erg_parser.ErgExpr` from the
    catalogue.  When set, the validator bypasses ``parse_prerequisite_field``
    entirely and evaluates this expression directly."""


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
    plan_report_allocations: bool = False
    render_rules_text: bool = False
    show_plan_warnings: bool = False
    add_overrides: tuple[str, ...] = ()


_CLAUSE_META_KEY = "_required_clause_meta"
_MISSING_UOC_ATOM_RE = re.compile(r"\d+\s*uoc", re.IGNORECASE)
_RPL_KEY = "rpl"
_DOUBLE_COUNTED_KEY = "double-counted"
_SHARED_COURSES_KEY = "shared-courses"
_OVER_DOUBLE_COUNT_LIMIT_KEY = "over-double-count-limit"


def _is_placeholder_min_from(node: dict[str, Any]) -> bool:
    return set(node.keys()) == {"min", "from", "placeholder"}


def _is_placeholder_or(node: dict[str, Any]) -> bool:
    return set(node.keys()) == {"or", "placeholder"}


# ---------------------------------------------------------------------------
# Unified ErgExpr evaluator
# ---------------------------------------------------------------------------

def evaluate_erg_expression(
    expr: ErgExpr,
    prior_courses: Counter[str],
    coreq_courses: Counter[str],
    prior_uoc: int,
    course_uoc: dict[str, int] | None = None,
) -> bool:
    """Evaluate a structured :data:`ErgExpr` tree against the student's history.

    Parameters
    ----------
    expr:
        The expression to evaluate.
    prior_courses:
        Courses completed *before* the current teaching period, after
        equivalences and RPL have been applied (a ``Counter``).
    coreq_courses:
        ``prior_courses`` extended with courses in the *current* period, after
        equivalences and RPL.  Used only for ``coreq``/``coreq_pattern`` leaves.
    prior_uoc:
        Total UoC from all completed prior periods.
    course_uoc:
        Optional mapping of course code → UoC, used to compute filtered UoC
        sums for ``{"uoc": N, "restriction": P}`` leaves.  When absent, the
        restriction is evaluated against *prior_uoc* directly (conservative).
    """
    # ── Combinators ──────────────────────────────────────────────────────────
    if "and" in expr:
        return all(
            evaluate_erg_expression(child, prior_courses, coreq_courses, prior_uoc, course_uoc)
            for child in cast(list[ErgExpr], expr["and"])
        )
    if "or" in expr:
        return any(
            evaluate_erg_expression(child, prior_courses, coreq_courses, prior_uoc, course_uoc)
            for child in cast(list[ErgExpr], expr["or"])
        )

    # ── Condition (ignorable) ─────────────────────────────────────────────────
    if "condition" in expr:
        return True

    # ── Course prerequisite ───────────────────────────────────────────────────
    if "prereq" in expr:
        code = cast(str, expr["prereq"])
        return code in prior_courses

    # ── Course corequisite ────────────────────────────────────────────────────
    if "coreq" in expr:
        code = cast(str, expr["coreq"])
        return code in coreq_courses

    # ── Pattern prerequisite ──────────────────────────────────────────────────
    if "prereq_pattern" in expr:
        pattern = cast(str, expr["prereq_pattern"])
        return any(match_erg_pattern(pattern, c) for c in prior_courses)

    # ── Pattern corequisite ───────────────────────────────────────────────────
    if "coreq_pattern" in expr:
        pattern = cast(str, expr["coreq_pattern"])
        return any(match_erg_pattern(pattern, c) for c in coreq_courses)

    # ── UoC maturity ──────────────────────────────────────────────────────────
    if "uoc" in expr:
        threshold = cast(int, expr["uoc"])
        restriction = cast(str, expr.get("restriction", ""))
        if restriction and course_uoc is not None:
            total = sum(
                uoc
                for code, uoc in course_uoc.items()
                if code in prior_courses and match_erg_pattern(restriction, code)
            )
            return total >= threshold
        # No restriction or no UoC map — fall back to total prior UoC
        return prior_uoc >= threshold

    # ── min/from (from text-parser path) ────────────────────────────────────
    if "min" in expr and "from" in expr:
        min_count = cast(int, expr["min"])
        satisfied = sum(
            int(evaluate_erg_expression(child, prior_courses, coreq_courses, prior_uoc, course_uoc))
            for child in cast(list[ErgExpr], expr["from"])
        )
        return satisfied >= min_count

    return False  # Unknown node shape — conservatively unsatisfied


def _validate_placeholder_code(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleValidationError(path, "'placeholder' must be a non-empty course code")

    placeholder = _normalize_course_code(value)
    if not _is_course_code(placeholder):
        raise RuleValidationError(path, "'placeholder' must be a valid course code")
    return placeholder


def _count_min_from_satisfied(
    from_exprs: list[RuleExpr],
    completed_courses: Counter[str],
    completed_uoc: int,
    placeholder: str | None = None,
) -> int:
    satisfied = sum(
        completed_courses[cast(str, child)]
        if _is_course_code(child)
        else int(evaluate_expression(child, completed_courses, completed_uoc))
        for child in from_exprs
    )
    if placeholder is None:
        return satisfied

    if any(
        _is_course_code(child) and cast(str, child) == placeholder
        for child in from_exprs
    ):
        return satisfied

    return satisfied + completed_courses[placeholder]


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
                    cast(list[object], raw_and_val)
                    if isinstance(raw_and_val, list)
                    else []
                )
                labels.append(
                    "+".join(str(c) for c in and_list[:3]) if and_list else "..."
                )
            else:
                labels.append("...")
        suffix = "|..." if len(options) > max_items else ""
        summary = f"or[{'|'.join(labels)}{suffix}]"
        placeholder = clause.get("placeholder")
        if isinstance(placeholder, str) and placeholder.strip():
            summary += f" placeholder[{placeholder.strip()}]"
        return summary
    if "min" in clause and "from" in clause:
        n = clause["min"]
        raw_from = clause.get("from")
        if isinstance(raw_from, list):
            pool = cast(list[Any], raw_from)  # type: ignore[redundant-cast]
            items = [str(c) for c in pool[:max_items]]
            suffix = "|..." if len(pool) > max_items else ""
            summary = f"min {n} from[{'|'.join(items)}{suffix}]"
            placeholder = clause.get("placeholder")
            if isinstance(placeholder, str) and placeholder.strip():
                summary += f" placeholder[{placeholder.strip()}]"
            return summary
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

    if set(node.keys()) == {"or"} or _is_placeholder_or(node):
        if evaluate_expression(expr, completed_courses, completed_uoc):
            return []
        return ["__ONEOF__"]

    return [expression_to_text(expr)]


def _collect_missing_erg_atoms(
    expr: ErgExpr,
    prior_courses: Counter[str],
    coreq_courses: Counter[str],
    prior_uoc: int,
    coreq_uoc: int,
    course_uoc: dict[str, int] | None,
) -> list[tuple[str, str]]:
    """Return ``(kind, atom_str)`` pairs for failing leaves in *expr*.

    ``kind`` is ``"prereq"`` or ``"coreq"``.  Only called when *expr* as a
    whole has already been found to fail.
    """
    if "condition" in expr:
        return []  # conditions always pass; never a failure source

    if "and" in expr:
        result: list[tuple[str, str]] = []
        for child in cast(list[ErgExpr], expr["and"]):
            if not evaluate_erg_expression(child, prior_courses, coreq_courses, prior_uoc, course_uoc):
                result.extend(_collect_missing_erg_atoms(child, prior_courses, coreq_courses, prior_uoc, coreq_uoc, course_uoc))
        return result

    if "or" in expr:
        # Whole OR failed — report as a single synthetic oneof atom
        return [("prereq", "__ONEOF__")]

    if "prereq" in expr:
        return [("prereq", cast(str, expr["prereq"]))]
    if "coreq" in expr:
        return [("coreq", cast(str, expr["coreq"]))]
    if "prereq_pattern" in expr:
        return [("prereq", cast(str, expr["prereq_pattern"]))]
    if "coreq_pattern" in expr:
        return [("coreq", cast(str, expr["coreq_pattern"]))]
    if "uoc" in expr:
        threshold = cast(int, expr["uoc"])
        return [("prereq", f"{threshold}uoc")]
    if "min" in expr and "from" in expr:
        return [("prereq", "__ONEOF__")]
    return [("prereq", "__ONEOF__")]


def validate_scheduled_prerequisites(
    courses: list[ScheduledPlanCourse],
    rpl_courses: Counter[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate prerequisite/corequisite expressions against scheduled plan order.

    Courses in the same teaching period do not satisfy prerequisites for one
    another, but they may satisfy corequisites.
    """
    failures, unsupported, _findings, _warnings = (
        validate_scheduled_prerequisites_detailed(courses, rpl_courses=rpl_courses)
    )
    return failures, unsupported  # equivalences not threaded here (legacy path)


def validate_scheduled_prerequisites_detailed(
    courses: list[ScheduledPlanCourse],
    equivalences: list[CourseEquivalence] | None = None,
    rpl_courses: Counter[str] | None = None,
) -> tuple[list[str], list[str], list[ValidationFinding], list[ValidationWarning]]:
    """Validate scheduled prerequisites and return diagnostics plus findings.

    Args:
        courses: Chronologically-sorted scheduled course records.
        equivalences: Optional list of directional course equivalences.  When a
            student holds the ``held`` code, ``equivalent_to`` is also treated as
            held during expression evaluation.  The raw accumulation counters are
            not mutated.
        rpl_courses: Optional implicitly-held courses that count for prerequisite
            and corequisite satisfaction without appearing in the plan itself.
    """
    _equivalences: list[CourseEquivalence] = (
        equivalences if equivalences is not None else []
    )
    _rpl_courses: Counter[str] = (
        Counter(rpl_courses) if rpl_courses is not None else Counter()
    )
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

        # Pre-compute the UoC map and expanded prior-courses counter once per
        # period group — both are the same for every course in the group.
        _period_course_uoc_map: dict[str, int] = {c.code: c.uoc for c in courses[:group_start]}
        _period_prior_exp = _apply_equivalences(prior_courses, _equivalences)
        _period_prior_exp.update(_rpl_courses)

        for current in group_courses:
            course_label = f"{current.code} ({current.year} {current.period})"

            if current.erg_expr is not None:
                # ── ERG-sourced: evaluate structured expression directly ────────
                _coreq_base = Counter(coreq_base)
                if _coreq_base[current.code] > 0:
                    _coreq_base[current.code] -= 1
                    if _coreq_base[current.code] <= 0:
                        del _coreq_base[current.code]
                _coreq_exp = _apply_equivalences(_coreq_base, _equivalences)
                _coreq_exp.update(_rpl_courses)
                _coreq_uoc = prior_uoc + group_uoc - current.uoc

                if not evaluate_erg_expression(
                    current.erg_expr, _period_prior_exp, _coreq_exp, prior_uoc, _period_course_uoc_map
                ):
                    missing_erg = _collect_missing_erg_atoms(
                        current.erg_expr, _period_prior_exp, _coreq_exp,
                        prior_uoc, _coreq_uoc, _period_course_uoc_map,
                    )
                    for kind, atom in missing_erg:
                        if atom == "__ONEOF__":
                            key_erg = (kind, current.code)
                            next_erg_idx = oneof_index_by_course.get(key_erg, 0) + 1
                            oneof_index_by_course[key_erg] = next_erg_idx
                            atom = f"oneof{next_erg_idx}"
                            findings.append(
                                {
                                    "failure_id": f"{kind}:{current.code}>{atom}",
                                    "kind": kind,
                                    "message": f"{course_label}: one of several options required",
                                    "overrideable": True,
                                    "accepted": False,
                                }
                            )
                        else:
                            sep = ">=" if kind == "coreq" else ">"
                            findings.append(
                                {
                                    "failure_id": f"{kind}:{current.code}{sep}{atom}",
                                    "kind": kind,
                                    "message": (
                                        f"{course_label}: missing {atom} (has {prior_uoc}uoc)"
                                        if _MISSING_UOC_ATOM_RE.fullmatch(atom)
                                        else f"{course_label}: missing {atom}"
                                    ),
                                    "overrideable": True,
                                    "accepted": False,
                                }
                            )
                    if missing_erg:
                        failures.append(
                            f"[Prerequisite/Corequisite] {course_label}: requirement not met"
                        )
                continue  # skip text-parser block below

            prereq_expr, coreq_expr, unsupported_reason = parse_prerequisite_field(
                current.prerequisites
            )

            if unsupported_reason:
                unsupported_msg = f"{course_label}: {unsupported_reason}; raw='{current.prerequisites.strip()}'"
                unsupported.append(unsupported_msg)
                findings.append(
                    {
                        "failure_id": f"unsupported-syntax:{current.code}>{current.prerequisites.strip()}",
                        "kind": "unsupported_syntax",
                        "message": unsupported_msg,
                        "overrideable": False,
                        "accepted": False,
                        "non_overrideable_reason": "unsupported_syntax",
                    }
                )
                continue

            _prior_expanded = _apply_equivalences(prior_courses, _equivalences)
            _prior_expanded.update(_rpl_courses)
            if prereq_expr is not None and not evaluate_expression(
                prereq_expr, _prior_expanded, prior_uoc
            ):
                diagnosis = diagnose_expression(prereq_expr, _prior_expanded, prior_uoc)
                prereq_text = expression_to_text(prereq_expr)
                failure_msg = (
                    f"[Prerequisite] {course_label}: {prereq_text} - {diagnosis}"
                )
                failures.append(failure_msg)

                missing_atoms = _collect_missing_atoms(
                    prereq_expr, _prior_expanded, prior_uoc
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

                _coreq_expanded = _apply_equivalences(coreq_courses, _equivalences)
                _coreq_expanded.update(_rpl_courses)
                if not evaluate_expression(coreq_expr, _coreq_expanded, coreq_uoc):
                    diagnosis = diagnose_expression(
                        coreq_expr, _coreq_expanded, coreq_uoc
                    )
                    coreq_text = expression_to_text(coreq_expr)
                    failure_msg = (
                        f"[Corequisite] {course_label}: {coreq_text} - {diagnosis}"
                    )
                    failures.append(failure_msg)

                    missing_atoms = _collect_missing_atoms(
                        coreq_expr, _coreq_expanded, coreq_uoc
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


def validate_nonstandard_periods(
    courses: list[ScheduledPlanCourse],
) -> list[ValidationFinding]:
    """Return findings for any courses scheduled in non-standard teaching periods.

    Non-standard periods are summer term and winter term.  Each finding is
    overrideable via the plan's sidecar ``.degree_rules_overrides.json`` file
    using the ``failure_id`` ``nonstandard-period:{COURSE_CODE}``.
    """
    findings: list[ValidationFinding] = []
    for course in courses:
        if is_nonstandard_period(course.period):
            findings.append(
                {
                    "failure_id": f"nonstandard-period:{course.code}",
                    "kind": "nonstandard_period",
                    "message": (
                        f"{course.code} ({course.year} {course.period}): "
                        "scheduled in non-standard teaching period"
                    ),
                    "overrideable": True,
                    "accepted": False,
                }
            )
    return findings


def validate_annual_loads(
    courses: list[ScheduledPlanCourse],
) -> list[ValidationFinding]:
    """Return findings for calendar years whose planned load exceeds 48 UoC."""

    uoc_by_year: dict[int, int] = {}
    for course in courses:
        uoc_by_year[course.year] = uoc_by_year.get(course.year, 0) + course.uoc

    findings: list[ValidationFinding] = []
    for year, total_uoc in sorted(uoc_by_year.items()):
        if total_uoc <= 48:
            continue
        findings.append(
            {
                "failure_id": f"annual-load:{year}",
                "kind": "annual_load",
                "message": (
                    f"{year}: planned {total_uoc} UoC in calendar year "
                    "(maximum 48 UoC)"
                ),
                "overrideable": True,
                "accepted": False,
            }
        )
    return findings


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
                raise RuleValidationError(
                    f"plan.courses[{idx}].code", str(exc)
                ) from exc

            if career is not None and catalogue_entry is None:
                raise RuleValidationError(
                    f"plan.courses[{idx}].code",
                    f"Catalogue course '{normalized_code}' was not found for career '{career}'",
                )

        prerequisites: str
        if catalogue_entry is not None:
            uoc = catalogue_entry.uoc
            prerequisites = catalogue_entry.prerequisites
            erg_expr_for_course = catalogue_entry.erg_expr
        else:
            uoc = _parse_int_like(course_record.get("uoc"), f"plan.courses[{idx}].uoc")

            raw_prerequisites = course_record.get("prerequisites")
            prerequisites = (
                raw_prerequisites if isinstance(raw_prerequisites, str) else ""
            )
            erg_expr_for_course = None

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
                erg_expr=erg_expr_for_course,
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
    rpl_courses: Counter[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate prerequisite and corequisite expressions for all plan courses.

    Returns:
        A tuple ``(failures, unsupported)`` where:
        - ``failures`` contains unmet prerequisite/corequisite diagnostics.
        - ``unsupported`` contains expressions that could not be parsed.
    """
    courses = extract_scheduled_courses(plan_data, catalogue=catalogue, career=career)
    return validate_scheduled_prerequisites(courses, rpl_courses=rpl_courses)


def validate_plan_prerequisites_detailed(
    plan_data: dict[str, Any],
    *,
    catalogue: Catalogue | None = None,
    career: str | None = None,
    equivalences: list[CourseEquivalence] | None = None,
    rpl_courses: Counter[str] | None = None,
) -> tuple[list[str], list[str], list[ValidationFinding], list[ValidationWarning]]:
    """Detailed schedule-aware prerequisite validation with structured output."""

    courses = extract_scheduled_courses(plan_data, catalogue=catalogue, career=career)
    nonstandard_findings = validate_nonstandard_periods(courses)
    annual_load_findings = validate_annual_loads(courses)
    failures, unsupported, prereq_findings, warnings = (
        validate_scheduled_prerequisites_detailed(
            courses,
            equivalences,
            rpl_courses=rpl_courses,
        )
    )
    return (
        failures,
        unsupported,
        [*nonstandard_findings, *annual_load_findings, *prereq_findings],
        warnings,
    )


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
        operator_clause.pop("_comment", None)
        keys = set(operator_clause.keys())

        if keys == {"min", "from"} or keys == {"min", "from", "placeholder"}:
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
            normalized_clause: dict[str, Any] = {
                "min": min_count,
                "from": [normalize_clause(child) for child in from_courses],
            }
            if "placeholder" in operator_clause:
                normalized_clause["placeholder"] = _validate_placeholder_code(
                    operator_clause["placeholder"],
                    "<clause>.placeholder",
                )
            return normalized_clause

        if keys == {"or", "placeholder"}:
            children_value = operator_clause["or"]
            if not isinstance(children_value, list):
                raise RuleValidationError("<clause>", "'or' value must be an array")
            children = cast(list[object], children_value)
            if len(children) < 2:
                raise RuleValidationError(
                    "<clause>", "'or' must contain at least 2 child expressions"
                )
            return {
                "or": [normalize_clause(child) for child in children],
                "placeholder": _validate_placeholder_code(
                    operator_clause["placeholder"],
                    "<clause>.placeholder",
                ),
            }

        if len(operator_clause) != 1:
            raise RuleValidationError(
                "<clause>",
                (
                    "operator clause must contain exactly one key "
                    "(or 'min' + 'from', or 'or' + 'placeholder')"
                ),
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
    if _RPL_KEY in data:
        data[_RPL_KEY] = _normalize_rpl_config_value(data[_RPL_KEY])

    if _DOUBLE_COUNTED_KEY in data:
        # Backward-compatible migration from legacy top-level key.
        if _SHARED_COURSES_KEY not in data:
            data[_SHARED_COURSES_KEY] = {_DOUBLE_COUNTED_KEY: data[_DOUBLE_COUNTED_KEY]}
        else:
            shared = cast(dict[str, Any], data[_SHARED_COURSES_KEY])
            if _DOUBLE_COUNTED_KEY in shared:
                raise RuleValidationError(
                    _SHARED_COURSES_KEY,
                    "double-counted declared in both top-level and shared-courses",
                )
            shared[_DOUBLE_COUNTED_KEY] = data[_DOUBLE_COUNTED_KEY]
        data.pop(_DOUBLE_COUNTED_KEY, None)

    if _SHARED_COURSES_KEY in data:
        data[_SHARED_COURSES_KEY] = _normalize_shared_courses_config_value(
            data[_SHARED_COURSES_KEY]
        )
    data["schemaVersion"] = 2
    if _CLAUSE_META_KEY in config:
        data[_CLAUSE_META_KEY] = deepcopy(config[_CLAUSE_META_KEY])
    return data


def _normalize_rpl_config_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RuleValidationError(_RPL_KEY, "rpl must be an array of course codes")

    normalized: list[str] = []
    entries = cast(list[object], value)
    for idx, item in enumerate(entries):
        if not isinstance(item, str) or not item.strip():
            raise RuleValidationError(
                f"{_RPL_KEY}[{idx}]", "rpl entries must be non-empty course codes"
            )
        normalized.append(_normalize_course_code(item))
    return normalized


def _normalize_double_counted_config_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RuleValidationError(
            _DOUBLE_COUNTED_KEY,
            "double-counted must be an array of course codes",
        )

    normalized: list[str] = []
    seen: set[str] = set()
    entries = cast(list[object], value)
    for idx, item in enumerate(entries):
        if not isinstance(item, str) or not item.strip():
            raise RuleValidationError(
                f"{_DOUBLE_COUNTED_KEY}[{idx}]",
                "double-counted entries must be non-empty course codes",
            )
        code = _normalize_course_code(item)
        if code in seen:
            raise RuleValidationError(
                f"{_DOUBLE_COUNTED_KEY}[{idx}]",
                f"duplicate course code '{code}' in double-counted",
            )
        seen.add(code)
        normalized.append(code)

    return normalized


def _normalize_over_double_count_limit_value(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuleValidationError(
            f"{_SHARED_COURSES_KEY}.{_OVER_DOUBLE_COUNT_LIMIT_KEY}",
            "over-double-count-limit must be an array",
        )

    selectors: list[dict[str, Any]] = []
    entries = cast(list[object], value)
    for idx, item in enumerate(entries):
        path = f"{_SHARED_COURSES_KEY}.{_OVER_DOUBLE_COUNT_LIMIT_KEY}[{idx}]"
        if not isinstance(item, dict):
            raise RuleValidationError(path, "entry must be an object")

        raw_selector = cast(dict[str, object], item)
        placeholder_raw = raw_selector.get("placeholder")
        if not isinstance(placeholder_raw, str) or not placeholder_raw.strip():
            raise RuleValidationError(
                f"{path}.placeholder",
                "placeholder must be a non-empty course code",
            )
        placeholder = _normalize_course_code(placeholder_raw)

        from_raw = raw_selector.get("from")
        if not isinstance(from_raw, list):
            raise RuleValidationError(f"{path}.from", "from must be an array")

        normalized_from: list[str] = []
        seen: set[str] = set()
        for from_idx, from_item in enumerate(cast(list[object], from_raw)):
            if not isinstance(from_item, str) or not from_item.strip():
                raise RuleValidationError(
                    f"{path}.from[{from_idx}]",
                    "from entries must be non-empty course codes",
                )
            code = _normalize_course_code(from_item)
            if code in seen:
                raise RuleValidationError(
                    f"{path}.from[{from_idx}]",
                    f"duplicate course code '{code}' in from",
                )
            seen.add(code)
            normalized_from.append(code)

        if not normalized_from:
            raise RuleValidationError(f"{path}.from", "from must not be empty")

        selectors.append({"placeholder": placeholder, "from": normalized_from})

    return selectors


def _normalize_shared_courses_config_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleValidationError(_SHARED_COURSES_KEY, "shared-courses must be an object")

    shared = cast(dict[str, object], value)
    allowed = {_DOUBLE_COUNTED_KEY, _OVER_DOUBLE_COUNT_LIMIT_KEY}
    for key in shared.keys():
        if key not in allowed:
            raise RuleValidationError(
                f"{_SHARED_COURSES_KEY}.{key}",
                "unsupported shared-courses key",
            )

    normalized: dict[str, Any] = {}
    if _DOUBLE_COUNTED_KEY in shared:
        normalized[_DOUBLE_COUNTED_KEY] = _normalize_double_counted_config_value(
            shared[_DOUBLE_COUNTED_KEY]
        )
    if _OVER_DOUBLE_COUNT_LIMIT_KEY in shared:
        normalized[_OVER_DOUBLE_COUNT_LIMIT_KEY] = (
            _normalize_over_double_count_limit_value(
                shared[_OVER_DOUBLE_COUNT_LIMIT_KEY]
            )
        )

    return normalized


def _shared_courses_config(normalized_config: dict[str, Any]) -> dict[str, Any]:
    shared = normalized_config.get(_SHARED_COURSES_KEY, {})
    if shared is None:
        return {}
    return _normalize_shared_courses_config_value(shared)


def _collect_expression_course_codes(expr: RuleExpr) -> set[str]:
    if _is_course_code(expr):
        return {cast(str, expr)}
    if not isinstance(expr, dict):
        return set()

    node = expr
    codes: set[str] = set()
    if set(node.keys()) == {"uoc"}:
        return codes

    if set(node.keys()) == {"min", "from"} or _is_placeholder_min_from(node):
        for child in cast(list[RuleExpr], node["from"]):
            codes.update(_collect_expression_course_codes(child))
        if _is_placeholder_min_from(node):
            codes.add(cast(str, node["placeholder"]))
        return codes

    if set(node.keys()) == {"or"} or _is_placeholder_or(node):
        for child in cast(list[RuleExpr], node["or"]):
            codes.update(_collect_expression_course_codes(child))
        if _is_placeholder_or(node):
            codes.add(cast(str, node["placeholder"]))
        return codes

    if len(node) == 1:
        op = next(iter(node.keys()))
        if op in {"and", "or"}:
            for child in cast(list[RuleExpr], node[op]):
                codes.update(_collect_expression_course_codes(child))
    return codes


def _collect_placeholder_clauses(
    normalized_config: dict[str, Any],
) -> dict[str, list[tuple[str, int, dict[str, Any]]]]:
    placeholders: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}
    required = normalized_config.get("required", {})
    if not isinstance(required, dict):
        return placeholders

    for level_name, clauses in cast(dict[str, Any], required).items():
        if not isinstance(clauses, list):
            continue
        for idx, clause in enumerate(cast(list[RuleExpr], clauses), start=1):
            if not isinstance(clause, dict) or not _is_placeholder_min_from(clause):
                continue
            placeholder = cast(str, clause["placeholder"])
            placeholders.setdefault(placeholder, []).append(
                (level_name, idx, clause)
            )

    return placeholders


def _compute_over_double_count_limit_requirements(
    normalized_config: dict[str, Any],
    consumed_courses: Counter[str],
) -> dict[str, int]:
    shared = _shared_courses_config(normalized_config)
    selectors = cast(list[dict[str, Any]], shared.get(_OVER_DOUBLE_COUNT_LIMIT_KEY, []))
    requirements: dict[str, int] = {}

    for selector in selectors:
        placeholder = cast(str, selector["placeholder"])
        selector_courses = cast(list[str], selector["from"])
        extra_needed = sum(
            1 for course in selector_courses if consumed_courses[course] >= 2
        )
        if extra_needed > 0:
            requirements[placeholder] = requirements.get(placeholder, 0) + extra_needed

    return requirements


def _remaining_after_consumption(
    remaining: Counter[str],
    consumed: Counter[str],
) -> Counter[str]:
    updated = Counter(remaining)
    for course, amount in consumed.items():
        updated[course] -= amount
        if updated[course] <= 0:
            updated.pop(course, None)
    return updated


def _consume_required_expression(
    expr: RuleExpr,
    remaining_courses: Counter[str],
    completed_uoc: int = 0,
    *,
    preserve_partial_consumption: bool = False,
) -> tuple[bool, Counter[str]]:
    """Try to satisfy one expression from remaining required-course capacity."""
    if _is_course_code(expr):
        course = cast(str, expr)
        if remaining_courses[course] <= 0:
            return False, Counter()
        return True, Counter({course: 1})

    if not isinstance(expr, dict):
        raise RuleValidationError("<eval>", "invalid expression shape")

    node = expr
    if set(node.keys()) == {"uoc"}:
        threshold = node["uoc"]
        if not isinstance(threshold, int) or threshold < 0:
            raise RuleValidationError(
                "<eval>.uoc", "'uoc' must be a non-negative integer"
            )
        return completed_uoc >= threshold, Counter()

    consumed: Counter[str]
    if set(node.keys()) == {"min", "from"} or _is_placeholder_min_from(node):
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        placeholder = (
            cast(str, node["placeholder"]) if _is_placeholder_min_from(node) else None
        )

        consumed = Counter()
        temp_remaining = Counter(remaining_courses)
        satisfied = 0

        for child in from_exprs:
            child_satisfied, child_consumed = _consume_required_expression(
                child,
                temp_remaining,
                completed_uoc,
                preserve_partial_consumption=preserve_partial_consumption,
            )
            if not child_satisfied:
                continue
            consumed.update(child_consumed)
            temp_remaining = _remaining_after_consumption(temp_remaining, child_consumed)
            satisfied += 1
            if satisfied >= min_count:
                return True, consumed

        if placeholder is not None and satisfied < min_count:
            required_placeholder = min_count - satisfied
            available_placeholder = temp_remaining[placeholder]
            if available_placeholder >= required_placeholder:
                consumed[placeholder] += required_placeholder
                return True, consumed
            if preserve_partial_consumption and available_placeholder > 0:
                consumed[placeholder] += available_placeholder

        return (False, consumed) if preserve_partial_consumption else (False, Counter())

    if len(node) != 1:
        if not _is_placeholder_or(node):
            raise RuleValidationError("<eval>", "invalid expression shape")

    op = "or" if _is_placeholder_or(node) else next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])
    if op == "and":
        consumed = Counter()
        temp_remaining = Counter(remaining_courses)
        for child in children:
            child_satisfied, child_consumed = _consume_required_expression(
                child,
                temp_remaining,
                completed_uoc,
                preserve_partial_consumption=preserve_partial_consumption,
            )
            if not child_satisfied:
                return (
                    (False, consumed)
                    if preserve_partial_consumption
                    else (False, Counter())
                )
            consumed.update(child_consumed)
            temp_remaining = _remaining_after_consumption(temp_remaining, child_consumed)
        return True, consumed

    if op == "or":
        for child in children:
            child_satisfied, child_consumed = _consume_required_expression(
                child,
                remaining_courses,
                completed_uoc,
                preserve_partial_consumption=preserve_partial_consumption,
            )
            if child_satisfied:
                return True, child_consumed
        if _is_placeholder_or(node):
            placeholder = cast(str, node["placeholder"])
            if remaining_courses[placeholder] > 0:
                return True, Counter({placeholder: 1})
        return False, Counter()

    raise RuleValidationError("<eval>", f"unknown operator '{op}'")


def _required_course_capacity(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> Counter[str]:
    """Build per-course capacity used for required-clause consumption."""
    capacity = Counter(completed_courses)
    shared = _shared_courses_config(normalized_config)
    double_counted = _normalize_double_counted_config_value(
        shared.get(_DOUBLE_COUNTED_KEY, [])
    )

    for course in double_counted:
        if capacity[course] > 0:
            capacity[course] = 2

    return capacity


def extract_rpl_courses(normalized_config: dict[str, Any]) -> Counter[str]:
    """Extract normalized prereq-only RPL courses from validated rules config."""

    raw_rpl = normalized_config.get(_RPL_KEY, [])
    if raw_rpl is None:
        return Counter()
    normalized_rpl = _normalize_rpl_config_value(raw_rpl)
    return Counter(normalized_rpl)


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

    if keys == {"min", "from"} or keys == {"min", "from", "placeholder"}:
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
        if "placeholder" in expr:
            _validate_placeholder_code(expr["placeholder"], f"{path}.placeholder")
        for idx, child in enumerate(from_courses):
            validate_canonical_expression(child, f"{path}.from[{idx}]")
        return

    if keys == {"or", "placeholder"}:
        _validate_placeholder_code(expr["placeholder"], f"{path}.placeholder")

        children_value = expr["or"]
        if not isinstance(children_value, list):
            raise RuleValidationError(path, "'or' must be an array")
        children = cast(list[RuleExpr], children_value)
        if len(children) < 2:
            raise RuleValidationError(
                path, "'or' must contain at least 2 child expressions"
            )
        for idx, child in enumerate(children):
            validate_canonical_expression(child, f"{path}.or[{idx}]")
        return

    if len(keys) != 1:
        raise RuleValidationError(
            path,
            (
                "operator object must contain exactly one key "
                "(or 'min' + 'from', or 'or' + 'placeholder')"
            ),
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

    if set(node.keys()) == {"min", "from"} or _is_placeholder_min_from(node):
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        placeholder = (
            cast(str, node["placeholder"]) if _is_placeholder_min_from(node) else None
        )
        satisfied = _count_min_from_satisfied(
            from_exprs,
            completed_courses,
            completed_uoc,
            placeholder,
        )
        return satisfied >= min_count

    if set(node.keys()) == {"or"} or _is_placeholder_or(node):
        children = cast(list[RuleExpr], node["or"])
        if any(
            evaluate_expression(child, completed_courses, completed_uoc)
            for child in children
        ):
            return True
        if _is_placeholder_or(node):
            placeholder = cast(str, node["placeholder"])
            if any(
                _is_course_code(child) and cast(str, child) == placeholder
                for child in children
            ):
                return False
            return completed_courses[placeholder] > 0
        return False

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
    remaining_courses = _required_course_capacity(normalized_config, completed_courses)
    consumed_courses: Counter[str] = Counter()
    for level_name, clauses in required_levels.items():
        level_clauses = cast(list[RuleExpr], clauses)
        level_passed = True
        for clause in level_clauses:
            clause_passed, consumed = _consume_required_expression(
                clause,
                remaining_courses,
            )
            if not clause_passed:
                level_passed = False
                continue
            remaining_courses = _remaining_after_consumption(remaining_courses, consumed)
            consumed_courses.update(consumed)

        result[level_name] = level_passed

    extra_requirements = _compute_over_double_count_limit_requirements(
        normalized_config,
        consumed_courses,
    )
    placeholder_clauses = _collect_placeholder_clauses(normalized_config)
    for placeholder, extra_needed in extra_requirements.items():
        candidate_clauses = placeholder_clauses.get(placeholder, [])
        if len(candidate_clauses) != 1:
            continue

        level_name, _clause_idx, clause = candidate_clauses[0]
        extra_clause: RuleExpr = {
            "min": extra_needed,
            "from": cast(list[RuleExpr], clause["from"]),
            "placeholder": placeholder,
        }
        is_satisfied, consumed_extra = _consume_required_expression(
            extra_clause,
            remaining_courses,
        )
        if is_satisfied:
            remaining_courses = _remaining_after_consumption(
                remaining_courses,
                consumed_extra,
            )
            continue

        result[level_name] = False

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

    placeholder_clauses = _collect_placeholder_clauses(normalized)
    shared = _shared_courses_config(normalized)
    selectors = cast(list[dict[str, Any]], shared.get(_OVER_DOUBLE_COUNT_LIMIT_KEY, []))
    for idx, selector in enumerate(selectors):
        selector_path = f"{_SHARED_COURSES_KEY}.{_OVER_DOUBLE_COUNT_LIMIT_KEY}[{idx}]"
        placeholder = cast(str, selector["placeholder"])
        candidate_clauses = placeholder_clauses.get(placeholder, [])
        if len(candidate_clauses) != 1:
            raise RuleValidationError(
                f"{selector_path}.placeholder",
                (
                    "placeholder must match exactly one required min/from clause "
                    "with the same placeholder"
                ),
            )

        _, _, clause = candidate_clauses[0]
        available_codes = _collect_expression_course_codes(cast(RuleExpr, clause))
        for course_idx, course in enumerate(cast(list[str], selector["from"])):
            if course not in available_codes:
                raise RuleValidationError(
                    f"{selector_path}.from[{course_idx}]",
                    (
                        f"course '{course}' is not available in the target placeholder "
                        f"clause '{placeholder}'"
                    ),
                )

    extract_rpl_courses(normalized)

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

    if set(node.keys()) == {"min", "from"} or _is_placeholder_min_from(node):
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        total = len(from_exprs)
        items = ", ".join(expression_to_text(child) for child in from_exprs)
        placeholder_text = ""
        if _is_placeholder_min_from(node):
            placeholder_text = f" [placeholder {cast(str, node['placeholder'])}]"
        if min_count == total:
            return f"ALL OF ({items}){placeholder_text}"
        return f"AT LEAST {min_count} OF ({items}){placeholder_text}"

    if set(node.keys()) == {"or"} or _is_placeholder_or(node):
        children = cast(list[RuleExpr], node["or"])
        text = " OR ".join(expression_to_text(child, "or") for child in children)
        if _is_placeholder_or(node):
            text = f"{text} [placeholder {cast(str, node['placeholder'])}]"
        if parent_op is not None and parent_op != "or":
            return f"({text})"
        return text

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

    if set(node.keys()) == {"min", "from"} or _is_placeholder_min_from(node):
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        placeholder = (
            cast(str, node["placeholder"]) if _is_placeholder_min_from(node) else None
        )
        satisfied = _count_min_from_satisfied(
            from_exprs,
            completed_courses,
            completed_uoc,
            placeholder,
        )
        needed = min_count - satisfied
        options = ", ".join(expression_to_text(e) for e in from_exprs)
        if placeholder is not None:
            options = f"{options}; placeholder {placeholder}"
        return f"need {needed} more from ({options})"

    if set(node.keys()) == {"or"} or _is_placeholder_or(node):
        options = ", ".join(
            expression_to_text(child)
            for child in cast(list[RuleExpr], node["or"])
        )
        if _is_placeholder_or(node):
            options = f"{options}; placeholder {cast(str, node['placeholder'])}"
        return f"none of the alternatives were completed: ({options})"

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
    failures, _findings, _warnings, _bucket_allocations = report_plan_detailed(
        normalized_config, completed_courses
    )
    return failures


def _expanded_consumed_courses(consumed: Counter[str]) -> list[str]:
    courses: list[str] = []
    for course in sorted(consumed.keys()):
        courses.extend([course] * consumed[course])
    return courses


def _collect_reported_allocation_usage(
    bucket_allocations: dict[str, list[dict[str, Any]]],
) -> tuple[Counter[str], dict[str, list[dict[str, Any]]]]:
    usage: Counter[str] = Counter()
    locations: dict[str, list[dict[str, Any]]] = {}

    for bucket_name, entries in bucket_allocations.items():
        for entry in entries:
            clause = entry.get("clause")
            rule = entry.get("rule")
            reason = entry.get("reason")
            allocated_courses_raw = entry.get("allocated_courses", [])
            if not isinstance(allocated_courses_raw, list):
                continue
            allocated_courses = cast(list[object], allocated_courses_raw)
            for course in allocated_courses:
                if not isinstance(course, str):
                    continue
                usage[course] += 1
                location: dict[str, Any] = {
                    "bucket": bucket_name,
                    "clause": clause,
                }
                if isinstance(rule, str) and rule:
                    location["rule"] = rule
                if isinstance(reason, str) and reason:
                    location["reason"] = reason
                locations.setdefault(course, []).append(location)

    return usage, locations


def _build_shared_course_allocations_report(
    normalized_config: dict[str, Any],
    bucket_allocations: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    shared = _shared_courses_config(normalized_config)
    usage, locations = _collect_reported_allocation_usage(bucket_allocations)

    double_counted = _normalize_double_counted_config_value(
        shared.get(_DOUBLE_COUNTED_KEY, [])
    )
    over_limit = cast(list[dict[str, Any]], shared.get(_OVER_DOUBLE_COUNT_LIMIT_KEY, []))

    over_limit_entries: list[dict[str, Any]] = []
    for entries in bucket_allocations.values():
        for entry in entries:
            if entry.get("reason") != "over-double-count-limit":
                continue
            over_limit_entries.append(entry)

    double_counted_report: list[dict[str, Any]] = [
        {
            "course": course,
            "allocation_count": usage[course],
            "is_shared": usage[course] >= 2,
            "allocated_to": locations.get(course, []),
        }
        for course in double_counted
    ]

    over_double_count_limit_report: list[dict[str, Any]] = []
    for selector in over_limit:
        placeholder = cast(str, selector["placeholder"])
        selector_courses = cast(list[str], selector["from"])
        triggered_by = sorted(
            course for course in selector_courses if usage[course] >= 2
        )
        selector_allocation_counts = {
            course: usage[course] for course in selector_courses
        }
        selector_allocated_to = {
            course: locations.get(course, [])
            for course in selector_courses
            if usage[course] > 0
        }
        matching_entries = [
            entry
            for entry in over_limit_entries
            if entry.get("placeholder") == placeholder
        ]
        extra_allocated_courses: list[str] = []
        allocated_to: list[dict[str, Any]] = []
        for entry in matching_entries:
            raw_allocated = entry.get("allocated_courses", [])
            if not isinstance(raw_allocated, list):
                continue
            allocated_list = cast(list[object], raw_allocated)
            extra_allocated_courses.extend(
                course for course in allocated_list if isinstance(course, str)
            )
            allocated_location: dict[str, Any] = {}
            bucket = entry.get("bucket")
            clause = entry.get("clause")
            rule = entry.get("rule")
            if isinstance(bucket, str) and bucket:
                allocated_location["bucket"] = bucket
            if isinstance(clause, int):
                allocated_location["clause"] = clause
            if isinstance(rule, str) and rule:
                allocated_location["rule"] = rule
            if allocated_location:
                allocated_to.append(allocated_location)

        over_double_count_limit_report.append(
            {
                "placeholder": placeholder,
                "from": selector_courses,
                "triggered_by": triggered_by,
                "extra_required": len(triggered_by),
                "selector_allocation_counts": selector_allocation_counts,
                "selector_allocated_to": selector_allocated_to,
                "extra_allocated_courses": sorted(extra_allocated_courses),
                "allocated_to": allocated_to,
            }
        )

    return {
        "double_counted": double_counted_report,
        "over_double_count_limit": over_double_count_limit_report,
    }


def _compute_unmatched_courses(
    held_courses: Counter[str],
    bucket_allocations: dict[str, list[dict[str, Any]]],
    equivalences: list[CourseEquivalence] | None = None,
) -> list[str]:
    usage, _locations = _collect_reported_allocation_usage(bucket_allocations)
    unmatched = Counter(held_courses)
    held_by_equivalent: dict[str, list[str]] = {}

    for equivalence in equivalences or []:
        held = equivalence.get("held")
        equivalent_to = equivalence.get("equivalent_to")
        if not held or not equivalent_to:
            continue
        held_by_equivalent.setdefault(equivalent_to, []).append(held)

    for equivalent_to, held_codes in held_by_equivalent.items():
        held_by_equivalent[equivalent_to] = sorted(set(held_codes))

    for course, amount in usage.items():
        remaining = amount

        direct = unmatched.get(course, 0)
        if direct > 0:
            consumed = min(direct, remaining)
            unmatched[course] -= consumed
            if unmatched[course] <= 0:
                unmatched.pop(course, None)
            remaining -= consumed

        if remaining <= 0:
            continue

        for held_code in held_by_equivalent.get(course, []):
            held_amount = unmatched.get(held_code, 0)
            if held_amount <= 0:
                continue
            consumed = min(held_amount, remaining)
            unmatched[held_code] -= consumed
            if unmatched[held_code] <= 0:
                unmatched.pop(held_code, None)
            remaining -= consumed
            if remaining <= 0:
                break

    return _expanded_consumed_courses(unmatched)


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
    *,
    include_bucket_allocations: bool = False,
) -> tuple[
    list[str],
    list[ValidationFinding],
    list[ValidationWarning],
    dict[str, list[dict[str, Any]]] | None,
]:
    """Build legacy and structured rule findings for one plan."""

    failures: list[str] = []
    findings: list[ValidationFinding] = []
    warnings: list[ValidationWarning] = []
    clause_meta_map = _as_clause_meta_map(normalized_config)
    remaining_courses = _required_course_capacity(normalized_config, completed_courses)
    consumed_courses: Counter[str] = Counter()
    bucket_allocations: dict[str, list[dict[str, Any]]] | None = (
        {} if include_bucket_allocations else None
    )

    required = cast(dict[str, Any], normalized_config.get("required", {}))
    for level_name, clauses in required.items():
        level_clauses = cast(list[RuleExpr], clauses)
        for idx, clause in enumerate(level_clauses, start=1):
            clause_passed, consumed = _consume_required_expression(
                clause,
                remaining_courses,
            )
            report_consumed = consumed
            if include_bucket_allocations and not clause_passed:
                _ignored_passed, report_consumed = _consume_required_expression(
                    clause,
                    remaining_courses,
                    preserve_partial_consumption=True,
                )
            if bucket_allocations is not None:
                bucket_entries = bucket_allocations.setdefault(level_name, [])
                bucket_entries.append(
                    {
                        "clause": idx,
                        "rule": expression_to_text(clause),
                        "allocated_courses": _expanded_consumed_courses(
                            report_consumed
                        ),
                    }
                )
            if clause_passed:
                remaining_courses = _remaining_after_consumption(
                    remaining_courses,
                    consumed,
                )
                consumed_courses.update(consumed)
                continue

            rule_text = expression_to_text(clause)
            diagnosis = diagnose_expression(clause, remaining_courses)
            failures.append(
                f"[{level_name}] clause {idx}: {rule_text} \u2014 {diagnosis}"
            )

            atomic_findings = _emit_atomic_rule_findings(
                clause, remaining_courses, level_name, idx
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

    extra_requirements = _compute_over_double_count_limit_requirements(
        normalized_config,
        consumed_courses,
    )
    placeholder_clauses = _collect_placeholder_clauses(normalized_config)
    for placeholder, extra_needed in extra_requirements.items():
        candidate_clauses = placeholder_clauses.get(placeholder, [])
        if len(candidate_clauses) != 1:
            continue

        level_name, clause_idx, clause = candidate_clauses[0]
        extra_clause: RuleExpr = {
            "min": extra_needed,
            "from": cast(list[RuleExpr], clause["from"]),
            "placeholder": placeholder,
        }
        is_satisfied, consumed_extra = _consume_required_expression(
            extra_clause,
            remaining_courses,
        )
        report_consumed_extra = consumed_extra
        if include_bucket_allocations and not is_satisfied:
            _ignored_passed, report_consumed_extra = _consume_required_expression(
                extra_clause,
                remaining_courses,
                preserve_partial_consumption=True,
            )
        if is_satisfied:
            remaining_courses = _remaining_after_consumption(
                remaining_courses,
                consumed_extra,
            )
            if bucket_allocations is not None:
                bucket_entries = bucket_allocations.setdefault(level_name, [])
                bucket_entries.append(
                    {
                        "bucket": level_name,
                        "clause": clause_idx,
                        "rule": expression_to_text(extra_clause),
                        "allocated_courses": _expanded_consumed_courses(
                            report_consumed_extra
                        ),
                        "reason": "over-double-count-limit",
                        "placeholder": placeholder,
                    }
                )
            continue

        if bucket_allocations is not None:
            bucket_entries = bucket_allocations.setdefault(level_name, [])
            bucket_entries.append(
                {
                    "bucket": level_name,
                    "clause": clause_idx,
                    "rule": expression_to_text(extra_clause),
                    "allocated_courses": _expanded_consumed_courses(
                        report_consumed_extra
                    ),
                    "reason": "over-double-count-limit",
                    "placeholder": placeholder,
                }
            )

        message = (
            f"[{level_name}] clause {clause_idx}: over-double-count-limit requires "
            f"{extra_needed} additional elective(s) for placeholder {placeholder}"
        )
        failures.append(message)
        findings.append(
            {
                "failure_id": f"rule:over-double-count-limit:{placeholder}",
                "kind": "rule",
                "message": message,
                "overrideable": True,
                "accepted": False,
            }
        )

    return failures, findings, warnings, bucket_allocations


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


def _load_equivalences_one_file(
    path: Path,
) -> tuple[list[CourseEquivalence], list[ValidationWarning]]:
    """Load equivalences from a single JSON file, returning entries and warnings."""
    if not path.is_file():
        return [], []

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        return [], [
            _make_warning(
                "invalid_equivalences_file",
                f"Could not parse equivalences JSON: {exc}",
                location=str(path),
            )
        ]

    if not isinstance(raw, list):
        return [], [
            _make_warning(
                "invalid_equivalences_file",
                "Equivalences file must contain a JSON list",
                location=str(path),
            )
        ]

    entries: list[CourseEquivalence] = []
    warnings: list[ValidationWarning] = []
    for idx, item in enumerate(cast(list[object], raw)):
        if not isinstance(item, dict):
            warnings.append(
                _make_warning(
                    "invalid_equivalence_entry",
                    "Equivalence entry must be an object",
                    location=f"{path}[{idx}]",
                )
            )
            continue

        item_dict = cast(dict[str, object], item)
        held = item_dict.get("held")
        equivalent_to = item_dict.get("equivalent_to")
        if not isinstance(held, str) or not held.strip():
            warnings.append(
                _make_warning(
                    "invalid_equivalence_entry",
                    "Equivalence entry requires non-empty 'held'",
                    location=f"{path}[{idx}]",
                )
            )
            continue
        if not isinstance(equivalent_to, str) or not equivalent_to.strip():
            warnings.append(
                _make_warning(
                    "invalid_equivalence_entry",
                    "Equivalence entry requires non-empty 'equivalent_to'",
                    location=f"{path}[{idx}]",
                )
            )
            continue

        entry: CourseEquivalence = {
            "held": _normalize_course_code(held),
            "equivalent_to": _normalize_course_code(equivalent_to),
        }
        reason = item_dict.get("reason")
        if isinstance(reason, str) and reason.strip():
            entry["reason"] = reason.strip()
        entries.append(entry)

    return entries, warnings


def _load_equivalences_from_dir(
    plan_dir: Path,
) -> tuple[list[CourseEquivalence], list[ValidationWarning]]:
    """Load additive equivalence lists from global and school-level files."""
    all_entries: list[CourseEquivalence] = []
    all_warnings: list[ValidationWarning] = []
    for candidate in (
        plan_dir.parent / "degree_rules_equivalences.json",
        plan_dir / "degree_rules_equivalences.json",
    ):
        entries, warnings = _load_equivalences_one_file(candidate)
        all_entries.extend(entries)
        all_warnings.extend(warnings)
    return all_entries, all_warnings


def _apply_equivalences(
    completed: Counter[str],
    equivalences: list[CourseEquivalence],
) -> Counter[str]:
    """Return a new Counter with equivalences applied."""
    if not equivalences:
        return Counter(completed)

    result: Counter[str] = Counter(completed)
    for equivalence in equivalences:
        held = equivalence.get("held", "")
        equivalent_to = equivalence.get("equivalent_to", "")
        if held and held in completed:
            result[equivalent_to] += completed[held]
    return result


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

    def _normalize_plan_notes(value: Any) -> dict[str, Any]:
        notes_obj: dict[str, Any] = cast(dict[str, Any], value) if isinstance(value, dict) else {}

        graduate_outcome: Any = notes_obj.get("graduate_outcome", "")
        adjustment_type: Any = notes_obj.get("adjustment_type", "")

        for_reviewers_raw: Any = notes_obj.get("for_reviewers", [])
        for_students_raw: Any = notes_obj.get("for_students", [])

        reviewers_items: list[Any] = for_reviewers_raw if isinstance(for_reviewers_raw, list) else [] # pyright: ignore[reportUnknownVariableType]
        students_items: list[Any] = for_students_raw if isinstance(for_students_raw, list) else [] # pyright: ignore[reportUnknownVariableType]

        for_reviewers = [str(item) for item in reviewers_items]
        for_students = [str(item) for item in students_items]

        return {
            "graduate_outcome": str(graduate_outcome) if graduate_outcome else "",
            "adjustment_type": str(adjustment_type) if adjustment_type else "",
            "for_reviewers": for_reviewers,
            "for_students": for_students,
        }

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

                override_paths = [command.plan_file.parent / "catalogue_overrides.json"]
                catalogue = load_catalogue(
                    command.catalogue_file,
                    override_paths=override_paths,
                )
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

        equivalences, equivalence_warnings = _load_equivalences_from_dir(
            command.plan_file.parent
        )

        try:
            completed_courses = extract_completed_courses(
                cast(dict[str, Any], plan_data)
            )
            rpl_courses = extract_rpl_courses(validated)
            (
                prereq_failures,
                prereq_unsupported,
                prereq_findings,
                prereq_warnings,
            ) = validate_plan_prerequisites_detailed(
                cast(dict[str, Any], plan_data),
                catalogue=catalogue,
                career=rules_career,
                equivalences=equivalences,
                rpl_courses=rpl_courses,
            )
        except RuleValidationError as exc:
            print(f"Error: {exc}", file=stderr)
            return 1

        rules_completed_courses = _apply_equivalences(completed_courses, equivalences)
        (
            rule_failures,
            rule_findings,
            generated_rule_warnings,
            bucket_allocations,
        ) = report_plan_detailed(
            validated,
            rules_completed_courses,
            include_bucket_allocations=command.plan_report_allocations,
        )

        all_findings = [*rule_findings, *prereq_findings]
        warnings: list[ValidationWarning] = [
            *equivalence_warnings,
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
                "notes": _normalize_plan_notes(cast(dict[str, Any], plan_data).get("notes", {})),
            }
            if command.plan_report_allocations:
                allocation_report = bucket_allocations or {}
                report_payload["bucket_allocations"] = allocation_report
                report_payload["shared_course_allocations"] = (
                    _build_shared_course_allocations_report(
                        validated,
                        allocation_report,
                    )
                )
                report_payload["unmatched_courses"] = _compute_unmatched_courses(
                    completed_courses,
                    allocation_report,
                    equivalences,
                )
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
        active_nonstandard = [
            f for f in active_findings if f["kind"] == "nonstandard_period"
        ]

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

        if active_nonstandard:
            print(
                f"Plan has {len(active_nonstandard)} non-standard teaching period(s):",
                file=stdout,
            )
            for f in active_nonstandard:
                print(f"  [{f['failure_id']}] {f['message']}", file=stdout)

        if active_unsup:
            print(
                f"Plan has {len(active_unsup)} unsupported syntax expression(s):",
                file=stdout,
            )
            for f in active_unsup:
                print(f"  [{f['failure_id']}] {f['message']}", file=stdout)

        visible_warnings = warnings if command.show_plan_warnings else []

        if warnings:
            summary = f"Plan has {len(warnings)} warning(s):"
            if not command.show_plan_warnings:
                summary += " use -v to show details"
            print(summary, file=stdout)
        if visible_warnings:
            for warning in visible_warnings:
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

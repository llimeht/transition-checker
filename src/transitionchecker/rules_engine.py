from __future__ import annotations

"""Validate degree rule expressions and check plan prerequisite compliance."""

import json
import logging
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, TypedDict, cast

from transitionchecker.core import period_rank


CourseCode = str
RuleExpr = CourseCode | dict[str, Any]


_PREREQ_PARSE_CACHE: dict[str, tuple[RuleExpr | None, RuleExpr | None, str | None]] = {}
_REQUIRED_VALIDATION_CACHE: set[int] = set()


class PlanCourseRecord(TypedDict, total=False):
    """Subset of plan JSON course fields needed by rule/prerequisite checks."""

    code: str
    year: int
    period: str
    course_n: str
    uoc: int
    prerequisites: str


@dataclass(frozen=True)
class ScheduledPlanCourse:
    """Normalized and sortable in-plan course record used for validation."""

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
    plan_report_json: bool = False
    render_rules_text: bool = False


COURSE_TOKEN_RE = re.compile(r"[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?")
UOC_TOKEN_RE = re.compile(r"(\d+)\s*UOC", re.IGNORECASE)
PREREQ_TOKEN_RE = re.compile(
    r"\s*(\(|\)|AND|OR|\d+\s*UOC|[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?)\s*", re.IGNORECASE
)
CO_REQUISITE_RE = re.compile(
    r"\b(?:CO-?REQ\w*)\b\s*:?",
    re.IGNORECASE,
)


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


def _canonicalize_prereq_text(text: str) -> str:
    """Normalize prerequisite text for token-based expression parsing."""
    canonical = text.upper()
    canonical = canonical.replace("&", " AND ")
    canonical = canonical.replace(",", " AND ")
    canonical = canonical.replace(";", " ")
    canonical = canonical.replace(".", " ")
    canonical = re.sub(r"\s+", " ", canonical).strip()
    return canonical


def _and_expressions(expressions: list[RuleExpr]) -> RuleExpr:
    """Combine expressions with flattened ``and`` semantics."""
    if len(expressions) == 1:
        return expressions[0]

    children: list[RuleExpr] = []
    for expr in expressions:
        if isinstance(expr, dict) and list(expr.keys()) == ["and"]:
            children.extend(cast(list[RuleExpr], expr["and"]))
        else:
            children.append(expr)
    return {"and": children}


def _parse_prerequisite_expression_single(
    text: str,
) -> tuple[RuleExpr | None, str | None]:
    """Parse one prerequisite expression segment.

    Args:
        text: Prerequisite text segment without PLUS splitting.

    Returns:
        Tuple of parsed expression and parse error message.
    """
    canonical = _canonicalize_prereq_text(text)
    if not canonical:
        return None, None

    tokens: list[str] = []
    position = 0
    while position < len(canonical):
        match = PREREQ_TOKEN_RE.match(canonical, position)
        if not match:
            snippet = canonical[position : position + 40].strip()
            if not snippet:
                break
            return None, f"unrecognized token near '{snippet}'"
        token = match.group(1).upper()
        tokens.append(token)
        position = match.end()

    if not tokens:
        return None, None

    token_idx = 0

    def parse_primary() -> tuple[RuleExpr | None, str | None]:
        nonlocal token_idx
        if token_idx >= len(tokens):
            return None, "unexpected end of expression"

        token = tokens[token_idx]
        if token == "(":
            token_idx += 1
            inner, err = parse_or()
            if err:
                return None, err
            if token_idx >= len(tokens) or tokens[token_idx] != ")":
                return None, "missing closing ')'"
            token_idx += 1
            return inner, None

        if COURSE_TOKEN_RE.fullmatch(token):
            token_idx += 1
            return token, None

        uoc_match = UOC_TOKEN_RE.fullmatch(token)
        if uoc_match:
            token_idx += 1
            return {"uoc": int(uoc_match.group(1))}, None

        return None, f"unexpected token '{token}'"

    def fold_operator(op: str, left: RuleExpr, right: RuleExpr) -> RuleExpr:
        children: list[RuleExpr] = []
        if isinstance(left, dict) and list(left.keys()) == [op]:
            children.extend(cast(list[RuleExpr], left[op]))
        else:
            children.append(left)
        if isinstance(right, dict) and list(right.keys()) == [op]:
            children.extend(cast(list[RuleExpr], right[op]))
        else:
            children.append(right)
        return {op: children}

    def parse_and() -> tuple[RuleExpr | None, str | None]:
        nonlocal token_idx
        left, err = parse_primary()
        if err:
            return None, err
        while token_idx < len(tokens) and tokens[token_idx] == "AND":
            token_idx += 1
            right, right_err = parse_primary()
            if right_err:
                return None, right_err
            left = fold_operator("and", cast(RuleExpr, left), cast(RuleExpr, right))
        return left, None

    def parse_or() -> tuple[RuleExpr | None, str | None]:
        nonlocal token_idx
        left, err = parse_and()
        if err:
            return None, err
        while token_idx < len(tokens) and tokens[token_idx] == "OR":
            token_idx += 1
            right, right_err = parse_and()
            if right_err:
                return None, right_err
            left = fold_operator("or", cast(RuleExpr, left), cast(RuleExpr, right))
        return left, None

    expr, parse_err = parse_or()
    if parse_err:
        return None, parse_err
    if token_idx != len(tokens):
        return None, f"unexpected trailing token '{tokens[token_idx]}'"
    return expr, None


def _parse_prerequisite_expression(text: str) -> tuple[RuleExpr | None, str | None]:
    """Parse prerequisite text with support for PLUS conjunctions.

    Args:
        text: Raw prerequisite text.

    Returns:
        Tuple of parsed expression and parse error message.
    """
    raw = text.strip()
    if not raw:
        return None, None

    plus_parts = [
        part.strip() for part in re.split(r"(?i)\bPLUS\b", raw) if part.strip()
    ]
    parsed_parts: list[RuleExpr] = []

    for part in plus_parts:
        normalized_part = re.sub(r"(?i)\bCOMPLETION\s+OF\b", "", part).strip()
        expr, err = _parse_prerequisite_expression_single(normalized_part)
        if err:
            return None, err
        if expr is not None:
            parsed_parts.append(expr)

    if not parsed_parts:
        return None, None

    return _and_expressions(parsed_parts), None


def _split_prerequisite_parts(raw_text: str) -> tuple[str, str | None]:
    """Split raw prerequisite text into prerequisite and corequisite sections.

    Args:
        raw_text: Original prerequisite field text.

    Returns:
        Tuple of (prerequisite_text, corequisite_text_or_none).
    """
    coreq_match = CO_REQUISITE_RE.search(raw_text)
    if not coreq_match:
        return raw_text, None

    prereq_part = raw_text[: coreq_match.start()]
    prereq_part = re.sub(r"(?i)\bPLUS\s*$", "", prereq_part).strip()

    coreq_part = raw_text[coreq_match.end() :]
    coreq_part = re.sub(r"^[\s:;,.+-]+", "", coreq_part).strip()

    return prereq_part, coreq_part if coreq_part else None


def _parse_prerequisite_field(
    raw_text: str,
) -> tuple[RuleExpr | None, RuleExpr | None, str | None]:
    """Parse a plan prerequisite field into prerequisite/corequisite expressions.

    Returns:
        Tuple ``(prereq_expr, coreq_expr, error_message)`` where expressions are
        ``None`` when absent. ``error_message`` is populated for unsupported
        formats that cannot be parsed safely.
    """
    trimmed = raw_text.strip()
    cached = _PREREQ_PARSE_CACHE.get(trimmed)
    if cached is not None:
        return cached

    result: tuple[RuleExpr | None, RuleExpr | None, str | None]

    if not trimmed or trimmed in {".", "0"}:
        result = (None, None, None)
        _PREREQ_PARSE_CACHE[trimmed] = result
        return result

    prereq_text, coreq_text = _split_prerequisite_parts(trimmed)

    prereq_expr: RuleExpr | None = None
    coreq_expr: RuleExpr | None = None

    prereq_expr, prereq_error = _parse_prerequisite_expression(prereq_text)
    if prereq_error:
        result = (None, None, f"prerequisite parse error: {prereq_error}")
        _PREREQ_PARSE_CACHE[trimmed] = result
        return result

    if coreq_text:
        coreq_expr, coreq_error = _parse_prerequisite_expression(coreq_text)
        if coreq_error:
            result = (None, None, f"corequisite parse error: {coreq_error}")
            _PREREQ_PARSE_CACHE[trimmed] = result
            return result
        if coreq_expr is None:
            result = (
                None,
                None,
                "corequisite text exists but no course code expression was parsed",
            )
            _PREREQ_PARSE_CACHE[trimmed] = result
            return result

    result = (prereq_expr, coreq_expr, None)
    _PREREQ_PARSE_CACHE[trimmed] = result
    return result


def parse_prerequisite_field(
    raw_text: str,
) -> tuple[RuleExpr | None, RuleExpr | None, str | None]:
    """Public wrapper for parsing prerequisite/corequisite text."""
    return _parse_prerequisite_field(raw_text)


def validate_scheduled_prerequisites(
    courses: list[ScheduledPlanCourse],
) -> tuple[list[str], list[str]]:
    """Validate prerequisite and corequisite expressions for scheduled courses.

    This is the reusable core used by ``validate_plan_prerequisites`` and can be
    called by generators/search code that already has normalized scheduled rows.

    Returns:
        A tuple ``(failures, unsupported)`` where:
        - ``failures`` contains unmet prerequisite/corequisite diagnostics.
        - ``unsupported`` contains expressions that could not be parsed.
    """
    failures: list[str] = []
    unsupported: list[str] = []

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

        for current in group_courses:
            prereq_expr, coreq_expr, unsupported_reason = _parse_prerequisite_field(
                current.prerequisites
            )
            course_label = f"{current.code} ({current.year} {current.period})"

            if unsupported_reason:
                unsupported.append(
                    f"{course_label}: {unsupported_reason}; raw='{current.prerequisites.strip()}'"
                )
                continue

            if prereq_expr is not None and not evaluate_expression(
                prereq_expr, prior_courses, prior_uoc
            ):
                diagnosis = diagnose_expression(prereq_expr, prior_courses, prior_uoc)
                failures.append(
                    f"[Prerequisite] {course_label}: {expression_to_text(prereq_expr)} - {diagnosis}"
                )

            if coreq_expr is not None:
                coreq_courses = Counter(coreq_base)
                if coreq_courses[current.code] > 0:
                    coreq_courses[current.code] -= 1
                    if coreq_courses[current.code] <= 0:
                        del coreq_courses[current.code]
                coreq_uoc = prior_uoc + group_uoc - current.uoc

                if not evaluate_expression(coreq_expr, coreq_courses, coreq_uoc):
                    diagnosis = diagnose_expression(coreq_expr, coreq_courses, coreq_uoc)
                    failures.append(
                        f"[Corequisite] {course_label}: {expression_to_text(coreq_expr)} - {diagnosis}"
                    )

        prior_courses.update(group_counter)
        prior_uoc += group_uoc
        group_start = group_end

    return failures, unsupported


def extract_scheduled_courses(plan_data: dict[str, Any]) -> list[ScheduledPlanCourse]:
    """Extract, normalize, and sort planned courses chronologically.

    Args:
        plan_data: Parsed plan JSON object.

    Returns:
        List of normalized scheduled course records.
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

        uoc = _parse_int_like(course_record.get("uoc"), f"plan.courses[{idx}].uoc")

        prerequisites = course_record.get("prerequisites")
        if not isinstance(prerequisites, str):
            prerequisites = ""

        scheduled_courses.append(
            ScheduledPlanCourse(
                index=idx,
                code=code.strip().upper(),
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
) -> tuple[list[str], list[str]]:
    """Validate prerequisite and corequisite expressions for all plan courses.

    Returns:
        A tuple ``(failures, unsupported)`` where:
        - ``failures`` contains unmet prerequisite/corequisite diagnostics.
        - ``unsupported`` contains expressions that could not be parsed.
    """
    courses = extract_scheduled_courses(plan_data)
    return validate_scheduled_prerequisites(courses)


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
        operator_clause = cast(dict[str, Any], clause)
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
    normalized = normalize_rules_config(config)

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
    failures: list[str] = []
    required = cast(dict[str, Any], normalized_config.get("required", {}))
    for level_name, clauses in required.items():
        level_clauses = cast(list[RuleExpr], clauses)
        for idx, clause in enumerate(level_clauses, start=1):
            if not evaluate_expression(clause, completed_courses):
                rule_text = expression_to_text(clause)
                diagnosis = diagnose_expression(clause, completed_courses)
                failures.append(
                    f"[{level_name}] clause {idx}: {rule_text} \u2014 {diagnosis}"
                )
    return failures


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
        validated = validate_rules_config(cast(dict[str, Any], raw_config))
    except RuleValidationError as exc:
        print(f"Validation failed: {exc}", file=stderr)
        return 1

    logger.info("Rules validation: OK")
    logger.debug("Rules:\n" + render_rules_human(validated))

    if command.json_output:
        print("\nCanonical JSON", file=stdout)
        print(json.dumps(validated, indent=2, sort_keys=False), file=stdout)

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

        try:
            completed_courses = extract_completed_courses(cast(dict[str, Any], plan_data))
            prereq_failures, prereq_unsupported = validate_plan_prerequisites(
                cast(dict[str, Any], plan_data)
            )
        except RuleValidationError as exc:
            print(f"Error: {exc}", file=stderr)
            return 1

        rule_failures = report_plan(validated, completed_courses)
        is_valid = not rule_failures and not prereq_failures and not prereq_unsupported

        if command.plan_report_json:
            report_payload: dict[str, Any] = {
                "valid": is_valid,
                "rule_failures": rule_failures,
                "prerequisite_failures": prereq_failures,
                "unsupported_prerequisites": prereq_unsupported,
            }
            print(json.dumps(report_payload, indent=2), file=stdout)
            return 0 if is_valid else 1

        if rule_failures:
            print(f"Plan does not satisfy {len(rule_failures)} degree rule(s):", file=stdout)
            for failure in rule_failures:
                print(f"  {failure}", file=stdout)

        if prereq_failures:
            print(
                f"Plan has {len(prereq_failures)} prerequisite/corequisite violation(s):",
                file=stdout,
            )
            for failure in prereq_failures:
                print(f"  {failure}", file=stdout)

        if prereq_unsupported:
            print(
                f"Plan has {len(prereq_unsupported)} unsupported prerequisite expression(s):",
                file=stdout,
            )
            for unsupported in prereq_unsupported:
                print(f"  {unsupported}", file=stdout)

        if is_valid:
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

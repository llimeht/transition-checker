"""Prerequisite parsing, lint classification, and snapshot helpers.

This module is intentionally limited to string-level prerequisite handling:
parsing raw catalogue fields into canonical expressions, classifying unsupported
clauses for lint triage, and generating deterministic parser snapshots.
Schedule-aware validation belongs in ``rules_engine``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, cast


RuleExpr = str | dict[str, Any]


class PrerequisiteClauseClassification(str, Enum):
    """Closed set of lint classifications for unsupported prerequisite text."""

    IGNORABLE = "ignorable"
    MIXED = "mixed"
    NON_IGNORABLE = "non_ignorable"


_PREREQ_PARSE_CACHE: dict[str, tuple[RuleExpr | None, RuleExpr | None, str | None]] = {}


COURSE_TOKEN_RE = re.compile(r"[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?")
UOC_TOKEN_RE = re.compile(r"(\d+)\s*UOC", re.IGNORECASE)
PREREQ_TOKEN_RE = re.compile(
    r"\s*(\(|\)|AND|OR|\d+\s*UOC|[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?)\s*", re.IGNORECASE
)
CO_REQUISITE_RE = re.compile(
    r"\b(?:CO-?REQ\w*)\b\s*:?",
    re.IGNORECASE,
)


def _canonicalize_prereq_text(text: str) -> str:
    """Normalize prerequisite text for token-based expression parsing."""
    canonical = text.upper()
    canonical = canonical.replace("&", " AND ")
    canonical = canonical.replace(",", " AND ")
    canonical = canonical.replace("UNITS OF CREDIT", " UOC ")
    canonical = canonical.replace("UNIT OF CREDITS", " UOC ")
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
            children.extend(expr["and"])
        else:
            children.append(expr)
    return {"and": children}


def _parse_prerequisite_expression_single(
    text: str,
) -> tuple[RuleExpr | None, str | None]:
    """Parse a single prerequisite segment without ``PLUS`` splitting."""
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
            children.extend(left[op])
        else:
            children.append(left)
        if isinstance(right, dict) and list(right.keys()) == [op]:
            children.extend(right[op])
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
    """Parse prerequisite text, treating ``PLUS`` as an ``AND`` separator."""
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
    """Split a raw field into prerequisite and corequisite text sections."""
    coreq_match = CO_REQUISITE_RE.search(raw_text)
    if not coreq_match:
        return raw_text, None

    prereq_part = raw_text[: coreq_match.start()]
    prereq_part = re.sub(r"(?i)\bPLUS\s*$", "", prereq_part).strip()

    coreq_part = raw_text[coreq_match.end() :]
    coreq_part = re.sub(r"^[\s:;,.+-]+", "", coreq_part).strip()

    return prereq_part, coreq_part if coreq_part else None


def parse_prerequisite_field(
    raw_text: str,
) -> tuple[RuleExpr | None, RuleExpr | None, str | None]:
    """Parse a raw prerequisite field into prerequisite/corequisite expressions.

    Returns ``(prereq_expr, coreq_expr, error_message)``. Expressions are
    ``None`` when absent. ``error_message`` is populated when the field cannot
    be parsed by the supported token grammar.
    """
    cached = _PREREQ_PARSE_CACHE.get(raw_text)
    if cached is not None:
        return cached

    trimmed = raw_text.strip()
    trimmed = re.sub(r"^pre-?req(uisite)?s?:?\s*", "", trimmed, flags=re.IGNORECASE)

    result: tuple[RuleExpr | None, RuleExpr | None, str | None]

    if not trimmed or trimmed in {".", "0", "NONE", "NIL", "N/A", "?"}:
        result = (None, None, None)
        _PREREQ_PARSE_CACHE[raw_text] = result
        return result

    prereq_text, coreq_text = _split_prerequisite_parts(trimmed)

    prereq_expr, prereq_error = _parse_prerequisite_expression(prereq_text)
    if prereq_error:
        result = (None, None, f"prerequisite parse error: {prereq_error}")
        _PREREQ_PARSE_CACHE[raw_text] = result
        return result

    coreq_expr: RuleExpr | None = None
    if coreq_text:
        coreq_expr, coreq_error = _parse_prerequisite_expression(coreq_text)
        if coreq_error:
            result = (None, None, f"corequisite parse error: {coreq_error}")
            _PREREQ_PARSE_CACHE[raw_text] = result
            return result
        if coreq_expr is None:
            result = (
                None,
                None,
                "corequisite text exists but no course code expression was parsed",
            )
            _PREREQ_PARSE_CACHE[raw_text] = result
            return result

    result = (prereq_expr, coreq_expr, None)
    _PREREQ_PARSE_CACHE[raw_text] = result
    return result


IGNORE_FAMILY_PATTERNS: dict[str, re.Pattern[str]] = {
    "program_enrolment": re.compile(
        r"(?i)\b(enrol(?:ment|led)?\s+in|program\s+\d{3,4}|major\b|speciali[sz]ation\b)"
    ),
    "wam_mark": re.compile(
        r"(?i)\b(wam\b|minimum\s+mark\b|\d+\+\s*wam\b|mark\s+of\s+\d+)"
    ),
    "cohort_timing": re.compile(
        r"(?i)\b(honours\b|final\s+term\b|final\s+year\b|single\s+degree\b|double\s+degree\b|at\s+level\s+\d)"
    ),
    "application_approval": re.compile(
        r"(?i)\b(by\s+consent\b|by\s+invitation\b|application\s+only\b|approval\b|permission\b|placement\s+approval\b)"
    ),
}

PARSEABLE_SIGNAL_RE = re.compile(
    r"(?i)(\b[A-Z]{4}[A-Z0-9-]*\d[A-Z0-9-]*\b|\b\d+\s*UOC\b)"
)


def classify_prerequisite_clause(
    text: str,
) -> tuple[PrerequisiteClauseClassification, list[str]]:
    """Classify unsupported prerequisite text for lint triage.

    Returns a classification enum plus the matched ignore-family names that
    drove the result.
    """
    matched_families = [
        family
        for family, pattern in IGNORE_FAMILY_PATTERNS.items()
        if pattern.search(text)
    ]
    if not matched_families:
        return PrerequisiteClauseClassification.NON_IGNORABLE, []

    stripped = text
    for family in matched_families:
        stripped = IGNORE_FAMILY_PATTERNS[family].sub(" ", stripped)

    if PARSEABLE_SIGNAL_RE.search(stripped):
        return PrerequisiteClauseClassification.MIXED, matched_families
    return PrerequisiteClauseClassification.IGNORABLE, matched_families


def build_prerequisite_snapshot(
    catalogue: dict[str, dict[str, Any]],
    source_catalogue: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build deterministic prerequisite parse snapshot data from catalogue rows.

    The returned payload is intended for regression tests and manual diffing of
    parser behavior across catalogue updates.
    """
    timestamp = generated_at
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    entries: list[dict[str, Any]] = []
    for course_code in sorted(catalogue.keys()):
        course = catalogue[course_code]
        prereq = str(course.get("prerequisites", ""))
        prereq_expr, coreq_expr, error = parse_prerequisite_field(prereq)
        entries.append(
            {
                "course_code": str(course_code),
                "prerequisites": prereq,
                "prereq_expr": prereq_expr,
                "coreq_expr": coreq_expr,
                "error": error,
            }
        )

    return {
        "meta": {
            "generated_at": timestamp,
            "source_catalogue": source_catalogue,
            "entry_count": len(entries),
            "parser": "parse_prerequisite_field",
        },
        "entries": entries,
    }

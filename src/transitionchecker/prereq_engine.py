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


COURSE_TOKEN_RE = re.compile(r"[A-Z]{4}\d{4}")
QUALIFIED_UOC_TOKEN_RE = re.compile(
    r"(\d+)\s*UOC\s+(?:OF|IN)\s+[A-Z][A-Z\s&/-]*\s+COURSES?",
    re.IGNORECASE,
)
UOC_TOKEN_RE = re.compile(r"(\d+)\s*UOC", re.IGNORECASE)
PREREQ_TOKEN_RE = re.compile(
    r"\s*(\(|\)|AND|OR|\d+\s*UOC\s+(?:OF|IN)\s+[A-Z][A-Z\s&/-]*\s+COURSES?|\d+\s*UOC|[A-Z]{4}\d{4})\s*",
    re.IGNORECASE,
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
    canonical = canonical.replace("UNIT OF CREDITS", " UOC ")  # sad but true
    canonical = canonical.replace(";", " ")
    canonical = canonical.replace(".", " ")
    canonical = re.sub(r"\bAND\s+AND\b", " AND ", canonical)
    canonical = re.sub(r"\bOR\s+OR\b", " OR ", canonical)
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

        qualified_uoc_match = QUALIFIED_UOC_TOKEN_RE.fullmatch(token)
        if qualified_uoc_match:
            token_idx += 1
            # For now treat prefix-qualified UOC as a generic maturity threshold.
            return {"uoc": int(qualified_uoc_match.group(1))}, None

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

    expr, parse_err = parse_or()
    if parse_err:
        return None, parse_err
    if token_idx != len(tokens):
        return None, f"unexpected trailing token '{tokens[token_idx]}'"
    return expr, None


def _parse_prerequisite_expression(text: str) -> tuple[RuleExpr | None, str | None]:
    """Parse prerequisite text in dark and disturbing ways

    What can I say... I recommend that you don't look at this function.

    The goggles will not save your eyesight.

    It is a fragile tangle of regexes and heuristics that evolved organically to handle the
    horribly messy set of real-world catalogue prerequisite text in the handbook, many of
    which are ambiguous, malformed, or even just wrong.

    It is not intended to be a robust general-purpose parser. It should be replaced with a
    proper grammar-based parser with a decent data source.... hahahaha.

    For now it serves its purpose of extracting structured expressions from the most common
    prerequisite/corequisite formats while classifying unsupported clauses for lint triage.
    """
    raw = text.strip()
    if not raw:
        return None, None

    plus_parts = [
        part.strip() for part in re.split(r"(?i)\bPLUS\b", raw) if part.strip()
    ]
    parsed_parts: list[RuleExpr] = []

    for part in plus_parts:
        # Replace "including ..." with AND + any course codes found in that clause.
        def _expand_including(m: re.Match[str]) -> str:
            codes = COURSE_TOKEN_RE.findall(m.group(0))
            return (" AND " + " AND ".join(codes)) if codes else ""

        normalized_part = re.sub(
            r"(?i),?\s*\(?\s*\bINCLUDING\b.*$", _expand_including, part
        ).strip()
        normalized_part = re.sub(
            r"(?is)^\s*CURRENTLY\s+ENROLLED\s+IN\s+PROGRAM\b[^.]*\.\s*",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*STUDENTS?\s+(?:MUST|SHOULD)\s+HAVE\s+(?:SUCCESSFULLY\s+)?COMPLETED\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*.*?\bSTUDENTS?\s+MUST\s+HAVE\s+(?:SUCCESSFULLY\s+)?COMPLETED\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*STUDENTS?\s+NEED\s+TO\s+HAVE\s+(?:SUCCESSFULLY\s+)?COMPLETED\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:MUST|SHOULD)\s+HAVE\s+(?:SUCCESSFULLY\s+)?COMPLETED\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*STUDENTS?\s+ARE\s+REQUIRED\s+TO\s+HAVE\s+(?:SUCCESSFULLY\s+)?COMPLETED\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*STUDENTS?\s+MUST\s+BE\s+ENROLLED\s+IN\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:MUST|SHOULD)\s+BE\s+ENROLLED\s+IN\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bCOMPLETION\s+OF\b", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*SUCCESSFUL(?:LY)?\s+", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:SUCCESSFULLY\s+)?COMPLETED\s+(?:AT\s+LEAST\s+)?(\d+\s*UOC)\b",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bunits?\s+of\s+credits?\b",
            "UOC",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\b(?:SUCCESSFULLY\s+)?COMPLETED\s+([A-Z]{4}\d{4})\b",
            r"\1",
            normalized_part,
        ).strip()
        # Drop handbook qualifier fragments like "or equivalent" that are not tokenized.
        normalized_part = re.sub(
            r"(?i)\b(?:OR|AND)\s+EQUIVALENT(?:\s+COURSES?)?\b", "", normalized_part
        ).strip()
        # Normalize minimum-UOC variants.
        normalized_part = re.sub(
            r"(?i)\b(?:A\s+)?MINIMUM\s+OF\s+(\d+\s*UOC)\b",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\b(?:A\s+)?MINIMUM\s+(\d+\s*UOC)\b",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:SUCCESSFULLY\s+)?COMPLETED\s+(\d+\s*UOC)\b",
            r"\1",
            normalized_part,
        ).strip()
        # Normalize maturity variants like "24 UOC completed in XYZ courses".
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+COMPLETED\s+(?:IN|OF)\s+[A-Z][A-Z\s&/-]*\s+COURSES?",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+COMPLETED\s+IN\s+[A-Z][A-Z0-9\s&/-]*",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+IN\s+LEVEL\s+\d+(?:\s+[A-Z0-9][A-Z0-9\s&/-]*)?\s+COURSES?",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+OF\s+ANY\s+[A-Z]{4}(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)[A-Z]{4})+",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+[A-Z][A-Z\s&/-]*\s+COURSES?",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+IN\s+PROGRAM\b", r"\1", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)(\d+\s*UOC)\s+COMPLETED\b", r"\1", normalized_part
        ).strip()
        # Strip program-list enrolment clauses: "in program 8281, 8282 ..." (comma- or or/and-separated).
        normalized_part = re.sub(
            r"(?i)\b(?:OR\s+|AND\s+)?ENROL(?:MENT|LED)?\s+IN\s+PROGRAM\s+\d{3,4}(?:(?:\s*,\s*|\s+(?:OR|AND)\s+)\d{3,4})*",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\b(?:OR\s+|AND\s+)?IN\s+PROGRAM\s+\d{3,4}(?:(?:\s*,\s*|\s+(?:OR|AND)\s+)\d{3,4})*",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(r"\(\s*\)", " ", normalized_part).strip()
        normalized_part = re.sub(
            r"(?i)\bAT\s+UNSW\s+PRIOR\s+TO\s+THIS\s+COURSE\b", "", normalized_part
        ).strip()
        # Drop non-prerequisite enrolment/status clauses that often appear in prose.
        normalized_part = re.sub(
            r"(?i)\bAND\s+BE\s+ENROLLED\s+IN\b[^,;]*",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bAND\s+\d+\s*WAM\b.*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:A\s+)?MINIMUM\s+WAM(?:\s+OF)?\s+\d+%?\s+AND\s+",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bAND\s+(?:AND\s+)?(?:A\s+)?MINIMUM\s+WAM(?:\s+OF)?\s+\d+%?",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:A\s+)?MINIMUM\s+WAM(?:\s+OF)?\s+\d+%?\s*[,;:.+-]*$",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bAND\s+\d+(?:ST|ND|RD|TH)\s+YEAR\s+CORE\b.*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bBE\s+IN\s+GOOD\s+ACADEMIC\s+STANDING\b",
            "",
            normalized_part,
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bIN\s+ORDER\s+TO\s+ENRO?L\b\s*[,;:.+-]*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bTO\s+ENRO?L\b\s*[,;:.+-]*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bTO\s+UNDERTAKE\s+THIS\s+COURSE\b\s*[,;:.+-]*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)\bIN\s+THE\s+SAME\s+TERM\b\s*[,;:.+-]*$", "", normalized_part
        ).strip()
        normalized_part = re.sub(
            r"(?i)\.?\s*(?:[A-Z]+\s+)?CONSENT\s+REQUIRED\b.*$", "", normalized_part
        ).strip()
        # Drop trailing course-title text after explicit course codes.
        normalized_part = re.sub(
            r"(?i)\b([A-Z]{4}\d{4})\b"
            r"(?:\s+(?!(?:AND|OR|UOC)\b|[A-Z]{4}\d{4}\b)[A-Z][A-Z0-9'/-]*"
            r"(?:\s+(?!(?:AND|OR|UOC)\b|[A-Z]{4}\d{4}\b)[A-Z0-9][A-Z0-9'/-]*|"
            r"\s+(?:AND|OR)\s+(?!(?:[A-Z]{4}\d{4}\b|UOC\b|\d+\b))[A-Z][A-Z0-9'/-]*)*)"
            r"(?=(?:\s*,?\s+(?:AND|OR)\s+(?:[A-Z]{4}\d{4}\b|\d+\s*UOC\b)|[\s,;:.+-]*$))",
            r"\1",
            normalized_part,
        ).strip()
        normalized_part = re.sub(r"(?i)\bAND\s+AND\b", "AND", normalized_part).strip()
        normalized_part = re.sub(r"(?i)\bOR\s+OR\b", "OR", normalized_part).strip()
        normalized_part = re.sub(
            r"(?i)^\s*(?:AND|OR)\b\s*", "", normalized_part
        ).strip()
        normalized_part = re.sub(r"(?i)\b(?:AND|OR)\s*$", "", normalized_part).strip()
        # Treat maturity qualifiers as non-semantic for expression parsing.
        normalized_part = re.sub(r"(?i)\bAT\s+LEAST\b", "", normalized_part).strip()
        normalized_part = re.sub(r"(?i)\bOVERALL\b", "", normalized_part).strip()
        normalized_part = re.sub(r"[\s,;:.+-]+$", "", normalized_part).strip()
        # Some catalogue rows append trailing completion qualifiers after a valid requirement.
        normalized_part = re.sub(
            r"(?i)\b(?:SUCCESSFULLY\s+)?COMPLETED\b\s*[,;:.+-]*$",
            "",
            normalized_part,
        ).strip()
        completed_codes = COURSE_TOKEN_RE.findall(normalized_part)
        if (
            len(completed_codes) == 1
            and not UOC_TOKEN_RE.search(normalized_part)
            and re.match(r"(?i)^\s*[A-Z]{4}\d{4}\b", normalized_part)
        ):
            normalized_part = completed_codes[0]
        if (
            len(completed_codes) == 1
            and not UOC_TOKEN_RE.search(normalized_part)
            and re.match(
                r"(?i)^\s*(?:(?:STUDENTS?\s+(?:MUST|SHOULD)\s+HAVE\s+)|(?:STUDENTS?\s+ARE\s+REQUIRED\s+TO\s+HAVE\s+)|(?:(?:MUST|SHOULD)\s+HAVE\s+))?(?:PREVIOUSLY\s+)?(?:SUCCESSFULLY\s+)?COMPLETED\b",
                part,
            )
        ):
            normalized_part = completed_codes[0]
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
    # Catalogue variant "Pre or Corequisite:" is semantically a corequisite-only label.
    trimmed = re.sub(
        r"^\s*pre\s+or\s+co-?req(uisite)?s?\s*:\s*",
        "Corequisite: ",
        trimmed,
        flags=re.IGNORECASE,
    )
    # Strip labels: "Prerequisite or Corequisite:" first, then just "Prerequisite:".
    # Do NOT strip corequisite labels—the split function needs them to identify corequisite parts.
    trimmed = re.sub(
        r"^\s*pre-?req(uisite)?s?\s+or\s+co-?req(uisite)?s?\s*:?\s*",
        "",
        trimmed,
        flags=re.IGNORECASE,
    )
    trimmed = re.sub(
        r"^\s*pre(?:\s+|-)?req(?:uisite)?s?\s*:\s*", "", trimmed, flags=re.IGNORECASE
    )
    trimmed = re.sub(r"^\s*pre\s*:\s*", "", trimmed, flags=re.IGNORECASE)
    trimmed = re.sub(
        r"^\s*pre-?req(uisite)?s?\s*:?\s*", "", trimmed, flags=re.IGNORECASE
    )
    # Strip trailing punctuation and whitespace that may interfere with parsing.
    trimmed = re.sub(r"[\s,;:.+-]+$", "", trimmed)

    result: tuple[RuleExpr | None, RuleExpr | None, str | None]

    # Normalize and check for empty prerequisites (case-insensitive).
    trimmed_upper = trimmed.upper().strip()
    if not trimmed or trimmed_upper in {
        ".",
        "0",
        "NONE",
        "NIL",
        "N/A",
        "?",
        "NIL PREREQUISITES",
        "NO PREREQUISITES",
    }:
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
        r"(?i)\b(enrol(?:ment|led)\s+in\b|program\s+\d{3,4}\b|major\b|speciali[sz]ation\b)"
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
    r"(?i)(\b[A-Z]{4}\d{4}\b|(?:at\s+least|minimum)?\s*\d+\s*\+?\s*UOC\b)"
)

SALVAGE_STRIP_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "program_enrolment": [
        re.compile(r"(?i)\benrol(?:ment|led)?\s+in\b[^.]*\.\s*"),
        re.compile(
            r"(?i)\b(?:must\s+be\s+|be\s+)?enrol(?:ment|led)?\s+in\b.*?(?=\b(?:and|or)\s+(?:have\s+)?completed\b)"
        ),
        re.compile(
            r"(?i)\benrol(?:ment|led)?\s+in\b.*?(?=\b(?:and|or)\s+completion\s+of\b)"
        ),
        re.compile(r"(?i)\benrol(?:ment|led)?\s+in\b.*?(?=\b(?:and|or)\s+completed\b)"),
        re.compile(
            r"(?i)\benrol(?:ment|led)?\s+in\b.*?(?=\b(?:and|or)\s+(?:either\s+)?[A-Z]{4}\d{4}\b)"
        ),
        re.compile(r"(?i)\benrol(?:ment|led)?\s+in\b[^,;.)]*"),
        re.compile(
            r"(?i)\bin\s+program\s+\d{3,4}(?:(?:\s*,\s*|\s+(?:or|and)\s+)\d{3,4})*"
        ),
        re.compile(r"(?i)\bprogram\s+\d{3,4}(?:(?:\s*,\s*|\s+(?:or|and)\s+)\d{3,4})*"),
        re.compile(r"(?i)\b(?:single|double)\s+degree(?:s)?\b"),
        re.compile(r"\b[A-Z]{4}\d[A-Z]\b"),
        re.compile(r"\b[A-Z]{5}\d\b"),
        re.compile(r"\b[A-Z]{6}\b"),
        re.compile(r"\b[A-Z]{4}\b"),
        re.compile(r"(?i)\b[ A-Z0-9-]+\s+major\b"),
        re.compile(r"(?i)\b[ A-Z0-9-]+\s+speciali[sz]ation\b"),
    ],
    "application_approval": [
        re.compile(r"(?i)\blanguage\s+placement\s+approval\b[^,;.)]*"),
        re.compile(r"(?i)\bplacement\s+approval\b[^,;.)]*"),
        re.compile(r"(?i)\b(?:approval|permission|consent)\b[^,;.)]*"),
        re.compile(r"(?i)\bby\s+(?:consent|invitation)\b[^,;.)]*"),
        re.compile(r"(?i)\bapplication\s+only\b[^,;.)]*"),
    ],
    "wam_mark": [
        re.compile(r"(?i)\b\d+\+\s*wam\b"),
        re.compile(r"(?i)\bminimum\s+mark\b[^,;.)]*"),
        re.compile(r"(?i)\bmark\s+of\s+\d+\b"),
    ],
    "cohort_timing": [
        re.compile(r"(?i)\b(?:honours|final\s+term|final\s+year)\b[^,;.)]*"),
        re.compile(r"(?i)\bat\s+level\s+\d\b"),
    ],
}


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


def salvage_mixed_prerequisite_clause(
    text: str,
    matched_families: list[str],
) -> tuple[bool, RuleExpr | None, str | None]:
    """Try to salvage parseable expression from a mixed clause.

    Returns ``(salvaged, salvaged_expr, salvage_error)``.
    """
    stripped = text

    # Remove descriptive qualifiers and compound unit requirement phrasing
    # before family stripping so the parser sees cleaner input.
    stripped = re.sub(r"(?i)\b(?:units?\s+of\s+credit)\b", "UOC", stripped)
    stripped = re.sub(r"(?i)\s+overall\b", "", stripped)
    stripped = re.sub(
        r"(?i)\s+including\s+", " and ", stripped
    )  # Rewrite "including" as "and"
    stripped = re.sub(r"(?i),\s*including\s+", " and ", stripped)

    for family in matched_families:
        pattern = IGNORE_FAMILY_PATTERNS.get(family)
        if family in {"program_enrolment", "application_approval"}:
            for salvage_pattern in SALVAGE_STRIP_PATTERNS.get(family, []):
                stripped = salvage_pattern.sub(" ", stripped)
            if pattern is not None:
                stripped = pattern.sub(" ", stripped)
            continue

        if pattern is not None:
            stripped = pattern.sub(" ", stripped)
        for salvage_pattern in SALVAGE_STRIP_PATTERNS.get(family, []):
            stripped = salvage_pattern.sub(" ", stripped)

    # Clean obvious connector/punctuation debris left after family stripping.
    stripped = re.sub(r"(?i)(\d+\s*UOC)\s+OF\s+ANY\b", r"\1", stripped)
    stripped = re.sub(r"(?i)\b(AND|OR)\b\s*$", "", stripped)
    stripped = re.sub(r"(?i)^\s*(AND|OR)\b\s*", "", stripped)
    stripped = re.sub(r"(?i),\s*(AND|OR)\b", r" \1", stripped)
    stripped = re.sub(r"(?i)\(\s*(AND|OR)\b", "(", stripped)
    stripped = re.sub(r"(?i)\b(AND|OR)\s*\)", ")", stripped)
    stripped = re.sub(r"(?i)\b(AND|OR)\s+(AND|OR)\b", r"\1", stripped)
    # Strip trailing commas before connectors: ", and" → "and", then remove trailing "and"/"or"
    stripped = re.sub(r"(?i),\s*(AND|OR)$", "", stripped)
    # Remove trailing AND/OR even with trailing junk punctuation: " and ." or " and," etc.
    stripped = re.sub(r"(?i)\s+(AND|OR)[\s,;:.+-]*$", "", stripped)
    stripped = re.sub(r",\s*\)", ")", stripped)
    stripped = re.sub(r"(?i)\b(AND|OR)\s*\)", ")", stripped)
    stripped = re.sub(r"\(\s*\)", " ", stripped)
    stripped = re.sub(r"^[\s,;:.+-]+|[\s,;:.+-]+$", "", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    expr, _coreq_expr, err = parse_prerequisite_field(stripped)
    if err:
        return False, None, err
    if expr is None:
        return False, None, "no parseable prerequisite expression remained"
    return True, expr, None


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

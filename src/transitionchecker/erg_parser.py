"""Parse structured ERG Requisite Detail lines into prerequisite/corequisite text.

The STU055 ERG report encodes prerequisite rules as a series of rows, one per
clause, with boolean operators and parentheses embedded in the detail text.
This module converts those rows into clean human-readable text compatible with
``parse_prerequisite_field()`` in ``prereq_engine``.

Each ERG group is a list of rows for a single ``(ERG ID, career)`` combination,
sorted by ``Group Number``.  Rows that reference an external requirement table
(non-empty ``Requirement ID`` column) cannot always be parsed, but the parser
attempts extraction for known patterns (e.g. UoC completions) before falling
back.

Supported ERG Requisite Detail token types
------------------------------------------
``PRE CRSE <num> <code> [<mark>] [<N> course/s] [<N> units]``
    A prerequisite course.  Optional trailing integer (not followed by
    ``units`` or ``course/s``) is a minimum-mark requirement; marks > 50
    are captured in :attr:`ErgParseResult.mark_requirements`.  Trailing
    ``N course/s`` and ``N units`` annotations are consumed and ignored.

``CO CRSE <num> <code> [<mark>] [<N> course/s] [<N> units]``
    A corequisite course, same mark/annotation handling as ``PRE CRSE``.

``COND Academic Plan EQ <plan_code> [<N> units]``
    Enrolment condition on an academic plan.  Emitted as
    ``"Enrolment in Academic Plan <plan_code> [(<N> UoC)]"``.

``COND Academic Program EQ <program_code>``
    Enrolment condition on an academic program.  Emitted as
    ``"Enrolment in program <program_code>"``.

``COND <any other subtype> <operator> <value>``
    Any other enrolment condition (career, level, standing, IN/NI variants,
    etc.).  Emitted as ``"Enrolment in program <ID:<value>>"`` — the value
    token is preserved as an opaque reference.  All COND forms match the
    ``program_enrolment`` ignore family and are stripped during salvage.

``PRE/CO CRSW [<faculty>] [<course_pattern>] <N> units``
    Coursework maturity/coreq requirement (minimum UoC completed, optionally
    restricted to a faculty or course-code pattern).  Emitted as
    ``"Completion of N UoC [in <faculty> <pattern> courses]"``.

``PRE/CO CRSW [<faculty>] <course_pattern>``
    Pattern-based prerequisite with no UoC count (e.g. ``MDIA68##``).
    Emits the pattern token as a prereq; sibling specific course codes in
    the same OR group are salvaged by the downstream mixed-clause parser.

``PRE/CO RQ <req_id> Completed [min] <N> UoC ...``
``PRE/CO RQ <req_id> Completion of <N> UoC ...``
``PRE/CO RQ <req_id> <N> UoC ...``
    Unit-of-credit completion requirement in any common phrasing.
    Emitted as ``"Completion of N UoC"``.

``PRE/CO RQ <req_id> Overall Units [Check] <N>``
    Alternative UoC maturity form.  Emitted as ``"Completion of N UoC"``.

``PRE/CO RQ <req_id> Equivalents: <code1> <code2> ...``
    OR-list of equivalent courses.  Emitted as ``"CODE1 OR CODE2 OR ..."``.

``PRE/CO RQ <req_id> [Asymmetrical] Exclu... <courses>``
    Exclusion note in any common spelling (``Exclusion:``, ``Excl:``,
    ``Exclude``, ``Asymmetrical Exclusion:``, …).  Normalised to canonical
    ``"[Asymmetrical ]Exclusion: <body>"`` and emitted as a condition so
    that ``parse_prerequisite_field()`` short-circuits to ``(None, None, None)``.

``PRE/CO RQ <req_id> <prose containing XXXX0000 course codes>``
    Any other description whose text contains recognisable course codes.
    The codes are extracted with ``[A-Za-z]{4}\\d{4}`` and OR-joined.
    ``re.DOTALL`` is used so multi-line descriptions are searched fully.

``PRE/CO RQ <req_id> <other description>``
    Descriptions with no extractable content are unresolvable.  The caller
    falls back to the ERG Description text and records the entry in the
    fallback report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast

# ---------------------------------------------------------------------------
# ErgExpr — structured expression type
# ---------------------------------------------------------------------------

#: A structured, JSON-serialisable prerequisite/corequisite expression tree.
#:
#: Leaf nodes:
#:   ``{"prereq": "CEIC3004"}``          — course must be in prior_courses
#:   ``{"coreq": "CEIC3007"}``           — course in prior ∪ current_period
#:   ``{"prereq_pattern": "MDIA68##"}``  — any matching course in prior_courses
#:   ``{"coreq_pattern": "COMP3###"}``   — any matching course in prior ∪ current
#:   ``{"uoc": 48}``                     — ΣUoC(prior) ≥ 48
#:   ``{"uoc": 72, "restriction": "JURD####"}``  — filtered ΣUoC ≥ 72
#:   ``{"condition": "Enrolment in ..."}`` — always True (not enforceable)
#:
#: Combinators (recursive):
#:   ``{"and": [ErgExpr, ...]}``
#:   ``{"or":  [ErgExpr, ...]}``
ErgExpr = dict[str, Any]


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class ErgRow:
    """One row from the ERG Requisite Detail section of the STU055 report."""

    group_number: str
    """Sort key within the ERG group (e.g. ``"0010"``, ``"0020"``)."""

    detail_text: str
    """Raw text of the ERG Requisite Detail cell."""

    requirement_id: str
    """Non-empty when this row references an external requirement table."""


@dataclass
class ErgParseResult:
    """Result of parsing one ``(ERG ID, career)`` group."""

    prereq_text: str
    """Reconstructed prerequisite text, or ``""`` if none."""

    coreq_text: str
    """Reconstructed corequisite text, or ``""`` if none."""

    has_unresolvable: bool
    """``True`` when at least one row could not be resolved.  The caller
    should fall back to the ERG Description text."""

    mark_requirements: dict[str, int] = field(
        default_factory=lambda: cast(dict[str, int], {})
    )
    """Minimum-mark requirements by course code.  Only marks **> 50** are
    recorded.  Populated even when *has_unresolvable* is ``True`` (the
    marks from resolved ``PRE CRSE`` rows are still valid)."""

    unresolvable_lines: list[str] = field(
        default_factory=lambda: cast(list[str], [])
    )
    """Raw detail-text strings that could not be parsed.  Populated when
    *has_unresolvable* is ``True`` to assist the fallback report."""


# ---------------------------------------------------------------------------
# Internal token type
# ---------------------------------------------------------------------------

# PRE CRSE <num> <COURSE_CODE> [<mark>] [<N> course/s] [<N> units]
# The optional <mark> must NOT be followed by "units" or "course/s" (which
# would make it a count/UoC annotation rather than a mark).
_PRE_CRSE_RE = re.compile(
    r"^PRE\s+CRSE\s+\d+\s+(?P<code>[A-Z]{4}\d{4})"
    r"(?:\s+(?P<mark>\d+)(?!\s+(?:units?|courses?(?:/s)?)\b))?"
    r"(?:\s+\d+\s+courses?(?:/s?)?)?"
    r"(?:\s+\d+\s+units?)?\s*$",
    re.IGNORECASE,
)

# CO CRSE <num> <COURSE_CODE> [<mark>] [<N> course/s] [<N> units]
_CO_CRSE_RE = re.compile(
    r"^CO\s+CRSE\s+\d+\s+(?P<code>[A-Z]{4}\d{4})"
    r"(?:\s+(?P<mark>\d+)(?!\s+(?:units?|courses?(?:/s)?)\b))?"
    r"(?:\s+\d+\s+courses?(?:/s?)?)?"
    r"(?:\s+\d+\s+units?)?\s*$",
    re.IGNORECASE,
)

# COND Academic Plan EQ <PLAN_CODE> [<N> units]
_COND_PLAN_RE = re.compile(
    r"^COND\s+Academic\s+Plan\s+EQ\s+(?P<plan>\S+)(?:\s+(?P<uoc>\d+)\s+units?)?",
    re.IGNORECASE,
)

# COND Academic Program EQ <PROGRAM_CODE>
_COND_PROGRAM_RE = re.compile(
    r"^COND\s+Academic\s+Program\s+EQ\s+(?P<prog>\S+)",
    re.IGNORECASE,
)

# Any other COND subtype (career, level, standing, …)
_COND_ANY_RE = re.compile(r"^COND\s+", re.IGNORECASE)

# PRE RQ / CO RQ with a recognisable UoC completion in the description text.
# Handles all common forms:
#   "Completed 84 UoC target career 84 units"
#   "Completed min 100 UOC"           (with "min" qualifier)
#   "Completion of 144 UOC in Target Career"
#   "12 UoC in target career"          (bare N UoC)
_PRE_RQ_UOC_RE = re.compile(
    r"^(?:PRE|CO)\s+RQ\s+\d+\s+(?:.*?Complet(?:ed|ion\s+of)\s+(?:min\s+)?)?(?P<uoc>\d+)\s+UoC\b",
    re.IGNORECASE,
)

# PRE RQ / CO RQ with an "Overall Units [Check] N" maturity check.
# e.g. "PRE RQ 000500005 Overall Units 12"
#      "PRE RQ 000005004 Overall Units Check 96"
_PRE_RQ_OVERALL_UNITS_RE = re.compile(
    r"^(?:PRE|CO)\s+RQ\s+\d+\s+.*?Overall\s+Units\s+(?:Check\s+)?(?P<uoc>\d+)\b",
    re.IGNORECASE | re.DOTALL,
)

# PRE RQ / CO RQ whose description is an "Equivalents:" course list
# e.g. "PRE RQ 490000009 Equivalents: ARCH1101 BENV1012 INTA1001"
_PRE_RQ_EQUIVALENTS_RE = re.compile(
    r"^(?:PRE|CO)\s+RQ\s+\d+\s+Equivalents?:\s*(?P<codes>.+)$",
    re.IGNORECASE,
)

# PRE RQ / CO RQ whose description is an exclusion note in any common form:
#   "Exclusion: AERO4500"
#   "Asymmetrical Exclusion: BIOM9914"
#   "Excl: COMP9021"          (abbreviated, with colon)
#   "Exclude INFS5602, INFS5978"  (verb form, no colon)
# The named group <asym> captures an optional "Asymmetrical" prefix;
# <body> captures everything after the exclusion keyword and its separator.
_PRE_RQ_EXCLUSION_RE = re.compile(
    r"^(?:PRE|CO)\s+RQ\s+\d+\s+"
    r"(?P<asym>Asym(?:metr?ical?)?\s+)?"
    r"(?:Exclu(?:sion|d(?:e[sd]?|ing?)?)[s]?|Excl)"
    r"\s*[:;\s]\s*"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)

# PRE/CO CRSW [FACULTY] [COURSE_PATTERN] <N> units  — UoC maturity/coreq requirement.
_PRE_CRSW_RE = re.compile(
    r"^(?P<rq_type>PRE|CO)\s+CRSW\s+(?P<prefix>.*?)\s*(?P<uoc>\d+)\s+units?\s*$",
    re.IGNORECASE,
)

# PRE/CO CRSW [FACULTY] <COURSE_PATTERN>  — pattern-based prereq (no UoC count).
_PRE_CRSW_PATTERN_RE = re.compile(
    r"^(?P<rq_type>PRE|CO)\s+CRSW\s+(?P<prefix>.+?)\s*$",
    re.IGNORECASE,
)

# Matches a PeopleSoft-style course-pattern token, e.g. "JURD####" or "CEIC7###".
_CRSW_COURSE_PATTERN_RE = re.compile(
    r"(?P<pattern>[A-Za-z]{4}[#\d]{4})",
)

# ---------------------------------------------------------------------------
# Detail line handler
# ---------------------------------------------------------------------------

class _DetailResult(NamedTuple):
    subject: str      # extracted value (course code, UoC text, condition text)
    kind: str         # "prereq", "coreq", "condition", "unresolvable"
    min_mark: int | None = None  # minimum mark for prereq/coreq (> 50 only)


def _handle_detail_type(type_text: str, requirement_id: str) -> _DetailResult:
    """Parse the type portion of one ERG Requisite Detail line.

    Returns a ``_DetailResult`` whose ``kind`` is one of:

    - ``"prereq"`` — a prerequisite course code or UoC completion requirement
    - ``"coreq"`` — a corequisite course code
    - ``"condition"`` — an enrolment condition (ignorable text)
    - ``"unresolvable"`` — could not be parsed; the caller should use fallback
    """
    type_text = type_text.strip()

    # ── PRE CRSE ────────────────────────────────────────────────────────────
    m = _PRE_CRSE_RE.match(type_text)
    if m:
        code = m.group("code").upper()
        mark_raw = m.group("mark")
        mark = int(mark_raw) if mark_raw is not None else None
        return _DetailResult(code, "prereq", mark)

    # ── CO CRSE ─────────────────────────────────────────────────────────────
    m = _CO_CRSE_RE.match(type_text)
    if m:
        code = m.group("code").upper()
        mark_raw = m.group("mark")
        mark = int(mark_raw) if mark_raw is not None else None
        return _DetailResult(code, "coreq", mark)

    # ── COND: specific Academic Plan subtype ────────────────────────────────
    m = _COND_PLAN_RE.match(type_text)
    if m:
        plan = m.group("plan")
        uoc_raw = m.group("uoc")
        label = (
            f"Enrolment in Academic Plan {plan} ({uoc_raw} UoC)"
            if uoc_raw
            else f"Enrolment in Academic Plan {plan}"
        )
        return _DetailResult(label, "condition")

    # ── COND: Academic Program subtype ─────────────────────────────────
    m = _COND_PROGRAM_RE.match(type_text)
    if m:
        prog = m.group("prog")
        return _DetailResult(f"Enrolment in program {prog}", "condition")

    # ── COND: any other subtype (career, program IN/NI, level, standing …) ───────
    # All COND types are enrolment conditions — ignorable by the existing parser.
    # Preserve the identifier value as an opaque token so the output is still
    # human-readable, e.g. "Enrolment in program <ID:000037>".
    if _COND_ANY_RE.match(type_text):
        tokens = type_text.strip().split()
        # Structure: COND <Attribute> [<Subattr>] <Operator> <Value>
        # The identifier is the last token.
        identifier = tokens[-1] if len(tokens) >= 2 else ""
        label = (
            f"Enrolment in program <ID:{identifier}>"
            if identifier
            else "Enrolment in program"
        )
        return _DetailResult(label, "condition")

    # ── PRE/CO CRSW: UoC maturity requirement ───────────────────────────────
    m = _PRE_CRSW_RE.match(type_text)
    if m:
        is_coreq = m.group("rq_type").upper() == "CO"
        uoc = int(m.group("uoc"))
        prefix = m.group("prefix").strip()
        course_pattern_m = _CRSW_COURSE_PATTERN_RE.search(prefix)
        if course_pattern_m:
            course_pattern = course_pattern_m.group("pattern")
            faculty = prefix[: course_pattern_m.start()].strip()
        else:
            course_pattern = ""
            faculty = prefix
        restriction = " ".join(filter(None, [faculty, course_pattern]))
        subject = (
            f"Completion of {uoc} UoC in {restriction} courses"
            if restriction
            else f"Completion of {uoc} UoC"
        )
        return _DetailResult(subject, "coreq" if is_coreq else "prereq")

    # ── PRE/CO CRSW: course-pattern prerequisite (no UoC count) ─────────────
    m = _PRE_CRSW_PATTERN_RE.match(type_text)
    if m:
        is_coreq = m.group("rq_type").upper() == "CO"
        prefix = m.group("prefix").strip()
        course_pattern_m = _CRSW_COURSE_PATTERN_RE.search(prefix)
        if course_pattern_m:
            return _DetailResult(course_pattern_m.group("pattern"), "coreq" if is_coreq else "prereq")
        return _DetailResult(
            f"Completion of {prefix} course" if prefix else "Completion",
            "condition",
        )

    # ── PRE RQ / CO RQ: equivalents list ────────────────────────────────────
    m = _PRE_RQ_EQUIVALENTS_RE.match(type_text)
    if m:
        codes = re.findall(r"[A-Za-z]{4}\d{4}", m.group("codes"))
        if codes:
            return _DetailResult(" OR ".join(c.upper() for c in codes), "prereq")
        # No valid course codes found — fall through to unresolvable

    # ── PRE RQ: exclusion note ───────────────────────────────────────────
    m = _PRE_RQ_EXCLUSION_RE.match(type_text)
    if m:
        # Normalise to canonical "[Asymmetrical ]Exclusion: <body>" so that
        # parse_prerequisite_field() always hits the fast-path short-circuit
        # at line 209 of prereq_engine.py regardless of the source abbreviation.
        asym = m.group("asym") or ""
        prefix = f"{asym}Exclusion: " if asym else "Exclusion: "
        return _DetailResult(f"{prefix}{m.group('body').strip()}", "condition")

    # ── PRE RQ / CO RQ: UoC completion ─────────────────────────────────────
    m = _PRE_RQ_UOC_RE.match(type_text)
    if m:
        uoc = int(m.group("uoc"))
        return _DetailResult(f"Completion of {uoc} UoC", "prereq")

    m = _PRE_RQ_OVERALL_UNITS_RE.match(type_text)
    if m:
        uoc = int(m.group("uoc"))
        return _DetailResult(f"Completion of {uoc} UoC", "prereq")

    # ── PRE RQ / CO RQ: everything else ──────────────────────────────────────
    # re.DOTALL is required: some RQ descriptions contain embedded newlines
    # (e.g. multi-line rules like "RULE1:\nenrolled in ...") and without it
    # the `.+` stops at the first \n, missing course codes on later lines.
    m = re.match(
        r"^(?:PRE|CO)\s+RQ\s+\d+\s+(?P<desc>.+)$",
        type_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        desc = m.group("desc").strip()
        # Before giving up, try to extract any embedded course codes and form
        # an OR expression.  This handles human-written descriptions such as
        # "one of the following, MUSC1602, MUSC1807, or MUSC1604".
        codes = re.findall(r"\b[A-Za-z]{4}\d{4}\b", desc)
        if codes:
            return _DetailResult(" OR ".join(c.upper() for c in codes), "prereq")
        return _DetailResult(desc, "unresolvable")

    # Truly unknown token — treat as unresolvable so the caller falls back.
    return _DetailResult(type_text, "unresolvable")


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def parse_erg_group(rows: list[ErgRow]) -> ErgParseResult:
    """Parse a sorted list of ERG rows for one ``(ERG ID, career)`` group.

    ``rows`` must be sorted by ``group_number`` ascending (the caller is
    responsible for this).

    The function rebuilds prerequisite and corequisite text that is compatible
    with ``parse_prerequisite_field()`` from ``prereq_engine``.

    Returns
    -------
    ErgParseResult
        ``prereq_text``, ``coreq_text``, ``has_unresolvable``,
        ``mark_requirements``, and ``unresolvable_lines``.
        When ``has_unresolvable`` is ``True``, the caller should fall back to
        the ERG Description text rather than using the reconstructed strings.
        ``mark_requirements`` is populated from resolved ``PRE CRSE`` / ``CO
        CRSE`` rows regardless of whether the group falls back.
    """
    prereq_parts: list[str] = []   # tokens for the prereq expression
    coreq_parts: list[str] = []    # tokens for the coreq expression
    mark_requirements: dict[str, int] = {}
    has_unresolvable = False
    unresolvable_lines: list[str] = []

    for row in rows:
        raw = row.detail_text.strip()
        if not raw:
            continue

        # Strip leading open-paren.
        open_paren = ""
        if raw.startswith("("):
            open_paren = "( "
            raw = raw[1:].strip()

        # Strip trailing close-paren.
        close_paren = ""
        if raw.endswith(")"):
            close_paren = " )"
            raw = raw[:-1].strip()

        # Extract optional leading AND/OR operator.
        operator = ""
        upper = raw.upper()
        if upper.startswith("AND "):
            operator = "AND"
            raw = raw[4:].strip()
        elif upper.startswith("OR "):
            operator = "OR"
            raw = raw[3:].strip()

        # A leading paren may follow the operator: "AND (PRE CRSE ..."
        if not open_paren and raw.startswith("("):
            open_paren = "( "
            raw = raw[1:].strip()

        detail_result = _handle_detail_type(raw, row.requirement_id)

        if detail_result.kind == "unresolvable":
            has_unresolvable = True
            unresolvable_lines.append(row.detail_text.strip())
            continue

        # Capture mark requirements from all resolved PRE CRSE / CO CRSE rows,
        # even if other rows in the same group are later found to be unresolvable.
        if detail_result.min_mark is not None and detail_result.min_mark > 50:
            mark_requirements[detail_result.subject] = detail_result.min_mark

        if detail_result.kind == "condition":
            parts = prereq_parts
        elif detail_result.kind == "coreq":
            parts = coreq_parts
        else:  # prereq
            parts = prereq_parts

        # Drop the operator when it would become the very first token in the
        # destination list — an operator carried over from the other list (e.g.
        # "OR PRE CRSE" following several "CO CRSE" lines) would be dangling.
        effective_operator = operator if parts else ""

        # Build token: operator then open_paren then subject then close_paren.
        # The operator must precede the paren: "AND ( COMM1180" not "( AND COMM1180".
        token_parts: list[str] = []
        if effective_operator:
            token_parts.append(effective_operator)
        if open_paren:
            token_parts.append("(")
        token_parts.append(detail_result.subject)
        if close_paren:
            token_parts.append(")")
        parts.append(" ".join(token_parts))

    prereq_text = " ".join(prereq_parts)
    coreq_text = " ".join(coreq_parts)
    return ErgParseResult(
        prereq_text=prereq_text,
        coreq_text=coreq_text,
        has_unresolvable=has_unresolvable,
        mark_requirements=mark_requirements,
        unresolvable_lines=unresolvable_lines,
    )


def build_prerequisites_field(result: ErgParseResult) -> str:
    """Combine prereq, coreq, and mark requirements into a single ``prerequisites`` field.

    Format: ``"<prereq>. Corequisite: <coreq>. Minimum mark of N in COURSE"``

    The prereq/coreq split convention matches ``parse_prerequisite_field()``.
    Mark requirements are appended as human-readable sentences whose text
    matches the ``wam_mark`` ignore family so the prerequisite parser treats
    them as ignorable and salvages the course codes.
    """
    parts: list[str] = []
    if result.prereq_text:
        parts.append(result.prereq_text)
    if result.coreq_text:
        parts.append(f"Corequisite: {result.coreq_text}")
    for code, mark in sorted(result.mark_requirements.items()):
        parts.append(f"Minimum mark of {mark} in {code}")
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# ErgExpr builder — structured expression tree from ERG rows
# ---------------------------------------------------------------------------

# Cache of compiled pattern regexes keyed by the raw pattern string.
_PATTERN_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def match_erg_pattern(pattern: str, course_code: str) -> bool:
    """Return True if *course_code* matches the ERG course pattern.

    ``#`` is treated as a single digit wildcard; all other characters must
    match literally (case-insensitive).  For example ``JURD####`` matches
    any ``JURD`` course at any level.

    Compiled regexes are cached by pattern string so repeated calls for the
    same pattern (common in plan validation loops) avoid recompilation.
    """
    compiled = _PATTERN_REGEX_CACHE.get(pattern)
    if compiled is None:
        parts = pattern.split("#")
        regex_body = r"\d".join(re.escape(p) for p in parts)
        compiled = re.compile(f"(?i)^{regex_body}$")
        _PATTERN_REGEX_CACHE[pattern] = compiled
    return bool(compiled.match(course_code))


def _handle_detail_type_to_erg_leaf(
    type_text: str,
    requirement_id: str,
) -> ErgExpr | None:
    """Parse one ERG Requisite Detail type-portion into an :data:`ErgExpr` leaf.

    Returns ``None`` when the token is unresolvable (caller should abort
    :func:`build_erg_expr` and use the fallback text path).
    """
    type_text = type_text.strip()

    # ── PRE CRSE ────────────────────────────────────────────────────────────
    m = _PRE_CRSE_RE.match(type_text)
    if m:
        return {"prereq": m.group("code").upper()}

    # ── CO CRSE ─────────────────────────────────────────────────────────────
    m = _CO_CRSE_RE.match(type_text)
    if m:
        return {"coreq": m.group("code").upper()}

    # ── COND: specific Academic Plan subtype ────────────────────────────────
    m = _COND_PLAN_RE.match(type_text)
    if m:
        plan = m.group("plan")
        uoc_raw = m.group("uoc")
        label = (
            f"Enrolment in Academic Plan {plan} ({uoc_raw} UoC)"
            if uoc_raw
            else f"Enrolment in Academic Plan {plan}"
        )
        return {"condition": label}

    # ── COND: Academic Program subtype ──────────────────────────────────────
    m = _COND_PROGRAM_RE.match(type_text)
    if m:
        return {"condition": f"Enrolment in program {m.group('prog')}"}

    # ── COND: any other subtype ──────────────────────────────────────────────
    if _COND_ANY_RE.match(type_text):
        tokens = type_text.strip().split()
        identifier = tokens[-1] if len(tokens) >= 2 else ""
        label = (
            f"Enrolment in program <ID:{identifier}>"
            if identifier
            else "Enrolment in program"
        )
        return {"condition": label}

    # ── PRE/CO CRSW: UoC maturity requirement ───────────────────────────────
    m = _PRE_CRSW_RE.match(type_text)
    if m:
        is_coreq = m.group("rq_type").upper() == "CO"
        uoc = int(m.group("uoc"))
        prefix = m.group("prefix").strip()
        course_pattern_m = _CRSW_COURSE_PATTERN_RE.search(prefix)
        if course_pattern_m:
            restriction = course_pattern_m.group("pattern")
            return {"uoc": uoc, "restriction": restriction}
        return {"uoc": uoc}

    # ── PRE/CO CRSW: course-pattern prerequisite (no UoC count) ─────────────
    m = _PRE_CRSW_PATTERN_RE.match(type_text)
    if m:
        is_coreq = m.group("rq_type").upper() == "CO"
        prefix = m.group("prefix").strip()
        course_pattern_m = _CRSW_COURSE_PATTERN_RE.search(prefix)
        if course_pattern_m:
            key = "coreq_pattern" if is_coreq else "prereq_pattern"
            return {key: course_pattern_m.group("pattern")}
        # No recognisable pattern — ignorable condition
        return {"condition": f"Completion of {prefix} course" if prefix else "Completion"}

    # ── PRE RQ / CO RQ: equivalents list ────────────────────────────────────
    m = _PRE_RQ_EQUIVALENTS_RE.match(type_text)
    if m:
        codes = re.findall(r"[A-Za-z]{4}\d{4}", m.group("codes"))
        if codes:
            children: list[ErgExpr] = [{"prereq": c.upper()} for c in codes]
            return children[0] if len(children) == 1 else {"or": children}

    # ── PRE RQ / CO RQ: exclusion note ──────────────────────────────────────
    m = _PRE_RQ_EXCLUSION_RE.match(type_text)
    if m:
        asym = m.group("asym") or ""
        prefix = f"{asym}Exclusion: " if asym else "Exclusion: "
        return {"condition": f"{prefix}{m.group('body').strip()}"}

    # ── PRE RQ / CO RQ: UoC completion ──────────────────────────────────────
    m = _PRE_RQ_UOC_RE.match(type_text)
    if m:
        return {"uoc": int(m.group("uoc"))}

    m = _PRE_RQ_OVERALL_UNITS_RE.match(type_text)
    if m:
        return {"uoc": int(m.group("uoc"))}

    # ── PRE RQ / CO RQ: everything else ─────────────────────────────────────
    m = re.match(
        r"^(?:PRE|CO)\s+RQ\s+\d+\s+(?P<desc>.+)$",
        type_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        desc = m.group("desc").strip()
        codes = re.findall(r"\b[A-Za-z]{4}\d{4}\b", desc)
        if codes:
            children2: list[ErgExpr] = [{"prereq": c.upper()} for c in codes]
            return children2[0] if len(children2) == 1 else {"or": children2}
        # Truly unresolvable
        return None

    return None  # Unknown token — unresolvable


def _combine_atoms(atoms: list[tuple[str, ErgExpr]]) -> ErgExpr | None:
    """Combine a frame's ``(operator, atom)`` pairs into a single ErgExpr.

    The first atom's operator is always ``""`` (it is the frame's first element
    and has no preceding sibling within the group).  Subsequent operators are
    ``"AND"`` or ``"OR"``.

    When operators are mixed, standard boolean precedence is applied: AND binds
    tighter than OR (consecutive AND-runs are grouped first, then OR-ed).
    """
    if not atoms:
        return None
    if len(atoms) == 1:
        return atoms[0][1]

    ops = [op for op, _ in atoms[1:]]

    if all(op == "AND" for op in ops):
        return {"and": [a for _, a in atoms]}
    if all(op == "OR" for op in ops):
        return {"or": [a for _, a in atoms]}

    # Mixed AND/OR — apply precedence
    or_groups: list[ErgExpr] = []
    and_run: list[ErgExpr] = [atoms[0][1]]
    for op, atom in atoms[1:]:
        if op == "AND":
            and_run.append(atom)
        else:  # OR
            or_groups.append(and_run[0] if len(and_run) == 1 else {"and": and_run})
            and_run = [atom]
    or_groups.append(and_run[0] if len(and_run) == 1 else {"and": and_run})
    return or_groups[0] if len(or_groups) == 1 else {"or": or_groups}


def build_erg_expr(rows: list[ErgRow]) -> ErgExpr | None:
    """Build a structured :data:`ErgExpr` tree directly from sorted ERG rows.

    This produces a single combined expression (unlike :func:`parse_erg_group`
    which produces a text string split across prereq and coreq).  The leaf
    atoms carry their timing requirement (``"prereq"`` / ``"coreq"``) so no
    separate split is needed.

    Returns ``None`` when any row is unresolvable — the caller should fall back
    to the ERG Description text path via :func:`parse_erg_group`.

    ``rows`` must already be sorted by ``group_number`` ascending.
    """
    # stack[i] = list of (operator, ErgExpr) — "operator" connects this atom
    # to the preceding atom within the same group frame.  The first atom in
    # every frame has operator="".
    stack: list[list[tuple[str, ErgExpr]]] = [[]]
    # Parallel: how each frame connects to its parent when closed.
    trigger_ops: list[str] = [""]

    for row in rows:
        raw = row.detail_text.strip()
        if not raw:
            continue

        # ── Strip leading open-paren ─────────────────────────────────────────
        open_paren = False
        if raw.startswith("("):
            open_paren = True
            raw = raw[1:].strip()

        # ── Strip trailing close-paren ───────────────────────────────────────
        close_paren = False
        if raw.endswith(")"):
            close_paren = True
            raw = raw[:-1].strip()

        # ── Extract leading AND/OR operator ──────────────────────────────────
        operator = ""
        upper = raw.upper()
        if upper.startswith("AND "):
            operator = "AND"
            raw = raw[4:].strip()
        elif upper.startswith("OR "):
            operator = "OR"
            raw = raw[3:].strip()

        # ── Leading paren may follow the operator: "AND (" ───────────────────
        if not open_paren and raw.startswith("("):
            open_paren = True
            raw = raw[1:].strip()

        leaf = _handle_detail_type_to_erg_leaf(raw, row.requirement_id)
        if leaf is None:
            return None  # unresolvable row — caller uses fallback

        if open_paren:
            # Start a new group.  This atom is the FIRST element (op="").
            # The current `operator` becomes the trigger connecting the group
            # to the parent frame when the group is eventually closed.
            stack.append([("", leaf)])
            trigger_ops.append(operator)
        else:
            stack[-1].append((operator, leaf))

        if close_paren:
            if len(stack) <= 1:
                # Unbalanced paren — treat as recoverable (ignore extra close)
                continue
            frame = stack.pop()
            trigger = trigger_ops.pop()
            combined = _combine_atoms(frame)
            if combined is not None:
                stack[-1].append((trigger, combined))

    if not stack:
        return None
    return _combine_atoms(stack[0])


# ---------------------------------------------------------------------------
# rule_exprs_to_erg_expr — wrap text-parser output into ErgExpr
# ---------------------------------------------------------------------------

def _wrap_rule_expr(expr: Any, kind: str) -> ErgExpr:
    """Recursively tag all string leaves in *expr* with *kind* (``"prereq"`` or
    ``"coreq"``).  Dict-based nodes (``and``, ``or``, ``uoc``, ``min``/``from``)
    are reconstructed with their children re-tagged.
    """
    if isinstance(expr, str):
        return {kind: expr}
    if not isinstance(expr, dict):
        return {"condition": str(expr)}

    d = cast(dict[str, Any], expr)
    if "uoc" in d and len(d) == 1:
        return {"uoc": cast(int, d["uoc"])}

    for op in ("and", "or"):
        if op in d:
            children = [_wrap_rule_expr(c, kind) for c in cast(list[Any], d[op])]
            return {op: children}

    if "min" in d and "from" in d:
        children = [_wrap_rule_expr(c, kind) for c in cast(list[Any], d["from"])]
        return {"min": d["min"], "from": children}

    # Unknown shape — treat as condition (ignorable)
    return {"condition": str(d)}


def rule_exprs_to_erg_expr(
    prereq_expr: Any,
    coreq_expr: Any,
) -> ErgExpr:
    """Convert the text-parser's ``(prereq_expr, coreq_expr)`` split into a
    single combined :data:`ErgExpr`.

    String leaves in *prereq_expr* are tagged ``{"prereq": code}``; string
    leaves in *coreq_expr* are tagged ``{"coreq": code}``.  Both halves are
    combined with ``{"and": [...]}`` when both are present.

    This allows the unified :func:`evaluate_erg_expression` evaluator to handle
    entries that went through the text-parsing path without requiring a separate
    code path.
    """
    tagged_prereq = _wrap_rule_expr(prereq_expr, "prereq") if prereq_expr is not None else None
    tagged_coreq = _wrap_rule_expr(coreq_expr, "coreq") if coreq_expr is not None else None

    if tagged_prereq is not None and tagged_coreq is not None:
        return {"and": [tagged_prereq, tagged_coreq]}
    if tagged_prereq is not None:
        return tagged_prereq
    if tagged_coreq is not None:
        return tagged_coreq
    # No requirement at all
    return {"condition": ""}

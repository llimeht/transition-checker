"""Shared rules-file resolution and programme metadata loading.

The functions here are used by both the extract-plans and plan-validate
workflows so that rules-file lookup logic lives in exactly one place.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, TypedDict, cast

logger = logging.getLogger(__name__)

_YEAR_RANGE_RE = re.compile(r"^(\d{4})-(\d{4})$")
_INTAKE_YEAR_RE = re.compile(r"^\d{4}$")
# Matches a trailing parenthesised suffix with optional surrounding whitespace,
# e.g. "(48 UoC RPL)" or "(48RPL)".
_TRAILING_PARENS_RE = re.compile(r"\s*\([^()]*\)\s*$")


class ProgramRef(TypedDict):
    id: str
    name: str


class SpecialisationRef(TypedDict):
    id: str
    name: str


class RulesMetadata(TypedDict):
    plan_code: str
    plan_description: str
    program: ProgramRef
    specialisation: list[SpecialisationRef]
    uoc: int
    rules_description: str


# ---------------------------------------------------------------------------
# Rules-file resolution
# ---------------------------------------------------------------------------


def _has_rule(plan_code: str, rules_dir: Path) -> bool:
    """Return True when at least one rules file exists for *plan_code*."""
    if (rules_dir / f"{plan_code}.json").exists():
        return True
    for candidate in rules_dir.glob("*.json"):
        stem = candidate.stem
        if stem.startswith(f"{plan_code}-") and _YEAR_RANGE_RE.fullmatch(
            stem[len(plan_code) + 1 :]
        ):
            return True
    return False


def resolve_plan_code(plan_code: str, rules_dir: Path) -> str:
    """Return the effective plan code for rules-file lookup.

    Tries *plan_code* exactly first.  When no matching rules file exists,
    progressively crops the trailing end in this order and retries:

    1. Trailing parenthesised suffix — e.g. ``CEICAH3707(RPL)`` → ``CEICAH3707``
    2. Everything after the rightmost ``_``
    3. Everything after the rightmost ``-``

    Each step is repeated until a match is found or no further cropping is
    possible.  Returns *plan_code* unchanged when no match is found, so that
    callers still receive a meaningful "file not found" path.
    """
    current = plan_code.strip()
    seen: set[str] = set()

    while current and current not in seen:
        seen.add(current)
        if _has_rule(current, rules_dir):
            return current

        # Strip trailing (...) with optional surrounding whitespace
        m = _TRAILING_PARENS_RE.search(current)
        if m:
            candidate = current[: m.start()].strip()
            if candidate and candidate not in seen:
                current = candidate
                continue

        # Strip at rightmost _
        idx = current.rfind("_")
        if idx > 0:
            candidate = current[:idx].strip()
            if candidate and candidate not in seen:
                current = candidate
                continue

        # Strip at rightmost -
        idx = current.rfind("-")
        if idx > 0:
            candidate = current[:idx].strip()
            if candidate and candidate not in seen:
                current = candidate
                continue

        break

    return plan_code


def resolve_rule_file(plan_code: str, intake_year: int | None, rules_dir: Path) -> Path:
    """Return the best matching rule file for *plan_code*.

    First resolves the effective plan code via :func:`resolve_plan_code`, which
    progressively crops trailing suffixes until a matching rules file is found.

    Once the effective code is known, year-range selection is applied: when
    *intake_year* is provided and a date-ranged file exists that covers that
    year (e.g. ``PROGRAM-2026-2029.json``), that file is preferred over the
    plain ``PROGRAM.json``.
    """
    effective_code = resolve_plan_code(plan_code, rules_dir)
    if intake_year is not None:
        for candidate in sorted(rules_dir.glob("*.json")):
            stem = candidate.stem
            if not stem.startswith(f"{effective_code}-"):
                continue
            range_part = stem[len(effective_code) + 1 :]
            m = _YEAR_RANGE_RE.fullmatch(range_part)
            if not m:
                continue
            if int(m.group(1)) <= intake_year <= int(m.group(2)):
                return candidate
    return rules_dir / f"{effective_code}.json"


def resolve_rule_file_for_plan(
    plan_code: str, plan_stem: str, rules_dir: Path
) -> Path:
    """Resolve a rules file from a plan *filename stem* (e.g. ``CEICAH3707_2026_T1``).

    This is the variant used by the validation workflow, which works from
    already-exported plan JSON filenames rather than live intake strings.
    """
    intake_year: int | None = None
    prefix = f"{plan_code}_"
    if plan_stem.startswith(prefix):
        for segment in plan_stem[len(prefix) :].split("_"):
            if _INTAKE_YEAR_RE.fullmatch(segment):
                intake_year = int(segment)
                break
    return resolve_rule_file(plan_code, intake_year, rules_dir)


def intake_year_from_intake_string(intake: str) -> int | None:
    """Extract the 4-digit year from an intake string such as ``"2026 T1"``."""
    m = re.match(r"^(\d{4})\b", intake.strip())
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Metadata loading
# ---------------------------------------------------------------------------


def load_rules_metadata(
    rules_file: Path,
    plan_code: str,
    plan_description: str,
) -> RulesMetadata | None:
    """Load *rules_file* and return a :class:`RulesMetadata` dict.

    Returns ``None`` when the file does not exist or cannot be parsed.
    """
    if not rules_file.exists():
        return None
    try:
        with open(rules_file, "r", encoding="utf-8") as fh:
            raw: Any = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read rules file %s: %s", rules_file, exc)
        return None

    if not isinstance(raw, dict):
        return None

    data = cast(dict[str, object], raw)

    # program
    program_raw = data.get("program")
    program_typed: dict[str, object] = (
        cast(dict[str, object], program_raw) if isinstance(program_raw, dict) else {}
    )
    program: ProgramRef = {
        "id": str(program_typed.get("id", "")),
        "name": str(program_typed.get("name", "")),
    }

    # specialisations
    specs_raw = data.get("specialisations")
    specialisation: list[SpecialisationRef] = []
    if isinstance(specs_raw, list):
        for s_raw in cast(list[object], specs_raw):
            if isinstance(s_raw, dict):
                s = cast(dict[str, object], s_raw)
                specialisation.append(
                    {
                        "id": str(s.get("id", "")),
                        "name": str(s.get("name", "")),
                    }
                )

    # uoc
    uoc: int = 0
    uoc_raw = data.get("uoc")
    if isinstance(uoc_raw, (int, float)):
        uoc = int(uoc_raw)

    # optional description
    rules_description = str(data["description"]) if "description" in data else ""

    return {
        "plan_code": plan_code,
        "plan_description": plan_description,
        "program": program,
        "specialisation": specialisation,
        "uoc": uoc,
        "rules_description": rules_description,
    }

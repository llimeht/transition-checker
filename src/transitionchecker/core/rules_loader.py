"""Shared rules-file resolution and programme metadata loading.

The functions here are used by both the extract-plans and plan-validate
workflows so that rules-file lookup logic lives in exactly one place.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

_YEAR_RANGE_RE = re.compile(r"^(\d{4})-(\d{4})$")
_INTAKE_YEAR_RE = re.compile(r"^\d{4}$")


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


def resolve_rule_file(plan_code: str, intake_year: int | None, rules_dir: Path) -> Path:
    """Return the best matching rule file for *plan_code*.

    When *intake_year* is provided and a date-ranged file exists that covers
    that year (e.g. ``PROGRAM-2026-2029.json``), that file is preferred.
    Otherwise falls back to ``rules_dir/PROGRAM.json``.
    """
    if intake_year is not None:
        for candidate in sorted(rules_dir.glob("*.json")):
            stem = candidate.stem
            if not stem.startswith(f"{plan_code}-"):
                continue
            range_part = stem[len(plan_code) + 1 :]
            m = _YEAR_RANGE_RE.fullmatch(range_part)
            if not m:
                continue
            if int(m.group(1)) <= intake_year <= int(m.group(2)):
                return candidate
    return rules_dir / f"{plan_code}.json"


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
        segment = plan_stem[len(prefix) :].split("_", 1)[0]
        if _INTAKE_YEAR_RE.fullmatch(segment):
            intake_year = int(segment)
    return resolve_rule_file(plan_code, intake_year, rules_dir)


def intake_year_from_intake_string(intake: str) -> int | None:
    """Extract the 4-digit year from an intake string such as ``"2026 T1"``."""
    m = re.match(r"^(\d{4})\b", intake.strip())
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Plan-code / description parsing
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


def _strip_parens(text: str) -> str:
    """Strip a single layer of enclosing parentheses from *text*, if present."""
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        return stripped[1:-1].strip()
    return stripped


def parse_plan_code(sheet_name: str, rules_dir: Path) -> tuple[str, str]:
    """Split *sheet_name* into ``(plan_code, plan_description)``.

    When the sheet name contains a space, the prefix before the first space is
    checked against available rules files.  If a match is found the remainder
    becomes the description, with any enclosing parentheses stripped.

    Example: ``"CEICKS8338 (48 UoC RPL)"`` → ``("CEICKS8338", "48 UoC RPL")``.

    When the full sheet name matches a rules file exactly (no space separator),
    the whole name is the plan code with an empty description.

    When no match can be determined the full sheet name is returned as the
    plan_code with an empty description.
    """
    # Try splitting on the first space first; a parenthesised qualifier after
    # the code is the common pattern (e.g. "CEICKS8338 (48 UoC RPL)").
    if " " in sheet_name:
        prefix, rest = sheet_name.split(" ", 1)
        if _has_rule(prefix, rules_dir):
            return prefix, _strip_parens(rest)

    # Exact match – full sheet name is the plan code with no description.
    if _has_rule(sheet_name, rules_dir):
        return sheet_name, ""

    # No matching rule found – treat the whole name as the plan code.
    return sheet_name, ""


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

    # program
    program_raw = raw.get("program", {})
    program: ProgramRef = {
        "id": str(program_raw.get("id", "")) if isinstance(program_raw, dict) else "",
        "name": str(program_raw.get("name", "")) if isinstance(program_raw, dict) else "",
    }

    # specialisations
    specs_raw = raw.get("specialisations", [])
    specialisation: list[SpecialisationRef] = []
    if isinstance(specs_raw, list):
        for s in specs_raw:
            if isinstance(s, dict):
                specialisation.append(
                    {
                        "id": str(s.get("id", "")),
                        "name": str(s.get("name", "")),
                    }
                )

    # uoc
    uoc: int = 0
    uoc_raw = raw.get("uoc")
    if isinstance(uoc_raw, (int, float)):
        uoc = int(uoc_raw)

    # optional description
    rules_description = str(raw.get("description", "")) if "description" in raw else ""

    return {
        "plan_code": plan_code,
        "plan_description": plan_description,
        "program": program,
        "specialisation": specialisation,
        "uoc": uoc,
        "rules_description": rules_description,
    }

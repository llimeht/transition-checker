"""Strongly-typed catalogue types for the transition checker.

The primary key for a catalogue entry is ``(code, career)`` — the same course
code can exist under different careers (e.g. ``Undergraduate``, ``Postgraduate``).

JSON format
-----------
``catalogue.json`` is a **list** of entry objects::

    [
      {"code": "CEIC1000", "career": "Undergraduate", "title": "...", "uoc": 6,
       "prerequisites": ".", "level": null},
      ...
    ]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, TextIO, cast

from transitionchecker.erg_parser import ErgExpr


def _normalize_code(code: str) -> str:
    """Canonical form for a course code: stripped and uppercased."""
    return code.strip().upper()


_CAREER_ALIASES = {
    "undergraduate": "Undergraduate",
    "ug": "Undergraduate",
    "ugrad": "Undergraduate",
    "ugrd": "Undergraduate",
    "postgraduate": "Postgraduate",
    "pg": "Postgraduate",
    "pgrad": "Postgraduate",
    "pgrd": "Postgraduate",
    "postgraduate (online)": "Postgraduate (Online)",
    "pg (online)": "Postgraduate (Online)",
    "pgrad (online)": "Postgraduate (Online)",
    "pgrd (online)": "Postgraduate (Online)",
    "pgonline": "Postgraduate (Online)",
}


@dataclass(frozen=True)
class CatalogueKey:
    """Immutable, hashable primary key for a catalogue entry.

    The ``code`` field is normalized to uppercase on construction so that
    lookups are case-insensitive by default.
    """

    code: str
    career: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _normalize_code(self.code))


@dataclass(frozen=True)
class CatalogueEntry:
    """All catalogue fields carried through to the planner and validators."""

    code: str
    title: str
    career: str
    uoc: int = 6
    prerequisites: str = ""
    level: str | None = None
    erg_expr: ErgExpr | None = field(default=None, compare=False, hash=False, repr=False)
    """Pre-parsed structured expression built from ERG Requisite Detail rows.
    When set, :func:`~transitionchecker.rules_engine.evaluate_erg_expression`
    is used directly and ``prerequisites`` is not passed through
    ``parse_prerequisite_field``.  ``None`` for all non-ERG-sourced entries.
    """

    @property
    def key(self) -> CatalogueKey:
        return CatalogueKey(self.code, self.career)


def normalize_catalogue_career(value: str) -> str:
    """Normalize common career aliases onto catalogue career labels."""

    normalized = value.strip()
    if not normalized:
        return ""
    return _CAREER_ALIASES.get(normalized.casefold(), normalized)


class Catalogue:
    """Indexed collection of :class:`CatalogueEntry` objects.

    Internally keyed by :class:`CatalogueKey` ``(code, career)`` for O(1)
    lookup.  Use :meth:`by_code` when the career is not yet known.
    """

    def __init__(self, entries: Iterable[CatalogueEntry]) -> None:
        self._index: dict[CatalogueKey, CatalogueEntry] = {}
        for entry in entries:
            self._index[entry.key] = entry

    # ------------------------------------------------------------------
    # Core mapping protocol
    # ------------------------------------------------------------------

    def __getitem__(self, key: CatalogueKey) -> CatalogueEntry:
        return self._index[key]

    def get(self, key: CatalogueKey) -> CatalogueEntry | None:
        return self._index.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __iter__(self) -> Iterator[CatalogueKey]:
        return iter(self._index)

    def __len__(self) -> int:
        return len(self._index)

    def values(self) -> Iterable[CatalogueEntry]:
        return self._index.values()

    def items(self) -> Iterable[tuple[CatalogueKey, CatalogueEntry]]:
        return self._index.items()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def by_code(self, code: str) -> list[CatalogueEntry]:
        """Return all entries whose code matches *code*, across all careers."""
        normalized = _normalize_code(code)
        return [e for e in self._index.values() if e.code == normalized]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict[str, Any]]:
        """Serialise to a JSON-compatible list of dicts."""
        result: list[dict[str, Any]] = []
        for e in self._index.values():
            entry_dict: dict[str, Any] = {
                "code": e.code,
                "title": e.title,
                "career": e.career,
                "uoc": e.uoc,
                "prerequisites": e.prerequisites,
                "level": e.level,
            }
            if e.erg_expr is not None:
                entry_dict["erg_expr"] = e.erg_expr
            result.append(entry_dict)
        return result

    @classmethod
    def from_list(cls, data: list[object]) -> "Catalogue":
        """Deserialise from a JSON list as produced by :meth:`to_list`."""
        entries: list[CatalogueEntry] = []
        for item_obj in data:
            if not isinstance(item_obj, dict):
                raise ValueError(
                    f"Each catalogue entry must be an object, got: {type(item_obj)}"
                )
            item = cast(dict[str, Any], item_obj)
            code = _normalize_code(str(item.get("code") or ""))
            career = str(item.get("career") or "").strip()
            if not code:
                raise ValueError(f"Catalogue entry missing 'code': {item}")
            title = str(item.get("title") or "").strip()
            uoc_raw = item.get("uoc", 6)
            try:
                uoc = int(uoc_raw) if uoc_raw is not None else 6
            except (TypeError, ValueError):
                uoc = 6
            prerequisites = str(item.get("prerequisites") or "").strip()
            level_raw = item.get("level")
            level = str(level_raw).strip() if level_raw is not None else None
            erg_expr_raw = item.get("erg_expr")
            erg_expr = cast(ErgExpr, erg_expr_raw) if isinstance(erg_expr_raw, dict) else None
            entries.append(
                CatalogueEntry(
                    code=code,
                    title=title,
                    career=career,
                    uoc=uoc,
                    prerequisites=prerequisites,
                    level=level,
                    erg_expr=erg_expr,
                )
            )
        return cls(entries)


def available_catalogue_careers(catalogue: Catalogue) -> list[str]:
    """Return sorted non-empty career labels present in the catalogue."""

    return sorted({entry.career for entry in catalogue.values() if entry.career})


def resolve_rules_career(rules_config: dict[str, Any]) -> str:
    """Return the normalized top-level rules career used for catalogue lookups."""

    raw_career = rules_config.get("career")
    if not isinstance(raw_career, str) or not raw_career.strip():
        raise ValueError(
            "Rules file must define a top-level 'career' field for catalogue lookups"
        )
    return normalize_catalogue_career(raw_career)


def ensure_catalogue_has_career(catalogue: Catalogue, career: str) -> None:
    """Raise when the catalogue has no entries for the requested career."""

    if any(entry.career == career for entry in catalogue.values()):
        return

    available = available_catalogue_careers(catalogue)
    detail = f" Available careers: {', '.join(available)}." if available else ""
    raise ValueError(f"Catalogue contains no entries for career '{career}'.{detail}")


def get_catalogue_entry_for_career(
    code: str,
    catalogue: Catalogue,
    career: str,
) -> CatalogueEntry | None:
    """Return the exact catalogue entry for ``(code, career)`` or fail clearly."""

    normalized_code = code.strip().upper()
    entry = catalogue.get(CatalogueKey(normalized_code, career))
    if entry is not None:
        return entry

    matches = catalogue.by_code(normalized_code)
    if not matches:
        return None

    available = sorted({match.career or "<blank>" for match in matches})
    raise ValueError(
        f"Catalogue course '{normalized_code}' is not available for career "
        f"'{career}' (available careers: {', '.join(available)})"
    )


def ensure_catalogue_courses_for_career(
    catalogue: Catalogue,
    codes: Iterable[str],
    career: str,
    *,
    stderr: TextIO | None = None,
) -> None:
    """Raise when any referenced course is missing for the requested career.

    When a course exists in the catalogue but under a different career, a warning
    is printed to *stderr* and the check passes (falling back to that career).
    """

    missing: list[str] = []
    for code in sorted({value.strip().upper() for value in codes if value.strip()}):
        try:
            entry = get_catalogue_entry_for_career(code, catalogue, career)
        except ValueError:
            # Course exists but under a different career — warn and fall back.
            matches = catalogue.by_code(code)
            available = sorted({m.career or "<blank>" for m in matches})
            msg = (
                f"Catalogue course '{code}' is not available for career '{career}' "
                f"(available careers: {', '.join(available)}); using first available career"
            )
            if stderr is not None:
                print(f"Warning: {msg}", file=stderr)
            continue

        if entry is None:
            missing.append(f"Catalogue course '{code}' was not found")

    if not missing:
        return

    details = "; ".join(missing)
    raise ValueError(f"Catalogue lookup failed for career '{career}': {details}")

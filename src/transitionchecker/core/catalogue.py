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

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, cast


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
    """Immutable, hashable primary key for a catalogue entry."""

    code: str
    career: str


@dataclass(frozen=True)
class CatalogueEntry:
    """All catalogue fields carried through to the planner and validators."""

    code: str
    title: str
    career: str
    uoc: int = 6
    prerequisites: str = ""
    level: str | None = None

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
        return [e for e in self._index.values() if e.code == code]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict[str, Any]]:
        """Serialise to a JSON-compatible list of dicts."""
        return [
            {
                "code": e.code,
                "title": e.title,
                "career": e.career,
                "uoc": e.uoc,
                "prerequisites": e.prerequisites,
                "level": e.level,
            }
            for e in self._index.values()
        ]

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
            code = str(item.get("code") or "").strip()
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
            entries.append(CatalogueEntry(
                code=code,
                title=title,
                career=career,
                uoc=uoc,
                prerequisites=prerequisites,
                level=level,
            ))
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
    detail = (
        f" Available careers: {', '.join(available)}."
        if available
        else ""
    )
    raise ValueError(
        f"Catalogue contains no entries for career '{career}'.{detail}"
    )


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
) -> None:
    """Raise when any referenced course is missing for the requested career."""

    missing: list[str] = []
    for code in sorted({value.strip().upper() for value in codes if value.strip()}):
        try:
            entry = get_catalogue_entry_for_career(code, catalogue, career)
        except ValueError as exc:
            missing.append(str(exc))
            continue

        if entry is None:
            missing.append(f"Catalogue course '{code}' was not found")

    if not missing:
        return

    details = "; ".join(missing)
    raise ValueError(
        f"Catalogue lookup failed for career '{career}': {details}"
    )

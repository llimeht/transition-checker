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

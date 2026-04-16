from __future__ import annotations

from typing import Any, cast


def as_json_object(value: object) -> dict[str, Any] | None:
    """Return a JSON object with string keys, otherwise None."""

    if not isinstance(value, dict):
        return None

    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        return None

    return cast(dict[str, Any], raw)


def as_text(value: object) -> str:
    """Normalize optional string-like values to trimmed text."""

    return value.strip() if isinstance(value, str) else ""

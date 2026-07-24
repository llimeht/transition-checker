from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanMatcher:
    """Match plan identities with OR semantics across glob and regex patterns."""

    glob_patterns: tuple[str, ...]
    regex_patterns: tuple[re.Pattern[str], ...]

    def matches(self, *, plan_stem: str, plan_code: str) -> bool:
        """Return whether a plan identity matches configured filters."""

        if not self.glob_patterns and not self.regex_patterns:
            return True

        identities = [plan_stem]
        if plan_code and plan_code != plan_stem:
            identities.append(plan_code)

        for identity in identities:
            if any(fnmatch.fnmatch(identity, pattern) for pattern in self.glob_patterns):
                return True
            if any(pattern.search(identity) for pattern in self.regex_patterns):
                return True

        return False


def normalize_filter_patterns(raw_values: list[str] | None) -> list[str]:
    """Normalize repeated CLI pattern values (supports comma-separated entries)."""

    if not raw_values:
        return []

    normalized: list[str] = []
    for raw in raw_values:
        for value in raw.split(","):
            pattern = value.strip()
            if pattern:
                normalized.append(pattern)
    return normalized


def compile_plan_matcher(
    *,
    glob_patterns: list[str] | None,
    regex_patterns: list[str] | None,
) -> PlanMatcher:
    """Create a matcher from raw glob and regex pattern collections."""

    normalized_globs = tuple(normalize_filter_patterns(glob_patterns))
    normalized_regexes = normalize_filter_patterns(regex_patterns)

    compiled_regexes: list[re.Pattern[str]] = []
    for pattern in normalized_regexes:
        try:
            compiled_regexes.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"Invalid --filter-regex pattern {pattern!r}: {exc}") from exc

    return PlanMatcher(
        glob_patterns=normalized_globs,
        regex_patterns=tuple(compiled_regexes),
    )

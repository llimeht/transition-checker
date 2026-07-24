"""Unit tests for shared plan filtering helpers."""

from __future__ import annotations

import pytest

from transitionchecker.core.plan_filtering import compile_plan_matcher, normalize_filter_patterns


def test_normalize_filter_patterns_supports_commas_and_repeats() -> None:
    patterns = normalize_filter_patterns(["CEIC*", "FOOD*, MATS*", "  "])
    assert patterns == ["CEIC*", "FOOD*", "MATS*"]


def test_compile_plan_matcher_matches_on_stem_or_code() -> None:
    matcher = compile_plan_matcher(glob_patterns=["*_2026_T1", "*3707*"], regex_patterns=None)
    assert matcher.matches(plan_stem="CEICAH3707_2025_T2", plan_code="CEICAH3707")
    assert matcher.matches(plan_stem="FOODJH3061_2026_T1", plan_code="FOODJH3061")
    assert not matcher.matches(plan_stem="MATSM13132_2027_T1", plan_code="MATSM13132")


def test_compile_plan_matcher_supports_regex_or_semantics() -> None:
    matcher = compile_plan_matcher(
        glob_patterns=None,
        regex_patterns=[r"_2026_T1$", r"^CEIC"],
    )
    assert matcher.matches(plan_stem="FOODJH3061_2026_T1", plan_code="FOODJH3061")
    assert matcher.matches(plan_stem="CEICAH3707_2025_T2", plan_code="CEICAH3707")
    assert not matcher.matches(plan_stem="MATSM13132_2025_T2", plan_code="MATSM13132")


def test_compile_plan_matcher_without_filters_matches_all() -> None:
    matcher = compile_plan_matcher(glob_patterns=None, regex_patterns=None)
    assert matcher.matches(plan_stem="ANY_2026_T1", plan_code="ANY")


def test_compile_plan_matcher_rejects_invalid_regex() -> None:
    with pytest.raises(ValueError):
        compile_plan_matcher(glob_patterns=None, regex_patterns=["("])

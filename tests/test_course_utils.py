"""Tests for transitionchecker.core.course_utils."""

from __future__ import annotations

import pytest

from transitionchecker.core.catalogue import (
    Catalogue,
    CatalogueEntry,
    CatalogueKey,
)
from transitionchecker.core.course_utils import (
    is_placeholder_course,
    looks_like_course,
    normalize_course_code,
)


class TestNormalizeCourseCode:
    def test_strips_whitespace(self) -> None:
        assert normalize_course_code("  ceic1000  ") == "CEIC1000"

    def test_uppercases(self) -> None:
        assert normalize_course_code("ceic1000") == "CEIC1000"

    def test_already_normalized(self) -> None:
        assert normalize_course_code("CEIC1000") == "CEIC1000"

    def test_empty_string(self) -> None:
        assert normalize_course_code("") == ""


class TestLooksLikeCourse:
    @pytest.mark.parametrize(
        "code",
        [
            "CEIC1000",
            "MATH1231",
            "GENE0001",
            "GENE-XXXX",
            "ABCD1234",
        ],
    )
    def test_valid_codes(self, code: str) -> None:
        assert looks_like_course(code)

    @pytest.mark.parametrize(
        "code",
        [
            "ceic1000",  # lowercase still passes (normalized internally)
            "CEIC 1000",  # space — fails fullmatch
            "1000CEIC",  # digits first
            "",
            "AND",
            "120 UOC",
        ],
    )
    def test_invalid_codes(self, code: str) -> None:
        # lowercase is normalized so it should pass; others fail
        if code == "ceic1000":
            assert looks_like_course(code)
        else:
            assert not looks_like_course(code)


class TestIsPlaceholderCourse:
    @pytest.mark.parametrize(
        "code",
        [
            "FREE1234",
            "free1234",
            "GENED1234",
            "gened9999",
            "FREEXXX",
            "GENEDABC",
        ],
    )
    def test_placeholder_codes(self, code: str) -> None:
        assert is_placeholder_course(code)

    @pytest.mark.parametrize(
        "code",
        [
            "CEIC1000",
            "MATH1231",
            "ELEC1111",
        ],
    )
    def test_non_placeholder_codes(self, code: str) -> None:
        assert not is_placeholder_course(code)


class TestCatalogueCaseInsensitivity:
    def _catalogue(self) -> Catalogue:
        return Catalogue(
            [
                CatalogueEntry(
                    code="CEIC1000", title="A", career="Undergraduate", uoc=6
                ),
                CatalogueEntry(
                    code="CEIC1000", title="A PG", career="Postgraduate", uoc=6
                ),
                CatalogueEntry(
                    code="MATH1131", title="B", career="Undergraduate", uoc=6
                ),
            ]
        )

    def test_get_lowercase_key_matches_uppercase_entry(self) -> None:
        cat = self._catalogue()
        assert cat.get(CatalogueKey("ceic1000", "Undergraduate")) is not None

    def test_get_mixed_case_key_matches_entry(self) -> None:
        cat = self._catalogue()
        assert cat.get(CatalogueKey("Ceic1000", "Postgraduate")) is not None

    def test_contains_lowercase_key(self) -> None:
        cat = self._catalogue()
        assert CatalogueKey("ceic1000", "Undergraduate") in cat

    def test_by_code_lowercase_returns_matches(self) -> None:
        cat = self._catalogue()
        entries = cat.by_code("ceic1000")
        assert len(entries) == 2

    def test_by_code_mixed_case_returns_matches(self) -> None:
        cat = self._catalogue()
        assert len(cat.by_code("Math1131")) == 1

    def test_cataloguekey_normalizes_code(self) -> None:
        assert CatalogueKey("ceic1000", "Undergraduate").code == "CEIC1000"
        assert CatalogueKey("  Ceic1000  ", "Undergraduate").code == "CEIC1000"

    def test_from_list_normalizes_code(self) -> None:
        cat = Catalogue.from_list(
            [
                {
                    "code": "ceic1000",
                    "career": "Undergraduate",
                    "title": "A",
                    "uoc": 6,
                    "prerequisites": "",
                }
            ]
        )
        assert len(cat.by_code("CEIC1000")) == 1
        assert len(cat.by_code("ceic1000")) == 1

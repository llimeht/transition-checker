"""Tests for parse_prerequisite_field in prereq_engine."""

from __future__ import annotations

from transitionchecker.prereq_engine import parse_prerequisite_field


def parse(text: str) -> tuple[object | None, object | None, str | None]:
    """Convenience wrapper returning (prereq_expr, coreq_expr, error)."""
    return parse_prerequisite_field(text)


class TestEmptyAndTrivialInputs:
    def test_empty_string(self) -> None:
        prereq, coreq, err = parse("")
        assert prereq is None and coreq is None and err is None

    def test_dot(self) -> None:
        prereq, coreq, err = parse(".")
        assert prereq is None and coreq is None and err is None

    def test_zero(self) -> None:
        prereq, coreq, err = parse("0")
        assert prereq is None and coreq is None and err is None

    def test_whitespace_only(self) -> None:
        prereq, coreq, err = parse("   ")
        assert prereq is None and coreq is None and err is None

    def test_na_only(self) -> None:
        prereq, coreq, err = parse("N/A")
        assert prereq is None and coreq is None and err is None


class TestSingleCourse:
    def test_single_course(self) -> None:
        prereq, coreq, err = parse("CEIC1000")
        assert err is None
        assert prereq == "CEIC1000"
        assert coreq is None

    def test_single_course_prereq(self) -> None:
        prereq, coreq, err = parse("Prerequisite: CEIC1000")
        assert err is None
        assert prereq == "CEIC1000"
        assert coreq is None
        prereq, coreq, err = parse("Prerequisites: CEIC1000")
        assert err is None
        assert prereq == "CEIC1000"
        assert coreq is None
        prereq, coreq, err = parse("Pre-requisite: CEIC1000")
        assert err is None
        assert prereq == "CEIC1000"
        assert coreq is None

    def test_single_course_lowercase(self) -> None:
        prereq, _, err = parse("ceic1000")
        assert err is None
        assert prereq == "CEIC1000"

    def test_students_must_have_completed_with_course_title(self) -> None:
        prereq, _, err = parse("Students must have completed JURD7152 Introduction to Law & Justice.")
        assert err is None
        assert prereq == "JURD7152"


class TestAndOrExpressions:
    def test_and_two_courses(self) -> None:
        prereq, _, err = parse("CEIC1000 AND CEIC1001")
        assert err is None
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}
        prereq, _, err = parse("Prerequisites: CEIC1000 AND CEIC1001")
        assert err is None
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}

    def test_or_two_courses(self) -> None:
        prereq, _, err = parse("CEIC1000 OR CEIC1001")
        assert err is None
        assert prereq == {"or": ["CEIC1000", "CEIC1001"]}

    def test_and_has_higher_precedence_than_or(self) -> None:
        # A OR B AND C  →  A OR (B AND C)
        prereq, _, err = parse("CEIC1000 OR CEIC1001 AND CEIC1002")
        assert err is None
        assert prereq == {"or": ["CEIC1000", {"and": ["CEIC1001", "CEIC1002"]}]}

    def test_ampersand_treated_as_and(self) -> None:
        prereq, _, err = parse("CEIC1000 & CEIC1001")
        assert err is None
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}

    def test_comma_treated_as_and(self) -> None:
        prereq, _, err = parse("CEIC1000, CEIC1001")
        assert err is None
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}

    def test_parenthesized_or(self) -> None:
        prereq, _, err = parse("CEIC1000 AND (CEIC1001 OR CEIC1002)")
        assert err is None
        assert prereq == {"and": ["CEIC1000", {"or": ["CEIC1001", "CEIC1002"]}]}


class TestUocTokens:
    def test_uoc_expression(self) -> None:
        prereq, _, err = parse("120 UOC")
        assert err is None
        assert prereq == {"uoc": 120}

    def test_uoc_case_insensitive(self) -> None:
        prereq, _, err = parse("48 uoc")
        assert err is None
        assert prereq == {"uoc": 48}

    def test_uoc_and_course(self) -> None:
        prereq, _, err = parse("48 UOC AND CEIC2001")
        assert err is None
        assert prereq == {"and": [{"uoc": 48}, "CEIC2001"]}

    def test_course_and_uoc(self) -> None:
        prereq, _, err = parse("CEIC2001 AND 48 UOC")
        assert err is None
        assert prereq == {"and": ["CEIC2001", {"uoc": 48}]}

    def test_qualified_uoc_expression(self) -> None:
        prereq, _, err = parse("72 UOC of JURD courses")
        assert err is None
        assert prereq == {"uoc": 72}

    def test_qualified_uoc_with_completion_of(self) -> None:
        prereq, _, err = parse("Completion of 72 UOC of JURD courses")
        assert err is None
        assert prereq == {"uoc": 72}

    def test_qualified_uoc_with_descriptive_group(self) -> None:
        prereq, _, err = parse("48 UOC of Science courses.")
        assert err is None
        assert prereq == {"uoc": 48}

    def test_qualified_uoc_with_trailing_completed(self) -> None:
        prereq, _, err = parse("Prerequisite: 30UOC of Science courses completed")
        assert err is None
        assert prereq == {"uoc": 30}

    def test_uoc_with_overall_suffix(self) -> None:
        prereq, _, err = parse("Prerequisite: 24 units of credit overall")
        assert err is None
        assert prereq == {"uoc": 24}

    def test_uoc_with_at_least_and_completed(self) -> None:
        prereq, _, err = parse("At least 24 UOC completed")
        assert err is None
        assert prereq == {"uoc": 24}

    def test_uoc_completed_in_course_group(self) -> None:
        prereq, _, err = parse("24UOC completed in Juris Doctor courses.")
        assert err is None
        assert prereq == {"uoc": 24}

    def test_uoc_with_leading_completed(self) -> None:
        prereq, _, err = parse("Prerequisite: Completed 72 UOC")
        assert err is None
        assert prereq == {"uoc": 72}


class TestPlus:
    def test_plus_combines_as_and(self) -> None:
        prereq, _, err = parse("CEIC1000 PLUS CEIC1001")
        assert err is None
        # Two independent segments joined by AND
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}

    def test_plus_with_completion_of(self) -> None:
        prereq, _, err = parse("Completion of CEIC1000 PLUS CEIC1001")
        assert err is None
        assert prereq == {"and": ["CEIC1000", "CEIC1001"]}


class TestCorequisiteSplit:
    def test_coreq_section_parsed(self) -> None:
        prereq, coreq, err = parse("CEIC1000 CO-REQ: CEIC1001")
        assert err is None
        assert prereq == "CEIC1000"
        assert coreq == "CEIC1001"

    def test_coreq_only(self) -> None:
        prereq, coreq, err = parse("COREQUISITE: CEIC1000")
        assert err is None
        # prereq section is empty
        assert prereq is None
        assert coreq == "CEIC1000"


class TestInvalidInputs:
    def test_unrecognized_token_returns_error(self) -> None:
        _, _, err = parse("CEIC1000 XYZZY CEIC1001")
        assert err is not None

    def test_advisory_completed_sentence_remains_error(self) -> None:
        prereq, coreq, err = parse(
            "Students who have previously completed ACCT5906 should not enrol into this course."
        )
        assert prereq is None
        assert coreq is None
        assert err is not None

    def test_result_is_cached(self) -> None:
        """Calling with the same input twice returns the same object (cache hit)."""
        result1 = parse("CEIC1000 AND CEIC1001")
        result2 = parse("CEIC1000 AND CEIC1001")
        assert result1 == result2

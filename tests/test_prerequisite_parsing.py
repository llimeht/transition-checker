"""Tests for parse_prerequisite_field in prereq_engine."""

from __future__ import annotations

from transitionchecker.prereq_engine import (
    parse_prerequisite_field,
    salvage_mixed_prerequisite_clause,
)


def parse(text: str) -> tuple[object | None, object | None, str | None]:
    """Convenience wrapper returning (prereq_expr, coreq_expr, error)."""
    return parse_prerequisite_field(text)


def salvage(
    text: str, matched_families: list[str]
) -> tuple[bool, object | None, str | None]:
    """Convenience wrapper for mixed-clause salvage tests."""
    return salvage_mixed_prerequisite_clause(text, matched_families)


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
        prereq, _, err = parse(
            "Students must have completed JURD7152 Introduction to Law & Justice."
        )
        assert err is None
        assert prereq == "JURD7152"

    def test_students_should_have_completed_or_equivalent_courses(self) -> None:
        prereq, _, err = parse(
            "Students should have completed ACTL3142 or equivalent courses and achieved "
            "an exemption level for that course (65 and above)."
        )
        assert err is None
        assert prereq == "ACTL3142"

    def test_enrolment_sentence_then_required_completed_course(self) -> None:
        prereq, _, err = parse(
            "Currently enrolled in program 8143 Master of Architecture or 8144 Master of "
            "Architecture / Property and Development. Students are required to have completed "
            "ARCH7111."
        )
        assert err is None
        assert prereq == "ARCH7111"

    def test_must_have_completed_single_course(self) -> None:
        prereq, _, err = parse("Must have completed DART4101")
        assert err is None
        assert prereq == "DART4101"

    def test_pre_requisite_label_with_course_title(self) -> None:
        prereq, _, err = parse(
            "Pre requisite: ZEIT3750 Naval Architecture Practice, Ship Hydrostatics and Stability"
        )
        assert err is None
        assert prereq == "ZEIT3750"


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

    def test_uoc_in_level_maths_courses(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: 12 units of credit in Level 2 Maths courses."
        )
        assert err is None
        assert prereq == {"uoc": 12}

    def test_uoc_in_level_courses(self) -> None:
        prereq, _, err = parse("Prerequisite: 36 Units of Credit in Level 1 courses")
        assert err is None
        assert prereq == {"uoc": 36}

    def test_uoc_completed_in_descriptive_group(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: 96 units of credit completed in Built Environment"
        )
        assert err is None
        assert prereq == {"uoc": 96}

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

    def test_uoc_with_minimum_prefix_and_completed(self) -> None:
        prereq, _, err = parse("Prerequisite: Minimum 48UOC completed")
        assert err is None
        assert prereq == {"uoc": 48}

    def test_uoc_with_students_must_have_completed_and_enrolment_tail(self) -> None:
        prereq, _, err = parse("Student must have completed 30 UoC in order to enrol.")
        assert err is None
        assert prereq == {"uoc": 30}

    def test_uoc_with_completed_and_status_clauses(self) -> None:
        prereq, _, err = parse(
            "Completed at least 72 UoC and be enrolled in a Commerce Program; "
            "be in good academic standing, and completed COMM1999"
        )
        assert err is None
        assert prereq == {"and": [{"uoc": 72}, "COMM1999"]}

    def test_uoc_with_students_must_have_completed_a_minimum_of(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: Students must have completed a minimum of 48 UoC"
        )
        assert err is None
        assert prereq == {"uoc": 48}

    def test_uoc_with_completion_of_a_minimum(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: Completion of a minimum 90 Units of Credit"
        )
        assert err is None
        assert prereq == {"uoc": 90}

    def test_course_and_minimum_uoc_completed(self) -> None:
        prereq, _, err = parse("Prerequisite: BIOS1301 and minimum 48UOC completed.")
        assert err is None
        assert prereq == {"and": ["BIOS1301", {"uoc": 48}]}

    def test_course_and_minimum_of_uoc_completed_to_enrol(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: BIOS1101, Minimum of 48 UOC completed to enrol"
        )
        assert err is None
        assert prereq == {"and": ["BIOS1101", {"uoc": 48}]}

    def test_completed_minimum_uoc_with_parenthesized_including(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: Completed a minimum of 108 UOC (including First Year core)."
        )
        assert err is None
        assert prereq == {"uoc": 108}

    def test_pre_label_minimum_uoc_and_wam(self) -> None:
        prereq, _, err = parse("Pre: Minimum 48 UoC completed and 70 WAM")
        assert err is None
        assert prereq == {"uoc": 48}

    def test_completion_of_uoc_and_third_year_core(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: Completion of 126 UOC and completion of 3rd year core"
        )
        assert err is None
        assert prereq == {"uoc": 126}

    def test_successful_completion_of_uoc(self) -> None:
        prereq, _, err = parse("Prerequisite: Successful completion of 96 UOC")
        assert err is None
        assert prereq == {"uoc": 96}

    def test_successful_completion_at_least_uoc_in_program(self) -> None:
        prereq, _, err = parse("Successful completion of at least 72 UOC in program")
        assert err is None
        assert prereq == {"uoc": 72}

    def test_minimum_uoc_completed_at_unsw_prior_to_course(self) -> None:
        prereq, _, err = parse(
            "Minimum of 96UOC completed at UNSW prior to this course"
        )
        assert err is None
        assert prereq == {"uoc": 96}

    def test_successful_completion_and_minimum_wam(self) -> None:
        prereq, _, err = parse("Successful completion of 96 UOC and minimum WAM 65%")
        assert err is None
        assert prereq == {"uoc": 96}

    def test_pre_label_uoc_and_minimum_wam(self) -> None:
        prereq, _, err = parse("Pre: 132 UOC and Minimum WAM of 80")
        assert err is None
        assert prereq == {"uoc": 132}

    def test_uoc_in_group_wam_and_consent_required_tail(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: 18 UOC in IEST courses and minimum WAM 65. "
            "Consent required from Environmental Management Program Convenor."
        )
        assert err is None
        assert prereq == {"uoc": 18}

    def test_uoc_group_courses_without_in_of_and_school_consent_tail(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: 24 UOC IEST courses and minimum WAM 75. School consent required."
        )
        assert err is None
        assert prereq == {"uoc": 24}

    def test_students_need_to_have_completed_minimum_uoc_to_undertake(self) -> None:
        prereq, _, err = parse(
            "Students need to have completed a minimum of 30 UoC to undertake this course."
        )
        assert err is None
        assert prereq == {"uoc": 30}

    def test_uoc_and_course_with_trailing_course_title(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: Students must have completed 60 UOC and BENV7020 Research Design."
        )
        assert err is None
        assert prereq == {"and": [{"uoc": 60}, "BENV7020"]}

    def test_two_courses_with_titles_and_conjunction_in_title(self) -> None:
        prereq, _, err = parse(
            "Prerequisites: ZSPS1111 Introduction to Information Technology and Networking, and "
            "ZSPS1337 Introduction to Cyber Security"
        )
        assert err is None
        assert prereq == {"and": ["ZSPS1111", "ZSPS1337"]}

    def test_single_course_with_title_ending_in_digit(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: ZSPS2119 Cyber Security Industry Project 1"
        )
        assert err is None
        assert prereq == "ZSPS2119"

    def test_minimum_wam_then_completion_of_uoc(self) -> None:
        prereq, _, err = parse("Minimum WAM of 70 and completion of 132UOC")
        assert err is None
        assert prereq == {"uoc": 132}

    def test_uoc_overall_and_a_minimum_wam(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: 48 units of credit overall, and a minimum WAM of 75"
        )
        assert err is None
        assert prereq == {"uoc": 48}

    def test_course_and_uoc_with_parenthesized_enrolment_program_clause(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: MGMT5050 AND (enrolment in program 8404 or 8417 or 8371) "
            "AND completion of 42 units of credit."
        )
        assert err is None
        assert prereq == {"and": ["MGMT5050", {"uoc": 42}]}

    def test_course_and_uoc_with_enrolment_program_clause_no_parentheses(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: MGMT5050 AND enrolment in program 8404 or 8417 or 8371 "
            "AND completion of 42 units of credit."
        )
        assert err is None
        assert prereq == {"and": ["MGMT5050", {"uoc": 42}]}

    def test_program_enrolment_only_clause_is_removed_from_expression(self) -> None:
        prereq, _, err = parse(
            "Prerequisite: MGMT5050 AND (enrolment in program 8404, 8417 and 8371)"
        )
        assert err is None
        assert prereq == "MGMT5050"


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

    def test_pre_or_corequisite_treated_as_coreq(self) -> None:
        prereq, coreq, err = parse("Pre or Corequisite: MARK5700 or MARK5800")
        assert err is None
        assert prereq is None
        assert coreq == {"or": ["MARK5700", "MARK5800"]}

    def test_pre_or_coreq_with_comma_separated_program_list(self) -> None:
        prereq, coreq, err = parse(
            "Pre or Corequisite: MARK5700 or MARK5800 OR in program 8281, 8282, 8291, 8234, 8224"
        )
        assert err is None
        assert prereq is None
        assert coreq == {"or": ["MARK5700", "MARK5800"]}

    def test_coreq_must_be_enrolled_in_courses_same_term(self) -> None:
        prereq, coreq, err = parse(
            "Corequisite: Student must be enrolled in JURD7161 Torts and JURD7114 "
            "Foundations Enrichment 2 in the same term"
        )
        assert err is None
        assert prereq is None
        assert coreq == {"and": ["JURD7161", "JURD7114"]}


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


class TestMixedClauseSalvage:
    def test_salvage_program_enrolment_sentence_with_following_uoc_and_courses(
        self,
    ) -> None:
        salvaged, expr, err = salvage(
            "Prerequisite: Enrolment in a postgraduate Education, Educational Leadership "
            "program, or Master of Teaching (Secondary). Master of Teaching (Secondary) "
            "students must have completed 48 UOC including EDST6760, EDST5112 and EDST5133.",
            ["program_enrolment"],
        )
        assert salvaged is True
        assert err is None
        assert expr == {"and": [{"uoc": 48}, "EDST6760", "EDST5112", "EDST5133"]}

    def test_salvage_program_enrolment_with_uoc_of_any_code_groups(self) -> None:
        salvaged, expr, err = salvage(
            "Prerequisite: 6 UOC of any DPGE, DPST, DPBS and enrolled in a UNSW Diploma program",
            ["program_enrolment"],
        )
        assert salvaged is True
        assert err is None
        assert expr == {"uoc": 6}

    def test_salvage_program_enrolment_with_following_completion_requirement(
        self,
    ) -> None:
        salvaged, expr, err = salvage(
            "Prerequisite: Enrolment in ACCTKS CPAA Specialisation AND completion of 36 UOC in Program 8415, "
            "OR Enrolment in 5415",
            ["program_enrolment"],
        )
        assert salvaged is True
        assert err is None
        assert expr == {"uoc": 36}

    def test_salvage_program_enrolment_with_following_course_requirement(self) -> None:
        salvaged, expr, err = salvage(
            "Prerequisite: Enrolment in program 3896 Exercise Science/Physiotherapy and Exercise Physiology "
            "AND SOMS1912",
            ["program_enrolment"],
        )
        assert salvaged is True
        assert err is None
        assert expr == "SOMS1912"

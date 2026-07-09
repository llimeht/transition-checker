"""Tests for erg_parser — ERG Requisite Detail line parsing."""

from __future__ import annotations

from transitionchecker.erg_parser import (
    ErgParseResult,
    ErgRow,
    build_prerequisites_field,
    parse_erg_group,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rows(*lines: tuple[str, str, str]) -> list[ErgRow]:
    """Build a list of ErgRow(group_number, detail_text, requirement_id)."""
    return [ErgRow(g, d, r) for g, d, r in lines]


def parse(detail_lines: list[tuple[str, str, str]]) -> ErgParseResult:
    return parse_erg_group(rows(*detail_lines))


# ---------------------------------------------------------------------------
# PRE CRSE — simple course prerequisites
# ---------------------------------------------------------------------------

class TestPreCrse:
    def test_single_course(self) -> None:
        r = parse([("0010", " PRE CRSE 066426 COMM1140", "")])
        assert r.prereq_text == "COMM1140"
        assert r.coreq_text == ""
        assert not r.has_unresolvable

    def test_or_chain(self) -> None:
        r = parse([
            ("0010", "( PRE CRSE 066426 COMM1140", ""),
            ("0015", "OR PRE CRSE 067800 COMM1240", ""),
            ("0020", "OR PRE CRSE 000001 ACCT1501 )", ""),
        ])
        assert r.prereq_text == "( COMM1140 OR COMM1240 OR ACCT1501 )"
        assert not r.has_unresolvable

    def test_and_group_with_or(self) -> None:
        r = parse([
            ("0010", "( PRE CRSE 066428 COMM1170", ""),
            ("0020", "AND PRE CRSE 066430 COMM1180 )", ""),
            ("0030", "OR PRE CRSE 000002 ACCT1511", ""),
        ])
        assert r.prereq_text == "( COMM1170 AND COMM1180 ) OR ACCT1511"
        assert not r.has_unresolvable

    def test_operator_then_paren(self) -> None:
        # "AND (PRE CRSE ..." — paren follows operator, not the other way round.
        # Reproduces the ACCT3610 pattern.
        r = parse([
            ("0010", "PRE CRSE 066432 ACCT2511", ""),
            ("0020", "AND (PRE CRSE 066430 COMM1180", ""),
            ("0030", "OR PRE CRSE 006301 ECON1102 )", ""),
        ])
        assert not r.has_unresolvable
        assert r.prereq_text == "ACCT2511 AND ( COMM1180 OR ECON1102 )"

    def test_rows_sorted_by_group_number(self) -> None:
        # Caller must sort; verify parser respects the order it receives
        r = parse([
            ("0020", "OR PRE CRSE 067800 COMM1240", ""),
            ("0010", "( PRE CRSE 066426 COMM1140", ""),
            ("0030", "OR PRE CRSE 000001 ACCT1501 )", ""),
        ])
        # Order is whatever was given (0020 first) — caller's responsibility
        assert "COMM1240" in r.prereq_text
        assert "COMM1140" in r.prereq_text

    def test_trailing_uoc_annotation_ignored(self) -> None:
        # "PRE CRSE <num> MATH2801 6 units" — 6 units is a UoC annotation, not a mark
        r = parse([("0010", "PRE CRSE 012061 MATH2801 6 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "MATH2801"
        assert r.mark_requirements == {}

    def test_course_count_annotation_ignored(self) -> None:
        # "PRE CRSE <num> MBAE7501 1 course/s 2 units" — "1 course/s" is a count annotation
        r = parse([("0010", "PRE CRSE 066648 MBAE7501 1 course/s 2 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "MBAE7501"
        assert r.mark_requirements == {}

    def test_mark_not_confused_with_uoc_annotation(self) -> None:
        # "65" without "units" → mark; "6 units" → UoC annotation
        r_mark = parse([("0010", "PRE CRSE 066426 COMM1140 65", "")])
        assert r_mark.mark_requirements == {"COMM1140": 65}
        r_uoc = parse([("0010", "PRE CRSE 066426 COMM1140 6 units", "")])
        assert r_uoc.mark_requirements == {}

    def test_math3311_pattern(self) -> None:
        # Full MATH3311 detail_lines pattern
        r = parse([
            ("0010", "( PRE CRSE 063726 MATH2121", ""),
            ("0020", "OR PRE CRSE 063728 MATH2221", ""),
            ("0030", "OR PRE CRSE 053610 MATH2111 )", ""),
            ("0040", "AND (PRE CRSE 012054 MATH2501", ""),
            ("0050", "OR PRE CRSE 012058 MATH2601 )", ""),
            ("0060", "AND (PRE CRSE 012061 MATH2801 6 units", ""),
            ("0070", "OR PRE CRSE 012077 MATH2901 6 units", ""),
            ("0080", "OR PRE CRSE 056934 MATH2871 )", ""),
        ])
        assert not r.has_unresolvable
        assert "MATH2121" in r.prereq_text
        assert "MATH2501" in r.prereq_text
        assert "MATH2801" in r.prereq_text


# ---------------------------------------------------------------------------
# CO CRSE — corequisites
# ---------------------------------------------------------------------------

class TestCoCrse:
    def test_single_coreq(self) -> None:
        r = parse([("0010", " CO CRSE 066430 COMM1180", "")])
        assert r.coreq_text == "COMM1180"
        assert r.prereq_text == ""
        assert not r.has_unresolvable

    def test_prereq_and_coreq_combined(self) -> None:
        r = parse([
            ("0010", " PRE CRSE 066426 COMM1140", ""),
            ("0020", " CO CRSE 066430 COMM1180", ""),
        ])
        assert r.prereq_text == "COMM1140"
        assert r.coreq_text == "COMM1180"
        assert not r.has_unresolvable

    def test_coreq_first_then_prereq_strips_dangling_or(self) -> None:
        # Reproduces ACCT5919: CO CRSE lines come first, then OR PRE CRSE lines.
        # The OR operators on the PRE CRSE lines must not appear as the first
        # token in prereq_parts.
        r = parse([
            ("0010", "CO CRSE 056263 COMM5003", ""),
            ("0020", "OR CO CRSE 000054 ACCT5930", ""),
            ("0030", "OR CO CRSE 063271 ACCT5906", ""),
            ("0040", "OR PRE CRSE 063609 COMM0028", ""),
            ("0050", "OR PRE CRSE 061073 COMM0020", ""),
        ])
        assert not r.has_unresolvable
        assert r.prereq_text == "COMM0028 OR COMM0020"
        assert r.coreq_text == "COMM5003 OR ACCT5930 OR ACCT5906"

    def test_build_field_includes_corequisite_label(self) -> None:
        r = parse([
            ("0010", " PRE CRSE 000002 CEIC2001", ""),
            ("0020", " CO CRSE 066430 CEIC2002", ""),
        ])
        text = build_prerequisites_field(r)
        assert "Corequisite:" in text
        assert "CEIC2001" in text
        assert "CEIC2002" in text


# ---------------------------------------------------------------------------
# Minimum mark handling
# ---------------------------------------------------------------------------

class TestMinimumMark:
    def test_mark_above_50_captured(self) -> None:
        r = parse([("0010", " PRE CRSE 066426 COMM1140 65", "")])
        assert r.mark_requirements == {"COMM1140": 65}
        assert not r.has_unresolvable

    def test_mark_of_50_ignored(self) -> None:
        r = parse([("0010", " PRE CRSE 066426 COMM1140 50", "")])
        assert r.mark_requirements == {}
        assert not r.has_unresolvable

    def test_no_mark_field_empty(self) -> None:
        r = parse([("0010", " PRE CRSE 066426 COMM1140", "")])
        assert r.mark_requirements == {}

    def test_mark_embedded_in_prereq_text(self) -> None:
        r = parse([
            ("0010", "( PRE CRSE 066426 COMM1140 65", ""),
            ("0015", "OR PRE CRSE 067800 COMM1240", ""),
            ("0020", "OR PRE CRSE 000001 ACCT1501 )", ""),
        ])
        text = build_prerequisites_field(r)
        assert "Minimum mark of 65 in COMM1140" in text
        # Course codes are still present
        assert "COMM1140" in text
        assert "COMM1240" in text
        assert "ACCT1501" in text

    def test_mark_not_duplicated_when_no_mark(self) -> None:
        r = parse([("0010", " PRE CRSE 066426 COMM1140", "")])
        text = build_prerequisites_field(r)
        assert "Minimum mark" not in text

    def test_multiple_marks(self) -> None:
        r = parse([
            ("0010", " PRE CRSE 066428 COMM1170 65", ""),
            ("0020", "AND PRE CRSE 066430 COMM1180 70", ""),
        ])
        assert r.mark_requirements == {"COMM1170": 65, "COMM1180": 70}
        text = build_prerequisites_field(r)
        assert "Minimum mark of 65 in COMM1170" in text
        assert "Minimum mark of 70 in COMM1180" in text

    def test_coreq_mark_captured(self) -> None:
        r = parse([("0010", " CO CRSE 066430 COMM1180 65", "")])
        assert r.mark_requirements == {"COMM1180": 65}


# ---------------------------------------------------------------------------
# COND — enrolment conditions
# ---------------------------------------------------------------------------

class TestCond:
    def test_academic_plan_eq(self) -> None:
        r = parse([("0010", " COND Academic Plan EQ ACCTB13554", "")])
        assert "Enrolment in Academic Plan ACCTB13554" in r.prereq_text
        assert not r.has_unresolvable

    def test_academic_plan_eq_with_trailing_uoc(self) -> None:
        # "COND Academic Plan EQ MNGTUS8625 78 units" — UoC preserved in text
        r = parse([("0010", "COND Academic Plan EQ MNGTUS8625 78 units", "")])
        assert not r.has_unresolvable
        assert "Enrolment in Academic Plan MNGTUS8625 (78 UoC)" in r.prereq_text

    def test_academic_program_eq(self) -> None:
        r = parse([("0010", "COND Academic Program EQ 9201", "")])
        assert not r.has_unresolvable
        assert "Enrolment in program 9201" in r.prereq_text

    def test_academic_program_or_chain(self) -> None:
        # Reproduces the LAWS8855 pattern
        r = parse([
            ("0010", "COND Academic Program EQ 9201", ""),
            ("0020", "OR COND Academic Program EQ 9225", ""),
            ("0030", "OR COND Academic Plan EQ MNGTUS8625 78 units", ""),
        ])
        assert not r.has_unresolvable
        assert "9201" in r.prereq_text
        assert "9225" in r.prereq_text
        assert "MNGTUS8625" in r.prereq_text

    def test_generic_cond_not_unresolvable(self) -> None:
        r = parse([("0010", " COND Academic Career EQ UGRD", "")])
        assert not r.has_unresolvable
        assert "Enrolment in program" in r.prereq_text

    def test_generic_cond_other_subtype(self) -> None:
        r = parse([("0010", " COND Academic Level EQ 03", "")])
        assert not r.has_unresolvable

    def test_cond_does_not_produce_unresolvable_lines(self) -> None:
        r = parse([("0010", " COND Academic Career EQ UGRD", "")])
        assert r.unresolvable_lines == []


class TestPreCrsw:
    def test_uoc_maturity_parsed(self) -> None:
        r = parse([("0010", "PRE CRSW 72 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Completion of 72 UoC"

    def test_uoc_maturity_in_build_field(self) -> None:
        r = parse([("0010", "PRE CRSW 72 units", "")])
        assert build_prerequisites_field(r) == "Completion of 72 UoC"

    def test_faculty_and_course_pattern(self) -> None:
        # "PRE CRSW LAW JURD#### 72 units" — faculty + course pattern
        r = parse([("0010", "PRE CRSW LAW JURD#### 72 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Completion of 72 UoC in LAW JURD#### courses"

    def test_course_pattern_without_faculty(self) -> None:
        r = parse([("0010", "PRE CRSW JURD#### 72 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Completion of 72 UoC in JURD#### courses"

    def test_faculty_without_course_pattern(self) -> None:
        r = parse([("0010", "PRE CRSW LAW 72 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Completion of 72 UoC in LAW courses"

    def test_course_pattern_no_uoc_count(self) -> None:
        # "PRE CRSW ADA MDIA68##" — no UoC count; emit the pattern as a prereq token
        r = parse([("0010", "PRE CRSW ADA MDIA68##", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == "MDIA68##"

    def test_mdia3004_pattern(self) -> None:
        # Full MDIA3004 group: specific courses OR course pattern
        r = parse([
            ("0010", "AND (PRE CRSE 059498 ARTS2065", ""),
            ("0020", "OR PRE CRSE 064191 ARTS2066", ""),
            ("0030", "OR PRE CRSW ADA MDIA68## )", ""),
        ])
        assert not r.has_unresolvable
        assert "ARTS2065" in r.prereq_text
        assert "ARTS2066" in r.prereq_text
        assert "MDIA68##" in r.prereq_text

    def test_combined_with_course_prereq(self) -> None:
        r = parse([
            ("0010", "PRE CRSE 066432 ACCT2511", ""),
            ("0020", "AND PRE CRSW 48 units", ""),
        ])
        assert not r.has_unresolvable
        assert "ACCT2511" in r.prereq_text
        assert "Completion of 48 UoC" in r.prereq_text
    def test_co_crsw_uoc_goes_to_coreq(self) -> None:
        # "CO CRSW EDST67## 36 units" — corequisite coursework UoC
        r = parse([("0010", "CO CRSW EDST67## 36 units", "")])
        assert not r.has_unresolvable
        assert r.prereq_text == ""
        assert "Completion of 36 UoC in EDST67## courses" in r.coreq_text

    def test_edst6761_pattern(self) -> None:
        r = parse([
            ("0010", "PRE CRSE 061999 EDST5112", ""),
            ("0020", "AND PRE CRSE 062039 EDST5133", ""),
            ("0030", "AND CO CRSE 062035 EDST5132", ""),
            ("0040", "AND CO CRSW EDST67## 36 units", ""),
        ])
        assert not r.has_unresolvable
        assert "EDST5112" in r.prereq_text
        assert "EDST5133" in r.prereq_text
        assert "EDST5132" in r.coreq_text
        assert "Completion of 36 UoC in EDST67## courses" in r.coreq_text

# ---------------------------------------------------------------------------
# PRE RQ — external requirement pointers
# ---------------------------------------------------------------------------

class TestPreRq:
    def test_uoc_requirement_parsed(self) -> None:
        r = parse([
            ("0010", " PRE RQ 210000014 Completed 84 UoC target career 84 units", "210000014"),
        ])
        assert not r.has_unresolvable
        assert "Completion of 84 UoC" in r.prereq_text

    def test_bare_uoc_without_completed_keyword(self) -> None:
        # "PRE RQ 710000003 12 UoC in target career" — no "Completed" prefix
        r = parse([("0010", "PRE RQ 710000003 12 UoC in target career", "710000003")])
        assert not r.has_unresolvable
        assert "Completion of 12 UoC" in r.prereq_text

    def test_completion_of_noun_form(self) -> None:
        # "Completion of 144 UOC" — noun form rather than past-tense "Completed"
        r = parse([("0010", "PRE RQ 210000051 Completion of 144 UOC in Target Career", "210000051")])
        assert not r.has_unresolvable
        assert "Completion of 144 UoC" in r.prereq_text

    def test_completed_min_uoc(self) -> None:
        # "Completed min 100 UOC" — "min" qualifier before the number
        r = parse([("0010", "PRE RQ 260000030 Completed min 100 UOC", "260000030")])
        assert not r.has_unresolvable
        assert "Completion of 100 UoC" in r.prereq_text

    def test_overall_units(self) -> None:
        # "Overall Units 12" — different keyword structure
        r = parse([("0010", "PRE RQ 000500005 Overall Units 12", "500005")])
        assert not r.has_unresolvable
        assert "Completion of 12 UoC" in r.prereq_text

    def test_overall_units_check(self) -> None:
        # "Overall Units Check 96" — with extra Check keyword
        r = parse([("0010", "PRE RQ 000005004 Overall Units Check 96", "5004")])
        assert not r.has_unresolvable
        assert "Completion of 96 UoC" in r.prereq_text

    def test_uoc_in_build_field(self) -> None:
        r = parse([
            ("0010", " PRE RQ 210000014 Completed 84 UoC target career 84 units", "210000014"),
        ])
        text = build_prerequisites_field(r)
        assert "Completion of 84 UoC" in text

    def test_exclusion_parsed_as_condition(self) -> None:
        r = parse([("0010", "PRE RQ 000026739 Exclusion: AERO4500", "26739")])
        assert not r.has_unresolvable
        assert "Exclusion: AERO4500" in r.prereq_text

    def test_excl_abbreviation_normalised(self) -> None:
        r = parse([("0010", "PRE RQ 510000001 Excl: COMP9021", "510000001")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Exclusion: COMP9021"

    def test_exclude_verb_no_colon(self) -> None:
        r = parse([("0010", "CO RQ 210000039 Exclude INFS5602, INFS5978", "210000039")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Exclusion: INFS5602, INFS5978"

    def test_asymmetrical_exclusion_parsed(self) -> None:
        r = parse([("0010", "PRE RQ 000012345 Asymmetrical Exclusion: BIOM9914", "12345")])
        assert not r.has_unresolvable
        assert r.prereq_text == "Asymmetrical Exclusion: BIOM9914"

    def test_co_rq_exclusion_parsed(self) -> None:
        # "CO RQ ... Exclusion: COURSE." — corequisite requirement exclusion
        r = parse([("0010", "CO RQ 000021292 Exclusion: ACCT5930.", "21292")])
        assert not r.has_unresolvable
        assert "Exclusion: ACCT5930" in r.prereq_text

    def test_co_rq_non_exclusion_unresolvable(self) -> None:
        r = parse([("0010", "CO RQ 999999 Some other requirement", "999999")])
        assert r.has_unresolvable

    def test_equivalents_list_parsed_as_or_prereqs(self) -> None:
        r = parse([("0010", "PRE RQ 490000009 Equivalents: ARCH1101 BENV1012 INTA1001", "490000009")])
        assert not r.has_unresolvable
        assert r.prereq_text == "ARCH1101 OR BENV1012 OR INTA1001"

    def test_equivalents_with_cond_program(self) -> None:
        # DPDE1001 pattern: program condition AND equivalents list
        r = parse([
            ("0010", "COND Academic Program EQ 7006", ""),
            ("0020", "AND PRE RQ 490000009 Equivalents: ARCH1101 BENV1012 INTA1001 LAND2101 IDES1211", "490000009"),
        ])
        assert not r.has_unresolvable
        assert "ARCH1101" in r.prereq_text
        assert "IDES1211" in r.prereq_text

    def test_non_uoc_pre_rq_is_unresolvable(self) -> None:
        r = parse([("0010", " PRE RQ 999999 Some other requirement", "999999")])
        assert r.has_unresolvable

    def test_prose_with_embedded_course_codes_extracted(self) -> None:
        # "one of the following, MUSC1602, MUSC1807, or MUSC1604"
        r = parse([("0010", "PRE RQ 110000009 one of the following, MUSC1602, MUSC1807, or MUSC1604", "110000009")])
        assert not r.has_unresolvable
        assert "MUSC1602" in r.prereq_text
        assert "MUSC1807" in r.prereq_text
        assert "MUSC1604" in r.prereq_text

    def test_multiline_rq_description_extracts_codes(self) -> None:
        # Embedded \n causes codes to appear on the second line of the description.
        desc = "PRE RQ 260000006 School of CSE RULE1: \nenrolled in COMPBH stream and (DESN1000 or DPST1071)"
        r = parse([("0010", desc, "260000006")])
        assert not r.has_unresolvable
        assert "DESN1000" in r.prereq_text
        assert "DPST1071" in r.prereq_text

    def test_unresolvable_line_recorded(self) -> None:
        r = parse([("0010", " PRE RQ 999999 Unknown requirement", "999999")])
        assert len(r.unresolvable_lines) == 1
        assert "Unknown requirement" in r.unresolvable_lines[0]

    def test_mixed_group_unresolvable_if_any_line_unresolvable(self) -> None:
        r = parse([
            ("0010", " PRE CRSE 066426 COMM1140", ""),
            ("0020", " PRE RQ 999999 Unknown", "999999"),
        ])
        assert r.has_unresolvable

    def test_mark_from_resolved_lines_kept_even_when_group_unresolvable(self) -> None:
        r = parse([
            ("0010", " PRE CRSE 066426 COMM1140 65", ""),
            ("0020", " PRE RQ 999999 Unknown", "999999"),
        ])
        assert r.has_unresolvable
        # Mark was extracted before the unresolvable line was hit
        assert r.mark_requirements == {"COMM1140": 65}


# ---------------------------------------------------------------------------
# build_prerequisites_field
# ---------------------------------------------------------------------------

class TestBuildPrerequisitesField:
    def test_empty_result(self) -> None:
        r = ErgParseResult(
            prereq_text="", coreq_text="", has_unresolvable=False
        )
        assert build_prerequisites_field(r) == ""

    def test_prereq_only(self) -> None:
        r = ErgParseResult(
            prereq_text="COMM1140", coreq_text="", has_unresolvable=False
        )
        assert build_prerequisites_field(r) == "COMM1140"

    def test_coreq_only(self) -> None:
        r = ErgParseResult(
            prereq_text="", coreq_text="CEIC2001", has_unresolvable=False
        )
        assert build_prerequisites_field(r) == "Corequisite: CEIC2001"

    def test_prereq_and_coreq(self) -> None:
        r = ErgParseResult(
            prereq_text="CEIC2000", coreq_text="CEIC2001", has_unresolvable=False
        )
        text = build_prerequisites_field(r)
        assert text == "CEIC2000. Corequisite: CEIC2001"

    def test_mark_appended_after_prereq(self) -> None:
        r = ErgParseResult(
            prereq_text="COMM1140",
            coreq_text="",
            has_unresolvable=False,
            mark_requirements={"COMM1140": 65},
        )
        text = build_prerequisites_field(r)
        assert text == "COMM1140. Minimum mark of 65 in COMM1140"

    def test_marks_sorted_alphabetically(self) -> None:
        r = ErgParseResult(
            prereq_text="COMM1170 AND COMM1180",
            coreq_text="",
            has_unresolvable=False,
            mark_requirements={"COMM1180": 70, "COMM1170": 65},
        )
        text = build_prerequisites_field(r)
        # COMM1170 < COMM1180 alphabetically
        idx_70 = text.index("COMM1170")
        idx_80 = text.index("COMM1180")
        assert idx_70 < idx_80

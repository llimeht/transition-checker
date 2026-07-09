"""Tests for build_erg_expr() — structured ErgExpr builder from ERG rows."""

from __future__ import annotations

from transitionchecker.erg_parser import (
    ErgRow,
    build_erg_expr,
    rule_exprs_to_erg_expr,
    _match_erg_pattern, # pyright: ignore[reportPrivateUsage]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rows(*lines: tuple[str, str, str]) -> list[ErgRow]:
    """Build sorted ErgRow list from (group_number, detail_text, req_id)."""
    return [ErgRow(g, d, r) for g, d, r in lines]


# ---------------------------------------------------------------------------
# Pattern matching helper
# ---------------------------------------------------------------------------

class TestMatchErgPattern:
    def test_exact_match(self) -> None:
        assert _match_erg_pattern("CEIC3004", "CEIC3004")

    def test_case_insensitive(self) -> None:
        assert _match_erg_pattern("ceic3004", "CEIC3004")

    def test_hash_wildcards_all(self) -> None:
        assert _match_erg_pattern("JURD####", "JURD7001")
        assert _match_erg_pattern("JURD####", "JURD1234")
        assert not _match_erg_pattern("JURD####", "JURD7")

    def test_mixed_hash_and_digits(self) -> None:
        assert _match_erg_pattern("COMP3###", "COMP3021")
        assert _match_erg_pattern("COMP3###", "COMP3999")
        assert not _match_erg_pattern("COMP3###", "COMP2021")

    def test_no_partial_match(self) -> None:
        assert not _match_erg_pattern("MDIA68##", "MDIA6800X")


# ---------------------------------------------------------------------------
# build_erg_expr — PRE CRSE / CO CRSE
# ---------------------------------------------------------------------------

class TestBuildErgExprCrse:
    def test_single_prereq(self) -> None:
        r = build_erg_expr(rows(("0010", " PRE CRSE 066426 COMM1140", "")))
        assert r == {"prereq": "COMM1140"}

    def test_single_coreq(self) -> None:
        r = build_erg_expr(rows(("0010", " CO CRSE 066430 COMM1180", "")))
        assert r == {"coreq": "COMM1180"}

    def test_or_chain_prereqs(self) -> None:
        r = build_erg_expr(rows(
            ("0010", "( PRE CRSE 066426 COMM1140", ""),
            ("0015", "OR PRE CRSE 067800 COMM1240", ""),
            ("0020", "OR PRE CRSE 000001 ACCT1501 )", ""),
        ))
        assert r == {"or": [{"prereq": "COMM1140"}, {"prereq": "COMM1240"}, {"prereq": "ACCT1501"}]}

    def test_and_group_with_or(self) -> None:
        r = build_erg_expr(rows(
            ("0010", "( PRE CRSE 066428 COMM1170", ""),
            ("0020", "AND PRE CRSE 066430 COMM1180 )", ""),
            ("0030", "OR PRE CRSE 000002 ACCT1511", ""),
        ))
        assert r == {"or": [
            {"and": [{"prereq": "COMM1170"}, {"prereq": "COMM1180"}]},
            {"prereq": "ACCT1511"},
        ]}

    def test_operator_then_paren(self) -> None:
        # "AND (PRE CRSE ..." pattern — ACCT3610
        r = build_erg_expr(rows(
            ("0010", "PRE CRSE 066432 ACCT2511", ""),
            ("0020", "AND (PRE CRSE 066430 COMM1180", ""),
            ("0030", "OR PRE CRSE 006301 ECON1102 )", ""),
        ))
        assert r == {"and": [
            {"prereq": "ACCT2511"},
            {"or": [{"prereq": "COMM1180"}, {"prereq": "ECON1102"}]},
        ]}

    def test_mixed_prereq_coreq_ceic4001(self) -> None:
        """CEIC4001 pattern: coreq inside an OR alternative."""
        r = build_erg_expr(rows(
            ("0010", "( PRE CRSE 000001 CEIC3004", ""),
            ("0020", "AND PRE CRSE 000002 CEIC3005 )", ""),
            ("0030", "OR ( PRE CRSE 000002 CEIC3005", ""),
            ("0040", "AND PRE CRSE 000003 CEIC3006 )", ""),
            ("0050", "OR ( PRE CRSE 000003 CEIC3006", ""),
            ("0060", "AND PRE CRSE 000001 CEIC3004", ""),
            ("0070", "AND CO CRSE 000004 CEIC3007 )", ""),
        ))
        assert r == {"or": [
            {"and": [{"prereq": "CEIC3004"}, {"prereq": "CEIC3005"}]},
            {"and": [{"prereq": "CEIC3005"}, {"prereq": "CEIC3006"}]},
            {"and": [{"prereq": "CEIC3006"}, {"prereq": "CEIC3004"}, {"coreq": "CEIC3007"}]},
        ]}

    def test_unresolvable_row_returns_none(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE RQ 999999 Some unresolvable thing", "999999")))
        assert r is None


# ---------------------------------------------------------------------------
# build_erg_expr — COND conditions
# ---------------------------------------------------------------------------

class TestBuildErgExprCond:
    def test_academic_plan_eq_is_condition(self) -> None:
        r = build_erg_expr(rows(("0010", "COND Academic Plan EQ ACCTB13554", "")))
        assert r is not None
        assert "condition" in r
        assert "ACCTB13554" in r["condition"]

    def test_academic_program_eq_is_condition(self) -> None:
        r = build_erg_expr(rows(("0010", "COND Academic Program EQ 9201", "")))
        assert r is not None
        assert "condition" in r

    def test_generic_cond_is_condition(self) -> None:
        r = build_erg_expr(rows(("0010", "COND Academic Career EQ UGRD", "")))
        assert r is not None
        assert "condition" in r

    def test_pure_cond_group_not_none(self) -> None:
        """ENGG1811 pattern: pure COND group should produce a result (not None)."""
        r = build_erg_expr(rows(
            ("0010", "COND Academic Program IN 000219", ""),
            ("0020", "OR COND Academic Program IN 000217", ""),
        ))
        assert r is not None
        assert "or" in r or "condition" in r


# ---------------------------------------------------------------------------
# build_erg_expr — CRSW (UoC maturity / course pattern)
# ---------------------------------------------------------------------------

class TestBuildErgExprCrsw:
    def test_uoc_maturity(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE CRSW 72 units", "")))
        assert r == {"uoc": 72}

    def test_uoc_with_restriction(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE CRSW LAW JURD#### 72 units", "")))
        assert r is not None
        assert r.get("uoc") == 72
        assert r.get("restriction") == "JURD####"

    def test_course_pattern_no_uoc(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE CRSW ADA MDIA68##", "")))
        assert r == {"prereq_pattern": "MDIA68##"}

    def test_co_crsw_uoc_is_not_prereq(self) -> None:
        r = build_erg_expr(rows(("0010", "CO CRSW EDST67## 36 units", "")))
        # CO CRSW with UoC — uoc node (treated as prereq-style UoC maturity)
        assert r is not None
        assert "uoc" in r


# ---------------------------------------------------------------------------
# build_erg_expr — PRE RQ UoC and equivalents
# ---------------------------------------------------------------------------

class TestBuildErgExprRq:
    def test_uoc_rq(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE RQ 210000014 Completed 84 UoC target career 84 units", "210000014")))
        assert r == {"uoc": 84}

    def test_equivalents_list(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE RQ 490000009 Equivalents: ARCH1101 BENV1012 INTA1001", "490000009")))
        assert r == {"or": [{"prereq": "ARCH1101"}, {"prereq": "BENV1012"}, {"prereq": "INTA1001"}]}

    def test_prose_with_codes(self) -> None:
        r = build_erg_expr(rows(("0010", "PRE RQ 110000009 one of MUSC1602 or MUSC1807", "110000009")))
        assert r is not None
        assert "or" in r


# ---------------------------------------------------------------------------
# rule_exprs_to_erg_expr
# ---------------------------------------------------------------------------

class TestRuleExprsToErgExpr:
    def test_single_prereq(self) -> None:
        r = rule_exprs_to_erg_expr("CEIC3004", None)
        assert r == {"prereq": "CEIC3004"}

    def test_single_coreq(self) -> None:
        r = rule_exprs_to_erg_expr(None, "CEIC3007")
        assert r == {"coreq": "CEIC3007"}

    def test_prereq_and_coreq_combined_with_and(self) -> None:
        r = rule_exprs_to_erg_expr("CEIC3004", "CEIC3007")
        assert r == {"and": [{"prereq": "CEIC3004"}, {"coreq": "CEIC3007"}]}

    def test_both_none(self) -> None:
        r = rule_exprs_to_erg_expr(None, None)
        assert r == {"condition": ""}

    def test_or_prereq(self) -> None:
        r = rule_exprs_to_erg_expr({"or": ["COMM1140", "COMM1240"]}, None)
        assert r == {"or": [{"prereq": "COMM1140"}, {"prereq": "COMM1240"}]}

    def test_and_prereq(self) -> None:
        r = rule_exprs_to_erg_expr({"and": ["CEIC2000", "CEIC2001"]}, None)
        assert r == {"and": [{"prereq": "CEIC2000"}, {"prereq": "CEIC2001"}]}

    def test_uoc_prereq(self) -> None:
        r = rule_exprs_to_erg_expr({"uoc": 48}, None)
        assert r == {"uoc": 48}

    def test_nested_and_or(self) -> None:
        rule = {"or": [{"and": ["A1234", "B5678"]}, "C9012"]}
        r = rule_exprs_to_erg_expr(rule, None)
        assert r == {"or": [
            {"and": [{"prereq": "A1234"}, {"prereq": "B5678"}]},
            {"prereq": "C9012"},
        ]}

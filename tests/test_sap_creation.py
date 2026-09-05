"""SAP creation package, base-colour resolution and supplier template drift.

The transformation rules were derived from the Carhartt FW26 pair (supplier
sheet + the SAP sheet Flender accepted) and verified against all 8,824 rows.
These tests pin the rules and the cases that were found to break them.
"""
from __future__ import annotations

import pytest

from app.core.base_color import (
    DEFAULT_VOCAB, audit_lookup, learn_lookup, propose,
)
from app.core.sap_creation import (
    REQUIRED, SAP_COLUMNS, build_creation_sheet, build_row,
    season_code, split_colour_code,
)
from app.core.template_diff import describe, diff_templates, normalise_header


# ── Season codes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("season,expected", [
    ("SS27", "2027-01"),   # matches the tracker header "2027-01 SPRING"
    ("SP26", "2026-01"),   # Thisisneverthat SS26 DC sheet says U_Saison 2026-01
    ("FW26", "2026-03"),   # matches the accepted Carhartt SAP sheet
    ("AW26", "2026-03"),
])
def test_season_code(season, expected):
    assert season_code(season)[0] == expected


def test_unknown_season_is_refused_not_guessed():
    # Holiday has no slot defined; inventing one would silently mis-file a
    # whole collection in SAP.
    code, reason = season_code("HO26")
    assert code is None and "HO" in reason


def test_malformed_season_is_refused():
    assert season_code("junk")[0] is None


# ── Colour code split ────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,house,finish", [
    ("8902", "89", "02"),      # 4-character code
    ("3T64O", "3T6", "4O"),    # 5-character code — split from the END
    ("00PXX", "00P", "XX"),
])
def test_split_colour_code(code, house, finish):
    assert split_colour_code(code) == (house, finish)


# ── Base colour ──────────────────────────────────────────────────────────────

def test_history_beats_rules_and_is_certain():
    lookup = learn_lookup([("Pumice", "Grey")])
    r = propose("Pumice", DEFAULT_VOCAB, lookup)
    assert r["value"] == "Grey" and r["confidence"] == 1.0
    assert r["source"] == "history"


def test_history_lookup_is_case_insensitive():
    lookup = learn_lookup([("Dark Navy", "BLUE")])
    assert propose("dark navy", DEFAULT_VOCAB, lookup)["value"] == "Blue"


@pytest.mark.parametrize("name,expected", [
    ("Steel Blue", "Blue"),                 # head noun
    ("Dark Navy", "Blue"),                  # synonym
    ("Alton Check, Cypress", "Multicolour"),  # pattern outranks the colour
    ("Camo Brute Bark, Moor", "Multicolour"),
    ("Todrick Houndstooth, Blue", "Multicolour"),
])
def test_rule_proposals(name, expected):
    assert propose(name, DEFAULT_VOCAB)["value"] == expected


def test_slash_takes_the_first_colour_only():
    # "Palisander / Black" is Brown in the FW26 data. Falling through to the
    # whole string would wrongly answer Black.
    assert propose("Palisander / Black", DEFAULT_VOCAB)["value"] is None


def test_slash_resolves_when_the_leading_colour_is_known():
    lookup = learn_lookup([("Palisander", "Brown")])
    assert propose("Palisander / Black", DEFAULT_VOCAB, lookup)["value"] == "Brown"


def test_opaque_name_declines_rather_than_guessing():
    r = propose("Styx", DEFAULT_VOCAB)
    assert r["value"] is None and r["confidence"] == 0.0


def test_audit_finds_the_casing_duplicates_in_real_data():
    # Carhartt FW26 stores both 'BLACK'/'Black' and 'BEIGE'/'Beige'.
    a = audit_lookup([("Soot", "BLACK"), ("Raven", "Black"), ("Wall", "BEIGE")])
    assert "Black" in a["casing_duplicates"]


def test_audit_reports_a_name_classified_two_ways():
    a = audit_lookup([("Moor", "Green"), ("Moor", "Brown")])
    assert "moor" in a["conflicts"]


# ── Creation rows ────────────────────────────────────────────────────────────

def _src(**kw):
    base = {
        "Item No.": "I026462", "Colour Code": "8902", "Color": "Black",
        "Size": "26", "Size Run": "30", "Brand": "CARHARTT",
        "Barcode": "4058459591286", "Item Description": "Bib Overall",
        "Item Group": "Pants Bib Non-Denim", "Main Waregroup": "Pants Bib",
        "New/Repeat Style": "Repeat", "New/Repeat Colour": "New",
        "Wholesale Price EUR": "78.75", "SKU": "I026462.8902.30.26",
    }
    base.update(kw)
    return base


def test_derived_columns_match_the_accepted_carhartt_sheet():
    row, exc = build_row(_src(), season="FW26", collection="Main")
    assert row["Season"] == "2026-03"
    assert row["Item.Colour"] == "I026462.8902"
    assert row["House Colour"] == "89"
    assert row["Finish"] == "02"
    assert row["New/Repeat"] == "Repeat"          # the Style flag, not Colour
    assert row["Main Waregroup"] == "Pants Bib Non-Denim"  # Item Group wins
    assert not [e for e in exc if e["severity"] == "critical"]


def test_output_uses_the_full_sap_layout_in_order():
    row, _ = build_row(_src(), season="FW26", collection="Main")
    assert list(row.keys()) == SAP_COLUMNS


def test_unknown_colour_raises_a_review_not_a_blank_write():
    row, exc = build_row(_src(Color="Pumice"), season="SS27", collection="Main")
    assert row["BASE COLOR"] == ""
    flagged = [e for e in exc if e["field"] == "BASE COLOR"]
    assert flagged and flagged[0]["severity"] == "manual_review"
    assert "Pumice" in flagged[0]["reason"]


def test_proposed_colour_is_a_warning_so_a_human_confirms_it():
    row, exc = build_row(_src(Color="Steel Blue"), season="SS27", collection="Main")
    assert row["BASE COLOR"] == "Blue"
    assert [e for e in exc if e["field"] == "BASE COLOR"][0]["severity"] == "warning"


def test_missing_required_field_is_critical():
    _, exc = build_row(_src(Barcode=""), season="FW26", collection="Main")
    assert any(e["field"] == "Barcode" and e["severity"] == "critical" for e in exc)


def test_headers_with_stray_whitespace_still_map():
    src = _src()
    src["Wholesale  Price  EUR"] = src.pop("Wholesale Price EUR")
    row, _ = build_row(src, season="FW26", collection="Main")
    assert row["Wholesale Price EUR"] == "78.75"


def test_sheet_groups_colour_decisions_by_name_not_by_line():
    # 3 lines share one unclassified colour: that is ONE decision, not three.
    rows = [_src(Color="Pumice", SKU=f"X{i}", Size=str(i)) for i in range(3)]
    out = build_creation_sheet(rows, season="SS27")
    assert out["summary"]["colour_decisions"] == ["Pumice"]
    assert out["summary"]["rows"] == 3


def test_required_columns_are_all_part_of_the_layout():
    assert set(REQUIRED) <= set(SAP_COLUMNS)


# ── Template drift ───────────────────────────────────────────────────────────

def test_whitespace_only_change_is_not_reported_as_a_change():
    # The real Carhartt case: "Earliest  Shipment  Date" vs "Earliest Shipment Date".
    d = diff_templates(["Earliest  Shipment  Date"], ["Earliest Shipment Date"])
    assert d["added"] == [] and d["removed"] == []
    assert d["has_changes"] is False
    assert len(d["cosmetic"]) == 1


def test_genuinely_new_column_is_reported():
    d = diff_templates(["Retail EUR"], ["Retail EUR", "Retail CAD"])
    assert d["added"] == ["Retail CAD"] and d["has_changes"] is True


def test_reordering_alone_does_not_need_a_decision():
    d = diff_templates(["A", "B"], ["B", "A"])
    assert d["has_changes"] is False and len(d["moved"]) == 2


def test_removed_column_is_reported():
    d = diff_templates(["A", "B"], ["A"])
    assert d["removed"] == ["B"] and d["has_changes"] is True


def test_describe_only_asks_about_material_changes():
    d = diff_templates(["A  B"], ["A B", "New Col"])
    lines = describe(d, brand="Carhartt WIP", season="SS27")
    assert any("New Col" in ln for ln in lines)
    assert any("spacing" in ln for ln in lines)


def test_normalise_header_collapses_runs_of_space():
    assert normalise_header("  Earliest   Shipment  Date ") == "Earliest Shipment Date"


# ── Supplier sheet layout ────────────────────────────────────────────────────

def test_header_is_found_below_a_title_block():
    """The common real case: brand name and ship dates above the real header."""
    import pandas as pd
    from app.core.sheet_layout import find_header_row
    raw = pd.DataFrame([
        ["EDWIN Collection", None, None, None],
        [None, None, None, None],
        ["Start Ship", "2026-07-01", None, None],
        ["Style Number", "Colour", "Size", "Barcode"],
        ["E-1001", "Blue", "M", "123"],
    ])
    assert find_header_row(raw) == 3


def test_a_numeric_row_is_not_mistaken_for_a_header():
    import pandas as pd
    from app.core.sheet_layout import find_header_row
    raw = pd.DataFrame([
        ["Style", "Colour", "Size", "Price"],
        ["1", "2", "3", "4"],
    ])
    assert find_header_row(raw) == 0


def test_sibling_delivery_sheets_are_kept_together():
    """HUF sends one tab per delivery date; both belong to one collection."""
    import pandas as pd
    from app.core.sheet_layout import pick_data_sheets
    head = ["Style Number", "Color", "Size"]
    d1 = pd.DataFrame([head, ["A", "Black", "M"], ["B", "Blue", "L"]])
    d2 = pd.DataFrame([head, ["C", "Red", "S"]])
    summary = pd.DataFrame([["Order Summary"], ["total"]])
    picked = pick_data_sheets({"Order Summary": summary, "07_31": d1, "08_28": d2})
    assert set(picked) == {"07_31", "08_28"}


def test_an_unrelated_summary_tab_is_left_out():
    import pandas as pd
    from app.core.sheet_layout import pick_data_sheets
    data = pd.DataFrame([["Style Number", "Color", "Size"],
                         ["A", "Black", "M"], ["B", "Blue", "L"]])
    other = pd.DataFrame([["Date", "Quantity", "Amount"], ["x", "1", "2"]])
    assert pick_data_sheets({"ACL FW26": data, "Total": other}) == ["ACL FW26"]

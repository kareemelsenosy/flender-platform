"""SAP reference feed — the nightly per-brand stock sheet.

Shapes taken from the live Gramicci Stock sheet: a "Date Updated" stamp above
the real header, one row per size, prices written as text with the currency
appended.
"""
from __future__ import annotations

from app.core.sap_reference import (
    SapReference, find_header_row, parse_tab, parse_workbook, price_history,
)

HEADER = ["Picture", "Brand Name", "Item Group", "Manufacturer Code",
          "Web Description 2", "Barcode", "Gender", "Color", "Size",
          "FreeStock", "WHS Price", "RRP Price", "Pictures", "Season"]


def _sheet(*rows):
    """A tab shaped like SAP writes it: stamp row, header row, then data."""
    return [["Date Updated", "2026-09-09 0:24:50"], HEADER, *rows]


def _row(mfr="G3FM-J002-BARK PIGMENT", group="GCI J G3FM-J002 BARK PIGMENT",
         barcode="195612724779", size="S", colour="Bark Pigment",
         gender="Men", whs="281.00 AED", rrp="590.00 AED", pics="Twill Jacket"):
    return ["", "GRAMICCI", group, mfr, "Twill-Around Jacket", barcode,
            gender, colour, size, "1", whs, rrp, pics, "2026-01"]


# ── Reading the sheet ────────────────────────────────────────────────────────

def test_header_is_found_below_the_date_stamp():
    # Reading row 0 as the header yields a table of empty columns.
    assert find_header_row(_sheet(_row())) == 1


def test_header_is_found_when_there_is_no_stamp():
    assert find_header_row([HEADER, _row()]) == 0


def test_fields_are_normalised_from_sap_column_names():
    rec = parse_tab(_sheet(_row()), "ReOrder")[0]
    assert rec["item_group"] == "GCI J G3FM-J002 BARK PIGMENT"
    assert rec["manufacturer_code"] == "G3FM-J002-BARK PIGMENT"
    assert rec["gender"] == "Men"
    assert rec["segment"] == "ReOrder"


def test_spacer_and_total_rows_are_dropped():
    blank = [""] * len(HEADER)
    assert len(parse_tab(_sheet(_row(), blank), "ReOrder")) == 1


def test_every_tab_is_read_and_tagged_with_its_segment():
    recs = parse_workbook({"ReOrder": _sheet(_row()),
                           "PreOrder_2027-01": _sheet(_row(barcode="999"))})
    assert {r["segment"] for r in recs} == {"ReOrder", "PreOrder_2027-01"}


# ── Lookup ───────────────────────────────────────────────────────────────────

def _ref(*rows):
    return SapReference(parse_tab(_sheet(*rows), "ReOrder"))


def test_a_supplier_style_and_colour_is_found():
    ref = _ref(_row())
    assert ref.exists(style="G3FM-J002", colour="BARK PIGMENT")


def test_barcode_wins_over_style_matching():
    ref = _ref(_row())
    assert ref.find(barcode="195612724779")[0]["size"] == "S"


def test_a_style_not_in_sap_is_not_invented():
    assert _ref(_row()).find(style="NOPE-123", colour="Black") == []


def test_sizes_are_collected_under_the_style_colour():
    ref = _ref(_row(size="S", barcode="1"), _row(size="M", barcode="2"),
               _row(size="L", barcode="3"))
    known = ref.known_values(style="G3FM-J002", colour="BARK PIGMENT")
    assert known["sizes"] == ["L", "M", "S"]


def test_known_values_supply_the_sap_item_group():
    """This is what settles TOP -> SHIRTS or T-SHIRTS for a repeat style."""
    known = _ref(_row()).known_values(style="G3FM-J002", colour="BARK PIGMENT")
    assert known["item_group"] == "GCI J G3FM-J002 BARK PIGMENT"
    assert known["gender"] == "Men"


def test_image_status_is_reported():
    assert _ref(_row(pics="Twill Jacket")).known_values(
        style="G3FM-J002", colour="BARK PIGMENT")["has_images"] is True
    assert _ref(_row(pics="")).known_values(
        style="G3FM-J002", colour="BARK PIGMENT")["has_images"] is False


def test_classify_splits_an_order_sheet_into_existing_and_new():
    ref = _ref(_row())
    rows = [{"Style Number": "G3FM-J002", "Color": "BARK PIGMENT", "Barcode": ""},
            {"Style Number": "BRAND-NEW", "Color": "Black", "Barcode": ""}]
    out = ref.classify(rows)
    assert out["summary"] == {"rows": 2, "existing": 1, "new": 1}


def test_seasons_present_are_reported():
    assert _ref(_row()).seasons == ["2026-01"]


# ── Price history ────────────────────────────────────────────────────────────

def test_prices_are_pulled_out_of_sap_text():
    """SAP writes '281.00 AED', not a number."""
    out = price_history(parse_tab(_sheet(_row()), "ReOrder"))
    assert out[0]["Flender WHS AED"] == 281.0
    assert out[0]["Flender RRP AED"] == 590.0


def test_rows_without_any_price_are_skipped():
    assert price_history(parse_tab(_sheet(_row(whs="", rrp="")), "ReOrder")) == []

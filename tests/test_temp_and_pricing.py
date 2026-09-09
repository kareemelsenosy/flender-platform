"""TEMP STANDARD TEMPLATE generation and per-brand pricing review.

Rules derived from the Hiking Patrol SS27 pair (order form + the TEMP sheet
Flender accepted) and from Flender's own Carhartt and Hiking Patrol pricing
files. These pin the rules and the cases that broke the first attempt.
"""
from __future__ import annotations

import pytest

from app.core.pricing import (
    RRP_OVER_WHS, learn_strategy, margin_pct, review_prices,
)
from app.core.temp_template import (
    TEMP_COLUMNS, barcode, build_temp_sheet, expand_sizes, normalise_size,
    size_list_code, smart_title, sort_index,
)


# ── Field rules ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,expected", [
    ("OS", "onesize"), ("O/S", "onesize"), ("One Size", "onesize"),
    ("XS", "XS"), ("M", "M"),
])
def test_normalise_size(given, expected):
    assert normalise_size(given) == expected


def test_smart_title_keeps_acronyms():
    # The accepted sheet says "Edge LT Softshell", not "Edge Lt Softshell".
    assert smart_title("EDGE LT SOFTSHELL TECHNICAL JACKET") == \
        "Edge LT Softshell Technical Jacket"


def test_barcode_is_style_colour_size_alphanumeric():
    assert barcode("HP0127001", "COYOTE BROWN", "XS") == "HP0127001COYOTEBROWNXS"


def test_barcode_uses_the_normalised_size():
    # 'OS' on the order form is 'onesize' in SAP, and the barcode follows.
    assert barcode("HP0127302", "BLACK", "OS") == "HP0127302BLACKONESIZE"


def test_sort_index_steps_by_500_and_sends_onesize_last():
    assert [sort_index(i, "M") for i in range(5)] == [1000, 1500, 2000, 2500, 3000]
    assert sort_index(0, "OS") == 58000


def test_size_list_code_for_a_known_run():
    assert size_list_code(["XS", "S", "M", "L", "XL"])[0] == "XS26XL"
    assert size_list_code(["OS"])[0] == "ONESIZE"


def test_unknown_size_run_is_refused_not_guessed():
    code, why = size_list_code(["XS", "XXXL"])
    assert code == "" and "no SAP size-list code" in why


# ── Size expansion ───────────────────────────────────────────────────────────

def _order_row(**kw):
    base = {"Style Number": "HP0127001", "Color": "COYOTE BROWN",
            "Name": "EDGE LT SOFTSHELL TECHNICAL JACKET", "Subcategory": "JACKET",
            "Season": "SS27",
            "Size 1": "XS", "Size 2": "S", "Size 3": "M",
            "Size 4": "L", "Size 5": "XL"}
    base.update(kw)
    return base


def test_one_order_row_becomes_one_row_per_size():
    assert len(expand_sizes(_order_row())) == 5


def test_empty_size_columns_are_skipped():
    row = _order_row(**{"Size 3": None, "Size 4": "", "Size 5": None})
    assert [s["size"] for s in expand_sizes(row)] == ["XS", "S"]


# ── Sheet build ──────────────────────────────────────────────────────────────

GROUPS = {"JACKET": ["JACKETS"], "TOP": ["SHIRTS", "T-SHIRTS"]}


def test_build_matches_the_accepted_field_rules():
    out = build_temp_sheet([_order_row()], brand="Hiking Patrol", season="SS27",
                           group_map=GROUPS, colour_lookup={"coyote brown": "Brown"})
    r = out["rows"][0]
    assert list(r.keys()) == TEMP_COLUMNS
    assert r["U_Season"] == "2027-01"
    assert r["U_SCode"] == "HP0127001"
    assert r["U_SuppCatNum"] == "HP0127001-COYOTE BROWN"
    assert r["U_Web_Color"] == "Coyote Brown"
    assert r["U_Webbeschreibung"] == "HIKING PATROL"
    assert r["U_Webbeschreibung2"] == "Edge LT Softshell Technical Jacket"
    assert r["U_VCat"] == "Jacket"
    assert r["U_ItmsGrpCod"] == "JACKETS"
    assert r["U_def_size_list_code"] == "XS26XL"
    assert r["U_BaseColor"] == "brown"        # lower-cased in this template
    assert out["summary"]["rows"] == 5


def test_osrow_repeats_across_the_sizes_of_one_order_row():
    out = build_temp_sheet([_order_row(), _order_row(**{"Style Number": "HP2"})],
                           brand="HP", season="SS27", group_map=GROUPS)
    assert {r["U_OSRow"] for r in out["rows"][:5]} == {"1"}
    assert {r["U_OSRow"] for r in out["rows"][5:]} == {"2"}


def test_ambiguous_subcategory_asks_instead_of_picking():
    # TOP is SHIRTS or T-SHIRTS; only the description decides.
    out = build_temp_sheet([_order_row(Subcategory="TOP")], brand="HP",
                           season="SS27", group_map=GROUPS)
    assert out["rows"][0]["U_ItmsGrpCod"] == ""
    flagged = [e for e in out["exceptions"] if e["field"] == "U_ItmsGrpCod"]
    assert flagged and flagged[0]["severity"] == "manual_review"
    assert "SHIRTS or T-SHIRTS" in flagged[0]["reason"]


def test_fields_the_order_form_cannot_supply_are_raised_not_invented():
    out = build_temp_sheet([_order_row()], brand="HP", season="SS27",
                           group_map=GROUPS)
    fields = {e["field"] for e in out["exceptions"]}
    assert {"U_HS_Code", "U_COO"} <= fields
    assert out["rows"][0]["U_HS_Code"] == ""


def test_history_fills_what_the_order_form_lacks():
    out = build_temp_sheet(
        [_order_row()], brand="HP", season="SS27", group_map=GROUPS,
        history={"HP0127001": {"U_HS_Code": "6203330000", "U_COO": "CN",
                               "U_Gender": "M", "U_VCName": "M"}})
    assert out["rows"][0]["U_HS_Code"] == "6203330000"
    assert not [e for e in out["exceptions"] if e["field"] == "U_HS_Code"]


def test_an_order_row_with_no_sizes_is_critical():
    row = {k: v for k, v in _order_row().items() if not k.startswith("Size")}
    out = build_temp_sheet([row], brand="HP", season="SS27", group_map=GROUPS)
    assert any(e["severity"] == "critical" for e in out["exceptions"])


# ── Pricing ──────────────────────────────────────────────────────────────────

def _priced(margin=53.0, rrp_mult=RRP_OVER_WHS, whs=100.0, sku="X1"):
    cost = whs * (1 - margin / 100)
    return {"Item No.": sku, "Flender WHS USD": whs,
            "Flender Cost Prices (EUR-USD)": cost,
            "Flender RRP USD": whs * rrp_mult}


def test_margin_matches_flenders_own_calculation():
    assert margin_pct(100, 47) == pytest.approx(53.0)


def test_strategy_is_learned_from_the_brands_own_history():
    s = learn_strategy([_priced(margin=44.0) for _ in range(30)])
    assert s["known"] and s["margin_pct"] == pytest.approx(44.0, abs=0.1)
    assert s["consistent"] is True


def test_two_brands_learn_two_different_margins():
    # The whole point: Carhartt sits near 53%, Hiking Patrol near 44%.
    car = learn_strategy([_priced(margin=53.0) for _ in range(20)])
    hik = learn_strategy([_priced(margin=44.0) for _ in range(20)])
    assert car["margin_pct"] != hik["margin_pct"]


def test_no_history_means_no_invented_target():
    assert learn_strategy([])["known"] is False


def test_margin_well_under_the_brands_own_is_manual_review():
    s = learn_strategy([_priced(margin=44.0) for _ in range(20)])
    ex = review_prices([_priced(margin=30.0)], s)["exceptions"]
    assert ex and ex[0]["severity"] == "manual_review"
    assert "44.0%" in ex[0]["reason"]


def test_margin_matching_the_brand_passes_silently():
    s = learn_strategy([_priced(margin=44.0) for _ in range(20)])
    out = review_prices([_priced(margin=44.0)], s)
    assert out["exceptions"] == [] and out["summary"]["passed"] == 1


def test_a_margin_normal_for_one_brand_is_flagged_for_another():
    hik = learn_strategy([_priced(margin=44.0) for _ in range(20)])
    car = learn_strategy([_priced(margin=53.0) for _ in range(20)])
    row = _priced(margin=44.0)
    assert review_prices([row], hik)["exceptions"] == []
    assert review_prices([row], car)["exceptions"] != []


def test_unusual_rrp_multiple_is_flagged():
    s = learn_strategy([_priced() for _ in range(20)])
    ex = review_prices([_priced(rrp_mult=1.5)], s)["exceptions"]
    assert any(e["field"] == "rrp" for e in ex)


def test_missing_cost_blocks_import():
    s = learn_strategy([_priced() for _ in range(20)])
    row = _priced(); row["Flender Cost Prices (EUR-USD)"] = None
    assert review_prices([row], s)["exceptions"][0]["severity"] == "critical"


def test_supplier_cost_jump_is_reported_against_last_season():
    s = learn_strategy([_priced(margin=44.0) for _ in range(20)])
    now = _priced(margin=44.0)
    prev = {"Flender Cost Prices (EUR-USD)": now["Flender Cost Prices (EUR-USD)"] / 1.3}
    ex = review_prices([now], s, {"X1": prev})["exceptions"]
    assert any(e["field"] == "cost" and "30%" in e["reason"] for e in ex)


def test_pandas_missing_values_do_not_become_rows_or_text():
    """A DataFrame converted to dicts carries NaN, and str(nan) == 'nan'."""
    from app.core.temp_template import is_blank
    assert is_blank(float("nan")) and is_blank("nan") and is_blank("NaT")
    assert not is_blank("XS")

    row = _order_row(**{"Size 3": float("nan"), "Size 4": "nan", "Size 5": None})
    assert [s["size"] for s in expand_sizes(row)] == ["XS", "S"]


def test_history_of_nan_is_treated_as_absent():
    out = build_temp_sheet(
        [_order_row()], brand="HP", season="SS27", group_map=GROUPS,
        history={"HP0127001": {"U_HS_Code": float("nan"), "U_COO": "CN"}})
    assert out["rows"][0]["U_HS_Code"] == ""
    assert out["rows"][0]["U_COO"] == "CN"


def test_ship_date_is_asked_for_not_copied_from_the_order_form():
    """Hiking Patrol shipped 20 Jan on the order form and 15 Jan into SAP."""
    out = build_temp_sheet([_order_row()], brand="HP", season="SS27",
                           group_map=GROUPS)
    assert out["rows"][0]["U_ESDate"] == ""
    assert any(e["field"] == "U_ESDate" for e in out["exceptions"])

    given = build_temp_sheet([_order_row()], brand="HP", season="SS27",
                             group_map=GROUPS,
                             earliest_ship_date="2027-01-15 00:00:00")
    assert given["rows"][0]["U_ESDate"] == "2027-01-15 00:00:00"
    assert not [e for e in given["exceptions"] if e["field"] == "U_ESDate"]

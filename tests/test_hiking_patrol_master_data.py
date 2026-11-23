"""Hiking Patrol SS27 and FW26, checked against Flender's finished TEMP sheets.

SS27 is the NuORDER order form plus the brand's master data (HS code and
origin per style). FW26 has only the master data: one row per style with its
colours and sizes listed in a cell, next to an old season's tab.
"""
from __future__ import annotations

import pandas as pd

from app.core import intake
from app.core.learn_from_sheet import _join
from app.core.sheet_layout import load_supplier_table
from app.core.temp_template import web_colour
from app.services import packages as pkg


def test_the_master_data_is_the_product_list():
    assert intake.classify_attachment(
        "Master data_ Mainline_ss27_ver1_SN_20260619 (1).xlsx") == intake.PRODUCT_LIST
    assert intake.classify_attachment("HIKING PATROL SS27 ORDERFROM.xlsx") == intake.ORDER_SHEET


def test_this_seasons_tab_beats_a_taller_old_one(tmp_path):
    path = tmp_path / "master.xlsx"
    this = pd.DataFrame({f"C{i}": [f"v{i}"] * 10 for i in range(12)})
    this.insert(0, "STYLE NO", [f"HP0226{i:03d}" for i in range(10)])
    old = pd.DataFrame({"STYLE NO": [f"AW24{i:04d}" for i in range(30)],
                        "STYLE NAME": ["x"] * 30})
    old = old.reindex(columns=["STYLE NO", "STYLE NAME"] + [f"E{i}" for i in range(20)])
    with pd.ExcelWriter(path) as xw:
        this.to_excel(xw, sheet_name="AW26", index=False)
        old.to_excel(xw, sheet_name="FW24_master", index=False)
    _table, info = load_supplier_table(str(path))
    assert info["sheets_used"] == ["AW26"]


def test_colours_listed_in_one_cell_become_one_row_each():
    mapping = {"Color": {"header": "COLORS"}}
    rows = [{"Item No.": "HP0226001", "COLORS": "CHARCOAL BLACK, GREY, STONE",
             "Color": "CHARCOAL BLACK, GREY, STONE", "Size": "XS, S"}]
    out = pkg._split_colour_lists(rows, mapping)
    assert [r["Color"] for r in out] == ["CHARCOAL BLACK", "GREY", "STONE"]
    # A single header names one colour, comma or not (Carhartt).
    one = pkg._split_colour_lists(
        [{"Color": "Alton Check, Cypress"}], {"Color": {"header": "Color"}})
    assert [r["Color"] for r in one] == ["Alton Check, Cypress"]


def test_a_colour_is_corrected_only_to_a_colour_word():
    from app.core.base_color import DEFAULT_VOCAB, SYNONYMS
    words = set(SYNONYMS) | {v.lower() for v in DEFAULT_VOCAB}
    rows = [{"Color": c} for c in ("CHARCOAL BLACK", "CHACOAL BLACK", "SMOKEY BROWN",
                                   "SMOKY BROWN", "GREY", "STONE")]
    pkg._name_typos(rows, "Color", "U_Web_Color", only_to=words)
    assert [r["Color"] for r in rows][:4] == [
        "CHARCOAL BLACK", "CHARCOAL BLACK", "SMOKEY BROWN", "SMOKY BROWN"]


def test_lt_in_a_colour_is_light():
    assert web_colour("LT GREY") == "Lt Grey"
    assert web_colour("DK NAVY") == "Dk Navy"


def test_teaching_pairs_lines_by_colour_name_or_item_code():
    rows = [{"Item No.": "HP0127001", "Color": c, "Size": "onesize", "Size 1": "XS"}
            for c in ("COYOTE BROWN", "NORWEIGIAN FOREST")]
    finished = [
        # SS27: the barcode is style + colour name + size.
        {"U_CodeBars": "HP0127001NORWEIGIANFORESTXS", "U_SizeCode": "XS",
         "U_SCode": "HP0127001", "U_SuppCatNum": "HP0127001-NORWEIGIAN FOREST"},
        # FW26: Flender's own colour number in the barcode; the item code says it.
        {"U_CodeBars": "HP0127001C02XS", "U_SizeCode": "XS",
         "U_SCode": "HP0127001", "U_SuppCatNum": "HP0127001-NORWEIGIAN FOREST"},
    ]
    assert [src["Color"] for _f, src in _join(finished, rows)] == [
        "NORWEIGIAN FOREST", "NORWEIGIAN FOREST"]


def test_teaching_clears_a_column_rule_the_finished_sheet_does_not_use():
    from app.core.learn_from_sheet import learn_columns
    pairs = [({"U_Webbeschreibung2": "Trace LT Jacket", "U_Material": ""},
              {"Item Description": "TRACE LT JACKET",
               "Item Description 2": "TRACE LT TECHNICAL JACKET",
               "Material": "Main fabric: water-repellent nylon"})] * 4
    learned = learn_columns(pairs)
    # U_Material is left empty, and the second description is not the one used.
    assert learned["Material"] == "-"
    assert learned["Item Description 2"] == "-"


# ── Arte: order grid + article export, and the seasons around it ─────────────

def test_an_article_export_is_the_product_list():
    assert intake.classify_attachment("SS27 Article Export.xlsx") == intake.PRODUCT_LIST


def test_an_empty_order_grid_takes_the_sizes_the_article_is_made_in():
    from app.core.combine import combine
    from app.core.size_expand import expand_rows
    headers = ["Item No.", "Color", "XS / 28", "S / 30", "One Size"]
    order, _note = expand_rows(
        [{"Item No.": "SS27-102SHO", "Color": "Dark Denim"},
         {"Item No.": "SS27-004T", "Color": "White"}], headers, has_size_field=False)
    listed = [{"Item No.": "SS27-102SHO", "Color": "Dark Denim", "Size": s, "Barcode": b}
              for s, b in (("28", "5400892256076"), ("30", "5400892256083"))]
    listed += [{"Item No.": "SS27-004T", "Color": "White", "Size": "XS", "Barcode": "5400892251439"}]
    rows, report = combine(order, listed)
    sizes = sorted((r["Item No."], r["Size"]) for r in rows)
    # Waist 28/30 from the 'XS / 28' columns; One Size and the T-shirt's S
    # are not made, and nothing was ordered in them.
    assert sizes == [("SS27-004T", "XS"), ("SS27-102SHO", "28"), ("SS27-102SHO", "30")]
    assert report["grid_sizes_not_made"] == 3


def test_a_season_prefix_is_not_a_style():
    from app.core.combine import _style_keys
    assert _style_keys({"Item No.": "SS27-297AC.C"}) == ["SS27297ACC"]
    assert _style_keys({"Item No.": "JK00525-CAMO"}) == ["JK00525CAMO", "JK00525"]


def test_a_colour_number_written_before_the_colour_is_split_off():
    mapping = {"Colour Code": {"header": ""}, "Color": {"header": "Color"}}
    rows = [{"Color": c} for c in ("450 Navy", "1 Black", "602 Bordeaux", "100 White",
                                   "300 Green", "487")]
    pkg._colour_code_in_name(rows, mapping)
    assert [(r.get("Colour Code"), r["Color"]) for r in rows][:3] == [
        ("450", "Navy"), ("1", "Black"), ("602", "Bordeaux")]
    # A number alone is left for a person: the colour's name is not there.
    assert rows[-1] == {"Color": "487"}


def test_the_group_column_is_found_under_this_seasons_name():
    rows = [{"Sub item group": "Jacket", "Item Group": "Outerwear"}]
    assert pkg._group_column(rows, "Sub Category") == "Sub item group"
    assert pkg._group_column(rows, "") == ""


def test_a_real_word_is_not_corrected_without_its_neighbour():
    names = ["Beach Print T'shirt", "Bleach Wash Hoodie", "Bleach Denim Jacket",
             "Bleach Wash Shorts", "Arte Logo Cap", "Heart Tee"]
    rows = [{"Item Description": n} for n in names]
    pkg._name_typos(rows)
    assert rows[0]["Item Description"] == "Beach Print T'shirt"


def test_sizes_across_the_row_are_counted_as_skus(monkeypatch, test_app):
    """NuORDER lays sizes out as Size 1…Size n: one line each, as in the TEMP."""
    import importlib
    from types import SimpleNamespace

    intake_routes = importlib.import_module("app.routers.intake_routes")
    pkg = importlib.import_module("app.services.packages")
    rows = [{"Item No.": "HP0127001", "Color": "COYOTE BROWN",
             "Size 1": "S", "Size 2": "M", "Size 3": "L"},
            {"Item No.": "HP0127002", "Color": "BLACK", "Size": "One Size"}]
    monkeypatch.setattr(pkg, "collection_rows", lambda job, kind, rules: (rows, None, {}))
    job = SimpleNamespace(total_styles=0, total_skus=0)
    report = {"warnings": []}
    intake_routes._recount(job, report, {})
    assert job.total_skus == 4
    assert "4 lines without a barcode" in report["warnings"]

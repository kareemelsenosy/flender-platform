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

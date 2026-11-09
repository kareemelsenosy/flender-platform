"""Another Cotton Lab SS27, checked against Flender's finished TEMP sheet.

ACL sends a German master-data file (Stammdaten): Modell (HS code, origin,
gender per article), Colors (colour names, material) and SKU & Barcodes (one
row per size), joined on the Factory Article No. The article number is style
+ colour code (ACL-271-SP-699-001) and SAP keeps it as the item code; women's
pieces say WMN in the name; 2XL is XXL in SAP. The supplier's typing slips
(a mistyped article number, a size written twice, a letter missing from a
name, one number for two products) are put right and shown for checking.
"""
from __future__ import annotations

import pandas as pd

from app.core.learn_from_sheet import learn_columns, learn_settings
from app.core.sheet_layout import load_supplier_table
from app.core.temp_template import build_temp_sheet
from app.services import packages as pkg


def _stammdaten(path):
    modell = pd.DataFrame({
        "Factory Article No": ["ACL-271-SP-699-001", "ACL-271-SP-699-016",
                               "ACL-261-TS-745-001", "ACL-271-TS-750-002",
                               "ACL-271-TS-750-002"],
        "Item description": ["Another Wide Fit Sweatpant", "Another Wide Fit Sweatpant",
                             "Another Pedal Society T-Shirt", "Another Country Club T-Shirt",
                             "Another Country Club Raglan Longsleeve"],
        "Country of Origin": ["CN"] * 5,
        "Brand": ["Another Cotton Lab"] * 5,
        "Commodity Code": ["61102091000", "61102091000", "61091000100",
                           "61091000100", "61091000101"],
        "Gender Id": ["Unisex"] * 5,
    })
    colors = pd.DataFrame({
        "Factory Article No": ["ACL-271-SP-699-001", "ACL-271-SP-699-016",
                               "ACL-271-TS-745-003", "ACL-271-TS-750-002",
                               "ACL-271-TS-750-002"],
        "Item description": list(modell["Item description"]),
        "Factory Colour Code": ["1", "16", "3", "2", "2"],
        "Internal Statistic Colour": ["Black", "Heather Grey", "Dark Grey",
                                      "Off White", "Off White"],
        "Material in %": ["52% Cotton 48% Polyester"] * 2 + ["100% Cotton"] * 3,
    })
    skus = []
    for art, name in zip(colors["Factory Article No"], colors["Item description"]):
        for size in ("XS", "S", "M"):
            skus.append({"Factory Article No": art, "Item description": name,
                         "Size": size, "Barcode": str(4255773390000 + len(skus))})
    with pd.ExcelWriter(path) as xw:
        modell.to_excel(xw, sheet_name="Modell", index=False)
        colors.to_excel(xw, sheet_name="Colors", index=False)
        pd.DataFrame(skus).to_excel(xw, sheet_name="SKU & Barcodes", index=False)
    return path


# ── Reading the master data ──────────────────────────────────────────────────

def test_the_master_data_tabs_are_joined_onto_every_size(tmp_path):
    table, info = load_supplier_table(str(_stammdaten(tmp_path / "s.xlsx")))
    assert info["sheets_used"] == ["SKU & Barcodes"]
    assert set(info["joined_sheets"]) == {"Modell", "Colors"}
    assert len(table) == 15
    first = table.iloc[0]
    assert first["Commodity Code"] == "61102091000"
    assert first["Internal Statistic Colour"] == "Black"
    assert first["Brand"] == "Another Cotton Lab"
    # Each tab's own name for the product is kept, for teaching to choose.
    assert "Item description (Modell)" in table.columns


def test_a_mistyped_article_number_is_the_one_on_the_same_row_of_the_other_tab(tmp_path):
    table, info = load_supplier_table(str(_stammdaten(tmp_path / "s.xlsx")))
    assert {"sheet": "Modell", "row": 4, "wrote": "ACL-261-TS-745-001",
            "used": "ACL-271-TS-745-003"} in info["key_fixes"]
    row = table[table["Factory Article No"] == "ACL-271-TS-745-003"].iloc[0]
    assert row["Commodity Code"] == "61091000100"


def test_two_products_under_one_number_each_get_their_own_details(tmp_path):
    table, _info = load_supplier_table(str(_stammdaten(tmp_path / "s.xlsx")))
    shared = table[table["Factory Article No"] == "ACL-271-TS-750-002"]
    by_name = dict(zip(shared["Item description"], shared["Commodity Code"]))
    assert by_name["Another Country Club Raglan Longsleeve"] == "61091000101"
    assert by_name["Another Country Club T-Shirt"] == "61091000100"


def test_a_workbook_whose_tabs_share_nothing_is_read_as_before(tmp_path):
    path = tmp_path / "plain.xlsx"
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame({"Style": ["A1", "A2", "A3", "A4"], "Color": ["Black"] * 4,
                      "Size": ["S"] * 4}).to_excel(xw, sheet_name="Order", index=False)
        pd.DataFrame({"Note": ["x", "y", "z", "w"]}).to_excel(xw, sheet_name="Info", index=False)
    table, info = load_supplier_table(str(path))
    assert info["joined_sheets"] == [] and list(table.columns[:3]) == ["Style", "Color", "Size"]


# ── Style, item code, sizes, gender ──────────────────────────────────────────

class _Job:
    brand = "Another Cotton Lab"


def test_the_colour_code_comes_off_the_end_of_the_article_number():
    rows = [{"Item No.": "ACL-271-SP-699-001", "Colour Code": "1", "Color": "Black"},
            {"Item No.": "ACL-271-JA-382-074", "Colour Code": "74", "Color": "Khaki"},
            {"Item No.": "ACL-271-TS-700", "Colour Code": "19", "Color": "Navy"}]
    out = pkg.fill_from_collection(rows, _Job(), {"item_code": "supplier"})
    assert [r["Item No."] for r in out] == ["ACL-271-SP-699", "ACL-271-JA-382", "ACL-271-TS-700"]
    # SAP keeps the supplier's own number as the item code.
    assert out[0]["Supplier Item Code"] == "ACL-271-SP-699-001"


def test_learned_size_spellings_and_gender_words_reach_the_temp():
    rows = pkg.fill_from_collection(
        [{"Item No.": "ACL-271-LS-743-001", "Colour Code": "1", "Color": "Black",
          "Item Description": "Another WMN Signature Longsleeve", "Sex": "Unisex",
          "Size": "2XL", "Barcode": "4255925723657"}],
        _Job(), {"item_code": "supplier", "size_map": {"2XL": "XXL"}})
    out = build_temp_sheet(rows, brand="Another Cotton Lab", season="SS27",
                           earliest_ship_date="2027-01-01", gender_map={"unisex": "M"},
                           gender_words={"WMN": "F"})
    row = out["rows"][0]
    assert row["U_SizeCode"] == "XXL" and row["U_Gender"] == "F"
    assert row["U_SuppCatNum"] == "ACL-271-LS-743-001" and row["U_SCode"] == "ACL-271-LS-743"
    assert row["U_CodeBars"] == "4255925723657"


def test_one_order_line_per_style_colour_however_many_size_rows():
    rows = [{"Item No.": "ACL-271-SP-699", "Original Item No.": "ACL-271-SP-699-001",
             "Color": "Black", "Size": s, "Barcode": f"42557733902{i}0"}
            for i, s in enumerate(("XS", "S"))]
    rows += [{"Item No.": "ACL-271-SP-699", "Original Item No.": "ACL-271-SP-699-016",
              "Color": "Heather Grey", "Size": "XS", "Barcode": "4255773390292"}]
    out = build_temp_sheet(rows, brand="Another Cotton Lab", season="SS27")
    assert [r["U_OSRow"] for r in out["rows"]] == ["1", "1", "2"]


def test_acl_habits_are_learned_from_the_finished_sheet():
    pairs = []
    for i, (name, sex, gender, size, target) in enumerate([
            ("Another WMN Stars Top", "Unisex", "F", "2XL", "XXL"),
            ("Another WMN Longsleeve", "Unisex", "F", "2XL", "XXL"),
            ("Another Wide Fit T-Shirt", "Unisex", "M", "2XL", "XXL"),
            ("Another Essential Jeans", "Men", "M", "S", "S")]):
        art = f"ACL-271-TS-70{i}-001"
        pairs.append(({"U_SuppCatNum": art, "U_SCode": art[:-4], "U_Gender": gender,
                       "U_SizeCode": target},
                      {"Original Item No.": art, "Item No.": art[:-4], "Sex": sex,
                       "Item Description": name, "Size": size}))
    learned = learn_settings(pairs)
    assert learned["item_code"] == "supplier"
    assert learned["gender_words"] == {"WMN": "F"}
    assert learned["size_map"] == {"2XL": "XXL"}


def test_teaching_finds_the_tab_the_names_were_taken_from():
    pairs = [({"U_Webbeschreibung2": good},
              {"Item Description": sku, "Item description (Modell)": good,
               "__columns": {"Item Description": "Item description"},
               "Item description": sku})
             for good, sku in [("Ride A Lot T-Shirt", "Rida A Lot T-Shirt"),
                               ("Cycling Dept. Cap", "Another Logo Cap")]
             + [("Another Cap", "Another Cap")] * 20]
    assert learn_columns(pairs) == {"Item Description": "Item description (Modell)"}


# ── The supplier's slips ─────────────────────────────────────────────────────

def _line(art, name, size="M"):
    return {"Item No.": art.rsplit("-", 1)[0], "Original Item No.": art,
            "Supplier Item Code": art, "Item Description": name, "Size": size,
            "Colour Code": art.rsplit("-", 1)[1]}


def test_one_article_number_for_two_products_takes_the_brands_letters():
    rows = [_line("ACL-271-TS-750-002", "Another Country Club T-Shirt"),
            _line("ACL-271-TS-750-002", "Another Country Club Raglan Longsleeve"),
            _line("ACL-271-LS-720-001", "Another Stripe Longsleeve"),
            _line("ACL-271-LS-743-001", "Another WMN Signature Longsleeve"),
            _line("ACL-271-TS-699-001", "Another Wide Fit T-Shirt"),
            _line("ACL-271-HO-750-002", "Another Country Club Hoodie")]
    pkg._shared_article_numbers(rows)
    assert rows[0]["Supplier Item Code"] == "ACL-271-TS-750-002"
    assert rows[1]["Supplier Item Code"] == "ACL-271-LS-750-002"
    assert rows[1]["Item No."] == "ACL-271-LS-750"
    assert pkg.notes_as_exceptions(rows)[0]["field"] == "U_SuppCatNum"


def test_one_number_for_two_products_with_nothing_to_go_on_is_asked():
    rows = [_line("ACL-271-TS-750-002", "Another Country Club T-Shirt"),
            _line("ACL-271-TS-750-002", "Another Country Club Bucket Hat")]
    pkg._shared_article_numbers(rows)
    assert [e["severity"] for e in pkg.notes_as_exceptions(rows)] == ["critical"]


def test_a_size_written_twice_where_the_run_skips_one():
    rows = [_line("ACL-271-SP-699-019", "Another Wide Fit Sweatpant", s)
            for s in ("XS", "S", "s", "L", "XL", "2XL")]
    pkg._size_typos(rows)
    assert [r["Size"] for r in rows] == ["XS", "S", "M", "L", "XL", "2XL"]
    assert rows[2]["Supplier Size"] == "s"
    # A run with a real gap is not touched.
    gap = [_line("ACL-271-SP-699-001", "Another Wide Fit Sweatpant", s)
           for s in ("XS", "S", "L")]
    pkg._size_typos(gap)
    assert [r["Size"] for r in gap] == ["XS", "S", "L"]


def test_a_letter_missing_from_a_name_is_put_right_from_the_brands_other_names():
    names = ["Another Wide Ft Camo Jorts", "Another Wide Fit Sweatpant",
             "Another Wide Fit T-Shirt", "Rid A Lot Hoodie", "Ride A Lot T-Shirt",
             "Slow Sunday Longsleve", "Another Stripe Longsleeve",
             "Another WMN Longsleeve", "Another Signature Longsleeve",
             "Another Logo Short", "Another Cargo Shorts"]
    rows = [{"Item Description": n} for n in names]
    pkg._name_typos(rows)
    fixed = [r["Item Description"] for r in rows]
    assert fixed[0] == "Another Wide Fit Camo Jorts"
    assert fixed[3] == "Ride A Lot Hoodie"
    assert fixed[5] == "Slow Sunday Longsleeve"
    # A singular is not a typo of its plural.
    assert fixed[9] == "Another Logo Short"


def test_a_product_word_never_taught_takes_its_group_from_the_name():
    out = build_temp_sheet(
        [{"Item No.": "ACL-271-DJ-706", "Color": "Denim Blue", "Size": "M",
          "Item Description": "Another Essential Jeans"}],
        brand="Another Cotton Lab", season="SS27", group_by_word=True,
        group_map={"SWEATPANT": ["PANTS"], "HOODIE": ["HOODYS"]})
    assert out["rows"][0]["U_ItmsGrpCod"] == "PANTS"
    assert any(e["field"] == "U_ItmsGrpCod" and e["severity"] == "warning"
               for e in out["exceptions"])

"""Dime SU26 and Komono Q1 2027, checked against their finished TEMP sheets.

Dime: the order form is grouped under section headings (JACKETS, TOPS) with
the header repeated per section and each section's own sizes; style codes end
with the colour code; the SAP item group follows the product word that ends
the name. Komono: no colour column (colour = description less style name),
the "Size" column is the frame measurement, colours keep their slash, HS codes
are cut to 8 digits.
"""
from __future__ import annotations

from app.core.combine import combine
from app.core.learn_from_sheet import learn_item_groups, learn_settings
from app.core.sap_creation import item_colour_code
from app.core.temp_template import (
    barcode, build_temp_sheet, country_code, gender_code, group_from_name,
    is_blank, product_words, web_colour,
)
from app.services import packages as pkg


def _mapping(**headers):
    fields = ("Item No.", "Item Description", "Color", "Item Group", "Duty Position",
              "Colour Code", "Size", "Barcode")
    return {f: {"header": headers.get(f, "")} for f in fields}


# ── Reading the files ────────────────────────────────────────────────────────

def test_natural_colour_code_nat_is_not_a_missing_value():
    assert not is_blank("NAT")
    assert is_blank("NaT") and is_blank("nan") and is_blank("")


def test_dime_sections_become_the_category_and_carry_their_own_sizes():
    headers = ["Style", "Description", "Color", "XXS", "XS", "S"]
    rows = [
        {"Item No.": "JACKETS"},
        {"Item No.": "DIMESU262CHA", "Item Description": "Houndstooth Jacket", "Color": "Charcoal"},
        {"Item No.": "Style", "XXS": "7", "XS": "7.125", "S": "8.25\""},
        {"Item No.": "HEADWEAR"},
        {"Item No.": "DIMESU2626NVY", "Item Description": "Classic New Era Cap", "Color": "Navy"},
    ]
    out = pkg._sections(rows, headers, "", _mapping(**{"Item No.": "Style"}))
    assert [r["Item No."] for r in out] == ["DIMESU262CHA", "DIMESU2626NVY"]
    assert out[0]["Item Group"] == "Jackets"
    assert out[1]["Item Group"] == "Headwear"
    # Caps in 7.125, decks in 8.25" — the inch mark is not part of the size.
    assert out[1]["__size_columns"] == {"XXS": "7", "XS": "7.125", "S": "8.25"}


def test_dime_style_codes_lose_the_colour_code_on_the_end():
    assert pkg._colour_code_suffix("DIMESU262CHA", "Electric Charcoal") == "CHA"
    assert pkg._colour_code_suffix("DIMESU2644GFOG", "Fog") == "FOG"
    assert pkg._colour_code_suffix("DIMESU2656GRP", "Charcoal Grape") == "GRP"
    assert pkg._colour_code_suffix("DIMESP2634", "Glory") == ""


def test_an_order_code_the_product_list_spells_differently_still_joins():
    # Dime's order form: DIMESU2656GRP; its COO list: DIMESU2656GRA.
    order = [{"Item No.": "DIMESU2656GRP", "Color": "Charcoal Grape", "Size": "M"}]
    listed = [{"Item No.": "DIMESU2656GRA", "Color": "Charcoal Grape",
               "Country of Origin": "China", "Duty Position": "6109.10.00"}]
    rows, report = combine(order, listed)
    assert rows[0]["Country of Origin"] == "China"
    assert rows[0]["Item No."] == "DIMESU2656GRP"
    assert not report["only_in_order"]


def test_komono_colour_is_the_description_less_the_style_name():
    assert pkg._strip_name("Sora Bronze", "Sora") == ("Bronze", True)
    assert pkg._strip_name("Francis Matte Black/Tortoise", "Francis") == ("Matte Black/Tortoise", True)
    assert pkg._strip_name("The Glitch Atlantic", "Glitch") == ("The Atlantic", True)


def test_a_collab_name_is_what_its_colourways_share():
    headers = ["Item No.", "Item Description", "Style Name"]
    rows = [{"Item No.": f"KOM-X7{i}", "Item Description": f"Komono X Gabrielle Szwarcenberg {c}",
             "Style Name": "Gabrielle"} for i, c in enumerate(["White Gold", "Silver", "Gun"])]
    mapping = _mapping(**{"Item Description": "Item Description"})
    pkg._colour_from_description(rows, headers, mapping)
    assert [r["Color"] for r in rows] == ["White Gold", "Silver", "Gun"]
    assert {r["Item Description"] for r in rows} == {"Komono X Gabrielle Szwarcenberg"}
    assert mapping["_colour_unsure"] == []


# ── Building the TEMP ────────────────────────────────────────────────────────

def test_the_product_word_decides_the_item_group():
    assert product_words("Classic Veins LS T-Shirt") == ["LS T-SHIRT", "T-SHIRT"]
    assert product_words("Much Hoodie (HW)") == ["MUCH HOODIE", "HOODIE"]
    # A T-shirt is not a shirt; a long-sleeve T-shirt is LONGSL.
    assert group_from_name("Arch Ringer T-Shirt", ["SHIRTS", "POLOS", "T-SHIRTS"]) == "T-SHIRTS"
    assert group_from_name("Classic Veins LS T-Shirt", ["T-SHIRTS", "LONGSL"]) == "LONGSL"
    groups = {"JACKET": ["JACKETS"], "T-SHIRT": ["T-SHIRTS"], "LS T-SHIRT": ["LONGSL"],
              "T-SHIRTS": ["T-SHIRTS"]}
    rows = [{"Item No.": "DIMESU2690", "Color": "Black", "Item Description": n,
             "Item Group": "T-Shirts", "Size": "M"}
            for n in ("Classic Veins LS T-Shirt", "Houndstooth Jacket")]
    out = build_temp_sheet(rows, brand="Dime", season="SU26", group_map=groups,
                           group_by_word=True)
    assert [r["U_ItmsGrpCod"] for r in out["rows"]] == ["LONGSL", "JACKETS"]


def test_built_barcodes_use_the_size_the_brand_uses():
    assert barcode("DIMESU2625", "BLU", "OS", supplier_size=True) == "DIMESU2625BLUOS"
    assert barcode("DIMESU2626", "NVY", "7.125", supplier_size=True) == "DIMESU2626NVY7125"
    # Hiking Patrol's SAP spells the one-size out.
    assert barcode("HP0127302", "BLACK", "OS") == "HP0127302BLACKONESIZE"


def test_komono_keeps_the_slash_in_colours_where_others_write_a_dash():
    assert web_colour("Matte Black/Tortoise", keep_slash=True) == "Matte Black/Tortoise"
    assert web_colour("BLACK/WHITE") == "Black-White"
    assert item_colour_code("KOM-S2257", "", "Matte Black/Tortoise", False,
                            keep_slash=True) == "KOM-S2257-MATTE BLACK/TORTOISE"
    assert item_colour_code("AC01312", "", "Black/White", False) == "AC01312-BLACK-WHITE"


def test_gender_spelling_and_countries():
    komono = {"ladies": "W", "unisex": "U", "men": "M", "": "U"}
    assert gender_code("Ladies", komono) == "W"
    assert gender_code("", komono) == "U"
    assert gender_code("Unisex") == "U"
    assert country_code("Canada") == "CA"
    assert country_code("China") == "CN"


# ── Learning from the finished sheet ─────────────────────────────────────────

def test_dime_item_groups_are_learned_from_the_product_word():
    names = [("Houndstooth Jacket", "Apparel", "JACKETS"), ("Plaid Shirt", "Tops", "SHIRTS"),
             ("Cable Knit Polo", "Tops", "POLOS"), ("Arch T-Shirt", "Tops", "T-SHIRTS"),
             ("Soulja T-Shirt", "T-Shirts", "T-SHIRTS"), ("Mural T-Shirt", "T-Shirts", "T-SHIRTS"),
             ("Veins LS T-Shirt", "T-Shirts", "LONGSL"), ("Much Hoodie", "Fleece", "HOODYS"),
             ("Logo Hoodie", "Fleece", "HOODYS"), ("Logo Crewneck", "Fleece", "SWEATER"),
             ("Denim Pants", "Bottoms", "PANTS"), ("Chino Pants", "Bottoms", "PANTS"),
             ("Denim Shorts", "Bottoms", "SHORTS")]
    pairs = []
    for i, (name, section, group) in enumerate(names):
        for size in ("S", "M", "L"):
            pairs.append(({"U_ItmsGrpCod": group, "U_SCode": f"D{i}"},
                          {"Item Description": name, "Item Group": section, "Size": size}))
    learned = learn_item_groups(pairs)
    assert learned["by_product_word"] is True
    assert learned["map"]["JACKET"] == ["JACKETS"]
    assert learned["map"]["LS T-SHIRT"] == ["LONGSL"]
    assert learned["map"]["T-SHIRT"][0] == "T-SHIRTS"


def test_equally_clean_columns_prefer_the_more_general_one():
    pairs = []
    for i in range(30):
        pairs.append(({"U_ItmsGrpCod": "SUNGLASS", "U_SCode": f"KOM-{i}"},
                      {"Item Group": "Sunglasses - Originals" if i % 2 else "Sunglasses - Next",
                       "Item Description": f"Style{i % 10}"}))
    assert learn_item_groups(pairs)["column"] == "Item Group"


def test_komono_habits_are_learned_as_settings():
    pairs = []
    for i, (gender, sap) in enumerate([("Ladies", "W"), ("Unisex", "U"), ("", "U"), ("Men", "M")]):
        pairs.append(({"U_Gender": sap, "U_HS_Code": "90041091", "U_SCode": f"KOM-S{i}",
                       "U_SizeCode": "onesize", "U_CodeBars": f"540403846156{i}",
                       "U_Web_Color": "Matte Black/Tortoise"},
                      {"Sex": gender, "Duty Position": "9004109100", "Item No.": f"KOM-S{i}",
                       "Size": "61☐14-140", "Color": "Matte Black/Tortoise"}))
    learned = learn_settings(pairs)
    assert learned["gender_map"] == {"ladies": "W", "unisex": "U", "": "U", "men": "M"}
    assert learned["hs_digits"] == 8
    assert learned["one_size"] is True
    assert learned["colour_slash"] == "keep"


def test_dime_habits_are_learned_as_settings():
    pairs = [({"U_SCode": "DIMESU2625", "U_CodeBars": "DIMESU2625BLUOS", "U_SizeCode": "Onesize"},
              {"Item No.": "DIMESU2625", "Original Item No.": "DIMESU2625BLU", "Size": "OS"}),
             ({"U_SCode": "DIMESU262", "U_CodeBars": "DIMESU262CHAXS", "U_SizeCode": "XS"},
              {"Item No.": "DIMESU262", "Original Item No.": "DIMESU262CHA", "Size": "XS"})]
    learned = learn_settings(pairs)
    assert learned["style_has_colour_code"] is True
    assert learned["barcode_size"] == "supplier"
    assert "one_size" not in learned

from __future__ import annotations

import openpyxl

from app.core.attribute_engine import build_upload_workbook, parse_sap_products
from app.core.attribute_taxonomy import master_for_item_group


def _make_sap_export(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Code", "Web Description 2", "Item Group", "Material", "Style Code", "Gender"])
    # Two size rows for one style (must dedupe) + a second style
    ws.append(["TNT A 1 BLACK M", "Logo Tee", "T-SHIRTS", "Cotton 100%", "TN001", "Men"])
    ws.append(["TNT A 1 BLACK L", "Logo Tee", "T-SHIRTS", "Cotton 100%", "TN001", "Men"])
    ws.append(["TNT P 2 NAVY M", "Relaxed Jeans", "PANTS", "Cotton 100%", "TN002", "Men"])
    wb.save(path)


def test_master_for_item_group_rolls_up_subgroups():
    assert master_for_item_group("HOODYS") == "SWEATS"
    assert master_for_item_group("HEAD") == "ACCS"
    assert master_for_item_group("LONGSL") == "T-SHIRTS"
    assert master_for_item_group("PANTS") == "PANTS"
    assert master_for_item_group("") == "ACCS"  # safe default


def test_parse_sap_products_dedupes_and_maps(tmp_path):
    p = tmp_path / "export.xlsx"
    _make_sap_export(p)
    styles, meta = parse_sap_products(str(p))
    assert {s["style_code"] for s in styles} == {"TN001", "TN002"}  # deduped
    by_code = {s["style_code"]: s for s in styles}
    assert by_code["TN001"]["master_group"] == "T-SHIRTS"
    assert by_code["TN001"]["name"] == "Logo Tee"
    assert by_code["TN002"]["master_group"] == "PANTS"
    assert "style_code" in meta["columns_found"]


def test_build_upload_workbook_drops_review_and_emits_sap_rows(tmp_path):
    results = [
        {"style_code": "TN001", "master_group": "T-SHIRTS", "product_type": "TSHIRT",
         "needs_review": False, "FABRIC": None, "FIT": "Loose", "WEIGHT": "Light",
         "STYLE": ["Street"]},
        {"style_code": "TN002", "master_group": "PANTS", "product_type": None,
         "needs_review": True, "FABRIC": None, "FIT": None, "WEIGHT": None, "STYLE": []},
    ]
    out = tmp_path / "upload.xlsx"
    summary = build_upload_workbook(results, str(out))
    assert summary == {"clean_styles": 1, "rows": 4, "review_styles": 1}

    wb = openpyxl.load_workbook(out)
    ws = wb["Sheet1"]
    rows = [tuple(ws.cell(r, c).value for c in range(1, 5)) for r in range(2, ws.max_row + 1)]
    assert ("TN001", "T-SHIRTS", "TSHIRT", "Y") in rows       # product type Boolean
    assert ("TN001", "T-SHIRTS", "FIT", "Loose") in rows      # valued attribute
    assert ("TN001", "T-SHIRTS", "STYLE", "Street") in rows
    assert all(r[0] != "TN002" for r in rows)                 # review style excluded


def test_products_page_requires_login_and_renders(client, login_as):
    # Unauthenticated -> redirected to login
    anon = client.get("/products", follow_redirects=False)
    assert anon.status_code in (302, 307)

    login_as()
    page = client.get("/products")
    assert page.status_code == 200
    assert "Product Attributes" in page.text

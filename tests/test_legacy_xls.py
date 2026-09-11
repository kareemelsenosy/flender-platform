"""Legacy .xls (BIFF) workbooks.

Suppliers still send these — Hamid's Liberaiders stock list arrived as one —
and until now the parser opened every Excel file with openpyxl, which cannot
read the binary format and fails with "File contains no valid workbook part".
The upload was accepted; the mapping step then showed that message and 0 rows.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.core.parser import FileParser, UnreadableWorkbook, _excel_engine

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_stock_list.xls"


def test_engine_is_chosen_by_content_not_extension(tmp_path):
    ole2 = tmp_path / "a.xls"
    ole2.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    assert _excel_engine(str(ole2)) == "xlrd"

    zipped = tmp_path / "b.xls"          # a real .xlsx wearing a .xls name
    zipped.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    assert _excel_engine(str(zipped)) == "openpyxl"


def test_a_genuine_legacy_xls_parses():
    parser = FileParser()
    assert parser.get_sheet_names(str(FIXTURE)) == ["stock list"]
    rows, unique, headers = parser.parse(str(FIXTURE))
    assert "Item Code" in headers
    assert {r["item_code"] for r in rows} == {"LIB-001", "LIB-002"}
    # Horizontal S/M/L columns expand into one row per size with stock.
    assert any(r.get("size") == "M" for r in rows)


def test_an_xlsx_mislabelled_as_xls_still_opens(tmp_path):
    """Suppliers rename files; the bytes decide, not the name."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item Code", "Color Name", "Size"])
    ws.append(["X-1", "Black", "M"])
    path = tmp_path / "renamed.xls"
    wb.save(path)
    rows, _, _ = FileParser().parse(str(path))
    assert rows and rows[0]["item_code"] == "X-1"


def test_garbage_gets_an_error_a_person_can_act_on(tmp_path):
    junk = tmp_path / "broken.xlsx"
    junk.write_bytes(b"this is not a spreadsheet at all")
    with pytest.raises(UnreadableWorkbook) as exc:
        FileParser().parse(str(junk))
    msg = str(exc.value)
    assert "re-save it as .xlsx" in msg.lower() or "could not be opened" in msg.lower()
    assert "workbook part" not in msg          # never the openpyxl internals


# ── Column detection on size-run sheets ──────────────────────────────────────
# Found on the same Liberaiders file: single-letter size headers were being
# claimed as prices and codes, so every S/M/L row vanished from the order sheet.

def test_single_letter_size_headers_are_not_mistaken_for_fields():
    from app.core.parser import detect_columns
    m = detect_columns(["Item Code", "Item Name", "Color Name",
                        "Retail Price (HKD)", "S", "M", "L", "XL"])
    assert m["style_name"] == "Item Name"
    assert m["wholesale_price"] is None       # "S" is not "whs"
    assert m["sap_code"] is None              # "L" is not a code column
    assert "S" not in m.values() and "M" not in m.values() and "L" not in m.values()


def test_every_size_column_expands_including_s_m_l():
    rows, _, _ = FileParser().parse(str(FIXTURE))
    assert {r["size"] for r in rows} == {"S", "M", "L"}
    assert {r["item_code"] for r in rows} == {"LIB-001", "LIB-002"}


def test_a_totals_column_is_never_mapped_to_a_field():
    from app.core.parser import detect_columns
    m = detect_columns(["Item Code", "Color Name", "S", "M", "L", "Grand Total"])
    assert "Grand Total" not in m.values()

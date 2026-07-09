"""Reference-file enrichment — matching SAP styles inside catalog/lookbook
files (PDF or Excel) by Style Number and pulling out their richer copy."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.core.reference_ingest import (
    MAX_REF_CHARS,
    ReferenceIngestError,
    extract_reference_texts,
)

PDF_FIXTURE = Path(__file__).parent / "fixtures" / "sample_linesheet.pdf"


def _catalog_xlsx(path, rows, header=("Style", "Category", "Details")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def test_xlsx_reference_matches_by_style_code(tmp_path):
    p = tmp_path / "catalog.xlsx"
    _catalog_xlsx(p, [
        ["TN001", "Tees", "Heavy jersey, boxy fit, screen print"],
        ["TN999", "Pants", "Not in the SAP export"],
    ])
    texts, files = extract_reference_texts([str(p)], ["TN001", "TN002"])
    assert set(texts) == {"TN001"}
    # Row content arrives labelled with its column headers.
    assert "Category: Tees" in texts["TN001"]
    assert "Heavy jersey" in texts["TN001"]
    assert files == [{"name": "catalog.xlsx", "matched": 1}]


def test_xlsx_reference_matches_code_with_vendor_suffix(tmp_path):
    # Catalogs often print "I000147.4FCXX" where SAP has "I000147".
    p = tmp_path / "catalog.xlsx"
    _catalog_xlsx(p, [["I000147.4FCXX", "POS Divers", "30 Pack sticker"]])
    texts, _ = extract_reference_texts([str(p)], ["I000147"])
    assert "30 Pack sticker" in texts["I000147"]


def test_xlsx_reference_caps_per_style_text(tmp_path):
    p = tmp_path / "catalog.xlsx"
    _catalog_xlsx(p, [["TN001", "Tees", "x" * 5000]])
    texts, _ = extract_reference_texts([str(p)], ["TN001"])
    assert len(texts["TN001"]) <= MAX_REF_CHARS


def test_pdf_reference_matches_page_text():
    # The committed line-sheet fixture carries styles ACME-100..ACME-550.
    texts, files = extract_reference_texts([str(PDF_FIXTURE)], ["ACME-100", "NOPE-1"])
    assert "ACME-100" in texts
    assert texts["ACME-100"]  # page text captured
    assert "NOPE-1" not in texts
    assert files[0]["matched"] == 1


def test_unreadable_reference_raises_clear_error(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"this is not a workbook")
    with pytest.raises(ReferenceIngestError) as exc:
        extract_reference_texts([str(p)], ["TN001"])
    assert "broken.xlsx" in str(exc.value)

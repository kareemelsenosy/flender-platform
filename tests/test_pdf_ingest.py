"""Multi-source Step 2 — PDF line-sheet ingest.

Extraction runs against a committed fixture (tests/fixtures/sample_linesheet.pdf,
a 2-page ruled table). The upload test drives the real /upload/file endpoint to
prove a PDF flows into the same mapping pipeline as Excel.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.pdf_ingest import extract_product_table, pdf_to_xlsx, PdfIngestError

FIXTURE = Path(__file__).parent / "fixtures" / "sample_linesheet.pdf"
PROSE_FIXTURE = Path(__file__).parent / "fixtures" / "prose_no_table.pdf"


def test_extract_stitches_multipage_table():
    table = extract_product_table(str(FIXTURE))
    header, data = table[0], table[1:]
    assert header[:4] == ["Style Code", "Description", "Colour", "Size"]
    assert "Barcode" in header and "WHS Price" in header
    # 6 rows on page 1 + 3 on page 2, repeated header row on page 2 dropped.
    assert len(data) == 9
    assert data[0][0] == "ACME-100"
    assert data[-1][0] == "ACME-550"
    # The header must not reappear inside the data.
    assert header not in data


def test_pdf_to_xlsx_writes_parseable_sheet_and_summary(tmp_path):
    out = tmp_path / "converted.xlsx"
    summary = pdf_to_xlsx(str(FIXTURE), str(out))
    assert out.exists()
    assert summary["n_lines"] == 9
    assert summary["n_styles"] == 5      # 5 style+colour master records
    assert summary["n_duplicates_removed"] == 0

    # The converted sheet must round-trip through the normal spreadsheet parser.
    from app.core.parser import FileParser
    rows, unique_items, headers = FileParser().parse(str(out))
    assert "Style Code" in headers
    assert len(rows) == 9
    assert len(unique_items) == 5


def test_non_table_pdf_raises_clear_error():
    # A PDF with prose but no table -> clear, actionable error.
    with pytest.raises(PdfIngestError) as exc:
        extract_product_table(str(PROSE_FIXTURE))
    assert "table" in str(exc.value).lower()


def test_pdf_upload_flows_into_mapping(client, login_as, test_app):
    """A PDF uploaded via /upload/file creates a mapping-ready session backed by
    the converted spreadsheet."""
    login_as()
    with open(FIXTURE, "rb") as fh:
        resp = client.post(
            "/upload/file",
            files={"file": ("acme_linesheet.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["mapping_url"].startswith("/mapping/")

    # Session recorded as a PDF import, pointing at the converted .xlsx.
    models = test_app["models"]
    db = test_app["database"].SessionLocal()
    try:
        sess = db.get(models.Session, body["session_id"])
        assert sess.source_type == "pdf_upload"
        uf = db.query(models.UploadedFile).filter_by(session_id=sess.id).one()
        assert uf.filename == "acme_linesheet.pdf"      # original name for display
        assert uf.file_path.endswith("__from_pdf.xlsx")  # parseable file
    finally:
        db.close()

    # The mapping page renders with the extracted columns.
    page = client.get(body["mapping_url"])
    assert page.status_code == 200
    assert "Style Code" in page.text


def test_pdf_upload_without_table_is_rejected_cleanly(client, login_as):
    login_as()
    with open(PROSE_FIXTURE, "rb") as fh:
        resp = client.post(
            "/upload/file",
            files={"file": ("cover.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 422
    assert "table" in resp.json()["error"].lower()

"""PDF ingest (multi-source Step 2).

Turn a supplier **PDF line sheet / catalogue** into the exact same tabular shape
we already handle for Excel and Google Sheets, so it flows through the existing
column-mapping → review → export pipeline unchanged.

Approach: pdfplumber pulls the product table(s) out of the PDF, we stitch
multi-page tables that share a header, clean the cells, drop repeated header
rows, and write the result to a `.xlsx`. From there the app treats it like any
other uploaded spreadsheet — the user confirms the column mapping and continues.

Scope (v1): PDFs whose products are laid out as a **table** (ruled or
well-aligned columns) — the common line-sheet format. Free-form/graphical
catalogues that carry no table are rejected with a clear message; AI-assisted
extraction for those is a later enhancement.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class PdfIngestError(Exception):
    """Raised when a PDF has no usable product table to import."""


def _clean_cell(value) -> str:
    """Normalise a pdfplumber cell: None -> '', collapse internal whitespace."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _clean_row(row: list) -> list[str]:
    return [_clean_cell(c) for c in row]


def _is_blank(row: list[str]) -> bool:
    return not any(c for c in row)


def extract_product_table(pdf_path: str) -> list[list[str]]:
    """Extract the product table from a PDF as a list of rows (row 0 = header).

    Stitches together tables across pages that share the header's column count,
    skips repeated header rows and blank rows. Raises ``PdfIngestError`` if no
    usable table is found.
    """
    import pdfplumber

    header: list[str] | None = None
    data: list[list[str]] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                rows = [_clean_row(r) for r in table if r is not None]
                rows = [r for r in rows if not _is_blank(r)]
                if not rows:
                    continue
                if header is None:
                    header = rows[0]
                    body = rows[1:]
                else:
                    # Only stitch tables with the same shape as the header; a
                    # table with a different column count is a different layout
                    # and would corrupt the mapping if merged in.
                    if len(rows[0]) != len(header):
                        continue
                    # A page that repeats the header starts its body one down.
                    body = rows[1:] if rows[0] == header else rows
                for r in body:
                    if r == header:  # stray repeated header mid-table
                        continue
                    # Pad/truncate to the header width so every row aligns.
                    if len(r) < len(header):
                        r = r + [""] * (len(header) - len(r))
                    elif len(r) > len(header):
                        r = r[: len(header)]
                    data.append(r)

    if header is None or not data:
        raise PdfIngestError(
            "No product table was found in this PDF. The importer currently "
            "supports line sheets laid out as a table (columns like Style Code, "
            "Colour, Size, Price, Qty). Please export the line sheet as Excel/CSV, "
            "or share a table-based PDF."
        )
    return [header] + data


def _summarize(header: list[str], data: list[list[str]]) -> dict:
    """Compute a human import summary using the app's own column detection +
    the product-identity module (Step 1): how many lines and distinct styles,
    and how many exact-duplicate lines were dropped."""
    from app.core.parser import detect_columns
    from app.core.product_identity import count_styles, dedupe_lines

    col_map = detect_columns(header)  # {standard_field: raw_header or None}
    idx = {h: i for i, h in enumerate(header)}
    row_dicts: list[dict] = []
    for r in data:
        d: dict = {}
        for std, raw in col_map.items():
            if raw and raw in idx and idx[raw] < len(r):
                d[std] = r[idx[raw]]
        row_dicts.append(d)

    deduped, removed = dedupe_lines(row_dicts)
    return {
        "n_lines": len(deduped),
        "n_styles": count_styles(deduped),
        "n_duplicates_removed": removed,
    }


def pdf_to_xlsx(pdf_path: str, xlsx_path: str) -> dict:
    """Extract a PDF line sheet's table and write it to ``xlsx_path`` so the
    existing spreadsheet pipeline can read it.

    Returns a summary dict: {n_lines, n_styles, n_duplicates_removed, headers}.
    """
    import openpyxl

    table = extract_product_table(pdf_path)
    header, data = table[0], table[1:]

    summary = _summarize(header, data)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Line Sheet"
    for row in table:
        ws.append(row)
    wb.save(xlsx_path)

    logger.info(
        "PDF ingest: %s -> %s (%d lines, %d styles, %d dup lines dropped)",
        pdf_path, xlsx_path, summary["n_lines"], summary["n_styles"],
        summary["n_duplicates_removed"],
    )
    summary["headers"] = header
    return summary

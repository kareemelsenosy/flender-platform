"""Reference-file enrichment for the Product Attributes tool.

The primary SAP export carries the Style Code list; the richer product copy
lives in **reference files** — PDF lookbooks/catalogues and supplementary
Excel sheets. This module locates each style inside those files (matched by
Style Number, which per Flender is present in every file they receive) and
returns the surrounding text, so the attribute AI can classify from real
catalog detail instead of a thin SAP description.

Matching is deliberately forgiving: codes are compared case-insensitively
with all punctuation/whitespace stripped, and a catalog cell/page matches if
it *contains* the style code (catalogs often print "I000147.4FCXX" where SAP
has "I000147").
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Per-style budget of reference text fed to the AI (keeps prompts + DB sane).
MAX_REF_CHARS = 1200
# Cap of text taken from a single PDF page (lookbook pages are mostly images
# with a short copy block; anything longer is boilerplate).
_MAX_PAGE_CHARS = 2000
# Codes shorter than this are too collision-prone for substring matching.
_MIN_CODE_LEN = 4


class ReferenceIngestError(Exception):
    """Raised when a reference file cannot be read at all."""


def _norm_code(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _append(texts: dict[str, str], code: str, extra: str) -> bool:
    """Add `extra` to a style's reference text within the per-style budget.
    Returns True if this is the first text captured for the style."""
    extra = _clean(extra)
    if not extra:
        return False
    first = code not in texts
    current = texts.get(code, "")
    if len(current) >= MAX_REF_CHARS:
        return False
    joined = (current + " | " + extra) if current else extra
    texts[code] = joined[:MAX_REF_CHARS]
    return first


def _xlsx_texts(path: str, codes_by_norm: dict[str, str], texts: dict[str, str]) -> int:
    """Scan every sheet of a reference Excel: a row belongs to a style when any
    cell contains its style code; the rest of the row (labelled with its
    header) becomes that style's reference text. Returns styles matched."""
    import openpyxl

    matched: set[str] = set()
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            header: list[str] | None = None
            for row in ws.iter_rows(values_only=True):
                cells = [_clean(c) for c in row]
                if not any(cells):
                    continue
                if header is None:
                    header = cells
                    continue
                hit = None
                for cell in cells:
                    n = _norm_code(cell)
                    if not n:
                        continue
                    if n in codes_by_norm:
                        hit = codes_by_norm[n]
                        break
                    for cn, code in codes_by_norm.items():
                        if len(cn) >= _MIN_CODE_LEN and cn in n:
                            hit = code
                            break
                    if hit:
                        break
                if not hit:
                    continue
                parts = []
                for i, cell in enumerate(cells):
                    if not cell:
                        continue
                    label = header[i] if i < len(header) and header[i] else ""
                    parts.append(f"{label}: {cell}" if label else cell)
                if _append(texts, hit, "; ".join(parts)):
                    matched.add(hit)
                elif hit in texts:
                    matched.add(hit)
    finally:
        wb.close()
    return len(matched)


def _pdf_texts(path: str, codes_by_norm: dict[str, str], texts: dict[str, str]) -> int:
    """Scan a PDF lookbook/catalogue page by page: every style code found on a
    page gets that page's text. Works for free-form catalog layouts, not just
    tables. Returns styles matched."""
    import pdfplumber

    matched: set[str] = set()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            if not text.strip():
                continue
            page_norm = _norm_code(text)
            page_hits = [code for cn, code in codes_by_norm.items()
                         if len(cn) >= _MIN_CODE_LEN and cn in page_norm]
            if not page_hits:
                continue
            snippet = _clean(text)[:_MAX_PAGE_CHARS]
            for code in page_hits:
                _append(texts, code, snippet)
                matched.add(code)
    return len(matched)


def extract_reference_texts(
    paths: list[str], style_codes: list[str],
    display_names: list[str] | None = None,
) -> tuple[dict[str, str], list[dict]]:
    """Pull per-style reference text out of catalog/lookbook files.

    Returns ``(texts, files)`` where ``texts`` maps style code -> catalog text
    (capped at MAX_REF_CHARS) and ``files`` reports per-file match counts:
    ``[{"name", "matched"}]``. Raises ``ReferenceIngestError`` if a file
    cannot be opened at all (corrupt / wrong format).
    """
    codes_by_norm = {}
    for c in style_codes:
        n = _norm_code(c)
        if n:
            codes_by_norm[n] = c

    texts: dict[str, str] = {}
    files: list[dict] = []
    for i, path in enumerate(paths):
        name = (display_names[i] if display_names and i < len(display_names)
                else os.path.basename(path))
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                matched = _pdf_texts(path, codes_by_norm, texts)
            else:
                matched = _xlsx_texts(path, codes_by_norm, texts)
        except Exception as e:
            raise ReferenceIngestError(f"Could not read reference file '{name}': {e}") from e
        files.append({"name": name, "matched": matched})
        logger.info("Reference ingest: %s matched %d styles", name, matched)
    return texts, files

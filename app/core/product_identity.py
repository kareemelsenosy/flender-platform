"""Product identity (multi-source Step 1).

Assign every product row a *stable identity* so the same product can be
recognised across different sources — a SAP export, a Google Sheet, a supplier
PDF line sheet, a B2B shop. This is the backbone the later "merge the best
attribute per product" step builds on.

Matching precedence (most to least reliable):
  1. barcode / EAN     — globally unique per size variant
  2. manufacturer/style code (+ colour + size) — stable per source family
  3. brand + name (+ colour + size)            — last-resort fuzzy-ish key

Two levels of key are exposed:
  * ``line_key``  — identifies one *sellable line* (style + colour + size),
                    the granularity of a single order row. Used to dedupe and
                    to line up the same size across sources.
  * ``style_key`` — identifies the *style* (the "master record per style"),
                    ignoring size. Used to group a product's size rows.

Everything here is pure/deterministic (no I/O), so it is cheap to unit-test and
safe to call anywhere in the import pipeline.
"""
from __future__ import annotations

import re


def _norm(value) -> str:
    """Lowercase and strip everything but a-z0-9 (spaces, punctuation, case)."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _get(row: dict, *keys: str) -> str:
    """First non-empty value among ``keys`` (rows come from varied sources)."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def style_key(row: dict) -> str:
    """Identity of the *style* (colour-level), ignoring size — the master record.

    Precedence: manufacturer/style code (+colour) → brand+name (+colour).
    Barcode is intentionally NOT used here: an EAN identifies a single size,
    so it cannot key a whole style.
    """
    code = _norm(_get(row, "item_code", "manufacturer_code", "style_code", "sap_code"))
    colour = _norm(_get(row, "color_name", "colour", "color"))
    if code:
        return f"code:{code}" + (f"|{colour}" if colour else "")
    brand = _norm(_get(row, "brand", "brand_name"))
    name = _norm(_get(row, "style_name", "description", "name", "web_description_2"))
    if brand or name:
        return f"nm:{brand}|{name}" + (f"|{colour}" if colour else "")
    return ""


def line_key(row: dict) -> str:
    """Identity of one *sellable line* (style + colour + size).

    Precedence: barcode → style_key (+size). This is what lets the exact same
    size be matched across two different sources, and what we dedupe on.
    """
    barcode = _norm(_get(row, "barcode", "ean", "gtin"))
    if barcode:
        return f"ean:{barcode}"
    base = style_key(row)
    if not base:
        return ""
    size = _norm(_get(row, "size"))
    return base + (f"|sz:{size}" if size else "")


def group_by_style(rows: list[dict]) -> "dict[str, list[dict]]":
    """Group rows into their style master records (order-preserving).

    Rows with no derivable key each land in their own singleton group so they
    are never silently merged together.
    """
    groups: dict[str, list[dict]] = {}
    for i, row in enumerate(rows):
        key = style_key(row) or f"row:{i}"
        groups.setdefault(key, []).append(row)
    return groups


def dedupe_lines(rows: list[dict]) -> "tuple[list[dict], int]":
    """Drop exact-duplicate sellable lines (same ``line_key``), keeping the
    first occurrence. Returns (deduped_rows, num_removed).

    PDF/line-sheet extraction commonly repeats a header block or a summary row
    across pages; this removes those without touching genuinely distinct sizes.
    Rows with no derivable ``line_key`` are always kept (never assumed dupes).
    """
    seen: set[str] = set()
    out: list[dict] = []
    removed = 0
    for row in rows:
        key = line_key(row)
        if key and key in seen:
            removed += 1
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out, removed


def count_styles(rows: list[dict]) -> int:
    """How many distinct style master records these rows represent."""
    return len(group_by_style(rows))

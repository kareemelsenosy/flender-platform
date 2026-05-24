"""
Core parser — Smart Excel/CSV column detection and parsing.
Refactored from images-finder/parser.py into a class (no globals).
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

import pandas as pd


COLUMN_PATTERNS: dict[str, list[str]] = {
    "item_code": [
        "style number", "style no", "style#", "item code", "item no",
        "sku", "art", "article", "ref", "reference", "product code", "style",
        "manufacturer code", "vendor item no", "vendor item number",
        "item number", "product no", "product number", "model no", "model number",
        "model", "article no", "article number",
    ],
    "style_name": [
        "style name", "product name", "description", "name", "title",
        "web description", "web description 2", "style description",
        "product description", "item description", "model name",
    ],
    "color_name": [
        "color name", "colour name", "color", "colour",
        "color description", "colour description",
    ],
    "color_code": [
        "color code", "colour code", "colorway", "color id", "colour id",
        "color number", "colour number",
    ],
    "size": [
        "size", "size name", "size description", "sizes", "size type",
    ],
    "brand": [
        "brand", "brand name", "manufacturer", "vendor", "make",
        "label", "supplier",
    ],
    "wholesale_price": [
        "wholesale price", "whs price", "whs", "cost price", "buy price",
        "net price", "dealer price", "whsl in eur", "whsl in gel",
        "purchase price", "trade price", "ex works",
    ],
    "retail_price": [
        "retail price", "rrp", "rrp price", "msrp", "recommended retail",
        "sugg. retail", "suggested retail", "srp", "selling price",
    ],
    "qty_available": [
        "quantity available", "qty available", "available", "stock",
        "qty", "quantity", "avail qty", "avail", "freestock", "free stock",
        "on hand", "inventory", "units available",
    ],
    "gender": [
        "gender", "sex", "gender description", "division",
    ],
    "barcode": [
        "barcode", "ean", "upc", "gtin", "vendor style",
        "vendor style no", "vendor style number",
    ],
    "item_group": [
        "item group", "group", "category", "product group",
        "department", "class", "sub category", "subcategory",
    ],
    "item_group_code": [
        "item group code", "itemgroupcode", "group code",
        "product group code", "folder group code", "image folder code",
    ],
    "sap_code": [
        "itemcode", "item code sap", "sap code", "sap item code",
        "folder code", "image folder", "b2b itemcode",
    ],
}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _best_match(col_header: str, patterns: list[str]) -> float:
    col_clean = re.sub(r"[_\-/\\]+", " ", col_header.lower().strip())
    best = 0.0
    for pat in patterns:
        score = _similarity(col_clean, pat)
        if pat in col_clean or col_clean in pat:
            score = max(score, 0.85)
        best = max(best, score)
    return best


def detect_columns(headers: list[str]) -> dict[str, str | None]:
    """Map standardised keys to original header names via fuzzy matching."""
    THRESHOLD = 0.55
    scores: dict[str, dict[str, float]] = {key: {} for key in COLUMN_PATTERNS}

    for header in headers:
        for key, patterns in COLUMN_PATTERNS.items():
            s = _best_match(str(header), patterns)
            if s >= THRESHOLD:
                scores[key][header] = s

    mapping: dict[str, str | None] = {key: None for key in COLUMN_PATTERNS}
    assigned: set[str] = set()

    candidates = sorted(
        [(s, k, h) for k, hs in scores.items() for h, s in hs.items()],
        reverse=True,
    )
    for score, key, header in candidates:
        if mapping[key] is None and header not in assigned:
            mapping[key] = header
            assigned.add(header)

    return mapping


def _find_header_row(df_raw: pd.DataFrame) -> int:
    best_row, best_hits = 0, -1
    for row_idx in range(min(5, len(df_raw))):
        row_values = [str(v) for v in df_raw.iloc[row_idx].tolist() if pd.notna(v)]
        hits = 0
        for cell in row_values:
            for patterns in COLUMN_PATTERNS.values():
                if _best_match(cell, patterns) >= 0.55:
                    hits += 1
                    break
        if hits > best_hits:
            best_hits = hits
            best_row = row_idx
    return best_row


_SIZE_LETTER_TOKENS = {
    "xs", "s", "m", "l", "xl", "xxl", "xxxl",
    "2xl", "3xl", "4xl", "5xl",
    "os", "onesize", "one size", "freesize", "free size",
}


def _looks_like_size_header(header: str) -> bool:
    """True if a column header looks like a single garment/shoe size."""
    if not header:
        return False
    h = re.sub(r"\s+", " ", str(header).strip().lower())
    if not h:
        return False
    # Numeric sizes: "7", "7.5", "10", "10.5", "42", "44"
    if re.fullmatch(r"\d{1,3}([.,]5)?", h):
        return True
    # Letter sizes
    if h in _SIZE_LETTER_TOKENS:
        return True
    # Prefixed sizes: "EU 42", "US 9.5", "UK 7", "Size 9"
    if re.fullmatch(r"(eu|us|uk|de|fr|jp|size)\s*\d{1,3}([.,]5)?", h):
        return True
    return False


def _detect_size_columns(headers: list[str], used_headers: set[str]) -> list[str]:
    """Find a block of column headers that represent per-size quantity columns.

    Used when the source has sizes laid horizontally (one column per size) rather
    than a separate `size` + `quantity` pair. Returns the matching column names
    in their original order, or an empty list if no such block is present.
    """
    available = [h for h in headers if h and h not in used_headers]
    size_cols = [h for h in available if _looks_like_size_header(h)]
    # Need a real block — at least 3 size-looking columns, otherwise it's
    # probably a coincidence (e.g. a single "M" column means "Medium" something).
    if len(size_cols) < 3:
        return []
    return size_cols


def _coerce_numeric(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "-", "n/a", ""):
        return None
    # Strip currency symbols, codes, spaces — keep digits, dot, comma, minus
    cleaned = re.sub(r"[^\d.,''\-]", "", s.replace(",", "."))
    # Remove duplicate dots (e.g. "1.234.56" → take last chunk)
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]).replace(".", "") + "." + parts[-1]
    try:
        return float(cleaned) if cleaned and cleaned not in (".", "-") else None
    except (ValueError, TypeError):
        return None


class FileParser:
    """Parse Excel/CSV files with auto-column detection."""

    def get_sheet_names(self, filepath: str) -> list[str]:
        """Return list of sheet names for Excel files (empty list for CSV)."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".xlsx", ".xls"):
            xl = pd.ExcelFile(filepath, engine="openpyxl")
            return xl.sheet_names
        return []

    def parse(self, filepath: str, selected_sheets: list[str] | None = None) -> tuple[list[dict], list[dict], list[str]]:
        """
        Parse a file.
        Returns (rows, unique_items, raw_headers).
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Input file not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        df_raw = self._load_raw(filepath, ext, selected_sheets=selected_sheets)

        if df_raw.empty:
            raise ValueError("The file appears to be empty.")

        header_row_idx = _find_header_row(df_raw)
        raw_headers = [str(v).strip() for v in df_raw.iloc[header_row_idx].tolist()
                       if pd.notna(v) and str(v).strip()]

        df = df_raw.iloc[header_row_idx + 1:].copy()
        df.columns = [str(v).strip() for v in df_raw.iloc[header_row_idx].tolist()]
        df = df.dropna(how="all")

        col_map = detect_columns(list(df.columns))

        rows = self._build_rows(df, col_map)
        unique_items = self._dedupe(rows)
        return rows, unique_items, raw_headers

    def parse_with_mapping(self, filepath: str, mapping: dict[str, str | None],
                           selected_sheets: list[str] | None = None) -> tuple[list[dict], list[dict]]:
        """Parse using a user-provided column mapping instead of auto-detection."""
        ext = os.path.splitext(filepath)[1].lower()
        df_raw = self._load_raw(filepath, ext, selected_sheets=selected_sheets)
        header_row_idx = _find_header_row(df_raw)

        df = df_raw.iloc[header_row_idx + 1:].copy()
        df.columns = [str(v).strip() for v in df_raw.iloc[header_row_idx].tolist()]
        df = df.dropna(how="all")

        rows = self._build_rows(df, mapping)
        unique_items = self._dedupe(rows)
        return rows, unique_items

    def _load_raw(self, filepath: str, ext: str,
                  selected_sheets: list[str] | None = None) -> pd.DataFrame:
        if ext in (".xlsx", ".xls"):
            xl = pd.ExcelFile(filepath, engine="openpyxl")
            skip = {"export summary", "summary", "index", "toc", "contents"}
            if selected_sheets:
                # Use only selected sheets (ignore skip list when user explicitly chose)
                sheets = [s for s in xl.sheet_names if s in selected_sheets] or xl.sheet_names
            else:
                sheets = [s for s in xl.sheet_names if s.lower() not in skip] or xl.sheet_names

            if len(sheets) == 1:
                return xl.parse(sheets[0], header=None, dtype=str)

            frames = [xl.parse(s, header=None, dtype=str) for s in sheets]
            frames = [f for f in frames if not f.empty]
            if not frames:
                raise ValueError("No data found in any sheet.")

            first = frames[0]
            hi = _find_header_row(first)
            headers = first.iloc[hi].tolist()

            combined = []
            for f in frames:
                h = _find_header_row(f)
                f.columns = range(len(f.columns))
                data = f.iloc[h + 1:].copy()
                data.columns = range(len(data.columns))
                combined.append(data)

            result = pd.concat(combined, ignore_index=True)
            header_df = pd.DataFrame([headers])
            return pd.concat([header_df, result], ignore_index=True)

        elif ext == ".csv":
            return pd.read_csv(filepath, header=None, dtype=str)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _build_rows(self, df: pd.DataFrame, col_map: dict[str, str | None]) -> list[dict]:
        # Detect sheets where sizes are laid horizontally (one column per size).
        # We run this regardless of what the user mapped for `size` / `qty`
        # because mapping picks a single column, which can't represent a row
        # that genuinely spans many size columns. When we detect ≥3 size-like
        # headers we treat ALL of them as size columns (including any the user
        # mapped to size or qty_available) and ignore those two mappings for
        # the purpose of this row expansion.
        used = {v for v in col_map.values() if v and v not in (col_map.get("size"), col_map.get("qty_available"))}
        size_columns = _detect_size_columns(list(df.columns), used)
        # When horizontal sizes are in play, drop any single-column size/qty
        # mapping the user picked — those are individual size columns we are
        # about to expand into rows. Otherwise the same value would leak as
        # the "size" of every emitted row.
        if size_columns:
            col_map = dict(col_map)
            col_map["size"] = None
            col_map["qty_available"] = None

        rows: list[dict] = []
        for _, raw_row in df.iterrows():
            base: dict[str, Any] = {}
            for std_key, orig_header in col_map.items():
                if orig_header and orig_header in raw_row.index:
                    value = raw_row[orig_header]
                    value = None if (isinstance(value, float) and pd.isna(value)) else str(value).strip()
                    if std_key in ("wholesale_price", "retail_price", "qty_available"):
                        value = _coerce_numeric(value)
                    base[std_key] = value
                else:
                    base[std_key] = None

            if not base.get("item_code"):
                continue

            raw_dict = {str(k): str(v).strip() if pd.notna(v) else ""
                        for k, v in raw_row.items()}

            if size_columns:
                # Emit one row per size that has a positive quantity. Each
                # output row carries the same item metadata but a distinct
                # (size, qty_available) pair, so the downstream dedupe
                # collects every available size on the item.
                emitted_any = False
                for col in size_columns:
                    qty = _coerce_numeric(raw_row.get(col))
                    if not qty or qty <= 0:
                        continue
                    row = dict(base)
                    row["size"] = str(col).strip()
                    row["qty_available"] = qty
                    row["_raw"] = raw_dict
                    rows.append(row)
                    emitted_any = True
                # If no size column had a qty, still emit the item so it
                # doesn't silently disappear from the import.
                if not emitted_any:
                    row = dict(base)
                    row["_raw"] = raw_dict
                    rows.append(row)
            else:
                base["_raw"] = raw_dict
                rows.append(base)
        return rows

    def _dedupe(self, rows: list[dict]) -> list[dict]:
        seen: dict[tuple, dict] = {}
        for row in rows:
            key = (row.get("item_code") or "", row.get("color_code") or "")
            if key not in seen:
                seen[key] = {
                    "item_code": row.get("item_code"),
                    "style_name": row.get("style_name"),
                    "color_name": row.get("color_name"),
                    "color_code": row.get("color_code"),
                    "brand": row.get("brand"),
                    "wholesale_price": row.get("wholesale_price"),
                    "retail_price": row.get("retail_price"),
                    "gender": row.get("gender"),
                    "barcode": row.get("barcode"),
                    "item_group": row.get("item_group"),
                    "item_group_code": row.get("item_group_code"),
                    "sap_code": row.get("sap_code"),
                    "pictures_url": row.get("pictures_url"),
                    "sizes": [],
                    "qty_available": 0,
                }
            item = seen[key]
            size = row.get("size")
            if size:
                # Split concatenated sizes like "S / M / L" or "7 / 8 / 8.5"
                parts = [s.strip() for s in str(size).split("/") if s.strip()]
                for part in parts:
                    if part not in item["sizes"]:
                        item["sizes"].append(part)
            qty = row.get("qty_available")
            if isinstance(qty, (int, float)) and qty:
                item["qty_available"] = (item["qty_available"] or 0) + qty

        return list(seen.values())

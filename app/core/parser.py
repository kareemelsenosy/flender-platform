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
        "manufacturer code",
    ],
    "style_name": [
        "style name", "product name", "description", "name", "title",
        "web description", "web description 2",
    ],
    "color_name": [
        "color name", "colour name", "color", "colour",
    ],
    "color_code": [
        "color code", "colour code", "colorway", "color id", "colour id",
    ],
    "size": [
        "size", "size name", "size description", "sizes",
    ],
    "brand": [
        "brand", "brand name", "manufacturer", "vendor",
    ],
    "wholesale_price": [
        "wholesale price", "whs price", "whs", "cost price", "buy price",
        "net price", "dealer price", "whsl in eur", "whsl in gel",
    ],
    "retail_price": [
        "retail price", "rrp", "rrp price", "msrp", "recommended retail",
        "sugg. retail", "suggested retail",
    ],
    "qty_available": [
        "quantity available", "qty available", "available", "stock",
        "qty", "quantity", "avail qty", "avail", "freestock", "free stock",
    ],
    "gender": [
        "gender", "sex", "gender description",
    ],
    "barcode": [
        "barcode", "ean", "upc", "gtin",
    ],
    "item_group": [
        "item group", "group", "category", "product group",
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


def _coerce_numeric(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except (ValueError, TypeError):
        return value


class FileParser:
    """Parse Excel/CSV files with auto-column detection."""

    def parse(self, filepath: str) -> tuple[list[dict], list[dict], list[str]]:
        """
        Parse a file.
        Returns (rows, unique_items, raw_headers).
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Input file not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        df_raw = self._load_raw(filepath, ext)

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

    def parse_with_mapping(self, filepath: str, mapping: dict[str, str | None]) -> tuple[list[dict], list[dict]]:
        """Parse using a user-provided column mapping instead of auto-detection."""
        ext = os.path.splitext(filepath)[1].lower()
        df_raw = self._load_raw(filepath, ext)
        header_row_idx = _find_header_row(df_raw)

        df = df_raw.iloc[header_row_idx + 1:].copy()
        df.columns = [str(v).strip() for v in df_raw.iloc[header_row_idx].tolist()]
        df = df.dropna(how="all")

        rows = self._build_rows(df, mapping)
        unique_items = self._dedupe(rows)
        return rows, unique_items

    def _load_raw(self, filepath: str, ext: str) -> pd.DataFrame:
        if ext in (".xlsx", ".xls"):
            xl = pd.ExcelFile(filepath, engine="openpyxl")
            skip = {"export summary", "summary", "index", "toc", "contents"}
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
        rows = []
        for _, raw_row in df.iterrows():
            row: dict[str, Any] = {}
            for std_key, orig_header in col_map.items():
                if orig_header and orig_header in raw_row.index:
                    value = raw_row[orig_header]
                    value = None if (isinstance(value, float) and pd.isna(value)) else str(value).strip()
                    if std_key in ("wholesale_price", "retail_price", "qty_available"):
                        value = _coerce_numeric(value)
                    row[std_key] = value
                else:
                    row[std_key] = None

            if not row.get("item_code"):
                continue

            row["_raw"] = {str(k): str(v).strip() if pd.notna(v) else ""
                          for k, v in raw_row.items()}
            rows.append(row)
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
                    "sizes": [],
                    "qty_available": 0,
                }
            item = seen[key]
            size = row.get("size")
            if size and size not in item["sizes"]:
                item["sizes"].append(size)
            qty = row.get("qty_available")
            if isinstance(qty, (int, float)) and qty:
                item["qty_available"] = (item["qty_available"] or 0) + qty

        return list(seen.values())

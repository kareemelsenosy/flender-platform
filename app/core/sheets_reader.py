"""
Google Sheets reader — Fetch data from Google Spreadsheets via gspread.
Ported from order-sheet-generator/main.py.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Extract spreadsheet ID from a Google Sheets URL or return raw ID."""
    url_or_id = url_or_id.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id


def extract_image_url(formula: str) -> Optional[str]:
    """Extract URL from =IMAGE("...") formula. Handles spaces and semicolon separators. (L6)"""
    if not formula or not isinstance(formula, str):
        return None
    # Matches: =IMAGE( "url" ) or =IMAGE("url",1) or =IMAGE("url";1)
    m = re.search(r'=IMAGE\s*\(\s*"([^"]+)"', formula, re.IGNORECASE)
    if m:
        return m.group(1)
    # Also handle single-quoted variants used in some locales
    m = re.search(r"=IMAGE\s*\(\s*'([^']+)'", formula, re.IGNORECASE)
    return m.group(1) if m else None


def extract_hyperlink_url(formula: str) -> Optional[str]:
    """Extract URL from =HYPERLINK("...") formula. Handles spaces and semicolons. (L6)"""
    if not formula or not isinstance(formula, str):
        return None
    # Matches: =HYPERLINK("url","label") or =HYPERLINK("url";"label")
    m = re.search(r'=HYPERLINK\s*\(\s*"([^"]+)"', formula, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"=HYPERLINK\s*\(\s*'([^']+)'", formula, re.IGNORECASE)
    return m.group(1) if m else None


def _parse_worksheet(ws) -> tuple:
    """Parse a single worksheet — fires both API calls simultaneously (2x faster per tab)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_display = pool.submit(ws.get_all_values, value_render_option='FORMATTED_VALUE')
        f_formula  = pool.submit(ws.get_all_values, value_render_option='FORMULA')
        display_values = f_display.result()
        try:
            formula_values = f_formula.result()
        except Exception:
            # Excel files opened in Google Sheets return 400 for FORMULA render;
            # fall back to display values — image/hyperlink formulas won't resolve
            # but all other data will import correctly.
            logger.warning("FORMULA render not supported for '%s', falling back to display values", ws.title)
            formula_values = display_values

    if not display_values:
        return [], [], []

    # M6: Robust header detection — prefer explicit "Picture" marker, but fall back
    # to the first row that has ≥3 non-empty columns (handles sheets without "Picture").
    _KNOWN_HEADERS = {"picture", "manufacturer code", "brand name", "color", "size",
                      "whs price", "rrp price", "item code", "barcode", "gender"}
    header_row_idx = 0
    best_score = -1
    for i, row in enumerate(display_values[:20]):  # scan first 20 rows only
        non_empty = [c.strip() for c in row if c.strip()]
        if not non_empty:
            continue
        # Score: how many cells match known header names
        score = sum(1 for c in non_empty if c.lower() in _KNOWN_HEADERS)
        if score > best_score:
            best_score = score
            header_row_idx = i
        # Early-exit: found "Picture" marker — definitive
        if row and row[0].strip() == "Picture":
            header_row_idx = i
            break

    headers      = display_values[header_row_idx]
    display_rows = display_values[header_row_idx + 1:]
    formula_rows = formula_values[header_row_idx + 1:]
    return headers, display_rows, formula_rows


class SheetsReader:
    """Read data from Google Spreadsheets."""

    def __init__(self, credentials_path: str):
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Google credentials file not found: {credentials_path}")
        self.credentials_path = credentials_path
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        self.gc = gspread.authorize(creds)

    def fetch_spreadsheet(self, spreadsheet_id: str) -> dict:
        """
        Fetch all tabs from a spreadsheet.
        Returns {
            "title": spreadsheet title,
            "tabs": [
                {
                    "title": tab title,
                    "headers": [...],
                    "display_rows": [[...], ...],
                    "formula_rows": [[...], ...],
                }
            ]
        }
        """
        spreadsheet = self.gc.open_by_key(spreadsheet_id)
        logger.info(f"Spreadsheet: '{spreadsheet.title}'")

        try:
            worksheets = spreadsheet.worksheets()
        except Exception as e:
            if "not supported for this document" in str(e) or "[400]" in str(e):
                raise RuntimeError(
                    "This document cannot be accessed via the Google Sheets API. "
                    "It is likely an Excel file (.xlsx) stored in Google Drive. "
                    "To fix: open it in Google Sheets → File → Save as Google Sheets, "
                    "then share the new Sheets file with your service account."
                ) from e
            raise

        # Fetch all tabs in parallel — each tab fires 2 API calls simultaneously
        def _fetch_one(args):
            idx, ws = args
            headers, display_rows, formula_rows = _parse_worksheet(ws)
            return idx, ws.title, headers, display_rows, formula_rows

        slot: list = [None] * len(worksheets)
        with ThreadPoolExecutor(max_workers=min(len(worksheets), 8)) as pool:
            futures = {pool.submit(_fetch_one, (i, ws)): i
                       for i, ws in enumerate(worksheets)}
            for future in as_completed(futures):
                idx, title, headers, display_rows, formula_rows = future.result()
                if headers:
                    slot[idx] = {
                        "title": title,
                        "headers": headers,
                        "display_rows": display_rows,
                        "formula_rows": formula_rows,
                    }

        tabs = [t for t in slot if t is not None]
        logger.info(f"  Fetched {len(tabs)} tabs in parallel")
        return {"title": spreadsheet.title, "tabs": tabs}

    def extract_items_from_tab(self, tab: dict) -> list[dict]:
        """
        Extract structured items from a single tab.
        Each item has: item_code, brand, style_name, color_name, size,
                       wholesale_price, retail_price, image_url, etc.
        """
        headers = tab["headers"]
        display_rows = tab["display_rows"]
        formula_rows = tab["formula_rows"]

        # Find column indices
        col_idx = {}
        for i, h in enumerate(headers):
            col_idx[h.strip()] = i

        def _find_column(*names: str) -> int:
            normalized = {str(k).strip().lower(): v for k, v in col_idx.items()}
            for name in names:
                idx = normalized.get(name.strip().lower())
                if idx is not None:
                    return idx
            return -1

        items = []
        # "last_" vars carry forward values from merged/blank cells
        last_item_code = ""
        last_brand = ""
        last_style_name = ""
        last_color_name = ""
        last_gender = ""
        last_barcode = ""
        last_item_group = ""
        last_wholesale_price = ""
        last_retail_price = ""
        last_image_url = ""
        last_dropbox_url = ""
        last_comming_soon_qty = ""

        for ri, row in enumerate(display_rows):
            def get_val(*col_names):
                if len(col_names) == 1:
                    idx = col_idx.get(col_names[0], -1)
                else:
                    idx = _find_column(*col_names)
                if idx < 0 or idx >= len(row):
                    return ""
                return str(row[idx]).strip()

            item_code = get_val("Manufacturer Code")

            # Extract image URL from formula
            image_url = ""
            pic_idx = col_idx.get("Picture", -1)
            if pic_idx >= 0 and ri < len(formula_rows) and pic_idx < len(formula_rows[ri]):
                image_url = extract_image_url(formula_rows[ri][pic_idx]) or ""

            # Extract dropbox link
            dropbox_url = ""
            pic_link_idx = col_idx.get("Pictures", -1)
            if pic_link_idx >= 0 and ri < len(formula_rows) and pic_link_idx < len(formula_rows[ri]):
                dropbox_url = extract_hyperlink_url(formula_rows[ri][pic_link_idx]) or get_val("Pictures")
                if dropbox_url and not dropbox_url.startswith("http"):
                    dropbox_url = ""

            if item_code:
                # First row of a product — update carry-forward values
                last_item_code = item_code
                last_brand = get_val("Brand Name")
                last_style_name = get_val("Web Description 2")
                last_color_name = get_val("Color")
                last_gender = get_val("Gender")
                last_barcode = get_val("Barcode")
                last_item_group = get_val("Item Group")
                last_wholesale_price = get_val("WHS Price")
                last_retail_price = get_val("RRP Price")
                last_image_url = image_url or last_image_url
                last_dropbox_url = dropbox_url or last_dropbox_url
                last_comming_soon_qty = get_val("Comming Soon", "Coming Soon")
            else:
                # Merged / blank row — only include if we have a size value
                # (continuation of previous product's sizes)
                size = get_val("Size")
                if not size or not last_item_code:
                    continue

            # Read per-row values (not carried forward — unique per size row)
            row_barcode = get_val("Barcode") or last_barcode
            row_stock = get_val("FreeStock") or get_val("Stock")
            row_sap_code = get_val("ItemCode")

            items.append({
                "item_code": last_item_code,
                "brand": last_brand,
                "style_name": last_style_name,
                "color_name": last_color_name,
                "size": get_val("Size"),
                "gender": last_gender,
                "barcode": row_barcode,
                "item_group": last_item_group,
                "wholesale_price": last_wholesale_price,
                "retail_price": last_retail_price,
                "qty_available": row_stock,
                "image_url": last_image_url,
                "dropbox_url": last_dropbox_url,
                "sap_code": row_sap_code,
                "comming_soon_qty": last_comming_soon_qty,
            })

        return items

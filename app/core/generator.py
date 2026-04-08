"""
Core generator — Excel order sheet with embedded images.
Supports both simple ordersheet and full 23-column standard format with
product groups, merged cells, and Row helper columns.
"""
from __future__ import annotations

import io
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import requests
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

# ─── Styles ────────────────────────────────────────────────────────────────
HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=9)
SUMMARY_FILL = PatternFill("solid", fgColor="F1F5F9")

GROUP_FILLS = [
    PatternFill("solid", fgColor="EFF6FF"),  # light blue
    PatternFill("solid", fgColor="F9FAFB"),  # light gray
]
PRICE_FILLS = [
    PatternFill("solid", fgColor="DBEAFE"),  # blue tint
    PatternFill("solid", fgColor="F3F4F6"),  # gray tint
]
QTY_FILL = PatternFill("solid", fgColor="FEF9C3")       # yellow = editable
TOTAL_FILL = PatternFill("solid", fgColor="DCFCE7")     # green = calculated
FREESTOCK_FILL = PatternFill("solid", fgColor="D1FAE5")  # light green

THIN_BORDER = Border(
    left=Side(style="thin", color="D1D5DB"),
    right=Side(style="thin", color="D1D5DB"),
    top=Side(style="thin", color="D1D5DB"),
    bottom=Side(style="thin", color="D1D5DB"),
)

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)

BODY_FONT = Font(size=9, name="Calibri")
PRICE_FONT = Font(bold=True, size=9, color="1D4ED8", name="Calibri")
LINK_FONT = Font(size=9, color="2563EB", underline="single", name="Calibri")
FORMULA_FONT = Font(bold=True, size=9, color="166534", name="Calibri")
EDITABLE_FONT = Font(size=9, color="92400E", name="Calibri")
SUMMARY_QTY_FONT = Font(bold=True, size=10, color="92400E", name="Calibri")
SUMMARY_TOTAL_FONT = Font(bold=True, size=10, color="166534", name="Calibri")

_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

IMAGE_PX = 150
IMAGE_PT = IMAGE_PX * 0.75
TEXT_ROW_H = 22


def detect_currency_symbol(items: list[dict], currency_override: str = "") -> str:
    """Detect currency from an explicit override or from WHS Price sample values."""
    if currency_override:
        return currency_override
    # Check first few items' wholesale_price raw string representation
    for item in items[:10]:
        whs = str(item.get("wholesale_price_raw", item.get("wholesale_price", "")))
        wu = whs.upper()
        if "AED" in wu or wu.startswith("DH"):
            return "AED "
        if wu.startswith("$") or "USD" in wu:
            return "$"
        if wu.startswith("£") or "GBP" in wu:
            return "£"
        if wu.startswith("€") or "EUR" in wu:
            return "€"
    return "€"

# Standard ordersheet columns
STANDARD_COLUMNS = {
    "Picture":               {"width": 21,  "align": CENTER},
    "Brand Name":            {"width": 18,  "align": LEFT},
    "Item Group":            {"width": 16,  "align": LEFT},
    "Manufacturer Code":     {"width": 20,  "align": LEFT},
    "Web Description 2":     {"width": 32,  "align": LEFT},
    "Barcode":               {"width": 16,  "align": CENTER},
    "Gender":                {"width": 10,  "align": CENTER},
    "Color":                 {"width": 14,  "align": LEFT},
    "Size":                  {"width": 8,   "align": CENTER},
    "Stock":                 {"width": 11,  "align": CENTER},
    "QTY":                   {"width": 8,   "align": CENTER},
    "QTY Total":             {"width": 14,  "align": RIGHT},
    "WHS Price":             {"width": 12,  "align": RIGHT},
    "RRP Price":             {"width": 12,  "align": RIGHT},
    "Pictures":              {"width": 10,  "align": CENTER},
    # Row helper columns (grouped/hidden)
    "Row WHS Price":         {"width": 0},
    "Row RRP Price":         {"width": 0},
    "Row Manufacturer Code": {"width": 0},
    "Row Web Description 2": {"width": 0},
    "Row Color":             {"width": 0},
    "Row Brand Name":        {"width": 0},
    "ItemCode":              {"width": 14,  "align": LEFT},
}


class OrderSheetGenerator:
    """Generate Excel order sheets with embedded images."""

    def __init__(self, config: dict[str, Any] | None = None,
                 progress_callback: Any = None):
        cfg = config or {}
        self.img_size = tuple(cfg.get("image_size", [150, 150]))
        self.row_height_pt = int(cfg.get("row_height_px", 100)) * 0.75
        self.save_images = cfg.get("save_images_to_folder", False)
        self._progress = progress_callback  # callable(downloaded, total, stage)

    def _report(self, downloaded: int, total: int, stage: str):
        if self._progress:
            self._progress(downloaded, total, stage)

    def generate(self, items: list[dict], output_dir: str,
                 input_filename: str = "export", brand: str = "",
                 currency: str = "") -> str:
        """Generate standard ordersheet Excel from approved items."""
        os.makedirs(output_dir, exist_ok=True)

        today = date.today().strftime("%Y%m%d")
        safe_brand = re.sub(r"[^\w\-]", "_", brand) if brand else "FLENDER"
        safe_input = re.sub(r"[^\w\-]", "_", os.path.splitext(input_filename)[0])
        out_filename = f"{today}_{safe_brand}_{safe_input}_OrderSheet.xlsx"
        out_path = os.path.join(output_dir, out_filename)

        # Image folders for saving
        images_dir = None
        if self.save_images:
            images_dir = os.path.join(output_dir, "images")
            os.makedirs(images_dir, exist_ok=True)

        wb = Workbook()
        ws = wb.active
        ws.title = "Order Sheet"

        # Build column list
        out_headers = list(STANDARD_COLUMNS.keys())

        # ── Header row (row 2) ──
        SUMMARY_ROW = 1
        HEADER_ROW = 2
        DATA_START = 3

        has_grouped_cols = False
        for ci, header in enumerate(out_headers, 1):
            cell = ws.cell(row=HEADER_ROW, column=ci, value=header)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER
            cell.border = THIN_BORDER

            col_letter = get_column_letter(ci)
            cfg = STANDARD_COLUMNS.get(header, {"width": 14})
            w = cfg.get("width", 14)
            if w == 0:
                ws.column_dimensions[col_letter].outlineLevel = 1
                ws.column_dimensions[col_letter].hidden = True
                has_grouped_cols = True
            else:
                ws.column_dimensions[col_letter].width = w

        ws.row_dimensions[HEADER_ROW].height = 28

        if has_grouped_cols:
            ws.sheet_format.outlineLevelCol = 1

        # ── Detect product groups (items with same item_code get grouped) ──
        groups = self._detect_product_groups(items)
        row_to_group = {}
        for gi, g in enumerate(groups):
            for ri in range(g["start"], g["end"] + 1):
                row_to_group[ri] = (g, gi)

        # ── Download all images (parallel for speed) ──
        image_data = {}
        url_to_bytes = {}  # cache to avoid double downloads for save_images
        unique_urls = {}
        for gi, g in enumerate(groups):
            url = g.get("image_url", "")
            if url:
                unique_urls.setdefault(url, []).append(gi)

        # Also collect per-item URLs for save_images (including additional URLs)
        all_item_urls = {}
        if images_dir:
            for ri, item in enumerate(items):
                url = item.get("approved_url", "")
                if url and url.startswith("http"):
                    all_item_urls[url] = True
                    if url not in unique_urls:
                        unique_urls.setdefault(url, [])
                for extra_url in item.get("additional_urls", []):
                    if extra_url and extra_url.startswith("http"):
                        all_item_urls[extra_url] = True
                        if extra_url not in unique_urls:
                            unique_urls.setdefault(extra_url, [])

        total_images = len(unique_urls)
        downloaded_count = 0
        self._report(0, total_images, "downloading")

        def _dl(url_gis):
            url, gis = url_gis
            return url, gis, self._download_image(url)

        with ThreadPoolExecutor(max_workers=16) as pool:
            for url, gis, img_bytes in pool.map(_dl, unique_urls.items()):
                downloaded_count += 1
                self._report(downloaded_count, total_images, "downloading")
                if img_bytes:
                    # Store raw bytes to avoid BytesIO cursor issues on reuse
                    img_bytes.seek(0)
                    raw = img_bytes.read()
                    url_to_bytes[url] = raw
                    for gi in gis:
                        image_data[gi] = raw

        # Column indices (1-based)
        col_idx = {h: i + 1 for i, h in enumerate(out_headers)}
        qty_col = col_idx.get("QTY")
        whs_col = col_idx.get("WHS Price")
        row_whs_col = col_idx.get("Row WHS Price")
        total_col = col_idx.get("QTY Total")
        pic_col = col_idx.get("Picture", 1)

        qty_letter = get_column_letter(qty_col) if qty_col else None
        whs_letter = get_column_letter(whs_col) if whs_col else None
        # Use hidden Row WHS Price for the formula — it's populated on every size row
        # even when the visible WHS Price column is blank for non-first rows
        formula_whs_letter = get_column_letter(row_whs_col) if row_whs_col else whs_letter
        total_letter = get_column_letter(total_col) if total_col else None

        currency_symbol = detect_currency_symbol(items, currency)
        currency_fmt = f'"{currency_symbol}"#,##0.00'

        # ── Data rows (row 3+) ──
        tmp_images = []
        for ri, item in enumerate(items):
            excel_row = ri + DATA_START
            g_info = row_to_group.get(ri)
            gi = g_info[1] if g_info else (ri % 2)
            g = g_info[0] if g_info else None

            num_in_group = g["end"] - g["start"] + 1 if g else 1
            ws.row_dimensions[excel_row].height = max(IMAGE_PT / num_in_group, TEXT_ROW_H) if g else self.row_height_pt

            gfill = GROUP_FILLS[gi % 2]
            pfill = PRICE_FILLS[gi % 2]

            for header in out_headers:
                ci = col_idx[header]
                cell = ws.cell(row=excel_row, column=ci)
                cell.border = THIN_BORDER

                if header == "Picture":
                    cell.fill = gfill
                    cell.alignment = CENTER

                elif header == "Brand Name":
                    cell.value = item.get("brand", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                elif header == "Item Group":
                    cell.value = item.get("item_group", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                elif header == "Manufacturer Code":
                    cell.value = item.get("item_code", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                elif header == "Web Description 2":
                    cell.value = item.get("style_name", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                elif header == "Barcode":
                    cell.value = item.get("barcode", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = CENTER

                elif header == "Gender":
                    cell.value = item.get("gender", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = CENTER

                elif header == "Color":
                    cell.value = item.get("color_name", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                elif header == "Size":
                    cell.value = item.get("size", "")
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = CENTER

                elif header == "Stock":
                    qty = item.get("qty_available")
                    cell.value = float(qty) if qty else 0
                    cell.fill = FREESTOCK_FILL
                    cell.font = Font(size=9, color="166534", name="Calibri")
                    cell.alignment = CENTER
                    cell.number_format = "#,##0"

                elif header == "QTY":
                    cell.value = 0
                    cell.fill = QTY_FILL
                    cell.font = EDITABLE_FONT
                    cell.alignment = CENTER

                elif header == "QTY Total":
                    if qty_letter and formula_whs_letter:
                        cell.value = f"={qty_letter}{excel_row}*{formula_whs_letter}{excel_row}"
                    else:
                        cell.value = 0
                    cell.number_format = currency_fmt
                    cell.fill = TOTAL_FILL
                    cell.font = FORMULA_FONT
                    cell.alignment = RIGHT

                elif header == "WHS Price":
                    whs = item.get("wholesale_price")
                    cell.value = float(whs) if whs else None
                    cell.number_format = currency_fmt
                    cell.fill = pfill
                    cell.font = PRICE_FONT
                    cell.alignment = RIGHT

                elif header == "RRP Price":
                    rrp = item.get("retail_price")
                    cell.value = float(rrp) if rrp else None
                    cell.number_format = currency_fmt
                    cell.fill = pfill
                    cell.font = PRICE_FONT
                    cell.alignment = RIGHT

                elif header == "Pictures":
                    url = item.get("approved_url", "")
                    if url and url.startswith("http"):
                        cell.value = "View"
                        cell.hyperlink = url
                        cell.font = LINK_FONT
                    else:
                        cell.value = ""
                        cell.font = BODY_FONT
                    cell.fill = gfill
                    cell.alignment = CENTER

                elif header == "ItemCode":
                    # Use SAP code from source, or construct from item_group + size
                    sap = item.get("sap_code", "")
                    if not sap:
                        ig = item.get("item_group", "")
                        sz = item.get("size", "")
                        sap = f"{ig} {sz}".strip() if ig else item.get("item_code", "")
                    cell.value = sap
                    cell.fill = gfill
                    cell.font = BODY_FONT
                    cell.alignment = LEFT

                # Row helper columns
                elif header == "Row WHS Price":
                    whs = item.get("wholesale_price")
                    cell.value = float(whs) if whs else None
                    cell.number_format = currency_fmt
                elif header == "Row RRP Price":
                    rrp = item.get("retail_price")
                    cell.value = float(rrp) if rrp else None
                    cell.number_format = currency_fmt
                elif header == "Row Manufacturer Code":
                    cell.value = item.get("item_code", "")
                elif header == "Row Web Description 2":
                    cell.value = item.get("style_name", "")
                elif header == "Row Color":
                    cell.value = item.get("color_name", "")
                elif header == "Row Brand Name":
                    cell.value = item.get("brand", "")

            # Save image to folder (use cached download, no re-download)
            if images_dir:
                img_url = item.get("approved_url", "")
                if img_url and img_url in url_to_bytes:
                    self._save_image_file(url_to_bytes[img_url], images_dir, item)
                # Save additional images with incrementing suffix
                for extra_url in item.get("additional_urls", []):
                    if extra_url and extra_url in url_to_bytes:
                        self._save_image_file(url_to_bytes[extra_url], images_dir, item)


        last_data_row = DATA_START + len(items) - 1

        # ── Merge cells for product groups & embed images ──
        embedded = 0
        for gi, g in enumerate(groups):
            excel_start = g["start"] + DATA_START
            excel_end = g["end"] + DATA_START

            # Merge Picture column for product group
            if excel_end > excel_start:
                ws.merge_cells(
                    start_row=excel_start, start_column=pic_col,
                    end_row=excel_end, end_column=pic_col,
                )

            mc = ws.cell(row=excel_start, column=pic_col)
            mc.fill = GROUP_FILLS[gi % 2]
            mc.alignment = CENTER
            mc.border = THIN_BORDER

            if gi not in image_data:
                continue

            # Embed image
            num_rows = g["end"] - g["start"] + 1
            target_h = min(IMAGE_PX, int(max(IMAGE_PT / num_rows, TEXT_ROW_H) * num_rows / 0.75))

            img_bytes = io.BytesIO(image_data[gi])
            raw_img = PILImage.open(img_bytes).convert("RGB")
            # Resize for Excel embedding only (keep original in url_to_bytes for folder saving)
            display_img = raw_img.copy()
            display_img.thumbnail((self.img_size[0], target_h), PILImage.LANCZOS)

            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            display_img.save(tmp.name, format="JPEG", quality=90, optimize=True)
            tmp.close()
            tmp_images.append(tmp.name)

            xl_img = XLImage(tmp.name)
            xl_img.width = self.img_size[0]
            xl_img.height = target_h

            try:
                anchor = TwoCellAnchor()
                anchor.editAs = "twoCell"
                anchor._from = AnchorMarker(col=pic_col - 1, colOff=0,
                                            row=excel_start - 1, rowOff=0)
                anchor.to = AnchorMarker(col=pic_col, colOff=0,
                                         row=excel_end, rowOff=0)
                xl_img.anchor = anchor
                ws.add_image(xl_img)
                embedded += 1
            except Exception:
                ws.add_image(xl_img, f"{get_column_letter(pic_col)}{excel_start}")
                embedded += 1

        # ── Summary row (row 1) ──
        for ci, header in enumerate(out_headers, 1):
            cell = ws.cell(row=SUMMARY_ROW, column=ci, value="")
            cell.fill = SUMMARY_FILL
            cell.border = THIN_BORDER

            if header == "QTY" and qty_letter:
                cell.value = f"=SUBTOTAL(109,{qty_letter}{DATA_START}:{qty_letter}{last_data_row})"
                cell.font = SUMMARY_QTY_FONT
                cell.alignment = CENTER
                cell.fill = QTY_FILL
            elif header == "QTY Total" and total_letter:
                cell.value = f"=SUBTOTAL(109,{total_letter}{DATA_START}:{total_letter}{last_data_row})"
                cell.number_format = currency_fmt
                cell.font = SUMMARY_TOTAL_FONT
                cell.alignment = RIGHT
                cell.fill = TOTAL_FILL
            else:
                cell.alignment = LEFT

        ws.row_dimensions[SUMMARY_ROW].height = 24

        # ── Freeze & filter ──
        ws.freeze_panes = f"B{DATA_START}"
        last_col_letter = get_column_letter(len(out_headers))
        ws.auto_filter.ref = f"A{HEADER_ROW}:{last_col_letter}{last_data_row}"

        ws.sheet_properties.tabColor = "1F2937"
        ws.print_title_rows = "1:2"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToPage = True
        ws.page_setup.fitToWidth = 1

        self._report(total_images, total_images, "saving")
        wb.save(out_path)
        self._report(total_images, total_images, "done")

        # Clean up temp images
        for p in tmp_images:
            try:
                os.remove(p)
            except Exception:
                pass

        return os.path.abspath(out_path)

    def _detect_product_groups(self, items: list[dict]) -> list[dict]:
        """Group items by item_code (same product, different sizes/colors)."""
        groups = []
        if not items:
            return groups

        current_code = items[0].get("item_code", "")
        start = 0

        for i in range(1, len(items)):
            code = items[i].get("item_code", "")
            if code != current_code:
                groups.append({
                    "start": start,
                    "end": i - 1,
                    "image_url": items[start].get("approved_url", ""),
                    "group_index": len(groups),
                })
                start = i
                current_code = code

        # Last group
        groups.append({
            "start": start,
            "end": len(items) - 1,
            "image_url": items[start].get("approved_url", ""),
            "group_index": len(groups),
        })

        return groups

    def _download_image(self, url: str) -> io.BytesIO | None:
        """Download image at full resolution (no thumbnail)."""
        if not url or not url.startswith("http"):
            return None
        try:
            resp = requests.get(url, headers=_DL_HEADERS, timeout=8)
            if resp.status_code != 200:
                return None
            buf = io.BytesIO(resp.content)
            # Validate it's actually an image
            PILImage.open(buf).verify()
            buf.seek(0)
            return buf
        except Exception:
            return None

    def _save_image_file(self, img_data: bytes, base_images_dir: str, item: dict):
        """
        Save full-resolution image to folder.
        Naming: {item_code}_{color_code}_1.jpg
        Folder name = Item Group if available, else item_code.
        """
        item_code = item.get("item_code", "unknown")
        color_code = item.get("color_code", "").strip()
        item_group = item.get("item_group", "").strip()
        safe_code = re.sub(r"[^\w\-]", "_", item_code)
        safe_color = re.sub(r"[^\w\-]", "_", color_code) if color_code else ""

        # Determine subfolder
        if item_group:
            folder_name = re.sub(r"[^\w\-]", "_", item_group)
        else:
            folder_name = safe_code

        folder_path = os.path.join(base_images_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)

        # Build filename base with color
        base_name = f"{safe_code}_{safe_color}" if safe_color else safe_code

        # Find next available number
        n = 1
        while os.path.exists(os.path.join(folder_path, f"{base_name}_{n}.jpg")):
            n += 1

        path = os.path.join(folder_path, f"{base_name}_{n}.jpg")

        # Save as high-quality JPEG at full resolution
        try:
            img = PILImage.open(io.BytesIO(img_data)).convert("RGB")
            img.save(path, format="JPEG", quality=95, optimize=True)
        except Exception:
            with open(path, "wb") as f:
                f.write(img_data)

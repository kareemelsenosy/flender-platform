"""SAP reference data — Operations OS.

SAP writes a nightly report per brand into a Google Sheet: one tab per stock
segment (ReOrder, PreOrder_<season>, SpecialStock, SampleStock, OverStock), each
listing what actually exists in SAP today with its item group, barcode, gender,
colour, size, prices and image status.

This is the "compare against what we already have" half of the pipeline. With it
the system can say which supplier styles are already in SAP, what SAP calls them,
and what the brand charged last season — instead of treating every collection as
if Flender had never traded the brand before.

Two details of the real sheets shape this module:

  * The header is on the **second** row; the first carries "Date Updated" and a
    timestamp. Reading row 1 as the header yields a table of empty columns.
  * A style appears once per size, so lookups are built at style-colour level
    and sizes are collected underneath.
"""
from __future__ import annotations

import re

# Column names as SAP writes them, mapped to the names the rest of the system
# uses. Tabs differ slightly (PreOrder has no FreeStock, ReOrder has no
# First Delivery Date), so every field is optional.
FIELD_ALIASES = {
    "brand": ("Brand Name", "Brand"),
    "item_group": ("Item Group",),                 # the SAP item group code
    "manufacturer_code": ("Manufacturer Code", "Mfr Catalog No."),
    "description": ("Web Description 2", "Description"),
    "barcode": ("Barcode", "Bar Code", "EAN"),
    "gender": ("Gender",),
    "colour": ("Color", "Colour", "Web Color"),
    "size": ("Size", "Size Description"),
    "free_stock": ("FreeStock", "Free Stock"),
    "coming_soon": ("Comming Soon", "Coming Soon"),
    "wholesale": ("WHS Price", "Wholesale"),
    "retail": ("RRP Price", "Retail"),
    "season": ("Season",),
    "first_delivery": ("First Delivery Date",),
    "stock_type": ("Stock Type",),
    "pictures": ("Pictures", "Picture"),
}

DATE_STAMP_LABEL = "Date Updated"


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _key(text) -> str:
    """Loose identity key: letters and digits only, case-folded."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def find_header_row(values) -> int:
    """Index of the header row. SAP puts a 'Date Updated' stamp above it."""
    for i, row in enumerate(values[:5]):
        if row and _norm(row[0]) == DATE_STAMP_LABEL:
            return i + 1
    # No stamp — assume the first non-empty row is the header.
    for i, row in enumerate(values[:5]):
        if any(_norm(c) for c in row):
            return i
    return 0


def parse_tab(values, tab_name: str = "") -> list[dict]:
    """Turn one tab's raw cell grid into normalised records."""
    if not values:
        return []
    h = find_header_row(values)
    header = [_norm(c) for c in values[h]]
    lookup = {}
    for field, names in FIELD_ALIASES.items():
        for n in names:
            if n in header:
                lookup[field] = header.index(n)
                break

    out = []
    for raw in values[h + 1:]:
        if not any(_norm(c) for c in raw):
            continue
        rec = {f: _norm(raw[i]) if i < len(raw) else ""
               for f, i in lookup.items()}
        if not rec.get("item_group") and not rec.get("barcode"):
            continue                      # a spacer or total row
        rec["segment"] = tab_name
        out.append(rec)
    return out


def parse_workbook(tabs: "dict[str, list]") -> list[dict]:
    """Parse every tab of a brand's stock sheet. ``tabs`` is {name: values}."""
    records: list[dict] = []
    for name, values in tabs.items():
        records.extend(parse_tab(values, name))
    return records


class SapReference:
    """What SAP already holds for one brand, indexed for lookup.

    A supplier style is found by manufacturer code first (the brand's own
    style-colour reference, which is what an order sheet carries), then by
    barcode, then by a loosened style key. Nothing is matched on description.
    """

    def __init__(self, records):
        self.records = list(records)
        self._by_mfr: dict[str, list[dict]] = {}
        self._by_barcode: dict[str, dict] = {}
        self._by_style: dict[str, list[dict]] = {}
        for r in self.records:
            mfr = _key(r.get("manufacturer_code"))
            if mfr:
                self._by_mfr.setdefault(mfr, []).append(r)
            bc = _key(r.get("barcode"))
            if bc:
                self._by_barcode.setdefault(bc, r)
            style = _key(str(r.get("manufacturer_code", "")).split("-")[0])
            if style:
                self._by_style.setdefault(style, []).append(r)

    def __len__(self):
        return len(self.records)

    @property
    def seasons(self) -> list[str]:
        return sorted({r["season"] for r in self.records if r.get("season")})

    def find(self, *, style: str = "", colour: str = "", barcode: str = "") -> list[dict]:
        """Every SAP row for one supplier style-colour (one per size)."""
        if barcode:
            hit = self._by_barcode.get(_key(barcode))
            if hit:
                return [hit]
        if style and colour:
            hits = self._by_mfr.get(_key(f"{style}-{colour}")) or \
                   self._by_mfr.get(_key(f"{style}{colour}"))
            if hits:
                return hits
        if style:
            hits = self._by_style.get(_key(style))
            if hits and colour:
                narrowed = [h for h in hits if _key(h.get("colour")) == _key(colour)]
                if narrowed:
                    return narrowed
            if hits:
                return hits
        return []

    def exists(self, **kw) -> bool:
        return bool(self.find(**kw))

    def known_values(self, *, style: str = "", colour: str = "",
                     barcode: str = "") -> dict:
        """What SAP already says about this product.

        Used to fill the fields a supplier order form never carries — most
        usefully the SAP item group, which settles the ambiguity where one
        supplier subcategory maps to several SAP groups.
        """
        hits = self.find(style=style, colour=colour, barcode=barcode)
        if not hits:
            return {}
        first = hits[0]
        return {
            "item_group": first.get("item_group", ""),
            "gender": first.get("gender", ""),
            "colour": first.get("colour", ""),
            "description": first.get("description", ""),
            "season": first.get("season", ""),
            "sizes": sorted({h["size"] for h in hits if h.get("size")}),
            "has_images": bool(_norm(first.get("pictures"))),
            "wholesale": first.get("wholesale", ""),
            "retail": first.get("retail", ""),
        }

    def classify(self, rows, *, style_field="Style Number",
                 colour_field="Color", barcode_field="Barcode") -> dict:
        """Split supplier rows into what SAP already has and what is new."""
        existing, new = [], []
        for r in rows:
            hit = self.find(style=str(r.get(style_field, "")),
                            colour=str(r.get(colour_field, "")),
                            barcode=str(r.get(barcode_field, "")))
            (existing if hit else new).append(r)
        return {"existing": existing, "new": new,
                "summary": {"rows": len(rows), "existing": len(existing),
                            "new": len(new)}}


def price_history(records, *, currency_suffix="AED") -> list[dict]:
    """Rows shaped for the pricing engine's margin learning.

    SAP writes prices as text with the currency appended ("281.00 AED"), so the
    number is pulled out here rather than in the pricing rules.
    """
    def number(text):
        m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(text or ""))
        return float(m.group(0).replace(",", "")) if m else None

    out = []
    for r in records:
        whs, rrp = number(r.get("wholesale")), number(r.get("retail"))
        if whs is None and rrp is None:
            continue
        out.append({"Item No.": r.get("item_group", ""),
                    f"Flender WHS {currency_suffix}": whs,
                    f"Flender RRP {currency_suffix}": rrp,
                    "season": r.get("season", "")})
    return out

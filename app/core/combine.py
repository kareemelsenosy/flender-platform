"""Combining a split collection: order sheet + product list.

Some brands send one collection as two files. The order sheet says what was
bought; the product list (HUF's "UPC HS code" excel, a Carhartt product data
export) carries the HS codes, origin and the per-size UPCs. Neither is
complete on its own, and SAP needs both halves on every line.

This joins them the way the team does by hand, and — just as important —
says what did not join: an item in the order sheet with no product-list
entry has no HS code, and an item only in the product list was either not
bought or is missing from the order sheet. Both are reported, never dropped.

Rows are expected in standard field names (see ``field_mapping``).
"""
from __future__ import annotations

import re

# Copied from the product list only when the match is to the exact line
# (barcode, or style + colour + size). From a style-level match they would
# stamp one size's barcode onto every size.
LINE_FIELDS = {"Size", "Barcode", "SKU", "Quantity", "Units", "Qty"}


def _n(value) -> str:
    text = str(value or "").strip()
    if text.lower() in ("nan", "none", "null"):
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _blank(value) -> bool:
    return value is None or str(value).strip().lower() in ("", "nan", "none", "null")


def _style_keys(row: dict) -> list[str]:
    """The style code as written, and the part before a colour suffix:
    HUF's order form writes 'JK00525-CAMO' where the list has 'JK00525'."""
    raw = str(row.get("Item No.") or "").strip()
    keys = [_n(raw)]
    head = re.split(r"[-./ ]", raw, maxsplit=1)[0]
    if _n(head) and _n(head) != keys[0]:
        keys.append(_n(head))
    return [k for k in keys if k]


def _colour_keys(row: dict) -> list[str]:
    return [k for k in (_n(row.get("Colour Code")), _n(row.get("Color"))) if k]


def _item_label(row: dict) -> str:
    style = str(row.get("Item No.") or "").strip()
    colour = str(row.get("Color") or row.get("Colour Code") or "").strip()
    if colour and colour.lower() != "nan" and _n(colour) not in _n(style):
        return f"{style} · {colour}"
    return style


def _item_key(row: dict) -> str:
    styles, colours = _style_keys(row), _colour_keys(row)
    if not styles:
        return ""
    if colours and not styles[0].endswith(colours[0]):
        return styles[0] + "|" + colours[0]
    return styles[0]


def combine(order_rows: list[dict], list_rows: list[dict], *,
            scale_codes: set[str] | None = None) -> "tuple[list[dict], dict]":
    """Join the product list onto the order sheet.

    ``scale_codes`` are size values that name a whole size scale rather than
    one size (HUF's A–E). An order row carrying one is expanded into the
    product list's individual sizes, each with its own UPC.

    Returns ``(rows, report)``.
    """
    scale_codes = {c.upper() for c in (scale_codes or set())}
    by_barcode: dict[str, dict] = {}
    by_sc: dict[str, list[dict]] = {}
    by_style: dict[str, list[dict]] = {}
    for r in list_rows:
        bc = _n(r.get("Barcode"))
        if bc:
            by_barcode.setdefault(bc, r)
        styles, colours = _style_keys(r), _colour_keys(r)
        for s in styles:
            by_style.setdefault(s, []).append(r)
            for c in colours:
                by_sc.setdefault(s + "|" + c, []).append(r)
                by_sc.setdefault(s + c, []).append(r)   # 'JK00525-CAMO' style

    used: set[int] = set()
    out: list[dict] = []
    added_fields: set[str] = set()
    unmatched_order: dict[str, str] = {}
    expanded = 0

    def merge(base: dict, extra: dict, line_level: bool) -> dict:
        new = dict(base)
        for k, v in extra.items():
            if _blank(v) or k.startswith("__"):
                continue
            if not line_level and k in LINE_FIELDS:
                continue
            if _blank(new.get(k)):
                new[k] = v
                added_fields.add(k)
        return new

    for row in order_rows:
        bc = _n(row.get("Barcode"))
        if bc and bc in by_barcode:
            hit = by_barcode[bc]
            used.add(id(hit))
            out.append(merge(row, hit, True))
            continue

        styles, colours = _style_keys(row), _colour_keys(row)
        matches: list[dict] = []
        for s in styles:
            for c in colours:
                matches = by_sc.get(s + "|" + c) or by_sc.get(s + c) or []
                if matches:
                    break
            if not matches:
                matches = by_sc.get(s) or []          # style code already holds the colour
            if matches:
                break
        if not matches:
            for s in styles:
                matches = by_style.get(s) or []
                if matches:
                    break
        if not matches:
            out.append(row)
            key = _item_key(row)
            if key:
                unmatched_order.setdefault(key, _item_label(row))
            continue

        for m in matches:
            used.add(id(m))
        size = str(row.get("Size") or "").strip()
        is_scale = (not size) or size.upper() in scale_codes
        sized = [m for m in matches if not _blank(m.get("Size"))]

        if not is_scale:
            same = [m for m in sized if _n(m.get("Size")) == _n(size)]
            out.append(merge(row, same[0], True) if same else merge(row, matches[0], False))
        elif sized:
            # One order line for a whole size scale becomes one line per size,
            # each carrying that size's UPC from the product list.
            for m in sized:
                line = merge(row, m, True)
                line["Size"] = m.get("Size")
                if not _blank(m.get("Barcode")):
                    line["Barcode"] = m.get("Barcode")
                out.append(line)
            expanded += len(sized) - 1
        else:
            out.append(merge(row, matches[0], False))

    only_in_list: dict[str, str] = {}
    for r in list_rows:
        if id(r) in used:
            continue
        key = _item_key(r)
        if key:
            only_in_list.setdefault(key, _item_label(r))

    order_items = {_item_key(r) for r in order_rows if _item_key(r)}
    list_items = {_item_key(r) for r in list_rows if _item_key(r)}
    report = {
        "order_items": len(order_items),
        "list_items": len(list_items),
        "matched_items": len(order_items) - len(unmatched_order),
        "only_in_order": sorted(unmatched_order.values())[:300],
        "only_in_order_count": len(unmatched_order),
        "only_in_list": sorted(only_in_list.values())[:300],
        "only_in_list_count": len(only_in_list),
        "fields_added": sorted(f for f in added_fields if f[:1] != "_"),
        "lines_expanded": expanded,
        "rows_out": len(out),
    }
    return out, report

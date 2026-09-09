"""TEMP STANDARD TEMPLATE — Operations OS.

The step Flender's process tracker calls "TEMP": a supplier order form becomes
the SAP staging sheet of ``U_*`` fields that feeds item creation.

Derived from the Hiking Patrol SS27 pair — the NuORDER order form and the TEMP
sheet that was accepted — and verified row for row: 60 order rows expand to
exactly 272 TEMP rows across 26 styles, with no row unaccounted for on either
side.

Order forms lay sizes out horizontally (``Size 1``…``Size 5`` with a price
each), so one order row becomes one TEMP row per populated size. Fields the
order form simply does not carry — HS code, country of origin, gender — are
left empty and raised as exceptions rather than invented; they come from the
brand's history.
"""
from __future__ import annotations

import re

from app.core.base_color import propose as propose_base_color
from app.core.sap_creation import normalise_header, season_code

# The TEMP layout, in order, from the accepted Hiking Patrol sheet.
TEMP_COLUMNS = [
    "U_Season", "U_OSRow", "U_NRStatus", "U_CodeBars", "U_ESDate",
    "U_SuppCatNum", "U_Gender", "U_VCat", "U_ItmsGrpCod", "U_SCode",
    "U_SizeCode", "U_Webbeschreibung2", "U_BaseColor", "U_Material",
    "U_HS_Code", "U_COO", "U_VCName", "U_Webbeschreibung",
    "U_def_size_list_code", "U_Web_Color", "U_SortIndex",
    "U_webbeschreibung_gr", "U_LongDscp",
    "U_Prop1", "U_Prop2", "U_Prop3",
    "U_SProp1", "U_SProp2", "U_SProp3", "U_SProp4", "U_SProp5",
]

# Blank in the accepted sheet too — not gaps.
EXPECTED_BLANK = {"U_Material", "U_LongDscp", "U_Prop1", "U_Prop2", "U_Prop3",
                  "U_SProp1", "U_SProp2", "U_SProp3", "U_SProp4", "U_SProp5"}

# Only history or a human can supply these; the order form never carries them.
FROM_HISTORY = ["U_HS_Code", "U_COO", "U_Gender", "U_VCName"]

# Supplier size labels to the SAP spelling.
SIZE_ALIASES = {"OS": "onesize", "O/S": "onesize", "ONE SIZE": "onesize",
                "ONESIZE": "onesize", "UNI": "onesize", "U": "onesize"}

# A size run maps to one SAP size-list code.
SIZE_LIST_CODES = {
    ("xs", "s", "m", "l", "xl"): "XS26XL",
    ("s", "m", "l", "xl"): "S24XL",
    ("xs", "s", "m", "l", "xl", "xxl"): "XS26XXL",
    ("onesize",): "ONESIZE",
}

# Words that stay upper-cased through title casing: the accepted sheet has
# "Edge LT Softshell", not "Edge Lt Softshell".
ACRONYMS = {"LT", "LS", "SS", "XL", "UV", "GTX", "DWR", "II", "III", "NY",
            "USA", "UK", "HP", "WIP", "3D", "2L", "3L"}

ONESIZE_SORT = 58000     # onesize sorts last, as in the accepted sheets
SORT_BASE, SORT_STEP = 1000, 500


# A DataFrame converted to dicts carries NaN, and str(nan) is "nan" — a
# non-empty string that would otherwise sail through every emptiness check and
# invent rows and values that were never in the supplier file.
_BLANKS = {"", "nan", "none", "nat", "null", "<na>"}


def is_blank(value) -> bool:
    """True for None, empty text, and the string forms of a missing value."""
    return value is None or str(value).strip().lower() in _BLANKS


def normalise_size(size: str) -> str:
    """'OS' -> 'onesize'. Anything else keeps the supplier's spelling."""
    s = str(size or "").strip()
    return SIZE_ALIASES.get(s.upper(), s)


def smart_title(text: str) -> str:
    """Title case that leaves acronyms alone."""
    words = str(text or "").split()
    out = []
    for w in words:
        core = re.sub(r"[^A-Za-z0-9]", "", w).upper()
        out.append(w.upper() if core in ACRONYMS else w.title())
    return " ".join(out)


def size_list_code(sizes) -> "tuple[str, str]":
    """SAP size-list code for a run of sizes. Returns (code, reason)."""
    run = tuple(normalise_size(s).lower() for s in sizes if not is_blank(s))
    if not run:
        return "", "no sizes given"
    code = SIZE_LIST_CODES.get(run)
    if code:
        return code, f"size run {'/'.join(run)}"
    return "", f"size run {'/'.join(run)} has no SAP size-list code"


def sort_index(position: int, size: str) -> int:
    """Where a size sorts inside its style. One-size items sort last."""
    if normalise_size(size).lower() == "onesize":
        return ONESIZE_SORT
    return SORT_BASE + SORT_STEP * position


def barcode(style: str, colour: str, size: str) -> str:
    """U_CodeBars — style, colour and size run together, letters and digits."""
    raw = f"{style or ''}{colour or ''}{normalise_size(size)}"
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def expand_sizes(row: dict, max_cols: int = 12) -> list[dict]:
    """One order row becomes one record per populated size column."""
    out = []
    for n in range(1, max_cols + 1):
        size = row.get(f"Size {n}")
        if is_blank(size):
            continue
        out.append({"size": str(size).strip(),
                    "size_price": row.get(f"Size price {n}"),
                    "qty": row.get(f"Qty {n}"),
                    "position": len(out)})
    return out


def build_temp_sheet(rows, *, brand: str, season: str,
                     group_map=None, vocab=None, colour_lookup=None,
                     history=None, earliest_ship_date: str = "") -> dict:
    """Build the TEMP sheet from supplier order rows.

    ``group_map`` maps a supplier subcategory to a SAP item group. It is
    deliberately one-to-many in reality — TOP is SHIRTS or T-SHIRTS depending
    on the product — so a subcategory with more than one candidate raises a
    review rather than picking the first.

    ``history`` supplies the per-style values the order form lacks, keyed by
    style code: ``{"HP0127001": {"U_HS_Code": ..., "U_COO": ...}}``.

    ``earliest_ship_date`` is set once for the collection. It is not the
    supplier's ship date — Hiking Patrol SS27 shipped 20 January on the order
    form and went into SAP as 15 January — so it is asked for, not copied.
    """
    group_map = group_map or {}
    history = history or {}
    out_rows: list[dict] = []
    exceptions: list[dict] = []

    if is_blank(earliest_ship_date):
        exceptions.append({"sku": "", "field": "U_ESDate", "severity": "manual_review",
                           "reason": "the collection has no earliest shipment date "
                                     "(it differs from the supplier's ship date)",
                           "suggestion": "Set the earliest shipment date for this collection"})

    code, why = season_code(season)
    if not code:
        exceptions.append({"sku": "", "field": "U_Season", "severity": "critical",
                           "reason": why, "suggestion": "Confirm the SAP season code"})

    for order_index, raw in enumerate(rows, start=1):
        src = {normalise_header(k): v for k, v in raw.items()}
        get = lambda *k: next(                                   # noqa: E731
            (str(src[x]).strip() for x in k
             if x in src and not is_blank(src[x])), "")

        style = get("Style Number", "Style Code", "Item No.")
        colour = get("Color", "Colour")
        name = get("Name", "Item Description", "Description")
        subcat = get("Subcategory", "Item Group", "Category")
        sizes = expand_sizes(src)
        if not sizes:
            exceptions.append({"sku": style, "field": "U_SizeCode",
                               "severity": "critical",
                               "reason": "no sizes on this order row",
                               "suggestion": "Check the size columns"})
            continue

        list_code, list_why = size_list_code([s["size"] for s in sizes])
        if not list_code:
            exceptions.append({"sku": style, "field": "U_def_size_list_code",
                               "severity": "manual_review", "reason": list_why,
                               "suggestion": "Add this size run to the SAP size lists"})

        # Item group: one supplier subcategory can map to several SAP groups.
        candidates = group_map.get(subcat.upper(), [])
        if isinstance(candidates, str):
            candidates = [candidates]
        group = candidates[0] if len(candidates) == 1 else ""
        if len(candidates) > 1:
            exceptions.append({
                "sku": style, "field": "U_ItmsGrpCod", "severity": "manual_review",
                "reason": f"'{subcat}' maps to {' or '.join(candidates)} — "
                          f"the description decides",
                "suggestion": f"Choose the SAP group for '{name}'"})
        elif not candidates and subcat:
            exceptions.append({
                "sku": style, "field": "U_ItmsGrpCod", "severity": "manual_review",
                "reason": f"no SAP item group known for '{subcat}'",
                "suggestion": f"Map '{subcat}' to a SAP item group"})

        base = propose_base_color(colour, vocab, colour_lookup)
        if not base["value"]:
            exceptions.append({"sku": style, "field": "U_BaseColor",
                               "severity": "manual_review",
                               "reason": f"'{colour}' has never been classified",
                               "suggestion": f"Choose a base colour for '{colour}'"})

        hist = history.get(style, {})
        for field in FROM_HISTORY:
            if not hist.get(field):
                exceptions.append({
                    "sku": style, "field": field, "severity": "warning",
                    "reason": f"{field} is not in the order form and "
                              f"{style} has no history",
                    "suggestion": f"Supply {field} for {style}"})

        for s in sizes:
            size = normalise_size(s["size"])
            row = {c: "" for c in TEMP_COLUMNS}
            row.update({
                "U_Season": code or "",
                "U_OSRow": str(order_index),
                "U_NRStatus": "N",
                "U_CodeBars": barcode(style, colour, size),
                "U_SuppCatNum": f"{style}-{colour}" if style and colour else "",
                "U_VCat": smart_title(subcat),
                "U_ItmsGrpCod": group,
                "U_SCode": style,
                "U_SizeCode": size,
                "U_webbeschreibung_gr": size,
                "U_Webbeschreibung2": smart_title(name),
                # The accepted sheet stores the base colour lower-cased here,
                # unlike the Carhartt creation sheet which title-cases it.
                "U_BaseColor": (base["value"] or "").lower(),
                "U_Webbeschreibung": (brand or "").upper(),
                "U_def_size_list_code": list_code,
                "U_Web_Color": smart_title(colour),
                "U_SortIndex": str(sort_index(s["position"], size)),
                "U_ESDate": "" if is_blank(earliest_ship_date) else str(earliest_ship_date),
            })
            for field in FROM_HISTORY:
                value = hist.get(field, "")
                row[field] = "" if is_blank(value) else str(value).strip()
            out_rows.append(row)

    by_sev: dict[str, int] = {}
    for e in exceptions:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1

    return {
        "columns": TEMP_COLUMNS,
        "rows": out_rows,
        "exceptions": exceptions,
        "summary": {"rows": len(out_rows), "order_rows": len(rows),
                    "styles": len({r["U_SCode"] for r in out_rows if r["U_SCode"]}),
                    "by_severity": by_sev},
    }

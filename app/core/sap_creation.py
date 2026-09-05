"""SAP product-creation package — Operations OS.

Turns a supplier import sheet into the SAP creation sheet Flender loads.

The transformation is not invented here: it was derived from the Carhartt FW26
pair (the supplier sheet and the finished SAP sheet that was accepted) and
verified row by row against all 8,824 of them. Each rule below records what it
scored, so a rule that stops holding for a new brand is visible rather than
silent.

Nothing is guessed into SAP. A value that cannot be derived is left empty and
raised as an exception with the reason, which is what the approval screen shows.
"""
from __future__ import annotations

import re

from app.core.base_color import propose as propose_base_color

# The SAP creation layout, in order. Taken from the accepted FW26 sheet.
SAP_COLUMNS = [
    "Season", "Collection", "New/Repeat", "Brand", "Barcode",
    "Earliest Shipment Date", "Requested Shipment Date", "Quantity",
    "Total Line Amount", "SKU", "Item.Colour", "Sex", "Main Waregroup",
    "Item No.", "Colour Code", "House Colour", "Finish", "Size Run", "Size",
    "Item Description", "Color", "BASE COLOR", "Execution",
    "Wholesale Price EUR", "Item Description 2", "Material",
    "Item Description Long", "Item Description Long 2", "Duty Position",
    "Country of Origin", "Country of Origin Description", "MID Code",
    "Minimum SalesQty", "OUT", "Retail EUR", "Retail GBP",
    "Wholesale Price USD", "Retail USD", "Retail DKK", "Retail NOK",
    "Retail SEK", "Retail CHF", "Retail FI EUR", "Retail CZK", "Retail PLN",
]

# Columns that are empty in the accepted FW26 sheet too. Blank here is normal
# and must not be reported as a gap.
EXPECTED_BLANK = {"Requested Shipment Date", "Quantity",
                  "Item Description Long 2", "Item Description 2"}

# Columns SAP cannot accept a row without.
REQUIRED = ["Season", "Brand", "Barcode", "SKU", "Item No.", "Colour Code",
            "Size", "Item Description", "Color", "BASE COLOR",
            "Main Waregroup", "Wholesale Price EUR"]

# Season letters to Flender's SAP season code. Confirmed against the process
# tracker ("2027-01 SPRING") and the FW26 sheet ("2026-03").
_SEASON_SLOT = {"SS": "01", "SP": "01", "FW": "03", "AW": "03"}


def season_code(season: str) -> "tuple[str | None, str]":
    """'SS27' -> ('2027-01', reason). Returns (None, why) when unmappable."""
    m = re.match(r"^\s*([A-Za-z]{2})\s*(\d{2}|\d{4})\s*$", str(season or ""))
    if not m:
        return None, f"season {season!r} is not in the form SS27 / FW26"
    letters, year = m.group(1).upper(), m.group(2)
    slot = _SEASON_SLOT.get(letters)
    if not slot:
        return None, f"no SAP season slot is defined for {letters!r}"
    return f"20{year[-2:]}-{slot}", f"{letters}{year[-2:]} is season {slot}"


def normalise_header(name: str) -> str:
    """Collapse whitespace so 'Earliest  Shipment  Date' matches its twin."""
    return re.sub(r"\s+", " ", str(name or "")).strip()


def split_colour_code(code: str) -> "tuple[str, str]":
    """'8902' -> ('89', '02'); '3T64O' -> ('3T6', '4O').

    House colour is everything before the final two characters, which is the
    finish. Verified on all 8,824 FW26 rows, where codes are 4 or 5 long.
    """
    code = str(code or "").strip()
    if len(code) < 3:
        return "", code
    return code[:-2], code[-2:]


def build_row(src: dict, *, season: str, collection: str,
              vocab=None, lookup=None) -> "tuple[dict, list[dict]]":
    """Build one SAP creation row. Returns (row, exceptions)."""
    # Normalise here too: build_row is called directly as well as through
    # build_creation_sheet, and supplier headers carry stray double spaces.
    src = {normalise_header(k): v for k, v in src.items()}
    get = lambda *keys: next(                       # noqa: E731
        (str(src[k]).strip() for k in map(normalise_header, keys)
         if k in src and src[k] is not None and str(src[k]).strip()), "")

    out = {c: "" for c in SAP_COLUMNS}
    exceptions: list[dict] = []

    item_no = get("Item No.", "Item No", "item_code")
    colour_code = get("Colour Code", "Color Code")
    colour_name = get("Color", "Colour")

    code, reason = season_code(season)
    if not code:
        exceptions.append({"field": "Season", "severity": "critical",
                           "reason": reason, "suggestion": "Confirm the SAP season code"})
    out["Season"] = code or ""
    out["Collection"] = collection

    # Two supplier flags collapse to one; the style flag is the one SAP takes.
    out["New/Repeat"] = get("New/Repeat Style", "New/Repeat")

    out["Item No."] = item_no
    out["Colour Code"] = colour_code
    out["Item.Colour"] = f"{item_no}.{colour_code}" if item_no and colour_code else ""
    out["House Colour"], out["Finish"] = split_colour_code(colour_code)

    # SAP's Main Waregroup is the supplier's *Item Group*, not their own
    # "Main Waregroup" column, which is one level too coarse.
    out["Main Waregroup"] = get("Item Group")

    base = propose_base_color(colour_name, vocab, lookup)
    out["BASE COLOR"] = base["value"] or ""
    if not base["value"]:
        exceptions.append({
            "field": "BASE COLOR", "severity": "manual_review",
            "reason": f"'{colour_name}' has never been classified — {base['reason']}",
            "suggestion": f"Choose a base colour for '{colour_name}'"})
    elif base["confidence"] < 1.0:
        exceptions.append({
            "field": "BASE COLOR", "severity": "warning",
            "reason": f"proposed {base['value']} for '{colour_name}' ({base['reason']})",
            "suggestion": "Approve or correct the proposed base colour"})

    # Everything else passes through untouched.
    for col in SAP_COLUMNS:
        if not out[col]:
            direct = get(col, normalise_header(col))
            if direct:
                out[col] = direct

    for col in REQUIRED:
        if not out[col] and col != "BASE COLOR":
            exceptions.append({
                "field": col, "severity": "critical",
                "reason": f"{col} is empty and SAP requires it",
                "suggestion": f"Supply {col} for this line"})
    return out, exceptions


def build_creation_sheet(rows, *, season: str, collection: str = "Main",
                         vocab=None, lookup=None) -> dict:
    """Build the whole package.

    Returns the rows, one exception record per problem (carrying the SKU so the
    approval screen can group them), and a summary of what needs a human.
    """
    src_rows = [{normalise_header(k): v for k, v in r.items()} for r in rows]
    out_rows: list[dict] = []
    exceptions: list[dict] = []

    for src in src_rows:
        row, excs = build_row(src, season=season, collection=collection,
                              vocab=vocab, lookup=lookup)
        out_rows.append(row)
        sku = row.get("SKU") or row.get("Item.Colour") or ""
        for e in excs:
            exceptions.append({**e, "sku": sku})

    by_sev: dict[str, int] = {}
    for e in exceptions:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1

    # Colour decisions are per colour name, not per line — 900 lines sharing an
    # unknown colour is one decision, and the screen must say so.
    colour_decisions = sorted({
        e["reason"].split("'")[1] for e in exceptions
        if e["field"] == "BASE COLOR" and e["severity"] == "manual_review"
        and "'" in e["reason"]})

    return {
        "columns": SAP_COLUMNS,
        "rows": out_rows,
        "exceptions": exceptions,
        "summary": {
            "rows": len(out_rows),
            "by_severity": by_sev,
            "colour_decisions": colour_decisions,
            "clean_rows": sum(
                1 for r in out_rows
                if all(r[c] for c in REQUIRED)),
        },
    }

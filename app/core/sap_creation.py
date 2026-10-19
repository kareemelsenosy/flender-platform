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
_SEASON_SLOT = {"SS": "01", "SP": "01", "FW": "03", "AW": "03",
                # HUF's summer and fall drops. Assumed to share the spring and
                # fall slots until Flender confirms otherwise.
                "SU": "01", "FA": "03",
                # Pre-fall: Carhartt PRE FALL 27 is 2027-02.
                "PF": "02",
                # Holiday: HUF HO26 is 2026-04. HUF itself calls it WN (winter).
                "HO": "04", "WN": "04"}


def season_code(season: str) -> "tuple[str | None, str]":
    """'SS27' -> ('2027-01', reason). Returns (None, why) when unmappable.

    A season already written the SAP way ('2026-04') is taken as it is.
    """
    sap = re.match(r"^\s*(20\d{2})\s*[-/.]\s*(0[1-4])\s*$", str(season or ""))
    if sap:
        return f"{sap.group(1)}-{sap.group(2)}", "given as a SAP season code"
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


def base_colour_exception(colour_name: str, base: dict, field: str) -> dict | None:
    """The question a base-colour result raises, worded by where it came from."""
    if base["value"] and base["confidence"] >= 1.0:
        return None
    if not base["value"]:
        return {
            "field": field, "subject": colour_name, "severity": "manual_review",
            "reason": f"'{colour_name}' is not in the brand colour table and has "
                      f"never been classified — {base['reason']}",
            "suggestion": f"Choose a base colour for '{colour_name}'"}
    if base.get("source") == "temporary":
        return {
            "field": field, "subject": colour_name, "severity": "manual_review",
            "reason": f"'{colour_name}' is missing from the brand colour table — "
                      f"temporary {base['value']} filled in ({base['reason']}); "
                      f"confirm or change it",
            "suggestion": f"Confirm {base['value']} for '{colour_name}'"}
    return {
        "field": field, "subject": colour_name, "severity": "warning",
        "reason": f"proposed {base['value']} for '{colour_name}' ({base['reason']}) "
                  f"— not yet in the brand colour table",
        "suggestion": "Approve or correct the proposed base colour"}


def item_colour_code(item_no: str, colour_code: str, colour_name: str,
                     uses_colour_codes: bool) -> str:
    """The per-colour item code.

    Carhartt and Edwin: style + colour code (I026462.8902), whose code is house
    colour + finish. Every other brand: style + colour name, as their own SAP
    records hold it (AC01312-SILVER, PT00408-SANDED BLACK).
    """
    item_no = str(item_no or "").strip()
    if not item_no:
        return ""
    if uses_colour_codes:
        return f"{item_no}.{colour_code}" if colour_code else ""
    name = re.sub(r"\s+", " ", str(colour_name or "")).strip().upper()
    # 'BLACK/WHITE' is written BLACK-WHITE in the item code.
    name = re.sub(r"(?<=\S)/(?=\S)", "-", name)
    return f"{item_no}-{name}" if name else ""


# Never copied from the supplier sheet: the supplier's own season ('WN26')
# or colour split is not SAP's.
_DERIVED = {"Season", "Item.Colour", "House Colour", "Finish", "BASE COLOR"}


def build_row(src: dict, *, season: str, collection: str,
              vocab=None, lookup=None, table=None, temporary=None,
              base_colour: bool = True,
              uses_colour_codes: bool = True) -> "tuple[dict, list[dict]]":
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
        exceptions.append({"field": "Season", "subject": season or "unknown",
                           "severity": "critical",
                           "reason": reason, "suggestion": "Confirm the SAP season code"})
    out["Season"] = code or ""
    out["Collection"] = collection

    # Two supplier flags collapse to one; the style flag is the one SAP takes.
    out["New/Repeat"] = get("New/Repeat Style", "New/Repeat")

    out["Item No."] = item_no
    out["Colour Code"] = colour_code
    out["Item.Colour"] = item_colour_code(item_no, colour_code, colour_name,
                                          uses_colour_codes)
    if uses_colour_codes:
        out["House Colour"], out["Finish"] = split_colour_code(colour_code)

    # SAP's Main Waregroup is the supplier's *Item Group*, not their own
    # "Main Waregroup" column, which is one level too coarse.
    out["Main Waregroup"] = get("Item Group")

    # Base colour is per item, and only for the brands whose SAP records carry
    # it; for the rest it stays empty and is never asked about.
    if base_colour:
        base = propose_base_color(colour_name, vocab, lookup,
                                  table=table, temporary=temporary,
                                  code=colour_code)
        out["BASE COLOR"] = base["value"] or ""
        question = base_colour_exception(colour_name, base, "BASE COLOR")
        if question:
            exceptions.append(question)

    # Everything else passes through untouched.
    for col in SAP_COLUMNS:
        if not out[col] and col not in _DERIVED:
            direct = get(col, normalise_header(col))
            if direct:
                out[col] = direct

    # SAP holds an HS code as digits (62034259); suppliers write 6201.40.5011.
    if out["Duty Position"]:
        out["Duty Position"] = re.sub(r"\D", "", out["Duty Position"])

    supplier_price = get("Supplier Wholesale Price")
    for col in REQUIRED:
        if col == "Wholesale Price EUR" and not out[col] and supplier_price:
            cur = get("Supplier Wholesale Currency") or "another currency"
            exceptions.append({
                "field": col, "subject": f"priced in {cur}", "severity": "warning",
                "reason": f"the supplier prices this collection in {cur} "
                          f"(e.g. {supplier_price}); the EUR price comes from the "
                          f"pricing step, so this column is left empty",
                "suggestion": "Add the EUR price list or fill it in the pricing step"})
            continue
        if col == "Colour Code" and not out[col] and not src.get("__brand_has_colour_codes", True):
            exceptions.append({
                "field": col, "subject": "brand sends none", "severity": "warning",
                "reason": "this brand's files carry no colour codes, so Colour Code, "
                          "Item.Colour and House Colour stay empty",
                "suggestion": "Confirm SAP accepts these items without a colour code"})
            continue
        if not out[col] and col != "BASE COLOR":
            exceptions.append({
                "field": col, "subject": col, "severity": "critical",
                "reason": f"{col} is empty and SAP requires it",
                "suggestion": f"Supply {col} for this line"})
    return out, exceptions


def _expand_horizontal_sizes(rows):
    """One row per size, whatever layout the supplier used.

    Carhartt writes a row per size; NuORDER brands spread Size 1..5 across the
    row. Reading only the first produced one SAP line per style-colour instead
    of one per size — 60 rows where the collection has 272.
    """
    from app.core.temp_template import expand_sizes, normalise_size

    out = []
    for row in rows:
        sizes = expand_sizes(row)
        if len(sizes) <= 1:
            out.append(row)
            continue
        for s in sizes:
            copy = dict(row)
            copy["Size"] = normalise_size(s["size"])
            if s.get("size_price") is not None:
                copy.setdefault("Wholesale Price EUR", s["size_price"])
            out.append(copy)
    return out


def build_creation_sheet(rows, *, season: str, collection: str = "Main",
                         vocab=None, lookup=None, table=None, temporary=None,
                         base_colour: bool = True,
                         uses_colour_codes: bool = True) -> dict:
    """Build the whole package.

    Returns the rows, one exception record per problem (carrying the SKU so the
    approval screen can group them), and a summary of what needs a human.
    """
    src_rows = [{normalise_header(k): v for k, v in r.items()}
                for r in _expand_horizontal_sizes(rows)]
    has_codes = any(str(r.get("Colour Code") or "").strip() not in ("", "nan", "None")
                    for r in src_rows)
    for r in src_rows:
        r["__brand_has_colour_codes"] = has_codes
    out_rows: list[dict] = []
    exceptions: list[dict] = []

    for src in src_rows:
        row, excs = build_row(src, season=season, collection=collection,
                              vocab=vocab, lookup=lookup, table=table,
                              temporary=temporary, base_colour=base_colour,
                              uses_colour_codes=uses_colour_codes)
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

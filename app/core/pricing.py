"""Pricing — Operations OS.

Flender has no single margin rule. Each brand has its own pricing strategy and
it is kept season to season, so the target is not a policy to be looked up but a
history to be learned and held to.

What is shared across brands, and what is not, was measured on two of Flender's
own pricing files — Carhartt WIP FW26 (8,824 SKUs) and Hiking Patrol SS27 (272):

    RRP = WHS x 2.10          identical in both, 100% of rows within 2%
    AED = USD x 3.68          identical
    USD = EUR x 1.115         identical
    Turkey = standard         identical (ratio exactly 1.000)
    margin on cost            Carhartt 53.0% USD / 47.6% EUR
                              Hiking Patrol 44.0% USD / 37.7% EUR

So the ratios are global constants and the margin is the brand's own. This
module learns the margin from a previous season and reports anything in the new
one that departs from it, rather than applying a target nobody agreed to.
"""
from __future__ import annotations

from statistics import median

# Measured identical across brands. Kept here so a change is visible and
# reviewable rather than buried in a formula.
RRP_OVER_WHS = 2.10
AED_PER_USD = 3.68
USD_PER_EUR = 1.115
TERRITORY_FACTORS = {"standard": 1.0, "turkey": 1.0}

# How far a SKU may sit from the brand's own history before it is raised.
WARN_MARGIN_DROP_PP = 3.0      # percentage points below the brand's median
REVIEW_MARGIN_DROP_PP = 6.0
WARN_COST_JUMP_PCT = 10.0      # supplier cost increase vs last season


def _num(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def margin_pct(wholesale, cost):
    """Margin on the wholesale price, the way Flender's own sheets compute it."""
    w, c = _num(wholesale), _num(cost)
    if w is None or c is None or w == 0:
        return None
    return (w - c) / w * 100.0


def learn_strategy(rows, *, wholesale_field="Flender WHS USD",
                   cost_field="Flender Cost Prices (EUR-USD)",
                   rrp_field="Flender RRP USD") -> dict:
    """Learn a brand's pricing strategy from a previous season.

    Returns the median margin and RRP multiple plus how tightly the brand
    actually holds them, because a brand that is consistent should be policed
    more strictly than one that is not.
    """
    margins, multiples = [], []
    for r in rows:
        m = margin_pct(r.get(wholesale_field), r.get(cost_field))
        if m is not None and -200 < m < 100:
            margins.append(m)
        w, rrp = _num(r.get(wholesale_field)), _num(r.get(rrp_field))
        if w and rrp:
            multiples.append(rrp / w)

    if not margins:
        return {"known": False, "reason": "no comparable prices in the history"}

    med = median(margins)
    spread = median([abs(m - med) for m in margins])
    return {
        "known": True,
        "margin_pct": round(med, 2),
        "margin_spread_pp": round(spread, 2),
        "rrp_multiple": round(median(multiples), 3) if multiples else RRP_OVER_WHS,
        "sample": len(margins),
        # A brand holding its margin to under half a point is being followed
        # deliberately; drift then means something rather than noise.
        "consistent": spread < 0.5,
    }


def check_row(row, strategy, previous=None, *,
              wholesale_field="Flender WHS USD",
              cost_field="Flender Cost Prices (EUR-USD)",
              rrp_field="Flender RRP USD") -> list[dict]:
    """Compare one priced SKU against the brand's strategy and its own history."""
    out: list[dict] = []
    sku = str(row.get("Item No.") or row.get("SKU") or "").strip()
    whs, cost, rrp = (_num(row.get(wholesale_field)), _num(row.get(cost_field)),
                      _num(row.get(rrp_field)))

    if whs is None or cost is None:
        out.append({"sku": sku, "field": "margin", "severity": "critical",
                    "reason": "wholesale price or cost is missing",
                    "suggestion": "Supply both before import"})
        return out

    m = margin_pct(whs, cost)
    if strategy.get("known") and m is not None:
        drop = strategy["margin_pct"] - m
        if drop >= REVIEW_MARGIN_DROP_PP:
            out.append({"sku": sku, "field": "margin", "severity": "manual_review",
                        "reason": f"margin {m:.1f}% against this brand's "
                                  f"{strategy['margin_pct']:.1f}%",
                        "suggestion": "Review RRP or the purchase price"})
        elif drop >= WARN_MARGIN_DROP_PP:
            out.append({"sku": sku, "field": "margin", "severity": "warning",
                        "reason": f"margin {m:.1f}%, {drop:.1f}pp under this "
                                  f"brand's usual {strategy['margin_pct']:.1f}%",
                        "suggestion": "Confirm this is intended"})

    if rrp and whs:
        mult = rrp / whs
        if abs(mult - RRP_OVER_WHS) / RRP_OVER_WHS > 0.02:
            out.append({"sku": sku, "field": "rrp", "severity": "warning",
                        "reason": f"RRP is {mult:.2f}x wholesale, not the usual "
                                  f"{RRP_OVER_WHS:.2f}x",
                        "suggestion": "Check the retail price"})

    if previous:
        old = _num(previous.get(cost_field))
        if old and cost and old > 0:
            jump = (cost - old) / old * 100
            if jump >= WARN_COST_JUMP_PCT:
                out.append({"sku": sku, "field": "cost", "severity": "warning",
                            "reason": f"supplier cost up {jump:.0f}% on last season",
                            "suggestion": "Check whether RRP should follow"})
    return out


def review_prices(rows, strategy, previous_by_sku=None) -> dict:
    """Run the commercial review over a priced collection."""
    previous_by_sku = previous_by_sku or {}
    exceptions: list[dict] = []
    for r in rows:
        sku = str(r.get("Item No.") or r.get("SKU") or "").strip()
        exceptions.extend(check_row(r, strategy, previous_by_sku.get(sku)))

    by_sev: dict[str, int] = {}
    for e in exceptions:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1
    flagged = {e["sku"] for e in exceptions}
    return {
        "strategy": strategy,
        "exceptions": exceptions,
        "summary": {"rows": len(rows), "by_severity": by_sev,
                    "passed": len(rows) - len(flagged)},
    }


# ── A first price list from the supplier's own prices ────────────────────────

PRICE_LIST_COLUMNS = [
    "Item Code", "Style", "Colour", "Item Description", "Supplier Currency",
    "Supplier WHS", "Supplier RRP", "Supplier WHS EUR", "Supplier WHS USD",
    "Supplier WHS AED", "Flender Cost Prices (EUR-USD)", "Flender WHS USD",
    "Flender RRP USD", "Flender WHS AED", "Flender RRP AED",
]

_TO_USD = {"USD": 1.0, "EUR": USD_PER_EUR, "AED": 1 / AED_PER_USD}


def draft_price_list(rows, item_code=None) -> dict:
    """One line per style and colour, priced from the supplier's sheet.

    The step after processing: the supplier's cost is converted with the
    constants measured above, and the Flender columns are left for a person
    to set (or a pricing file to fill) — a margin is the brand's own decision
    and is never invented here. A currency the sheet does not state is not
    converted.
    """
    out, seen = [], set()
    currencies = set()
    for r in rows:
        style = str(r.get("Item No.") or "").strip()
        colour = str(r.get("Color") or "").strip()
        code = (item_code(r) if item_code else "") or f"{style}-{colour.upper()}".strip("-")
        key = code.upper()
        if not style or key in seen:
            continue
        seen.add(key)
        if r.get("Wholesale Price EUR") not in (None, "") and str(r.get("Wholesale Price EUR")) != "nan":
            cur, whs = "EUR", _num(r.get("Wholesale Price EUR"))
            rrp = _num(r.get("Retail EUR"))
        else:
            cur = str(r.get("Supplier Wholesale Currency") or "").strip()
            whs = _num(r.get("Supplier Wholesale Price"))
            rrp = _num(r.get("Supplier Retail Price"))
        currencies.add(cur or "unstated")
        usd = whs * _TO_USD[cur] if whs is not None and cur in _TO_USD else None
        out.append({
            "Item Code": code, "Style": style, "Colour": colour,
            "Item Description": str(r.get("Item Description") or "").strip(),
            "Supplier Currency": cur or "not stated",
            "Supplier WHS": whs, "Supplier RRP": rrp,
            "Supplier WHS EUR": round(usd / USD_PER_EUR, 2) if usd is not None else None,
            "Supplier WHS USD": round(usd, 2) if usd is not None else None,
            "Supplier WHS AED": round(usd * AED_PER_USD, 2) if usd is not None else None,
            "Flender Cost Prices (EUR-USD)": round(usd, 2) if usd is not None else None,
            "Flender WHS USD": None, "Flender RRP USD": None,
            "Flender WHS AED": None, "Flender RRP AED": None,
        })
    exceptions = [{
        "sku": "", "field": "Flender prices", "subject": "this collection",
        "severity": "manual_review",
        "reason": "no Flender price sheet and no pricing history for this brand — "
                  "the list below carries the supplier's prices; set the Flender "
                  "WHS and RRP (or attach the pricing file and regenerate)",
        "suggestion": "Fill in the Flender columns in the downloaded sheet"}]
    unstated = [c for c in currencies if c == "unstated"]
    if unstated:
        exceptions.append({
            "sku": "", "field": "Supplier Currency", "subject": "not stated",
            "severity": "warning",
            "reason": "the supplier's price columns do not say which currency they are in, "
                      "so they were not converted",
            "suggestion": "Confirm the currency with the brand"})
    return {"columns": PRICE_LIST_COLUMNS, "rows": out, "exceptions": exceptions,
            "summary": {"rows": len(out), "draft": True,
                        "currencies": sorted(currencies)}}


# ── The price list SAP takes: one row per item code ──────────────────────────
#
# SAP's price lists, as named in Flender's SAP (Price Lists window). A price
# holds across sizes, so the list is per item code — style + colour — not per
# size: a size-level list only made the same number to check many times over.

SAP_PRICE_LISTS = [
    "Flender RRP AED", "Flender WHS AED", "Official RRP USD", "Official RRP EUR",
    "Official WHS EUR", "Flender RRP USD", "Flender WHS USD", "India Target RRP INR",
    "Flender Cost Prices (EUR-USD)", "Turkish Target RRP USD",
    "Flender Turkey RRP AED", "Flender Turkey WHS AED", "Flender RRP EUR",
    "Flender WHS EUR", "Flender Turkey RRP USD", "Flender Turkey WHS USD",
    "Official WHS USD", "Russia RRP USD", "Russia RRP EUR",
]

# Other spellings seen in Flender's own pricing files.
_PRICE_ALIASES = {"flender cost price": "Flender Cost Prices (EUR-USD)",
                  "flender cost prices": "Flender Cost Prices (EUR-USD)",
                  "flender cost price (eur-usd)": "Flender Cost Prices (EUR-USD)"}


def _price_key(header) -> str:
    return " ".join(str(header or "").split()).lower()


def price_list_columns(headers) -> dict[str, str]:
    """SAP price list -> the header that holds it in this file.

    Matched by name, ignoring case and stray spaces (HUF's file writes
    ' Flender RRP AED ').
    """
    wanted = {_price_key(n): n for n in SAP_PRICE_LISTS}
    wanted.update(_PRICE_ALIASES)
    found: dict[str, str] = {}
    for h in headers:
        sap = wanted.get(_price_key(h))
        if sap and sap not in found:
            found[sap] = h
    return found


def sap_price_list(rows, item_code) -> dict:
    """One row per item code with the SAP price lists the file carries.

    ``item_code(row)`` returns the row's item code (style + colour). When the
    sizes of one item disagree on a price, the first is kept and the item is
    flagged, because SAP holds one price per item.
    """
    headers = list(rows[0].keys()) if rows else []
    cols = price_list_columns(headers)
    order = [n for n in SAP_PRICE_LISTS if n in cols]
    items: dict[str, dict] = {}
    differs: dict[str, set] = {}
    for r in rows:
        code = item_code(r)
        if not code:
            continue
        item = items.setdefault(code, {
            "Item Code": code, "Style": str(r.get("Item No.") or "").strip(),
            "Colour": str(r.get("Color") or "").strip(),
            "Item Description": str(r.get("Item Description") or "").strip()})
        for name in order:
            v = _num(r.get(cols[name]))
            if v is None:
                continue
            if item.get(name) is None:
                item[name] = round(v, 2)
            elif abs(item[name] - v) > 0.01:
                differs.setdefault(code, set()).add(name)

    exceptions = [{
        "sku": code, "field": "price per size", "subject": code, "severity": "warning",
        "reason": f"sizes of {code} carry different {', '.join(sorted(names))} — "
                  f"the first size's price is used",
        "suggestion": "Check the price for this item"} for code, names in differs.items()]
    out_rows = [{c: it.get(c) for c in ["Item Code", "Style", "Colour", "Item Description"] + order}
                for it in items.values()]
    return {"columns": ["Item Code", "Style", "Colour", "Item Description"] + order,
            "rows": out_rows, "exceptions": exceptions,
            "summary": {"rows": len(out_rows), "price_lists": order,
                        "price_lists_missing": [n for n in SAP_PRICE_LISTS if n not in cols]}}

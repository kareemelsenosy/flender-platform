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
    "Item No.", "Colour", "Item Description", "Supplier Currency",
    "Supplier WHS", "Supplier RRP", "Supplier WHS EUR", "Supplier WHS USD",
    "Supplier WHS AED", "Flender Cost Prices (EUR-USD)", "Flender WHS USD",
    "Flender RRP USD", "Flender WHS AED", "Flender RRP AED",
]

_TO_USD = {"USD": 1.0, "EUR": USD_PER_EUR, "AED": 1 / AED_PER_USD}


def draft_price_list(rows) -> dict:
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
        key = (style.upper(), colour.upper())
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
            "Item No.": style, "Colour": colour,
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

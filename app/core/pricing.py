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

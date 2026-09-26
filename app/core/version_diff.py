"""Supplier version comparison — Operations OS.

A supplier collection is not static. A new file arrives mid-season with
styles added, colours dropped, prices moved, and reprocessing the whole
collection to find out what changed wastes everyone's time and risks
re-approving work that was already signed off.

This compares a new supplier file against the last one and reports only
Added, Removed and Changed, so the onboarding checks run on the difference
rather than the collection.

Products are compared at style-colour level, which is the level SAP creates
items at. Sizes are gathered underneath, so a style losing one size shows as a
change to that product rather than a removal.
"""
from __future__ import annotations

from app.core.product_identity import _get, style_key

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
UNCHANGED = "unchanged"

# What is worth reporting when it moves. A quantity that shifts is normal
# trading; a barcode or a price that shifts is a decision.
WATCHED_FIELDS = {
    "barcode": ("barcode", "bar_code", "ean", "gtin"),
    "wholesale_price": ("wholesale_price", "wholesale", "whs_price",
                        "wholesale_price_eur", "wholesale_usd"),
    "retail_price": ("retail_price", "rrp", "rrp_price", "msrp", "retail_eur"),
    "description": ("style_name", "name", "item_description", "description"),
    "material": ("material", "materials", "fabrication"),
    "country_of_origin": ("country_of_origin", "coo"),
}


def _value(row, names) -> str:
    return _get(row, *names).strip()


def _sizes(rows) -> set:
    out = set()
    for r in rows:
        size = _get(r, "size", "size_description", "size_code").strip()
        if size:
            out.add(size)
    return out


def _index(rows) -> "dict[str, list[dict]]":
    """Group rows into products, keyed at style-colour level."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = style_key(row)
        if key:
            grouped.setdefault(key, []).append(row)
    return grouped


def _label(rows) -> str:
    first = rows[0]
    style = _get(first, "item_code", "style_number", "item_no", "style_code")
    colour = _get(first, "color_name", "colour", "color")
    return f"{style} {colour}".strip() or style or "(unnamed)"


def compare_versions(previous, current) -> dict:
    """Compare two supplier files. Returns added / removed / changed records."""
    old, new = _index(previous), _index(current)

    added = [{"key": k, "label": _label(v), "sizes": sorted(_sizes(v))}
             for k, v in new.items() if k not in old]
    removed = [{"key": k, "label": _label(v), "sizes": sorted(_sizes(v))}
               for k, v in old.items() if k not in new]

    changed = []
    for key in sorted(set(old) & set(new)):
        before, after = old[key], new[key]
        differences = []

        for field, names in WATCHED_FIELDS.items():
            was, now = _value(before[0], names), _value(after[0], names)
            if was != now and (was or now):
                differences.append({"field": field, "was": was, "now": now})

        was_sizes, now_sizes = _sizes(before), _sizes(after)
        if was_sizes != now_sizes:
            differences.append({
                "field": "size_range",
                "was": "/".join(sorted(was_sizes)),
                "now": "/".join(sorted(now_sizes))})

        if differences:
            changed.append({"key": key, "label": _label(after),
                            "changes": differences})

    return {
        "added": sorted(added, key=lambda r: r["label"]),
        "removed": sorted(removed, key=lambda r: r["label"]),
        "changed": changed,
        "summary": {
            "previous_products": len(old),
            "current_products": len(new),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(set(old) & set(new)) - len(changed),
        },
    }


def describe(diff: dict, *, brand: str = "", season: str = "") -> list[str]:
    """Plain sentences for the change report."""
    s = diff["summary"]
    who = " ".join(p for p in (brand, season) if p) or "The supplier"
    lines = []
    if s["added"]:
        lines.append(f"{s['added']} products added")
    if s["removed"]:
        lines.append(f"{s['removed']} products removed — check orders and "
                     f"B2B before deactivating any of them")
    if s["changed"]:
        fields: dict[str, int] = {}
        for item in diff["changed"]:
            for c in item["changes"]:
                fields[c["field"]] = fields.get(c["field"], 0) + 1
        detail = ", ".join(f"{n} {f.replace('_', ' ')}"
                           for f, n in sorted(fields.items(), key=lambda x: -x[1]))
        lines.append(f"{s['changed']} products changed ({detail})")
    if not lines:
        return [f"{who} sent no changes — the collection is identical"]
    lines.insert(0, f"{who}: {s['current_products']} products against "
                    f"{s['previous_products']} last time")
    return lines

"""Style-level attributes for a collection — Operations OS.

The Product Attributes tool already assigns SAP attributes to a style: it
picks a product type from the group's controlled list and fills FABRIC, FIT,
STYLE and WEIGHT from SAP's own value lists. It expects a SAP product export.
A collection gives us a supplier order sheet instead, so this maps one onto
the other and reports what the AI was not confident about.

Attributes are decided per style, never per size or colour — a Detroit Jacket
is a work jacket whichever colour it comes in — so an order sheet of ten
thousand rows collapses to a few hundred decisions at most.
"""
from __future__ import annotations

import re

from app.core.attribute_taxonomy import (
    ITEM_GROUP_TO_MASTER, SAP_MASTER_GROUPS, master_for_item_group,
)

# Supplier wording -> SAP master group, checked against the group and then
# the product name. Order matters: "shirt jacket" is a jacket, "t-shirt" is
# not a shirt.
_MASTER_WORDS = [
    ("SHOES", r"shoe|sneaker|boot|sandal|slide|trainer|loafer|footwear"),
    ("JACKETS", r"jacket|coat|parka|puffer|anorak|blazer|vest|gilet|bomber|overshirt"),
    ("T-SHIRTS", r"t-?shirt|\btee\b|tees|tank|longsleeve|l/s tee|s/s tee"),
    ("SWEATS", r"hood|sweat|crew ?neck|cardigan|knit|sweater|jumper|fleece|pullover"),
    ("SHORTS", r"short"),
    ("PANTS", r"pant|jean|trouser|chino|denim|jogger|overall|cargo"),
    ("SHIRTS", r"shirt|polo|blouse"),
    ("ACCS", r"\bbag|backpack|cap\b|hat\b|beanie|sock|belt|wallet|scarf|glove|"
             r"keychain|necklace|ring\b|bracelet|earring|watch|sunglass|jewel|"
             r"accessor|gadget|underwear|boxer"),
]


def guess_master(item_group: str, name: str = "") -> str:
    """The SAP master group for a style, or '' when nothing says.

    A SAP item group is looked up directly. A supplier's own wording
    ("Pants Bib Non-Denim", "JACKETS", "OUTER") is matched by keyword — the
    group first, then the product name. Unknown stays unknown: guessing
    accessories for everything is how a cardigan was offered only bags.
    """
    key = str(item_group or "").strip().upper()
    if key in ITEM_GROUP_TO_MASTER or key in SAP_MASTER_GROUPS:
        return master_for_item_group(key)
    for text in (item_group, name):
        low = str(text or "").lower()
        if not low:
            continue
        for master, pattern in _MASTER_WORDS:
            if re.search(pattern, low):
                return master
    return ""

# Below this the assignment is a suggestion, not an answer.
CONFIDENT = 0.75


def _first(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() and str(v).strip().lower() != "nan":
            return str(v).strip()
    return ""


def styles_from_rows(rows, *, brand: str = "", season: str = "") -> list[dict]:
    """Collapse supplier rows into the style records the engine expects.

    Colour and size fall away; what survives is what decides an attribute —
    the name, the material, the group and any vendor description.
    """
    styles: dict[str, dict] = {}
    for row in rows:
        code = _first(row, "Item No.", "Style Number", "Style Code", "item_code")
        if not code:
            continue
        name = _first(row, "Name", "Item Description", "Description",
                      "Web Description 2", "style_name")
        group = _first(row, "Item Group", "Subcategory", "Main Waregroup",
                       "Category", "item_group")
        entry = styles.setdefault(code, {
            "style_code": code,
            "name": name,
            "material": _first(row, "Material", "Fabrication", "Materials"),
            "item_group": group,
            "master_group": guess_master(group, name),
            "gender": _first(row, "Sex", "Gender", "Department", "gender"),
            "vendor_category": _first(row, "Category", "Subcategory"),
            "season": season,
            "long_description": _first(row, "Item Description Long",
                                       "Long Description", "Product Notes"),
            "brand": brand,
            "rows": 0,
        })
        entry["rows"] += 1
        # Later rows fill gaps the first one left; suppliers are inconsistent
        # about which row of a style carries the full description.
        for key, names in (("material", ("Material", "Fabrication", "Materials")),
                           ("long_description", ("Item Description Long",
                                                 "Long Description", "Product Notes"))):
            if not entry[key]:
                entry[key] = _first(row, *names)
    return list(styles.values())


def _enrich_all(styles, enrich, workers: int, on_progress):
    """Yield (style, result | exception) as each style finishes.

    Styles are independent, so they run side by side: a model call takes
    seconds and a collection has hundreds of styles, which one at a time took
    long enough that people gave up waiting. Progress is reported from this
    thread, so the callback may touch the database and may raise to stop
    the run (a cancel); styles not yet started are then abandoned.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(styles)
    if workers <= 1:
        for index, style in enumerate(styles, start=1):
            if on_progress is not None:
                on_progress(index - 1, total)
            try:
                yield style, enrich(style)
            except Exception as exc:
                yield style, exc
        if on_progress is not None and total:
            on_progress(total, total)
        return

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(enrich, s): s for s in styles}
        for done, fut in enumerate(as_completed(futures), start=1):
            style = futures[fut]
            try:
                yield style, fut.result()
            except Exception as exc:
                yield style, exc
            if on_progress is not None:
                on_progress(done, total)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def build_attribute_package(styles, enrich, *, vocab_note: str = "",
                            on_progress=None, workers: int = 1) -> dict:
    """Run the attribute assignment and separate the confident from the rest.

    ``enrich`` is injected so the package can be built and tested without
    calling a model.
    """
    rows, exceptions = [], []
    order = {s["style_code"]: i for i, s in enumerate(styles)}
    for style, result in _enrich_all(styles, enrich, workers, on_progress):
        if isinstance(result, Exception):            # one bad style, not a dead run
            exceptions.append({
                "sku": style["style_code"], "field": "product_type",
                "subject": style["style_code"], "severity": "critical",
                "reason": f"attributes could not be assigned ({type(result).__name__})",
                "suggestion": "Assign the product type by hand"})
            continue

        merged = {**style, **result}
        rows.append(merged)

        pt = result.get("product_type")
        confidence = float(result.get("confidence") or 0)
        if not pt:
            exceptions.append({
                "sku": style["style_code"], "field": "product_type",
                "subject": style["style_code"], "severity": "manual_review",
                "reason": f"no product type could be chosen for "
                          f"'{style.get('name') or style['style_code']}'",
                "suggestion": "Choose the SAP product type"})
        elif confidence < CONFIDENT:
            exceptions.append({
                "sku": style["style_code"], "field": "product_type",
                "subject": style["style_code"], "severity": "warning",
                "reason": f"{pt} proposed for "
                          f"'{style.get('name') or style['style_code']}' "
                          f"with {confidence:.0%} confidence",
                "suggestion": "Confirm the product type"})

    rows.sort(key=lambda r: order.get(r.get("style_code"), 0))
    columns = ["style_code", "name", "item_group", "master_group", "gender",
               "material", "product_type", "confidence",
               "FABRIC", "FIT", "STYLE", "WEIGHT"]
    return {
        "columns": columns,
        "rows": [{c: _flatten(r.get(c)) for c in columns} for r in rows],
        "exceptions": exceptions,
        "summary": {"styles": len(styles), "assigned": len(rows),
                    "note": vocab_note},
    }


def _flatten(value):
    """STYLE comes back as a list; SAP wants one cell."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return "" if value is None else value

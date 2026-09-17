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

from app.core.attribute_taxonomy import master_for_item_group

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
        code = _first(row, "Style Number", "Item No.", "Style Code", "item_code")
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
            "master_group": master_for_item_group(group),
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


def build_attribute_package(styles, enrich, *, vocab_note: str = "") -> dict:
    """Run the attribute assignment and separate the confident from the rest.

    ``enrich`` is injected so the package can be built and tested without
    calling a model.
    """
    rows, exceptions = [], []
    for style in styles:
        try:
            result = enrich(style)
        except Exception as exc:                     # one bad style, not a dead run
            exceptions.append({
                "sku": style["style_code"], "field": "product_type",
                "subject": style["style_code"], "severity": "critical",
                "reason": f"attributes could not be assigned ({type(exc).__name__})",
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

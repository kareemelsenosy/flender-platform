"""Which supplier column holds which field — visible and correctable.

The packages were derived from Carhartt's import sheet and read its column
names ("Item No.", "Color", "Wholesale Price EUR"). Another brand calls the
same things "Style Number", "Colour" and "Price", and without a mapping those
fields came through empty and the package stalled on errors nobody could fix.

So every file is mapped onto one set of standard fields before a package is
built: automatically where the header is unambiguous, from the brand's rules
where staff have said which column to use, and shown on the collection page
either way so a wrong guess is visible.

Matching is by whole normalised header, never fuzzy similarity — fuzzy
matching is how "Season" once became the barcode.
"""
from __future__ import annotations

import re

# (standard field, plain name, header spellings — most specific first)
FIELDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Item No.", "Style number",
     ("item no", "style number", "style no", "style code", "style nr",
      "article number", "article no", "article", "item number", "item code",
      "product code", "model number", "model", "style")),
    ("Color", "Colour name",
     ("color", "colour", "color name", "colour name", "color description",
      "colour description", "colorway name", "colourway name")),
    ("Colour Code", "Colour code",
     ("colour code", "color code", "color id", "colour id", "color number",
      "colour number", "colorway", "colourway")),
    ("Size", "Size", ("size", "size name", "size description", "size label")),
    ("Size Run", "Size run / scale code",
     ("size run", "size scale", "size code", "size group", "scale")),
    ("Barcode", "Barcode (EAN / UPC)",
     ("barcode", "ean", "upc", "gtin", "ean code", "upc code", "ean13", "upc a")),
    ("Item Description", "Product name",
     ("item description", "description", "style name", "product name", "name",
      "style description", "product description", "model name")),
    ("Item Group", "Product group",
     ("item group", "subcategory", "sub category", "category", "product group",
      "product type", "product category")),
    ("Sex", "Gender", ("sex", "gender", "gender description")),
    ("Material", "Material / composition",
     ("material", "composition", "fabric", "fabrication", "materials",
      "fabric composition")),
    ("Wholesale Price EUR", "Wholesale price",
     ("wholesale price eur", "wholesale price", "wholesale", "whs price", "whs",
      "whsl", "cost price", "purchase price", "net price", "price")),
    ("Retail EUR", "Retail price",
     ("retail eur", "retail price", "rrp", "msrp", "srp", "retail")),
    ("Duty Position", "HS code",
     ("duty position", "hs code", "hs", "hts", "hts code", "hs tariff",
      "tariff code", "tariff", "commodity code", "customs code", "hs number")),
    ("Country of Origin", "Country of origin",
     ("country of origin", "coo", "origin", "made in", "country")),
    ("Brand", "Brand", ("brand", "brand name")),
]

FIELD_NAMES = [f for f, _n, _a in FIELDS]
PLAIN_NAMES = {f: n for f, n, _a in FIELDS}

# A brand rule saying "this brand's files do not have this field" — so a
# column that merely looks right is not picked up automatically.
NONE = "-"

# The fields a package cannot be built without — shown first, and a missing
# one is called out on the collection page.
ESSENTIAL = ("Item No.", "Color", "Size", "Barcode", "Item Description")


def norm(header) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(header or "").lower()).strip()


def detect(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, dict]:
    """Map each standard field to a header in this file.

    Returns ``{field: {"header": str, "source": str}}`` where source is
    ``brand rule`` (staff said so), ``same name``, ``detected`` or ``missing``.
    A header is used for at most one field.
    """
    overrides = overrides or {}
    by_norm: dict[str, str] = {}
    for h in headers:
        if str(h).strip():
            by_norm.setdefault(norm(h), str(h))

    taken: set[str] = set()
    out: dict[str, dict] = {}

    # Brand rules first, then exact standard names, then aliases.
    for field in FIELD_NAMES:
        wanted = overrides.get(field)
        if wanted == NONE:
            out[field] = {"header": "", "source": "brand rule",
                          "note": "brand rule: not in this brand's files"}
            continue
        if wanted:
            real = by_norm.get(norm(wanted))
            out[field] = ({"header": real, "source": "brand rule"} if real else
                          {"header": "", "source": "missing",
                           "note": f"brand rule says “{wanted}” but this file has no such column"})
            if real:
                taken.add(real)
    for field in FIELD_NAMES:
        if field in out:
            continue
        real = by_norm.get(norm(field))
        if real and real not in taken:
            out[field] = {"header": real, "source": "same name"}
            taken.add(real)
    for field, _plain, aliases in FIELDS:
        if field in out:
            continue
        for alias in aliases:
            real = by_norm.get(alias)
            if real and real not in taken:
                out[field] = {"header": real, "source": "detected"}
                taken.add(real)
                break
        else:
            out[field] = {"header": "", "source": "missing"}
    return out


def apply(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    """Copy each mapped column into its standard field name.

    Originals are kept, so anything already reading the supplier's own names
    keeps working; a standard field that already holds a value is never
    overwritten.
    """
    pairs = [(f, m["header"]) for f, m in mapping.items()
             if m.get("header") and m["header"] != f]
    if not pairs:
        return rows
    out = []
    for row in rows:
        new = dict(row)
        for field, header in pairs:
            current = new.get(field)
            if current is None or str(current).strip().lower() in ("", "nan", "none"):
                new[field] = row.get(header)
        out.append(new)
    return out

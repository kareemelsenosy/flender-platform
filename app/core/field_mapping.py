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
      "single sku", "kom code", "article number", "article no", "article",
      "item number", "item code", "product code", "model number", "model",
      "style", "no")),
    ("Color", "Colour name",
     ("color", "colour", "colors", "colours", "color name", "colour name",
      "color description", "colour description", "colorway name",
      "colourway name", "finishing")),
    ("Colour Code", "Colour code",
     ("colour code", "color code", "colour no", "color no", "color id",
      "colour id", "color number", "colour number", "colorway", "colourway")),
    ("Size", "Size",
     ("size", "size name", "size description", "size label", "sizes",
      "size range", "size group", "lot size")),
    ("Size Run", "Size run / scale code",
     ("size run", "size scale", "size code", "scale", "sr")),
    ("Barcode", "Barcode (EAN / UPC)",
     ("barcode", "ean", "ean code", "eancode", "upc", "gtin", "upc code",
      "upc number", "ean number", "barcode number", "ean13", "upc a")),
    ("Item Description 2", "Item description 2",
     ("item description 2", "description 2", "web description 2",
      "style name 2", "name 2")),
    ("Item Description", "Product name",
     ("item description", "description", "style name", "product name", "name",
      "article name", "style description", "product description",
      "model name", "item")),
    ("Item Group", "Product group",
     ("item group", "subcategory", "sub category", "category 2", "category",
      "category 1", "main category", "product group", "product type",
      "product category", "family")),
    ("Sex", "Gender", ("sex", "gender", "gender description")),
    ("Material", "Material / composition",
     ("material", "material composition", "material description",
      "composition", "fabric comp", "fabric composition", "fabric",
      "fabrication", "materials", "style detail content description",
      "style content description", "content description", "fabric content")),
    ("Wholesale Price EUR", "Wholesale price",
     ("wholesale price eur", "wholesale price", "wholesale", "whs price",
      "whs", "whsl", "ws", "w s", "dt price", "cost price", "purchase price",
      "net price", "price")),
    ("Retail EUR", "Retail price",
     ("retail eur", "retail price", "rrp", "msrp", "srp", "retail")),
    ("Duty Position", "HS code",
     ("duty position", "hs code", "hs", "hts", "hts code", "hs tariff",
      "tariff code", "tariff", "commodity code", "customs code", "hs number",
      "rm hs number", "hs code number")),
    ("Country of Origin", "Country of origin",
     ("country of origin", "coo", "c o o", "origin", "made in", "country")),
    ("Brand", "Brand", ("brand", "brand name")),
]

# Price columns carry their currency in the header ("WHS AED", "Wholesale
# (USD)", "EUR RETAIL"). The currency is stripped for matching and remembered,
# because a price in AED must never land in a column SAP reads as EUR.
CURRENCIES = ("eur", "eu", "usd", "aed", "gbp", "chf", "dkk", "sek", "nok",
              "jpy", "krw", "sar", "kwd")
PRICE_FIELDS = ("Wholesale Price EUR", "Retail EUR")

FIELD_NAMES = [f for f, _n, _a in FIELDS]
PLAIN_NAMES = {f: n for f, n, _a in FIELDS}

# A brand rule saying "this brand's files do not have this field" — so a
# column that merely looks right is not picked up automatically.
NONE = "-"

# The fields a package cannot be built without — shown first, and a missing
# one is called out on the collection page.
ESSENTIAL = ("Item No.", "Color", "Size", "Barcode", "Item Description")


def norm(header) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(header or "").lower()).strip()
    # "M.S.R.P." -> "msrp", "W/S" stays "w s" (two letters are an alias).
    text = re.sub(r"\b(?:[a-z] ){3,}[a-z]\b",
                  lambda m: m.group(0).replace(" ", ""), text)
    return text


def currency_of(header) -> str:
    """The currency a price header names, upper-case, or ''."""
    words = norm(header).split()
    for w in words:
        if w in CURRENCIES:
            return "EUR" if w == "eu" else w.upper()
    return ""


def _strip_currency(text: str) -> str:
    return " ".join(w for w in text.split() if w not in CURRENCIES)


def detect(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, dict]:
    """Map each standard field to a header in this file.

    Returns ``{field: {"header": str, "source": str}}`` where source is
    ``brand rule`` (staff said so), ``same name``, ``detected`` or ``missing``.
    A header is used for at most one field.
    """
    overrides = overrides or {}
    by_norm: dict[str, str] = {}
    for h in headers:
        if str(h).strip() and not str(h).startswith("(unnamed"):
            by_norm.setdefault(norm(h), str(h))
    # Price headers also answer to their name without the currency.
    for h in headers:
        stripped = _strip_currency(norm(h))
        if stripped and stripped != norm(h) and currency_of(h):
            by_norm.setdefault(stripped, str(h))

    taken: set[str] = set()
    out: dict[str, dict] = {}

    # Brand rules first, then exact standard names, then aliases. A rule may
    # name a column per file ("Description | Style Name"); a rule naming a
    # column this file does not have falls back to detection for this file,
    # so a rule written for the product list never blanks the order sheet.
    for field in FIELD_NAMES:
        wanted = overrides.get(field)
        if wanted == NONE:
            out[field] = {"header": "", "source": "brand rule",
                          "note": "brand rule: not in this brand's files"}
            continue
        if wanted:
            for option in [w.strip() for w in str(wanted).split("|") if w.strip()]:
                real = by_norm.get(norm(option))
                if real and real not in taken:
                    out[field] = {"header": real, "source": "brand rule"}
                    taken.add(real)
                    break
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

    # Last resort, by the words in the header: HUF's "UPC Number" and
    # "RM HS Number" carry the barcode and HS code under names no list holds.
    for field, words in (("Barcode", {"upc", "ean", "gtin", "barcode"}),
                         ("Duty Position", {"hs", "hts", "tariff"}),
                         ("Material", {"content", "composition"})):
        if out[field]["header"]:
            continue
        hits = [h for h in headers if h not in taken and words & set(norm(h).split())]
        # A description beats a code: "Style Detail Content Desc" (100% NYLON)
        # over "Style Content" (NYLN01).
        hits.sort(key=lambda h: 0 if re.search(r"desc|name", str(h), re.I) else 1)
        if hits:
            out[field] = {"header": str(hits[0]), "source": "detected"}
            taken.add(str(hits[0]))

    # A style code column the aliases did not name ("KOM-Code") — a header
    # that is just "<something> code" and not a colour, HS, EAN or size code.
    if not out["Item No."]["header"]:
        for h in headers:
            n = norm(h)
            if (n.endswith(" code") and h not in taken
                    and not re.search(r"colou?r|hs|hts|ean|upc|size|zip|post|country|tariff|group", n)):
                out["Item No."] = {"header": str(h), "source": "detected"}
                taken.add(str(h))
                break

    # Only a price that says it is in EUR may fill a EUR field. Flender's
    # suppliers mostly price in AED or USD, and a header that names no
    # currency at all ("Price", "WS") cannot be assumed to be EUR.
    for field in PRICE_FIELDS:
        header = out[field]["header"]
        if not header:
            continue
        cur = currency_of(header) or ("EUR" if norm(header) == norm(field) else "")
        out[field]["currency"] = cur or "unstated currency"
        if cur != "EUR":
            out[field]["note"] = (f"prices in {cur}, not EUR" if cur else
                                  "the column does not say which currency — not used as EUR")
    return out


def foreign_price(mapping: dict[str, dict], field: str = "Wholesale Price EUR") -> str:
    """The currency of a price mapped onto a EUR field, when it is not EUR."""
    cur = (mapping.get(field) or {}).get("currency", "")
    return cur if cur and cur != "EUR" else ""


def apply(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    """Copy each mapped column into its standard field name.

    Originals are kept, so anything already reading the supplier's own names
    keeps working; a standard field that already holds a value is never
    overwritten.
    """
    # A price in another currency is kept under its own name, never copied
    # into the EUR column SAP reads.
    pairs = [(f, m["header"]) for f, m in mapping.items()
             if m.get("header") and m["header"] != f
             and not (f in PRICE_FIELDS and foreign_price(mapping, f))]
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

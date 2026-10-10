"""Supplier email intake — Operations OS Phase 1.

One forwarded supplier email becomes a structured Collection Job: every
attachment is classified, brand and season are detected, and the system reports
what arrived and what is missing *before* anything is generated.

Everything here is pure and deterministic (no I/O, no DB) so the classification
rules can be unit-tested and corrected as new supplier formats turn up. The
rules are deliberately conservative: when a file cannot be confidently placed it
becomes ``OTHER`` and the intake report says so, rather than being guessed into
the wrong package.
"""
from __future__ import annotations

import os
import re

from app.core.product_identity import count_style_only, count_styles, line_key

# ── File kinds ───────────────────────────────────────────────────────────────
ORDER_SHEET = "order_sheet"
PRICE_LIST = "price_list"
CATALOG = "catalog"
IMAGES = "images"
# The split half of a collection some brands send: the order sheet carries
# what was bought, a separate product list carries the HS codes, origin and
# UPCs (HUF's "UPC HS code" excel, Carhartt's product data). Optional — most
# brands put everything in the order sheet — so it is never reported missing.
PRODUCT_LIST = "product_list"
OTHER = "other"

# The four inputs a complete collection intake is expected to carry. Anything
# missing is reported as a gap, not silently tolerated.
EXPECTED_KINDS = (ORDER_SHEET, PRICE_LIST, CATALOG, IMAGES)

KIND_LABELS = {
    ORDER_SHEET: "Order sheet",
    PRICE_LIST: "Price list",
    CATALOG: "Catalog",
    IMAGES: "Image package",
    PRODUCT_LIST: "Product list (HS codes)",
    OTHER: "Other",
}

SPREADSHEET_EXTS = {".xlsx", ".xls", ".csv"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}

# Filename keywords, checked against the normalised stem. Order matters inside
# each tuple only for readability; scoring below picks the strongest signal.
_PRICE_WORDS = ("pricelist", "price list", "price", "prices", "rrp", "msrp",
                "wholesale", "whsl", "cost")
_ORDER_WORDS = ("ordersheet", "order sheet", "order form", "orderform",
                "importsheet", "import sheet",
                "order", "buy sheet", "buysheet", "booking", "preorder",
                "pre order", "reorder", "re order")
_CATALOG_WORDS = ("lookbook", "look book", "catalogue", "catalog",
                  "linesheet", "line sheet", "linelist", "line list")
_PRODUCT_LIST_WORDS = ("hs code", "hs codes", "hscode", "hts", "upc", "tariff",
                       "customs", "product list", "productlist", "product data",
                       "master list", "masterlist", "item list", "itemlist",
                       "material list", "commodity")
_IMAGE_WORDS = ("image", "images", "photo", "photos", "picture", "pictures",
                "packshot", "packshots", "visual", "visuals", "assets")


def _norm_name(filename: str) -> str:
    """Lowercase stem with separators flattened to single spaces."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return re.sub(r"[\s_\-.]+", " ", stem.lower()).strip()


def _hits(text: str, words: tuple[str, ...]) -> int:
    """Length of the longest matching keyword — longer matches are stronger
    evidence ("pricelist" beats an incidental "price" inside another word)."""
    return max((len(w) for w in words if w in text), default=0)


def classify_attachment(filename: str) -> str:
    """Classify one attachment into an Operations OS file kind.

    Extension decides the family; filename keywords decide within it. A .zip is
    always the image package, a bare image file likewise; a PDF is a catalog
    unless it names itself an order sheet or price list.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    name = _norm_name(filename)

    if ext in ARCHIVE_EXTS or ext in IMAGE_EXTS:
        return IMAGES

    price, order, catalog, images = (
        _hits(name, _PRICE_WORDS), _hits(name, _ORDER_WORDS),
        _hits(name, _CATALOG_WORDS), _hits(name, _IMAGE_WORDS),
    )

    if ext == ".pdf":
        # A PDF line sheet still reads as a catalog for intake purposes; the
        # PDF ingest can pull a product table out of it either way.
        if price > order and price > catalog:
            return PRICE_LIST
        if order > catalog:
            return ORDER_SHEET
        return CATALOG

    if ext in SPREADSHEET_EXTS:
        # "SU27 UPC HS code.xlsx" — the half of the data that carries HS codes.
        # A bare "HS" only counts as a whole word: it is inside too many others.
        product_list = max(_hits(name, _PRODUCT_LIST_WORDS),
                           2 if re.search(r"\bhs\b", name) else 0)
        if product_list and product_list >= order and product_list >= price:
            return PRODUCT_LIST
        if images and images > price and images > order:
            # e.g. "SS27 image list.xlsx" — an index of photos, not products.
            return OTHER
        if price > order:
            return PRICE_LIST
        if order or catalog:
            return ORDER_SHEET if order >= catalog else CATALOG
        # An unnamed spreadsheet from a supplier is overwhelmingly the order
        # sheet; the intake report shows the assumption so it can be corrected.
        return ORDER_SHEET

    return OTHER


# ── Brand & season ───────────────────────────────────────────────────────────

def _known_brands() -> list[str]:
    """Brands the platform already recognises, longest first.

    Reuses the image searcher's registry rather than keeping a second list —
    a brand that is registered there is one we can already work with, and the
    two must not drift apart.
    """
    try:
        from app.core.searcher import BRAND_DOMAINS, BRAND_PLAYBOOKS
        names = set(BRAND_DOMAINS) | set(BRAND_PLAYBOOKS)
    except Exception:
        names = set()
    return sorted(names, key=len, reverse=True)


# The registry stores brands lowercased for matching, but title-casing them for
# display mangles the ones that are initialisms. Only the exceptions live here.
_BRAND_DISPLAY = {
    "carhartt wip": "Carhartt WIP",
    "cp company": "CP Company",
    "c.p. company": "C.P. Company",
    "huf": "HUF",
    "bape": "BAPE",
    "ami paris": "AMI Paris",
    "hoka": "HOKA",
    "hoka one one": "HOKA ONE ONE",
    "asics": "ASICS",
    "puma": "PUMA",
    "fila": "FILA",
    "obey": "OBEY",
    "on running": "On Running",
    "thisisneverthat": "thisisneverthat",
    "dsquared2": "DSQUARED2",
    "off-white": "Off-White",
    "acl": "ACL",
    "twojeys": "TwoJeys",
    "rip n dip": "Rip N Dip",
    "ripndip": "RIPNDIP",
    "arte antwerp": "Arte Antwerp",
}


def brand_display_name(brand: str) -> str:
    """Presentable form of a registry brand key."""
    key = (brand or "").strip().lower()
    if key in _BRAND_DISPLAY:
        return _BRAND_DISPLAY[key]
    return brand.title() if brand.islower() else brand


def detect_brands(*texts: str) -> list[str]:
    """Every registered brand named in ``texts``, most specific first.

    A brand whose name is contained in a longer match is dropped, so
    "Carhartt WIP" does not also report "Carhartt". More than one survivor
    means a genuinely multi-brand file — suppliers do send these, and picking
    one silently would file the whole collection under the wrong brand.
    """
    haystack = " ".join(_norm_name(t) if "." in (t or "") else (t or "").lower()
                        for t in texts)
    haystack = re.sub(r"[\s_\-.]+", " ", haystack)

    hits: list[str] = []
    for brand in _known_brands():
        # Compare on the same flattened form the haystack uses, so a registry
        # key like "c.p. company" still matches "C.P. Company" in a subject.
        needle = re.sub(r"[\s_\-.]+", " ", brand.lower()).strip()
        if needle and needle in haystack:
            hits.append(needle)

    distinct = [b for b in hits
                if not any(b != other and b in other for other in hits)]
    seen: set[str] = set()
    out: list[str] = []
    for b in distinct:
        name = brand_display_name(b)
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def detect_brand(*texts: str) -> str | None:
    """The single most specific registered brand named in ``texts``."""
    found = detect_brands(*texts)
    return found[0] if found else None


_SEASON_CODES = {
    "ss": "SS", "sp": "SS", "spring": "SS", "summer": "SS",
    # HUF splits the year four ways (SP/SU/FA/HO) and SU27 is a different drop
    # from SP27, so its own letters are kept rather than folded into SS/FW.
    "su": "SU", "fa": "FA",
    "fw": "FW", "aw": "FW", "fall": "FW", "autumn": "FW", "winter": "FW",
    "ho": "HO", "holiday": "HO", "resort": "RE", "pf": "PF", "prefall": "PF",
}


def detect_season(*texts: str) -> str | None:
    """Normalise a season reference to a short code such as ``SS27``.

    Handles the shapes suppliers actually send: ``SS27``, ``FW 26``,
    ``Spring/Summer 2027``, ``HOLIDAY 26``, ``Pre-Fall 2026``.
    """
    blob = " ".join(t or "" for t in texts)
    blob = re.sub(r"[_\-/]+", " ", blob).lower()

    # Compact form first: SS27, FW 26, SP2027.
    m = re.search(r"\b(ss|sp|su|fa|fw|aw|ho|pf)\s?((?:20)?\d{2})\b", blob)
    if m:
        return f"{_SEASON_CODES[m.group(1)]}{m.group(2)[-2:]}"

    # Spelled-out form: "spring summer 2027", "pre-fall 26".
    m = re.search(
        r"\b(spring|summer|fall|autumn|winter|holiday|resort|pre ?fall)\b"
        r"[^0-9]{0,16}((?:20)?\d{2})\b", blob)
    if m:
        word = m.group(1).replace(" ", "")
        return f"{_SEASON_CODES.get(word, 'SS')}{m.group(2)[-2:]}"
    return None


# Columns a supplier names its brand or season in. "Division" is HUF's — in
# the UPC HS list it holds the brand, in the order form it holds "Apparel" —
# so a division value only counts when it is a brand we recognise.
_BRAND_COLUMNS = ("brand", "brand name", "brand description", "label")
_WEAK_BRAND_COLUMNS = ("division", "vendor", "vendor name", "manufacturer",
                       "supplier", "supplier name")
_SEASON_COLUMNS = ("season", "season code", "season name", "collection",
                   "collection name", "collection code", "delivery")


def _header_key(header) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(header or "").lower()).strip()


def _top_values(rows: list[dict], header: str, limit: int = 5) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        v = str(row.get(header) or "").strip()
        if v and v.lower() not in ("nan", "none", "null"):
            counts[v] = counts.get(v, 0) + 1
    return [v for v, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


def detect_identity_from_rows(rows: list[dict]) -> dict:
    """Brand and season read from inside the supplier file.

    Filenames are not reliable — "FW27 Pre EUR Importsheet.xlsx" names no
    brand — but the rows usually do: Carhartt writes CARHARTT WIP in a Brand
    column and "SS27 D1" under Collection, HUF writes the brand under Division
    and the season under Collection and Collection Name.

    Returns ``{"brand", "brand_source", "season", "season_source"}``; a value
    is None when the file does not say.
    """
    out = {"brand": None, "brand_source": "", "season": None, "season_source": ""}
    if not rows:
        return out
    headers = list(rows[0].keys())

    for strong in (True, False):
        if out["brand"]:
            break
        wanted = _BRAND_COLUMNS if strong else _WEAK_BRAND_COLUMNS
        for h in headers:
            if _header_key(h) not in wanted:
                continue
            values = _top_values(rows, h)
            known = detect_brands(*values)
            if known:
                out["brand"], out["brand_source"] = known[0], f"the {h} column"
                break
            if strong and values and not re.fullmatch(r"[\d\s.,-]+", values[0]):
                # An explicit Brand column is trusted even for a label the
                # registry has not met yet.
                v = values[0]
                out["brand"] = brand_display_name(v.lower()) if v.isupper() else v
                out["brand_source"] = f"the {h} column"
                break

    for h in headers:
        if _header_key(h) not in _SEASON_COLUMNS:
            continue
        season = detect_season(*_top_values(rows, h, limit=3))
        if season:
            out["season"], out["season_source"] = season, f"the {h} column"
            break
    return out


# ── Intake analysis ──────────────────────────────────────────────────────────

def analyse_rows(rows: list[dict]) -> dict:
    """Summarise parsed supplier rows: volume plus the data-quality gaps.

    Reports counts, never opinions — the review engine decides what is an
    exception. Missing barcodes and missing prices are the two gaps that most
    often block SAP creation, so they are counted per sellable line.
    """
    rows = rows or []
    skus = 0
    seen_lines: set[str] = set()
    colours: set[str] = set()
    missing_barcode = 0
    missing_wholesale = 0
    missing_retail = 0
    missing_size = 0

    for row in rows:
        key = line_key(row)
        if key:
            if key in seen_lines:
                continue
            seen_lines.add(key)
        skus += 1

        colour = (row.get("color_name") or row.get("color_code") or "").strip()
        if colour:
            colours.add(colour.lower())
        if not str(row.get("barcode") or "").strip():
            missing_barcode += 1
        if row.get("wholesale_price") in (None, "", 0):
            missing_wholesale += 1
        if row.get("retail_price") in (None, "", 0):
            missing_retail += 1
        if not str(row.get("size") or "").strip():
            missing_size += 1

    return {
        # Styles collapse colour; colour styles are the master records per
        # colour. Reporting the latter as "styles" multiplied a collection by
        # the number of colourways it happens to come in.
        "styles": count_style_only(rows),
        "colour_styles": count_styles(rows),
        "distinct_colours": len(colours),
        "skus": skus,
        "missing_barcode": missing_barcode,
        "missing_wholesale_price": missing_wholesale,
        "missing_retail_price": missing_retail,
        "missing_size": missing_size,
    }


def missing_kinds(kinds: list[str]) -> list[str]:
    """Which of the four expected inputs did not arrive."""
    present = set(kinds or [])
    return [k for k in EXPECTED_KINDS if k not in present]


def build_intake_report(brand: str | None, season: str | None,
                        files: list[dict], analysis: dict,
                        all_brands: list[str] | None = None) -> dict:
    """Assemble the intake summary shown in the UI and sent as the status email.

    ``files`` is a list of ``{"filename": ..., "kind": ...}``. The report is
    plain data so the same structure can render as HTML, email or JSON.
    """
    received = sorted({f["kind"] for f in files if f.get("kind") != OTHER})
    absent = missing_kinds(received)

    warnings: list[str] = []
    if analysis.get("missing_barcode"):
        warnings.append(f"{analysis['missing_barcode']:,} lines without a barcode")
    if analysis.get("missing_wholesale_price"):
        warnings.append(
            f"{analysis['missing_wholesale_price']:,} lines without a purchase price")
    if analysis.get("missing_retail_price"):
        warnings.append(f"{analysis['missing_retail_price']:,} lines without an RRP")
    if analysis.get("missing_size"):
        warnings.append(f"{analysis['missing_size']:,} lines without a size")
    if not brand:
        warnings.append("Brand could not be detected from the email or filenames")
    if not season:
        warnings.append("Season could not be detected from the email or filenames")
    if all_brands and len(all_brands) > 1:
        warnings.append(
            f"{len(all_brands)} brands named in this collection "
            f"({', '.join(all_brands)}) — filed under {brand or all_brands[0]}; "
            "split it if they are separate collections")

    return {
        "brand": brand,
        "brands": all_brands or ([brand] if brand else []),
        "season": season,
        "received": received,
        "missing": absent,
        "files": files,
        "totals": analysis,
        "warnings": warnings,
        # Ready means: something parseable arrived and we know what collection
        # it belongs to. A missing image package does not block the data work.
        "ready_to_process": bool(analysis.get("skus")) and bool(brand),
    }


def format_intake_email(report: dict) -> str:
    """Plain-text intake summary — the first response the supplier email gets."""
    brand = report.get("brand") or "Unknown brand"
    season = report.get("season") or "unknown season"
    t = report.get("totals") or {}

    lines = [f"{brand} — {season}", ""]

    lines.append("Files received:")
    for kind in EXPECTED_KINDS:
        mark = "OK  " if kind in report.get("received", []) else "--  "
        lines.append(f"  {mark}{KIND_LABELS[kind]}")

    lines += ["", "Detected:",
              f"  {t.get('styles', 0):,} styles",
              f"  {t.get('colour_styles', 0):,} colour styles",
              f"  {t.get('skus', 0):,} SKUs"]

    if report.get("warnings"):
        lines += ["", "Needs attention:"]
        lines += [f"  - {w}" for w in report["warnings"]]

    lines += ["", "Processing has started." if report.get("ready_to_process")
              else "Processing is on hold until the missing inputs arrive."]
    return "\n".join(lines)

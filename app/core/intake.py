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

from app.core.product_identity import count_styles, line_key

# ── File kinds ───────────────────────────────────────────────────────────────
ORDER_SHEET = "order_sheet"
PRICE_LIST = "price_list"
CATALOG = "catalog"
IMAGES = "images"
OTHER = "other"

# The four inputs a complete collection intake is expected to carry. Anything
# missing is reported as a gap, not silently tolerated.
EXPECTED_KINDS = (ORDER_SHEET, PRICE_LIST, CATALOG, IMAGES)

KIND_LABELS = {
    ORDER_SHEET: "Order sheet",
    PRICE_LIST: "Price list",
    CATALOG: "Catalog",
    IMAGES: "Image package",
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
                "order", "buy sheet", "buysheet", "booking", "preorder",
                "pre order", "reorder", "re order")
_CATALOG_WORDS = ("lookbook", "look book", "catalogue", "catalog",
                  "linesheet", "line sheet", "linelist", "line list")
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
    m = re.search(r"\b(ss|sp|fw|aw|ho|pf)\s?((?:20)?\d{2})\b", blob)
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
        "styles": count_styles(rows),
        "colour_styles": len(colours),
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

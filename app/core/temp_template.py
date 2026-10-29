"""TEMP STANDARD TEMPLATE — Operations OS.

The step Flender's process tracker calls "TEMP": a supplier order form becomes
the SAP staging sheet of ``U_*`` fields that feeds item creation.

Derived from the Hiking Patrol SS27 pair — the NuORDER order form and the TEMP
sheet that was accepted — and verified row for row: 60 order rows expand to
exactly 272 TEMP rows across 26 styles, with no row unaccounted for on either
side.

Order forms lay sizes out horizontally (``Size 1``…``Size 5`` with a price
each), so one order row becomes one TEMP row per populated size. Fields the
order form simply does not carry — HS code, country of origin, gender — are
left empty and raised as exceptions rather than invented; they come from the
brand's history.
"""
from __future__ import annotations

import re

from app.core.base_color import propose as propose_base_color
from app.core.sap_creation import (
    base_colour_exception, normalise_header, season_code,
)

# The TEMP layout, in order, from the accepted Hiking Patrol sheet.
TEMP_COLUMNS = [
    "U_Season", "U_OSRow", "U_NRStatus", "U_CodeBars", "U_ESDate",
    "U_SuppCatNum", "U_Gender", "U_VCat", "U_ItmsGrpCod", "U_SCode",
    "U_SizeCode", "U_Webbeschreibung2", "U_BaseColor", "U_Material",
    "U_HS_Code", "U_COO", "U_VCName", "U_Webbeschreibung",
    "U_def_size_list_code", "U_Web_Color", "U_SortIndex",
    "U_webbeschreibung_gr", "U_LongDscp",
    "U_Prop1", "U_Prop2", "U_Prop3",
    "U_SProp1", "U_SProp2", "U_SProp3", "U_SProp4", "U_SProp5",
]

# Blank in the accepted sheet too — not gaps.
EXPECTED_BLANK = {"U_Material", "U_LongDscp", "U_Prop1", "U_Prop2", "U_Prop3",
                  "U_SProp1", "U_SProp2", "U_SProp3", "U_SProp4", "U_SProp5"}

# Only history or a human can supply these; the order form never carries them.
FROM_HISTORY = ["U_HS_Code", "U_COO", "U_Gender", "U_VCName"]

# Supplier size labels to the SAP spelling.
SIZE_ALIASES = {"OS": "onesize", "O/S": "onesize", "ONE SIZE": "onesize",
                "ONESIZE": "onesize", "UNI": "onesize", "U": "onesize",
                "OSFA": "onesize", "ONE SIZE FITS ALL": "onesize",
                "ONE SIZE FITS MOST": "onesize", "NS": "onesize", "TU": "onesize"}

# A size run maps to one SAP size-list code.
SIZE_LIST_CODES = {
    ("xs", "s", "m", "l", "xl"): "XS26XL",
    ("s", "m", "l", "xl"): "S24XL",
    ("xs", "s", "m", "l", "xl", "xxl"): "XS26XXL",
    ("onesize",): "ONESIZE",
}

# Words that stay upper-cased through title casing: the accepted sheet has
# "Edge LT Softshell", not "Edge Lt Softshell".
ACRONYMS = {"LT", "LS", "SS", "XL", "UV", "GTX", "DWR", "II", "III", "NY",
            "USA", "UK", "HP", "WIP", "3D", "2L", "3L"}

ONESIZE_SORT = 58000     # onesize sorts last, as in the accepted sheets
SORT_BASE, SORT_STEP = 1000, 500


# A DataFrame converted to dicts carries NaN, and str(nan) is "nan" — a
# non-empty string that would otherwise sail through every emptiness check and
# invent rows and values that were never in the supplier file.
# Pandas' missing date prints as 'NaT' — matched exactly, because 'NAT' is
# Dime's colour code for Natural.
_BLANKS = {"", "nan", "none", "null", "<na>"}


def is_blank(value) -> bool:
    """True for None, empty text, and the string forms of a missing value."""
    text = "" if value is None else str(value).strip()
    return value is None or text.lower() in _BLANKS or text == "NaT"


def normalise_size(size: str) -> str:
    """'OS' -> 'onesize'. Anything else keeps the supplier's spelling."""
    s = str(size or "").strip()
    return SIZE_ALIASES.get(s.upper(), s)


def smart_title(text: str, keep_upper=()) -> str:
    """Title case that leaves acronyms alone.

    ``keep_upper`` adds the brand's own words (HUF, EXO, KCUF, PO, FZ),
    learned from a finished sheet.
    """
    keep = ACRONYMS | {str(k).upper() for k in keep_upper}
    # Only shouting is re-cased: "OLD E EXO RING" becomes "Old E EXO Ring",
    # but Dime's own "Plaid Button up Shirt" is kept as the brand wrote it.
    if not str(text or "").isupper():
        return " ".join(str(text or "").split())
    words = str(text or "").split()
    out = []
    for w in words:
        core = re.sub(r"[^A-Za-z0-9]", "", w).upper()
        if core in keep:
            out.append(w.upper())
        else:
            out.append(w.title())
    return " ".join(out)


def web_colour(colour: str, keep_slash: bool = False) -> str:
    """A colour as SAP shows it: 'BLACK/WHITE' -> 'Black-White'; a spaced
    'Dark Navy / WIP H Brown' (Carhartt's own spelling) is kept, and so is
    every slash for a brand whose SAP keeps them (Komono)."""
    text = str(colour or "").strip()
    if not keep_slash:
        text = re.sub(r"(?<=\S)/(?=\S)", "-", text)
    return smart_title(text)


def size_list_code(sizes) -> "tuple[str, str]":
    """SAP size-list code for a run of sizes. Returns (code, reason)."""
    run = tuple(normalise_size(s).lower() for s in sizes if not is_blank(s))
    if not run:
        return "", "no sizes given"
    code = SIZE_LIST_CODES.get(run)
    if code:
        return code, f"size run {'/'.join(run)}"
    return "", f"size run {'/'.join(run)} has no SAP size-list code"


def sort_index(position: int, size: str) -> int:
    """Where a size sorts inside its style. One-size items sort last."""
    if normalise_size(size).lower() == "onesize":
        return ONESIZE_SORT
    return SORT_BASE + SORT_STEP * position


# SAP stores the country of origin as a two-letter code ("CN", "PK"); most
# order sheets write the name. Only countries Flender's brands produce in.
COUNTRY_CODES = {
    "china": "CN", "vietnam": "VN", "viet nam": "VN", "bangladesh": "BD",
    "india": "IN", "pakistan": "PK", "turkey": "TR", "türkiye": "TR",
    "portugal": "PT", "italy": "IT", "spain": "ES", "tunisia": "TN",
    "korea": "KR", "south korea": "KR", "republic of korea": "KR",
    "japan": "JP", "indonesia": "ID", "cambodia": "KH", "sri lanka": "LK",
    "myanmar": "MM", "thailand": "TH", "taiwan": "TW", "morocco": "MA",
    "egypt": "EG", "romania": "RO", "bulgaria": "BG", "poland": "PL",
    "lithuania": "LT", "germany": "DE", "france": "FR", "belgium": "BE",
    "mexico": "MX", "usa": "US", "united states": "US", "peru": "PE",
    "mauritius": "MU", "madagascar": "MG", "hong kong": "HK", "macedonia": "MK",
    "north macedonia": "MK", "laos": "LA", "philippines": "PH", "malaysia": "MY",
    "canada": "CA", "united states of america": "US", "uk": "GB",
    "united kingdom": "GB", "great britain": "GB", "england": "GB",
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL",
    "switzerland": "CH", "austria": "AT", "denmark": "DK", "sweden": "SE",
    "greece": "GR", "hungary": "HU", "czech republic": "CZ", "czechia": "CZ",
    "slovakia": "SK", "slovenia": "SI", "croatia": "HR", "serbia": "RS",
    "bosnia and herzegovina": "BA", "albania": "AL", "moldova": "MD",
    "ukraine": "UA", "estonia": "EE", "latvia": "LV", "ireland": "IE",
    "brazil": "BR", "colombia": "CO", "guatemala": "GT", "honduras": "HN",
    "el salvador": "SV", "nicaragua": "NI", "dominican republic": "DO",
    "haiti": "HT", "jordan": "JO", "ethiopia": "ET", "kenya": "KE",
    "south africa": "ZA", "lesotho": "LS",
    "australia": "AU", "new zealand": "NZ", "nepal": "NP", "uzbekistan": "UZ",
    "kyrgyzstan": "KG", "georgia": "GE", "armenia": "AM", "israel": "IL",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "prc": "CN", "p.r. china": "CN",
}


def country_code(value: str) -> str:
    """'China' -> 'CN'. A two-letter code passes through; unknown names stay."""
    v = str(value or "").strip()
    if is_blank(v):
        return ""
    if re.fullmatch(r"[A-Za-z]{2}", v):
        return v.upper()
    return COUNTRY_CODES.get(v.lower(), v)


def barcode(style: str, colour: str, size: str, supplier_size: bool = False) -> str:
    """U_CodeBars — style, colour and size run together, letters and digits.

    ``supplier_size``: the size as the supplier labels it — Dime's SAP holds
    DIMESU2625BLUOS where Hiking Patrol's holds HP0127302BLACKONESIZE.
    """
    raw = f"{style or ''}{colour or ''}{size if supplier_size else normalise_size(size)}"
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


_GENDER = {"m": "M", "men": "M", "mens": "M", "men's": "M", "male": "M",
           "man": "M", "herren": "M", "unisex": "U", "u": "U",
           "w": "W", "f": "W", "women": "W", "womens": "W", "women's": "W",
           "female": "W", "woman": "W", "ladies": "W", "damen": "W",
           "k": "K", "kids": "K", "children": "K", "youth": "K"}


# What a product's name says about its SAP item group, checked only among
# the groups a category is already known to map to.
_GROUP_WORDS = {
    "HOODYS": r"hood",
    "SWEATER": r"sweater|crew ?neck|jumper|cardigan|knit",
    "JACKETS": r"jacket|fleece|coat|parka|anorak|vest|gilet|shell|bomber",
    "LONGSL": r"\bl/?s\b|long ?sleeve|longsleeve",
    "T-SHIRTS": r"\btee\b|t-shirt",
    "POLOS": r"polo",
    "SHIRTS": r"(?<!t-)(?<!\bt )(?<!sweat)shirt",
    "PANTS": r"pant|jean|trouser|chino",
    "SHORTS": r"short",
    "HEAD": r"\bhat\b|\bcap\b|beanie|snapback",
    "SOCKS": r"sock",
    "BAGS": r"\bbag|backpack|tote",
    "BELT": r"belt",
    "WALLET": r"wallet|card ?holder",
}


def group_from_name(name: str, candidates) -> str:
    """The one candidate group the product name points to, or ''."""
    low = str(name or "").lower()
    hits = [g for g in candidates
            if _GROUP_WORDS.get(g.upper()) and re.search(_GROUP_WORDS[g.upper()], low)]
    if len(hits) > 1 and "JACKETS" in hits and "HOODYS" in hits:
        hits = ["HOODYS"] if "hood" in low else hits
    # A long-sleeve T-shirt is LONGSL, not T-SHIRTS.
    if len(hits) > 1 and "LONGSL" in hits and "T-SHIRTS" in hits:
        hits = [g for g in hits if g != "T-SHIRTS"]
    return hits[0] if len(hits) == 1 else ""


def product_words(name: str) -> list[str]:
    """The words of a product name that say what it is, most specific first.

    Dime names end with the product: 'Classic Veins LS T-Shirt' gives
    ['LS T-SHIRT', 'T-SHIRT']; 'Much Hoodie (HW)' gives ['MUCH HOODIE',
    'HOODIE']. A brand's finished TEMP teaches which of these maps to which
    SAP group, and the words come back next season under new names.
    """
    text = re.sub(r"\([^)]*\)", " ", str(name or "")).upper()
    words = re.findall(r"[A-Z0-9][A-Z0-9/&'-]*", text)
    if not words:
        return []
    out = [words[-1]]
    if len(words) > 1:
        out.insert(0, f"{words[-2]} {words[-1]}")
    return out


def group_for_product(name: str, group_map: dict) -> list[str]:
    """SAP group candidates from the product word of the name, or []."""
    for key in product_words(name):
        if key in group_map:
            found = group_map[key]
            return [found] if isinstance(found, str) else list(found)
    return []


def gender_code(value: str, brand_map=None) -> str:
    """'Men' -> 'M', 'Ladies' -> 'W', 'Unisex' -> 'U'. SAP holds one letter.

    ``brand_map`` is the brand's own spelling, learned from a finished TEMP
    (Komono: Ladies W, Men M, Unisex U, and an empty gender U).
    """
    v = "" if is_blank(value) else str(value).strip()
    brand_map = {str(k).lower(): val for k, val in (brand_map or {}).items()}
    if v.lower() in brand_map:
        return brand_map[v.lower()]
    if not v:
        return ""
    return _GENDER.get(v.lower(), v[:1].upper())


def barcode_of(src: dict) -> str:
    """The supplier's barcode on a row, digits only, or ''."""
    for key in ("Barcode", "EAN", "UPC"):
        v = src.get(key)
        if not is_blank(v):
            digits = re.sub(r"\.0$", "", str(v).strip())
            if re.fullmatch(r"\d{8,14}", digits):
                return digits
    return ""


def expand_sizes(row: dict, max_cols: int = 12) -> list[dict]:
    """The sizes on one order row.

    Suppliers lay them out two ways and both are common: across the row as
    ``Size 1``…``Size 5`` with a price each (Hiking Patrol, NuORDER), or one
    row per size with a single ``Size`` column (Carhartt). Handling only the
    first produced an empty sheet for every brand using the second.
    """
    out = []
    for n in range(1, max_cols + 1):
        size = row.get(f"Size {n}")
        if is_blank(size):
            continue
        out.append({"size": str(size).strip(),
                    "size_price": row.get(f"Size price {n}"),
                    "qty": row.get(f"Qty {n}"),
                    "position": len(out)})
    if out:
        return out

    # Already one row per size.
    for key in ("Size", "Size Description", "size"):
        size = row.get(key)
        if not is_blank(size):
            return [{"size": str(size).strip(),
                     "size_price": row.get("Wholesale Price EUR") or row.get("Size price 1"),
                     "qty": row.get("Quantity") or row.get("Qty 1"),
                     "position": 0}]
    return []


def build_temp_sheet(rows, *, brand: str, season: str,
                     group_map=None, vocab=None, colour_lookup=None,
                     history=None, earliest_ship_date: str = "",
                     colour_table=None, temporary=None,
                     base_colour: bool = True, size_tables=None,
                     uses_colour_codes: bool = False, keep_upper=(),
                     gender_map=None, hs_digits: int = 0,
                     group_by_word: bool = False,
                     barcode_supplier_size: bool = False,
                     keep_colour_slash: bool = False) -> dict:
    """Build the TEMP sheet from supplier order rows.

    ``group_map`` maps a supplier subcategory to a SAP item group. It is
    deliberately one-to-many in reality — TOP is SHIRTS or T-SHIRTS depending
    on the product — so a subcategory with more than one candidate raises a
    review rather than picking the first.

    ``history`` supplies the per-style values the order form lacks, keyed by
    style code: ``{"HP0127001": {"U_HS_Code": ..., "U_COO": ...}}``.

    When the supplier writes one row per size, the size run for a style is
    assembled across its rows rather than read off one of them.

    ``earliest_ship_date`` is set once for the collection. It is not the
    supplier's ship date — Hiking Patrol SS27 shipped 20 January on the order
    form and went into SAP as 15 January — so it is asked for, not copied.
    """
    from app.core.brand_rules import size_list_for, size_prefix, size_spelling
    from app.core.sap_creation import item_colour_code

    group_map = group_map or {}
    history = history or {}
    size_tables = size_tables or {}
    out_rows: list[dict] = []

    def sap_size(src_get, size):
        """The size as SAP writes it: HUF's ring sizes are R7, R8, R9."""
        size = normalise_size(size)
        prefix = size_prefix(src_get("Size Scale", "Size Run"), size_tables)
        if prefix and not size.upper().startswith(prefix.upper()):
            return f"{prefix}{size}"
        return size
    exceptions: list[dict] = []

    if is_blank(earliest_ship_date):
        exceptions.append({"sku": "", "field": "U_ESDate", "subject": "this collection",
                           "severity": "manual_review",
                           "reason": "the collection has no earliest shipment date "
                                     "(it differs from the supplier's ship date)",
                           "suggestion": "Set the earliest shipment date for this collection"})

    code, why = season_code(season)
    if not code:
        exceptions.append({"sku": "", "field": "U_Season", "severity": "critical",
                           "reason": why, "suggestion": "Confirm the SAP season code"})

    # A supplier writing one row per size spreads a style's run over many
    # rows; asking about each row's single size raised "size run XS" as a
    # question two hundred times. The run is the style-colour's sizes, in
    # the order the sheet gives them.
    runs: dict[tuple, list[str]] = {}
    for raw in rows:
        src = {normalise_header(k): v for k, v in raw.items()}
        g = lambda *k: next((str(src[x]).strip() for x in k                  # noqa: E731
                             if x in src and not is_blank(src[x])), "")
        key = (g("Item No.", "Style Number", "Style Code"), g("Color", "Colour"))
        for sz in expand_sizes(src):
            label = sap_size(g, sz["size"])
            if label not in runs.setdefault(key, []):
                runs[key].append(label)

    for order_index, raw in enumerate(rows, start=1):
        src = {normalise_header(k): v for k, v in raw.items()}
        get = lambda *k: next(                                   # noqa: E731
            (str(src[x]).strip() for x in k
             if x in src and not is_blank(src[x])), "")

        # The standard fields first: they are the supplier's columns already
        # cleaned (HUF's 'JK00525-CAMO' split into style and colour code).
        style = get("Item No.", "Style Number", "Style Code")
        colour = get("Color", "Colour")
        name = get("Item Description", "Name", "Description")
        subcat = get("Subcategory", "Item Group", "Category")
        sizes = expand_sizes(src)
        if not sizes:
            exceptions.append({"sku": style, "field": "U_SizeCode",
                               "subject": style, "severity": "critical",
                               "reason": "no sizes on this order row",
                               "suggestion": "Check the size columns"})
            continue

        run = runs.get((style, colour)) or [sap_size(get, x["size"]) for x in sizes]
        if len(sizes) > 1:
            run = [sap_size(get, x["size"]) for x in sizes]
        # The brand's own SAP size lists first (learned from its finished
        # TEMP sheets); the built-in runs only when the brand has none.
        list_code, sort_of = size_list_for(run, size_tables)
        spelled = size_spelling(list_code, size_tables)
        list_why = ""
        if not list_code:
            list_code, list_why = size_list_code(run)
        if not list_code:
            # The subject is the size run, not the style: two styles sharing
            # an unknown run are one decision, not two.
            exceptions.append({"sku": style, "field": "U_def_size_list_code",
                               "subject": "/".join(run),
                               "severity": "manual_review", "reason": list_why,
                               "suggestion": "Add this size run to the SAP size lists"})

        # Item group: one supplier subcategory can map to several SAP groups.
        candidates = group_map.get(subcat.upper(), [])
        if isinstance(candidates, str):
            candidates = [candidates]
        if group_by_word or len(candidates) != 1:
            # The product word ('Jacket', 'LS T-Shirt') when the brand was
            # taught by it: Dime's sections (Tops, Bottoms) are too coarse.
            by_word = group_for_product(name, group_map)
            if by_word and (group_by_word or not candidates
                            or set(by_word) <= set(candidates)):
                candidates = by_word
        group = candidates[0] if len(candidates) == 1 else ""
        if len(candidates) > 1:
            # The product's own name usually settles it: HUF's C&S ZIP FLEECE
            # is HOODYS for a "Zip Hoodie" and JACKETS for a "Zip Fleece".
            group = group_from_name(name, candidates)
        if len(candidates) > 1 and not group:
            exceptions.append({
                "sku": style, "field": "U_ItmsGrpCod", "subject": subcat,
                "severity": "manual_review",
                "reason": f"'{subcat}' maps to {' or '.join(candidates)} — "
                          f"the description decides",
                "suggestion": f"Choose the SAP group for '{name}'"})
        elif not candidates and not subcat:
            exceptions.append({
                "sku": style, "field": "U_ItmsGrpCod", "subject": "no product category",
                "severity": "warning",
                "reason": "the brand's files have no product category mapped, so the SAP "
                          "item group is empty — map Product group in Column mapping, or "
                          "teach the brand from a finished TEMP",
                "suggestion": "Map the product category"})
        elif not candidates and subcat:
            exceptions.append({
                "sku": style, "field": "U_ItmsGrpCod", "subject": subcat,
                "severity": "manual_review",
                "reason": f"no SAP item group known for '{subcat}'",
                "suggestion": f"Map '{subcat}' to a SAP item group"})

        if base_colour:
            base = propose_base_color(colour, vocab, colour_lookup,
                                      table=colour_table, temporary=temporary,
                                      code=get("Colour Code", "Color Code"))
            question = base_colour_exception(colour, base, "U_BaseColor")
            if question and question["severity"] != "warning":
                exceptions.append({**question, "sku": style})
        else:
            base = {"value": ""}

        # What the supplier sheet itself says wins over history: it is this
        # season's data. History fills only what the sheet leaves empty.
        hs = re.sub(r"[^0-9]", "", get("Duty Position", "HS Code", "HTS Code"))
        if hs_digits:
            hs = hs[:hs_digits]          # Komono's SAP holds 8 of the 10 digits
        from_sheet = {"U_HS_Code": hs,
                      "U_COO": country_code(get("Country of Origin", "COO")),
                      "U_Gender": gender_code(get("Sex", "Gender"), gender_map)}
        hist = {**history.get(style, {}),
                **{k: v for k, v in from_sheet.items() if v}}
        colour_code = get("Colour Code", "Color Code")
        for field in FROM_HISTORY:
            if not hist.get(field):
                exceptions.append({
                    "sku": style, "field": field, "subject": style,
                    "severity": "warning",
                    "reason": f"{field} is not in the order form and "
                              f"{style} has no history",
                    "suggestion": f"Supply {field} for {style}"})

        description = get("Item Description 2") or name
        for s in sizes:
            size = sap_size(get, s["size"])
            row = {c: "" for c in TEMP_COLUMNS}
            row.update({
                "U_Season": code or "",
                "U_OSRow": str(order_index),
                "U_NRStatus": "N",
                # The supplier's own EAN when the sheet has one per size —
                # that is what SAP holds; otherwise the built code.
                # Without one, style + colour code + size (DIMESU262CHAXXS),
                # or the colour name where the brand has no codes.
                "U_CodeBars": (barcode_of(src) if len(sizes) == 1 and barcode_of(src)
                               else barcode(style, colour_code or colour,
                                            s["size"] if barcode_supplier_size else size,
                                            barcode_supplier_size)),
                # Carhartt / Edwin: style.colour-code; others: style-COLOUR NAME.
                "U_SuppCatNum": item_colour_code(style, colour_code, colour,
                                                 uses_colour_codes,
                                                 keep_slash=keep_colour_slash),
                "U_Material": get("Material"),
                "U_VCat": smart_title(subcat),
                "U_ItmsGrpCod": group,
                "U_SCode": style,
                # Written as the brand's SAP size list spells it ("Onesize").
                "U_SizeCode": spelled.get(size.lower(), size),
                "U_webbeschreibung_gr": spelled.get(size.lower(), size),
                "U_Webbeschreibung2": smart_title(description,
                                                  set(keep_upper) | set(str(brand).upper().split())),
                # The accepted sheet stores the base colour lower-cased here,
                # unlike the Carhartt creation sheet which title-cases it.
                "U_BaseColor": (base["value"] or "").lower(),
                "U_Webbeschreibung": (brand or "").upper(),
                "U_def_size_list_code": list_code,
                "U_Web_Color": web_colour(colour, keep_colour_slash),
                "U_SortIndex": str(sort_of.get(size.lower()) or sort_index(
                    run.index(size) if size in run else s["position"], size)),
                "U_ESDate": "" if is_blank(earliest_ship_date) else str(earliest_ship_date),
            })
            for field in FROM_HISTORY:
                value = hist.get(field, "")
                row[field] = "" if is_blank(value) else str(value).strip()
            out_rows.append(row)

    by_sev: dict[str, int] = {}
    for e in exceptions:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1

    return {
        "columns": TEMP_COLUMNS,
        "rows": out_rows,
        "exceptions": exceptions,
        "summary": {"rows": len(out_rows), "order_rows": len(rows),
                    "styles": len({r["U_SCode"] for r in out_rows if r["U_SCode"]}),
                    "by_severity": by_sev},
    }

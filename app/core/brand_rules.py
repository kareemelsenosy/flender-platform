"""Brand rules — the per-brand mapping tables staff can read and change.

Every brand sends its data differently, and the rules for reading it never
change from season to season: Carhartt's size run "06" always means one size,
HUF's order-form size code "D" always means XS–XXL, "Sky Blue" is always Blue.
So the rules live in tables — not inside a model, not in code — where staff
can see exactly what the system will do and correct it.

The system may *suggest* a rule (a colour it has never seen, a size run that
is not in the table) but it never changes one by itself: it raises a change
request, and a person approves it. That is the hand-over the process needs —
the programme builds the base rules, then staff own them.

Everything here is pure (no DB) so it can be tested in isolation; the service
layer in ``app/services/brand_rules.py`` stores and audits the tables.
"""
from __future__ import annotations

import re

# ── The tables ───────────────────────────────────────────────────────────────
#
# Each table is a list of plain dict rows. ``key`` is the column that makes a
# row unique, so adding "Sky Blue" twice updates the first instead of
# duplicating it.

TABLES: dict[str, dict] = {
    "colours": {
        "label": "Base colours",
        "help": ("The brand's Seasonal Colour Table. Each colour name the brand "
                 "uses points to a statistic code, and the statistic code points "
                 "to the base colour. A colour can also be given a base colour "
                 "directly."),
        "columns": [("colour", "Colour name (as the brand writes it)"),
                    ("stat_code", "Statistic code"),
                    ("base", "Base colour (optional if the code has one)")],
        "key": "colour",
    },
    "stat_codes": {
        "label": "Statistic codes",
        "help": "The second sheet of the colour table: statistic code to base colour.",
        "columns": [("stat_code", "Statistic code"), ("base", "Base colour")],
        "key": "stat_code",
    },
    "size_runs": {
        "label": "Size runs",
        "help": ("What the brand's size-run code means. A range such as 32-36 "
                 "covers every code inside it."),
        "columns": [("code", "Size run code"), ("meaning", "What it means"),
                    ("sizes", "Sizes (optional, comma separated)")],
        "key": "code",
    },
    "size_codes": {
        "label": "Size codes",
        "help": ("Letter codes a brand uses for a whole size scale. HUF writes one "
                 "code in the order form and a different one in the UPC/HS list "
                 "for the same scale; both are listed so the two files can be "
                 "matched and the sizes spelled out."),
        "columns": [("order_code", "Order form size code"),
                    ("list_code", "Product list (HS) size code"),
                    ("meaning", "What it means"),
                    ("sizes", "Sizes (comma separated)")],
        "key": "order_code",
    },
    "columns": {
        "label": "Column mapping",
        "help": ("Which column in the brand's files holds each field the system "
                 "needs. Filled automatically where the column name is obvious; "
                 "set here when it is not."),
        "columns": [("field", "Field the system needs"),
                    ("header", "Column name in the brand's file")],
        "key": "field",
    },
}

TABLE_ORDER = ["colours", "stat_codes", "size_runs", "size_codes", "columns"]

SETTINGS = {
    "uses_base_colour": {
        "label": "This brand uses base colours",
        "help": ("Base colour is filled per item for brands whose SAP records "
                 "carry it (Carhartt WIP, Edwin). For other brands it is left "
                 "empty and never asked about."),
        "default": False,
    },
}


def brand_key(brand: str) -> str:
    """One spelling per brand: 'Carhartt WIP', 'CARHARTT  WIP' -> 'carhartt wip'."""
    key = re.sub(r"\s+", " ", str(brand or "")).strip().lower()
    return _ALIASES.get(key, key)


_ALIASES = {"carhartt": "carhartt wip", "carhartt work in progress": "carhartt wip",
            "huf worldwide": "huf"}


# ── Seeds: the rules Flender has already written down ────────────────────────
#
# From the Task Specification Adjustment Sheet. They are a starting point, not
# a lock: staff can change every one of them.

SEEDS: dict[str, dict] = {
    "carhartt wip": {
        "settings": {"uses_base_colour": True},
        "tables": {
            "size_runs": [
                {"code": "00", "meaning": "Set length for legwear", "sizes": ""},
                {"code": "03", "meaning": "XS, S, … , L, XL sizes",
                 "sizes": "XS, S, M, L, XL, XXL"},
                {"code": "06", "meaning": "Onesize", "sizes": "onesize"},
                {"code": "14", "meaning": "Socks sizes", "sizes": ""},
                {"code": "15", "meaning": "Caps or flexible fit range", "sizes": ""},
                {"code": "32-36", "meaning": "Pants length (inches)", "sizes": ""},
            ],
        },
    },
    "edwin": {"settings": {"uses_base_colour": True}, "tables": {}},
    "huf": {
        "settings": {"uses_base_colour": False},
        "tables": {
            "size_codes": [
                {"order_code": "C", "list_code": "O", "meaning": "O/S",
                 "sizes": "O/S"},
                {"order_code": "B", "list_code": "R", "meaning": "Ring sizes",
                 "sizes": ""},
                {"order_code": "D", "list_code": "A", "meaning": "XS-XXL",
                 "sizes": "XS, S, M, L, XL, XXL"},
                {"order_code": "A", "list_code": "P", "meaning": "26-38",
                 "sizes": "26, 28, 30, 32, 34, 36, 38"},
            ],
        },
    },
}


def seed_for(brand: str) -> dict:
    """The starting rules for a brand — empty tables when nothing is known."""
    seed = SEEDS.get(brand_key(brand), {})
    settings = {k: v["default"] for k, v in SETTINGS.items()}
    settings.update(seed.get("settings", {}))
    tables = {name: [dict(r) for r in seed.get("tables", {}).get(name, [])]
              for name in TABLE_ORDER}
    return {"settings": settings, "tables": tables}


# ── Editing ──────────────────────────────────────────────────────────────────

def _clean(value) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return "" if text.lower() in ("nan", "none", "null") else text


def clean_row(table: str, row: dict) -> dict:
    """Keep only the table's columns, trimmed. Base colours are title-cased so
    'BLUE' and 'Blue' never become two values."""
    cols = [c for c, _ in TABLES[table]["columns"]]
    out = {c: _clean(row.get(c)) for c in cols}
    if "base" in out and out["base"]:
        out["base"] = out["base"].title()
    return out


def upsert_row(rows: list[dict], table: str, row: dict) -> "tuple[list[dict], dict | None]":
    """Add a row, or replace the one with the same key. Returns (rows, old)."""
    row = clean_row(table, row)
    key = TABLES[table]["key"]
    if not row.get(key):
        raise ValueError(f"{dict(TABLES[table]['columns'])[key]} is required")
    out, old = [], None
    for r in rows:
        if _clean(r.get(key)).lower() == row[key].lower():
            old = r
            continue
        out.append(r)
    out.append(row)
    return out, old


def delete_row(rows: list[dict], table: str, key_value: str) -> "tuple[list[dict], dict | None]":
    key = TABLES[table]["key"]
    out, old = [], None
    for r in rows:
        if old is None and _clean(r.get(key)).lower() == _clean(key_value).lower():
            old = r
            continue
        out.append(r)
    return out, old


def sentence(table: str, row: dict) -> str:
    """One row in words a non-technical reader can check."""
    g = lambda k: _clean(row.get(k))                       # noqa: E731
    if table == "colours":
        if g("stat_code") and g("base"):
            return f"“{g('colour')}” has statistic code {g('stat_code')}, so its base colour is {g('base')}."
        if g("stat_code"):
            return f"“{g('colour')}” has statistic code {g('stat_code')}; the base colour comes from the statistic-code table."
        if not g("base"):
            return f"“{g('colour')}” — base colour not chosen yet."
        return f"“{g('colour')}” is base colour {g('base')}."
    if table == "stat_codes":
        return f"Statistic code {g('stat_code')} means base colour {g('base') or '(not set)'}."
    if table == "size_runs":
        if not g("meaning"):
            return f"Size run {g('code')} — meaning not described yet."
        tail = f" — sizes {g('sizes')}" if g("sizes") else ""
        return f"Size run {g('code')} means {g('meaning')}{tail}."
    if table == "size_codes":
        parts = [f"Order-form size code {g('order_code')}"]
        if g("list_code"):
            parts.append(f"(product-list code {g('list_code')})")
        return " ".join(parts) + f" means {g('meaning') or '(not described)'}" + (
            f": {g('sizes')}." if g("sizes") else ".")
    if table == "columns":
        if g("header") == "-":
            return f"{g('field')} is not in this brand's files — never guess a column for it."
        return f"{g('field')} is read from the column “{g('header')}”."
    return ", ".join(f"{k}: {v}" for k, v in row.items())


# ── Reading the rules ────────────────────────────────────────────────────────

def _norm_colour(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def colour_table_lookup(tables: dict) -> dict[str, dict]:
    """Colour name -> {"base", "stat_code"} with statistic codes resolved."""
    stats = {_clean(r.get("stat_code")).lower(): _clean(r.get("base"))
             for r in tables.get("stat_codes", []) if _clean(r.get("stat_code"))}
    out: dict[str, dict] = {}
    for r in tables.get("colours", []):
        name = _norm_colour(r.get("colour"))
        if not name:
            continue
        code = _clean(r.get("stat_code"))
        base = _clean(r.get("base")) or stats.get(code.lower(), "")
        out[name] = {"base": base.title() if base else "", "stat_code": code}
    return out


def resolve_from_table(colour: str, lookup: dict[str, dict]) -> dict | None:
    """The brand table's answer for one colour, or None when it is not listed."""
    hit = lookup.get(_norm_colour(colour))
    if not hit or not hit["base"]:
        return None
    code = f" (statistic code {hit['stat_code']})" if hit["stat_code"] else ""
    return {"value": hit["base"], "confidence": 1.0, "source": "brand_table",
            "reason": f"in the brand colour table{code}"}


def size_run_meaning(code: str, tables: dict) -> dict | None:
    """The size-run row covering ``code``; ranges like 32-36 cover 32..36."""
    code = _clean(code)
    if not code:
        return None
    for r in tables.get("size_runs", []):
        rc = _clean(r.get("code"))
        if rc.lower() == code.lower():
            return r
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", rc)
        if m and code.isdigit() and int(m.group(1)) <= int(code) <= int(m.group(2)):
            return r
    return None


def split_sizes(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,;]", str(text or "")) if s.strip()]


def sizes_for_code(code: str, tables: dict) -> list[str]:
    """Spell out a size-scale letter code from either of the brand's files."""
    code = _clean(code).lower()
    if not code:
        return []
    for r in tables.get("size_codes", []):
        if code in (_clean(r.get("order_code")).lower(), _clean(r.get("list_code")).lower()):
            return split_sizes(r.get("sizes"))
    return []


def scale_codes(tables: dict) -> set[str]:
    """Every letter code that names a whole size scale, from either file."""
    out = set()
    for r in tables.get("size_codes", []):
        for k in ("order_code", "list_code"):
            if _clean(r.get(k)):
                out.add(_clean(r.get(k)).upper())
    return out


def read_size_legend(path: str) -> dict[str, list[str]]:
    """Size-scale letters a workbook defines for itself.

    Three places they are printed: a "SIZE SCALES" block above the table
    (HUF, ACL), rows inside the header (Thisisneverthat), or a sheet of lot
    keys (Karl Kani). When a file defines its own scales, that definition is
    the one that holds for the file — HUF's letters change between seasons.
    """
    from app.core.sheet_layout import find_header_row, pick_data_sheets, read_workbook
    from app.core.size_expand import legend_from_lot_sheet, legend_in_header

    legend: dict[str, list[str]] = {}
    try:
        sheets = read_workbook(path)
    except Exception:
        return legend
    data = pick_data_sheets(sheets)
    for name in data:
        raw = sheets[name]
        h = find_header_row(raw)
        for _, r in raw.iloc[:h].iterrows():
            vals = [_clean(v) for v in r.tolist() if _clean(v)]
            if vals and vals[0].upper().replace(" ", "") in ("SIZESCALES", "SIZESCALE"):
                vals = vals[1:]
            if len(vals) >= 2 and re.fullmatch(r"[A-Z]", vals[0].upper()):
                sizes = [v for v in vals[1:] if len(v) <= 9]
                if sizes:
                    legend.setdefault(vals[0].upper(), sizes)
        for code, sizes in legend_in_header(raw, h).items():
            legend.setdefault(code, sizes)
        if legend:
            break
    for name, raw in sheets.items():
        if name not in data and re.search(r"lot", name, re.I):
            for code, sizes in legend_from_lot_sheet(raw).items():
                legend.setdefault(code, sizes)
    return legend


def expand_scale_rows(rows: list[dict], tables: dict,
                      legend: dict[str, list[str]] | None = None) -> list[dict]:
    """One row per size where a row names a whole size scale.

    HUF's order form writes "D" in the Size column for an XS–XXL item; SAP
    needs a line per size. A scale whose sizes are not written down in the
    table is left as it is, so the gap is visible instead of guessed.
    """
    codes = {c: sizes_for_code(c, tables) for c in scale_codes(tables)}
    codes = {c: s for c, s in codes.items() if s}
    codes.update(legend or {})
    if not codes:
        return rows
    out = []
    for row in rows:
        size = _clean(row.get("Size")).upper()
        sizes = codes.get(size)
        if not sizes:
            out.append(row)
            continue
        for s in sizes:
            line = dict(row)
            line["Size"] = s
            line["Size Scale"] = size
            if len(sizes) > 1:
                # The row's barcode belonged to the whole pack, not one size.
                line["Barcode"] = ""
                line.pop("SKU", None)
            out.append(line)
    return out


def column_overrides(tables: dict) -> dict[str, str]:
    return {_clean(r.get("field")): _clean(r.get("header"))
            for r in tables.get("columns", [])
            if _clean(r.get("field")) and _clean(r.get("header"))}


# ── Importing the brand's Seasonal Colour Table ──────────────────────────────

def _find_col(headers: list[str], *needles: str, avoid: tuple = ()) -> int | None:
    for i, h in enumerate(headers):
        low = h.lower()
        if any(n in low for n in needles) and not any(a in low for a in avoid):
            return i
    return None


def parse_colour_workbook(path: str) -> dict:
    """Read a Seasonal Colour Table workbook.

    The table comes as two sheets: colour -> statistic code, then statistic
    code -> base colour. Column names vary, so each sheet is read by what its
    headers mention. A sheet with colour and base colour side by side is read
    as direct entries.

    Returns ``{"colours": [...], "stat_codes": [...], "notes": [...]}``.
    """
    from app.core.sheet_layout import find_header_row, read_workbook

    colours: list[dict] = []
    stats: list[dict] = []
    notes: list[str] = []
    for name, raw in read_workbook(path).items():
        h = find_header_row(raw)
        headers = [_clean(v) for v in raw.iloc[h].tolist()]
        stat = _find_col(headers, "statistic", "stat code", "stat.", "stat ")
        base = _find_col(headers, "base")
        colour = _find_col(headers, "colour", "color", avoid=("base", "code", "statistic"))
        if colour is None:
            colour = _find_col(headers, "description", "name", avoid=("base",))

        body = raw.iloc[h + 1:]
        added = 0
        if colour is not None and (stat is not None or base is not None):
            for _, r in body.iterrows():
                vals = [_clean(v) for v in r.tolist()]
                if colour < len(vals) and vals[colour]:
                    colours.append({
                        "colour": vals[colour],
                        "stat_code": vals[stat] if stat is not None and stat < len(vals) else "",
                        "base": vals[base].title() if base is not None and base < len(vals) else "",
                    })
                    added += 1
            notes.append(f"{name}: {added} colours")
        elif stat is not None and base is not None:
            for _, r in body.iterrows():
                vals = [_clean(v) for v in r.tolist()]
                if stat < len(vals) and vals[stat] and base < len(vals) and vals[base]:
                    stats.append({"stat_code": vals[stat], "base": vals[base].title()})
                    added += 1
            notes.append(f"{name}: {added} statistic codes")
        else:
            notes.append(f"{name}: skipped — no colour, statistic code or base colour column")
    return {"colours": colours, "stat_codes": stats, "notes": notes}

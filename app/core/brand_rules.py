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
        "help": ("The brand's Seasonal Colour Table. Each colour (by its colour "
                 "code, or by name when the brand has no codes) points to a "
                 "statistic code, and the statistic code points to the base "
                 "colour. A colour can also be given a base colour directly."),
        "columns": [("code", "Colour code (optional)"),
                    ("colour", "Colour name (as the brand writes it)"),
                    ("stat_code", "Statistic code"),
                    ("base", "Base colour (optional if the code has one)")],
        # A row is identified by its colour code when it has one, else by name.
        "key": "colour",
        "alt_key": "code",
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
            # Carhartt's statistic colour codes. Every one of the 7,995
            # colours carrying both a statistic code and a BASE COLOR in the
            # 051026 colour table agrees with this list, without exception.
            "stat_codes": [
                {"stat_code": c, "base": b} for c, b in (
                    ("001", "Black"), ("002", "Grey"), ("003", "Brown"),
                    ("004", "Beige"), ("005", "Green"), ("006", "Blue"),
                    ("007", "Purple"), ("008", "Red"), ("009", "Pink"),
                    ("010", "Orange"), ("011", "Yellow"), ("012", "White"),
                    ("013", "Multicolour"))],
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


def row_key(table: str, row: dict) -> str:
    """What makes a row unique. A colour with a code is that code — Carhartt
    has many colours called "Black" — and a colour without one is its name."""
    alt = TABLES[table].get("alt_key")
    if alt and _clean(row.get(alt)):
        return _clean(row.get(alt)).lower()
    return _clean(row.get(TABLES[table]["key"])).lower()


def upsert_row(rows: list[dict], table: str, row: dict) -> "tuple[list[dict], dict | None]":
    """Add a row, or replace the one with the same key. Returns (rows, old)."""
    row = clean_row(table, row)
    key = TABLES[table]["key"]
    if not row_key(table, row):
        raise ValueError(f"{dict(TABLES[table]['columns'])[key]} is required")
    want = row_key(table, row)
    out, old = [], None
    for r in rows:
        if row_key(table, r) == want:
            old = r
            continue
        out.append(r)
    out.append(row)
    return out, old


def upsert_many(rows: list[dict], table: str, new_rows: list[dict]) -> list[dict]:
    """Add or replace many rows at once (an imported table of thousands)."""
    index = {row_key(table, r): i for i, r in enumerate(rows)}
    out = list(rows)
    for raw in new_rows:
        row = clean_row(table, raw)
        k = row_key(table, row)
        if not k:
            continue
        if k in index:
            out[index[k]] = row
        else:
            index[k] = len(out)
            out.append(row)
    return out


def delete_row(rows: list[dict], table: str, key_value: str) -> "tuple[list[dict], dict | None]":
    out, old = [], None
    for r in rows:
        if old is None and row_key(table, r) == _clean(key_value).lower():
            old = r
            continue
        out.append(r)
    return out, old


def sentence(table: str, row: dict) -> str:
    """One row in words a non-technical reader can check."""
    g = lambda k: _clean(row.get(k))                       # noqa: E731
    if table == "colours":
        if g("code"):
            label = f"Colour code {g('code')}" + (f" (“{g('colour')}”)" if g("colour") else "")
            if g("stat_code"):
                tail = f", so its base colour is {g('base')}." if g("base") else \
                    "; the base colour comes from the statistic-code table."
                return f"{label} has statistic code {g('stat_code')}{tail}"
            return f"{label} is base colour {g('base') or '(not chosen yet)'}."
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


def _norm_stat(code) -> str:
    """'001' and '1' are the same statistic code."""
    c = _clean(code)
    return str(int(c)) if c.isdigit() else c.lower()


_CODE = "#code:"


def colour_table_lookup(tables: dict) -> dict[str, dict]:
    """Colour -> {"base", "stat_code", "via"} with statistic codes resolved.

    Keys are colour names, plus ``#code:<code>`` for rows that carry a colour
    code; ``resolve_from_table`` tries the code first.
    """
    stats = {_norm_stat(r.get("stat_code")): _clean(r.get("base"))
             for r in tables.get("stat_codes", []) if _clean(r.get("stat_code"))}
    out: dict[str, dict] = {}
    for r in tables.get("colours", []):
        stat = _clean(r.get("stat_code"))
        base = _clean(r.get("base")) or stats.get(_norm_stat(stat), "")
        entry = {"base": base.title() if base else "", "stat_code": stat,
                 "name": _clean(r.get("colour"))}
        code = _clean(r.get("code"))
        if code:
            out[_CODE + code.upper()] = {**entry, "via": f"colour code {code}"}
        name = _norm_colour(r.get("colour"))
        # By-name lookups only from rows without a code, or when unambiguous:
        # Carhartt has dozens of codes all described as "Black".
        if name and (not code or name not in out):
            out.setdefault(name, {**entry, "via": f"colour name “{entry['name']}”"})
    return out


def resolve_from_table(colour: str, lookup: dict[str, dict], code: str = "") -> dict | None:
    """The brand table's answer for one colour, or None when it is not listed.

    With a colour code the code decides: the house colour first — the code
    without its two-character finish (Carhartt '8902' is house colour '89'
    with finish '02') — then the full code. House first matters: '0260' is
    White ('02') with finish '60', while the table also lists an unrelated
    house colour '0260' (Scotty Stripe, Black). The name is the fallback.
    """
    hit = None
    code = _clean(code).upper()
    if code:
        for candidate in (code[:-2] if len(code) > 2 else "", code):
            if candidate and (lookup.get(_CODE + candidate) or {}).get("base"):
                hit = lookup[_CODE + candidate]
                break
    if hit is None:
        hit = lookup.get(_norm_colour(colour))
    if not hit or not hit["base"]:
        return None
    stat = f", statistic code {hit['stat_code']}" if hit["stat_code"] else ""
    return {"value": hit["base"], "confidence": 1.0, "source": "brand_table",
            "reason": f"brand colour table: {hit.get('via', 'listed')}{stat}"}


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
    """Read a brand colour table workbook.

    Two shapes are understood:

    * **A colour master** (Carhartt's): one row per colour *code* with a
      description and a Statistic Colour Code, often several dated copies of
      the sheet. The newest, fullest sheet is used; a BASE COLOR column, when
      present, also teaches the statistic-code -> base-colour table.
    * **Two sheets**: colour -> statistic code, then statistic code -> base.

    Returns ``{"colours": [...], "stat_codes": [...], "notes": [...]}``.
    """
    from app.core.sheet_layout import find_header_row, read_workbook

    sheets = read_workbook(path)
    parsed = []
    for name, raw in sheets.items():
        h = find_header_row(raw)
        headers = [_clean(v) for v in raw.iloc[h].tolist()]
        cols = {
            "stat": _find_col(headers, "statistic", "stat code", "stat.", "stat "),
            "base": _find_col(headers, "base"),
            "code": next((i for i, x in enumerate(headers)
                          if x.lower() in ("code", "colour code", "color code")), None),
            "colour": (_find_col(headers, "colour", "color", avoid=("base", "code", "statistic"))
                       if _find_col(headers, "colour", "color", avoid=("base", "code", "statistic")) is not None
                       else _find_col(headers, "description", "name", avoid=("base",))),
        }
        parsed.append((name, raw.iloc[h + 1:], cols))

    colours: list[dict] = []
    stats: dict[str, str] = {}
    notes: list[str] = []

    def val(vals, i):
        return vals[i] if i is not None and i < len(vals) else ""

    # A colour master: keep only the fullest sheet with codes and statistic
    # codes — the dated copies are older versions of the same list.
    masters = [p for p in parsed if p[2]["code"] is not None and p[2]["stat"] is not None]
    if masters:
        name, body, c = max(masters, key=lambda p: (p[2]["base"] is not None, len(p[1])))
        skipped = 0
        for _, r in body.iterrows():
            vals = [_clean(v) for v in r.tolist()]
            code, stat, base = val(vals, c["code"]), val(vals, c["stat"]), val(vals, c["base"])
            if not code or not (stat or base) or set(code) <= {"0"}:
                skipped += 1
                continue
            colours.append({"code": code, "colour": val(vals, c["colour"]),
                            "stat_code": stat, "base": base.title() if base else ""})
            if stat and base:
                stats.setdefault(_norm_stat(stat), base.title())
        notes.append(f"{name}: {len(colours)} colour codes with a statistic code "
                     f"({skipped} without one skipped)")
        others = [p[0] for p in masters if p[0] != name]
        if others:
            notes.append(f"older copies not used: {', '.join(others)}")
    else:
        for name, body, c in parsed:
            added = 0
            if c["colour"] is not None and (c["stat"] is not None or c["base"] is not None):
                for _, r in body.iterrows():
                    vals = [_clean(v) for v in r.tolist()]
                    if val(vals, c["colour"]):
                        colours.append({"code": val(vals, c["code"]),
                                        "colour": val(vals, c["colour"]),
                                        "stat_code": val(vals, c["stat"]),
                                        "base": val(vals, c["base"]).title()})
                        added += 1
                notes.append(f"{name}: {added} colours")
            elif c["stat"] is not None and c["base"] is not None:
                for _, r in body.iterrows():
                    vals = [_clean(v) for v in r.tolist()]
                    if val(vals, c["stat"]) and val(vals, c["base"]):
                        stats.setdefault(_norm_stat(val(vals, c["stat"])),
                                         val(vals, c["base"]).title())
                        added += 1
                notes.append(f"{name}: {added} statistic codes")
            else:
                notes.append(f"{name}: skipped — no colour, statistic code or base colour column")

    return {"colours": colours,
            "stat_codes": [{"stat_code": k.zfill(3) if k.isdigit() else k, "base": v}
                           for k, v in sorted(stats.items(), key=lambda kv: kv[0].zfill(5))],
            "notes": notes}

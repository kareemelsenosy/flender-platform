"""Turning however a supplier writes sizes into one row per size.

Measured against the fourteen order sheets Flender works with, sizes arrive as:

* one row per size (Carhartt, RIPNDIP, Butter Goods, Market)
* a list in one cell — "XS, S, M, L, XL" (Hiking Patrol)
* a range in one cell — "28 - 38" (Edwin), "XXS-4XL" / "26-44" (Arte)
* a letter naming a size scale, defined in a legend above the table (HUF,
  ACL) or inside the header rows (Thisisneverthat)
* a lot key defined on its own sheet (Karl Kani: KM = One Size, SX = 35-38)
* columns across the sheet, one per size, holding quantities
* nothing at all, for one-size goods (Komono)

SAP needs a line per size, so every shape is reduced to that here. Nothing
is guessed: a range is only filled from sizes the sheet itself lists, or from
the standard letter order.
"""
from __future__ import annotations

import re

LETTERS = ["XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL"]
_LETTER_ALIASES = {"2XS": "XXS", "3XS": "XXXS", "2XL": "XXL", "3XL": "XXXL",
                   "XXXXL": "4XL"}
ONE_SIZE = {"OS", "O/S", "ONESIZE", "ONE SIZE", "UNI", "U", "TU", "NS"}


def _blank(v) -> bool:
    return v is None or str(v).strip().lower() in ("", "nan", "none", "null", "nat")


def letter_rank(size: str) -> int | None:
    s = str(size or "").strip().upper().replace(" ", "")
    s = _LETTER_ALIASES.get(s, s)
    return LETTERS.index(s) if s in LETTERS else None


def _num(size: str) -> float | None:
    m = re.fullmatch(r"\s*(\d{1,3}(?:[.,]5)?)\s*", str(size or ""))
    return float(m.group(1).replace(",", ".")) if m else None


# ── Columns across the sheet ─────────────────────────────────────────────────

def size_label_parts(header: str) -> "tuple[str | None, float | None]":
    """'XS / 28' -> ('XS', 28.0); '29' -> (None, 29.0); 'One Size' -> ('OS', None)."""
    h = re.sub(r"\s+", " ", str(header or "")).strip()
    if h.upper().replace(" ", "") in {x.replace(" ", "") for x in ONE_SIZE}:
        return "OS", None
    letter, number = None, None
    for part in re.split(r"\s*/\s*", h):
        if letter_rank(part) is not None and letter is None:
            letter = part.upper()
        elif _num(part) is not None and number is None:
            number = _num(part)
    return letter, number


def size_columns(headers: list[str]) -> list[str]:
    """The block of headers that are sizes. Needs three or more to count —
    one "M" column is a coincidence, not a size grid."""
    out = []
    for h in headers:
        letter, number = size_label_parts(h)
        if letter is not None or (number is not None and 0 < number < 60):
            out.append(h)
    return out if len(out) >= 3 else []


# ── One cell ─────────────────────────────────────────────────────────────────

def split_list(value: str) -> list[str]:
    """'XS, S, M, L, XL' -> five sizes. A single value stays single."""
    parts = [p.strip() for p in re.split(r"[,;]", str(value or "")) if p.strip()]
    return parts if len(parts) > 1 else []


_RANGE = re.compile(r"^\s*([A-Z0-9/.]+)\s*[-–]\s*([A-Z0-9/.]+)\s*$", re.I)


def expand_range(value: str, columns: list[str] | None = None) -> list[str]:
    """'28 - 38' or 'XXS-4XL' -> the sizes inside it.

    When the sheet has size columns, the range picks from them, so the
    result is exactly what the supplier offers ('28 - 38' over columns 25…38
    gives 28, 29, … 34, 36, 38). Without columns a letter range follows the
    standard order; a numeric range without columns is left alone rather
    than invented.
    """
    m = _RANGE.match(str(value or ""))
    if not m:
        return []
    lo, hi = m.group(1), m.group(2)
    # "24/26-44/46": two numbering systems; the second is the one sheets show.
    lo_n, hi_n = _num(lo.split("/")[-1]), _num(hi.split("/")[-1])
    lo_l, hi_l = letter_rank(lo), letter_rank(hi)

    if columns:
        out = []
        for h in columns:
            letter, number = size_label_parts(h)
            if lo_l is not None and hi_l is not None and letter and letter != "OS":
                r = letter_rank(letter)
                if r is not None and lo_l <= r <= hi_l:
                    out.append(letter)
            elif lo_n is not None and hi_n is not None and number is not None:
                if lo_n <= number <= hi_n:
                    out.append(_fmt(number))
        if out:
            return out
    if lo_l is not None and hi_l is not None and lo_l <= hi_l:
        return LETTERS[lo_l:hi_l + 1]
    return []


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)


def _is_one_size(value: str) -> bool:
    return str(value or "").strip().upper() in ONE_SIZE or \
        str(value or "").strip().lower() in ("one size", "onesize")


# ── Rows ─────────────────────────────────────────────────────────────────────

def expand_rows(rows: list[dict], headers: list[str], *, has_size_field: bool) -> "tuple[list[dict], dict]":
    """One row per size. Returns ``(rows, note)`` saying what was done.

    ``Size`` holds whatever the size field was mapped to. Rows whose size is
    a scale letter or lot key are left for the legend step, which knows what
    those letters mean.
    """
    cols = size_columns(headers)
    out: list[dict] = []
    note = {"lists": 0, "ranges": 0, "from_columns": 0, "one_size_default": 0}

    for row in rows:
        size = "" if _blank(row.get("Size")) else str(row.get("Size")).strip()
        sizes: list[str] = []

        has_barcode = not _blank(row.get("Barcode"))
        if size and (has_barcode or _is_one_size(size)):
            # A line with its own barcode is one sellable size, whatever it is
            # called: Carhartt's "S-M" / "M-L" are sizes, not ranges.
            sizes = [size]
        elif size and split_list(size):
            sizes, note["lists"] = split_list(size), note["lists"] + 1
        elif size and expand_range(size, cols):
            sizes, note["ranges"] = expand_range(size, cols), note["ranges"] + 1
        elif not size and cols:
            ordered = [h for h in cols if _qty(row.get(h))]
            sizes = [_label(h) for h in (ordered or cols)]
            note["from_columns"] += 1
        elif not size and not has_size_field and not cols:
            sizes, note["one_size_default"] = ["onesize"], note["one_size_default"] + 1

        if len(sizes) <= 1:
            if sizes:
                row = {**row, "Size": sizes[0]}
            out.append(row)
            continue
        for s in sizes:
            line = dict(row)
            line["Size"] = s
            # A barcode or SKU on a row that covers several sizes belongs to
            # none of them.
            line["Barcode"] = ""
            line.pop("SKU", None)
            out.append(line)
    return out, note


def _qty(value) -> bool:
    try:
        return float(str(value).replace(",", ".")) > 0
    except (TypeError, ValueError):
        return False


def _label(header: str) -> str:
    letter, number = size_label_parts(header)
    if letter == "OS":
        return "onesize"
    if letter:
        return letter
    return _fmt(number) if number is not None else str(header).strip()


# ── Legends ──────────────────────────────────────────────────────────────────

def legend_in_header(raw, header_row: int) -> dict[str, list[str]]:
    """A size-scale legend printed inside the header (Thisisneverthat).

    The header has a column titled with one letter ("A") followed by the
    sizes of scale A; the rows straight after it repeat the letter column
    with B, C… and their sizes under the same columns.
    """
    hdr = [str(v).strip() if not _blank(v) else "" for v in raw.iloc[header_row].tolist()]
    scale_col = next((i for i, h in enumerate(hdr) if re.fullmatch(r"[A-Z]", h)), None)
    if scale_col is None:
        return {}
    # The size block ends at the first header that is a word, not a size.
    end = len(hdr)
    for i in range(scale_col + 1, len(hdr)):
        if hdr[i] and size_label_parts(hdr[i]) == (None, None):
            end = i
            break

    def sizes_in(values):
        return [str(v).strip() for v in values[scale_col + 1:end] if not _blank(v)]

    legend = {hdr[scale_col]: sizes_in(hdr)}
    for r in range(header_row + 1, min(header_row + 8, len(raw))):
        vals = raw.iloc[r].tolist()
        code = str(vals[scale_col]).strip() if not _blank(vals[scale_col]) else ""
        others = [v for i, v in enumerate(vals[:scale_col]) if not _blank(v)]
        if not re.fullmatch(r"[A-Z]", code) or others:
            break
        legend[code] = sizes_in(vals)
    return {k: v for k, v in legend.items() if v}


def legend_from_lot_sheet(raw) -> dict[str, list[str]]:
    """Lot keys defined on their own sheet (Karl Kani's "Lotkeys").

    Blocks of: a row of sizes, then one row per lot key with the quantity of
    each size in the pack. A lot's sizes are the ones with a quantity.
    """
    legend: dict[str, list[str]] = {}
    sizes_row: list[str] | None = None
    for _, r in raw.iterrows():
        vals = ["" if _blank(v) else str(v).strip() for v in r.tolist()]
        if not any(vals):
            sizes_row = None
            continue
        first, rest = vals[0], vals[1:]
        if not first and any(rest):
            sizes_row = rest                       # the size header of a block
            continue
        if sizes_row and re.fullmatch(r"[A-Z0-9]{1,4}", first):
            picked = [sizes_row[i] for i, v in enumerate(rest)
                      if i < len(sizes_row) and sizes_row[i]
                      and sizes_row[i].lower() not in ("sum", "total") and _qty(v)]
            if picked:
                legend.setdefault(first.upper(), picked)
    return legend

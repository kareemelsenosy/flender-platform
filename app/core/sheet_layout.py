"""Finding the actual data in a supplier workbook — Operations OS.

Supplier order sheets are laid out for humans, not importers. Half of the 14
brands Flender works with put the real header six or twelve rows down, under a
block of brand name, ship dates and size-scale legends; several spread the
collection across sheets, next to summary tabs that must not be mistaken for
the data.

Two questions this module answers:

  * **Which sheet(s)?** The largest populated sheet, plus any sibling sheet
    with the same header — HUF sends one tab per delivery date and both are
    part of the same collection.
  * **Which row is the header?** The densest early row that reads as labels
    rather than values.

Measured on the 14 brand order sheets Flender supplied: naive "first sheet,
first row" yields usable rows for 7 of 14; this yields rows for all 14.
"""
from __future__ import annotations

import re

import pandas as pd

# How far down to look for a header. Dime's order form opens with a 34-row
# block of store, billing and shipping fields before the product table.
MAX_HEADER_SCAN = 60
MIN_HEADER_CELLS = 3


def _cells(row) -> list[str]:
    return [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[-+]?[\d.,%\s]+", value))


def score_header_row(row, index: int) -> float:
    """How much a row reads like a header rather than data or a title block.

    Rewards width, label-like (non-numeric) text and distinct values; penalises
    depth so an early genuine header beats a later lookalike.
    """
    vals = _cells(row)
    if len(vals) < MIN_HEADER_CELLS:
        return -1.0
    non_numeric = sum(1 for v in vals if not _looks_numeric(v))
    distinct = len({v.casefold() for v in vals})
    # Cells that are field names (Style, Description, Color, Price...) are
    # the strongest sign of a header; a form's "Store Name." block has none.
    known = sum(1 for v in vals if _is_field_name(v))
    return len(vals) * 2 + non_numeric + distinct + known * 4 - index * 0.5


def _is_field_name(value: str) -> bool:
    from app.core.field_mapping import FIELDS, norm
    global _FIELD_WORDS
    if _FIELD_WORDS is None:
        _FIELD_WORDS = {a for _f, _p, aliases in FIELDS for a in aliases} | {
            norm(f) for f, _p, _a in FIELDS} | {"qty", "units", "total", "sizes"}
    return norm(value) in _FIELD_WORDS


_FIELD_WORDS = None


def find_header_row(df: pd.DataFrame, scan: int = MAX_HEADER_SCAN) -> int:
    """Index of the header row in a raw (header=None) frame."""
    best, best_score = 0, -1.0
    for i in range(min(scan, len(df))):
        s = score_header_row(df.iloc[i], i)
        if s > best_score:
            best, best_score = i, s
    return best


def header_signature(df: pd.DataFrame, header_row: int) -> frozenset:
    """Normalised header labels, for deciding whether two sheets match."""
    return frozenset(
        re.sub(r"\s+", " ", c).casefold()
        for c in _cells(df.iloc[header_row])
    )


def pick_data_sheets(sheets: "dict[str, pd.DataFrame]") -> list[str]:
    """Which sheets hold the collection.

    The biggest populated sheet leads. Any other sheet sharing most of its
    header is included too, which is how a brand that sends one tab per
    delivery date stays one collection instead of losing a delivery.
    """
    if not sheets:
        return []
    sized = sorted(sheets.items(), key=lambda kv: -(kv[1].shape[0] * kv[1].shape[1]))
    primary_name, primary = sized[0]
    if primary.empty:
        return [primary_name]

    base = header_signature(primary, find_header_row(primary))
    chosen = [primary_name]
    for name, df in sized[1:]:
        if df.empty or df.shape[0] < 2:
            continue
        sig = header_signature(df, find_header_row(df))
        if base and sig and len(base & sig) / len(base | sig) >= 0.6:
            chosen.append(name)
    return chosen


def read_workbook(path: str) -> "dict[str, pd.DataFrame]":
    """Every sheet as a raw frame, headers not yet applied."""
    book = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    return {name: df for name, df in book.items() if not df.empty}


def load_supplier_table(path: str) -> "tuple[pd.DataFrame, dict]":
    """Read a supplier workbook into one table with real headers applied.

    Returns the table and a report of what was decided, so the collection
    screen can show which sheet and header row were used — and let a human
    correct it when the guess is wrong.
    """
    sheets = read_workbook(path)
    if not sheets:
        return pd.DataFrame(), {"sheets_used": [], "header_row": None,
                                "reason": "workbook has no populated sheet"}

    used = pick_data_sheets(sheets)
    frames, header_row = [], None
    section_above = ""
    for name in used:
        raw = sheets[name]
        h = find_header_row(raw)
        if header_row is None:
            header_row = h
            # A one-cell heading straight above the header names the first
            # section (Dime: JACKETS, then the Style / Description header).
            for i in range(h - 1, max(h - 3, -1), -1):
                cells = _cells(raw.iloc[i])
                if len(cells) == 1 and not _looks_numeric(cells[0]) and len(cells[0]) <= 40:
                    section_above = cells[0]
                    break
                if cells:
                    break
        cols = [str(v).strip() if pd.notna(v) else "" for v in raw.iloc[h].tolist()]
        body = raw.iloc[h + 1:].copy()
        body.columns = cols
        body = body.dropna(how="all")
        body["__sheet"] = name
        frames.append(body)

    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    table, joined, key_fixes = _join_sheets_on_key(table, sheets, used)
    return table, {
        "sheets_used": used,
        "sheets_available": list(sheets),
        "header_row": header_row,
        "section_above": section_above,
        "rows": len(table),
        "joined_sheets": joined,
        "key_fixes": key_fixes,
    }


def _norm_label(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _key_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip().upper()
    return "" if text in ("NAN", "NONE") else text


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _norm_label(a), _norm_label(b)).ratio()


def _with_header(raw: pd.DataFrame) -> pd.DataFrame:
    h = find_header_row(raw)
    cols = [str(v).strip() if pd.notna(v) else "" for v in raw.iloc[h].tolist()]
    body = raw.iloc[h + 1:].copy()
    body.columns = cols
    body = body.loc[:, [bool(c) for c in body.columns]]
    body = body.loc[:, ~body.columns.duplicated()]
    return body.dropna(how="all").reset_index(drop=True)


def _join_sheets_on_key(table: pd.DataFrame, sheets: dict, used: list[str]):
    """Sheets that describe the same articles at a coarser level, joined on.

    A German master-data file (Another Cotton Lab's Stammdaten) splits one
    collection over three tabs: Modell (HS code, origin, gender per article),
    Colors (colour names, material) and SKU & Barcodes (one row per size).
    The size tab is the data; the others share its Factory Article No and are
    joined onto every size row, so nothing on them is lost.

    Two tabs typed by hand drift: Modell row 120 says ACL-261-TS-745-001
    where Colors and the SKU tab say ACL-271-TS-745-003. When two tabs list
    the same articles row for row, a row whose number matches nothing is the
    article on the same row of the other tab — if the names agree.

    Returns ``(table, joined sheet names, key fixes)``.
    """
    if table.empty or len(sheets) < 2:
        return table, [], []
    base_cols = {_norm_label(c): c for c in table.columns if str(c).strip()}
    sides = []
    for name, raw in sheets.items():
        if name in used or raw.shape[0] < 4:
            continue
        frame = _with_header(raw)
        if frame.empty or len(frame) > len(table):
            continue
        best = None
        for col in frame.columns:
            base_col = base_cols.get(_norm_label(col))
            # A product name is shared by its colourways: never the key.
            if not base_col or re.search(r"description|name", _norm_label(col)):
                continue
            keys = [_key_text(v) for v in frame[col]]
            filled = [k for k in keys if k]
            if len(filled) < 3 or len(set(filled)) < 0.75 * len(filled):
                continue
            base_keys = {_key_text(v) for v in table[base_col]}
            hit = sum(k in base_keys for k in filled) / len(filled)
            if hit >= 0.6 and (best is None or hit > best[0]):
                best = (hit, col, base_col, keys)
        if best:
            sides.append({"name": name, "frame": frame, "col": best[1],
                          "base_col": best[2], "keys": best[3]})
    if not sides:
        return table, [], []

    # The product name, to tell apart two articles a supplier numbered alike
    # (ACL-271-TS-750-002 is a T-Shirt and a Raglan Longsleeve) and to
    # confirm a mistyped number is the same article.
    def name_col(cols, avoid):
        return next((c for c in cols if c != avoid and re.search(
            r"description|name|model", _norm_label(c))), None)

    key_fixes = []
    for side in sides:
        base_keys = {_key_text(v) for v in table[side["base_col"]]}
        side_name = name_col(side["frame"].columns, side["col"])
        for other in sides:
            if other is side or len(other["keys"]) != len(side["keys"]):
                continue
            same = sum(a == b for a, b in zip(side["keys"], other["keys"]))
            if same < 0.8 * len(side["keys"]):
                continue
            other_name = name_col(other["frame"].columns, other["col"])
            for i, (mine, theirs) in enumerate(zip(side["keys"], other["keys"])):
                if mine in base_keys or theirs not in base_keys or mine == theirs:
                    continue
                if side_name and other_name and _similar(
                        side["frame"].at[i, side_name],
                        other["frame"].at[i, other_name]) < 0.75:
                    continue
                side["keys"][i] = theirs
                key_fixes.append({"sheet": side["name"], "row": i + 2,
                                  "wrote": mine, "used": theirs})

    out = table.copy()
    taken = {_norm_label(c) for c in out.columns}
    base_name = name_col(table.columns, None)
    joined = []
    for side in sides:
        frame = side["frame"]
        side_name = name_col(frame.columns, side["col"])
        index: dict[str, list[int]] = {}
        for i, k in enumerate(side["keys"]):
            if k:
                index.setdefault(k, []).append(i)
        picks = []
        for _, row in table.iterrows():
            found = index.get(_key_text(row[side["base_col"]]), [])
            if len(found) > 1 and base_name and side_name:
                found = [max(found, key=lambda i: _similar(
                    row[base_name], frame.at[i, side_name]))]
            picks.append(found[0] if found else None)
        for col in frame.columns:
            if col == side["col"] or frame[col].isna().all():
                continue
            label = col if _norm_label(col) not in taken else f"{col} ({side['name']})"
            if _norm_label(label) in taken:
                continue
            taken.add(_norm_label(label))
            values = frame[col].tolist()
            out[label] = [values[i] if i is not None else None for i in picks]
        joined.append(side["name"])
    return out, joined, key_fixes


def workbook_labels(path: str) -> list[str]:
    """Where a workbook names itself: its data sheets' names and the title
    block above the table ("EDWIN Collection FW26 in AED", "2026 FALL/WINTER").
    Used to find the season when the filename does not carry it."""
    try:
        sheets = read_workbook(path)
    except Exception:
        return []
    labels: list[str] = []
    for name in pick_data_sheets(sheets):
        labels.append(name)
        raw = sheets[name]
        for i in range(min(find_header_row(raw), 12)):
            labels.extend(_cells(raw.iloc[i]))
    return labels

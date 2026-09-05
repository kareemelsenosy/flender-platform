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

# How far down to look for a header. The deepest real case seen is row 12
# (Thisisneverthat); the margin covers worse.
MAX_HEADER_SCAN = 30
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
    return len(vals) * 2 + non_numeric + distinct - index * 0.5


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
    for name in used:
        raw = sheets[name]
        h = find_header_row(raw)
        if header_row is None:
            header_row = h
        cols = [str(v).strip() if pd.notna(v) else "" for v in raw.iloc[h].tolist()]
        body = raw.iloc[h + 1:].copy()
        body.columns = cols
        body = body.dropna(how="all")
        body["__sheet"] = name
        frames.append(body)

    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table, {
        "sheets_used": used,
        "sheets_available": list(sheets),
        "header_row": header_row,
        "rows": len(table),
    }

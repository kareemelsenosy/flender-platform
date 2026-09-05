"""Supplier template drift — Operations OS.

A brand's order sheet layout changes between seasons: new currency columns
appear, columns move, headers get re-typed. The system must notice, say what
changed, and ask what to do — without raising noise for changes that are not
real.

That last part is the whole difficulty. Comparing Carhartt SS27 against FW26
naively reports four changes; two of them are the same header with a different
number of spaces ("Earliest  Shipment  Date" against "Earliest Shipment Date").
Headers are therefore compared on a normalised form, and a header whose only
change is spacing or casing is reported separately as cosmetic.
"""
from __future__ import annotations

import re


def normalise_header(name: str) -> str:
    """The form headers are compared on: single spaces, trimmed."""
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _key(name: str) -> str:
    return normalise_header(name).casefold()


def diff_templates(previous, current) -> dict:
    """Compare two header lists.

    Returns added / removed / moved / renamed_cosmetically, plus ``has_changes``
    for the genuinely material ones. Position changes alone do not make a
    template "changed" in the sense that needs a decision — a reordered sheet
    still maps correctly — so they are reported but kept out of ``has_changes``.
    """
    prev = [normalise_header(h) for h in previous if normalise_header(h)]
    curr = [normalise_header(h) for h in current if normalise_header(h)]
    prev_keys = {_key(h): h for h in prev}
    curr_keys = {_key(h): h for h in curr}

    added = [curr_keys[k] for k in curr_keys if k not in prev_keys]
    removed = [prev_keys[k] for k in prev_keys if k not in curr_keys]

    # Same header, different spacing or casing in the raw file.
    cosmetic = []
    raw_prev = {_key(h): str(h) for h in previous if normalise_header(h)}
    raw_curr = {_key(h): str(h) for h in current if normalise_header(h)}
    for k in set(raw_prev) & set(raw_curr):
        if raw_prev[k] != raw_curr[k]:
            cosmetic.append({"header": curr_keys[k],
                             "before": raw_prev[k], "after": raw_curr[k]})

    moved = []
    for k in set(prev_keys) & set(curr_keys):
        before, after = prev.index(prev_keys[k]), curr.index(curr_keys[k])
        if before != after:
            moved.append({"header": curr_keys[k], "from": before, "to": after})

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "moved": sorted(moved, key=lambda m: m["to"]),
        "cosmetic": sorted(cosmetic, key=lambda c: c["header"]),
        "has_changes": bool(added or removed),
    }


def describe(diff: dict, *, brand: str = "", season: str = "") -> list[str]:
    """Human sentences for the review screen — only what needs a decision."""
    who = " ".join(p for p in (brand, season) if p) or "This supplier"
    out: list[str] = []
    for h in diff["added"]:
        out.append(f"{who} added a column: “{h}” — decide where it maps in SAP")
    for h in diff["removed"]:
        out.append(f"{who} no longer sends “{h}” — check what depended on it")
    if diff["moved"]:
        out.append(f"{len(diff['moved'])} columns moved position "
                   "(handled automatically, no action needed)")
    if diff["cosmetic"]:
        out.append(f"{len(diff['cosmetic'])} headers differ only in spacing "
                   "or casing (ignored)")
    return out

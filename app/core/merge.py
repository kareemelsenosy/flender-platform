"""Multi-source Step 3 — merge with provenance.

Combine rows describing the same product from several sources (a SAP export, a
Google Sheet, a supplier PDF line sheet, …) into one record per sellable line,
taking the **best value per field** and **remembering where each value came
from** — including where sources disagreed, so the conflict-review step (Step 4)
can surface only the genuine disagreements for a human to resolve.

Merge grain:
  * **Style-level** fields (brand, description, colour, material, prices, image …)
    are merged across every row of the style, so a source that only carries
    style-level info (e.g. a PDF with material but no sizes) still enriches every
    size of that style.
  * **Line-level** fields (size, barcode, quantities) are merged within the exact
    sellable line, so distinct sizes never collapse together.

Value selection: sources are given in **priority order** (first = most
authoritative). For each field the highest-priority non-empty value wins; any
other source with a *different* non-empty value is recorded as a conflict.
Filling an empty field from a lower-priority source is enrichment, not a
conflict. Everything here is pure/deterministic and unit-tested.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from app.core.product_identity import style_key

# Fields that vary per size (merged within the exact line); everything else is
# treated as a style-level attribute shared across the style's sizes.
LINE_LEVEL_FIELDS = {
    "size", "barcode", "qty_available", "comming_soon_qty", "ordered_qty",
}
# Internal/bookkeeping keys that must never be treated as merge-able product data.
_SKIP_FIELDS = {"sizes", "color_code", "source_sheet", "source_order"}


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _nonempty(value) -> bool:
    return value is not None and str(value).strip() != ""


def _pick(candidates: "list[tuple[str, str]]", priority: "dict[str, int]"):
    """Choose the best value for a field.

    ``candidates`` are (source, value) pairs with non-empty values, in input
    order. Returns (chosen_value, chosen_source, conflicts) where ``conflicts``
    lists the (source, value) pairs whose value differs from the chosen one.
    """
    ranked = sorted(candidates, key=lambda sv: priority.get(sv[0], 10**6))
    chosen_source, chosen_value = ranked[0]
    chosen_norm = _norm(chosen_value)
    conflicts: list[dict] = []
    seen_conflict_norms: set[str] = set()
    for src, val in candidates:
        if src == chosen_source and val == chosen_value:
            continue
        n = _norm(val)
        if n != chosen_norm and n not in seen_conflict_norms:
            conflicts.append({"source": src, "value": val})
            seen_conflict_norms.add(n)
    return chosen_value, chosen_source, conflicts


def _merge_fields(fields, group, priority):
    """Merge a set of ``fields`` across ``group`` (list of (source, row)).
    Returns (values, provenance) dicts, only for fields that had a value."""
    values: dict = {}
    provenance: dict = {}
    for f in fields:
        cands = [(src, str(r.get(f)).strip()) for src, r in group if _nonempty(r.get(f))]
        if not cands:
            continue
        value, source, conflicts = _pick(cands, priority)
        values[f] = value
        provenance[f] = {"value": value, "source": source, "conflicts": conflicts}
    return values, provenance


def merge_sources(sources: "list[dict]") -> dict:
    """Merge product rows from several sources.

    ``sources``: list of ``{"name": str, "rows": list[dict]}`` in **priority
    order** (first = most authoritative).

    Returns ``{"records": [...], "summary": {...}}``. Each record:
      * ``values``          — merged field -> best value
      * ``provenance``      — field -> {value, source, conflicts:[{source,value}]}
      * ``conflict_fields`` — fields where sources disagreed
      * ``sources``         — sources that contributed to this line
      * ``style_key`` / ``line_key``
    """
    priority = {s["name"]: i for i, s in enumerate(sources)}
    tagged: list[tuple[str, dict]] = []
    for s in sources:
        for row in s["rows"]:
            tagged.append((s["name"], row))

    all_fields = set()
    for _, row in tagged:
        all_fields.update(k for k in row.keys() if not k.startswith("_") and k not in _SKIP_FIELDS)
    style_fields = sorted(f for f in all_fields if f not in LINE_LEVEL_FIELDS)
    line_fields = sorted(f for f in all_fields if f in LINE_LEVEL_FIELDS)

    # Group rows into style components with a union-find that links any two rows
    # sharing a barcode (EAN — the same product for certain, even if the sources
    # use different style codes) OR the same style_key. This honours the
    # barcode → code → name precedence: a shared EAN merges products that a
    # style-code-only grouping would have split.
    n = len(tagged)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # keep the earliest row as root

    first_by_barcode: dict[str, int] = {}
    first_by_style: dict[str, int] = {}
    for i, (_, row) in enumerate(tagged):
        bc = _norm(row.get("barcode"))
        if bc:
            union(i, first_by_barcode.setdefault(bc, i))
        sk = style_key(row)
        if sk:
            union(i, first_by_style.setdefault(sk, i))

    components: "OrderedDict[int, list]" = OrderedDict()
    for i in range(n):
        components.setdefault(find(i), []).append(i)

    records: list[dict] = []
    conflict_field_total = 0
    for root, idxs in components.items():
        group = [tagged[i] for i in idxs]
        style_vals, style_prov = _merge_fields(style_fields, group, priority)

        # Split the component's rows into real sellable lines (have a size or
        # barcode) vs. style-only rows (attribute carriers with no size). Within
        # a component, line identity is the barcode if present, else the size —
        # so the same size lines up across sources that use different codes.
        line_groups: "OrderedDict[str, list]" = OrderedDict()
        for src, row in group:
            bc = _norm(row.get("barcode"))
            size = _norm(row.get("size"))
            if bc:
                key = f"ean:{bc}"
            elif size:
                key = f"sz:{size}"
            else:
                key = "__style_only__"
            line_groups.setdefault(key, []).append((src, row))

        real_lines = [k for k in line_groups if k != "__style_only__"]
        emit_keys = real_lines if real_lines else ["__style_only__"]

        for lk in emit_keys:
            grp = line_groups.get(lk, [])
            line_vals, line_prov = _merge_fields(line_fields, grp, priority)

            values = {**style_vals, **line_vals}
            provenance = {**style_prov, **line_prov}
            conflict_fields = sorted(f for f, p in provenance.items() if p["conflicts"])
            conflict_field_total += len(conflict_fields)

            # Credit every source that actually contributed a value to this line —
            # the winning source of any field or a recorded conflict value — so an
            # enrichment-only source (e.g. a PDF adding material) is reflected too.
            srcs: list[str] = []
            for f in sorted(provenance):
                p = provenance[f]
                for s in [p["source"]] + [c["source"] for c in p["conflicts"]]:
                    if s and s not in srcs:
                        srcs.append(s)

            records.append({
                "style_key": f"comp:{root}",
                "line_key": f"comp:{root}|{lk}",
                "values": values,
                "provenance": provenance,
                "conflict_fields": conflict_fields,
                "sources": srcs,
            })

    summary = {
        "sources": [s["name"] for s in sources],
        "input_rows": len(tagged),
        "merged_lines": len(records),
        "styles": len(components),
        "lines_from_multiple_sources": sum(1 for r in records if len(r["sources"]) > 1),
        "conflict_lines": sum(1 for r in records if r["conflict_fields"]),
        "conflict_fields": conflict_field_total,
    }
    return {"records": records, "summary": summary}

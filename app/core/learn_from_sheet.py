"""Learning a brand's rules from a SAP sheet a person already finished.

The fastest way to teach the system a brand is to show it the answer: the
TEMP sheet Flender corrected by hand for a past (or this) collection. Joined
to the collection's own rows by barcode — or by style when the barcodes do
not meet — it says, without anyone typing a rule:

* which of the brand's columns decides the SAP item group, and how each value
  maps (HUF: "P3 Name" -> SHORT SLEEVE TEES = T-SHIRTS, 27 of 28 clean);
* the SAP size lists and the sort index of every size;
* which columns the brand's TEMP carries, in what order;
* columns that hold one constant value nothing else can supply (U_VCName P).

Pure: takes rows, returns what was learned. Applying it is the service's job.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

# TEMP columns the package builds from data: never learned as a constant,
# even when every row of one collection happens to agree (all HUF is M).
_DERIVED = {"U_Season", "U_CodeBars", "U_ESDate", "U_SuppCatNum", "U_Gender",
            "U_ItmsGrpCod", "U_SCode", "U_SizeCode", "U_Webbeschreibung2",
            "U_BaseColor", "U_Material", "U_HS_Code", "U_COO", "U_Webbeschreibung",
            "U_def_size_list_code", "U_Web_Color", "U_SortIndex",
            "U_webbeschreibung_gr", "U_LongDscp", "U_OSRow", "U_VCat"}


def _v(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in ("nan", "none", "nat") else text


def _digits(value) -> str:
    return re.sub(r"\D", "", re.sub(r"\.0$", "", _v(value)))


def _join(finished: list[dict], rows: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each finished line with the collection row it came from."""
    by_bc = {}
    by_style = {}
    for r in rows:
        bc = _digits(r.get("Barcode"))
        if bc:
            by_bc.setdefault(bc, r)
        style = _v(r.get("Item No.")).upper()
        if style:
            by_style.setdefault(style, r)
    pairs = []
    for f in finished:
        src = by_bc.get(_digits(f.get("U_CodeBars")))
        if src is None:
            src = by_style.get(_v(f.get("U_SCode")).upper())
        if src is not None:
            pairs.append((f, src))
    return pairs


def learn_item_groups(pairs) -> dict:
    """Find the brand column that best predicts U_ItmsGrpCod, and its map.

    Every text column of the source rows is tried; the one whose values map
    most cleanly onto a single SAP group wins. Returns ``{"column", "map",
    "clean", "total"}`` or ``{}`` when nothing predicts it.
    """
    by_col: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for f, src in pairs:
        group = _v(f.get("U_ItmsGrpCod")).upper()
        if not group:
            continue
        for col, val in src.items():
            if str(col).startswith(("_", "(unnamed")) or col in ("Item No.", "Barcode", "Size"):
                continue
            val = _v(val)
            if val and len(val) <= 60:
                by_col[col][val.upper()][group] += 1

    best, best_score = None, 0.0
    for col, values in by_col.items():
        n_values = len(values)
        if n_values < 2:
            continue
        rows_total = sum(sum(c.values()) for c in values.values())
        rows_clean = sum(c.most_common(1)[0][1] for c in values.values())
        # Clean, and general: a style-level column (one value per style) is
        # perfectly "clean" but teaches nothing for the next season.
        styles = len({_v(f.get("U_SCode")) for f, _s in pairs})
        if n_values >= max(styles * 0.8, 2):
            continue
        score = rows_clean / rows_total
        # On a tie, the readable column ("P3 Name") over its code ("P3").
        if re.search(r"name|category|group|type|desc", str(col), re.I):
            score += 1e-6
        if score > best_score:
            best, best_score = col, score
    if best is None or best_score < 0.8:
        return {}
    mapping = {val: [g for g, _n in c.most_common()] for val, c in by_col[best].items()}
    clean = sum(1 for g in mapping.values() if len(g) == 1)
    return {"column": best, "map": mapping, "clean": clean, "total": len(mapping),
            "agreement": round(best_score, 3)}


def learn_size_lists(finished: list[dict]) -> list[dict]:
    """SAP size lists with each size's sort index, as the finished sheet has them."""
    sizes: dict[str, dict[str, int]] = defaultdict(dict)
    for f in finished:
        code = _v(f.get("U_def_size_list_code"))
        size = _v(f.get("U_SizeCode"))
        try:
            sort = int(float(_v(f.get("U_SortIndex"))))
        except ValueError:
            continue
        if code and size:
            sizes[code].setdefault(size, sort)
    out = []
    for code, by_size in sizes.items():
        ordered = sorted(by_size.items(), key=lambda kv: kv[1])
        out.append({"list_code": code,
                    "sizes": ", ".join(z for z, _o in ordered),
                    "sort": ", ".join(str(o) for _z, o in ordered)})
    return out


def learn_template(columns: list[str], finished: list[dict]) -> "tuple[list[dict], dict]":
    """The finished sheet's columns as the brand's TEMP template.

    Returns ``(template, constants)``; a column every row fills with the same
    value, that the builder cannot derive, becomes a fixed value.
    """
    constants = {}
    for col in columns:
        if col in _DERIVED:
            continue
        values = {_v(f.get(col)) for f in finished}
        if len(values) == 1 and next(iter(values)):
            constants[col] = next(iter(values))
    template = [{"column": c, "label": c, "include": True,
                 "value": constants.get(c, "")} for c in columns]
    return template, constants


def learn_upper_words(finished: list[dict]) -> list[str]:
    """Words the brand writes in capitals in its descriptions (EXO, KCUF)."""
    from app.core.temp_template import ACRONYMS
    seen: Counter = Counter()
    for f in finished:
        for w in _v(f.get("U_Webbeschreibung2")).split():
            core = re.sub(r"[^A-Za-z0-9]", "", w)
            if len(core) >= 2 and core.isupper() and core.isalpha() and core not in ACRONYMS:
                seen[core] += 1
    return sorted(seen)


def learn(finished: list[dict], columns: list[str], rows: list[dict]) -> dict:
    """Everything a finished TEMP teaches about the brand."""
    pairs = _join(finished, rows)
    return {
        "matched": len(pairs), "lines": len(finished),
        "item_groups": learn_item_groups(pairs),
        "size_lists": learn_size_lists(finished),
        "template": learn_template(columns, finished),
        "keep_upper": learn_upper_words(finished),
    }

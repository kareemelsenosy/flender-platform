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
    return "" if text.lower() in ("nan", "none") or text == "NaT" else text


def _digits(value) -> str:
    return re.sub(r"\D", "", re.sub(r"\.0$", "", _v(value)))


def _flat(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", _v(value).upper())


def _join(finished: list[dict], rows: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each finished line with the collection row it came from.

    By barcode when there is one; else by the built code (Dime has no EANs:
    U_CodeBars DIMESU262CHAXXS is the order form's DIMESU262CHA + size XXS);
    else by style, with or without the colour code still on it.
    """
    by_bc, by_code_size, by_style = {}, {}, {}
    for r in rows:
        bc = _digits(r.get("Barcode"))
        if len(bc) >= 8:
            by_bc.setdefault(bc, r)
        style = _flat(r.get("Item No."))
        code = _flat(r.get("Colour Code"))
        size = _flat(r.get("Size"))
        if style:
            by_code_size.setdefault(style + size, r)
            by_code_size.setdefault(style + code + size, r)
            by_style.setdefault(style, r)
    pairs = []
    for f in finished:
        cb = _v(f.get("U_CodeBars"))
        src = by_bc.get(_digits(cb)) if _digits(cb) == _flat(cb) else None
        if src is None:
            src = by_code_size.get(_flat(cb))
        if src is None:
            src = by_style.get(_flat(f.get("U_SCode")))
        if src is None:
            sc = _flat(f.get("U_SCode"))
            src = next((r for k, r in by_style.items() if sc and k.startswith(sc)), None)
        if src is not None:
            pairs.append((f, src))
    return pairs


def learn_settings(pairs) -> dict:
    """Brand habits a finished sheet shows, as settings.

    * ``gender_map`` — the brand's gender words to SAP's letter (Komono:
      Ladies W, Unisex U, and an empty gender U).
    * ``hs_digits`` — when SAP keeps fewer digits than the supplier writes
      (Komono 9004109100 -> 90041091).
    * ``style_has_colour_code`` — when SAP's style is the supplier's code
      with the colour code taken off the end (Dime DIMESU262CHA -> DIMESU262).
    """
    out: dict = {}
    genders: dict[str, Counter] = defaultdict(Counter)
    hs_cut = hs_seen = split = split_seen = 0
    for f, src in pairs:
        target_g = _v(f.get("U_Gender"))
        if target_g:
            genders[_v(src.get("Sex")).lower()][target_g] += 1
        t_hs, s_hs = _digits(f.get("U_HS_Code")), _digits(src.get("Duty Position"))
        if t_hs and s_hs:
            hs_seen += 1
            if len(t_hs) < len(s_hs) and s_hs.startswith(t_hs):
                hs_cut += 1
        t_style = _flat(f.get("U_SCode"))
        raw = _flat(src.get("Original Item No.") or src.get("Item No."))
        if t_style and raw:
            split_seen += 1
            rest = raw[len(t_style):] if raw.startswith(t_style) else ""
            if rest and rest.isalpha() and 2 <= len(rest) <= 4:
                split += 1
    # Whose product names SAP got: the product list's (HUF) or the order
    # sheet's (Dime), where the two differ.
    from_list = from_order = 0
    for f, src in pairs:
        target = " ".join(_v(f.get("U_Webbeschreibung2")).lower().split())
        listed = " ".join(_v(src.get("Item Description")).lower().split())
        ordered = " ".join(_v(src.get("Order Item Description")).lower().split())
        if target and ordered and listed != ordered:
            from_order += target == ordered
            from_list += target == listed
    if from_order > from_list:
        out["description_from"] = "order"
    elif from_list:
        out["description_from"] = "list"
    gmap = {k: c.most_common(1)[0][0] for k, c in genders.items()}
    if gmap:
        out["gender_map"] = gmap
    if hs_seen and hs_cut / hs_seen >= 0.9:
        lengths = Counter(len(_digits(f.get("U_HS_Code"))) for f, _s in pairs
                          if _digits(f.get("U_HS_Code")))
        out["hs_digits"] = lengths.most_common(1)[0][0]
    if split_seen and split / split_seen >= 0.8:
        out["style_has_colour_code"] = True
    # The size in a built barcode: the supplier's own label (Dime
    # DIMESU2625BLUOS) or SAP's spelling (Hiking Patrol HP0127302BLACKONESIZE).
    # Only sizes the two write differently tell them apart.
    from app.core.temp_template import normalise_size
    own = sap = 0
    for f, src in pairs:
        size = _v(src.get("Size"))
        cb = _v(f.get("U_CodeBars")).upper()
        if not size or _digits(cb) == _flat(cb):
            continue
        mine, theirs = _flat(size), _flat(normalise_size(size))
        if mine == theirs:
            continue
        flat_cb = _flat(cb)
        own += flat_cb.endswith(mine) and not flat_cb.endswith(theirs)
        sap += flat_cb.endswith(theirs)
    if own > sap:
        out["barcode_size"] = "supplier"
    # Every line one-size in SAP while the supplier's "Size" says something
    # else: Komono's Size is the frame (61☐14-140), not a size.
    target_sizes = {_v(f.get("U_SizeCode")).lower() for f, _s in pairs}
    supplier = [_v(src.get("Supplier Size") or src.get("Size")) for _f, src in pairs]
    if (pairs and target_sizes and target_sizes <= {"onesize", "os", "one size"}
            and sum(bool(x) and normalise_size(x) != "onesize" for x in supplier)
            >= 0.8 * len(pairs)):
        out["one_size"] = True
    # 'Matte Black/Tortoise' kept with its slash (Komono) or written
    # Black-White (HUF, Carhartt).
    kept = changed = 0
    for f, src in pairs:
        colour = _v(src.get("Color"))
        if re.search(r"\S/\S", colour):
            web = _v(f.get("U_Web_Color"))
            kept += "/" in web
            changed += "/" not in web and bool(web)
    if kept > changed:
        out["colour_slash"] = "keep"
    return out


def learn_colours(pairs) -> list[dict]:
    """Colour name -> base colour, as the finished sheet classified them."""
    seen: dict[str, Counter] = defaultdict(Counter)
    spelled: dict[str, str] = {}
    for f, src in pairs:
        base = _v(f.get("U_BaseColor"))
        name = _v(src.get("Color")) or _v(f.get("U_Web_Color"))
        if base and name:
            key = name.lower()
            seen[key][base.strip()] += 1
            spelled.setdefault(key, name.strip())
    return [{"colour": spelled[k], "base": c.most_common(1)[0][0]} for k, c in seen.items()]


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
            # Prices and quantities predict nothing about a product group.
            if val and len(val) <= 60 and not re.fullmatch(r"[\d.,\s%-]+", val):
                by_col[col][val.upper()][group] += 1

    with_group = sum(1 for f, _s in pairs if _v(f.get("U_ItmsGrpCod")))
    best, best_score, best_values = None, 0.0, 0
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
        # A column empty on most lines decides nothing for them.
        score = rows_clean / max(rows_total, with_group)
        # On a tie, the readable column ("P3 Name") over its code ("P3").
        if re.search(r"name|category|group|type|desc", str(col), re.I):
            score += 1e-6
        # Equally clean: the column with fewer values is the more general
        # one — Komono's Item Group (2 values) over its Item Description (149).
        if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9
                                         and n_values < best_values):
            best, best_score, best_values = col, score, n_values
    words = _product_word_groups(pairs, with_group)
    # The product word only when it beats every column: a real category
    # column is what the brand means, and HUF's P3 Name is exact.
    if words and words["agreement"] > best_score + 0.02 and words["agreement"] >= 0.8:
        return words
    if best is None or best_score < 0.8:
        return {}
    mapping = {val: [g for g, _n in c.most_common()] for val, c in by_col[best].items()}
    clean = sum(1 for g in mapping.values() if len(g) == 1)
    return {"column": best, "map": mapping, "clean": clean, "total": len(mapping),
            "agreement": round(best_score, 3)}


def _product_word_groups(pairs, with_group: int) -> dict:
    """SAP group by the product word that ends the name (Dime: Jacket -> JACKETS).

    The last word decides; the last two only where they say something else
    ('LS T-Shirt' is LONGSL where 'T-Shirt' is T-SHIRTS).
    """
    from app.core.temp_template import product_words

    last: dict[str, Counter] = defaultdict(Counter)
    two: dict[str, Counter] = defaultdict(Counter)
    for f, src in pairs:
        group = _v(f.get("U_ItmsGrpCod")).upper()
        keys = product_words(src.get("Item Description") or f.get("U_Webbeschreibung2"))
        if not group or not keys:
            continue
        last[keys[-1]][group] += 1
        if len(keys) > 1:
            two[keys[0]][group] += 1
    if len(last) < 2 or not with_group:
        return {}
    mapping = {w: [g for g, _n in c.most_common()] for w, c in last.items()}
    for pair, c in two.items():
        top = c.most_common(1)[0][0]
        if top != mapping[pair.split(" ")[-1]][0] and c[top] == sum(c.values()):
            mapping[pair] = [top]
    right = 0
    for f, src in pairs:
        group = _v(f.get("U_ItmsGrpCod")).upper()
        keys = product_words(src.get("Item Description") or f.get("U_Webbeschreibung2"))
        hit = next((mapping[k] for k in keys if k in mapping), [])
        right += bool(group and hit and hit[0] == group)
    # Names that teach nothing general: one word per style is a style list.
    styles = len({_v(f.get("U_SCode")) for f, _s in pairs})
    if len(last) >= max(styles * 0.8, 2):
        return {}
    clean = sum(1 for g in mapping.values() if len(g) == 1)
    return {"column": "", "by_product_word": True, "map": mapping, "clean": clean,
            "total": len(mapping), "agreement": round(right / with_group, 3)}


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
        "settings": learn_settings(pairs),
        "colours": learn_colours(pairs),
    }

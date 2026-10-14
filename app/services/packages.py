"""Generating and approving output packages — Operations OS.

A package is a pure function of three things: the supplier file, what SAP
already knows, and the decisions a human has made. Nothing is stored between
them, so regenerating always reflects the decisions currently on record and a
10,000-row collection never lands in the database.

The important behaviour here is how exceptions become decisions. The SS27
Carhartt run raises 1,267 rows needing a base colour, but those rows share only
59 distinct colour names — so the screen asks 59 questions, not 1,267. Every
exception therefore carries a *subject* (the colour name, the subcategory), and
identical subjects collapse into one decision that applies everywhere.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone

import pandas as pd

from app.config import OUTPUT_DIR
from app.core import field_mapping
from app.core import intake as intake_core
from app.core.base_color import DEFAULT_VOCAB, learn_lookup
from app.core.pricing import learn_strategy, review_prices
from app.core.sap_creation import SAP_COLUMNS, build_creation_sheet
from app.core.sheet_layout import load_supplier_table
from app.core.temp_template import TEMP_COLUMNS, build_temp_sheet

PRODUCT_CREATION = "product_creation"
TEMP = "temp"
PRICING = "pricing"
ATTRIBUTES = "attributes"
IMAGES = "images"

KINDS = {
    PRODUCT_CREATION: "SAP Product Creation",
    TEMP: "TEMP Standard Template",
    PRICING: "SAP Pricing",
    ATTRIBUTES: "Product Attributes",
    IMAGES: "Product Images",
}

# Which supplier file each package is built from.
SOURCE_KIND = {
    PRODUCT_CREATION: intake_core.ORDER_SHEET,
    TEMP: intake_core.ORDER_SHEET,
    PRICING: intake_core.PRICE_LIST,
    ATTRIBUTES: intake_core.ORDER_SHEET,
    IMAGES: intake_core.ORDER_SHEET,
}

# Attributes ask a model about every style, which takes minutes on a real
# collection, so that package runs in the background.
ASYNC_KINDS = {ATTRIBUTES}


def _utcnow():
    return datetime.now(timezone.utc)


def decision_key(field: str, subject: str) -> str:
    """One question, however many rows it covers."""
    return f"{field}::{subject}".strip()


def group_exceptions(exceptions) -> list[dict]:
    """Collapse row-level exceptions into the questions a person answers.

    Identical subjects merge, carrying the row count so the reviewer can see
    that one answer settles nine hundred lines.
    """
    grouped: dict[str, dict] = {}
    for e in exceptions:
        subject = e.get("subject") or _subject_from(e)
        key = decision_key(e.get("field", ""), subject)
        g = grouped.setdefault(key, {
            "key": key, "field": e.get("field", ""), "subject": subject,
            "severity": e.get("severity", "warning"),
            "reason": e.get("reason", ""), "suggestion": e.get("suggestion", ""),
            "options": e.get("options") or [], "rows": 0, "skus": [],
        })
        g["rows"] += 1
        if len(g["skus"]) < 5 and e.get("sku"):
            g["skus"].append(e["sku"])
        # A critical anywhere makes the whole question critical.
        if e.get("severity") == "critical":
            g["severity"] = "critical"
        elif e.get("severity") == "manual_review" and g["severity"] == "warning":
            g["severity"] = "manual_review"

    order = {"critical": 0, "manual_review": 1, "warning": 2}
    return sorted(grouped.values(),
                  key=lambda g: (order.get(g["severity"], 3), -g["rows"]))


def _subject_from(e) -> str:
    """Pull the thing being decided out of an exception's own words."""
    reason = e.get("reason", "")
    if "'" in reason:
        return reason.split("'")[1]
    return e.get("sku", "") or e.get("field", "")


# ── Loading the collection's source rows ─────────────────────────────────────

def _read_mapped(path: str, overrides: dict, legend: dict | None = None
                 ) -> "tuple[list[dict], dict]":
    """One supplier file as rows in standard field names, one row per size.

    Everything a brand-specific layout needs is settled here, so the package
    builders only ever see one shape: the column mapping, a colour code split
    over unlabelled columns, section-heading rows dropped, sizes expanded,
    and a price that is not in EUR kept out of the EUR column.
    """
    from app.core.size_expand import expand_rows

    table, _info = load_supplier_table(path)
    if table.empty:
        return [], {}
    # Unlabelled columns keep their position so a layout that relies on them
    # (Edwin's colour code) can still be read; they are never mapped by name.
    table.columns = [c if str(c).strip() else f"(unnamed {i})"
                     for i, c in enumerate(table.columns)]
    table = table.loc[:, ~table.columns.duplicated()]
    headers = [str(c) for c in table.columns if not str(c).startswith("__")]
    mapping = field_mapping.detect(headers, overrides)

    # A column titled with a scale letter holds each row's scale
    # (Thisisneverthat's "A" column, with B and C defined beneath it).
    if not mapping["Size"]["header"] and legend:
        for h in headers:
            if str(h).strip() in legend:
                mapping["Size"] = {"header": str(h), "source": "detected",
                                   "note": "size-scale column"}
                break

    rows = field_mapping.apply(table.to_dict("records"), mapping)
    _colour_code_from_unnamed(rows, headers, mapping)

    # Section headings ("MIJ Men Pants"), legend rows and totals carry no
    # style, or a "style" with nothing else on the row.
    rows = [r for r in rows if not _blank(r.get("Item No."))
            and any(not _blank(r.get(f)) for f in
                    ("Color", "Item Description", "Size", "Barcode", "Colour Code"))]

    for field in field_mapping.PRICE_FIELDS:
        cur = field_mapping.foreign_price(mapping, field)
        if cur:
            plain = "Wholesale" if field.startswith("Wholesale") else "Retail"
            for r in rows:
                r[f"Supplier {plain} Price"] = r.get(mapping[field]["header"])
                r[f"Supplier {plain} Currency"] = cur

    rows, size_note = expand_rows(rows, headers,
                                  has_size_field=bool(mapping["Size"]["header"]))
    mapping["_size_note"] = size_note
    return rows, mapping


def _colour_code_from_unnamed(rows, headers, mapping) -> None:
    """Edwin writes the colour code as two unlabelled columns after the style
    number — house colour then finish (89 + 99), the same scheme Carhartt
    spells out as one Colour Code. Joined only when the cells are short codes.
    """
    if mapping["Colour Code"]["header"] or not mapping["Item No."]["header"]:
        return
    try:
        at = headers.index(mapping["Item No."]["header"])
    except ValueError:
        return
    parts = []
    for h in headers[at + 1:at + 3]:
        if not h.startswith("(unnamed"):
            break
        parts.append(h)
    if not parts:
        return
    sample = [str(r.get(parts[0]) or "") for r in rows if not _blank(r.get("Item No."))][:50]
    if not sample or not all(len(v.strip()) <= 3 for v in sample if v and v != "nan"):
        return
    def part(v):
        v = str(v).strip()
        # Excel stores "01" as the number 1; the codes are two characters.
        return v.zfill(2) if v.isdigit() and len(v) == 1 else v

    for r in rows:
        code = "".join(part(r.get(h)) for h in parts if not _blank(r.get(h)))
        if code:
            r["Colour Code"] = code
    mapping["Colour Code"] = {"header": " + ".join(parts), "source": "detected",
                              "note": "joined from the unlabelled columns after the style number"}


def _rules(job, rules=None) -> dict:
    if rules is not None:
        return rules
    from sqlalchemy.orm import object_session
    from app.services.brand_rules import rules_for
    db = object_session(job)
    if db is None:
        from app.core.brand_rules import seed_for
        return seed_for(job.brand or "")
    return rules_for(db, job.brand or "")


def collection_rows(job, kind: str, rules=None) -> "tuple[list[dict], str, dict]":
    """Rows a package is built from: mapped, combined and size-expanded.

    For the order sheet, any product list in the collection (HS codes, UPCs)
    is joined on, and the report says which items did not join on either
    side. Returns ``(rows, problem, combine_report)``.
    """
    from app.core.brand_rules import (
        column_overrides, expand_scale_rows, read_size_legend, scale_codes)
    from app.core.combine import combine

    rules = _rules(job, rules)
    overrides = column_overrides(rules["tables"])
    want = SOURCE_KIND.get(kind, intake_core.ORDER_SHEET)
    files = [f for f in job.files if f.kind == want and f.parse_path]
    if not files and want == intake_core.PRICE_LIST:
        # A brand with prices inside the order sheet still has a price package.
        files = [f for f in job.files
                 if f.kind == intake_core.ORDER_SHEET and f.parse_path]
    if not files:
        return [], f"no {intake_core.KIND_LABELS.get(want, want)} in this collection", {}

    rows: list[dict] = []
    legend: dict[str, list[str]] = {}
    for f in files:
        if not os.path.exists(f.parse_path):
            continue
        own = read_size_legend(f.parse_path)
        rows.extend(_read_mapped(f.parse_path, overrides, own)[0])
        for code, sizes in own.items():
            legend.setdefault(code, sizes)
    if not rows:
        return [], "the supplier file could not be read", {}

    report: dict = {}
    if legend:
        report["size_legend"] = legend
    if want == intake_core.ORDER_SHEET:
        list_rows: list[dict] = []
        for f in job.files:
            if f.kind == intake_core.PRODUCT_LIST and f.parse_path \
                    and os.path.exists(f.parse_path):
                list_rows.extend(_read_mapped(
                    f.parse_path, overrides, read_size_legend(f.parse_path))[0])
        if list_rows:
            rows, joined = combine(rows, list_rows,
                                   scale_codes=scale_codes(rules["tables"]) | set(legend))
            report.update(joined)
    rows = expand_scale_rows(rows, rules["tables"], legend)
    rows = fill_from_collection(rows, job)
    rows, dropped = _dedupe_lines(rows)
    if dropped:
        report["duplicate_lines_removed"] = dropped
    return rows, "", report


def _abbreviates(suffix: str, colour) -> bool:
    """Whether a style-number suffix is the colour written short.

    HUF: 'JK00520-DKBRN' for DARK BROWN, 'JK00521-MGENT' for MAGENTA. Market's
    'MKT26Q1-SS0589' is a style code that happens to contain a dash, and must
    not be split. The suffix's letters have to appear, in order, in the colour.
    """
    suf = re.sub(r"[^A-Z]", "", str(suffix or "").upper())
    col = re.sub(r"[^A-Z]", "", str(colour or "").upper())
    if not suf or not col or not str(suffix).isalpha():
        return False
    it = iter(col)
    return suf[0] == col[0] and all(ch in it for ch in suf)


def _dedupe_lines(rows: list[dict]) -> "tuple[list[dict], int]":
    """Each sellable product once.

    Order sheets list the same line more than once — Market repeats it per
    delivery, HUF per delivery tab, Karl Kani's prepacks overlap in size.
    SAP creates a product once, so the first occurrence is kept. The key is
    the barcode when there is one, otherwise style + colour + size + size run
    (Edwin's leg length makes 32/30 and 32/34 different products).
    """
    out, seen, dropped = [], set(), 0
    for r in rows:
        norm = lambda k: re.sub(r"\s+", " ", str(r.get(k) or "")).strip().upper()   # noqa: E731
        bc = "" if _blank(r.get("Barcode")) else norm("Barcode")
        key = ("bc", bc, norm("Item No.")) if bc else ("line", norm("Item No."),
                                     norm("Colour Code") or norm("Color"),
                                     norm("Size"), norm("Size Run"))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dropped


_SPLIT_STYLE = re.compile(r"^([A-Z0-9]{4,})-([A-Z0-9]{2,8})$", re.I)


def fill_from_collection(rows: list[dict], job) -> list[dict]:
    """Fields the supplier never writes but the collection already knows.

    * **Brand** — HUF's order form has no brand column; the collection does.
    * **Colour code** — HUF writes the colour into the style number
      ('JK00525-CAMO'); split it so the style is the style.
    * **SKU** — style.colour.size, Carhartt's own shape, when none is given.

    Never overwrites a value the supplier did send.
    """
    brand = (job.brand or "").upper()
    for row in rows:
        if brand and _blank(row.get("Brand")):
            row["Brand"] = brand
        item = str(row.get("Item No.") or "").strip()
        m = _SPLIT_STYLE.match(item)
        if m and _blank(row.get("Colour Code")) and _abbreviates(m.group(2), row.get("Color")):
            row["Item No."], row["Colour Code"] = m.group(1), m.group(2).upper()
        if _blank(row.get("SKU")):
            colour = (row.get("Colour Code") if not _blank(row.get("Colour Code"))
                      else row.get("Color"))
            parts = [str(v or "").strip() for v in
                     (row.get("Item No."), colour, row.get("Size Run"), row.get("Size"))]
            if all(parts[i] and parts[i].lower() != "nan" for i in (0, 1, 3)):
                row["SKU"] = ".".join(p.replace(" ", "").upper() for p in parts
                                      if p and p.lower() != "nan")
    return rows


def _blank(value) -> bool:
    return value is None or str(value).strip().lower() in ("", "nan", "none", "null")


def _source_rows(job, kind: str, rules=None) -> "tuple[list[dict], str]":
    """Rows from the supplier file this package is built from."""
    rows, problem, _report = collection_rows(job, kind, rules)
    return rows, problem


def _brand_config(db, job):
    from app.models import BrandConfig
    if not job.brand:
        return None
    return db.query(BrandConfig).filter(
        BrandConfig.user_id == job.user_id,
        BrandConfig.brand == job.brand).first()


def _sap_reference(config):
    """The brand's live SAP feed, when one is configured."""
    if not config or not config.sap_sheet_id:
        return None, "no SAP sheet configured for this brand"
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from app.core.sap_reference import SapReference, parse_workbook
        creds = Credentials.from_service_account_file(
            "credentials/google_credentials.json",
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly",
                    "https://www.googleapis.com/auth/drive.readonly"])
        sh = gspread.authorize(creds).open_by_key(config.sap_sheet_id)
        tabs = {ws.title: ws.get_all_values() for ws in sh.worksheets()}
        return SapReference(parse_workbook(tabs)), ""
    except Exception as exc:               # a missing feed must not stop the run
        return None, f"SAP sheet could not be read: {type(exc).__name__}"


# ── Generation ───────────────────────────────────────────────────────────────

def generate(db, job, kind: str, decisions=None) -> dict:
    """Build one package. Returns the result; does not persist it."""
    from app.core.brand_rules import colour_table_lookup
    from app.services.brand_rules import rules_for

    decisions = decisions or {}
    rules = rules_for(db, job.brand or "")
    rows, problem = _source_rows(job, kind, rules)
    if problem:
        return {"error": problem, "columns": [], "rows": [], "exceptions": [],
                "summary": {}}

    uses_base = bool(rules["settings"].get("uses_base_colour"))
    colour_table = colour_table_lookup(rules["tables"])

    config = _brand_config(db, job)
    reference, ref_note = _sap_reference(config)

    colour_lookup = dict((config.colour_map if config else {}) or {})
    group_map = dict((config.group_map if config else {}) or {})

    # Decisions already taken become part of the lookups, so an answered
    # question never comes back.
    for key, value in decisions.items():
        field, _, subject = key.partition("::")
        if not subject or not value:
            continue
        if field in ("BASE COLOR", "U_BaseColor"):
            colour_lookup[subject.lower()] = value
        elif field in ("U_ItmsGrpCod", "Main Waregroup"):
            group_map[subject.upper()] = [value]

    # Answers ADD to the vocabulary; they never replace it. Narrowing it to
    # the values chosen so far made previously-resolved colours unresolvable,
    # so answering one question raised another.
    vocab = sorted(set(DEFAULT_VOCAB) | {v for v in colour_lookup.values() if v}
                   | {v["base"] for v in colour_table.values() if v["base"]})

    temporary: dict = {}
    if uses_base and kind in (PRODUCT_CREATION, TEMP):
        temporary = _temporary_colours(db, job, rows, vocab, colour_lookup,
                                       colour_table)
        from app.services.brand_rules import training_mode
        if training_mode(db):
            # What training mode just learned is in the table now; use it.
            rules = rules_for(db, job.brand or "")
            colour_table = colour_table_lookup(rules["tables"])

    if kind == PRODUCT_CREATION:
        out = build_creation_sheet(rows, season=job.season or "",
                                   vocab=vocab, lookup=colour_lookup,
                                   table=colour_table, temporary=temporary,
                                   base_colour=uses_base)
        _check_size_runs(db, job, out, rules)
    elif kind == TEMP:
        out = build_temp_sheet(
            rows, brand=job.brand or "", season=job.season or "",
            group_map=group_map, vocab=vocab, colour_lookup=colour_lookup,
            history=_history_from(reference, rows),
            earliest_ship_date=(config.earliest_ship_date if config else "") or "",
            colour_table=colour_table, temporary=temporary,
            base_colour=uses_base)
    elif kind == ATTRIBUTES:
        from app.core.attribute_engine import enrich_style
        from app.core.collection_attributes import (
            build_attribute_package, styles_from_rows)
        styles = styles_from_rows(rows, brand=job.brand or "",
                                  season=job.season or "")
        # A decision names the product type outright; that style is not
        # sent to the model again.
        chosen = {k.partition("::")[2]: v for k, v in decisions.items()
                  if k.startswith("product_type::")}

        def resolve(style):
            picked = chosen.get(style["style_code"])
            if picked:
                return {"product_type": picked, "confidence": 1.0}
            return enrich_style(style)

        run = _run_for(db, job, kind)

        def note(done, total):
            # Cheap and frequent: the reviewer wants to see it moving.
            if run is not None and (done == total or done % 5 == 0):
                run.progress_done, run.progress_total = done, total
                run.stage = f"Assigning attributes ({done} of {total} styles)"
                db.commit()

        failures = {"n": 0}

        def counted(style):
            result = resolve(style)
            if not result.get("product_type"):
                failures["n"] += 1
            return result

        def note_and_check(done, total):
            if run is not None:
                from app.models import PackageRun
                state = db.query(PackageRun.status).filter(
                    PackageRun.id == run.id).scalar()
                if state == "cancelling":
                    raise PackageCancelled()
            # A model that refuses every call (no credit, bad key) would
            # otherwise crawl through the whole collection producing nothing.
            if done >= 6 and failures["n"] >= done:
                from app.services.ai_service import ai_last_error_summary
                why = ai_last_error_summary()
                if any(w in why.lower() for w in _AI_DEAD):
                    raise RuntimeError(
                        "the AI provider is refusing requests — " + why[:200])
            note(done, total)

        out = build_attribute_package(styles, counted, on_progress=note_and_check,
                                      workers=ATTRIBUTE_WORKERS)

    elif kind == IMAGES:
        from app.core.collection_images import build_image_package
        supplied = [f.filename for f in job.files
                    if f.kind == intake_core.IMAGES]
        out = build_image_package(rows, reference=reference,
                                  supplied_files=supplied, decisions=decisions)

    elif kind == PRICING:
        priced = {"Flender WHS USD", "Flender Cost Prices (EUR-USD)",
                  "Flender RRP USD", "Flender WHS AED", "Flender RRP AED"}
        if rows and not (priced & set(rows[0])):
            # Not a Flender price sheet: start the price list from the
            # supplier's own prices, for a person to finish.
            from app.core.pricing import draft_price_list
            out = draft_price_list(rows)
            out["reference_note"] = ref_note
            return out
        history = _price_history(reference)
        strategy = learn_strategy(history) if history else {"known": False}
        review = review_prices(rows, strategy)
        out = {"columns": list(rows[0].keys()) if rows else [],
               "rows": rows, "exceptions": review["exceptions"],
               "summary": {**review["summary"], "strategy": strategy}}
    else:
        return {"error": f"unknown package {kind!r}", "columns": [], "rows": [],
                "exceptions": [], "summary": {}}

    out = _apply_fill_decisions(out, decisions)
    out["reference_note"] = ref_note
    if reference:
        out["summary"]["sap_compared"] = len(reference)
    return out


class PackageCancelled(Exception):
    """Someone pressed Cancel while the package was being built."""


# Styles assigned side by side. Bounded so one collection cannot exhaust a
# provider's rate limit on its own.
ATTRIBUTE_WORKERS = int(os.getenv("ATTRIBUTE_WORKERS", "6"))

_AI_DEAD = ("credit", "api key", "api_key", "authentication", "permission",
            "quota", "billing", "unauthorized", "invalid x-api-key")

# Decisions that hold one value for a whole column. Identifiers are excluded:
# one barcode typed in for nine thousand lines is never right — those come
# from mapping the column or adding the product list.
_NOT_FILLABLE = {"Barcode", "SKU", "Item No.", "Item.Colour", "U_CodeBars",
                 "U_SCode", "BASE COLOR", "U_BaseColor", "product_type",
                 "image_status", "U_ItmsGrpCod"}


def is_fillable(field: str) -> bool:
    return field not in _NOT_FILLABLE


def _apply_fill_decisions(out: dict, decisions: dict) -> dict:
    """Put a person's answer to a missing-column question into the sheet.

    Answering "Wholesale Price EUR is empty" used to be stored and then
    ignored on regeneration, so the question came straight back and the
    package could never be approved. The answer now fills the empty cells of
    that column, and the question is settled.
    """
    columns = set(out.get("columns") or [])
    fills = {}
    for key, value in (decisions or {}).items():
        field, _, subject = key.partition("::")
        if value and field in columns and is_fillable(field):
            fills[key] = (field, value)
    if not fills:
        return out
    for row in out.get("rows", []):
        for field, value in fills.values():
            if str(row.get(field) or "").strip() in ("", "nan", "None"):
                row[field] = value
    out["exceptions"] = [
        e for e in out.get("exceptions", [])
        if decision_key(e.get("field", ""),
                        e.get("subject") or _subject_from(e)) not in fills]
    return out


def _colour_names(rows) -> list[str]:
    names = []
    for r in rows:
        v = str(r.get("Color") or r.get("Colour") or "").strip()
        if v and v.lower() != "nan":
            names.append(v)
    return list(dict.fromkeys(names))


def _colours_with_codes(rows) -> dict[str, list[str]]:
    """Each colour name with the colour codes it appears under."""
    out: dict[str, list[str]] = {}
    for r in rows:
        v = str(r.get("Color") or r.get("Colour") or "").strip()
        if not v or v.lower() == "nan":
            continue
        code = str(r.get("Colour Code") or "").strip()
        codes = out.setdefault(v, [])
        if code and code.lower() != "nan" and code not in codes:
            codes.append(code)
    return out


def _temporary_colours(db, job, rows, vocab, lookup, table) -> dict:
    """Temporary base colours for names the brand colour table lacks.

    Also where the system asks to change the rules: every colour it had to
    work out for itself becomes a pending request in Brand Rules, so the
    table grows only with a person's approval. A suggestion already waiting
    as a request is reused instead of asking the model again.
    """
    from app.core.base_color import propose
    from app.core.brand_rules import resolve_from_table
    from app.services import brand_rules as rules_svc
    from app.services.colour_suggest import (
        catalogue_text, snippets_for, suggest)

    brand = job.brand or ""
    if not brand:
        return {}
    temporary: dict = {}
    for change in rules_svc.pending(db, brand):
        if change.table_name == "colours" and change.row.get("base"):
            temporary[change.row["colour"].lower()] = {
                "value": change.row["base"],
                "why": (change.reason or "suggested")[:160]}

    unresolved: list[str] = []
    for name, codes in _colours_with_codes(rows).items():
        # Settled when the table answers every code this name appears under
        # (or the name itself, for a brand without colour codes).
        if (codes and all(resolve_from_table(name, table, c) for c in codes)) \
                or (not codes and resolve_from_table(name, table)) \
                or lookup.get(name.lower()):
            continue
        guess = propose(name, vocab, lookup)
        if guess["value"] and name.strip().lower() == guess["value"].lower():
            continue                     # "Black" is Black; nothing to ask
        if guess["value"]:
            rules_svc.propose_rule(
                db, brand, "colours", {"colour": name, "base": guess["value"]},
                reason=f"Not in the colour table. Proposed {guess['value']} "
                       f"because {guess['reason']}.",
                source="system", job_id=job.id)
        elif name.lower() not in temporary:
            unresolved.append(name)

    if not unresolved:
        return temporary

    catalogues = [f.file_path for f in job.files
                  if f.kind == intake_core.CATALOG and f.file_path]
    snippets = snippets_for(unresolved, catalogue_text(catalogues))
    palette = [f"{n}={v['base']}" for n, v in list(table.items())[:60] if v["base"]]
    suggestions = suggest(brand, unresolved, vocab, snippets, palette)

    for name in unresolved:
        hit = suggestions.get(name)
        if hit:
            temporary[name.lower()] = hit
        rules_svc.propose_rule(
            db, brand, "colours",
            {"colour": name, "base": hit["value"] if hit else ""},
            reason=("Not in the colour table. Temporary colour "
                    + (f"{hit['value']} {hit['why']}." if hit else
                       "could not be suggested — choose the base colour.")),
            source="ai" if hit else "system", job_id=job.id)
    return temporary


def _check_size_runs(db, job, out, rules) -> None:
    """Flag size-run codes the brand's size-run table does not explain."""
    from app.core.brand_rules import size_run_meaning
    from app.services import brand_rules as rules_svc

    if not rules["tables"].get("size_runs"):
        return
    seen: set[str] = set()
    for row in out.get("rows", []):
        code = str(row.get("Size Run") or "").strip()
        if not code or code.lower() == "nan" or code in seen:
            continue
        seen.add(code)
        if size_run_meaning(code, rules["tables"]):
            continue
        out["exceptions"].append({
            "field": "Size Run", "subject": code, "severity": "warning",
            "sku": row.get("SKU", ""),
            "reason": f"size run '{code}' is not in the brand's size-run table "
                      f"— a request to add it is waiting in Brand Rules",
            "suggestion": "Describe this size run in Brand Rules"})
        rules_svc.propose_rule(
            db, job.brand or "", "size_runs", {"code": code, "meaning": ""},
            reason=f"Size run {code} appears in {job.label} but is not in the table.",
            job_id=job.id)


def _run_for(db, job, kind):
    from app.models import PackageRun
    return db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                       PackageRun.kind == kind).first()


def _history_from(reference, rows) -> dict:
    """Per-style values SAP already holds, for the fields an order form lacks."""
    if not reference:
        return {}
    history: dict[str, dict] = {}
    for r in rows:
        style = str(r.get("Style Number") or r.get("Item No.") or "").strip()
        if not style or style in history:
            continue
        known = reference.known_values(style=style,
                                       colour=str(r.get("Color") or ""))
        if known.get("item_group") or known.get("gender"):
            history[style] = {"U_Gender": known.get("gender", ""),
                              "U_VCName": known.get("gender", "")}
    return history


def _price_history(reference):
    if not reference:
        return []
    from app.core.sap_reference import price_history
    return price_history(reference.records)


# ── Persisting ───────────────────────────────────────────────────────────────

def run_package(db, job, kind: str, *, background: bool = True):
    """Generate, store the exceptions and write the sheet. Returns PackageRun.

    A package that calls a model per style is started in a thread and reports
    progress instead of holding the request open for minutes.
    """
    from app.models import PackageRun

    run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                      PackageRun.kind == kind).first()
    if run is None:
        run = PackageRun(job_id=job.id, kind=kind)
        db.add(run)
        db.commit()
        db.refresh(run)

    if run.status in ("approved", "delivered", "reconciled", "running",
                      "cancelling"):
        return run

    if background and kind in ASYNC_KINDS:
        run.status = "running"
        run.stage = "Reading the supplier file"
        run.progress_done, run.progress_total = 0, 0
        run.error = None
        db.commit()
        db.refresh(run)
        threading.Thread(target=_run_in_background,
                         args=(job.id, kind), daemon=True).start()
        return run

    out = generate(db, job, kind, run.decisions)
    if out.get("error"):
        run.status, run.error = "error", out["error"]
        db.commit()
        db.refresh(run)
        return run

    return _finish(db, job, run, out)


def _finish(db, job, run, out):
    """Store an outcome, whichever path produced it."""
    if out.get("error"):
        run.status, run.error = "error", out["error"]
        db.commit()
        db.refresh(run)
        return run

    grouped = group_exceptions(out["exceptions"])
    run.error = None
    run.row_count = len(out["rows"])
    run.critical_count = sum(1 for g in grouped if g["severity"] == "critical")
    run.review_count = sum(1 for g in grouped if g["severity"] == "manual_review")
    run.warning_count = sum(1 for g in grouped if g["severity"] == "warning")
    run.exceptions = grouped
    run.summary = {**out.get("summary", {}),
                   "reference_note": out.get("reference_note", "")}
    was = run.status
    run.status = "needs_decisions" if (run.critical_count or run.review_count) else "ready"
    run.stage = None
    run.file_path = _write_sheet(job, run.kind, out)
    db.commit()
    db.refresh(run)
    # Only on the first build; regenerating after a decision must not mail.
    if was in ("draft", "running"):
        _notify(db, run, "approval_needed")
    return run


def _run_in_background(job_id: int, kind: str):
    """Run a slow package on its own session, so a failure is recorded."""
    from app.database import SessionLocal
    from app.models import CollectionJob, PackageRun
    db = SessionLocal()
    try:
        job = db.get(CollectionJob, job_id)
        run = db.query(PackageRun).filter(PackageRun.job_id == job_id,
                                          PackageRun.kind == kind).first()
        if not job or not run:
            return
        try:
            _finish(db, job, run, generate(db, job, kind, run.decisions))
        except PackageCancelled:
            db.rollback()
            still_there = db.query(PackageRun).filter(
                PackageRun.id == run.id).first()
            if still_there is not None:
                still_there.status = "cancelled"
                still_there.stage = None
                still_there.error = None
                db.commit()
        except Exception as exc:
            # The collection can be deleted while this is still working, and
            # the failed flush leaves the session unusable — roll back before
            # trying to record anything, and say nothing if the row is gone.
            db.rollback()
            still_there = db.query(PackageRun).filter(
                PackageRun.id == run.id).first()
            if still_there is None:
                return
            still_there.status = "error"
            still_there.error = f"{type(exc).__name__}: {exc}"[:500]
            still_there.stage = None
            try:
                db.commit()
            except Exception:
                db.rollback()
    finally:
        db.close()


def progress_of(run) -> dict:
    """What to show while a package is still being built."""
    total = run.progress_total or 0
    done = run.progress_done or 0
    return {"running": run.status in ("running", "cancelling"),
            "done": done, "total": total,
            "stage": run.stage or "",
            "pct": round(done / total * 100) if total else 0}


def _write_sheet(job, kind: str, out) -> str:
    """Write the package to disk so it can be downloaded and handed to SAP."""
    folder = OUTPUT_DIR / f"collection_{job.id}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{(job.brand or 'collection').replace(' ', '_')}_{job.season or ''}_{kind}.xlsx"
    path = folder / name
    columns = out.get("columns") or (list(out["rows"][0].keys()) if out["rows"] else [])
    pd.DataFrame(out["rows"], columns=columns).to_excel(path, index=False)
    return str(path)


def apply_decision(db, run, key: str, value: str, user_id=None):
    """Record one answer and rebuild the package around it.

    A base colour chosen here is a person's decision about the brand, not
    just this collection, so it is written into the brand colour table (and
    settles any request waiting for that colour) — the next season never
    asks again.
    """
    decisions = run.decisions
    if value:
        decisions[key] = value
    else:
        decisions.pop(key, None)
    run.decisions = decisions
    db.commit()
    job = run.job
    field, _, subject = key.partition("::")
    if value and subject and job.brand and field in ("BASE COLOR", "U_BaseColor"):
        _record_colour_decision(db, job, subject, value, user_id)
    return run_package(db, job, run.kind)


def _record_colour_decision(db, job, colour: str, base: str, user_id=None):
    from app.services import brand_rules as rules_svc
    try:
        for change in rules_svc.pending(db, job.brand):
            if change.table_name == "colours" and \
                    str(change.row.get("colour", "")).lower() == colour.lower():
                rules_svc.decide_change(db, change.id, True, user_id=user_id,
                                        override={"base": base})
                return
        rules_svc.edit_row(db, job.brand, "colours", {"colour": colour, "base": base},
                           user_id=user_id, source="decision", job_id=job.id,
                           reason=f"Decided while reviewing {job.label}")
    except Exception:
        db.rollback()


def cancel(db, run) -> bool:
    """Ask a package that is still being built to stop.

    The worker checks between styles, so it stops within a few seconds; what
    it had already done is discarded and Generate starts again cleanly.
    """
    if run.status != "running":
        return False
    run.status = "cancelling"
    run.stage = "Cancelling…"
    db.commit()
    return True


def approve(db, run, user_id: int):
    """Sign the package off. Refused while anything critical is open."""
    if run.critical_count:
        return run, "critical exceptions must be resolved first"
    run.status = "approved"
    run.approved_by_id = user_id
    run.approved_at = _utcnow()
    db.commit()
    db.refresh(run)
    return run, ""


def _notify(db, run, which: str):
    """Send one status email. Never lets a mail failure affect the package."""
    from app.models import User
    from app.services import status_email
    try:
        owner = db.get(User, run.job.user_id)
        if not owner or not owner.email:
            return
        label = KINDS.get(run.kind, run.kind)
        getattr(status_email, which)(owner.email, run.job, run, label)
    except Exception:
        pass


def delivery_root(config=None) -> "Path":
    """Where approved sheets are put for SAP to collect.

    A brand's own setting wins, then the environment, then a local outbox —
    so an install can be pointed at the Dropbox import root from the settings
    page without waiting for a deploy.
    """
    from pathlib import Path
    if config is not None and getattr(config, "delivery_root", ""):
        return Path(config.delivery_root)
    return Path(os.getenv("DELIVERY_ROOT") or (OUTPUT_DIR / "outbox"))


# SAP watches a folder per package. The names mirror Flender's import folders.
DELIVERY_FOLDER = {
    PRODUCT_CREATION: "product_creation",
    TEMP: "temp",
    PRICING: "prices",
    ATTRIBUTES: "attributes",
    IMAGES: "images",
}


def deliver(db, run):
    """Copy an approved package into the SAP import folder.

    Only an approved package is ever delivered — that is the whole point of
    the gate. The file is stamped so a redelivery never silently overwrites
    the sheet SAP may already be reading.
    """
    import shutil
    if not run.is_approved:
        return run, "approve the package first"
    if not run.file_path or not os.path.exists(run.file_path):
        return run, "the package file is missing — regenerate it"

    folder = (delivery_root(_brand_config(db, run.job))
              / DELIVERY_FOLDER.get(run.kind, run.kind))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = _utcnow().strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_{os.path.basename(run.file_path)}"
        target = folder / name
        shutil.copy2(run.file_path, target)
    except Exception as exc:
        return run, f"could not write to the import folder ({type(exc).__name__})"

    summary = run.summary
    summary["delivered_to"] = str(target)
    summary["delivered_at"] = _utcnow().isoformat()
    run.summary = summary
    run.status = "delivered"
    db.commit()
    db.refresh(run)
    _notify(db, run, "delivered")
    return run, ""


def reconcile(db, run):
    """Re-read SAP and confirm the package actually landed.

    A file reaching the import folder is not the same as SAP having created
    anything, so the expected item codes are looked for in the brand's own
    nightly feed. Anything absent is reported rather than assumed late.
    """
    job = run.job
    if run.status not in ("delivered", "reconciled"):
        return run, "deliver the package first"

    config = _brand_config(db, job)
    reference, note = _sap_reference(config)
    if reference is None:
        return run, note or "no SAP sheet configured for this brand"

    out = generate(db, job, run.kind, run.decisions)
    if out.get("error"):
        return run, out["error"]

    # Counted per product, not per row: SAP creates items, and a product that
    # is absent is absent in every size. Counting rows against products would
    # report more found than were looked for.
    products: dict[str, bool] = {}
    for row in out["rows"]:
        style = str(row.get("U_SCode") or row.get("Item No.") or "").strip()
        colour = str(row.get("U_Web_Color") or row.get("Color") or "").strip()
        barcode = str(row.get("U_CodeBars") or row.get("Barcode") or "").strip()
        if not style:
            continue
        key = f"{style} {colour}".strip()
        if key in products:
            continue
        products[key] = bool(reference.find(style=style, colour=colour,
                                            barcode=barcode))

    missing = [k for k, found in products.items() if not found]
    run.expected_count = len(products)
    run.found_count = len(products) - len(missing)
    run.missing_in_sap = missing
    run.reconciled_at = _utcnow()
    run.status = "reconciled"
    db.commit()
    db.refresh(run)
    _notify(db, run, "reconciled")
    return run, ""


def find_sap_sheet(brand: str) -> "tuple[str, str]":
    """Look for a brand's nightly stock sheet in Drive.

    SAP writes one per brand, named like "Gramicci Stock". Finding it saves
    pasting an id, and says plainly when the sheet has not been shared rather
    than leaving an empty box.
    """
    if not brand:
        return "", "no brand set for this collection"
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_file(
            "credentials/google_credentials.json",
            scopes=["https://www.googleapis.com/auth/drive.readonly"])
        svc = build("drive", "v3", credentials=creds)
        safe = brand.replace("'", " ")
        res = svc.files().list(
            q=("mimeType='application/vnd.google-apps.spreadsheet' "
               f"and trashed=false and name contains '{safe}'"),
            fields="files(id,name,modifiedTime)", pageSize=50,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    except Exception as exc:
        return "", f"Drive could not be searched ({type(exc).__name__})"

    files = res.get("files", [])
    stock = [f for f in files if "stock" in f["name"].lower()]
    if not stock:
        return "", (f"no sheet named like '{brand} Stock' is shared with the "
                    f"Operations OS account")
    # The most recently written one is the live feed; the rest are old copies.
    stock.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
    return stock[0]["id"], f"found '{stock[0]['name']}'"


def package_state(job) -> dict:
    """Per-kind state for the collection and overview screens."""
    runs = {r.kind: r for r in (job.packages or [])}
    out = {}
    for kind in KINDS:
        r = runs.get(kind)
        out[kind] = {
            "state": r.status if r else "not_started",
            "rows": r.row_count if r else 0,
            "exceptions": (r.critical_count + r.review_count) if r else 0,
            "approved": bool(r and r.status in ("approved", "delivered")),
            "delivered": bool(r and r.status == "delivered"),
        }
    return out

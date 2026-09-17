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
from datetime import datetime, timezone

import pandas as pd

from app.config import OUTPUT_DIR
from app.core import intake as intake_core
from app.core.base_color import DEFAULT_VOCAB, learn_lookup
from app.core.pricing import learn_strategy, review_prices
from app.core.sap_creation import SAP_COLUMNS, build_creation_sheet
from app.core.sheet_layout import load_supplier_table
from app.core.temp_template import TEMP_COLUMNS, build_temp_sheet

PRODUCT_CREATION = "product_creation"
TEMP = "temp"
PRICING = "pricing"

KINDS = {
    PRODUCT_CREATION: "SAP Product Creation",
    TEMP: "TEMP Standard Template",
    PRICING: "SAP Pricing",
}

# Which supplier file each package is built from.
SOURCE_KIND = {
    PRODUCT_CREATION: intake_core.ORDER_SHEET,
    TEMP: intake_core.ORDER_SHEET,
    PRICING: intake_core.PRICE_LIST,
}


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

def _source_rows(job, kind: str) -> "tuple[list[dict], str]":
    """Rows from the supplier file this package is built from."""
    want = SOURCE_KIND.get(kind, intake_core.ORDER_SHEET)
    files = [f for f in job.files if f.kind == want and f.parse_path]
    if not files and want == intake_core.PRICE_LIST:
        # A brand with prices inside the order sheet still has a price package.
        files = [f for f in job.files
                 if f.kind == intake_core.ORDER_SHEET and f.parse_path]
    if not files:
        return [], f"no {intake_core.KIND_LABELS.get(want, want)} in this collection"

    rows: list[dict] = []
    for f in files:
        if not os.path.exists(f.parse_path):
            continue
        table, _info = load_supplier_table(f.parse_path)
        if not table.empty:
            rows.extend(table.to_dict("records"))
    if not rows:
        return [], "the supplier file could not be read"
    return rows, ""


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
    decisions = decisions or {}
    rows, problem = _source_rows(job, kind)
    if problem:
        return {"error": problem, "columns": [], "rows": [], "exceptions": [],
                "summary": {}}

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
    vocab = sorted(set(DEFAULT_VOCAB) | {v for v in colour_lookup.values() if v})

    if kind == PRODUCT_CREATION:
        out = build_creation_sheet(rows, season=job.season or "",
                                   vocab=vocab, lookup=colour_lookup)
    elif kind == TEMP:
        out = build_temp_sheet(
            rows, brand=job.brand or "", season=job.season or "",
            group_map=group_map, vocab=vocab, colour_lookup=colour_lookup,
            history=_history_from(reference, rows),
            earliest_ship_date=(config.earliest_ship_date if config else "") or "")
    elif kind == PRICING:
        history = _price_history(reference)
        strategy = learn_strategy(history) if history else {"known": False}
        review = review_prices(rows, strategy)
        out = {"columns": list(rows[0].keys()) if rows else [],
               "rows": rows, "exceptions": review["exceptions"],
               "summary": {**review["summary"], "strategy": strategy}}
    else:
        return {"error": f"unknown package {kind!r}", "columns": [], "rows": [],
                "exceptions": [], "summary": {}}

    out["reference_note"] = ref_note
    if reference:
        out["summary"]["sap_compared"] = len(reference)
    return out


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

def run_package(db, job, kind: str):
    """Generate, store the exceptions and write the sheet. Returns PackageRun."""
    from app.models import PackageRun

    run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                      PackageRun.kind == kind).first()
    if run is None:
        run = PackageRun(job_id=job.id, kind=kind)
        db.add(run)
        db.commit()
        db.refresh(run)

    if run.is_approved:
        return run

    out = generate(db, job, kind, run.decisions)
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
    run.status = "needs_decisions" if (run.critical_count or run.review_count) else "ready"
    run.file_path = _write_sheet(job, kind, out)
    db.commit()
    db.refresh(run)
    return run


def _write_sheet(job, kind: str, out) -> str:
    """Write the package to disk so it can be downloaded and handed to SAP."""
    folder = OUTPUT_DIR / f"collection_{job.id}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{(job.brand or 'collection').replace(' ', '_')}_{job.season or ''}_{kind}.xlsx"
    path = folder / name
    columns = out.get("columns") or (list(out["rows"][0].keys()) if out["rows"] else [])
    pd.DataFrame(out["rows"], columns=columns).to_excel(path, index=False)
    return str(path)


def apply_decision(db, run, key: str, value: str):
    """Record one answer and rebuild the package around it."""
    decisions = run.decisions
    if value:
        decisions[key] = value
    else:
        decisions.pop(key, None)
    run.decisions = decisions
    db.commit()
    job = run.job
    return run_package(db, job, run.kind)


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
            "approved": bool(r and r.is_approved),
        }
    return out

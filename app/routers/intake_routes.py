"""Supplier email intake — Operations OS Phase 1.

Exposes the endpoint n8n posts a forwarded supplier email to, and the
collections list/detail pages that show what the system made of it.

The intake never modifies a supplier file. Attachments are stored as received;
anything derived (a spreadsheet pulled out of a PDF line sheet) is written
alongside and recorded separately, so a job can always be re-analysed from the
original source of record.
"""
from __future__ import annotations

import os
import pathlib
import secrets
import threading

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import (
    INTAKE_API_KEY, INTAKE_MAX_FILE_MB, INTAKE_OWNER_EMAIL, UPLOAD_DIR,
)
from app.core import intake as intake_core
from app.core.parser import FileParser
from app.database import get_db
from app.models import CollectionFile, CollectionJob, User
from app.services.file_safety import normalize_uploaded_name, unique_path
from app.templates_config import templates

router = APIRouter()

MAX_INTAKE_SIZE = INTAKE_MAX_FILE_MB * 1024 * 1024


# ── Helpers ──────────────────────────────────────────────────────────────────

def _intake_authorised(request: Request) -> bool:
    """Constant-time check of the shared intake key.

    Accepts either ``X-Intake-Key`` or a bearer token so the workflow tool can
    use whichever it makes easier.
    """
    if not INTAKE_API_KEY:
        return False
    supplied = request.headers.get("x-intake-key", "")
    if not supplied:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return bool(supplied) and secrets.compare_digest(supplied, INTAKE_API_KEY)


def _intake_owner(db: DBSession) -> User | None:
    """The account collections are filed under when they arrive by email."""
    if INTAKE_OWNER_EMAIL:
        user = db.query(User).filter(
            User.email == INTAKE_OWNER_EMAIL, User.is_active == True  # noqa: E712
        ).first()
        if user:
            return user
    return db.query(User).filter(User.is_active == True).order_by(User.id).first()  # noqa: E712


async def _save_attachment(upload: UploadFile, dest_dir: pathlib.Path) -> tuple[pathlib.Path, str, int]:
    """Stream one attachment to disk unchanged. Returns (path, display, bytes)."""
    display, safe = normalize_uploaded_name(upload.filename or "attachment")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(dest_dir, safe)
    written = 0
    with open(path, "wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_INTAKE_SIZE:
                out.close()
                path.unlink(missing_ok=True)
                raise ValueError(f"{display} exceeds {INTAKE_MAX_FILE_MB} MB")
            out.write(chunk)
    return path, display, written


def _parse_path_for(saved: pathlib.Path) -> tuple[pathlib.Path | None, str | None]:
    """Return (parseable path, error). PDFs are converted to a sibling .xlsx."""
    ext = saved.suffix.lower()
    if ext in {".xlsx", ".xls", ".csv"}:
        return saved, None
    if ext == ".pdf":
        from app.core.pdf_ingest import PdfIngestError, pdf_to_xlsx
        target = saved.with_name(saved.stem + "__from_pdf.xlsx")
        try:
            pdf_to_xlsx(str(saved), str(target))
            return target, None
        except PdfIngestError as exc:
            return None, str(exc)
        except Exception as exc:  # pragma: no cover — defensive
            return None, f"PDF could not be read: {exc}"
    return None, None


def analyse_job(db: DBSession, job: CollectionJob) -> CollectionJob:
    """Parse the job's product-bearing files and store the intake report.

    Only the order sheet and price list are parsed for volume; a catalog is
    reference material and the image package carries no product rows. A file
    that fails to parse records its error and does not sink the whole intake.
    """
    parser = FileParser()
    rows: list[dict] = []
    by_kind: dict[str, list[dict]] = {}

    for cf in job.files:
        if cf.kind not in (intake_core.ORDER_SHEET, intake_core.PRICE_LIST):
            continue
        if not cf.parse_path:
            continue
        try:
            parsed, _unique, _headers = parser.parse(cf.parse_path)
            # Half of Flender's brands bury the real header under a title block
            # or split a collection across delivery tabs, which the default
            # first-sheet/first-row read returns as zero rows. Fall back to the
            # layout detector before treating the file as unreadable.
            if not parsed and cf.parse_path.lower().endswith((".xlsx", ".xls")):
                from app.core.sheet_layout import load_supplier_table
                table, info = load_supplier_table(cf.parse_path)
                if not table.empty:
                    parsed = parser.parse_frame(table)
                    cf.parse_note = (
                        f"header row {info['header_row']}, "
                        f"sheet(s): {', '.join(info['sheets_used'])}")
            rows.extend(parsed)
            by_kind.setdefault(cf.kind, []).extend(parsed)
        except Exception as exc:
            cf.parse_error = str(exc)[:500]

    # The rows themselves name the brand and season more reliably than the
    # filename does ("FW27 Pre EUR Importsheet.xlsx" names no brand at all).
    _identity_from_contents(job)

    # Volume comes from the order sheet, never from the sum of every file. A
    # price list describes the same products, so adding the two together
    # double-counts: Hiking Patrol read as 332 styles when it has 60, because
    # 60 order rows and 272 price rows were simply added up.
    counted = by_kind.get(intake_core.ORDER_SHEET) or rows
    analysis = intake_core.analyse_rows(counted)
    # Re-detect across every filename so a multi-brand drop is still flagged
    # when the job is re-analysed after a correction.
    all_brands = intake_core.detect_brands(
        job.email_subject or "", *[f.filename for f in job.files])
    report = intake_core.build_intake_report(
        job.brand, job.season,
        [{"filename": f.filename, "kind": f.kind} for f in job.files],
        analysis, all_brands=all_brands,
    )

    job.total_styles = analysis["styles"]
    job.total_skus = analysis["skus"]
    _read_inside_files(db, job, report)       # may recount from the package rows
    job.report = report
    if report["missing"] or report["warnings"]:
        job.status = "needs_input" if report["missing"] else "analysed"
    else:
        job.status = "analysed"
    if not report["ready_to_process"]:
        job.status = "needs_input"
    db.commit()
    db.refresh(job)
    return job


_TABLE_KINDS = (intake_core.ORDER_SHEET, intake_core.PRICE_LIST,
                intake_core.PRODUCT_LIST)


def _sample(cf, limit: int = 400) -> "tuple[list[str], list[dict]]":
    """Headers and the first rows of one supplier table, as the packages read it."""
    from app.core.sheet_layout import load_supplier_table
    if not cf.parse_path or not os.path.exists(cf.parse_path):
        return [], []
    try:
        table, _info = load_supplier_table(cf.parse_path)
    except Exception:
        return [], []
    if table.empty:
        return [], []
    table = table.loc[:, [str(c).strip() != "" for c in table.columns]]
    table = table.loc[:, ~table.columns.duplicated()]
    headers = [str(c) for c in table.columns
               if str(c).strip() and not str(c).startswith("__")]
    return headers, table.head(limit).to_dict("records")


def _identity_from_contents(job: CollectionJob) -> None:
    """Fill a missing brand or season from inside the supplier files."""
    if job.brand and job.season:
        return
    for cf in job.files:
        if cf.kind not in (intake_core.ORDER_SHEET, intake_core.PRODUCT_LIST,
                           intake_core.PRICE_LIST):
            continue
        _headers, rows = _sample(cf, limit=300)
        found = intake_core.detect_identity_from_rows(rows)
        if not job.brand and found["brand"]:
            job.brand = found["brand"][:255]
            job.identity_note = f"Brand read from {found['brand_source']} of {cf.filename}"
        if not job.season and found["season"]:
            job.season = found["season"][:50]
        if not job.season and cf.parse_path:
            # Sheet names and the title block: "KK SP26", "2026 FALL/WINTER".
            from app.core.sheet_layout import workbook_labels
            season = intake_core.detect_season(*workbook_labels(cf.parse_path))
            if season:
                job.season = season
        if job.brand and job.season:
            break


def _recount(job: CollectionJob, report: dict, rules: dict) -> None:
    """Count the collection from the rows the packages are built from.

    The first-pass reader only understands one row per size with obvious
    headers; on sheets with size grids, ranges or scale letters it reported
    every line as missing a size. The package reader handles all of those,
    so its rows are the ones counted.
    """
    from app.services import packages as pkg
    try:
        rows, problem, _rep = pkg.collection_rows(job, pkg.PRODUCT_CREATION, rules)
    except Exception:
        return
    if problem or not rows:
        return
    def v(r, *keys):
        for k in keys:
            if not pkg._blank(r.get(k)):
                return str(r.get(k)).strip()
        return ""

    as_identity = [{"item_code": v(r, "Item No."),
                    # Edwin: one colour name, several finish codes — each its own product.
                    "color_name": " ".join(x for x in (v(r, "Colour Code"), v(r, "Color")) if x),
                    "color_code": v(r, "Colour Code"),
                    "size": "/".join(x for x in (v(r, "Size"), v(r, "Size Run")) if x),
                    "barcode": v(r, "Barcode"), "brand": v(r, "Brand"),
                    "style_name": v(r, "Item Description"),
                    "wholesale_price": v(r, "Wholesale Price EUR", "Supplier Wholesale Price") or None,
                    "retail_price": v(r, "Retail EUR", "Supplier Retail Price") or None}
                   for r in rows]
    totals = intake_core.analyse_rows(as_identity)
    gap_words = ("lines without a barcode", "lines without a purchase price",
                 "lines without an RRP", "lines without a size")
    report["warnings"] = [w for w in report["warnings"] if not w.endswith(gap_words)]
    for key, words in (("missing_barcode", "without a barcode"),
                       ("missing_wholesale_price", "without a purchase price"),
                       ("missing_retail_price", "without an RRP"),
                       ("missing_size", "without a size")):
        if totals.get(key):
            report["warnings"].append(f"{totals[key]:,} lines {words}")
    cur = next((r.get("Supplier Wholesale Currency") for r in rows
                if r.get("Supplier Wholesale Currency")), "")
    if cur:
        report["warnings"].append(
            f"Prices are in {cur}, not EUR — the EUR price comes from the pricing step")
    report["totals"] = totals
    job.total_styles, job.total_skus = totals["styles"], totals["skus"]


def _read_inside_files(db: DBSession, job: CollectionJob, report: dict) -> None:
    """Column mapping per file, and the order sheet / product list check.

    Stored in the intake report so the collection page can show — and staff
    can correct — which column was read for each field, and which items are
    missing from either half of a split collection.
    """
    from app.core import field_mapping
    from app.core.brand_rules import column_overrides
    from app.services import brand_rules as rules_svc
    from app.services.brand_rules import rules_for
    from app.services import packages as pkg

    rules = rules_for(db, job.brand or "")
    overrides = column_overrides(rules["tables"])
    mappings = {}
    for cf in job.files:
        if cf.kind not in _TABLE_KINDS:
            continue
        headers, _rows = _sample(cf, limit=5)
        if not headers:
            continue
        fields = field_mapping.detect(headers, overrides)
        mappings[str(cf.id)] = {"file": cf.filename, "kind": cf.kind,
                                "headers": headers, "fields": fields}
        if cf.kind == intake_core.ORDER_SHEET:
            for essential in ("Item No.", "Color"):
                if not fields[essential]["header"]:
                    report["warnings"].append(
                        f"{cf.filename}: no column found for "
                        f"{field_mapping.PLAIN_NAMES[essential].lower()} — "
                        f"set it under Column mapping")
    report["mappings"] = mappings

    # A brand seen for the first time gets base rules from what was found.
    if job.brand and mappings:
        from app.core.brand_rules import read_size_legend
        legend = {}
        for cf in job.files:
            if cf.kind == intake_core.ORDER_SHEET and cf.parse_path and os.path.exists(cf.parse_path):
                for code, sizes in read_size_legend(cf.parse_path).items():
                    legend.setdefault(code, sizes)
        try:
            made = rules_svc.generate_base_rules(
                db, job.brand, mappings, legend, job_id=job.id,
                source_files=", ".join(m["file"] for m in mappings.values()))
            if made:
                report["base_rules"] = made
                rules = rules_for(db, job.brand)
        except Exception:
            db.rollback()
    _recount(job, report, rules)

    kinds = {f.kind for f in job.files}
    if intake_core.PRODUCT_LIST in kinds and intake_core.ORDER_SHEET in kinds:
        try:
            joined, _problem, combined = pkg.collection_rows(
                job, pkg.PRODUCT_CREATION, rules)
            # The order sheet alone has no barcodes when the product list
            # carries them; count what is still missing after the join.
            report["warnings"] = [w for w in report["warnings"]
                                  if not w.endswith("lines without a barcode")]
            still = sum(1 for r in joined if pkg._blank(r.get("Barcode")))
            if still:
                report["warnings"].append(
                    f"{still:,} lines still without a barcode after joining the product list")
        except Exception as exc:                     # never sink the intake
            combined = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        report["combine"] = combined
        if combined.get("only_in_order_count"):
            report["warnings"].append(
                f"{combined['only_in_order_count']:,} items in the order sheet are "
                "not in the product list — they will have no HS code")
        if combined.get("only_in_list_count"):
            report["warnings"].append(
                f"{combined['only_in_list_count']:,} items in the product list are "
                "not in the order sheet")

    if job.picture_links and intake_core.IMAGES in report.get("missing", []):
        report["missing"] = [k for k in report["missing"] if k != intake_core.IMAGES]
        report["received"] = sorted(set(report["received"]) | {intake_core.IMAGES})


async def _create_job(db: DBSession, owner: User, attachments: list[UploadFile],
                      *, source: str, email_from: str = "", subject: str = "",
                      brand: str = "", season: str = "",
                      supplier: str = "") -> CollectionJob:
    """Create a Collection Job from a set of attachments and analyse it."""
    job = CollectionJob(
        user_id=owner.id,
        source=source,
        email_from=(email_from or "")[:320] or None,
        email_subject=(subject or "") or None,
        supplier=(supplier or "")[:255] or None,
        status="received",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    names = await _store_files(db, job, attachments)

    # Brand and season come from the caller when known, otherwise from the
    # subject line and the filenames — which is how suppliers actually label.
    job.brand = (brand or "").strip() or intake_core.detect_brand(subject, *names)
    job.season = (season or "").strip() or intake_core.detect_season(subject, *names)
    db.commit()
    db.refresh(job)

    job = analyse_job(db, job)
    _connect_sap_history(db, job)
    _notify_intake(db, job)
    return job


async def _store_files(db: DBSession, job: CollectionJob,
                       attachments: list[UploadFile],
                       kind_override: str = "") -> list[str]:
    """Save attachments to the collection, classified. Returns their names.

    ``kind_override`` lets a person adding a file say what it is (a price
    list submitted after processing, say) instead of trusting the filename.
    """
    job_dir = UPLOAD_DIR / f"user_{job.user_id}" / f"collection_{job.id}"
    names: list[str] = []
    for upload in attachments:
        if not upload or not upload.filename:
            continue
        try:
            path, display, size = await _save_attachment(upload, job_dir)
        except ValueError as exc:
            db.add(CollectionFile(
                job_id=job.id, filename=(upload.filename or "attachment")[:500],
                file_path="", kind=intake_core.OTHER, file_size=0,
                parse_error=str(exc),
            ))
            continue

        kind = (kind_override if kind_override in intake_core.KIND_LABELS
                else intake_core.classify_attachment(display))
        parse_path, parse_error = (None, None)
        if kind in _TABLE_KINDS:
            parse_path, parse_error = _parse_path_for(path)

        db.add(CollectionFile(
            job_id=job.id, filename=display, file_path=str(path),
            parse_path=str(parse_path) if parse_path else None,
            kind=kind, file_size=size, parse_error=parse_error,
            kind_corrected=bool(kind_override),
        ))
        names.append(display)
    db.commit()
    db.refresh(job)
    return names


def _notify_intake(db: DBSession, job: CollectionJob) -> None:
    """Tell the owner what arrived, without letting a mail failure matter."""
    from app.services.status_email import intake_complete
    owner = db.get(User, job.user_id)
    if owner and owner.email:
        intake_complete(owner.email, job)


def _connect_sap_history(db: DBSession, job: CollectionJob) -> None:
    """Point the collection at its brand's SAP sheet, if one is shared.

    The history is what stops the system asking about things SAP already
    knows, so waiting for someone to notice a button and paste an id wastes
    the benefit on the very first run.

    The lookup talks to Drive, so it runs off the request: an intake must not
    get slower because a Google call is having a bad day. The collection page
    shows the connection once it lands.
    """
    from app.models import BrandConfig

    if not job.brand:
        return
    config = db.query(BrandConfig).filter(
        BrandConfig.user_id == job.user_id, BrandConfig.brand == job.brand).first()
    if config and config.sap_sheet_id:
        return
    threading.Thread(target=_lookup_sap_sheet,
                     args=(job.user_id, job.brand), daemon=True).start()


def _lookup_sap_sheet(user_id: int, brand: str) -> None:
    """Find and save the brand's SAP sheet, on its own session."""
    from app.database import SessionLocal
    from app.models import BrandConfig
    from app.services.packages import find_sap_sheet

    try:
        sheet_id, _note = find_sap_sheet(brand)
    except Exception:
        return
    if not sheet_id:
        return
    db = SessionLocal()
    try:
        config = db.query(BrandConfig).filter(
            BrandConfig.user_id == user_id, BrandConfig.brand == brand).first()
        if config and config.sap_sheet_id:
            return
        if not config:
            config = BrandConfig(user_id=user_id, brand=brand)
            db.add(config)
        config.sap_sheet_id = sheet_id
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── n8n webhook ──────────────────────────────────────────────────────────────

@router.post("/api/intake/email")
async def intake_email(
    request: Request,
    db: DBSession = Depends(get_db),
    files: list[UploadFile] = File(default=[]),
    sender: str = Form(default=""),
    subject: str = Form(default=""),
    brand: str = Form(default=""),
    season: str = Form(default=""),
    supplier: str = Form(default=""),
):
    """Create a Collection Job from a forwarded supplier email.

    Posted by n8n as multipart/form-data: the email metadata as fields, every
    attachment as a repeated ``files`` part. Authenticated with the shared
    intake key, since there is no browser session behind it.
    """
    if not INTAKE_API_KEY:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not _intake_authorised(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    owner = _intake_owner(db)
    if not owner:
        return JSONResponse(
            {"error": "No active user to own the collection"}, status_code=503)

    real = [f for f in (files or []) if f and f.filename]
    if not real:
        return JSONResponse({"error": "No attachments in the email"}, status_code=400)

    job = await _create_job(
        db, owner, real, source="email", email_from=sender, subject=subject,
        brand=brand, season=season, supplier=supplier,
    )
    report = job.report
    return JSONResponse({
        "ok": True,
        "job_id": job.id,
        "brand": job.brand,
        "season": job.season,
        "status": job.status,
        "styles": job.total_styles,
        "skus": job.total_skus,
        "received": report.get("received", []),
        "missing": report.get("missing", []),
        "warnings": report.get("warnings", []),
        "summary": intake_core.format_intake_email(report),
        "url": f"/collections/{job.id}",
    })


# ── UI ───────────────────────────────────────────────────────────────────────

@router.get("/collections", response_class=HTMLResponse)
async def collections_list(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    jobs = (
        db.query(CollectionJob)
        .filter(CollectionJob.user_id == uid)
        .order_by(CollectionJob.created_at.desc())
        .limit(100).all()
    )
    return templates.TemplateResponse(request, "collections.html", {
        "user": db.get(User, uid), "jobs": jobs,
        "labels": intake_core.KIND_LABELS,
    })


@router.get("/collections/{job_id}", response_class=HTMLResponse)
async def collection_detail(job_id: int, request: Request,
                            db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)
    from app.core import field_mapping, workflow
    from app.core.brand_rules import brand_key
    from app.models import BrandConfig
    from app.services import brand_rules as rules_svc
    from app.services import packages as pkg
    report = job.report
    brand_config = db.query(BrandConfig).filter(
        BrandConfig.user_id == uid, BrandConfig.brand == (job.brand or "")).first()
    state = pkg.package_state(job)
    rules = rules_svc.rules_for(db, job.brand or "")
    pending_rules = rules_svc.pending(db, job.brand) if job.brand else []
    steps = workflow.steps(
        job, report, state, rules=rules, pending_rules=len(pending_rules),
        sap_connected=bool(brand_config and brand_config.sap_sheet_id))
    return templates.TemplateResponse(request, "collection_detail.html", {
        "user": db.get(User, uid), "job": job, "report": report,
        "steps": steps, "rules": rules, "pending_rules": pending_rules,
        "brand_rules_key": brand_key(job.brand or ""),
        "field_names": field_mapping.FIELD_NAMES,
        "plain_names": field_mapping.PLAIN_NAMES,
        "essential_fields": field_mapping.ESSENTIAL,
        "mapping_note": request.session.pop("mapping_note", ""),
        "learn_note": request.session.pop("learn_note", ""),
        "package_kinds": pkg.KINDS, "package_state": state,
        "brand_config": brand_config,
        "sap_sheet_note": request.session.pop("sap_sheet_note", ""),
        "change_report": job.change_report,
        "other_collections": db.query(CollectionJob).filter(
            CollectionJob.user_id == uid, CollectionJob.id != job.id,
            CollectionJob.brand == job.brand).order_by(
                CollectionJob.created_at.desc()).limit(10).all(),
        "summary": intake_core.format_intake_email(report),
        "labels": intake_core.KIND_LABELS,
        "expected": intake_core.EXPECTED_KINDS,
    })


@router.post("/collections/upload")
async def collection_upload(request: Request, db: DBSession = Depends(get_db),
                            files: list[UploadFile] = File(default=[]),
                            brand: str = Form(default=""),
                            season: str = Form(default="")):
    """Manual equivalent of the email intake — same pipeline, browser session.

    Kept so the intake can be exercised before the mailbox exists, and so a
    collection that arrives outside email still becomes a proper job.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    owner = db.get(User, uid)
    real = [f for f in (files or []) if f and f.filename]
    if not owner or not real:
        return RedirectResponse("/collections", status_code=302)

    job = await _create_job(db, owner, real, source="upload",
                            subject=" ".join(f.filename or "" for f in real),
                            brand=brand, season=season)
    return RedirectResponse(f"/collections/{job.id}", status_code=302)


@router.post("/collections/{job_id}/files/{file_id}/kind")
async def correct_kind(job_id: int, file_id: int, request: Request,
                       kind: str = Form(...), db: DBSession = Depends(get_db)):
    """Correct an attachment's detected kind and re-run the analysis.

    Every correction is flagged so the classification rules can be reviewed
    against the supplier files that actually defeated them.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)

    cf = db.query(CollectionFile).filter(
        CollectionFile.id == file_id, CollectionFile.job_id == job.id).first()
    if cf and kind in intake_core.KIND_LABELS:
        cf.kind = kind
        cf.kind_corrected = True
        cf.parse_error = None
        if kind in _TABLE_KINDS and cf.file_path:
            parse_path, err = _parse_path_for(pathlib.Path(cf.file_path))
            cf.parse_path = str(parse_path) if parse_path else None
            cf.parse_error = err
        else:
            cf.parse_path = None
        db.commit()
        analyse_job(db, job)
    return RedirectResponse(f"/collections/{job_id}", status_code=302)


@router.post("/collections/{job_id}/delete")
async def delete_collection(job_id: int, request: Request,
                            db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if job:
        import shutil
        job_dir = UPLOAD_DIR / f"user_{uid}" / f"collection_{job.id}"
        if job_dir.is_dir():
            try:
                shutil.rmtree(job_dir)
            except OSError:
                pass
        db.delete(job)
        db.commit()
    return RedirectResponse("/collections", status_code=302)


@router.post("/collections/{job_id}/files")
async def add_files(job_id: int, request: Request,
                    files: list[UploadFile] = File(default=[]),
                    kind: str = Form(default=""),
                    db: DBSession = Depends(get_db)):
    """Add files to a collection that already exists.

    The second half of a split collection (the product list with HS codes),
    a price list submitted once processing is done, or a catalogue that
    arrived later — all join the same collection and it is re-analysed.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)
    real = [f for f in (files or []) if f and f.filename]
    if real:
        await _store_files(db, job, real, kind_override=kind)
        analyse_job(db, job)
    return RedirectResponse(f"/collections/{job_id}", status_code=302)


@router.post("/collections/{job_id}/picture-links")
async def save_picture_links(job_id: int, request: Request,
                             links: str = Form(default=""),
                             db: DBSession = Depends(get_db)):
    """Record where the brand's product pictures are (one link per line)."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if job:
        found = [l.strip() for l in (links or "").splitlines()
                 if l.strip().lower().startswith(("http://", "https://"))]
        job.picture_links = list(dict.fromkeys(found))[:50]
        db.commit()
        analyse_job(db, job)
        # Read what the links hold — a Dropbox folder can be over a GB, so in
        # the background; the collection page shows the progress.
        from app.services.picture_index import read_links_in_background
        read_links_in_background(job.id)
    return RedirectResponse(f"/collections/{job_id}", status_code=302)


@router.post("/collections/{job_id}/mapping")
async def save_mapping(job_id: int, request: Request,
                       db: DBSession = Depends(get_db)):
    """Save which column holds each field — as a rule for the whole brand.

    A column mapping is a fact about how the brand writes its files, so it
    is stored in the brand's rules (with a history entry) and applies to
    every collection of that brand from now on.
    """
    from app.core import field_mapping
    from app.services import brand_rules as rules_svc

    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)
    if not job.brand:
        request.session["mapping_note"] = "Set the brand first — mappings are saved per brand."
        return RedirectResponse(f"/collections/{job_id}#mapping", status_code=302)

    form = await request.form()
    mappings = job.report.get("mappings") or {}
    changed = 0
    # One save covers every file. A brand rule names the column per file
    # ("Description | Style Name") so each file keeps its own column.
    for field in field_mapping.FIELD_NAMES:
        chosen_by_file, moved = {}, False
        for fid, m in mappings.items():
            name = f"col::{fid}::{field}"
            if name not in form:
                continue
            chosen = str(form.get(name) or "").strip()
            info = (m.get("fields") or {}).get(field) or {}
            was = info.get("header", "")
            chosen_by_file[fid] = chosen
            if chosen != was and not (chosen == "" and not was):
                moved = True
        if not moved:
            continue
        values = list(dict.fromkeys(v for v in chosen_by_file.values()
                                    if v and v != "__auto"))
        if values:
            rules_svc.edit_row(db, job.brand, "columns",
                               {"field": field, "header": " | ".join(values)},
                               user_id=uid, reason=f"Set on {job.label}", job_id=job.id)
        elif "__auto" in chosen_by_file.values():
            rules_svc.remove_row(db, job.brand, "columns", field, user_id=uid)
        else:
            rules_svc.edit_row(db, job.brand, "columns",
                               {"field": field, "header": field_mapping.NONE},
                               user_id=uid, reason=f"Set on {job.label}", job_id=job.id)
        changed += 1
    analyse_job(db, job)
    request.session["mapping_note"] = (
        f"Saved {changed} column{'s' if changed != 1 else ''} to the {job.brand} rules — "
        "regenerate packages to use them." if changed else "Nothing changed.")
    return RedirectResponse(f"/collections/{job_id}#mapping", status_code=302)


@router.post("/collections/{job_id}/learn")
async def learn_from_finished(job_id: int, request: Request,
                              file: UploadFile = File(...),
                              db: DBSession = Depends(get_db)):
    """Teach the brand's rules from a TEMP sheet a person already finished.

    The sheet is matched to this collection's rows by barcode (or style), and
    what it shows — product groups, SAP size lists and sort indexes, the TEMP
    columns and their fixed values — becomes the brand's rules, so the next
    collection comes out the same way without anyone typing a rule.
    """
    import tempfile
    import pandas as pd
    from app.core.learn_from_sheet import learn
    from app.services import brand_rules as rules_svc
    from app.services import packages as pkg

    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)
    if not job.brand:
        request.session["learn_note"] = "Set the brand first — what is learned is saved for the brand."
        return RedirectResponse(f"/collections/{job_id}#learn", status_code=302)

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        finished = pd.read_excel(path, dtype=str)
        finished.columns = [str(c).strip() for c in finished.columns]
        if "U_CodeBars" not in finished.columns and "U_SCode" not in finished.columns:
            request.session["learn_note"] = (
                "That is not a finished TEMP sheet — it needs the U_ columns "
                "(U_CodeBars, U_SCode, …).")
            return RedirectResponse(f"/collections/{job_id}#learn", status_code=302)
        rows, _problem, _rep = pkg.collection_rows(job, pkg.TEMP)
        learned = learn(finished.to_dict("records"), list(finished.columns), rows)
        done = rules_svc.apply_learned(db, job.brand, learned, filename=file.filename or "",
                                       user_id=uid, job_id=job.id)
        request.session["learn_note"] = (
            f"Matched {learned['matched']} of {learned['lines']} lines. "
            + " ".join(done) + " Regenerate the packages to use it.")
    except Exception as exc:
        request.session["learn_note"] = f"Could not read that sheet ({type(exc).__name__}: {exc})"[:300]
    finally:
        os.unlink(path)
    return RedirectResponse(f"/collections/{job_id}#learn", status_code=302)


@router.post("/collections/{job_id}/include-items")
async def include_items(job_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Keep items that are only in the product list — or stop keeping them.

    Fixing the supplier file and starting again is not the only way: an item
    the order sheet left out can be taken from the product list here.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return RedirectResponse("/collections", status_code=302)
    form = await request.form()
    add = [str(v) for v in form.getlist("add") if str(v).strip()]
    remove = {str(v).upper() for v in form.getlist("remove")}
    kept = [i for i in job.included_items if i.upper() not in remove] + add
    job.included_items = kept
    db.commit()
    analyse_job(db, job)
    return RedirectResponse(f"/collections/{job_id}#combine", status_code=302)


@router.get("/collections/{job_id}/picture-status")
async def picture_status(job_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    idx = job.picture_index
    return JSONResponse({"status": idx.get("status", ""), "photos": len(idx.get("files", [])),
                         "error": idx.get("error", "")})

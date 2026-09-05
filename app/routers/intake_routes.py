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
        except Exception as exc:
            cf.parse_error = str(exc)[:500]

    analysis = intake_core.analyse_rows(rows)
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

    job_dir = UPLOAD_DIR / f"user_{owner.id}" / f"collection_{job.id}"
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

        kind = intake_core.classify_attachment(display)
        parse_path, parse_error = (None, None)
        if kind in (intake_core.ORDER_SHEET, intake_core.PRICE_LIST):
            parse_path, parse_error = _parse_path_for(path)

        db.add(CollectionFile(
            job_id=job.id, filename=display, file_path=str(path),
            parse_path=str(parse_path) if parse_path else None,
            kind=kind, file_size=size, parse_error=parse_error,
        ))
        names.append(display)

    # Brand and season come from the caller when known, otherwise from the
    # subject line and the filenames — which is how suppliers actually label.
    job.brand = (brand or "").strip() or intake_core.detect_brand(subject, *names)
    job.season = (season or "").strip() or intake_core.detect_season(subject, *names)
    db.commit()
    db.refresh(job)

    return analyse_job(db, job)


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
    report = job.report
    return templates.TemplateResponse(request, "collection_detail.html", {
        "user": db.get(User, uid), "job": job, "report": report,
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
        if kind in (intake_core.ORDER_SHEET, intake_core.PRICE_LIST) and cf.file_path:
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

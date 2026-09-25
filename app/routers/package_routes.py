"""Output packages and their approval gate — Operations OS.

Open a collection, generate a package, answer only the questions the system
could not settle itself, approve, and hand the sheet to SAP. One screen serves
every package, because the shape is the same for all of them.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
)
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.core.base_color import DEFAULT_VOCAB
from app.database import get_db
from app.models import BrandConfig, CollectionJob, PackageRun, User
from app.services import packages as pkg
from app.templates_config import templates

router = APIRouter()


def _job(db: DBSession, job_id: int, uid: int) -> CollectionJob | None:
    return db.query(CollectionJob).filter(
        CollectionJob.id == job_id, CollectionJob.user_id == uid).first()


def _options_for(field: str, job, db) -> list[str]:
    """Sensible answers to offer for a question, so it is a click not an essay."""
    if field in ("BASE COLOR", "U_BaseColor"):
        config = db.query(BrandConfig).filter(
            BrandConfig.user_id == job.user_id,
            BrandConfig.brand == (job.brand or "")).first()
        learned = sorted({v for v in (config.colour_map or {}).values()}) if config else []
        return learned or DEFAULT_VOCAB
    if field in ("U_ItmsGrpCod", "Main Waregroup"):
        from app.core.attribute_taxonomy import SAP_MASTER_GROUPS
        return list(SAP_MASTER_GROUPS)
    if field == "image_status":
        from app.core.collection_images import STATUS_LABELS
        return list(STATUS_LABELS.values())
    if field == "product_type":
        from app.core.attribute_taxonomy import PRODUCT_TYPES_BY_GROUP
        return sorted({code for pairs in PRODUCT_TYPES_BY_GROUP.values()
                       for code, _name in pairs})
    return []


@router.post("/collections/{job_id}/packages/{kind}/generate")
async def generate_package(job_id: int, kind: str, request: Request,
                           db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if not job or kind not in pkg.KINDS:
        return RedirectResponse(f"/collections/{job_id}", status_code=302)
    pkg.run_package(db, job, kind)
    return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                            status_code=302)


@router.get("/collections/{job_id}/packages/{kind}", response_class=HTMLResponse)
async def package_detail(job_id: int, kind: str, request: Request,
                         db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if not job or kind not in pkg.KINDS:
        return RedirectResponse("/collections", status_code=302)

    run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                      PackageRun.kind == kind).first()
    questions = run.exceptions if run else []
    decisions = run.decisions if run else {}

    # Once a question is answered the engine stops raising it, so the answered
    # list is built from the decisions on record rather than from what is left
    # outstanding — otherwise an answer would simply vanish from the screen.
    # A warning is something to be aware of, not something to answer. Listing
    # both under one heading told a reviewer of Hiking Patrol there were 112
    # decisions when there were 8, the other 104 being notes.
    open_q, warnings = [], []
    for q in questions:
        if decisions.get(q["key"]):
            continue
        q["options"] = q.get("options") or _options_for(q["field"], job, db)
        (open_q if q["severity"] in ("critical", "manual_review")
         else warnings).append(q)

    answered = []
    for key, value in decisions.items():
        field, _, subject = key.partition("::")
        answered.append({"key": key, "field": field, "subject": subject,
                         "answer": value,
                         "rows": next((q["rows"] for q in questions
                                       if q["key"] == key), 0)})
    answered.sort(key=lambda a: (a["field"], a["subject"]))

    return templates.TemplateResponse(request, "package.html", {
        "user": db.get(User, uid), "job": job, "run": run, "kind": kind,
        "kind_label": pkg.KINDS[kind],
        "open_questions": open_q, "answered": answered,
        "warnings": warnings,
        "rows_covered": sum(q["rows"] for q in open_q),
    })


@router.post("/collections/{job_id}/packages/{kind}/decide")
async def decide(job_id: int, kind: str, request: Request,
                 key: str = Form(...), value: str = Form(default=""),
                 db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                          PackageRun.kind == kind).first()
        if run and not run.is_approved:
            pkg.apply_decision(db, run, key, value.strip())
    return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                            status_code=302)


@router.post("/collections/{job_id}/packages/{kind}/approve")
async def approve_package(job_id: int, kind: str, request: Request,
                          db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                          PackageRun.kind == kind).first()
        if run:
            pkg.approve(db, run, uid)
    return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                            status_code=302)


@router.post("/collections/{job_id}/packages/{kind}/deliver")
async def deliver_package(job_id: int, kind: str, request: Request,
                          db: DBSession = Depends(get_db)):
    """Put an approved package in the SAP import folder."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                          PackageRun.kind == kind).first()
        if run:
            pkg.deliver(db, run)
    return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                            status_code=302)


@router.post("/collections/{job_id}/packages/{kind}/reconcile")
async def reconcile_package(job_id: int, kind: str, request: Request,
                            db: DBSession = Depends(get_db)):
    """Re-read SAP and confirm the delivered package actually landed."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                          PackageRun.kind == kind).first()
        if run:
            pkg.reconcile(db, run)
    return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                            status_code=302)


@router.get("/collections/{job_id}/packages/{kind}/status")
async def package_status(job_id: int, kind: str, request: Request,
                         db: DBSession = Depends(get_db)):
    """Progress for a package still being built, polled by the page."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = _job(db, job_id, uid)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                      PackageRun.kind == kind).first()
    if not run:
        return JSONResponse({"state": "not_started"})
    return JSONResponse({"state": run.status, **pkg.progress_of(run)})


@router.post("/collections/{job_id}/find-sap-sheet")
async def find_sap_sheet(job_id: int, request: Request,
                         db: DBSession = Depends(get_db)):
    """Search Drive for this brand's nightly stock sheet."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        sheet_id, note = pkg.find_sap_sheet(job.brand or "")
        config = db.query(BrandConfig).filter(
            BrandConfig.user_id == uid,
            BrandConfig.brand == (job.brand or "")).first()
        if not config and job.brand:
            config = BrandConfig(user_id=uid, brand=job.brand)
            db.add(config)
        if config:
            if sheet_id:
                config.sap_sheet_id = sheet_id
            db.commit()
        request.session["sap_sheet_note"] = note
    return RedirectResponse(f"/collections/{job_id}", status_code=302)


@router.get("/collections/{job_id}/packages/{kind}/download")
async def download_package(job_id: int, kind: str, request: Request,
                           db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if not job:
        return RedirectResponse("/collections", status_code=302)
    run = db.query(PackageRun).filter(PackageRun.job_id == job.id,
                                      PackageRun.kind == kind).first()
    if not run or not run.file_path or not os.path.exists(run.file_path):
        return RedirectResponse(f"/collections/{job_id}/packages/{kind}",
                                status_code=302)
    return FileResponse(
        run.file_path, filename=os.path.basename(run.file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Brand settings ───────────────────────────────────────────────────────────

@router.post("/collections/{job_id}/identity")
async def set_identity(job_id: int, request: Request,
                       brand: str = Form(default=""), season: str = Form(default=""),
                       db: DBSession = Depends(get_db)):
    """Correct the brand or season by hand.

    Detection covers the registered brands, but a new label, a typo in a
    subject line or a multi-brand drop all leave it blank — and without a
    brand there is nowhere to hang the SAP history.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        if brand.strip():
            job.brand = brand.strip()[:255]
        if season.strip():
            job.season = season.strip()[:50]
        db.commit()
    return RedirectResponse(f"/collections/{job_id}", status_code=302)


@router.post("/collections/{job_id}/brand-config")
async def save_brand_config(job_id: int, request: Request,
                            sap_sheet_id: str = Form(default=""),
                            earliest_ship_date: str = Form(default=""),
                            delivery_root: str = Form(default=""),
                            db: DBSession = Depends(get_db)):
    """Point a brand at its nightly SAP sheet — where its history lives."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    job = _job(db, job_id, uid)
    if job:
        brand = job.brand or (job.email_subject or f"collection-{job.id}")[:255]
        if not job.brand:
            job.brand = brand          # so the setting has something to hang on
        config = db.query(BrandConfig).filter(
            BrandConfig.user_id == uid, BrandConfig.brand == brand).first()
        if not config:
            config = BrandConfig(user_id=uid, brand=brand)
            db.add(config)
        # Accept a full Google Sheets URL as well as a bare id.
        raw = (sap_sheet_id or "").strip()
        if "/spreadsheets/d/" in raw:
            raw = raw.split("/spreadsheets/d/")[1].split("/")[0]
        config.sap_sheet_id = raw
        config.earliest_ship_date = (earliest_ship_date or "").strip()
        config.delivery_root = (delivery_root or "").strip()
        db.commit()
    return RedirectResponse(f"/collections/{job_id}", status_code=302)

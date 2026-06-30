"""Product Management — attribute enrichment.

Upload a SAP product export → the engine assigns each style its SAP product type
+ FABRIC/FIT/STYLE/WEIGHT (constrained to SAP's value lists) → download the SAP
attribute upload sheet. Low-confidence / unmapped styles are flagged for review.
"""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.auth import get_current_user_id
from app.config import OUTPUT_DIR
from app.core.attribute_engine import (
    build_upload_workbook,
    enrich_style,
    parse_sap_products,
)
from app.templates_config import templates

router = APIRouter()

# In-memory job store: job_id -> {...}. Results live for the session lifetime.
_jobs: dict[str, dict] = {}
_MAX_WORKERS = 4


def _job_dir(job_id: str) -> str:
    d = os.path.join(str(OUTPUT_DIR), "products", job_id)
    os.makedirs(d, exist_ok=True)
    return d


@router.get("/products", response_class=HTMLResponse)
async def products_page(request: Request):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "products.html", {})


@router.post("/products/run")
async def products_run(request: Request):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    upload = form.get("file")
    if not upload or not getattr(upload, "filename", ""):
        return JSONResponse({"error": "No file provided"}, status_code=400)
    if not upload.filename.lower().endswith((".xlsx", ".xlsm")):
        return JSONResponse({"error": "Please upload an .xlsx product export"}, status_code=400)

    job_id = uuid.uuid4().hex
    in_path = os.path.join(_job_dir(job_id), "input.xlsx")
    with open(in_path, "wb") as fh:
        fh.write(await upload.read())

    try:
        styles, meta = await asyncio.to_thread(parse_sap_products, in_path)
    except Exception as e:
        return JSONResponse({"error": f"Could not read product export: {e}"}, status_code=400)
    if not styles:
        return JSONResponse({"error": "No product styles found in the file."}, status_code=400)

    _jobs[job_id] = {
        "uid": uid, "status": "running", "total": len(styles), "done": 0,
        "filename": upload.filename, "columns": meta.get("columns_found", []),
        "results": [], "output_path": None, "summary": None, "error": None,
    }

    threading.Thread(target=_run_job, args=(job_id, styles), daemon=True).start()
    return JSONResponse({"ok": True, "job_id": job_id, "total": len(styles),
                         "columns": meta.get("columns_found", [])})


def _run_job(job_id: str, styles: list[dict]) -> None:
    job = _jobs[job_id]
    results: list[dict] = []
    lock = threading.Lock()

    def work(style):
        try:
            r = enrich_style(style)
        except Exception:
            r = {**style, "product_type": None, "confidence": 0.0,
                 "FABRIC": None, "FIT": None, "WEIGHT": None, "STYLE": [],
                 "needs_review": True}
        with lock:
            results.append(r)
            job["done"] += 1
        return r

    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            list(pool.map(work, styles))
        out_path = os.path.join(_job_dir(job_id), "SAP_attribute_upload.xlsx")
        summary = build_upload_workbook(results, out_path)
        job["results"] = results
        job["output_path"] = out_path
        job["summary"] = summary
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def _owned_job(request: Request, job_id: str):
    uid = get_current_user_id(request)
    job = _jobs.get(job_id)
    if not uid or not job or job.get("uid") != uid:
        return None
    return job


@router.get("/products/job/{job_id}")
async def products_job(job_id: str, request: Request):
    job = _owned_job(request, job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    payload = {
        "status": job["status"], "total": job["total"], "done": job["done"],
        "error": job.get("error"), "summary": job.get("summary"),
        "filename": job.get("filename"), "columns": job.get("columns", []),
    }
    if job["status"] == "done":
        # Compact preview: per-style attribute counts + review flags.
        preview = []
        for r in job["results"]:
            attrs = []
            if r.get("FABRIC"): attrs.append(f"FABRIC:{r['FABRIC']}")
            if r.get("FIT"): attrs.append(f"FIT:{r['FIT']}")
            if r.get("WEIGHT"): attrs.append(f"WEIGHT:{r['WEIGHT']}")
            attrs += [f"STYLE:{s}" for s in r.get("STYLE", [])]
            preview.append({
                "style_code": r["style_code"], "name": r.get("name", ""),
                "item_group": r["master_group"], "product_type": r.get("product_type"),
                "confidence": round(r.get("confidence") or 0, 2),
                "attributes": attrs, "needs_review": bool(r.get("needs_review")),
            })
        payload["preview"] = preview
    return JSONResponse(payload)


@router.get("/products/download/{job_id}")
async def products_download(job_id: str, request: Request):
    job = _owned_job(request, job_id)
    if job is None or not job.get("output_path") or not os.path.exists(job["output_path"]):
        return RedirectResponse("/products", status_code=302)
    return FileResponse(
        job["output_path"],
        filename="SAP_attribute_upload.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

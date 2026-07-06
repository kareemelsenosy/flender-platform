"""Product Management — attribute enrichment.

Upload one or more SAP product exports → the engine assigns each style its SAP
product type + FABRIC/FIT/STYLE/WEIGHT (constrained to SAP's value lists) →
download the SAP attribute upload sheet. Low-confidence / unmapped styles are
flagged for review.

Runs are **saved** to the database so users can reopen them, re-download, and
**hand-correct** the AI's product type / attributes (the corrections flow into
the download and clear the review flag).
"""
from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import OUTPUT_DIR
from app.core.attribute_engine import (
    build_upload_workbook,
    enrich_style,
    parse_sap_products,
)
from app.core.attribute_taxonomy import PRODUCT_TYPES_BY_GROUP, VALUE_LISTS
from app.database import SessionLocal, get_db
from app.models import ProductAttributeRun
from app.templates_config import templates

router = APIRouter()

# Live progress for in-flight runs — {run_id: {"done", "total", "status", "error"}}.
# The persisted results live in the DB; this is only the during-run progress bar.
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()
_MAX_WORKERS = 4


# ── Attribute value vocabulary (for the inline edit dropdowns) ────────────────
_VALID_PT_BY_GROUP = {g: {c for c, _ in cands} for g, cands in PRODUCT_TYPES_BY_GROUP.items()}


def _summary_counts(results: list[dict]) -> dict:
    """Clean styles / review styles / SAP rows — mirrors build_upload_workbook."""
    clean = rows = review = 0
    for r in results:
        if r.get("needs_review"):
            review += 1
        if r.get("needs_review") or not r.get("product_type"):
            continue
        clean += 1
        rows += 1  # the product-type "Y" row
        for a in ("FABRIC", "FIT", "WEIGHT"):
            if r.get(a):
                rows += 1
        rows += len(r.get("STYLE") or [])
    return {"clean_styles": clean, "review_styles": review, "rows": rows}


def _preview_row(r: dict) -> dict:
    """Full editable shape for one style row (display + raw attribute values)."""
    attrs = []
    if r.get("FABRIC"):
        attrs.append(f"FABRIC:{r['FABRIC']}")
    if r.get("FIT"):
        attrs.append(f"FIT:{r['FIT']}")
    if r.get("WEIGHT"):
        attrs.append(f"WEIGHT:{r['WEIGHT']}")
    attrs += [f"STYLE:{s}" for s in (r.get("STYLE") or [])]
    return {
        "style_code": r.get("style_code"),
        "name": r.get("name", ""),
        "item_group": r.get("master_group"),
        "master_group": r.get("master_group"),
        "product_type": r.get("product_type"),
        "confidence": round(r.get("confidence") or 0, 2),
        "attributes": attrs,
        "FABRIC": r.get("FABRIC"), "FIT": r.get("FIT"),
        "WEIGHT": r.get("WEIGHT"), "STYLE": r.get("STYLE") or [],
        "needs_review": bool(r.get("needs_review")),
        "edited": bool(r.get("edited")),
    }


def _run_summary(run: ProductAttributeRun) -> dict:
    return {"clean_styles": run.clean_count, "review_styles": run.review_count,
            "rows": run.row_count}


@router.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    runs = (
        db.query(ProductAttributeRun)
        .filter(ProductAttributeRun.user_id == uid)
        .order_by(ProductAttributeRun.created_at.desc())
        .limit(50)
        .all()
    )
    history = [{
        "id": r.id, "name": r.name, "status": r.status,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        "total": r.total_styles, "review": r.review_count, "clean": r.clean_count,
    } for r in runs]
    # Product-type options per group + attribute value lists power the edit UI.
    pt_options = {g: [{"code": c, "name": n} for c, n in cands]
                  for g, cands in PRODUCT_TYPES_BY_GROUP.items()}
    return templates.TemplateResponse(request, "products.html", {
        "history": history,
        "pt_options": pt_options,
        "value_lists": VALUE_LISTS,
    })


@router.post("/products/run")
async def products_run(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    uploads = [f for f in form.getlist("file") if getattr(f, "filename", "")]
    if not uploads:
        return JSONResponse({"error": "No file provided"}, status_code=400)
    for up in uploads:
        if not up.filename.lower().endswith((".xlsx", ".xlsm")):
            return JSONResponse({"error": f"'{up.filename}' is not an .xlsx product export"}, status_code=400)

    # Parse every file and combine, de-duplicating styles by style code (the same
    # style appearing in two exports is the same product — keep the first).
    combined: dict[str, dict] = {}
    columns: list[str] = []
    names: list[str] = []
    workdir = tempfile.mkdtemp(prefix="prodattr_")
    for up in uploads:
        names.append(up.filename)
        path = os.path.join(workdir, up.filename)
        with open(path, "wb") as fh:
            fh.write(await up.read())
        try:
            styles, meta = parse_sap_products(path)
        except Exception as e:
            return JSONResponse({"error": f"Could not read '{up.filename}': {e}"}, status_code=400)
        for c in meta.get("columns_found", []):
            if c not in columns:
                columns.append(c)
        for s in styles:
            key = str(s.get("style_code") or "").strip()
            if key and key not in combined:
                combined[key] = s
    styles = list(combined.values())
    if not styles:
        return JSONResponse({"error": "No product styles found in the file(s)."}, status_code=400)

    run = ProductAttributeRun(
        user_id=uid,
        name=" + ".join(names)[:500],
        status="running",
        filename=", ".join(names)[:1000],
        total_styles=len(styles),
    )
    run.columns = columns
    db.add(run)
    db.commit()
    db.refresh(run)

    with _progress_lock:
        _progress[run.id] = {"done": 0, "total": len(styles), "status": "running", "error": None}
    threading.Thread(target=_run_job, args=(run.id, styles), daemon=True).start()
    return JSONResponse({"ok": True, "run_id": run.id, "total": len(styles), "columns": columns})


def _run_job(run_id: int, styles: list[dict]) -> None:
    lock = threading.Lock()
    results: list[dict] = []

    def work(style):
        try:
            r = enrich_style(style)
        except Exception:
            r = {**style, "product_type": None, "confidence": 0.0,
                 "FABRIC": None, "FIT": None, "WEIGHT": None, "STYLE": [],
                 "needs_review": True}
        with lock:
            results.append(r)
            with _progress_lock:
                if run_id in _progress:
                    _progress[run_id]["done"] += 1
        return r

    db = SessionLocal()
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            list(pool.map(work, styles))
        summary = _summary_counts(results)
        run = db.get(ProductAttributeRun, run_id)
        if run:
            run.results = results
            run.clean_count = summary["clean_styles"]
            run.review_count = summary["review_styles"]
            run.row_count = summary["rows"]
            run.status = "done"
            db.commit()
        with _progress_lock:
            if run_id in _progress:
                _progress[run_id]["status"] = "done"
    except Exception as e:
        run = db.get(ProductAttributeRun, run_id)
        if run:
            run.status = "error"
            run.error = str(e)
            db.commit()
        with _progress_lock:
            if run_id in _progress:
                _progress[run_id].update({"status": "error", "error": str(e)})
    finally:
        db.close()


def _owned_run(db: DBSession, request: Request, run_id: int) -> ProductAttributeRun | None:
    uid = get_current_user_id(request)
    if not uid:
        return None
    run = db.get(ProductAttributeRun, run_id)
    if not run or run.user_id != uid:
        return None
    return run


@router.get("/products/run/{run_id}/status")
async def products_status(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    with _progress_lock:
        prog = dict(_progress.get(run_id, {}))
    status = prog.get("status") or run.status
    done = prog.get("done", run.total_styles if run.status == "done" else 0)
    payload = {
        "status": status, "total": run.total_styles, "done": done,
        "error": run.error or prog.get("error"),
    }
    if status == "done":
        payload["summary"] = _run_summary(run)
        payload["preview"] = [_preview_row(r) for r in run.results]
    return JSONResponse(payload)


@router.get("/products/run/{run_id}")
async def products_open(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Reopen a saved run — its full results for the table."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "id": run.id, "name": run.name, "status": run.status,
        "summary": _run_summary(run),
        "preview": [_preview_row(r) for r in run.results],
    })


@router.post("/products/run/{run_id}/style")
async def products_edit_style(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Hand-correct one style's product type / attributes. Validates against the
    SAP value lists, clears its review flag, and re-computes the run summary."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    style_code = str(data.get("style_code") or "").strip()
    if not style_code:
        return JSONResponse({"error": "style_code required"}, status_code=400)

    results = run.results
    target = next((r for r in results if str(r.get("style_code")) == style_code), None)
    if target is None:
        return JSONResponse({"error": "style not found in run"}, status_code=404)

    grp = target.get("master_group")
    valid_pt = _VALID_PT_BY_GROUP.get(grp, set())

    if "product_type" in data:
        pt = (str(data["product_type"]).strip() or None)
        if pt is not None and pt not in valid_pt:
            return JSONResponse({"error": f"'{pt}' is not a valid product type for {grp}"}, status_code=400)
        target["product_type"] = pt
    for attr in ("FABRIC", "FIT", "WEIGHT"):
        if attr in data:
            v = (str(data[attr]).strip() or None)
            if v is not None and v not in VALUE_LISTS[attr]:
                return JSONResponse({"error": f"'{v}' is not a valid {attr}"}, status_code=400)
            target[attr] = v
    if "STYLE" in data:
        raw = data["STYLE"] if isinstance(data["STYLE"], list) else []
        target["STYLE"] = [s for s in raw if s in VALUE_LISTS["STYLE"]][:2]

    # A hand-corrected style is considered reviewed once it has a product type.
    target["edited"] = True
    target["needs_review"] = not bool(target.get("product_type"))
    if not target["needs_review"]:
        target["confidence"] = 1.0

    summary = _summary_counts(results)
    run.results = results
    run.clean_count = summary["clean_styles"]
    run.review_count = summary["review_styles"]
    run.row_count = summary["rows"]
    db.commit()

    return JSONResponse({"ok": True, "summary": summary, "row": _preview_row(target)})


@router.get("/products/download/{run_id}")
async def products_download(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return RedirectResponse("/products", status_code=302)
    # Rebuild the workbook from the saved (possibly hand-corrected) results so the
    # download always reflects the latest edits and survives server restarts.
    out_dir = os.path.join(str(OUTPUT_DIR), "products", str(run.id))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "SAP_attribute_upload.xlsx")
    build_upload_workbook(run.results, out_path)
    return FileResponse(
        out_path,
        filename="SAP_attribute_upload.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/products/run/{run_id}/delete")
async def products_delete(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    db.delete(run)
    db.commit()
    with _progress_lock:
        _progress.pop(run_id, None)
    return JSONResponse({"ok": True})

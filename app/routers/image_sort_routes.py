"""Image Sorter — file unnamed product photos into SAP Item Group folders.

Flow:
  ``POST /image-sort/run``           start a run (master file + image sources)
  ``GET  /image-sort/run/{id}/status`` poll progress, then the full result
  ``POST /image-sort/run/{id}/assign`` correct one photo's item group
  ``POST /image-sort/run/{id}/main``   promote one photo to ``_1`` (main)
  ``GET  /image-sort/download/{id}``   the folder ZIP
  ``GET  /image-sort/report/{id}``     the match report CSV

Every run is saved, so a user can reopen it, fix the AI's mistakes, and
re-download the ZIP without paying for the vision passes again. The rebuilt ZIP
always reflects the latest corrections.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import GENERATED_FILE_RETENTION_DAYS, OUTPUT_DIR
from app.core.image_sorter import (
    CONFIDENCE_REVIEW_THRESHOLD,
    ImageSortError,
    ItemGroup,
    MatchResult,
    assign_positions,
    build_output_zip,
    build_report_csv,
    build_thumbnail,
    build_thumbnails,
    collect_images,
    image_file_name,
    match_images,
    parse_master_workbook,
    safe_folder_name,
    summarise,
)
from app.core.image_sources import (
    SourceFetchError,
    expand_pdfs,
    fetch_google_sheet_xlsx,
    fetch_image_source,
    save_upload,
)
from app.database import SessionLocal, get_db
from app.models import ImageSortRun, User
from app.services.ai_service import ai_available
from app.templates_config import templates

logger = logging.getLogger(__name__)
router = APIRouter()

MASTER_EXTS = (".xlsx", ".xlsm")
_MAX_WORKERS = 6

# Live progress for in-flight runs. The saved results live in the DB; this only
# drives the progress bar between "started" and "done".
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


def _run_dir(run_id: int) -> Path:
    return Path(OUTPUT_DIR) / "image_sort" / str(run_id)


def _set_progress(run_id: int, **fields) -> None:
    with _progress_lock:
        _progress.setdefault(run_id, {}).update(fields)


# ── Page ─────────────────────────────────────────────────────────────────────

@router.get("/image-sort", response_class=HTMLResponse)
async def image_sort_page(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    # Runs this user owns, plus any a colleague handed them to check.
    from sqlalchemy import or_
    runs = (
        db.query(ImageSortRun)
        .filter(or_(ImageSortRun.user_id == uid, ImageSortRun.assigned_to_id == uid))
        .order_by(ImageSortRun.created_at.desc())
        .limit(40)
        .all()
    )
    names = {
        u.id: u.username
        for u in db.query(User.id, User.username).filter(
            User.id.in_({r.user_id for r in runs} | {r.assigned_to_id for r in runs if r.assigned_to_id})
        )
    } if runs else {}

    history = [{
        "id": r.id,
        "name": r.name or "Untitled run",
        "status": r.status,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        "images": r.total_images,
        "matched": r.matched_count,
        "review": r.review_count,
        "folders": r.folder_count,
        "mine": r.user_id == uid,
        "owner": names.get(r.user_id, ""),
        "assigned_to": names.get(r.assigned_to_id, "") if r.assigned_to_id else "",
    } for r in runs]

    return templates.TemplateResponse(request, "image_sort.html", {
        "history": history,
        "ai_ready": ai_available(),
    })


# ── Start a run ──────────────────────────────────────────────────────────────

@router.post("/image-sort/run")
async def image_sort_run(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    master_upload = form.get("master")
    master_url = str(form.get("master_url") or "").strip()
    image_uploads = [f for f in form.getlist("images") if getattr(f, "filename", "")]
    image_links = [
        line.strip()
        for line in str(form.get("image_links") or "").replace(",", "\n").splitlines()
        if line.strip()
    ]
    brand = str(form.get("brand") or "").strip()
    code_column = str(form.get("code_column") or "").strip()

    has_master = bool(getattr(master_upload, "filename", "")) or bool(master_url)
    if not has_master:
        return JSONResponse(
            {"error": "Add the SAP master file — upload the .xlsx or paste its Google Sheets link."},
            status_code=400)
    if not image_uploads and not image_links:
        return JSONResponse(
            {"error": "Add the photos — upload a ZIP / image files, or paste a Dropbox, Drive or Brandboom link."},
            status_code=400)

    # Create the run first so its id names the working directory.
    run = ImageSortRun(
        user_id=uid, status="running", stage="Reading the master file",
        brand=brand, name="Image sort",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    work_dir = _run_dir(run.id)
    source_dir = work_dir / "source"
    master_dir = work_dir / "master"
    source_dir.mkdir(parents=True, exist_ok=True)
    master_dir.mkdir(parents=True, exist_ok=True)
    run.work_dir = str(work_dir)

    def fail(message: str, status: int = 400):
        run.status = "error"
        run.error = message
        db.commit()
        return JSONResponse({"error": message, "run_id": run.id}, status_code=status)

    # ── Master file ──────────────────────────────────────────────────────────
    try:
        if getattr(master_upload, "filename", ""):
            if not master_upload.filename.lower().endswith(MASTER_EXTS):
                return fail(f"'{master_upload.filename}' is not an .xlsx master file.")
            master_path = save_upload(await master_upload.read(), master_upload.filename, master_dir)
            master_name = master_upload.filename
        else:
            master_path = fetch_google_sheet_xlsx(master_url, master_dir)
            master_name = master_url
    except SourceFetchError as e:
        return fail(str(e))

    try:
        groups, master_meta = parse_master_workbook(str(master_path), code_column=code_column or None)
    except ImageSortError as e:
        return fail(str(e))
    except Exception as e:
        logger.error(f"Master parse failed on run {run.id}: {e}", exc_info=True)
        return fail(f"Could not read the master file: {e}")

    if not brand:
        brand = next((g.brand for g in groups if g.brand), "")

    # ── Image sources ────────────────────────────────────────────────────────
    sources: list[dict] = []
    for upload in image_uploads:
        data = await upload.read()
        save_upload(data, upload.filename, source_dir)
        sources.append({"kind": "upload", "name": upload.filename, "bytes": len(data)})
    for link in image_links:
        try:
            fetched = fetch_image_source(link, source_dir)
            sources.append({"kind": fetched["kind"], "name": link, "files": len(fetched["files"])})
        except SourceFetchError as e:
            return fail(str(e))
        except Exception as e:
            logger.error(f"Source fetch failed on run {run.id}: {e}", exc_info=True)
            return fail(f"Could not fetch '{link}': {e}")

    expand_pdfs(source_dir)
    try:
        images = collect_images(source_dir)
    except Exception as e:
        logger.error(f"Image collection failed on run {run.id}: {e}", exc_info=True)
        return fail(f"Could not read the uploaded photos: {e}")
    if not images:
        return fail("No usable photos were found in those sources.")

    run.name = f"{master_name} — {len(images)} photos"[:500]
    run.master_name = master_name[:500]
    run.brand = brand[:255]
    run.sources = sources
    run.total_images = len(images)
    run.group_count = len(groups)
    run.groups = [g.to_dict() for g in groups]
    run.stage = "Matching photos"
    db.commit()

    _set_progress(run.id, done=0, total=len(images), status="running", stage="Matching photos")
    threading.Thread(
        target=_match_job, args=(run.id, images, groups, brand), daemon=True
    ).start()

    return JSONResponse({
        "ok": True,
        "run_id": run.id,
        "images": len(images),
        "groups": len(groups),
        "brand": brand,
        "master": master_meta,
        "sources": sources,
    })


def _match_job(run_id: int, images, groups, brand: str) -> None:
    """Background worker: match every photo, then persist the results."""
    def progress(done: int, total: int) -> None:
        _set_progress(run_id, done=done, total=total)

    db = SessionLocal()
    try:
        results = match_images(
            images, groups, brand=brand,
            max_workers=_MAX_WORKERS, on_progress=progress,
        )
        # Build the review-grid previews before the page can ask for them —
        # generating a few hundred on demand is what makes the grid look broken.
        _set_progress(run_id, stage="Preparing previews")
        try:
            build_thumbnails(results, Path(_run_dir(run_id)) / "thumbs")
        except Exception as e:
            logger.warning(f"Thumbnail pre-build failed for run {run_id}: {e}")
        summary = summarise(results, groups)
        run = db.get(ImageSortRun, run_id)
        if run:
            run.results = [r.to_dict() for r in results]
            run.matched_count = summary["matched"]
            run.review_count = summary["review"]
            run.folder_count = summary["groups_covered"]
            run.status = "done"
            run.stage = "Done"
            db.commit()
        _set_progress(run_id, status="done", stage="Done")
    except Exception as e:
        logger.error(f"Image sort run {run_id} failed: {e}", exc_info=True)
        run = db.get(ImageSortRun, run_id)
        if run:
            run.status = "error"
            run.error = str(e)
            db.commit()
        _set_progress(run_id, status="error", error=str(e))
    finally:
        db.close()


# ── Reading a run ────────────────────────────────────────────────────────────

def _owned_run(db: DBSession, request: Request, run_id: int) -> ImageSortRun | None:
    """The run, if this user may edit it — the owner or the QC assignee."""
    uid = get_current_user_id(request)
    if not uid:
        return None
    run = db.get(ImageSortRun, run_id)
    if not run or uid not in (run.user_id, run.assigned_to_id):
        return None
    return run


def _owner_run(db: DBSession, request: Request, run_id: int) -> ImageSortRun | None:
    """Stricter: only the owner. Reassigning and deleting are not the
    assignee's to do — they were handed the checking, not the run."""
    uid = get_current_user_id(request)
    if not uid:
        return None
    run = db.get(ImageSortRun, run_id)
    if not run or run.user_id != uid:
        return None
    return run


def _load(run: ImageSortRun) -> tuple[list[MatchResult], list[ItemGroup]]:
    results = [MatchResult.from_dict(d) for d in run.results]
    groups = [ItemGroup(**g) for g in run.groups]
    return results, groups


def _payload(run: ImageSortRun) -> dict:
    """Full result view: folders (with their photos) + the unmatched pile.

    Each folder carries two lists. ``photos`` are the product images that get
    named and exported; ``reference`` are catalogue crops of the same product,
    shown alongside purely so the user can eyeball that the tool matched the
    right thing.
    """
    results, groups = _load(run)
    by_code = {g.code: g for g in groups}
    approved = set(run.approved)

    folders: dict[str, dict] = {}
    unmatched: list[dict] = []
    for result in sorted(results, key=lambda r: (r.code, r.position, r.filename)):
        if result.deleted:
            continue
        exported = bool(result.code) and not result.is_reference
        card = {
            "index": result.index,
            "filename": result.filename,
            "position": result.position,
            "confidence": round(result.confidence, 2),
            "method": result.method,
            "reason": result.reason,
            "needs_review": result.needs_review,
            "is_reference": result.is_reference,
            "is_catalog": result.is_catalog,
            "output_name": image_file_name(result.code, result.position, result.filename) if exported else "",
            "thumb": f"/image-sort/run/{run.id}/photo/{result.index}",
            "saw": {
                "product_type": (result.description or {}).get("product_type", ""),
                "primary_color": (result.description or {}).get("primary_color", ""),
                "visible_text": (result.description or {}).get("visible_text", [])[:4],
                "view": (result.description or {}).get("view", ""),
            },
            "candidates": result.candidates[:6],
        }
        if not result.code:
            unmatched.append(card)
            continue
        group = by_code.get(result.code)
        folder = folders.setdefault(result.code, {
            "code": result.code,
            "folder": safe_folder_name(result.code),
            "name": group.name if group else "",
            "color": group.color if group else "",
            "category": group.category if group else "",
            "photos": [],
            "reference": [],
            "needs_review": False,
            "approved": result.code in approved,
        })
        folder["reference" if result.is_reference else "photos"].append(card)
        folder["needs_review"] = folder["needs_review"] or result.needs_review

    # A folder left with only catalogue reference and no product image is not
    # covered — surface it with the ones that got nothing at all.
    covered = {code for code, f in folders.items() if f["photos"]}
    missing = [
        {"code": g.code, "name": g.name, "color": g.color, "category": g.category}
        for g in groups if g.code not in covered
    ]

    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "brand": run.brand,
        "master": run.master_name,
        "sources": run.sources,
        "summary": summarise(results, groups),
        "review_threshold": CONFIDENCE_REVIEW_THRESHOLD,
        "export_catalog": bool(run.export_catalog),
        "assigned_to": run.assigned_to_id,
        "approved_count": len([f for f in folders.values() if f["approved"]]),
        "folders": sorted(folders.values(), key=lambda f: f["code"]),
        "unmatched": unmatched,
        "missing": missing,
        "all_groups": [
            {"code": g.code, "name": g.name, "color": g.color, "category": g.category}
            for g in sorted(groups, key=lambda g: g.code)
        ],
    }


@router.get("/image-sort/run/{run_id}/status")
async def image_sort_status(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    with _progress_lock:
        prog = dict(_progress.get(run_id, {}))
    status = prog.get("status") or run.status
    body = {
        "status": status,
        "stage": prog.get("stage") or run.stage or "",
        "done": prog.get("done", run.total_images if run.status == "done" else 0),
        "total": prog.get("total", run.total_images),
        "error": run.error or prog.get("error"),
    }
    if status == "done":
        body["result"] = _payload(run)
    return JSONResponse(body)


@router.get("/image-sort/run/{run_id}")
async def image_sort_open(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_payload(run))


@router.get("/image-sort/run/{run_id}/photo/{index}")
def image_sort_photo(run_id: int, index: int, request: Request, db: DBSession = Depends(get_db)):
    """Serve one photo's preview for the review grid.

    Deliberately a sync ``def`` (FastAPI runs it in a threadpool): the review
    page fires a request per photo, and doing this DB work on the event loop
    stalls every other request on the page.

    It serves the pre-built thumbnail, not the original — a season's photos are
    ~2 MB each, and a few hundred of those at once is what left the grid full of
    broken images. Only the two columns needed are selected, so the run's large
    results JSON is never parsed on this path.
    """
    uid = get_current_user_id(request)
    if not uid:
        return Response(status_code=404)
    row = (
        db.query(ImageSortRun.user_id, ImageSortRun.work_dir)
        .filter(ImageSortRun.id == run_id)
        .first()
    )
    if row is None or row.user_id != uid:
        return Response(status_code=404)

    work_dir = Path(row.work_dir or _run_dir(run_id))
    thumb = work_dir / "thumbs" / f"{index}.jpg"
    if thumb.is_file():
        return FileResponse(thumb, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    # No thumbnail yet — a run from before previews existed, or one whose
    # pre-build was interrupted. Build it now from the source and cache it.
    run = db.get(ImageSortRun, run_id)
    if run is None:
        return Response(status_code=404)
    result = next((r for r in run.results if int(r.get("index") or 0) == index), None)
    if not result:
        return Response(status_code=404)

    source = Path(str(result.get("path") or ""))
    # Never serve anything outside this run's own working directory.
    try:
        if not str(source.resolve()).startswith(str(work_dir.resolve())) or not source.is_file():
            return Response(status_code=404)
    except OSError:
        return Response(status_code=404)

    if build_thumbnail(source, thumb):
        return FileResponse(thumb, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})
    return FileResponse(source, headers={"Cache-Control": "private, max-age=3600"})


# ── Corrections ──────────────────────────────────────────────────────────────

def _save_results(db: DBSession, run: ImageSortRun, results: list[MatchResult], groups: list[ItemGroup]) -> None:
    assign_positions(results, export_catalog=bool(run.export_catalog))
    summary = summarise(results, groups)
    run.results = [r.to_dict() for r in results]
    run.matched_count = summary["matched"]
    run.review_count = summary["review"]
    run.folder_count = summary["groups_covered"]
    db.commit()


@router.post("/image-sort/run/{run_id}/assign")
async def image_sort_assign(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Move one photo to a different item group (or to the unmatched pile)."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "index required"}, status_code=400)
    code = str(data.get("code") or "").strip()

    results, groups = _load(run)
    target = next((r for r in results if r.index == index), None)
    if target is None:
        return JSONResponse({"error": "photo not found in this run"}, status_code=404)
    if code and code not in {g.code for g in groups}:
        return JSONResponse({"error": f"'{code}' is not an item group in this master file"}, status_code=400)

    target.code = code
    target.method = "manual" if code else "none"
    target.confidence = 1.0 if code else 0.0
    target.reason = "Set by hand." if code else "Unassigned by hand."
    # It is leaving this folder, so its old pinned slot means nothing in the
    # new one. Every other folder keeps its own hand-set order untouched.
    target.manual_rank = 0
    target.flagged = ""
    _save_results(db, run, results, groups)
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.post("/image-sort/run/{run_id}/main")
async def image_sort_set_main(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Promote one photo to ``_1`` — the main image of its folder."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "index required"}, status_code=400)

    results, groups = _load(run)
    target = next((r for r in results if r.index == index), None)
    if target is None or not target.code:
        return JSONResponse({"error": "that photo is not in a folder"}, status_code=400)

    # Pin this folder's order: the chosen photo first, the rest behind it in
    # their current order. manual_rank is what makes the choice survive later
    # corrections elsewhere in the run — assign_positions() honours it.
    siblings = sorted(
        [r for r in results if r.code == target.code],
        key=lambda r: (r.index != index, r.position or 999, r.filename.lower()),
    )
    for rank, result in enumerate(siblings, start=1):
        result.manual_rank = rank

    _save_results(db, run, results, groups)
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.post("/image-sort/run/{run_id}/delete-photo")
async def image_sort_delete_photo(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Throw a photo out of the run — the X on the tile.

    Kept as a flag rather than dropping the record so it can be undone and so
    the match report can still show what was rejected.
    """
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "index required"}, status_code=400)
    deleted = bool(data.get("deleted", True))

    results, groups = _load(run)
    target = next((r for r in results if r.index == index), None)
    if target is None:
        return JSONResponse({"error": "photo not found in this run"}, status_code=404)

    target.deleted = deleted
    if deleted:
        # Its slot in the folder goes with it; the rest close up automatically.
        target.manual_rank = 0
    _save_results(db, run, results, groups)
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.post("/image-sort/run/{run_id}/reorder")
async def image_sort_reorder(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Set a folder's photo order outright — drag and drop sends the new order.

    The order is stored as manual_rank so it survives every later correction,
    the same way "Make main" does.
    """
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    code = str(data.get("code") or "").strip()
    try:
        order = [int(i) for i in (data.get("order") or [])]
    except (TypeError, ValueError):
        return JSONResponse({"error": "order must be a list of photo indexes"}, status_code=400)
    if not code or not order:
        return JSONResponse({"error": "code and order required"}, status_code=400)

    results, groups = _load(run)
    in_folder = {
        r.index: r for r in results
        if r.code == code and not r.deleted and not r.is_reference
    }
    if set(order) != set(in_folder):
        return JSONResponse(
            {"error": "the order must list exactly the photos in that folder"},
            status_code=400)

    for rank, index in enumerate(order, start=1):
        in_folder[index].manual_rank = rank
    _save_results(db, run, results, groups)
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.post("/image-sort/run/{run_id}/approve")
async def image_sort_approve(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Sign a folder off (or put it back). Approved folders drop out of the
    working list so a big collection stops being one endless scroll."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    codes = data.get("codes")
    if codes is None:
        codes = [data.get("code")]
    codes = [str(c).strip() for c in codes if str(c or "").strip()]
    if not codes:
        return JSONResponse({"error": "code required"}, status_code=400)

    known = {g["code"] for g in run.groups}
    unknown = [c for c in codes if c not in known]
    if unknown:
        return JSONResponse({"error": f"'{unknown[0]}' is not an item group in this run"},
                            status_code=400)

    approved = set(run.approved)
    if bool(data.get("approved", True)):
        approved |= set(codes)
    else:
        approved -= set(codes)
    run.approved = list(approved)
    db.commit()
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.post("/image-sort/run/{run_id}/catalog-export")
async def image_sort_catalog_export(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Toggle whether catalogue pages are exported as product images.

    Off (the default) they stay reference-only; on, they are named and land in
    the ZIP like any other photo.
    """
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    run.export_catalog = bool(data.get("export_catalog"))
    results, groups = _load(run)
    _save_results(db, run, results, groups)
    return JSONResponse({"ok": True, "result": _payload(run)})


@router.get("/image-sort/teammates")
async def image_sort_teammates(request: Request, db: DBSession = Depends(get_db)):
    """Colleagues a run can be handed to for checking."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    users = (
        db.query(User.id, User.username)
        .filter(User.id != uid, User.is_active.is_(True))
        .order_by(User.username)
        .limit(200)
        .all()
    )
    return JSONResponse({"users": [{"id": u.id, "username": u.username} for u in users]})


@router.post("/image-sort/run/{run_id}/assign-user")
async def image_sort_assign_user(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Hand the run to a colleague to finish the quality check.

    They get the same editing rights; only the owner can reassign or delete.
    """
    run = _owner_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    raw = data.get("user_id")
    if raw in (None, "", 0, "0"):
        run.assigned_to_id = None
        db.commit()
        return JSONResponse({"ok": True, "assigned_to": None, "assigned_to_name": ""})

    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "user_id must be a number"}, status_code=400)
    if user_id == run.user_id:
        return JSONResponse({"error": "that is already the owner of this run"}, status_code=400)

    user = db.get(User, user_id)
    if not user or not user.is_active:
        return JSONResponse({"error": "no such user"}, status_code=404)

    run.assigned_to_id = user.id
    db.commit()
    return JSONResponse({"ok": True, "assigned_to": user.id, "assigned_to_name": user.username})


# ── Downloads ────────────────────────────────────────────────────────────────

@router.get("/image-sort/download/{run_id}")
async def image_sort_download(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    """The folder ZIP — rebuilt from the saved (possibly corrected) results."""
    run = _owned_run(db, request, run_id)
    if run is None:
        # Browser navigation: never return JSON here or the browser saves a
        # ".json" file instead of showing the page.
        return RedirectResponse("/image-sort", status_code=302)

    results, _groups = _load(run)
    work_dir = Path(run.work_dir or _run_dir(run.id))
    zip_path = work_dir / "sorted_images.zip"
    try:
        build_output_zip(results, zip_path, work_dir)
    except Exception as e:
        logger.error(f"ZIP build failed for run {run_id}: {e}", exc_info=True)
        return RedirectResponse("/image-sort?error=zip", status_code=302)

    stem = os.path.splitext(os.path.basename(run.master_name or "images"))[0] or "images"
    return FileResponse(
        zip_path,
        filename=f"{safe_folder_name(stem)}_SAP_image_folders.zip",
        media_type="application/zip",
    )


@router.get("/image-sort/report/{run_id}")
async def image_sort_report(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    run = _owned_run(db, request, run_id)
    if run is None:
        return RedirectResponse("/image-sort", status_code=302)
    results, groups = _load(run)
    csv_text = build_report_csv(results, groups)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="image_sort_report_{run_id}.csv"'},
    )


def prune_old_image_sort_dirs(db_session, max_age_days: int = GENERATED_FILE_RETENTION_DAYS) -> tuple[int, int]:
    """Delete the stored photos of runs that are gone or past the retention window.

    Each run keeps every source photo on disk so the ZIP can be rebuilt after a
    correction — a season drop is easily a gigabyte. Deleting a run cleans up its
    own directory, but abandoned runs (and anything left behind by a crash) would
    otherwise accumulate until the volume fills, which has taken this server down
    before. Called from startup alongside the session-output sweeper.

    Returns ``(dirs_removed, bytes_freed)``.
    """
    root = Path(OUTPUT_DIR) / "image_sort"
    if not root.exists():
        return (0, 0)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
    live_ids = {rid for (rid,) in db_session.query(ImageSortRun.id).all()}

    removed = 0
    bytes_freed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            run_id = int(entry.name)
        except ValueError:
            continue
        try:
            if run_id in live_ids and entry.stat().st_mtime >= cutoff:
                continue
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
            bytes_freed += size
        except OSError:
            continue
    return (removed, bytes_freed)


@router.post("/image-sort/run/{run_id}/delete")
async def image_sort_delete(run_id: int, request: Request, db: DBSession = Depends(get_db)):
    # Deleting the whole run is the owner's call, not the assignee's.
    run = _owner_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    from app.core.image_sources import cleanup_dir
    cleanup_dir(run.work_dir or _run_dir(run.id))
    db.delete(run)
    db.commit()
    with _progress_lock:
        _progress.pop(run_id, None)
    return JSONResponse({"ok": True})

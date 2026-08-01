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
from app.models import ImageSortRun
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

    runs = (
        db.query(ImageSortRun)
        .filter(ImageSortRun.user_id == uid)
        .order_by(ImageSortRun.created_at.desc())
        .limit(40)
        .all()
    )
    history = [{
        "id": r.id,
        "name": r.name or "Untitled run",
        "status": r.status,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        "images": r.total_images,
        "matched": r.matched_count,
        "review": r.review_count,
        "folders": r.folder_count,
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
    """Full result view: folders (with their photos) + the unmatched pile."""
    results, groups = _load(run)
    by_code = {g.code: g for g in groups}

    folders: dict[str, dict] = {}
    unmatched: list[dict] = []
    for result in sorted(results, key=lambda r: (r.code, r.position, r.filename)):
        card = {
            "index": result.index,
            "filename": result.filename,
            "position": result.position,
            "confidence": round(result.confidence, 2),
            "method": result.method,
            "reason": result.reason,
            "needs_review": result.needs_review,
            "output_name": image_file_name(result.code, result.position, result.filename) if result.code else "",
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
            "needs_review": False,
        })
        folder["photos"].append(card)
        folder["needs_review"] = folder["needs_review"] or result.needs_review

    covered = set(folders)
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
async def image_sort_photo(run_id: int, index: int, request: Request, db: DBSession = Depends(get_db)):
    """Serve one source photo so the review grid can show it."""
    run = _owned_run(db, request, run_id)
    if run is None:
        return Response(status_code=404)
    result = next((r for r in run.results if int(r.get("index") or 0) == index), None)
    if not result:
        return Response(status_code=404)

    path = Path(str(result.get("path") or ""))
    # Never serve outside this run's own working directory.
    work_dir = Path(run.work_dir or _run_dir(run.id)).resolve()
    try:
        if not str(path.resolve()).startswith(str(work_dir)) or not path.is_file():
            return Response(status_code=404)
    except OSError:
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


# ── Corrections ──────────────────────────────────────────────────────────────

def _save_results(db: DBSession, run: ImageSortRun, results: list[MatchResult], groups: list[ItemGroup]) -> None:
    assign_positions(results)
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

    # Re-number this folder by hand: the chosen photo first, the rest in their
    # current order behind it. assign_positions() would undo this, so the
    # positions are written directly.
    siblings = sorted(
        [r for r in results if r.code == target.code],
        key=lambda r: (r.index != index, r.position or 999, r.filename.lower()),
    )
    for position, result in enumerate(siblings, start=1):
        result.position = position

    summary = summarise(results, groups)
    run.results = [r.to_dict() for r in results]
    run.matched_count = summary["matched"]
    run.review_count = summary["review"]
    run.folder_count = summary["groups_covered"]
    db.commit()
    return JSONResponse({"ok": True, "result": _payload(run)})


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
    run = _owned_run(db, request, run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    from app.core.image_sources import cleanup_dir
    cleanup_dir(run.work_dir or _run_dir(run.id))
    db.delete(run)
    db.commit()
    with _progress_lock:
        _progress.pop(run_id, None)
    return JSONResponse({"ok": True})

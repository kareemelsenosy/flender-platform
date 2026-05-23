"""Upload & session management routes."""
from __future__ import annotations

import os
import pathlib
import shutil

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import UPLOAD_DIR
from app.core.parser import FileParser
from app.database import get_db
from app.services.file_safety import normalize_uploaded_name, unique_path
from app.templates_config import templates
from app.models import Session, UniqueItem, UploadedFile, User

router = APIRouter()

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


async def _stream_upload_to_disk(file: UploadFile, dest: pathlib.Path, max_size: int) -> int:
    """Stream-write an UploadFile to disk, aborting if it exceeds max_size.

    Returns the number of bytes written. Removes the partial file on overflow
    and raises ValueError so callers can return a 413.
    """
    written = 0
    chunk_size = 1024 * 1024  # 1 MB
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_size:
                out.close()
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError("upload_too_large")
            out.write(chunk)
    return written


def _owned_upload_path(uid: int, file_path: str) -> pathlib.Path | None:
    try:
        resolved = pathlib.Path(file_path).resolve()
        allowed_base = (UPLOAD_DIR / f"user_{uid}").resolve()
        resolved.relative_to(allowed_base)
        return resolved
    except Exception:
        return None


@router.get("/order-sheet", response_class=HTMLResponse)
async def dashboard(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    user = db.get(User, uid)
    sessions = (
        db.query(Session)
        .filter(Session.user_id == uid)
        .order_by(Session.created_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user, "sessions": sessions,
    })


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...),
                      db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    # Validate file extension
    display_name, safe_name = normalize_uploaded_name(file.filename or "upload")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return RedirectResponse("/order-sheet", status_code=302)

    # Save file to disk (stream-write with size cap so large uploads don't OOM)
    session_dir = UPLOAD_DIR / f"user_{uid}"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = unique_path(session_dir, safe_name)
    try:
        await _stream_upload_to_disk(file, file_path, MAX_UPLOAD_SIZE)
    except ValueError:
        return RedirectResponse("/order-sheet", status_code=302)

    # Create session
    ext = os.path.splitext(safe_name)[1].lower()
    source_type = "csv_upload" if ext == ".csv" else "excel_upload"

    sess = Session(
        user_id=uid,
        name=display_name,
        source_type=source_type,
        source_ref=display_name,
        status="mapping",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    # Save file record
    uf = UploadedFile(
        session_id=sess.id,
        filename=display_name,
        file_path=str(file_path),
        file_size=os.path.getsize(file_path),
    )
    db.add(uf)
    db.commit()

    return RedirectResponse(f"/mapping/{sess.id}", status_code=302)


@router.post("/upload/file")
async def upload_file_json(request: Request, file: UploadFile = File(...),
                           db: DBSession = Depends(get_db)):
    """Same as /upload but returns JSON {ok, session_id, name, mapping_url} for JS batch upload."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    display_name, safe_name = normalize_uploaded_name(file.filename or "upload")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return JSONResponse({"error": f"Unsupported file type: {ext}"}, status_code=400)

    session_dir = UPLOAD_DIR / f"user_{uid}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Avoid name collisions when uploading multiple files
    file_path = unique_path(session_dir, safe_name)

    try:
        await _stream_upload_to_disk(file, file_path, MAX_UPLOAD_SIZE)
    except ValueError:
        return JSONResponse({"error": "File too large (max 50MB)"}, status_code=413)

    source_type = "csv_upload" if ext == ".csv" else "excel_upload"
    sess = Session(
        user_id=uid,
        name=display_name,
        source_type=source_type,
        source_ref=display_name,
        status="mapping",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    uf = UploadedFile(
        session_id=sess.id,
        filename=display_name,
        file_path=str(file_path),
        file_size=os.path.getsize(file_path),
    )
    db.add(uf)
    db.commit()

    return JSONResponse({
        "ok": True,
        "session_id": sess.id,
        "name": display_name,
        "mapping_url": f"/mapping/{sess.id}",
    })


@router.post("/sessions/{session_id}/delete")
async def delete_session(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    is_ajax = "application/json" in request.headers.get("accept", "")
    if not uid:
        if is_ajax:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if sess:
        # Clean up uploaded Excel/CSV file
        if sess.uploaded_file:
            try:
                owned_path = _owned_upload_path(uid, sess.uploaded_file.file_path)
                if owned_path and owned_path.exists():
                    os.remove(owned_path)
            except OSError:
                pass
        # Clean up uploaded images folder (from local search)
        img_dir = UPLOAD_DIR / f"user_{uid}" / f"session_{session_id}_images"
        if img_dir.is_dir():
            try:
                shutil.rmtree(img_dir)
            except OSError:
                pass
        db.delete(sess)
        db.commit()
        if is_ajax:
            return JSONResponse({"ok": True})
    elif is_ajax:
        return JSONResponse({"ok": True})  # idempotent — already gone
    return RedirectResponse("/order-sheet", status_code=302)

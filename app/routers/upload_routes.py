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


def _owned_upload_path(uid: int, file_path: str) -> pathlib.Path | None:
    try:
        resolved = pathlib.Path(file_path).resolve()
        allowed_base = (UPLOAD_DIR / f"user_{uid}").resolve()
        resolved.relative_to(allowed_base)
        return resolved
    except Exception:
        return None


@router.get("/", response_class=HTMLResponse)
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
        return RedirectResponse("/", status_code=302)

    # Read with size limit
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return RedirectResponse("/", status_code=302)

    # Save file to disk
    session_dir = UPLOAD_DIR / f"user_{uid}"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = unique_path(session_dir, safe_name)
    with open(file_path, "wb") as f:
        f.write(content)

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

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": "File too large (max 50MB)"}, status_code=413)

    with open(file_path, "wb") as f:
        f.write(content)

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
    return RedirectResponse("/", status_code=302)

"""Upload & session management routes."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import UPLOAD_DIR
from app.core.parser import FileParser
from app.database import get_db
from app.main import templates
from app.models import Session, UniqueItem, UploadedFile, User

router = APIRouter()

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    user = db.query(User).get(uid)
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
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return RedirectResponse("/", status_code=302)

    # Read with size limit
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return RedirectResponse("/", status_code=302)

    # Save file to disk
    session_dir = UPLOAD_DIR / f"user_{uid}"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / filename
    with open(file_path, "wb") as f:
        f.write(content)

    # Create session
    ext = os.path.splitext(file.filename)[1].lower()
    source_type = "csv_upload" if ext == ".csv" else "excel_upload"

    sess = Session(
        user_id=uid,
        name=file.filename,
        source_type=source_type,
        source_ref=file.filename,
        status="mapping",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    # Save file record
    uf = UploadedFile(
        session_id=sess.id,
        filename=file.filename,
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

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return JSONResponse({"error": f"Unsupported file type: {ext}"}, status_code=400)

    session_dir = UPLOAD_DIR / f"user_{uid}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Avoid name collisions when uploading multiple files
    base, sfx = os.path.splitext(filename)
    file_path = session_dir / filename
    counter = 1
    while file_path.exists():
        file_path = session_dir / f"{base}_{counter}{sfx}"
        counter += 1

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": "File too large (max 50MB)"}, status_code=413)

    with open(file_path, "wb") as f:
        f.write(content)

    source_type = "csv_upload" if ext == ".csv" else "excel_upload"
    sess = Session(
        user_id=uid,
        name=file_path.name,
        source_type=source_type,
        source_ref=file_path.name,
        status="mapping",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    uf = UploadedFile(
        session_id=sess.id,
        filename=file_path.name,
        file_path=str(file_path),
        file_size=os.path.getsize(file_path),
    )
    db.add(uf)
    db.commit()

    return JSONResponse({
        "ok": True,
        "session_id": sess.id,
        "name": file_path.name,
        "mapping_url": f"/mapping/{sess.id}",
    })


@router.post("/sessions/{session_id}/delete")
async def delete_session(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if sess:
        # Clean up uploaded file
        if sess.uploaded_file:
            try:
                os.remove(sess.uploaded_file.file_path)
            except OSError:
                pass
        db.delete(sess)
        db.commit()
    return RedirectResponse("/", status_code=302)

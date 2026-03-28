"""Generate & download routes."""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import OUTPUT_DIR
from app.core.generator import OrderSheetGenerator
from app.database import get_db
from app.main import templates
from app.models import GeneratedFile, Session, UniqueItem

router = APIRouter()

# In-memory progress tracking per session — protected by a lock (M4)
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


@router.get("/generate/{session_id}/progress")
async def generate_progress(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Poll endpoint for real-time generation progress. Requires auth + session ownership. (C1)"""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)
    with _progress_lock:
        return JSONResponse(_progress.get(session_id, {"stage": "waiting", "downloaded": 0, "total": 0}))


@router.get("/generate/{session_id}", response_class=HTMLResponse)
async def generate_page(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return RedirectResponse("/", status_code=302)

    # Count approved items
    approved_count = db.query(UniqueItem).filter(
        UniqueItem.session_id == session_id,
        UniqueItem.review_status == "approved",
    ).count()

    return templates.TemplateResponse(request, "generate.html", {
        "session": sess,
        "approved_count": approved_count,
    })


@router.post("/generate/{session_id}")
async def generate_excel(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Get approved items
    items = db.query(UniqueItem).filter(
        UniqueItem.session_id == session_id,
        UniqueItem.review_status == "approved",
    ).all()

    if not items:
        return JSONResponse({"error": "No approved items"}, status_code=400)

    # Check if save_images requested
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    save_images = body.get("save_images", False)

    # Build item dicts for generator — include all fields for 23-column format
    item_dicts = []
    for item in items:
        item_dicts.append({
            "item_code": item.item_code,
            "style_name": item.style_name,
            "color_name": item.color_name,
            "color_code": item.color_code,
            "gender": item.gender,
            "wholesale_price": item.wholesale_price,
            "retail_price": item.retail_price,
            "qty_available": item.qty_available,
            "sizes": item.sizes,
            "approved_url": item.approved_url,
            "brand": item.brand,
            "barcode": "",  # From parsed data if available
            "item_group": "",  # From parsed data if available
        })

    # Determine brand from first item
    brand = items[0].brand if items else ""

    config = {
        "save_images_to_folder": save_images,
        "image_size": sess.config.get("image_size", [150, 150]),
        "row_height_px": sess.config.get("row_height_px", 100),
    }

    # Progress callback updates shared dict for polling — lock prevents dirty reads (M4)
    with _progress_lock:
        _progress[session_id] = {"stage": "starting", "downloaded": 0, "total": 0}

    def on_progress(downloaded: int, total: int, stage: str):
        with _progress_lock:
            _progress[session_id] = {"stage": stage, "downloaded": downloaded, "total": total}

    generator = OrderSheetGenerator(config, progress_callback=on_progress)
    out_dir = str(OUTPUT_DIR / f"session_{session_id}")

    # Run in thread so progress polling works during generation
    loop = asyncio.get_event_loop()
    out_path = await loop.run_in_executor(
        None,
        lambda: generator.generate(
            items=item_dicts,
            output_dir=out_dir,
            input_filename=sess.name,
            brand=brand,
        ),
    )

    # Create download token for Excel
    token = uuid.uuid4().hex
    images_folder = os.path.join(out_dir, "images") if save_images else None
    gen_file = GeneratedFile(
        session_id=session_id,
        token=token,
        file_path=out_path,
        filename=os.path.basename(out_path),
        image_folder_path=images_folder,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db.add(gen_file)
    sess.status = "completed"
    db.commit()

    result = {
        "ok": True,
        "filename": gen_file.filename,
        "download_url": f"/download/{token}",
    }

    # If images were saved, create ZIP and add download link
    if save_images and images_folder and os.path.isdir(images_folder):
        zip_path = os.path.join(out_dir, "images.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(images_folder):
                    for f in files:
                        full = os.path.join(root, f)
                        arc = os.path.relpath(full, images_folder)
                        zf.write(full, arc)
            zip_token = uuid.uuid4().hex
            zip_gen = GeneratedFile(
                session_id=session_id,
                token=zip_token,
                file_path=zip_path,
                filename="images.zip",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            )
            db.add(zip_gen)
            db.commit()
            result["images_zip_url"] = f"/download-zip/{zip_token}"
        except Exception:
            pass  # ZIP creation failed, skip

    # Clean up progress tracking
    with _progress_lock:
        _progress.pop(session_id, None)

    return JSONResponse(result)


def cleanup_expired_files(db_session) -> int:
    """Delete expired GeneratedFile records and their files from disk. Returns count deleted. (L4)"""
    now = datetime.now(timezone.utc)
    expired = db_session.query(GeneratedFile).all()
    deleted = 0
    for rec in expired:
        rec_expires = rec.expires_at
        if rec_expires.tzinfo is None:
            from datetime import timezone as _tz
            rec_expires = rec_expires.replace(tzinfo=_tz.utc)
        if rec_expires < now:
            try:
                if os.path.exists(rec.file_path):
                    os.remove(rec.file_path)
            except OSError:
                pass
            db_session.delete(rec)
            deleted += 1
    if deleted:
        db_session.commit()
    return deleted


@router.get("/download/{token}")
async def download_file(token: str, db: DBSession = Depends(get_db)):
    gen = db.query(GeneratedFile).filter(GeneratedFile.token == token).first()
    if not gen or not os.path.exists(gen.file_path):
        return JSONResponse({"error": "File not found or expired"}, status_code=404)

    if gen.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return JSONResponse({"error": "Download link expired"}, status_code=410)

    return FileResponse(
        gen.file_path,
        filename=gen.filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/download-zip/{token}")
async def download_zip(token: str, db: DBSession = Depends(get_db)):
    gen = db.query(GeneratedFile).filter(GeneratedFile.token == token).first()
    if not gen or not os.path.exists(gen.file_path):
        return JSONResponse({"error": "File not found or expired"}, status_code=404)

    if gen.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return JSONResponse({"error": "Download link expired"}, status_code=410)

    return FileResponse(
        gen.file_path,
        filename=gen.filename,
        media_type="application/zip",
    )

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
from app.templates_config import templates
from app.models import GeneratedFile, Session, UniqueItem

router = APIRouter()

# In-memory progress tracking per session — protected by a lock (M4)
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()

# Recently completed exports — {user_id: [{session_id, name, download_url, ...}, ...]}
# Kept for 5 minutes so the sidebar can show download buttons
_completed_exports: dict[int, list] = {}


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

    # Check if export is currently running
    with _progress_lock:
        is_exporting = session_id in _progress

    # Check if export recently completed
    completed_entry = None
    for entry in _completed_exports.get(uid, []):
        if entry.get("session_id") == session_id:
            completed_entry = entry
            break

    # Also check DB for generated files (covers case where server restarted)
    if not completed_entry and not is_exporting:
        gen_file = db.query(GeneratedFile).filter(
            GeneratedFile.session_id == session_id,
            GeneratedFile.expires_at > datetime.now(timezone.utc),
        ).order_by(GeneratedFile.expires_at.desc()).first()
        if gen_file:
            completed_entry = {
                "download_url": f"/download/{gen_file.token}",
                "images_zip_url": "",
            }
            # Check for images zip too
            zip_file = db.query(GeneratedFile).filter(
                GeneratedFile.session_id == session_id,
                GeneratedFile.filename == "images.zip",
                GeneratedFile.expires_at > datetime.now(timezone.utc),
            ).first()
            if zip_file:
                completed_entry["images_zip_url"] = f"/download-zip/{zip_file.token}"

    return templates.TemplateResponse(request, "generate.html", {
        "session": sess,
        "approved_count": approved_count,
        "is_exporting": is_exporting,
        "completed": completed_entry,
    })


def _run_export_background(session_id: int, user_id: int, item_dicts: list,
                           sess_name: str, sess_config: dict,
                           brand: str, save_images: bool,
                           currency: str = ""):
    """Run export in background thread so user can navigate away."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        def on_progress(downloaded: int, total: int, stage: str):
            with _progress_lock:
                _progress[session_id] = {"stage": stage, "downloaded": downloaded, "total": total}

        config = {
            "save_images_to_folder": save_images,
            "image_size": sess_config.get("image_size", [150, 150]),
            "row_height_px": sess_config.get("row_height_px", 100),
        }
        generator = OrderSheetGenerator(config, progress_callback=on_progress)
        out_dir = str(OUTPUT_DIR / f"session_{session_id}")

        out_path = generator.generate(
            items=item_dicts,
            output_dir=out_dir,
            input_filename=sess_name,
            brand=brand,
            currency=currency,
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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        db.add(gen_file)

        sess = db.query(Session).get(session_id)
        if sess:
            sess.status = "completed"
        db.commit()

        result = {
            "download_url": f"/download/{token}",
            "images_zip_url": "",
        }

        # If images were saved, create ZIP
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
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                )
                db.add(zip_gen)
                db.commit()
                result["images_zip_url"] = f"/download-zip/{zip_token}"
            except Exception:
                pass

        # Move from active to completed
        with _progress_lock:
            _progress.pop(session_id, None)

        entry = {
            "session_id": session_id,
            "name": sess_name,
            "download_url": result["download_url"],
            "images_zip_url": result["images_zip_url"],
            "completed_at": datetime.now(timezone.utc).timestamp(),
        }
        _completed_exports.setdefault(user_id, []).append(entry)

        # Clean up old entries (> 5 min)
        now = datetime.now(timezone.utc).timestamp()
        _completed_exports[user_id] = [e for e in _completed_exports[user_id] if now - e["completed_at"] < 300]

        # Notify user
        from app.services.notifications import add_notification
        add_notification(
            user_id, "export_done",
            "Export Complete",
            f"Your export is ready — {sess_name}",
            session_id,
            actions=[{"label": "Download", "url": result["download_url"]}],
        )

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(f"Export failed for session {session_id}: {exc}")
        with _progress_lock:
            _progress[session_id] = {"stage": f"Error: {exc}", "downloaded": 0, "total": 0}
    finally:
        db.close()


@router.post("/generate/{session_id}")
async def generate_excel(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Already exporting?
    with _progress_lock:
        if session_id in _progress:
            return JSONResponse({"ok": True, "started": True, "message": "Export already in progress"})

    # Get approved items — ordered by id to preserve original Google Sheet row order
    items = db.query(UniqueItem).filter(
        UniqueItem.session_id == session_id,
        UniqueItem.review_status == "approved",
    ).order_by(UniqueItem.id).all()

    if not items:
        return JSONResponse({"error": "No approved items"}, status_code=400)

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    save_images = body.get("save_images", False)

    # Build item dicts — expand each item into one row per size
    item_dicts = []
    for item in items:
        sizes = item.sizes or []
        if not sizes:
            sizes = [""]  # one row even with no size data
        for size in sizes:
            item_dicts.append({
                "item_code": item.item_code,
                "style_name": item.style_name,
                "color_name": item.color_name,
                "color_code": item.color_code,
                "gender": item.gender,
                "wholesale_price": item.wholesale_price,
                "retail_price": item.retail_price,
                "qty_available": item.qty_available,
                "size": str(size),  # single size per row
                "approved_url": item.approved_url,
                "pictures_url": item.pictures_url or "",
                "additional_urls": item.additional_urls,
                "brand": item.brand,
                "barcode": item.barcode or "",
                "item_group": item.item_group or "",
            })

    brand = items[0].brand if items else ""
    currency = sess.config.get("currency", "")

    # Initialize progress and start background thread
    with _progress_lock:
        _progress[session_id] = {"stage": "starting", "downloaded": 0, "total": len(item_dicts)}

    threading.Thread(
        target=_run_export_background,
        args=(session_id, uid, item_dicts, sess.name, sess.config, brand, save_images, currency),
        daemon=True,
    ).start()

    return JSONResponse({"ok": True, "started": True, "message": "Export started in background"})


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

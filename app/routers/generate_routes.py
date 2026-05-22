"""Generate & download routes."""
from __future__ import annotations

import asyncio
import os
import secrets
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
from app.core.searcher import item_sort_key
from app.database import get_db
from app.templates_config import templates
from app.models import GeneratedFile, Session, UniqueItem
from app.services.review_defaults import materialize_default_review_approvals
from app.services.sap_code_backfill import backfill_sap_codes_for_session

router = APIRouter()

# In-memory progress tracking per session — protected by a lock (M4)
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()

# Recently completed exports — {user_id: [{session_id, name, download_url, ...}, ...]}
# Kept for 5 minutes so the sidebar can show download buttons
_completed_exports: dict[int, list] = {}
_completed_lock = threading.Lock()


def _materialize_google_sheet_conversion_approvals(db: DBSession, sess: Session) -> None:
    """Older Google Sheets convert-only imports may still have pending rows.
    Promote them to approved so export behaves like a pure conversion flow.
    """
    if sess.source_type != "google_sheets":
        return
    if sess.config.get("search_missing", True):
        return

    pending_items = db.query(UniqueItem).filter(
        UniqueItem.session_id == sess.id,
        UniqueItem.review_status != "approved",
    ).all()
    if not pending_items:
        return

    for item in pending_items:
        item.review_status = "approved"
        item.search_status = "done"
    db.commit()


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
        return RedirectResponse("/order-sheet", status_code=302)

    materialize_default_review_approvals(db, session_id)
    _materialize_google_sheet_conversion_approvals(db, sess)
    backfill_sap_codes_for_session(db, sess, uid)

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
    with _completed_lock:
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
                           currency: str = "",
                           google_sheet_tabs: list[str] | None = None):
    """Run export in background thread so user can navigate away."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        def on_progress(downloaded: int, total: int, stage: str):
            with _progress_lock:
                existing = _progress.get(session_id, {})
                existing.update({"stage": stage, "downloaded": downloaded, "total": total})
                _progress[session_id] = existing

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
            google_sheet_tabs=google_sheet_tabs,
        )

        # Create download token for Excel
        token = secrets.token_urlsafe(32)
        images_folder = os.path.join(out_dir, "images") if save_images else None
        gen_file = GeneratedFile(
            session_id=session_id,
            token=token,
            file_path=out_path,
            filename=os.path.basename(out_path),
            image_folder_path=images_folder,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db.add(gen_file)

        sess = db.get(Session, session_id)
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
                zip_token = secrets.token_urlsafe(32)
                zip_gen = GeneratedFile(
                    session_id=session_id,
                    token=zip_token,
                    file_path=zip_path,
                    filename="images.zip",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
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
        with _completed_lock:
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
        # Notify user of failure
        try:
            from app.services.notifications import add_notification
            add_notification(
                user_id, "export_error",
                "Export Failed",
                f"Export error: {str(exc)[:100]}",
                session_id,
            )
        except Exception:
            pass
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

    materialize_default_review_approvals(db, session_id)
    _materialize_google_sheet_conversion_approvals(db, sess)
    backfill_sap_codes_for_session(db, sess, uid)

    # Already exporting?
    with _progress_lock:
        if session_id in _progress:
            return JSONResponse({"ok": True, "started": True, "message": "Export already in progress"})

    # Approved items — grouped so similar styles are adjacent (brand -> style -> base code -> color)
    items = db.query(UniqueItem).filter(
        UniqueItem.session_id == session_id,
        UniqueItem.review_status == "approved",
    ).all()
    items = sorted(
        items,
        key=lambda it: item_sort_key(
            brand=it.brand,
            style_name=it.style_name,
            item_code=it.item_code,
            item_group=it.item_group,
            color_name=it.color_name,
            color_code=it.color_code,
        ),
    )

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
                "item_code": item.item_code or "",
                "style_name": item.style_name or "",
                "color_name": item.color_name or "",
                "color_code": item.color_code or "",
                "gender": item.gender or "",
                "wholesale_price": item.wholesale_price,
                "retail_price": item.retail_price,
                "qty_available": item.qty_available,
                "size": str(size or ""),  # single size per row
                "approved_url": item.approved_url or "",
                "pictures_url": item.pictures_url or "",
                "additional_urls": item.additional_urls,
                "brand": item.brand or "",
                "barcode": item.barcode or "",
                "item_group": item.item_group or "",
                "item_group_code": item.item_group_code or "",
                "sap_code": item.sap_code or "",
                "source_sheet": item.source_sheet or "",
                "comming_soon_qty": item.comming_soon_qty if item.comming_soon_qty is not None else "",
            })

    # Final export order should mirror the cleaner sample sheets:
    # brand -> style -> base item code -> color -> numeric size.
    item_dicts = sorted(
        item_dicts,
        key=lambda it: item_sort_key(
            brand=it.get("brand"),
            style_name=it.get("style_name"),
            item_code=it.get("item_code"),
            item_group=it.get("item_group"),
            color_name=it.get("color_name"),
            color_code=it.get("color_code"),
            size=it.get("size"),
        ),
    )

    brand = (items[0].brand or "") if items else ""
    currency = sess.config.get("currency", "")
    google_sheet_tabs = sess.config.get("selected_sheet_tabs", []) if sess.source_type == "google_sheets" else []

    # Initialize progress and start background thread
    with _progress_lock:
        import time as _time_mod
        _progress[session_id] = {"stage": "starting", "downloaded": 0, "total": len(item_dicts), "started_at": _time_mod.time()}

    threading.Thread(
        target=_run_export_background,
        args=(session_id, uid, item_dicts, sess.name, sess.config, brand, save_images, currency, google_sheet_tabs),
        daemon=True,
    ).start()

    return JSONResponse({"ok": True, "started": True, "message": "Export started in background"})


def cleanup_expired_files(db_session) -> int:
    """Delete expired GeneratedFile records and their files from disk. Returns count deleted. (L4)"""
    now = datetime.now(timezone.utc)
    expired = db_session.query(GeneratedFile).filter(GeneratedFile.expires_at < now).all()
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


def _download_error_page(title: str, message: str, status_code: int,
                          session_id: int | None = None) -> HTMLResponse:
    """Return an HTML error page for failed downloads.

    Browsers initiate downloads via top-level navigation; if we return a JSON
    body the browser saves it to disk (with a .json extension, since the
    Content-Type is application/json) and the download manager reports it as
    a failed transfer ("Site wasn't available"). An HTML response renders in
    the tab instead, so the user sees a real error and a path forward.
    """
    re_export = ""
    if session_id is not None:
        re_export = (
            f'<a href="/generate/{session_id}" '
            f'style="display:inline-block;margin-top:16px;padding:10px 18px;'
            f'background:#000;color:#fff;border-radius:6px;text-decoration:none;'
            f'font-weight:600">Re-export this session</a>'
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:520px;margin:80px auto;padding:0 24px;color:#111">
  <h1 style="font-size:1.5rem;margin-bottom:12px">{title}</h1>
  <p style="color:#444;line-height:1.5">{message}</p>
  {re_export}
  <p style="margin-top:20px"><a href="/" style="color:#2563eb">← Back to dashboard</a></p>
</body></html>"""
    return HTMLResponse(html, status_code=status_code)


@router.get("/download/{token}")
async def download_file(token: str, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse(f"/login?next=/download/{token}", status_code=302)

    gen = db.query(GeneratedFile).filter(GeneratedFile.token == token).first()
    if not gen:
        return _download_error_page(
            "Download unavailable",
            "This download link is invalid or has been removed. Please re-export the session to get a fresh link.",
            404,
        )
    if not os.path.exists(gen.file_path):
        return _download_error_page(
            "Export file missing",
            "The exported file is no longer on the server (the storage may have been reset on redeploy). Please re-export to regenerate it.",
            404,
            session_id=gen.session_id,
        )

    # Verify ownership
    sess = db.query(Session).filter(Session.id == gen.session_id, Session.user_id == uid).first()
    if not sess:
        return _download_error_page(
            "Not allowed",
            "You don't have access to this download.",
            403,
        )

    if gen.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return _download_error_page(
            "Download link expired",
            "Download links are kept for 24 hours after the last download. Please re-export to get a fresh link.",
            410,
            session_id=gen.session_id,
        )

    # Sliding expiry: each successful download bumps the link forward 24h,
    # so users actively reviewing/correcting images don't lose access.
    gen.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()

    return FileResponse(
        gen.file_path,
        filename=gen.filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/download-zip/{token}")
async def download_zip(token: str, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse(f"/login?next=/download-zip/{token}", status_code=302)

    gen = db.query(GeneratedFile).filter(GeneratedFile.token == token).first()
    if not gen:
        return _download_error_page(
            "Download unavailable",
            "This image ZIP link is invalid or has been removed. Please re-export the session to get a fresh link.",
            404,
        )
    if not os.path.exists(gen.file_path):
        return _download_error_page(
            "Image ZIP missing",
            "The image ZIP is no longer on the server (the storage may have been reset on redeploy). Please re-export to regenerate it.",
            404,
            session_id=gen.session_id,
        )

    # Verify ownership
    sess = db.query(Session).filter(Session.id == gen.session_id, Session.user_id == uid).first()
    if not sess:
        return _download_error_page(
            "Not allowed",
            "You don't have access to this download.",
            403,
        )

    if gen.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return _download_error_page(
            "Download link expired",
            "Download links are kept for 24 hours after the last download. Please re-export to get a fresh link.",
            410,
            session_id=gen.session_id,
        )

    # Sliding expiry: each successful download bumps the link forward 24h.
    gen.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()

    return FileResponse(
        gen.file_path,
        filename=gen.filename,
        media_type="application/zip",
    )

"""Global API routes — notifications polling, active task status."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import get_current_user_id
from app.services.notifications import poll_notifications

router = APIRouter()


@router.get("/api/health")
async def health_check():
    return JSONResponse({"status": "ok"})


@router.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse("/", status_code=302)


@router.get("/api/notifications/poll")
async def notifications_poll(request: Request):
    """Return unseen notifications for the current user and mark them seen."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"notifications": []})
    notifs = poll_notifications(uid)
    return JSONResponse({"notifications": notifs})


@router.get("/api/active-tasks")
async def active_tasks(request: Request):
    """Return all background tasks (running + recently completed) for the sidebar."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"tasks": []})

    from app.routers.sheets_routes import _batch_progress, _user_batches
    from app.routers.search_routes import _search_progress
    from app.routers.generate_routes import _progress as _gen_progress, _progress_lock as _gen_lock, _completed_exports

    tasks = []

    # ── Sheet batch imports ──────────────────────────────────────────────────
    for batch_id in list(_user_batches.get(uid, [])):
        batch = _batch_progress.get(batch_id)
        if batch:
            done = batch.get("done", 0)
            total = batch.get("total", 0)
            running = batch.get("running", False)
            tasks.append({
                "type": "sheets",
                "batch_id": batch_id,
                "done": done,
                "total": total,
                "running": running,
                "status": "running" if running else ("done" if done >= total and total > 0 else "idle"),
                "url": "/sheets",
                "label": "Sheets Import",
            })

    # ── Image searches ───────────────────────────────────────────────────────
    def _query_searching():
        from app.database import SessionLocal
        from app.models import Session as SessionModel
        db = SessionLocal()
        try:
            return db.query(SessionModel).filter(
                SessionModel.user_id == uid,
                SessionModel.status.in_(["searching", "reviewing"]),
            ).order_by(SessionModel.updated_at.desc()).limit(5).all()
        finally:
            db.close()

    sessions = await asyncio.to_thread(_query_searching)
    for sess in sessions:
        prog = _search_progress.get(sess.id)
        if prog:
            running = prog.get("running", False)
            done = prog.get("done", 0)
            total = prog.get("total", 0)
            status = "running" if running else "done"
            tasks.append({
                "type": "search",
                "session_id": sess.id,
                "session_name": sess.name,
                "done": done,
                "total": total,
                "running": running,
                "status": status,
                "url": f"/search/{sess.id}" if running else f"/review/{sess.id}",
                "label": sess.name or f"Search #{sess.id}",
                "action_label": "View Progress" if running else "Review Images",
            })

    # ── Export / generate tasks ──────────────────────────────────────────────
    with _gen_lock:
        gen_snapshot = dict(_gen_progress)

    for sid, prog in gen_snapshot.items():
        # Verify this session belongs to the user
        def _check_owner(sid=sid):
            from app.database import SessionLocal
            from app.models import Session as SessionModel
            db = SessionLocal()
            try:
                s = db.query(SessionModel).filter(SessionModel.id == sid, SessionModel.user_id == uid).first()
                return s.name if s else None
            finally:
                db.close()

        name = await asyncio.to_thread(_check_owner)
        if name:
            downloaded = prog.get("downloaded", 0)
            total = prog.get("total", 0)
            tasks.append({
                "type": "export",
                "session_id": sid,
                "done": downloaded,
                "total": total,
                "running": True,
                "status": "running",
                "url": f"/generate/{sid}",
                "label": f"Export: {name}",
                "stage": prog.get("stage", ""),
            })

    # ── Recently completed exports (kept for 5 min) ──────────────────────────
    for entry in list(_completed_exports.get(uid, [])):
        tasks.append({
            "type": "export_done",
            "session_id": entry.get("session_id"),
            "done": 1,
            "total": 1,
            "running": False,
            "status": "done",
            "url": entry.get("download_url", ""),
            "label": f"Export: {entry.get('name', '')}",
            "action_label": "Download",
            "download_url": entry.get("download_url", ""),
            "images_zip_url": entry.get("images_zip_url", ""),
        })

    return JSONResponse({"tasks": tasks})

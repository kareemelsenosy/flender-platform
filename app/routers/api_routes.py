"""Global API routes — notifications polling, active task status."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import get_current_user_id
from app.services.notifications import poll_notifications

router = APIRouter()


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
    """Return currently running background tasks for the current user."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"tasks": []})

    # Import here to avoid circular imports at module load time
    from app.routers.sheets_routes import _batch_progress, _user_batches
    from app.routers.search_routes import _search_progress

    tasks = []

    # Active sheet batch imports (in-memory, no DB needed)
    for batch_id in list(_user_batches.get(uid, [])):
        batch = _batch_progress.get(batch_id)
        if batch and batch.get("running"):
            tasks.append({
                "type": "sheets",
                "batch_id": batch_id,
                "done": batch.get("done", 0),
                "total": batch.get("total", 0),
            })

    # Active image searches — run DB query in thread to avoid blocking the event loop
    def _query_searching():
        from app.database import SessionLocal
        from app.models import Session as SessionModel
        db = SessionLocal()
        try:
            return db.query(SessionModel).filter(
                SessionModel.user_id == uid,
                SessionModel.status == "searching",
            ).all()
        finally:
            db.close()

    searching = await asyncio.to_thread(_query_searching)
    for sess in searching:
        prog = _search_progress.get(sess.id)
        if prog and prog.get("running"):
            tasks.append({
                "type": "search",
                "session_id": sess.id,
                "session_name": sess.name,
                "done": prog.get("done", 0),
                "total": prog.get("total", 0),
            })

    return JSONResponse({"tasks": tasks})

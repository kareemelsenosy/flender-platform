"""Global API routes — notifications polling, active task status."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import INTERNAL_API_ENABLED
from app.core.searcher import ImageSearcher, split_and_normalize_domains
from app.database import get_db
from app.models import BrandSearchConfig, Session as SessionModel, UniqueItem
from app.services.ai_service import ai_assistant_chat, ai_available
from app.services.notifications import poll_notifications
from app.services.review_defaults import materialize_default_review_approvals

router = APIRouter()


def _trim_assistant_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()[:600]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, raw in list(value.items())[:24]:
            trimmed = _trim_assistant_value(raw, depth + 1)
            if trimmed is None:
                continue
            if isinstance(trimmed, (str, list, dict)) and not trimmed:
                continue
            out[str(key)[:80]] = trimmed
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for raw in list(value)[:12]:
            trimmed = _trim_assistant_value(raw, depth + 1)
            if trimmed is None:
                continue
            out.append(trimmed)
        return out
    return str(value)[:300]


def _build_assistant_session_context(db: DBSession, uid: int, session_id: int) -> dict[str, Any] | None:
    from sqlalchemy import or_

    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == uid,
    ).first()
    if not session:
        return None

    materialize_default_review_approvals(db, session.id)

    items_q = db.query(UniqueItem).filter(UniqueItem.session_id == session.id)
    brands = [
        brand for (brand,) in db.query(UniqueItem.brand).filter(
            UniqueItem.session_id == session.id,
            UniqueItem.brand.isnot(None),
            UniqueItem.brand != "",
        ).distinct().limit(12).all()
        if brand
    ]
    missing_samples = items_q.filter(
        or_(UniqueItem.approved_url == None, UniqueItem.approved_url == "")
    ).limit(5).all()
    user_configs = db.query(BrandSearchConfig).filter(BrandSearchConfig.user_id == uid).all()
    searcher = ImageSearcher({
        "brand_site_urls": {cfg.brand_name: cfg.site_urls for cfg in user_configs},
    })
    matched_brand_domains: dict[str, list[str]] = {}
    for brand in brands:
        domains: list[str] = []
        for _, urls in searcher.matching_brand_configs(brand):
            domains.extend(urls)
        if domains:
            matched_brand_domains[brand] = list(dict.fromkeys(domains))[:8]

    cfg = session.config
    return {
        "id": session.id,
        "name": session.name,
        "status": session.status,
        "source_type": session.source_type,
        "source_ref": str(session.source_ref or "")[:200],
        "total_items": session.total_items,
        "searched_items": session.searched_items,
        "brands": brands,
        "search_config": {
            "search_mode": cfg.get("search_mode", "web"),
            "local_folder": bool(cfg.get("local_folder")),
            "priority_domains": split_and_normalize_domains(cfg.get("extra_brand_urls", [])),
            "search_notes": str(cfg.get("search_notes", "") or "").strip()[:800],
        },
        "matched_brand_domains": matched_brand_domains,
        "counts": {
            "approved": items_q.filter(UniqueItem.review_status == "approved").count(),
            "missing_images": items_q.filter(
                or_(UniqueItem.approved_url == None, UniqueItem.approved_url == "")
            ).count(),
            "edited": items_q.filter(
                UniqueItem.review_status == "approved",
                UniqueItem.auto_selected == False,
            ).count(),
        },
        "sample_missing_items": [
            {
                "id": item.id,
                "item_code": item.item_code,
                "brand": item.brand,
                "style_name": item.style_name,
                "color_name": item.color_name,
                "item_group": item.item_group,
                "barcode": item.barcode,
            }
            for item in missing_samples
        ],
    }


def _build_assistant_item_context(
    db: DBSession,
    uid: int,
    session_id: int | None,
    item_id: int | None,
) -> dict[str, Any] | None:
    if not item_id:
        return None

    query = db.query(UniqueItem).join(SessionModel, UniqueItem.session_id == SessionModel.id).filter(
        UniqueItem.id == item_id,
        SessionModel.user_id == uid,
    )
    if session_id:
        query = query.filter(UniqueItem.session_id == session_id)
    item = query.first()
    if not item:
        return None

    label_base = str(item.style_name or item.item_group or item.item_code or "").strip()
    color_label = str(item.color_name or item.color_code or "").strip()
    group_count = db.query(UniqueItem).filter(
        UniqueItem.session_id == item.session_id,
        UniqueItem.brand == item.brand,
        UniqueItem.style_name == item.style_name,
        UniqueItem.item_group == item.item_group,
        UniqueItem.color_name == item.color_name,
        UniqueItem.color_code == item.color_code,
    ).count()
    return {
        "id": item.id,
        "session_id": item.session_id,
        "item_code": item.item_code,
        "brand": item.brand,
        "style_name": item.style_name,
        "color_name": item.color_name,
        "color_code": item.color_code,
        "item_group": item.item_group,
        "barcode": item.barcode,
        "qty_available": item.qty_available,
        "approved_url": item.approved_url,
        "candidate_count": len(item.candidates),
        "top_candidates": item.candidates[:5],
        "top_scores": [
            {"url": url, "score": item.scores.get(url)}
            for url in item.candidates[:5]
        ],
        "group_label": " · ".join(part for part in [label_base, color_label] if part),
        "group_count": group_count,
        "auto_selected": bool(item.auto_selected),
        "review_status": item.review_status,
    }


@router.get("/api/health")
async def health_check(db: DBSession = Depends(get_db)):
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "database": "connected"})
    except Exception as e:
        return JSONResponse({"status": "error", "database": str(e)}, status_code=503)


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
async def active_tasks(request: Request, db: DBSession = Depends(get_db)):
    """Return all background tasks (running + recently completed) for the sidebar.

    Uses the request-scoped DB session from FastAPI's Depends(get_db) instead of
    creating new SessionLocal() instances per query — avoids connection pool pressure.
    """
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"tasks": []})

    from app.routers.sheets_routes import _batch_progress, _user_batches
    from app.routers.search_routes import _search_progress
    from app.routers.generate_routes import _progress as _gen_progress, _progress_lock as _gen_lock, _completed_exports
    from app.models import Session as SessionModel

    tasks = []

    # ── Sheet batch imports ──────────────────────────────────────────────────
    for batch_id in list(_user_batches.get(uid, [])):
        batch = _batch_progress.get(batch_id)
        if batch:
            done = batch.get("done", 0)
            total = batch.get("total", 0)
            running = batch.get("running", False)
            status = "running" if running else ("done" if done >= total and total > 0 else "idle")
            # Skip idle/stalled batches — they haven't started or failed to launch
            if status == "idle":
                continue
            tasks.append({
                "type": "sheets",
                "batch_id": batch_id,
                "done": done,
                "total": total,
                "running": running,
                "status": status,
                "url": "/sheets",
                "label": "Sheets Import",
            })

    # ── Image searches — use request-scoped DB session ────────────────────────
    sessions = db.query(SessionModel).filter(
        SessionModel.user_id == uid,
        SessionModel.status.in_(["searching", "reviewing"]),
    ).order_by(SessionModel.updated_at.desc()).limit(5).all()

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

    # ── Export / generate tasks — use in-memory data + single DB query ────────
    with _gen_lock:
        gen_snapshot = dict(_gen_progress)

    if gen_snapshot:
        # Single query to verify ownership of all exporting sessions
        gen_sids = list(gen_snapshot.keys())
        owned_sessions = {
            s.id: s.name
            for s in db.query(SessionModel.id, SessionModel.name).filter(
                SessionModel.id.in_(gen_sids),
                SessionModel.user_id == uid,
            ).all()
        }
        for sid, prog in gen_snapshot.items():
            name = owned_sessions.get(sid)
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


@router.post("/api/ai-assistant/chat")
async def ai_assistant_chat_api(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        data = {}

    message = str(data.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    try:
        session_id = int(data.get("session_id")) if data.get("session_id") is not None else None
    except Exception:
        session_id = None
    try:
        item_id = int(data.get("item_id")) if data.get("item_id") is not None else None
    except Exception:
        item_id = None

    page_context = _trim_assistant_value(data.get("page_context") or {}) or {}
    page_path = str(data.get("page_path") or "").strip()[:200]

    context: dict[str, Any] = {
        "page_path": page_path,
        "page_context": page_context,
        "assistant_capabilities": {
            "configured": ai_available(),
            "can_apply_step3_suggestions": bool((page_context or {}).get("can_apply_step3_suggestions")),
            "can_review_by_group": bool((page_context or {}).get("group_mode_available")),
        },
    }

    session_context = _build_assistant_session_context(db, uid, session_id) if session_id else None
    if session_context:
        context["session"] = session_context

    item_context = _build_assistant_item_context(db, uid, session_id, item_id)
    if item_context:
        context["item"] = item_context

    result = ai_assistant_chat(message, context)
    return JSONResponse({
        "ok": True,
        "ai_available": ai_available(),
        "reply": result.get("reply", ""),
        "suggestions": result.get("suggestions", []),
        "search_instructions": result.get("search_instructions", ""),
        "priority_domains": result.get("priority_domains", []),
    })


@router.get("/api/search-test")
async def search_test(request: Request):
    """Test if web search sources are reachable from the server."""
    if not INTERNAL_API_ENABLED:
        return JSONResponse({"error": "not found"}, status_code=404)
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import requests as _req
    results = {}
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    test_query = "Carhartt WIP I027773 Jaden Keyholder"
    for name, url in [
        ("bing_images", f"https://www.bing.com/images/search?q={_req.utils.quote(test_query)}&form=HDRSC2"),
        ("google_images", f"https://www.google.com/search?q={_req.utils.quote(test_query)}&tbm=isch"),
        ("duckduckgo", f"https://duckduckgo.com/?q={_req.utils.quote(test_query)}&iax=images&ia=images"),
    ]:
        try:
            r = _req.get(url, headers={"User-Agent": ua}, timeout=10, allow_redirects=True)
            import re
            murl_count = len(re.findall(r'"murl"', r.text))
            ou_count = len(re.findall(r'"ou"', r.text))
            results[name] = {"status": r.status_code, "len": len(r.text), "murl": murl_count, "ou": ou_count}
        except Exception as e:
            results[name] = {"error": str(e)[:100]}
    return JSONResponse(results)


@router.post("/api/fix-pending-items")
async def fix_pending_items(request: Request, db: DBSession = Depends(get_db)):
    """Fix items incorrectly left as pending after search — restores them to approved."""
    if not INTERNAL_API_ENABLED:
        return JSONResponse({"error": "not found"}, status_code=404)
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    user_session_ids = [sid for (sid,) in db.query(SessionModel.id).filter(SessionModel.user_id == uid).all()]
    fixed = sum(materialize_default_review_approvals(db, session_id) for session_id in user_session_ids)
    return JSONResponse({"fixed": fixed})

"""Review routes — image review SPA + API."""
from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Set

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id, get_current_user_id_db
from app.database import get_db
from app.main import templates
from app.models import Session, UniqueItem

router = APIRouter()

_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


@router.get("/review/{session_id}", response_class=HTMLResponse)
async def review_page(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse(request, "review.html", {
        "session": sess,
    })


@router.get("/review/{session_id}/state")
async def review_state(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id_db(request, db)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    items = db.query(UniqueItem).filter(UniqueItem.session_id == session_id).all()
    state = {}
    for item in items:
        key = f"{item.item_code}__{item.color_code or ''}"
        state[key] = {
            "id": item.id,
            "item": {
                "item_code": item.item_code,
                "color_code": item.color_code,
                "brand": item.brand,
                "style_name": item.style_name,
                "color_name": item.color_name,
                "wholesale_price": item.wholesale_price,
                "retail_price": item.retail_price,
                "gender": item.gender,
                "sizes": item.sizes,
                "qty_available": item.qty_available,
            },
            "candidates": item.candidates,
            "scores": item.scores,
            "approved_url": item.approved_url,
            "status": item.review_status,
            "auto_selected": item.auto_selected,
        }

    return JSONResponse(state)


@router.post("/review/{session_id}/approve")
async def approve_item(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")
    url = data.get("url", "")

    item = db.query(UniqueItem).filter(
        UniqueItem.id == item_id, UniqueItem.session_id == session_id
    ).first()
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    item.approved_url = url
    item.review_status = "approved"
    item.auto_selected = False
    db.commit()

    return JSONResponse({"ok": True})


@router.post("/review/{session_id}/skip")
async def skip_item(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")

    item = db.query(UniqueItem).filter(
        UniqueItem.id == item_id, UniqueItem.session_id == session_id
    ).first()
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    item.review_status = "skipped"
    db.commit()

    return JSONResponse({"ok": True})


@router.post("/review/{session_id}/set-url")
async def set_custom_url(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")
    custom_url = data.get("url", "").strip()

    item = db.query(UniqueItem).filter(
        UniqueItem.id == item_id, UniqueItem.session_id == session_id
    ).first()
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Add to candidates if new
    candidates = item.candidates
    if custom_url and custom_url not in candidates:
        candidates.insert(0, custom_url)
        item.candidates = candidates

    item.approved_url = custom_url
    item.review_status = "approved"
    item.auto_selected = False
    db.commit()

    return JSONResponse({"ok": True})


@router.get("/review/{session_id}/download-images")
async def download_all_images(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Download all approved images as a ZIP file."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return RedirectResponse("/", status_code=302)

    items = db.query(UniqueItem).filter(
        UniqueItem.session_id == session_id,
        UniqueItem.review_status == "approved",
    ).all()

    # Download images concurrently for speed
    from concurrent.futures import ThreadPoolExecutor

    def _download_one(item):
        url = item.approved_url
        if not url or not url.startswith("http"):
            return None
        try:
            resp = requests.get(url, headers=_DL_HEADERS, timeout=15)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "").lower()
                # L5: Detect actual format from Content-Type, not just jpg fallback
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                elif "gif" in ct:
                    ext = ".gif"
                elif "svg" in ct:
                    ext = ".svg"
                else:
                    ext = ".jpg"
                safe_code = item.item_code.replace("/", "_").replace("\\", "_")
                color = (item.color_code or "").replace("/", "_").replace("\\", "_")
                fname = f"{safe_code}_{color}{ext}" if color else f"{safe_code}{ext}"
                return (fname, resp.content)
        except Exception:
            pass
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # C5: Track filenames to avoid collisions — append counter suffix when needed
        used_names: set[str] = set()
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(_download_one, items):
                if result:
                    fname, content = result
                    # Deduplicate filename
                    if fname in used_names:
                        base, ext = os.path.splitext(fname)
                        counter = 1
                        while f"{base}_{counter}{ext}" in used_names:
                            counter += 1
                        fname = f"{base}_{counter}{ext}"
                    used_names.add(fname)
                    zf.writestr(fname, content)

    buf.seek(0)
    safe_name = sess.name.replace(" ", "_")[:50]
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_images.zip"'},
    )

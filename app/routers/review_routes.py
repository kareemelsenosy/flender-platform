"""Review routes — image review SPA + API."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import urllib.parse
import zipfile
from typing import Set

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id, get_current_user_id_db
from app.config import GOOGLE_SEARCH_KEY, GOOGLE_CSE_ID
from app.core.searcher import ImageSearcher
from app.database import get_db
from app.templates_config import templates
from app.models import BrandSearchConfig, Session, UniqueItem

router = APIRouter()
logger = logging.getLogger(__name__)

_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Simple in-memory image cache: url_hash -> (content_bytes, content_type, timestamp)
_image_cache: dict[str, tuple[bytes, str, float]] = {}
_CACHE_MAX_AGE = 600  # 10 minutes
_CACHE_MAX_ITEMS = 500


@router.get("/api/image/proxy")
async def image_proxy(request: Request, url: str = ""):
    """Fetch an external image and serve it, bypassing CORS restrictions."""
    uid = get_current_user_id(request)
    if not uid:
        return Response(status_code=401)

    url = url.strip()
    if not url or not url.startswith("http"):
        return Response(status_code=400)

    # Convert dropbox sharing URLs to direct download
    if "dropbox.com" in url or "dropboxusercontent.com" in url:
        import re
        if "dropboxusercontent.com/scl/fi/" in url:
            url = re.sub(r"[?&]dl=\d", "", url)
            sep = "&" if "?" in url else "?"
            url = url + sep + "dl=1"
        elif "www.dropbox.com" in url:
            url = url.replace("www.dropbox.com", "dl.dropbox.com")
            url = re.sub(r"[?&]dl=\d", "", url)
            sep = "&" if "?" in url else "?"
            url = url + sep + "dl=1"

    # Check cache
    url_hash = hashlib.md5(url.encode()).hexdigest()
    now = time.time()
    if url_hash in _image_cache:
        data, ct, ts = _image_cache[url_hash]
        if now - ts < _CACHE_MAX_AGE:
            return Response(content=data, media_type=ct,
                            headers={"Cache-Control": "public, max-age=300"})

    # Fetch from origin
    try:
        resp = requests.get(url, headers=_DL_HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return Response(status_code=resp.status_code)

        ct = resp.headers.get("content-type", "image/jpeg")
        data = resp.content

        # Only cache if it looks like an image and is < 5MB
        if len(data) < 5_000_000:
            # Evict old entries if cache is full
            if len(_image_cache) >= _CACHE_MAX_ITEMS:
                oldest = sorted(_image_cache, key=lambda k: _image_cache[k][2])
                for k in oldest[:100]:
                    _image_cache.pop(k, None)
            _image_cache[url_hash] = (data, ct, now)

        return Response(content=data, media_type=ct,
                        headers={"Cache-Control": "public, max-age=300"})
    except Exception as e:
        logger.warning(f"Image proxy failed for {url[:80]}: {e}")
        return Response(status_code=502)


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
            "additional_urls": item.additional_urls,
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


@router.post("/review/{session_id}/set-additional")
async def set_additional_urls(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Set additional image URLs (for multi-image selection, up to 3)."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")
    urls = data.get("urls", [])

    item = db.query(UniqueItem).filter(
        UniqueItem.id == item_id, UniqueItem.session_id == session_id
    ).first()
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    item.additional_urls = urls[:3]
    db.commit()

    return JSONResponse({"ok": True})


@router.post("/review/{session_id}/re-search")
async def re_search_item(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Re-run image search for a single item with optional custom instructions."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")
    instructions = data.get("instructions", "").strip()

    item = db.query(UniqueItem).filter(
        UniqueItem.id == item_id, UniqueItem.session_id == session_id
    ).first()
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Load brand search config for this user
    brand_site_urls = {}
    user_brands = db.query(BrandSearchConfig).filter(
        BrandSearchConfig.user_id == uid
    ).all()
    for bc in user_brands:
        brand_site_urls[bc.brand_name.lower()] = bc.site_urls

    search_config = {
        "brand_site_urls": brand_site_urls,
        "google_api_key": GOOGLE_SEARCH_KEY,
        "google_cse_id": GOOGLE_CSE_ID,
    }
    searcher = ImageSearcher(search_config)

    item_dict = {
        "item_code": item.item_code,
        "color_code": item.color_code,
        "color_name": item.color_name,
        "style_name": item.style_name,
        "brand": item.brand,
    }

    # Build AI queries using instructions if provided
    ai_queries = []
    from app.services.ai_service import ai_available, ai_build_search_queries, ai_optimize_search_query, ai_rank_urls
    if ai_available():
        if instructions:
            ai_queries = ai_build_search_queries(item_dict, item.brand or "", instructions)
        else:
            ai_queries = ai_optimize_search_query(item_dict, item.brand or "")

    candidates, scores = searcher.search(item_dict, ai_queries=ai_queries or None)

    # AI re-rank
    if ai_available() and candidates:
        web_urls = [u for u in candidates if not u.startswith("file://")]
        if web_urls:
            ranked = ai_rank_urls(web_urls, item_dict, item.brand or "")
            new_scores = {}
            for i, url in enumerate(ranked):
                base = scores.get(url, 0.5)
                bonus = max(0.0, 0.1 - i * 0.02)
                new_scores[url] = min(round(base + bonus, 2), 1.0)
            candidates = ranked
            scores = new_scores

    # Update item in DB
    item.candidates = candidates
    item.scores = scores
    if candidates:
        best = max(candidates, key=lambda u: scores.get(u, 0))
        item.approved_url = best
        item.auto_selected = True
    item.review_status = "approved"
    db.commit()

    return JSONResponse({
        "ok": True,
        "candidates": candidates,
        "scores": scores,
        "approved_url": item.approved_url,
    })


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

    def _detect_ext(ct: str) -> str:
        ct = ct.lower()
        if "png" in ct: return ".png"
        if "webp" in ct: return ".webp"
        if "gif" in ct: return ".gif"
        if "svg" in ct: return ".svg"
        return ".jpg"

    def _download_one(url_info):
        url, safe_code, color, suffix = url_info
        if not url or not url.startswith("http"):
            return None
        try:
            resp = requests.get(url, headers=_DL_HEADERS, timeout=15)
            if resp.status_code == 200:
                ext = _detect_ext(resp.headers.get("content-type", ""))
                base = f"{safe_code}_{color}" if color else safe_code
                fname = f"{base}_{suffix}{ext}"
                return (fname, resp.content)
        except Exception:
            pass
        return None

    # Build download tasks: primary + additional URLs
    download_tasks = []
    for item in items:
        safe_code = item.item_code.replace("/", "_").replace("\\", "_")
        color = (item.color_code or "").replace("/", "_").replace("\\", "_")
        if item.approved_url:
            download_tasks.append((item.approved_url, safe_code, color, "1"))
        for i, extra_url in enumerate(item.additional_urls):
            download_tasks.append((extra_url, safe_code, color, str(i + 2)))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used_names: set[str] = set()
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(_download_one, download_tasks):
                if result:
                    fname, content = result
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

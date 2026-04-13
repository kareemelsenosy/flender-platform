"""Review routes — image review SPA + API."""
from __future__ import annotations

import asyncio
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id, get_current_user_id_db
from app.config import GOOGLE_SEARCH_KEY, GOOGLE_CSE_ID, UPLOAD_DIR
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


def _make_upstream_http() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=100,
        pool_maxsize=100,
        max_retries=0,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# Success cache: url_hash -> (content_bytes, content_type, timestamp)
_image_cache: dict[str, tuple[bytes, str, float]] = {}
_CACHE_MAX_AGE = 1800  # 30 minutes
_CACHE_MAX_ITEMS = 1000
# Failure cache: url_hash -> timestamp — skip re-fetching known-broken URLs for 10 min
_fail_cache: dict[str, float] = {}
_FAIL_CACHE_MAX_AGE = 600  # 10 minutes
_LOCAL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
_UPSTREAM_HTTP = _make_upstream_http()


def _normalize_image_url(url: str | None, uid: int | None = None, allow_empty: bool = False) -> str | None:
    text = str(url or "").strip()
    if not text:
        return "" if allow_empty else None
    if text.startswith("file://"):
        if uid is None:
            return None
        try:
            import pathlib
            resolved = pathlib.Path(text[7:]).resolve()
            allowed_base = (UPLOAD_DIR / f"user_{uid}").resolve()
            resolved.relative_to(allowed_base)
            if resolved.suffix.lower() not in _LOCAL_IMAGE_EXTENSIONS:
                return None
            return f"file://{resolved}"
        except Exception:
            return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _get_owned_item(db: DBSession, uid: int, session_id: int, item_id: int | None) -> UniqueItem | None:
    if not item_id:
        return None
    return db.query(UniqueItem).join(Session, UniqueItem.session_id == Session.id).filter(
        UniqueItem.id == item_id,
        UniqueItem.session_id == session_id,
        Session.user_id == uid,
    ).first()


def _fetch_remote_image(url: str) -> tuple[int, dict[str, str], bytes]:
    resp = _UPSTREAM_HTTP.get(url, headers=_DL_HEADERS, timeout=5, allow_redirects=True)
    return resp.status_code, dict(resp.headers), resp.content


@router.get("/api/image/local")
async def serve_local_image(request: Request, path: str = ""):
    """Serve an uploaded image from UPLOAD_DIR with auth + traversal protection."""
    import mimetypes
    import pathlib
    from app.config import UPLOAD_DIR

    uid = get_current_user_id(request)
    if not uid:
        return Response(status_code=401)

    if not path:
        return Response(status_code=400)

    try:
        resolved = pathlib.Path(path).resolve()
        upload_base = (UPLOAD_DIR / f"user_{uid}").resolve()
        resolved.relative_to(upload_base)  # raises ValueError if outside this user's uploads
    except ValueError:
        return Response(status_code=403)
    except Exception:
        return Response(status_code=400)

    if not resolved.is_file():
        return Response(status_code=404)
    if resolved.suffix.lower() not in _LOCAL_IMAGE_EXTENSIONS:
        return Response(status_code=403)

    mime_type, _ = mimetypes.guess_type(str(resolved))
    return FileResponse(str(resolved), media_type=mime_type or "image/jpeg",
                        headers={"Cache-Control": "public, max-age=600"})


@router.get("/api/image/proxy")
async def image_proxy(request: Request, url: str = ""):
    """Fetch an external image and serve it, bypassing CORS restrictions."""
    uid = get_current_user_id(request)
    if not uid:
        return Response(status_code=401)

    url = url.strip()
    if not url or not url.startswith("http"):
        return Response(status_code=400)

    # SSRF protection: block requests to private/internal IPs
    try:
        import socket
        from ipaddress import ip_address
        hostname = urllib.parse.urlparse(url).hostname
        if hostname:
            for info in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
                addr = info[4][0]
                ip = ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return Response(status_code=403)
    except Exception:
        pass  # DNS resolution failed — let httpx handle it

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

    # Check caches
    url_hash = hashlib.md5(url.encode()).hexdigest()
    now = time.time()
    if url_hash in _image_cache:
        data, ct, ts = _image_cache[url_hash]
        if now - ts < _CACHE_MAX_AGE:
            return Response(content=data, media_type=ct,
                            headers={"Cache-Control": "public, max-age=600"})
    # Fast-fail for known-broken URLs (avoids repeated 5s timeouts)
    if url_hash in _fail_cache and now - _fail_cache[url_hash] < _FAIL_CACHE_MAX_AGE:
        return Response(status_code=502)

    # Fetch from origin using a shared session so repeated thumbnail loads
    # reuse upstream connections instead of creating a fresh client each time.
    try:
        status_code, resp_headers, data = await asyncio.to_thread(_fetch_remote_image, url)

        if status_code != 200:
            _fail_cache[url_hash] = now
            return Response(status_code=status_code)

        # Detect actual content type from magic bytes (some CDNs lie about content-type)
        if data[:4] == b"\xff\xd8\xff\xe0" or data[:4] == b"\xff\xd8\xff\xe1":
            ct = "image/jpeg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ct = "image/png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ct = "image/webp"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            ct = "image/gif"
        else:
            ct = resp_headers.get("content-type", "image/jpeg")
            # If response is HTML (error page), don't serve it as an image
            if "text/html" in ct or data[:20].strip().startswith(b"<"):
                _fail_cache[url_hash] = now
                return Response(status_code=502)

        # Only cache if it looks like an image and is < 5MB
        if len(data) < 5_000_000:
            # Evict old entries if cache is full
            if len(_image_cache) >= _CACHE_MAX_ITEMS:
                oldest = sorted(_image_cache, key=lambda k: _image_cache[k][2])
                for k in oldest[:100]:
                    _image_cache.pop(k, None)
            _image_cache[url_hash] = (data, ct, now)

        return Response(content=data, media_type=ct,
                        headers={"Cache-Control": "public, max-age=600"})
    except Exception as e:
        logger.warning(f"Image proxy failed for {url[:80]}: {e}")
        _fail_cache[url_hash] = now
        return Response(status_code=502)


@router.get("/review/{session_id}", response_class=HTMLResponse)
def review_page(session_id: int, request: Request, db: DBSession = Depends(get_db)):
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
def review_state(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id_db(request, db)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    items = db.query(
        UniqueItem.id,
        UniqueItem.item_code,
        UniqueItem.color_code,
        UniqueItem.brand,
        UniqueItem.approved_url,
        UniqueItem.review_status,
        UniqueItem.auto_selected,
    ).filter(
        UniqueItem.session_id == session_id
    ).order_by(UniqueItem.id.asc()).all()
    state = {}
    for item in items:
        key = f"{item.item_code}__{item.color_code or ''}"
        state[key] = {
            "id": item.id,
            "item": {
                "item_code": item.item_code,
                "color_code": item.color_code,
                "brand": item.brand,
            },
            "approved_url": item.approved_url,
            "status": item.review_status,
            "auto_selected": item.auto_selected,
            "details_loaded": False,
        }

    return JSONResponse(state)


@router.get("/review/{session_id}/items/{item_id}")
def review_item_detail(session_id: int, item_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id_db(request, db)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    item = _get_owned_item(db, uid, session_id, item_id)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse({
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
        "details_loaded": True,
    })


@router.post("/review/{session_id}/approve")
async def approve_item(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    item_id = data.get("id")
    url = _normalize_image_url(data.get("url"), uid=uid, allow_empty=True)
    if url is None:
        return JSONResponse({"error": "invalid url"}, status_code=400)

    item = _get_owned_item(db, uid, session_id, item_id)
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

    item = _get_owned_item(db, uid, session_id, item_id)
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
    custom_url = _normalize_image_url(data.get("url"), uid=uid)
    if not custom_url:
        return JSONResponse({"error": "invalid url"}, status_code=400)

    item = _get_owned_item(db, uid, session_id, item_id)
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

    item = _get_owned_item(db, uid, session_id, item_id)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)

    clean_urls: list[str] = []
    for raw in urls[:3]:
        normalized = _normalize_image_url(raw, uid=uid)
        if normalized:
            clean_urls.append(normalized)
    item.additional_urls = clean_urls
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

    item = _get_owned_item(db, uid, session_id, item_id)
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
        "barcode": item.barcode,
        "item_group": item.item_group,
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
        if not url:
            return None

        base = f"{safe_code}_{color}" if color else safe_code

        # Local file uploaded to server — read directly from disk
        if url.startswith("file://"):
            path = url[7:]
            try:
                import mimetypes
                with open(path, "rb") as f:
                    content = f.read()
                mime, _ = mimetypes.guess_type(path)
                ext = _detect_ext(mime or "")
                return (f"{base}_{suffix}{ext}", content)
            except Exception:
                return None

        if not url.startswith("http"):
            return None

        try:
            resp = requests.get(url, headers=_DL_HEADERS, timeout=15)
            if resp.status_code == 200:
                ext = _detect_ext(resp.headers.get("content-type", ""))
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

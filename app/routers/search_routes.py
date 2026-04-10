"""Search routes — start search, SSE progress, local + web search."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import GOOGLE_SEARCH_KEY, GOOGLE_CSE_ID, UPLOAD_DIR
from app.core.searcher import ImageSearcher
from app.services.ai_service import (
    ai_available,
    ai_build_search_queries,
    ai_optimize_search_query,
    ai_rank_urls,
)
from app.database import SessionLocal, get_db
from app.templates_config import templates
from app.models import BrandSearchConfig, SearchCache, Session, UniqueItem

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
_MAX_IMAGE_UPLOAD = 500 * 1024 * 1024  # 500 MB total

router = APIRouter()
logger = logging.getLogger(__name__)

# Track active searches: session_id -> {"done": int, "total": int, "running": bool}
_search_progress: dict[int, dict] = {}

# I/O-bound search — 10 workers to avoid HTTP pool exhaustion on large batches
_DEFAULT_WORKERS = 10


def _run_search_background(session_id: int, config: dict, user_id: int = None):
    """Run image search in background thread."""
    db = SessionLocal()
    # Snapshot the search generation at start — if it changes (remap), we abort DB updates
    search_gen = config.get("search_gen", 0)
    try:
        items = db.query(UniqueItem).filter(
            UniqueItem.session_id == session_id,
            UniqueItem.search_status == "pending",
        ).all()

        total = len(items)
        import time as _time_mod
        _search_progress[session_id] = {"done": 0, "total": total, "running": True, "current": "", "started_at": _time_mod.time()}

        search_mode = config.get("search_mode", "web")  # web, local, both
        local_folder = config.get("local_folder", "")

        # Load user's brand search configs + notes
        brand_site_urls = {}
        brand_notes = {}  # brand_name_lower -> search instructions text
        if user_id:
            user_brands = db.query(BrandSearchConfig).filter(
                BrandSearchConfig.user_id == user_id
            ).all()
            for bc in user_brands:
                brand_site_urls[bc.brand_name.lower()] = bc.site_urls
                if bc.search_notes:
                    brand_notes[bc.brand_name.lower()] = bc.search_notes

        # Session-level search instructions (applies to all brands)
        session_notes = config.get("search_notes", "")

        # Extra brand URLs entered in Step 3 form — apply to ALL items as priority domains
        extra_brand_urls = config.get("extra_brand_urls", [])

        search_config = {
            **config,
            "brand_site_urls": brand_site_urls,
            "extra_site_urls": extra_brand_urls,  # priority domains for this session
            "google_api_key": GOOGLE_SEARCH_KEY,
            "google_cse_id": GOOGLE_CSE_ID,
        }
        searcher = ImageSearcher(search_config) if search_mode in ("web", "both") else None
        workers = int(config.get("search_workers", _DEFAULT_WORKERS))

        # Import local search if needed — validate path to prevent traversal (C4)
        local_search_fn = None
        if search_mode in ("local", "both") and local_folder:
            from app.services.local_search import search_local_folder
            import pathlib
            _lf = pathlib.Path(local_folder).resolve()
            if _lf.is_dir():
                local_search_fn = search_local_folder
            else:
                logger.warning(f"Local folder not found or not a directory: {local_folder}")
                local_folder = ""

        use_ai = ai_available()

        def _search_one(item_id: int, item_dict: dict):
            cache_db = SessionLocal()
            try:
                # Check cross-session cache first
                cached = cache_db.query(SearchCache).filter(
                    SearchCache.item_code == item_dict["item_code"],
                    SearchCache.color_code == (item_dict.get("color_code") or ""),
                    SearchCache.brand == (item_dict.get("brand") or ""),
                ).first()

                if cached and cached.candidates:
                    return item_id, cached.candidates, cached.scores, True

                candidates = []
                scores = {}
                item_brand = (item_dict.get("brand") or "").lower()
                brand_label = item_dict.get("brand", "")

                # ── STEP 1: AI builds initial search queries ─────────────────
                # Always use AI for query building when available.
                # If user has search notes/instructions, those take priority.
                # Otherwise AI still crafts smarter queries than simple concatenation.
                ai_queries = []
                if use_ai:
                    combined_notes = "\n".join(filter(None, [
                        session_notes,
                        brand_notes.get(item_brand, ""),
                    ]))
                    if combined_notes.strip():
                        ai_queries = ai_build_search_queries(
                            item_dict, brand_label, combined_notes
                        )
                    else:
                        # No instructions → AI still generates optimized queries
                        ai_queries = ai_optimize_search_query(item_dict, brand_label)

                # ── STEP 2: Local folder search ───────────────────────────────
                if local_search_fn:
                    local_results = local_search_fn(local_folder, item_dict)
                    for lr in local_results:
                        file_url = f"file://{lr['path']}"
                        candidates.append(file_url)
                        scores[file_url] = lr["score"]

                # ── STEP 3: Web search with AI queries ────────────────────────
                if searcher and (search_mode == "web" or (search_mode == "both" and len(candidates) < 3)):
                    web_candidates, web_scores = searcher.search(
                        item_dict, ai_queries=ai_queries or None
                    )
                    for url in web_candidates:
                        if url not in candidates:
                            candidates.append(url)
                            scores[url] = web_scores.get(url, 0)

                # ── STEP 4: AI retry if results are poor ─────────────────────
                # If we got fewer than 2 results or all scores < 0.25, AI tries
                # new queries based on what failed.
                if use_ai and searcher and len(candidates) < 2:
                    retry_queries = ai_optimize_search_query(
                        item_dict, brand_label, failed_queries=ai_queries or None
                    )
                    if retry_queries:
                        retry_candidates, retry_scores = searcher.search(
                            item_dict, ai_queries=retry_queries
                        )
                        for url in retry_candidates:
                            if url not in candidates:
                                candidates.append(url)
                                scores[url] = retry_scores.get(url, 0)

                # ── STEP 5: AI re-ranks the final URL list ────────────────────
                # Only web URLs go through AI ranking (local file:// paths are kept as-is)
                if use_ai and candidates:
                    web_urls = [u for u in candidates if not u.startswith("file://")]
                    local_urls = [u for u in candidates if u.startswith("file://")]
                    if web_urls:
                        ranked_web = ai_rank_urls(web_urls, item_dict, brand_label)
                        # Rebuild candidates: local first, then AI-ranked web
                        reranked = local_urls + ranked_web
                        # Carry over scores, default 0.5 for AI-promoted URLs
                        new_scores = {}
                        for i, url in enumerate(reranked):
                            base = scores.get(url, 0.5)
                            # Boost top-ranked URLs slightly
                            position_bonus = max(0.0, 0.1 - i * 0.02)
                            new_scores[url] = min(round(base + position_bonus, 2), 1.0)
                        candidates = reranked
                        scores = new_scores

                # ── STEP 6: Save to cache — upsert pattern avoids constraint errors (M5)
                try:
                    existing_cache = cache_db.query(SearchCache).filter(
                        SearchCache.item_code == item_dict["item_code"],
                        SearchCache.color_code == (item_dict.get("color_code") or ""),
                        SearchCache.brand == (item_dict.get("brand") or ""),
                    ).first()
                    if existing_cache:
                        existing_cache.candidates = candidates
                        existing_cache.scores = scores
                    else:
                        new_cache = SearchCache(
                            item_code=item_dict["item_code"],
                            color_code=item_dict.get("color_code") or "",
                            brand=item_dict.get("brand") or "",
                        )
                        new_cache.candidates = candidates
                        new_cache.scores = scores
                        cache_db.add(new_cache)
                    cache_db.commit()
                except Exception:
                    cache_db.rollback()

                return item_id, candidates, scores, False
            finally:
                cache_db.close()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for item in items:
                item_dict = {
                    "item_code": item.item_code,
                    "color_code": item.color_code,
                    "color_name": item.color_name,
                    "style_name": item.style_name,
                    "brand": item.brand,
                }
                futures[executor.submit(_search_one, item.id, item_dict)] = item

            for future in as_completed(futures):
                # Check if search was cancelled (remap bumped search_gen)
                try:
                    current_sess = db.query(Session).get(session_id)
                    current_gen = (current_sess.config or {}).get("search_gen", 0) if current_sess else -1
                    if current_gen != search_gen:
                        logger.info(f"Search cancelled for session {session_id} (gen {search_gen} != {current_gen})")
                        break
                except Exception:
                    pass

                try:
                    item_id, candidates, scores, from_cache = future.result()
                    db_item = db.query(UniqueItem).get(item_id)
                    if db_item:
                        db_item.candidates = candidates
                        db_item.scores = scores
                        db_item.search_status = "done"
                        if candidates:
                            best = max(candidates, key=lambda u: scores.get(u, 0))
                            db_item.approved_url = best
                            db_item.review_status = "approved"
                            db_item.auto_selected = True
                        else:
                            db_item.approved_url = ""
                            db_item.review_status = "approved"
                            db_item.auto_selected = True
                        db.commit()
                except Exception as e:
                    logger.error(f"Search error: {e}")

                _search_progress[session_id]["done"] += 1
                item_obj = futures[future]
                _search_progress[session_id]["current"] = item_obj.item_code

        # Update session status — only if this search generation is still current
        # (guards against a remap happening mid-search that bumped search_gen)
        sess = db.query(Session).get(session_id)
        current_gen = (sess.config or {}).get("search_gen", 0) if sess else -1
        if sess and current_gen == search_gen:
            sess.status = "reviewing"
            sess.searched_items = total
            db.commit()

        _search_progress[session_id]["running"] = False

        # L3: Schedule cleanup of progress entry after 5 minutes (gives SSE time to drain)
        import time as _time
        def _cleanup_progress():
            _time.sleep(300)
            _search_progress.pop(session_id, None)
        threading.Thread(target=_cleanup_progress, daemon=True).start()

        # Notify user
        if user_id:
            from app.services.notifications import add_notification
            sess_name = sess.name if sess else f"Session #{session_id}"
            add_notification(
                user_id, "search_done",
                "Image Search Complete",
                f"{total} items searched — {sess_name}",
                session_id,
                [
                    {"label": "Review Images", "url": f"/review/{session_id}"},
                    {"label": "Export", "url": f"/generate/{session_id}"},
                ],
            )
    except Exception as e:
        logger.error(f"Background search failed: {e}")
        _search_progress[session_id]["running"] = False
    finally:
        db.close()


# ── Batch search: start multiple sessions in parallel ─────────────────────────
# NOTE: these static routes must come before /search/{session_id} dynamic routes.

@router.post("/search/batch/start")
async def start_batch_search(request: Request, db: DBSession = Depends(get_db)):
    """Start image search for multiple sessions simultaneously."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    session_ids: list[int] = data.get("session_ids", [])
    search_mode = data.get("search_mode", "web")
    local_folder = data.get("local_folder", "")
    brand_urls = data.get("brand_urls", [])
    search_notes = data.get("search_notes", "")

    if not session_ids:
        return JSONResponse({"error": "No session IDs provided"}, status_code=400)

    started = []
    for sid in session_ids:
        sess = db.query(Session).filter(Session.id == sid, Session.user_id == uid).first()
        if not sess:
            continue
        if _search_progress.get(sid, {}).get("running"):
            started.append(sid)
            continue

        config = sess.config
        config["search_mode"] = search_mode
        config["local_folder"] = local_folder
        config["search_notes"] = search_notes
        if brand_urls:
            config["extra_brand_urls"] = brand_urls
        sess.config = config
        sess.status = "searching"
        db.commit()

        thread = threading.Thread(
            target=_run_search_background,
            args=(sid, config, uid),
            daemon=True,
        )
        thread.start()
        started.append(sid)

    return JSONResponse({"ok": True, "started": started})


@router.get("/search/batch/progress")
async def batch_search_progress_sse(session_ids: str, request: Request):
    """SSE for multiple sessions' search progress. Pass session_ids as comma-separated."""
    ids = [int(x) for x in session_ids.split(",") if x.strip().isdigit()]

    async def event_stream():
        # Give threads up to 3 s to register themselves in _search_progress
        for _ in range(30):
            if all(sid in _search_progress for sid in ids):
                break
            await asyncio.sleep(0.1)

        while True:
            snapshot = {}
            all_done = True
            for sid in ids:
                p = _search_progress.get(sid)
                if p is None:
                    snapshot[str(sid)] = {"done": 0, "total": 0, "running": True, "current": ""}
                    all_done = False
                else:
                    snapshot[str(sid)] = p
                    if p.get("running") or (p.get("total", 0) > 0 and p.get("done", 0) < p.get("total", 0)):
                        all_done = False

            yield f"data: {json.dumps(snapshot)}\n\n"
            if all_done:
                yield f"data: {json.dumps({**snapshot, 'complete': True})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Image upload for local search ────────────────────────────────────────────

@router.post("/search/{session_id}/upload-images")
async def upload_images_for_search(
    session_id: int,
    request: Request,
    files: List[UploadFile] = File(...),
    db: DBSession = Depends(get_db),
):
    """
    Accept image files (JPG/PNG/WebP/etc. or ZIP) uploaded from the user's browser.
    Stores them server-side so the local search can run against them.
    Returns {"ok": True, "folder_path": "...", "image_count": N}.
    """
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    img_dir = UPLOAD_DIR / f"user_{uid}" / f"session_{session_id}_images"
    img_dir.mkdir(parents=True, exist_ok=True)

    image_count = 0
    total_bytes = 0

    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > _MAX_IMAGE_UPLOAD:
            return JSONResponse({"error": "Total upload size exceeds 500 MB limit"}, status_code=413)

        filename = upload.filename or "image"
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for zname in zf.namelist():
                        zext = os.path.splitext(zname)[1].lower()
                        if zext not in _IMAGE_EXTENSIONS:
                            continue
                        basename = os.path.basename(zname)
                        if not basename:
                            continue
                        dest = img_dir / basename
                        # Avoid overwriting with a counter suffix
                        counter = 1
                        stem, sfx = os.path.splitext(basename)
                        while dest.exists():
                            dest = img_dir / f"{stem}_{counter}{sfx}"
                            counter += 1
                        dest.write_bytes(zf.read(zname))
                        image_count += 1
            except zipfile.BadZipFile:
                logger.warning(f"Skipping bad ZIP: {filename}")
        elif ext in _IMAGE_EXTENSIONS:
            dest = img_dir / filename
            counter = 1
            stem, sfx = os.path.splitext(filename)
            while dest.exists():
                dest = img_dir / f"{stem}_{counter}{sfx}"
                counter += 1
            dest.write_bytes(content)
            image_count += 1

    logger.info(f"Session {session_id}: uploaded {image_count} images to {img_dir}")
    return JSONResponse({"ok": True, "folder_path": str(img_dir), "image_count": image_count})


# ── Per-session routes ────────────────────────────────────────────────────────

@router.get("/search/{session_id}", response_class=HTMLResponse)
async def search_page(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return RedirectResponse("/", status_code=302)

    # If already done searching, go to review
    if sess.status in ("reviewing", "completed"):
        return RedirectResponse(f"/review/{session_id}", status_code=302)

    # Check if search is already running
    prog = _search_progress.get(session_id, {})
    is_running = prog.get("running", False)

    # If session says "searching" but no active background thread,
    # the search finished (or crashed) — check if items were searched
    if sess.status == "searching" and not is_running:
        searched = db.query(UniqueItem).filter(
            UniqueItem.session_id == session_id,
            UniqueItem.search_status == "done",
        ).count()
        if searched > 0:
            sess.status = "reviewing"
            db.commit()
            return RedirectResponse(f"/review/{session_id}", status_code=302)

    return templates.TemplateResponse(request, "search.html", {
        "session": sess,
        "is_running": is_running,
    })


@router.post("/search/{session_id}/start")
async def start_search(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Start search with user-selected options."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = await request.json()
    search_mode = data.get("search_mode", "web")  # web, local, both
    local_folder = data.get("local_folder", "")
    brand_urls = data.get("brand_urls", [])  # Additional brand URLs for this search

    # Update session config with search settings
    config = sess.config
    config["search_mode"] = search_mode
    config["local_folder"] = local_folder
    if brand_urls:
        config["extra_brand_urls"] = brand_urls
    # Bump search generation so any stale background threads won't overwrite status
    config["search_gen"] = config.get("search_gen", 0) + 1
    sess.config = config
    sess.status = "searching"
    db.commit()

    # Clear any stale progress so old threads don't block a new search from starting
    _search_progress.pop(session_id, None)

    # Start search if not already running
    if session_id not in _search_progress or not _search_progress.get(session_id, {}).get("running"):
        thread = threading.Thread(
            target=_run_search_background,
            args=(session_id, config, uid),
            daemon=True,
        )
        thread.start()

    return JSONResponse({"ok": True})


@router.get("/search/{session_id}/progress")
async def search_progress_sse(session_id: int, request: Request):
    """SSE endpoint for real-time search progress."""
    async def event_stream():
        while True:
            progress = _search_progress.get(session_id, {"done": 0, "total": 0, "running": False, "current": ""})
            data = json.dumps(progress)
            yield f"data: {data}\n\n"

            if not progress.get("running") and progress.get("done", 0) >= progress.get("total", 1):
                yield f"data: {json.dumps({**progress, 'complete': True})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

"""Google Sheets import routes — supports single and parallel batch imports."""
from __future__ import annotations

import asyncio
import os
import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import BASE_DIR
from app.database import SessionLocal, get_db
from app.main import templates
from app.models import Session, UniqueItem

router = APIRouter()

# Batch import progress: batch_id -> {jobs: [...], running: bool, done: int, total: int}
_batch_progress: dict[str, dict] = {}
# Track which batches belong to which user: user_id -> [batch_id, ...]
_user_batches: dict[int, list[str]] = {}


def _persist_batch(batch_id: str, uid: int) -> None:
    """Save current batch state to disk (non-blocking best-effort)."""
    try:
        from app.services.task_state import save_batch
        save_batch(batch_id, uid, _batch_progress[batch_id])
    except Exception:
        pass


def _get_credentials_path(user_id: int) -> str:
    """Get path to credentials — user-specific first, then shared default."""
    cred_dir = BASE_DIR / "credentials"
    cred_dir.mkdir(exist_ok=True)
    user_path = cred_dir / f"user_{user_id}_google.json"
    if os.path.exists(user_path):
        return str(user_path)
    default_path = cred_dir / "google_credentials.json"
    return str(default_path)


def _do_import_sheet_sync(uid: int, sheets_url: str, cred_path: str) -> dict:
    """Synchronous import logic — safe to run in a thread."""
    db = SessionLocal()
    try:
        from app.core.sheets_reader import SheetsReader, extract_spreadsheet_id
        spreadsheet_id = extract_spreadsheet_id(sheets_url)
        reader = SheetsReader(cred_path)
        result = reader.fetch_spreadsheet(spreadsheet_id)
    except FileNotFoundError:
        db.close()
        return {"error": "Google credentials file not found"}
    except Exception as e:
        db.close()
        return {"error": f"Failed to fetch spreadsheet: {str(e)}"}

    try:
        if not result["tabs"]:
            return {"error": "No data found in spreadsheet"}

        sess = Session(
            user_id=uid,
            name=result["title"],
            source_type="google_sheets",
            source_ref=sheets_url,
            status="reviewing",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)

        # Reuse the already-authenticated reader instance
        reader_inst = reader

        total_items = 0
        seen_keys: set = set()

        for tab in result["tabs"]:
            items = reader_inst.extract_items_from_tab(tab)
            for item in items:
                item_code = item.get("item_code", "").strip()
                color_code = (item.get("color_name", "") or "").strip()
                if not item_code:
                    continue
                key = (item_code, color_code)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                whs = _parse_price(item.get("wholesale_price"))
                rrp = _parse_price(item.get("retail_price"))
                qty = _parse_price(item.get("qty_available"))

                ui = UniqueItem(
                    session_id=sess.id,
                    item_code=item_code,
                    color_code=color_code,
                    brand=item.get("brand", ""),
                    style_name=item.get("style_name", ""),
                    color_name=item.get("color_name", ""),
                    gender=item.get("gender", ""),
                    wholesale_price=whs,
                    retail_price=rrp,
                    qty_available=qty,
                )
                image_url = item.get("image_url") or item.get("dropbox_url") or ""
                if image_url:
                    ui.approved_url = image_url
                    ui.review_status = "approved"
                    ui.auto_selected = True
                    ui.search_status = "done"
                ui.sizes = [item.get("size")] if item.get("size") else []
                db.add(ui)
                total_items += 1

        sess.total_items = total_items
        sess.searched_items = total_items
        # C2: commit without destroying the session on duplicate — duplicates were
        # already filtered out in-memory by seen_keys above; the DB constraint is
        # a last-resort guard. If it fires, rollback just the item inserts and
        # re-insert one-by-one so only true duplicates are skipped.
        try:
            db.commit()
        except Exception:
            db.rollback()
            # Re-attach session and re-insert items individually, skipping conflicts
            db.add(sess)
            db.commit()
            total_items = 0
            for tab in result["tabs"]:
                for item in reader_inst.extract_items_from_tab(tab):
                    item_code = item.get("item_code", "").strip()
                    color_code = (item.get("color_name", "") or "").strip()
                    if not item_code:
                        continue
                    try:
                        ui2 = UniqueItem(
                            session_id=sess.id,
                            item_code=item_code,
                            color_code=color_code,
                            brand=item.get("brand", ""),
                            style_name=item.get("style_name", ""),
                            color_name=item.get("color_name", ""),
                            gender=item.get("gender", ""),
                            wholesale_price=_parse_price(item.get("wholesale_price")),
                            retail_price=_parse_price(item.get("retail_price")),
                            qty_available=_parse_price(item.get("qty_available")),
                        )
                        image_url = item.get("image_url") or item.get("dropbox_url") or ""
                        if image_url:
                            ui2.approved_url = image_url
                            ui2.review_status = "approved"
                            ui2.auto_selected = True
                            ui2.search_status = "done"
                        ui2.sizes = [item.get("size")] if item.get("size") else []
                        db.add(ui2)
                        db.commit()
                        total_items += 1
                    except Exception:
                        db.rollback()
            sess.total_items = total_items
            sess.searched_items = total_items
            db.commit()

        with_images = db.query(UniqueItem).filter(
            UniqueItem.session_id == sess.id,
            UniqueItem.approved_url.isnot(None),
            UniqueItem.approved_url != "",
        ).count()
        without_images = total_items - with_images

        return {
            "ok": True,
            "session_id": sess.id,
            "title": result["title"],
            "tabs": len(result["tabs"]),
            "items": total_items,
            "with_images": with_images,
            "without_images": without_images,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


# ── Single import (original endpoint, kept for compatibility) ─────────────────

@router.get("/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    cred_path = _get_credentials_path(uid)
    has_credentials = os.path.exists(cred_path)

    return templates.TemplateResponse(request, "sheets.html", {
        "has_credentials": has_credentials,
    })


@router.post("/sheets/import")
async def import_sheets(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    sheets_url = data.get("url", "").strip()
    if not sheets_url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    cred_path = _get_credentials_path(uid)
    if not os.path.exists(cred_path):
        return JSONResponse({"error": "Google credentials not configured. Go to Settings to upload."}, status_code=400)

    result = await asyncio.to_thread(_do_import_sheet_sync, uid, sheets_url, cred_path)
    if result.get("ok"):
        return JSONResponse(result)
    return JSONResponse(result, status_code=400)


# ── Batch import (multiple URLs in parallel) ──────────────────────────────────

@router.post("/sheets/import-batch")
async def import_sheets_batch(request: Request):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    urls: list[str] = [u.strip() for u in data.get("urls", []) if u.strip()]
    if not urls:
        return JSONResponse({"error": "No URLs provided"}, status_code=400)

    cred_path = _get_credentials_path(uid)
    if not os.path.exists(cred_path):
        return JSONResponse({"error": "Google credentials not configured. Go to Settings to upload."}, status_code=400)

    batch_id = str(uuid.uuid4())
    _user_batches.setdefault(uid, []).append(batch_id)
    _batch_progress[batch_id] = {
        "jobs": [
            {
                "url": url, "status": "pending", "title": "", "error": "",
                "session_id": None, "items": 0, "with_images": 0, "without_images": 0,
            }
            for url in urls
        ],
        "running": True,
        "done": 0,
        "total": len(urls),
    }

    async def _run_job(idx: int, url: str):
        from app.services.notifications import add_notification
        _batch_progress[batch_id]["jobs"][idx]["status"] = "importing"
        _persist_batch(batch_id, uid)
        try:
            result = await asyncio.to_thread(_do_import_sheet_sync, uid, url, cred_path)
            job = _batch_progress[batch_id]["jobs"][idx]
            if result.get("ok"):
                job.update({
                    "status": "done",
                    "session_id": result["session_id"],
                    "title": result["title"],
                    "items": result["items"],
                    "with_images": result["with_images"],
                    "without_images": result["without_images"],
                })
                _persist_batch(batch_id, uid)
                add_notification(
                    uid, "import_done",
                    f"Import Complete: {result['title']}",
                    f"{result['items']} items · {result['with_images']} with images",
                    result["session_id"],
                    [
                        {"label": "Review", "url": f"/review/{result['session_id']}"},
                        {"label": "Export", "url": f"/generate/{result['session_id']}"},
                    ],
                )
            else:
                job["status"] = "error"
                job["error"] = result.get("error", "Unknown error")
                _persist_batch(batch_id, uid)
                add_notification(
                    uid, "import_error",
                    "Import Failed",
                    result.get("error", "Unknown error"),
                )
        except Exception as e:
            _batch_progress[batch_id]["jobs"][idx]["status"] = "error"
            _batch_progress[batch_id]["jobs"][idx]["error"] = str(e)
            _persist_batch(batch_id, uid)
            add_notification(uid, "import_error", "Import Failed", str(e))
        finally:
            _batch_progress[batch_id]["done"] += 1

    async def _run_all():
        await asyncio.gather(*[_run_job(i, url) for i, url in enumerate(urls)])
        _batch_progress[batch_id]["running"] = False
        _persist_batch(batch_id, uid)
        # Remove from active user batches
        try:
            _user_batches.get(uid, []).remove(batch_id)
        except ValueError:
            pass
        # Clean up persisted state for completed batch
        try:
            from app.services.task_state import delete_batch
            delete_batch(batch_id)
        except Exception:
            pass
        # C3: Remove completed batch from in-memory dict after a short grace period
        # (grace period lets SSE clients receive the final 'complete' event)
        await asyncio.sleep(30)
        _batch_progress.pop(batch_id, None)

    asyncio.create_task(_run_all())

    return JSONResponse({"ok": True, "batch_id": batch_id, "total": len(urls)})


@router.get("/sheets/batch/{batch_id}/progress")
async def batch_progress_sse(batch_id: str, request: Request):
    """SSE stream for batch import progress."""
    import json as _json

    async def event_stream():
        while True:
            batch = _batch_progress.get(batch_id)
            if not batch:
                yield f"data: {_json.dumps({'error': 'not found'})}\n\n"
                break
            yield f"data: {_json.dumps(batch)}\n\n"
            if not batch.get("running") and batch.get("done", 0) >= batch.get("total", 1):
                yield f"data: {_json.dumps({**batch, 'complete': True})}\n\n"
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/sheets/credentials")
async def upload_credentials(request: Request, db: DBSession = Depends(get_db)):
    """Upload Google service account JSON credentials."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    cred_file = form.get("credentials")
    if not cred_file:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    content = await cred_file.read()
    cred_path = _get_credentials_path(uid)
    with open(cred_path, "wb") as f:
        f.write(content)

    return JSONResponse({"ok": True})


def _parse_price(val) -> float | None:
    """Parse price supporting both US (1,234.56) and European (1.234,56) formats. (L2)"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    # Strip currency symbols and whitespace
    s = re.sub(r"[€$£¥\s]", "", s)
    # Detect European format: comma as decimal separator (e.g. "12,50" or "1.234,56")
    # Pattern: ends with comma + 1-2 digits (e.g. ",50") → European decimal
    if re.search(r",\d{1,2}$", s) and "." in s:
        # "1.234,56" → remove dots (thousands), replace comma with dot
        s = s.replace(".", "").replace(",", ".")
    elif re.search(r",\d{1,2}$", s):
        # "12,50" → replace comma with dot (no thousands separator)
        s = s.replace(",", ".")
    else:
        # US format or already normalized: remove commas (thousands separator)
        s = s.replace(",", "")
    # Strip any remaining non-numeric chars except dot and minus
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

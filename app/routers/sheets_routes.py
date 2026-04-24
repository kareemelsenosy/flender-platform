"""Google Sheets import routes — supports single and parallel batch imports."""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import BASE_DIR
from app.database import SessionLocal, get_db
from app.templates_config import templates
from app.models import Session, UniqueItem

router = APIRouter()

# Batch import progress: batch_id -> {jobs: [...], running: bool, done: int, total: int}
_batch_progress: dict[str, dict] = {}
# Track which batches belong to which user: user_id -> [batch_id, ...]
_user_batches: dict[int, list[str]] = {}
# Completed batches kept for 10 minutes so users can return to sheets page
# user_id -> [{"batch_id": ..., "jobs": [...], "completed_at": timestamp}, ...]
_completed_batches: dict[int, list[dict]] = {}


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


def _do_import_sheet_sync(uid: int, sheets_url: str, cred_path: str,
                          selected_tabs: list[str] | None = None,
                          save_images: bool = True,
                          search_missing: bool = True) -> dict:
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

        # Reuse the already-authenticated reader instance
        reader_inst = reader

        total_items = 0
        all_rows = []  # flat list of per-row dicts

        # Filter to selected tabs only (if user made a selection)
        tabs_to_process = result["tabs"]
        if selected_tabs:
            tabs_to_process = [t for t in result["tabs"] if t["title"] in selected_tabs]
            if not tabs_to_process:
                tabs_to_process = result["tabs"]  # fallback: all tabs

        session_name = result["title"]
        if len(tabs_to_process) == 1:
            session_name = f"{result['title']} — {tabs_to_process[0]['title']}"

        sess = Session(
            user_id=uid,
            name=session_name,
            source_type="google_sheets",
            source_ref=sheets_url,
            status="reviewing",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)

        # Detect currency from first tab's headers and WHS Price values
        detected_currency = "€"
        if tabs_to_process:
            first_tab = tabs_to_process[0]
            headers = first_tab.get("headers", [])
            # Check for currency in Total column name (e.g. "TOTAL (AED)")
            for h in headers:
                hu = h.upper()
                if "TOTAL" in hu:
                    if "AED" in hu:
                        detected_currency = "AED "
                    elif "USD" in hu or "$" in hu:
                        detected_currency = "$"
                    elif "GBP" in hu or "£" in hu:
                        detected_currency = "£"
                    elif "EUR" in hu or "€" in hu:
                        detected_currency = "€"
                    break
            # Also check first few WHS Price values for currency prefix
            if detected_currency == "€":
                items_sample = reader_inst.extract_items_from_tab(first_tab)[:5]
                for it in items_sample:
                    raw_whs = str(it.get("wholesale_price", "")).strip()
                    ru = raw_whs.upper()
                    if "AED" in ru or ru.startswith("DH"):
                        detected_currency = "AED "
                        break
                    elif ru.startswith("$"):
                        detected_currency = "$"
                        break
                    elif ru.startswith("£"):
                        detected_currency = "£"
                        break

        for tab in tabs_to_process:
            items = reader_inst.extract_items_from_tab(tab)
            for item in items:
                item_code = item.get("item_code", "").strip()
                if not item_code:
                    continue
                size = item.get("size", "").strip()
                all_rows.append({
                    "item_code": item_code,
                    "color_name": item.get("color_name", ""),
                    "size": size,
                    "brand": item.get("brand", ""),
                    "style_name": item.get("style_name", ""),
                    "gender": item.get("gender", ""),
                    "wholesale_price": _parse_price(item.get("wholesale_price")),
                    "retail_price": _parse_price(item.get("retail_price")),
                    "qty_available": _parse_price(item.get("qty_available")),
                    "barcode": item.get("barcode", ""),
                    "item_group": item.get("item_group", ""),
                    "sap_code": item.get("sap_code", ""),
                    "image_url": item.get("image_url") or "" if save_images else "",
                    "pictures_url": item.get("dropbox_url") or "" if save_images else "",
                    "comming_soon_qty": item.get("comming_soon_qty", ""),
                    "source_sheet": tab["title"],
                })

        # Store currency in session config
        cfg = sess.config
        cfg["currency"] = detected_currency
        cfg["selected_sheet_tabs"] = [tab["title"] for tab in tabs_to_process]
        cfg["google_sheet_title"] = result["title"]
        cfg["search_missing"] = bool(search_missing)
        sess.config = cfg

        # Store each row as its own UniqueItem (no aggregation).
        # color_code = color|size|source_sheet so items from different tabs with
        # the same SKU+color+size don't collide on the unique constraint.
        for row in all_rows:
            size = row["size"]
            color = row["color_name"]
            _src = row.get("source_sheet") or ""
            ui = UniqueItem(
                session_id=sess.id,
                item_code=row["item_code"],
                color_code=f"{color}|{size}|{_src}" if _src else (f"{color}|{size}" if size else color),
                brand=row["brand"],
                style_name=row["style_name"],
                color_name=color,
                gender=row["gender"],
                wholesale_price=row["wholesale_price"],
                retail_price=row["retail_price"],
                qty_available=row["qty_available"],
                barcode=row["barcode"],
                item_group=row["item_group"],
                source_sheet=row.get("source_sheet", ""),
                comming_soon_qty=row.get("comming_soon_qty", ""),
            )
            ui.sizes = [size] if size else []
            ui.pictures_url = row.get("pictures_url") or ""
            image_url = row["image_url"]
            if image_url and save_images:
                ui.approved_url = image_url
                ui.review_status = "approved"
                ui.auto_selected = True
                ui.search_status = "done"
            elif not search_missing:
                ui.review_status = "approved"
                ui.search_status = "done"
            db.add(ui)
            total_items += 1

        sess.total_items = total_items
        sess.searched_items = total_items
        try:
            db.commit()
        except Exception:
            db.rollback()
            db.add(sess)
            db.commit()
            total_items = 0
            for row in all_rows:
                try:
                    size = row["size"]
                    color = row["color_name"]
                    _src = row.get("source_sheet") or ""
                    ui2 = UniqueItem(
                        session_id=sess.id,
                        item_code=row["item_code"],
                        color_code=f"{color}|{size}|{_src}" if _src else (f"{color}|{size}" if size else color),
                        brand=row["brand"],
                        style_name=row["style_name"],
                        color_name=color,
                        gender=row["gender"],
                        wholesale_price=row["wholesale_price"],
                        retail_price=row["retail_price"],
                        qty_available=row["qty_available"],
                        barcode=row["barcode"],
                        item_group=row["item_group"],
                        source_sheet=row.get("source_sheet", ""),
                        comming_soon_qty=row.get("comming_soon_qty", ""),
                    )
                    ui2.sizes = [size] if size else []
                    ui2.pictures_url = row.get("pictures_url") or ""
                    image_url = row["image_url"]
                    if image_url and save_images:
                        ui2.approved_url = image_url
                        ui2.review_status = "approved"
                        ui2.auto_selected = True
                        ui2.search_status = "done"
                    elif not search_missing:
                        ui2.review_status = "approved"
                        ui2.search_status = "done"
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
            "title": session_name,
            "tabs": len(tabs_to_process),
            "items": total_items,
            "with_images": with_images,
            "without_images": without_images,
            "search_missing": bool(search_missing),
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


@router.post("/sheets/preview-tabs")
async def preview_tabs(request: Request):
    """Fetch tab names from a Google Sheets URL (no import). Used by UI for tab selection."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    url = data.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    cred_path = _get_credentials_path(uid)
    if not os.path.exists(cred_path):
        return JSONResponse({"error": "No Google credentials found"}, status_code=400)

    try:
        from app.core.sheets_reader import SheetsReader, extract_spreadsheet_id
        spreadsheet_id = extract_spreadsheet_id(url)
        reader = SheetsReader(cred_path)
        spreadsheet = reader.gc.open_by_key(spreadsheet_id)
        tabs = [ws.title for ws in spreadsheet.worksheets()]
        return JSONResponse({"ok": True, "title": spreadsheet.title, "tabs": tabs})
    except Exception as e:
        msg = str(e)
        if "not supported for this document" in msg or "[400]" in msg:
            msg = (
                "This document cannot be accessed via the Google Sheets API. "
                "It is likely an Excel file (.xlsx) stored in Google Drive. "
                "To fix: open it in Google Sheets → File → Save as Google Sheets, "
                "then share the new Sheets file with your service account."
            )
        return JSONResponse({"error": msg}, status_code=500)


# ── Single import (original endpoint, kept for compatibility) ─────────────────

@router.get("/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    cred_path = _get_credentials_path(uid)
    has_credentials = os.path.exists(cred_path)

    # Check for recently completed batches so the page can show results on revisit
    import time as _t
    now = _t.time()
    completed_jobs = []
    for batch in _completed_batches.get(uid, []):
        if now - batch["completed_at"] < 600:  # 10 min
            completed_jobs.extend(batch.get("jobs", []))

    return templates.TemplateResponse(request, "sheets.html", {
        "has_credentials": has_credentials,
        "completed_jobs_json": json.dumps(completed_jobs) if completed_jobs else "",
    })


@router.post("/sheets/import")
async def import_sheets(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    sheets_url = data.get("url", "").strip()
    save_images: bool = data.get("save_images", True)
    search_missing: bool = data.get("search_missing", True)
    if not sheets_url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    cred_path = _get_credentials_path(uid)
    if not os.path.exists(cred_path):
        return JSONResponse({"error": "Google credentials not configured. Go to Settings to upload."}, status_code=400)

    result = await asyncio.to_thread(_do_import_sheet_sync, uid, sheets_url, cred_path, None, save_images, search_missing)
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
    # selected_tabs_per_url: dict mapping url -> list of tab names to import
    selected_tabs_per_url: dict[str, list[str]] = data.get("selected_tabs", {})
    save_images: bool = data.get("save_images", True)
    search_missing: bool = data.get("search_missing", True)
    if not urls:
        return JSONResponse({"error": "No URLs provided"}, status_code=400)

    cred_path = _get_credentials_path(uid)
    if not os.path.exists(cred_path):
        return JSONResponse({"error": "Google credentials not configured. Go to Settings to upload."}, status_code=400)

    import_jobs = _expand_batch_jobs(urls, selected_tabs_per_url)

    batch_id = str(uuid.uuid4())
    _user_batches.setdefault(uid, []).append(batch_id)
    _batch_progress[batch_id] = {
        "jobs": [
            {
                "url": job["url"], "label": job["label"], "status": "pending", "title": "", "error": "",
                "session_id": None, "items": 0, "with_images": 0, "without_images": 0,
                "search_missing": search_missing,
            }
            for job in import_jobs
        ],
        "running": True,
        "done": 0,
        "total": len(import_jobs),
    }

    async def _run_job(idx: int, job_data: dict):
        from app.services.notifications import add_notification
        _batch_progress[batch_id]["jobs"][idx]["status"] = "importing"
        _persist_batch(batch_id, uid)
        try:
            url = job_data["url"]
            sel_tabs = job_data.get("selected_tabs") or None
            result = await asyncio.wait_for(
                asyncio.to_thread(_do_import_sheet_sync, uid, url, cred_path, sel_tabs, save_images, search_missing),
                timeout=300,  # 5 min max per sheet
            )
            job = _batch_progress[batch_id]["jobs"][idx]
            if result.get("ok"):
                job.update({
                    "status": "done",
                    "session_id": result["session_id"],
                    "title": result["title"],
                    "items": result["items"],
                    "with_images": result["with_images"],
                    "without_images": result["without_images"],
                    "search_missing": result.get("search_missing", search_missing),
                })
                _persist_batch(batch_id, uid)
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
        await asyncio.gather(*[_run_job(i, job) for i, job in enumerate(import_jobs)])
        _batch_progress[batch_id]["running"] = False
        _persist_batch(batch_id, uid)

        # Send final notification for batch import
        from app.services.notifications import add_notification
        jobs = _batch_progress[batch_id]["jobs"]
        total_items = sum(j.get("items", 0) for j in jobs)
        total_with_images = sum(j.get("with_images", 0) for j in jobs)
        total_without_images = sum(j.get("without_images", 0) for j in jobs)
        first_session_id = next((j.get("session_id") for j in jobs if j.get("status") == "done" and j.get("session_id")), None)
        if first_session_id:
            add_notification(
                uid, "import_done",
                "Sheets Import Complete",
                f"{len(jobs)} sheet import(s) · {total_items} items · {total_with_images} with images",
                first_session_id,
                [
                    {"label": "Review First", "url": f"/review/{first_session_id}"},
                    {"label": "Export First", "url": f"/generate/{first_session_id}"},
                ],
            )

        # Save completed batch info for page revisits (kept 10 min)
        from datetime import datetime, timezone
        _completed_batches.setdefault(uid, []).append({
            "batch_id": batch_id,
            "jobs": list(_batch_progress[batch_id].get("jobs", [])),
            "completed_at": datetime.now(timezone.utc).timestamp(),
        })
        # Prune old completed batches (> 10 min)
        import time as _t
        now = _t.time()
        _completed_batches[uid] = [
            b for b in _completed_batches[uid]
            if now - b["completed_at"] < 600
        ]

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

    return JSONResponse({"ok": True, "batch_id": batch_id, "total": len(import_jobs)})


@router.get("/sheets/batch/{batch_id}/progress")
async def batch_progress_sse(batch_id: str, request: Request):
    """SSE stream for batch import progress."""
    import json as _json
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    owns_batch = batch_id in _user_batches.get(uid, []) or any(
        b.get("batch_id") == batch_id for b in _completed_batches.get(uid, [])
    )
    if not owns_batch:
        return JSONResponse({"error": "forbidden"}, status_code=403)

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

    try:
        creds_data = json.loads(content)
        required = {"type", "project_id", "private_key_id", "private_key", "client_email"}
        if not required.issubset(creds_data.keys()):
            return JSONResponse({"error": "Invalid Google credentials — missing required fields"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

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


def _expand_batch_jobs(urls: list[str], selected_tabs_per_url: dict[str, list[str]]) -> list[dict]:
    """Create one import job per spreadsheet URL so multiple sheets run in parallel."""
    jobs: list[dict] = []
    for url in urls:
        tabs = [str(t).strip() for t in (selected_tabs_per_url.get(url) or []) if str(t).strip()]
        jobs.append({"url": url, "selected_tabs": tabs or None, "label": url})
    return jobs

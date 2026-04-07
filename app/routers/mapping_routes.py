"""Column mapping routes with AI suggestions."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.core.parser import FileParser, detect_columns, COLUMN_PATTERNS
from app.database import get_db
from app.templates_config import templates
from app.models import Session, UniqueItem, ColumnMappingFormat

router = APIRouter()


@router.get("/mapping/{session_id}", response_class=HTMLResponse)
async def mapping_page(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess or not sess.uploaded_file:
        return RedirectResponse("/", status_code=302)

    # Parse file to get headers
    parser = FileParser()
    sheet_names = parser.get_sheet_names(sess.uploaded_file.file_path)
    try:
        rows, unique_items, raw_headers = parser.parse(sess.uploaded_file.file_path)
    except Exception as e:
        return templates.TemplateResponse(request, "mapping.html", {
            "session": sess, "error": str(e),
            "headers": [], "auto_mapping": {}, "standard_fields": [],
            "sample_rows": [], "saved_formats": [], "ai_mapping": {},
            "sheet_names": sheet_names,
        })

    # Use existing session mapping if available, otherwise auto-detect
    existing_mapping = sess.column_mapping
    auto_mapping = existing_mapping if existing_mapping else detect_columns(raw_headers)

    # Get saved formats
    saved_formats = db.query(ColumnMappingFormat).filter(
        ColumnMappingFormat.user_id == uid
    ).all()

    # Sample rows for preview (first 5)
    sample_rows = rows[:5]

    return templates.TemplateResponse(request, "mapping.html", {
        "session": sess,
        "error": None,
        "headers": raw_headers,
        "auto_mapping": auto_mapping,
        "standard_fields": list(COLUMN_PATTERNS.keys()),
        "sample_rows": sample_rows,
        "saved_formats": saved_formats,
        "total_rows": len(rows),
        "total_unique": len(unique_items),
        "ai_mapping": {},
        "is_remap": sess.status not in ("created", "mapping"),
        "sheet_names": sheet_names,
    })


@router.post("/mapping/{session_id}/ai-suggest")
async def ai_suggest_mapping(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    """Use Claude AI to suggest column mappings."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess or not sess.uploaded_file:
        return JSONResponse({"error": "not found"}, status_code=404)

    parser = FileParser()
    try:
        rows, _, raw_headers = parser.parse(sess.uploaded_file.file_path)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Build sample data for AI
    sample_data = []
    for row in rows[:3]:
        sample_data.append({k: v for k, v in row.items() if k != "_raw" and v is not None})

    from app.services.ai_service import ai_map_columns, ai_available
    if not ai_available():
        return JSONResponse({"error": "No AI key configured. Add GEMINI_API_KEY (free) or CLAUDE_API_KEY to your .env file."}, status_code=400)

    result = ai_map_columns(raw_headers, sample_data, list(COLUMN_PATTERNS.keys()))

    if not result:
        return JSONResponse({"error": "AI request failed. Check your API key in .env file."}, status_code=400)

    return JSONResponse(result)


@router.post("/mapping/{session_id}")
async def save_mapping(session_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if not sess or not sess.uploaded_file:
        return RedirectResponse("/", status_code=302)

    form = await request.form()

    # Build mapping from form data
    mapping = {}
    for field in COLUMN_PATTERNS.keys():
        val = form.get(f"map_{field}", "")
        mapping[field] = val if val else None

    sess.column_mapping = mapping
    sess.status = "searching"
    sess.searched_items = 0

    # Read selected sheets from form (multi-select)
    selected_sheets_raw = form.getlist("selected_sheets")
    selected_sheets = selected_sheets_raw if selected_sheets_raw else None

    # M8: Validate mapped column values actually exist in the file's headers
    parser = FileParser()
    try:
        _, _, raw_headers = parser.parse(sess.uploaded_file.file_path, selected_sheets=selected_sheets)
    except Exception as e:
        return templates.TemplateResponse(request, "mapping.html", {
            "session": sess, "error": f"Could not read file: {e}",
            "headers": [], "auto_mapping": mapping, "standard_fields": list(COLUMN_PATTERNS.keys()),
            "sample_rows": [], "saved_formats": [], "ai_mapping": {}, "is_remap": True,
        })
    invalid_cols = [v for v in mapping.values() if v and v not in raw_headers]
    if invalid_cols:
        return templates.TemplateResponse(request, "mapping.html", {
            "session": sess,
            "error": f"These mapped columns were not found in the file: {', '.join(invalid_cols)}",
            "headers": raw_headers, "auto_mapping": mapping,
            "standard_fields": list(COLUMN_PATTERNS.keys()),
            "sample_rows": [], "saved_formats": [], "ai_mapping": {}, "is_remap": True,
        })

    rows, unique_items = parser.parse_with_mapping(sess.uploaded_file.file_path, mapping,
                                                    selected_sheets=selected_sheets)

    # Clear old items and search progress
    db.query(UniqueItem).filter(UniqueItem.session_id == sess.id).delete()

    # Save unique items to DB with size normalization
    for item in unique_items:
        sizes = item.get("sizes", [])
        # Size normalization: split "32;33;34" or "S/M/L" into individual sizes
        normalized_sizes = _normalize_sizes(sizes)

        ui = UniqueItem(
            session_id=sess.id,
            item_code=item.get("item_code", ""),
            color_code=item.get("color_code"),
            brand=item.get("brand"),
            style_name=item.get("style_name"),
            color_name=item.get("color_name"),
            gender=item.get("gender"),
            wholesale_price=item.get("wholesale_price"),
            retail_price=item.get("retail_price"),
            qty_available=item.get("qty_available"),
        )
        ui.sizes = normalized_sizes
        db.add(ui)

    sess.total_items = len(unique_items)
    sess.searched_items = 0
    db.commit()

    # Optionally save as format
    format_name = form.get("save_format_name", "").strip()
    if format_name:
        existing = db.query(ColumnMappingFormat).filter(
            ColumnMappingFormat.user_id == uid,
            ColumnMappingFormat.name == format_name,
        ).first()
        if existing:
            existing.mapping = mapping
        else:
            fmt = ColumnMappingFormat(user_id=uid, name=format_name)
            fmt.mapping = mapping
            db.add(fmt)
        db.commit()

    return RedirectResponse(f"/search/{sess.id}", status_code=302)


@router.post("/mapping/{session_id}/apply-format/{format_id}")
async def apply_format(session_id: int, format_id: int, request: Request,
                       db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    fmt = db.query(ColumnMappingFormat).filter(
        ColumnMappingFormat.id == format_id, ColumnMappingFormat.user_id == uid
    ).first()
    if not fmt:
        return RedirectResponse(f"/mapping/{session_id}", status_code=302)

    sess = db.query(Session).filter(Session.id == session_id, Session.user_id == uid).first()
    if sess:
        sess.column_mapping = fmt.mapping
        db.commit()

    return RedirectResponse(f"/mapping/{session_id}", status_code=302)


def _normalize_sizes(sizes: list) -> list:
    """
    Normalize sizes: split "32;33;34" or "S/M/L" into individual sizes.
    Also handles "32-34" ranges and comma-separated.
    """
    normalized = []
    for size in sizes:
        if not size:
            continue
        s = str(size).strip()
        # Split by common delimiters
        parts = re.split(r"[;,/|]+", s)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Handle numeric ranges like "32-34" — capped at 30 items (L8)
            range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                if 0 <= end - start <= 30:
                    normalized.extend(str(i) for i in range(start, end + 1))
                    continue
            normalized.append(part)

    # Dedupe while preserving order
    seen = set()
    result = []
    for s in normalized:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result

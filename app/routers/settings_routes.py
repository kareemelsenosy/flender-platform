"""Settings routes — brand URLs, API keys, search config."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import BASE_DIR
from app.database import get_db
from app.templates_config import templates
from app.models import BrandSearchConfig, ColumnMappingFormat, User

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    user = db.get(User, uid)
    brand_configs = db.query(BrandSearchConfig).filter(
        BrandSearchConfig.user_id == uid
    ).order_by(BrandSearchConfig.brand_name).all()

    saved_formats = db.query(ColumnMappingFormat).filter(
        ColumnMappingFormat.user_id == uid
    ).all()

    # Check Google credentials — user-specific or shared default
    cred_path = BASE_DIR / "credentials" / f"user_{uid}_google.json"
    default_path = BASE_DIR / "credentials" / "google_credentials.json"
    has_google_creds = os.path.exists(cred_path) or os.path.exists(default_path)

    return templates.TemplateResponse(request, "settings.html", {
        "user": user,
        "brand_configs": brand_configs,
        "saved_formats": saved_formats,
        "has_google_creds": has_google_creds,
    })


@router.post("/settings/brand")
async def save_brand_config(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    brand_name = data.get("brand_name", "").strip()
    site_urls = data.get("site_urls", [])
    search_notes = data.get("search_notes", "").strip()

    if not brand_name:
        return JSONResponse({"error": "Brand name required"}, status_code=400)
    if len(brand_name) > 100:
        return JSONResponse({"error": "Brand name too long (max 100 chars)"}, status_code=400)
    if len(search_notes) > 5000:
        return JSONResponse({"error": "Search notes too long (max 5000 chars)"}, status_code=400)

    # Clean URLs
    clean_urls = [u.strip() for u in site_urls if u.strip()]

    existing = db.query(BrandSearchConfig).filter(
        BrandSearchConfig.user_id == uid,
        BrandSearchConfig.brand_name == brand_name,
    ).first()

    if existing:
        existing.site_urls = clean_urls
        existing.search_notes = search_notes
    else:
        config = BrandSearchConfig(
            user_id=uid,
            brand_name=brand_name,
            search_notes=search_notes,
        )
        config.site_urls = clean_urls
        db.add(config)

    db.commit()
    return JSONResponse({"ok": True})


@router.post("/settings/brand/{config_id}/delete")
async def delete_brand_config(config_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    config = db.query(BrandSearchConfig).filter(
        BrandSearchConfig.id == config_id,
        BrandSearchConfig.user_id == uid,
    ).first()

    if config:
        db.delete(config)
        db.commit()

    return JSONResponse({"ok": True})


@router.post("/settings/format/{format_id}/delete")
async def delete_format(format_id: int, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    fmt = db.query(ColumnMappingFormat).filter(
        ColumnMappingFormat.id == format_id,
        ColumnMappingFormat.user_id == uid,
    ).first()

    if fmt:
        db.delete(fmt)
        db.commit()

    return JSONResponse({"ok": True})


@router.post("/settings/google-credentials")
async def upload_google_credentials(request: Request, db: DBSession = Depends(get_db)):
    """Upload Google service account JSON credentials."""
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    cred_file = form.get("credentials")
    if not cred_file:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    content = await cred_file.read()

    # Validate JSON and check it's a Google service account
    try:
        creds_data = json.loads(content)
        required = {"type", "project_id", "private_key_id", "private_key", "client_email"}
        if not required.issubset(creds_data.keys()):
            return JSONResponse({"error": "Invalid Google credentials — missing required fields"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

    cred_dir = BASE_DIR / "credentials"
    cred_dir.mkdir(exist_ok=True)
    cred_path = cred_dir / f"user_{uid}_google.json"
    with open(cred_path, "wb") as f:
        f.write(content)

    return JSONResponse({"ok": True})

"""AI Tools hub — the landing page after login.

Shows a tile for each FLENDER AI tool. Each tile opens in a new tab.
This is the shared front door; it uses the same authentication and user
database as the rest of the platform.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.config import SMT_URL
from app.database import get_db

# SMT is served under the same domain at /smt by nginx. SMT_URL is only used
# as a fallback when an absolute URL is needed; the tile uses the path so the
# session cookie carries across naturally.
SMT_HUB_LINK = "/smt"
from app.models import User
from app.templates_config import templates

router = APIRouter()


def _tools() -> list[dict]:
    """Registry of tools shown on the hub."""
    return [
        {
            "id": "order-sheet",
            "name": "Order Sheet Generator",
            "description": (
                "Convert a FLENDER Google Sheet or Excel upload into a formatted "
                "order sheet with embedded product images, pricing and QTY columns."
            ),
            "url": "/order-sheet",
            "icon": "sheet",
            "accent": "#111111",
            "accent_soft": "#f0f0f0",
        },
        {
            "id": "products",
            "name": "Product Attributes",
            "description": (
                "Upload a SAP product export and auto-fill each style's SAP "
                "attributes — product type plus Fabric, Fit, Style and Weight — "
                "into an upload-ready sheet, with low-confidence styles flagged."
            ),
            "url": "/products",
            "icon": "sheet",
            "accent": "#0F766E",
            "accent_soft": "#e6f4f1",
        },
        {
            "id": "smt",
            "name": "Social Media Tracker",
            "description": (
                "Upload partner Instagram screenshots, auto-rename and organise "
                "them, and export session ZIPs plus monthly activity reports."
            ),
            "url": SMT_HUB_LINK,
            "icon": "radio",
            "accent": "#2D6FF7",
            "accent_soft": "#eaf1ff",
        },
    ]


@router.get("/", response_class=HTMLResponse)
async def hub(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    user = db.get(User, uid)
    if not user:
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse(request, "hub.html", {
        "user": user,
        "tools": _tools(),
    })

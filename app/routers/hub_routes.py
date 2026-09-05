"""Operations OS front door.

``/`` is the Operations Overview: which collections are being processed, where
each one is blocked, and what needs a person. The individual tools still exist
and still work — they moved to ``/tools``, because a collection is the unit of
work now, not a spreadsheet.
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
from app.core.lifecycle import PACKAGES, STAGES, collection_progress
from app.models import CollectionJob, User
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
        {
            "id": "image-sort",
            "name": "Image Sorter",
            "description": (
                "Drop in the brand's unnamed product photos plus the SAP list of "
                "items missing images — the AI works out which product each photo "
                "is and files it into a folder named after the Item Group Code, "
                "main shot named _1."
            ),
            "url": "/image-sort",
            "icon": "image",
            "accent": "#7C3AED",
            "accent_soft": "#f1ebfe",
        },
    ]


@router.get("/", response_class=HTMLResponse)
async def operations_overview(request: Request, db: DBSession = Depends(get_db)):
    """Operations Overview — the first dashboard the roadmap asks for."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    user = db.get(User, uid)
    if not user:
        return RedirectResponse("/login", status_code=302)

    jobs = (db.query(CollectionJob)
              .filter(CollectionJob.user_id == uid)
              .order_by(CollectionJob.created_at.desc())
              .limit(50).all())

    rows = [{"job": j, **collection_progress(j)} for j in jobs]
    blocked = [r for r in rows if r["job"].status in ("needs_input", "error")]
    open_warnings = sum(len((r["job"].report or {}).get("warnings", [])) for r in rows)

    return templates.TemplateResponse(request, "operations.html", {
        "user": user,
        "rows": rows,
        "stages": STAGES,
        "packages": PACKAGES,
        "blocked": blocked,
        "open_warnings": open_warnings,
        "total_styles": sum(j.total_styles or 0 for j in jobs),
        "total_skus": sum(j.total_skus or 0 for j in jobs),
        "tools": _tools(),
    })


@router.get("/tools", response_class=HTMLResponse)
async def tools_hub(request: Request, db: DBSession = Depends(get_db)):
    """The individual tools. Still here, still working — just not the front door."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    user = db.get(User, uid)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "hub.html", {
        "user": user, "tools": _tools(),
    })

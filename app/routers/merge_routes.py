"""Multi-source Step 3 — merge sessions into one enriched order sheet.

POST /merge {session_ids:[...], name?} takes two or more of the user's own
import sessions (in priority order, first = most authoritative), matches their
products across sources by identity, and writes a new "merged" session whose
items carry the best value per field plus a per-field provenance record (which
source each value came from + any conflicts). The merged session then flows
through the normal review/export pipeline.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models import Session, UniqueItem
from app.core.merge import merge_sources

router = APIRouter()


def _num(value):
    """Coerce a price/qty string to float; blank/garbage -> None."""
    if value is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(value).replace(",", "").strip())
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _item_to_row(it: UniqueItem) -> dict:
    """Flatten a UniqueItem into the row shape the merge engine consumes."""
    sizes = it.sizes or []
    return {
        "item_code": it.item_code or "",
        "color_name": it.color_name or "",
        "size": (sizes[0] if sizes else ""),
        "barcode": it.barcode or "",
        "brand": it.brand or "",
        "style_name": it.style_name or "",
        "gender": it.gender or "",
        "item_group": it.item_group or "",
        "item_group_code": it.item_group_code or "",
        "sap_code": it.sap_code or "",
        "wholesale_price": "" if it.wholesale_price is None else it.wholesale_price,
        "retail_price": "" if it.retail_price is None else it.retail_price,
        "qty_available": "" if it.qty_available is None else it.qty_available,
        "comming_soon_qty": it.comming_soon_qty or "",
        "image_url": it.approved_url or it.suggested_url or "",
    }


@router.post("/merge")
async def merge_sessions(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    session_ids = data.get("session_ids") or []
    if not isinstance(session_ids, list) or len(session_ids) < 2:
        return JSONResponse({"error": "Select at least two sources to merge."}, status_code=400)

    # Load the sessions in the caller's order (= priority), owned by this user.
    sources = []
    ordered_names = []
    for sid in session_ids:
        sess = db.query(Session).filter(Session.id == sid, Session.user_id == uid).first()
        if not sess:
            return JSONResponse({"error": f"Session {sid} not found."}, status_code=404)
        items = db.query(UniqueItem).filter(UniqueItem.session_id == sess.id).all()
        # Disambiguate duplicate source names so provenance stays readable.
        name = sess.name or f"Session {sess.id}"
        if name in ordered_names:
            name = f"{name} (#{sess.id})"
        ordered_names.append(name)
        sources.append({"name": name, "rows": [_item_to_row(it) for it in items]})

    if not any(s["rows"] for s in sources):
        return JSONResponse({"error": "The selected sources have no items to merge."}, status_code=400)

    result = merge_sources(sources)
    records = result["records"]
    summary = result["summary"]

    merged = Session(
        user_id=uid,
        name=(data.get("name") or f"Merged ({' + '.join(ordered_names)})")[:255],
        source_type="merged",
        source_ref=", ".join(str(s) for s in session_ids),
        status="reviewing",
        total_items=len(records),
        searched_items=len(records),
    )
    db.add(merged)
    db.commit()
    db.refresh(merged)

    for order, rec in enumerate(records):
        v = rec["values"]
        size = v.get("size", "")
        color = v.get("color_name", "")
        ui = UniqueItem(
            session_id=merged.id,
            item_code=v.get("item_code") or "(merged)",
            color_code=f"{color}|{size}|merged|{order}",
            brand=v.get("brand", ""),
            style_name=v.get("style_name", ""),
            color_name=color,
            gender=v.get("gender", ""),
            wholesale_price=_num(v.get("wholesale_price")),
            retail_price=_num(v.get("retail_price")),
            qty_available=_num(v.get("qty_available")),
            barcode=v.get("barcode", ""),
            item_group=v.get("item_group", ""),
            item_group_code=v.get("item_group_code", ""),
            sap_code=v.get("sap_code", ""),
            comming_soon_qty=v.get("comming_soon_qty", ""),
            source_sheet="merged",
            source_order=order,
        )
        ui.sizes = [size] if size else []
        ui.provenance = rec["provenance"]
        img = v.get("image_url", "")
        if img:
            ui.approved_url = img
            ui.auto_selected = True
        # Merged items arrive review-ready (data already resolved); user can still
        # open them to check/edit and, in Step 4, resolve flagged conflicts.
        ui.review_status = "approved"
        ui.search_status = "done"
        db.add(ui)
    db.commit()

    return JSONResponse({
        "ok": True,
        "session_id": merged.id,
        "review_url": f"/review/{merged.id}",
        "summary": summary,
    })

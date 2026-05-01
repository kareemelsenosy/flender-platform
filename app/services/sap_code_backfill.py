"""Backfill folder code fields for older Google Sheet sessions.

Older imports did not persist the Google Sheet ``Item Group Code`` / ``ItemCode``
columns on ``UniqueItem``. Export folder naming depends on those values, so this
best-effort backfill lets already-reviewed sessions download correctly without
redoing image review.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from app.config import BASE_DIR
from app.core.sheets_reader import SheetsReader, extract_spreadsheet_id
from app.models import Session, UniqueItem


def _credentials_path(user_id: int) -> str:
    cred_dir = BASE_DIR / "credentials"
    user_path = cred_dir / f"user_{user_id}_google.json"
    if os.path.exists(user_path):
        return str(user_path)
    return str(cred_dir / "google_credentials.json")


def _item_sizes(item: UniqueItem) -> list[str]:
    sizes = item.sizes or []
    if not sizes:
        return [""]
    return [str(size or "").strip() for size in sizes]


def backfill_sap_codes_for_session(db: DBSession, sess: Session, user_id: int) -> int:
    """Populate missing folder code fields from the original Google Sheet.

    Returns the number of updated rows. Any external/API/credential failure is
    intentionally swallowed because export should still continue with fallback
    folder names instead of hard failing.
    """
    if sess.source_type != "google_sheets" or not sess.source_ref:
        return 0

    missing_items = db.query(UniqueItem).filter(
        UniqueItem.session_id == sess.id,
        (
            (UniqueItem.item_group_code.is_(None)) | (UniqueItem.item_group_code == "") |
            (UniqueItem.sap_code.is_(None)) | (UniqueItem.sap_code == "")
        ),
    ).all()
    if not missing_items:
        return 0

    cred_path = _credentials_path(user_id)
    if not Path(cred_path).exists():
        return 0

    try:
        reader = SheetsReader(cred_path)
        spreadsheet = reader.fetch_spreadsheet(extract_spreadsheet_id(sess.source_ref))
        selected_tabs = [str(t).strip() for t in (sess.config.get("selected_sheet_tabs", []) or []) if str(t).strip()]
        tabs = spreadsheet.get("tabs", [])
        if selected_tabs:
            selected = set(selected_tabs)
            tabs = [tab for tab in tabs if tab.get("title") in selected]

        by_full_key: dict[tuple[str, str, str, str], dict[str, str]] = {}
        by_simple_key: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        for tab in tabs:
            tab_title = str(tab.get("title") or "").strip()
            for row in reader.extract_items_from_tab(tab):
                sap_code = str(row.get("sap_code") or "").strip()
                item_group_code = str(row.get("item_group_code") or "").strip()
                item_code = str(row.get("item_code") or "").strip()
                size = str(row.get("size") or "").strip()
                color = str(row.get("color_name") or "").strip()
                if not (item_group_code or sap_code) or not item_code:
                    continue
                codes = {"item_group_code": item_group_code, "sap_code": sap_code}
                by_full_key[(tab_title, item_code, color, size)] = codes
                by_simple_key.setdefault((tab_title, item_code, size), []).append(codes)

        updated = 0
        for item in missing_items:
            tab_title = str(item.source_sheet or "").strip()
            item_code = str(item.item_code or "").strip()
            color = str(item.color_name or "").strip()
            for size in _item_sizes(item):
                codes = by_full_key.get((tab_title, item_code, color, size))
                if not codes:
                    simple_matches = by_simple_key.get((tab_title, item_code, size), [])
                    if len(simple_matches) == 1:
                        codes = simple_matches[0]
                if codes:
                    changed = False
                    if not item.item_group_code and codes.get("item_group_code"):
                        item.item_group_code = codes["item_group_code"]
                        changed = True
                    if not item.sap_code and codes.get("sap_code"):
                        item.sap_code = codes["sap_code"]
                        changed = True
                    if changed:
                        updated += 1
                    break

        if updated:
            db.commit()
        return updated
    except Exception:
        db.rollback()
        return 0

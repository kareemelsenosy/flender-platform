from __future__ import annotations

from pathlib import Path


class _FakeSheetsReader:
    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path

    def fetch_spreadsheet(self, spreadsheet_id: str) -> dict:
        return {
            "title": "Dubai Reorder",
            "tabs": [{"title": "ReOrder_Dubai", "headers": [], "display_rows": [], "formula_rows": []}],
        }

    def extract_items_from_tab(self, tab: dict) -> list[dict]:
        return [
            {
                "item_code": "I036363.B89XX",
                "brand": "Carhartt WIP",
                "style_name": "Vestige Bandana",
                "color_name": "Blue / Wax",
                "size": "onesize",
                "gender": "Men",
                "barcode": "4068584443039",
                "item_group": "Accessories",
                "wholesale_price": "88.00 AED",
                "retail_price": "185.00 AED",
                "qty_available": "20",
                "image_url": "",
                "dropbox_url": "",
                "sap_code": "SAP-001",
                "comming_soon_qty": "3",
            }
        ]


def test_google_sheets_import_persists_comming_soon_qty(test_app, make_user, monkeypatch, tmp_path):
    user = make_user(username="sheets_user", email="sheets_user@flendergroup.com")
    cred_path = tmp_path / "google.json"
    cred_path.write_text("{}", encoding="utf-8")

    import app.core.sheets_reader as sheets_reader
    import app.routers.sheets_routes as sheets_routes

    monkeypatch.setattr(sheets_reader, "SheetsReader", _FakeSheetsReader)
    monkeypatch.setattr(sheets_reader, "extract_spreadsheet_id", lambda url: "sheet-123")

    result = sheets_routes._do_import_sheet_sync(
        user["id"],
        "https://docs.google.com/spreadsheets/d/sheet-123/edit",
        str(cred_path),
    )

    assert result["ok"] is True

    db = test_app["database"].SessionLocal()
    try:
        item = db.query(test_app["models"].UniqueItem).filter_by(session_id=result["session_id"]).one()
        assert item.comming_soon_qty == "3"
        assert item.sap_code == "SAP-001"
    finally:
        db.close()


def test_google_sheets_import_without_search_missing_auto_approves_rows(
    test_app,
    make_user,
    monkeypatch,
    tmp_path,
):
    user = make_user(username="convert_only", email="convert_only@flendergroup.com")
    cred_path = tmp_path / "google.json"
    cred_path.write_text("{}", encoding="utf-8")

    import app.core.sheets_reader as sheets_reader
    import app.routers.sheets_routes as sheets_routes

    monkeypatch.setattr(sheets_reader, "SheetsReader", _FakeSheetsReader)
    monkeypatch.setattr(sheets_reader, "extract_spreadsheet_id", lambda url: "sheet-123")

    result = sheets_routes._do_import_sheet_sync(
        user["id"],
        "https://docs.google.com/spreadsheets/d/sheet-123/edit",
        str(cred_path),
        search_missing=False,
    )

    assert result["ok"] is True

    db = test_app["database"].SessionLocal()
    try:
        item = db.query(test_app["models"].UniqueItem).filter_by(session_id=result["session_id"]).one()
        sess = db.query(test_app["models"].Session).filter_by(id=result["session_id"]).one()
        assert item.review_status == "approved"
        assert item.search_status == "done"
        assert item.approved_url in (None, "")
        assert sess.config["search_missing"] is False
    finally:
        db.close()

from __future__ import annotations

import io
import importlib
import time

from openpyxl import load_workbook


def test_expand_batch_jobs_keeps_one_job_per_sheet_url(test_app):
    sheets_routes = test_app["sheets_routes"]
    url_one = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    url_two = "https://docs.google.com/spreadsheets/d/xyz789/edit#gid=0"

    jobs = sheets_routes._expand_batch_jobs(
        [url_one, url_two],
        {
            url_one: ["ReOrder_Dubai", "PreOrder_Carhartt WIP_2026-04"],
            url_two: ["Main"],
        },
    )

    assert len(jobs) == 2
    assert jobs[0]["url"] == url_one
    assert jobs[0]["selected_tabs"] == ["ReOrder_Dubai", "PreOrder_Carhartt WIP_2026-04"]
    assert jobs[1]["url"] == url_two
    assert jobs[1]["selected_tabs"] == ["Main"]


def test_import_batch_initializes_one_parallel_job_per_sheet_url(
    client,
    login_as,
    test_app,
    monkeypatch,
):
    login_as()
    sheets_routes = test_app["sheets_routes"]

    cred_path = test_app["temp_root"] / "google-test-creds.json"
    cred_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sheets_routes, "_get_credentials_path", lambda _uid: str(cred_path))

    def fake_create_task(coro):
        coro.close()

        class _DummyTask:
            pass

        return _DummyTask()

    monkeypatch.setattr(sheets_routes.asyncio, "create_task", fake_create_task)

    url_one = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    url_two = "https://docs.google.com/spreadsheets/d/xyz789/edit#gid=0"

    response = client.post(
        "/sheets/import-batch",
        json={
            "urls": [url_one, url_two],
            "selected_tabs": {
                url_one: ["Sheet One", "Sheet Two"],
                url_two: ["Main"],
            },
            "save_images": True,
            "search_missing": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 2

    batch = sheets_routes._batch_progress[data["batch_id"]]
    assert batch["total"] == 2
    assert batch["jobs"][0]["url"] == url_one
    assert batch["jobs"][1]["url"] == url_two


def test_google_sheet_import_persists_selected_tab_order_and_source_sheet(
    make_user,
    test_app,
    monkeypatch,
):
    user = make_user()
    sheets_routes = test_app["sheets_routes"]
    sheets_reader = importlib.import_module("app.core.sheets_reader")
    models = test_app["models"]

    class FakeSheetsReader:
        def __init__(self, _cred_path):
            pass

        def fetch_spreadsheet(self, _spreadsheet_id):
            return {
                "title": "Buying Sheet",
                "tabs": [
                    {"title": "Tab A", "headers": ["WHS Price"]},
                    {"title": "Tab B", "headers": ["WHS Price"]},
                    {"title": "Tab C", "headers": ["WHS Price"]},
                ],
            }

        def extract_items_from_tab(self, tab):
            title = tab["title"]
            return [{
                "item_code": f"SKU-{title[-1]}",
                "size": "42",
                "brand": "Brand",
                "style_name": f"Style {title[-1]}",
                "color_name": "Black",
                "gender": "Men",
                "wholesale_price": "10",
                "retail_price": "20",
                "qty_available": "5",
                "barcode": f"BAR-{title[-1]}",
                "item_group": "Shoes",
                "sap_code": f"SAP-{title[-1]}",
                "image_url": "",
                "dropbox_url": "",
                "comming_soon_qty": "",
            }]

    monkeypatch.setattr(sheets_reader, "SheetsReader", FakeSheetsReader)
    monkeypatch.setattr(sheets_reader, "extract_spreadsheet_id", lambda _url: "sheet-123")

    result = sheets_routes._do_import_sheet_sync(
        user["id"],
        "https://docs.google.com/spreadsheets/d/sheet-123/edit#gid=0",
        "unused-creds.json",
        selected_tabs=["Tab B", "Tab A"],
    )

    assert result["ok"] is True

    db = test_app["database"].SessionLocal()
    try:
        sess = db.get(models.Session, result["session_id"])
        items = (
            db.query(models.UniqueItem)
            .filter(models.UniqueItem.session_id == sess.id)
            .order_by(models.UniqueItem.id.asc())
            .all()
        )
        assert sess.config["selected_sheet_tabs"] == ["Tab A", "Tab B"]
        assert [item.source_sheet for item in items] == ["Tab A", "Tab B"]
    finally:
        db.close()


def test_google_sheet_export_keeps_selected_tabs_as_workbook_sheets(
    client,
    login_as,
    test_app,
):
    user = login_as()
    models = test_app["models"]
    db = test_app["database"].SessionLocal()
    try:
        sess = models.Session(
            user_id=user["id"],
            name="Google Multi Tab",
            source_type="google_sheets",
            source_ref="https://docs.google.com/spreadsheets/d/sheet-123/edit#gid=0",
            status="reviewing",
            total_items=2,
            searched_items=2,
        )
        sess.config = {
            "selected_sheet_tabs": ["Tab A", "Tab B", "Tab C"],
            "currency": "€",
        }
        db.add(sess)
        db.commit()
        db.refresh(sess)

        db.add_all([
            models.UniqueItem(
                session_id=sess.id,
                item_code="SKU-A",
                color_code="Black|42",
                brand="Brand",
                style_name="Style A",
                color_name="Black",
                gender="Men",
                wholesale_price=10,
                retail_price=20,
                qty_available=5,
                review_status="approved",
                source_sheet="Tab A",
                sizes=["42"],
            ),
            models.UniqueItem(
                session_id=sess.id,
                item_code="SKU-C",
                color_code="Brown|43",
                brand="Brand",
                style_name="Style C",
                color_name="Brown",
                gender="Men",
                wholesale_price=11,
                retail_price=21,
                qty_available=6,
                review_status="approved",
                source_sheet="Tab C",
                sizes=["43"],
            ),
        ])
        db.commit()
        session_id = sess.id
    finally:
        db.close()

    response = client.post(f"/generate/{session_id}", json={"save_images": False})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    excel_file = None
    for _ in range(40):
        poll_db = test_app["database"].SessionLocal()
        try:
            excel_file = (
                poll_db.query(models.GeneratedFile)
                .filter(
                    models.GeneratedFile.session_id == session_id,
                    models.GeneratedFile.filename != "images.zip",
                )
                .first()
            )
            if excel_file is not None:
                poll_db.expunge(excel_file)
                break
        finally:
            poll_db.close()
        time.sleep(0.25)

    assert excel_file is not None

    download = client.get(f"/download/{excel_file.token}")
    assert download.status_code == 200

    wb = load_workbook(io.BytesIO(download.content))
    try:
        assert wb.sheetnames == ["Tab A", "Tab B", "Tab C"]
        assert wb["Tab A"]["D3"].value == "SKU-A"
        assert wb["Tab C"]["D3"].value == "SKU-C"
        assert wb["Tab B"]["A2"].value == "Picture"
        assert wb["Tab B"].max_row == 2
    finally:
        wb.close()

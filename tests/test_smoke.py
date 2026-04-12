from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from PIL import Image


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _make_local_image(base_dir: Path, user_id: int, name: str = "sample.jpg") -> Path:
    path = base_dir / f"user_{user_id}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), color=(220, 40, 40)).save(path, format="JPEG")
    return path


def test_core_smoke_flow_upload_mapping_search_review_export_and_downloads(
    client,
    login_as,
    test_app,
    db_session,
    monkeypatch,
):
    user = login_as()

    csv_bytes = b"""Manufacturer Code,Web Description 2,Color,Size,Brand Name,WHS Price,RRP Price,FreeStock,Gender\nSKU-001,Runner,Red,42,FLENDER,10,20,5,Men\nSKU-001,Runner,Red,43,FLENDER,10,20,3,Men\nSKU-002,Cap,Blue,One,FLENDER,5,12,2,Women\n"""

    upload_resp = client.post(
        "/upload/file",
        files={"file": ("catalog.csv", csv_bytes, "text/csv")},
    )
    assert upload_resp.status_code == 200
    session_id = upload_resp.json()["session_id"]

    mapping_page = client.get(f"/mapping/{session_id}")
    assert mapping_page.status_code == 200
    assert "Column Mapping" in mapping_page.text

    mapping_resp = client.post(
        f"/mapping/{session_id}",
        data={
            "map_item_code": "Manufacturer Code",
            "map_style_name": "Web Description 2",
            "map_color_name": "Color",
            "map_size": "Size",
            "map_brand": "Brand Name",
            "map_wholesale_price": "WHS Price",
            "map_retail_price": "RRP Price",
            "map_qty_available": "FreeStock",
            "map_gender": "Gender",
        },
        follow_redirects=False,
    )
    assert mapping_resp.status_code == 302
    assert mapping_resp.headers["location"] == f"/search/{session_id}"

    models = test_app["models"]
    items = (
        db_session.query(models.UniqueItem)
        .filter(models.UniqueItem.session_id == session_id)
        .order_by(models.UniqueItem.id.asc())
        .all()
    )
    assert len(items) == 2
    assert items[0].sizes == ["42", "43"]
    assert items[1].sizes == ["One"]

    local_image_path = _make_local_image(test_app["upload_dir"], user["id"])
    image_url = f"file://{local_image_path.resolve()}"

    def fake_search_worker(session_id_arg: int, config: dict, user_id: int | None = None):
        search_routes = test_app["search_routes"]
        db = test_app["database"].SessionLocal()
        try:
            pending = (
                db.query(models.UniqueItem)
                .filter(models.UniqueItem.session_id == session_id_arg)
                .order_by(models.UniqueItem.id.asc())
                .all()
            )
            search_routes._search_progress[session_id_arg] = {
                "done": 0,
                "total": len(pending),
                "running": True,
                "current": "",
                "started_at": 0,
            }
            for index, item in enumerate(pending, start=1):
                item.candidates = [image_url]
                item.scores = {image_url: 1.0}
                item.approved_url = image_url
                item.review_status = "approved"
                item.auto_selected = True
                item.search_status = "done"
                search_routes._search_progress[session_id_arg]["done"] = index
                search_routes._search_progress[session_id_arg]["current"] = item.item_code
            sess = db.query(models.Session).filter(models.Session.id == session_id_arg).first()
            sess.status = "reviewing"
            sess.searched_items = len(pending)
            db.commit()
            search_routes._search_progress[session_id_arg]["running"] = False
        finally:
            db.close()

    real_thread_class = test_app["search_routes"].threading.Thread
    monkeypatch.setattr(test_app["search_routes"], "_run_search_background", fake_search_worker)
    monkeypatch.setattr(test_app["search_routes"].threading, "Thread", ImmediateThread)

    search_resp = client.post(
        f"/search/{session_id}/start",
        json={
            "search_mode": "web",
            "local_folder": "",
            "brand_urls": [],
            "search_notes": "smoke test",
        },
    )
    assert search_resp.status_code == 200
    assert search_resp.json()["ok"] is True

    review_state = client.get(f"/review/{session_id}/state")
    assert review_state.status_code == 200
    state = review_state.json()
    assert len(state) == 2
    for entry in state.values():
        assert entry["status"] == "approved"
        assert entry["approved_url"] == image_url
        assert entry["details_loaded"] is False

    first_entry = next(iter(state.values()))
    review_item = client.get(f"/review/{session_id}/items/{first_entry['id']}")
    assert review_item.status_code == 200
    detail = review_item.json()
    assert detail["details_loaded"] is True
    assert detail["candidates"] == [image_url]
    assert detail["approved_url"] == image_url

    monkeypatch.setattr(test_app["search_routes"].threading, "Thread", real_thread_class)

    export_resp = client.post(f"/generate/{session_id}", json={"save_images": True})
    assert export_resp.status_code == 200
    assert export_resp.json()["ok"] is True
    excel_file = None
    zip_file = None
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
            zip_file = (
                poll_db.query(models.GeneratedFile)
                .filter(
                    models.GeneratedFile.session_id == session_id,
                    models.GeneratedFile.filename == "images.zip",
                )
                .first()
            )
            if excel_file is not None and zip_file is not None:
                poll_db.expunge(excel_file)
                poll_db.expunge(zip_file)
                break
        finally:
            poll_db.close()
        time.sleep(0.25)

    assert excel_file is not None
    assert zip_file is not None
    assert Path(excel_file.file_path).exists()
    assert Path(zip_file.file_path).exists()

    generate_page = client.get(f"/generate/{session_id}")
    assert generate_page.status_code == 200
    assert "Download Excel" in generate_page.text

    excel_download = client.get(f"/download/{excel_file.token}")
    assert excel_download.status_code == 200
    assert (
        excel_download.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert excel_download.content[:2] == b"PK"

    zip_download = client.get(f"/download-zip/{zip_file.token}")
    assert zip_download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_download.content)) as archive:
        assert archive.namelist()
        assert any(name.endswith(".jpg") for name in archive.namelist())


def test_upload_file_sanitizes_names_and_avoids_overwrite(
    client,
    login_as,
    test_app,
    db_session,
):
    user = login_as()

    payload = b"Manufacturer Code\nSKU-001\n"
    for _ in range(2):
        response = client.post(
            "/upload/file",
            files={"file": ("../unsafe name.csv", payload, "text/csv")},
        )
        assert response.status_code == 200

    files = db_session.query(test_app["models"].UploadedFile).order_by(test_app["models"].UploadedFile.id.asc()).all()
    assert len(files) == 2
    stored_paths = [Path(record.file_path).resolve() for record in files]
    allowed_base = (test_app["upload_dir"] / f"user_{user['id']}").resolve()
    assert all(path.parent == allowed_base for path in stored_paths)
    assert all(".." not in str(path) for path in stored_paths)
    assert stored_paths[0] != stored_paths[1]
    assert files[0].filename == "unsafe name.csv"


def test_review_and_progress_endpoints_enforce_ownership(
    client,
    make_user,
    test_app,
):
    owner = make_user(username="owner", email="owner@flendergroup.com")
    intruder = make_user(username="intruder", email="intruder@flendergroup.com")

    models = test_app["models"]
    db = test_app["database"].SessionLocal()
    try:
        sess = models.Session(
            user_id=owner["id"],
            name="Owner Session",
            source_type="csv_upload",
            source_ref="owner.csv",
            status="reviewing",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)

        item = models.UniqueItem(
            session_id=sess.id,
            item_code="SKU-OWNER",
            review_status="approved",
            approved_url="https://example.com/image.jpg",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
    finally:
        db.close()

    owner_image = _make_local_image(test_app["upload_dir"], owner["id"], name="owner.jpg")
    test_app["search_routes"]._search_progress[sess.id] = {
        "done": 1,
        "total": 1,
        "running": False,
        "current": "SKU-OWNER",
    }

    login = client.post(
        "/login",
        data={"username": intruder["username"], "password": intruder["password"]},
        follow_redirects=False,
    )
    assert login.status_code == 302

    review_resp = client.post(
        f"/review/{sess.id}/set-url",
        json={"id": item.id, "url": "https://example.com/other.jpg"},
    )
    assert review_resp.status_code == 404

    progress_resp = client.get(f"/search/{sess.id}/progress")
    assert progress_resp.status_code == 403

    local_image_resp = client.get("/api/image/local", params={"path": str(owner_image)})
    assert local_image_resp.status_code == 403


def test_sheets_batch_progress_requires_batch_ownership(
    client,
    make_user,
    test_app,
):
    owner = make_user(username="sheet-owner", email="sheet-owner@flendergroup.com")
    intruder = make_user(username="sheet-intruder", email="sheet-intruder@flendergroup.com")
    batch_id = "batch-123"

    sheets_routes = test_app["sheets_routes"]
    sheets_routes._batch_progress[batch_id] = {
        "jobs": [],
        "running": True,
        "done": 0,
        "total": 1,
    }
    sheets_routes._user_batches[owner["id"]] = [batch_id]

    login = client.post(
        "/login",
        data={"username": intruder["username"], "password": intruder["password"]},
        follow_redirects=False,
    )
    assert login.status_code == 302

    response = client.get(f"/sheets/batch/{batch_id}/progress")
    assert response.status_code == 403

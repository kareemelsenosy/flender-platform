"""Image Sorter routes — upload → match → correct → download, end to end.

The vision passes are replaced with a stub that matches on the photo's filename,
so the whole HTTP flow is exercised without calling a model.
"""
from __future__ import annotations

import importlib
import io
import time
import zipfile

import openpyxl
import pytest
from PIL import Image


@pytest.fixture
def image_sort_routes(test_app):
    return importlib.import_module("app.routers.image_sort_routes")


@pytest.fixture(autouse=True)
def clear_runs(test_app, image_sort_routes):
    yield
    image_sort_routes._progress.clear()


def _master_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Code", "Web Description", "Web Description 2",
               "Item Group Code", "Web Color", "Item Group", "Style Code"])
    ws.append(["CTM T SS1 Black S", "CHINATOWN MARKET", "Call Me T-Shirt",
               "CTM T SS1 Black", "Black", "T-SHIRTS", "MKT-SS1"])
    ws.append(["CTM T SS1 White S", "CHINATOWN MARKET", "Call Me T-Shirt",
               "CTM T SS1 White", "White", "T-SHIRTS", "MKT-SS1"])
    ws.append(["CTM H HD9 Pink S", "CHINATOWN MARKET", "Nice Day Hoodie",
               "CTM H HD9 Pink", "Pink", "HOODYS", "MKT-HD9"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _png(color=(10, 10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buf, format="PNG")
    return buf.getvalue()


def _images_zip() -> bytes:
    """Three unnamed photos — the stub maps them by their order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MOCK_A.png", _png((10, 10, 10)))
        zf.writestr("MOCK_B.png", _png((250, 250, 250)))
        zf.writestr("MOCK_C.png", _png((250, 120, 200)))
    return buf.getvalue()


@pytest.fixture
def stub_ai(monkeypatch):
    """Stand in for both vision passes with a deterministic filename rule."""
    sorter = importlib.import_module("app.core.image_sorter")
    by_name = {
        "MOCK_A.png": ("CTM T SS1 Black", 0.95),
        "MOCK_B.png": ("CTM T SS1 Black", 0.55),   # low confidence → to check
        "MOCK_C.png": ("", 0.0),                   # unmatched
    }

    monkeypatch.setattr(sorter, "ai_available", lambda: True)
    monkeypatch.setattr(sorter, "describe_image", lambda image, cats, colors, brand="": {
        "category": "T-SHIRTS", "product_type": "t-shirt", "primary_color": "Black",
        "secondary_colors": [], "pattern": "", "visible_text": ["CALL ME"],
        "graphic": "", "view": "front", "quality": 0.8,
    })

    def fake_match(image, desc, candidates, brand=""):
        code, confidence = by_name.get(image.filename, ("", 0.0))
        return {"code": code, "runner_up": "", "confidence": confidence,
                "reason": "stubbed"}

    monkeypatch.setattr(sorter, "match_one_image", fake_match)
    return by_name


def _run_and_wait(client, timeout=15):
    resp = client.post("/image-sort/run", files=[
        ("master", ("master.xlsx", _master_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ("images", ("drop.zip", _images_zip(), "application/zip")),
    ])
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/image-sort/run/{run_id}/status").json()
        if status["status"] in ("done", "error"):
            return run_id, status
        time.sleep(0.1)
    pytest.fail("the run never finished")


# ── Page ─────────────────────────────────────────────────────────────────────

def test_page_requires_login(client):
    resp = client.get("/image-sort", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_page_renders_when_logged_in(client, login_as):
    login_as()
    resp = client.get("/image-sort")
    assert resp.status_code == 200
    assert "Image Sorter" in resp.text


def test_hub_lists_the_image_sorter_as_a_tool(client, login_as):
    login_as()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Image Sorter" in resp.text
    assert "/image-sort" in resp.text


# ── Validation ───────────────────────────────────────────────────────────────

def test_run_rejects_a_missing_master_file(client, login_as):
    login_as()
    resp = client.post("/image-sort/run", files=[
        ("images", ("drop.zip", _images_zip(), "application/zip")),
    ])
    assert resp.status_code == 400
    assert "master file" in resp.json()["error"]


def test_run_rejects_a_master_that_is_not_a_workbook(client, login_as):
    login_as()
    resp = client.post("/image-sort/run", files=[
        ("master", ("notes.txt", b"hello", "text/plain")),
        ("images", ("drop.zip", _images_zip(), "application/zip")),
    ])
    assert resp.status_code == 400
    assert ".xlsx" in resp.json()["error"]


def test_run_rejects_a_drop_with_no_photos(client, login_as):
    login_as()
    resp = client.post("/image-sort/run", data={"image_links": ""}, files=[
        ("master", ("master.xlsx", _master_bytes(), "application/octet-stream")),
    ])
    assert resp.status_code == 400
    assert "photos" in resp.json()["error"]


def test_run_requires_login(client):
    resp = client.post("/image-sort/run", files=[
        ("master", ("master.xlsx", _master_bytes(), "application/octet-stream")),
    ])
    assert resp.status_code == 401


# ── The full flow ────────────────────────────────────────────────────────────

def test_run_files_photos_into_item_group_folders(client, login_as, stub_ai):
    login_as()
    _run_id, status = _run_and_wait(client)
    assert status["status"] == "done"

    result = status["result"]
    assert result["summary"] == {
        "images": 3, "matched": 2, "unmatched": 1, "review": 2,
        "groups_total": 3, "groups_covered": 1, "groups_missing": 2,
    }

    folder = result["folders"][0]
    assert folder["code"] == "CTM T SS1 Black"
    assert folder["name"] == "Call Me T-Shirt"
    # The confident photo takes the main slot.
    main = next(p for p in folder["photos"] if p["position"] == 1)
    assert main["filename"] == "MOCK_A.png"
    assert main["output_name"] == "CTM_T_SS1_Black_1.png"

    assert [p["filename"] for p in result["unmatched"]] == ["MOCK_C.png"]
    assert {g["code"] for g in result["missing"]} == {"CTM T SS1 White", "CTM H HD9 Pink"}


def test_download_zip_has_the_folder_tree(client, login_as, stub_ai):
    login_as()
    run_id, _status = _run_and_wait(client)

    resp = client.get(f"/image-sort/download/{run_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert sorted(zf.namelist()) == [
            "CTM T SS1 Black/CTM_T_SS1_Black_1.png",
            "CTM T SS1 Black/CTM_T_SS1_Black_2.png",
        ]


def test_report_csv_covers_matched_and_missing(client, login_as, stub_ai):
    login_as()
    run_id, _status = _run_and_wait(client)

    resp = client.get(f"/image-sort/report/{run_id}")
    assert resp.status_code == 200
    assert "CTM_T_SS1_Black_1.png" in resp.text
    assert "CTM H HD9 Pink" in resp.text
    assert "no-image" in resp.text


def test_reassigning_a_photo_rebuilds_the_zip(client, login_as, stub_ai):
    login_as()
    run_id, status = _run_and_wait(client)
    orphan = status["result"]["unmatched"][0]

    resp = client.post(f"/image-sort/run/{run_id}/assign",
                       json={"index": orphan["index"], "code": "CTM H HD9 Pink"})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["summary"]["matched"] == 3
    assert result["summary"]["unmatched"] == 0
    assert {f["code"] for f in result["folders"]} == {"CTM T SS1 Black", "CTM H HD9 Pink"}

    # A hand-set match is trusted, so it is no longer in the "to check" pile.
    moved = next(p for f in result["folders"] if f["code"] == "CTM H HD9 Pink"
                 for p in f["photos"])
    assert moved["method"] == "manual"
    assert not moved["needs_review"]

    zipped = client.get(f"/image-sort/download/{run_id}")
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as zf:
        assert "CTM H HD9 Pink/CTM_H_HD9_Pink_1.png" in zf.namelist()


def test_assign_rejects_a_code_outside_the_master_file(client, login_as, stub_ai):
    login_as()
    run_id, status = _run_and_wait(client)
    index = status["result"]["unmatched"][0]["index"]

    resp = client.post(f"/image-sort/run/{run_id}/assign",
                       json={"index": index, "code": "NOT A REAL CODE"})
    assert resp.status_code == 400
    assert "not an item group" in resp.json()["error"]


def test_unassigning_moves_a_photo_back_to_unmatched(client, login_as, stub_ai):
    login_as()
    run_id, status = _run_and_wait(client)
    photo = status["result"]["folders"][0]["photos"][0]

    resp = client.post(f"/image-sort/run/{run_id}/assign",
                       json={"index": photo["index"], "code": ""})
    result = resp.json()["result"]
    assert photo["filename"] in [p["filename"] for p in result["unmatched"]]


def test_promoting_a_photo_makes_it_underscore_one(client, login_as, stub_ai):
    login_as()
    run_id, status = _run_and_wait(client)
    second = next(p for p in status["result"]["folders"][0]["photos"] if p["position"] == 2)

    resp = client.post(f"/image-sort/run/{run_id}/main", json={"index": second["index"]})
    assert resp.status_code == 200
    photos = {p["filename"]: p for p in resp.json()["result"]["folders"][0]["photos"]}
    assert photos[second["filename"]]["position"] == 1
    assert photos[second["filename"]]["output_name"] == "CTM_T_SS1_Black_1.png"


def test_photos_are_served_for_the_review_grid(client, login_as, stub_ai):
    login_as()
    run_id, status = _run_and_wait(client)
    photo = status["result"]["folders"][0]["photos"][0]

    resp = client.get(photo["thumb"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")


def test_every_photo_in_the_payload_is_actually_serveable(client, login_as, stub_ai):
    """The grid requests one URL per photo — a single 404 shows as a broken
    image, so every URL the payload hands out has to resolve."""
    login_as()
    _run_id, status = _run_and_wait(client)
    result = status["result"]
    photos = result["unmatched"] + [p for f in result["folders"] for p in f["photos"]]
    assert len(photos) == 3

    for photo in photos:
        resp = client.get(photo["thumb"])
        assert resp.status_code == 200, f"{photo['filename']} → {resp.status_code}"
        assert resp.headers["content-type"] == "image/jpeg"


def test_the_grid_is_served_thumbnails_not_the_originals(client, login_as, stub_ai, test_app):
    """Serving full-size photos is what left the review grid full of broken
    images — a season's shots are megabytes each and the server runs one
    worker. The preview must be a small JPEG, whatever the source was."""
    login_as()
    run_id, status = _run_and_wait(client)
    photo = status["result"]["folders"][0]["photos"][0]

    thumbs = test_app["output_dir"] / "image_sort" / str(run_id) / "thumbs"
    assert thumbs.is_dir(), "thumbnails should be pre-built when the run finishes"

    resp = client.get(photo["thumb"])
    assert resp.headers["content-type"] == "image/jpeg"   # the source was a PNG

    # The dimension cap is the invariant that matters. Comparing byte counts
    # would prove nothing here: these fixtures are 48px flat-colour PNGs that
    # compress smaller than a JPEG's own header.
    import io

    from PIL import Image
    with Image.open(io.BytesIO(resp.content)) as img:
        assert max(img.size) <= 420
    with Image.open(next((test_app["output_dir"] / "image_sort" / str(run_id)
                          / "source").rglob(photo["filename"]))) as src:
        assert src.format == "PNG"


def test_a_missing_thumbnail_is_rebuilt_on_demand(client, login_as, stub_ai, test_app):
    """Runs created before previews existed, and any interrupted pre-build,
    must still render rather than 404."""
    import shutil

    login_as()
    run_id, status = _run_and_wait(client)
    photo = status["result"]["folders"][0]["photos"][0]

    thumbs = test_app["output_dir"] / "image_sort" / str(run_id) / "thumbs"
    shutil.rmtree(thumbs)

    resp = client.get(photo["thumb"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert (thumbs / f"{photo['index']}.jpg").is_file()   # and it got cached


# ── Ownership ────────────────────────────────────────────────────────────────

def test_another_user_cannot_open_or_download_a_run(client, login_as, stub_ai):
    login_as()
    run_id, _status = _run_and_wait(client)
    client.post("/logout")

    login_as(username="bob", email="bob@flendergroup.com")
    assert client.get(f"/image-sort/run/{run_id}").status_code == 404
    assert client.get(f"/image-sort/run/{run_id}/photo/1").status_code == 404
    # Browser navigation must redirect, never hand back a JSON file.
    resp = client.get(f"/image-sort/download/{run_id}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/image-sort"


def test_deleting_a_run_removes_it_and_its_photos(client, login_as, stub_ai, test_app):
    login_as()
    run_id, _status = _run_and_wait(client)
    work_dir = test_app["output_dir"] / "image_sort" / str(run_id)
    assert work_dir.exists()

    assert client.post(f"/image-sort/run/{run_id}/delete").json() == {"ok": True}
    assert client.get(f"/image-sort/run/{run_id}").status_code == 404
    assert not work_dir.exists()


# ── Retention ────────────────────────────────────────────────────────────────

def test_prune_drops_orphan_photo_dirs_but_keeps_live_runs(
    client, login_as, stub_ai, test_app, image_sort_routes, db_session
):
    """A run's source photos are a gigabyte-scale liability, so anything the DB
    no longer knows about has to go."""
    login_as()
    run_id, _status = _run_and_wait(client)

    root = test_app["output_dir"] / "image_sort"
    orphan = root / "999999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "leftover.png").write_bytes(_png())
    (root / "not-a-run-id").mkdir(exist_ok=True)   # must be left alone

    removed, freed = image_sort_routes.prune_old_image_sort_dirs(db_session)
    assert removed == 1
    assert freed > 0
    assert not orphan.exists()
    assert (root / str(run_id)).exists()           # the live run survives
    assert (root / "not-a-run-id").exists()


def test_prune_drops_a_live_run_once_it_is_past_retention(
    client, login_as, stub_ai, test_app, image_sort_routes, db_session
):
    import os
    import time

    login_as()
    run_id, _status = _run_and_wait(client)
    work_dir = test_app["output_dir"] / "image_sort" / str(run_id)

    stale = time.time() - (40 * 86400)             # older than the 30-day window
    os.utime(work_dir, (stale, stale))

    removed, _freed = image_sort_routes.prune_old_image_sort_dirs(db_session)
    assert removed == 1
    assert not work_dir.exists()

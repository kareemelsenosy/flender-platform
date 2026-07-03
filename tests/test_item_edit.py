"""In-app review/edit: users can correct an item's data fields (prices,
quantities, description, Coming Soon, …) and the edits persist to the DB so
they flow straight into the next export."""
from __future__ import annotations


def _make_session_with_item(db_session, models, user_id: int):
    session = models.Session(
        user_id=user_id,
        name="Dubai Reorder.xlsx",
        source_type="google_sheet",
        status="reviewing",
        total_items=1,
    )
    db_session.add(session)
    db_session.flush()

    item = models.UniqueItem(
        session_id=session.id,
        item_code="I021756.89XX",
        color_code="BLACK|M-L|ReOrder_Dubai",
        brand="TestBrand",
        style_name="Test Bag",
        color_name="BLACK",
        gender="Men",
        wholesale_price=100.0,
        retail_price=200.0,
        qty_available=0.0,
        comming_soon_qty="2",
        review_status="approved",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return session, item


def test_edit_updates_fields_and_coerces_numbers(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    session, item = _make_session_with_item(db_session, models, user["id"])

    resp = client.post(
        f"/review/{session.id}/items/{item.id}/edit",
        json={
            "style_name": "  Corrected Bag  ",   # trimmed
            "comming_soon_qty": "3",             # the bug-era value was wrong
            "wholesale_price": "88.00 AED",      # currency stripped -> 88.0
            "retail_price": "1,250",             # thousands separator -> 1250.0
            "qty_available": "",                 # blank -> None
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["changed"]) == {
        "style_name", "comming_soon_qty", "wholesale_price",
        "retail_price", "qty_available",
    }

    db_session.expire_all()
    fresh = db_session.get(models.UniqueItem, item.id)
    assert fresh.style_name == "Corrected Bag"
    assert fresh.comming_soon_qty == "3"
    assert fresh.wholesale_price == 88.0
    assert fresh.retail_price == 1250.0
    assert fresh.qty_available is None
    # Untouched field stays put.
    assert fresh.brand == "TestBrand"


def test_edit_reflected_in_item_detail_endpoint(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    session, item = _make_session_with_item(db_session, models, user["id"])

    client.post(
        f"/review/{session.id}/items/{item.id}/edit",
        json={"comming_soon_qty": "9", "color_name": "Navy"},
    )
    detail = client.get(f"/review/{session.id}/items/{item.id}").json()
    assert detail["item"]["comming_soon_qty"] == "9"
    assert detail["item"]["color_name"] == "Navy"


def test_edit_requires_login(client, db_session, test_app, make_user):
    models = test_app["models"]
    user = make_user()  # created but NOT logged in
    session, item = _make_session_with_item(db_session, models, user["id"])
    resp = client.post(
        f"/review/{session.id}/items/{item.id}/edit",
        json={"brand": "Hacker"},
    )
    assert resp.status_code == 401


def test_edit_rejects_other_users_item(client, login_as, db_session, test_app, make_user):
    models = test_app["models"]
    owner = make_user(username="owner", email="owner@flendergroup.com")
    session, item = _make_session_with_item(db_session, models, owner["id"])

    # A different, logged-in user must not be able to edit the owner's item.
    login_as(username="intruder", email="intruder@flendergroup.com")
    resp = client.post(
        f"/review/{session.id}/items/{item.id}/edit",
        json={"brand": "Hacked"},
    )
    assert resp.status_code == 404

    db_session.expire_all()
    assert db_session.get(models.UniqueItem, item.id).brand == "TestBrand"


def test_edit_empty_payload_is_rejected(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    session, item = _make_session_with_item(db_session, models, user["id"])
    resp = client.post(f"/review/{session.id}/items/{item.id}/edit", json={})
    assert resp.status_code == 400


def test_review_page_renders_edit_ui(client, login_as, db_session, test_app):
    """The review page ships the in-app edit panel + toggle + save wiring."""
    models = test_app["models"]
    user = login_as()
    session, _item = _make_session_with_item(db_session, models, user["id"])
    resp = client.get(f"/review/{session.id}")
    assert resp.status_code == 200
    assert "edit-details-toggle" in resp.text
    assert "edit-details-panel" in resp.text
    assert "saveItemDetails" in resp.text
    assert "/items/${itemId}/edit" in resp.text

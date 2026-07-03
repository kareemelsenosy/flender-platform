"""Multi-source Step 4 — surface + resolve merge conflicts in the review/edit UI."""
from __future__ import annotations


def _merged_item(db_session, models, uid, provenance):
    sess = models.Session(user_id=uid, name="Merged", source_type="merged",
                          status="reviewing", total_items=1, searched_items=1)
    db_session.add(sess)
    db_session.flush()
    it = models.UniqueItem(
        session_id=sess.id, item_code="ACME-100", color_code="Black|M|merged|0",
        color_name="Black", style_name="Hoodie", brand="ACME", item_group="SWEATS",
        review_status="approved", search_status="done",
    )
    it.sizes = ["M"]
    it.provenance = provenance
    db_session.add(it)
    db_session.commit()
    db_session.refresh(it)
    return sess, it


_CONFLICT_PROV = {
    "style_name": {"value": "Hoodie", "source": "SAP",
                   "conflicts": [{"source": "PDF", "value": "Panelled Hoodie"}]},
    "brand": {"value": "ACME", "source": "SAP", "conflicts": []},
}


def test_item_detail_returns_provenance_and_conflict_flag(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    sess, it = _merged_item(db_session, models, user["id"], _CONFLICT_PROV)
    d = client.get(f"/review/{sess.id}/items/{it.id}").json()
    assert d["has_conflicts"] is True
    assert d["provenance"]["style_name"]["conflicts"][0]["value"] == "Panelled Hoodie"
    # A field with no disagreement is not a conflict.
    assert d["provenance"]["brand"]["conflicts"] == []


def test_state_flags_items_with_conflicts(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    sess, it = _merged_item(db_session, models, user["id"], _CONFLICT_PROV)
    state = client.get(f"/review/{sess.id}/state").json()["state"]
    entry = next(iter(state.values()))
    assert entry["has_conflicts"] is True


def test_saving_a_conflicting_field_resolves_it(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    sess, it = _merged_item(db_session, models, user["id"], _CONFLICT_PROV)

    resp = client.post(f"/review/{sess.id}/items/{it.id}/edit",
                       json={"style_name": "Panelled Hoodie"})
    assert resp.status_code == 200
    assert resp.json()["has_conflicts"] is False   # last conflict resolved

    db_session.expire_all()
    fresh = db_session.get(models.UniqueItem, it.id)
    assert fresh.style_name == "Panelled Hoodie"
    entry = fresh.provenance["style_name"]
    assert entry["resolved"] is True
    assert entry["value"] == "Panelled Hoodie"
    assert entry["source"] == "you"

    # And the state no longer flags it.
    state = client.get(f"/review/{sess.id}/state").json()["state"]
    assert next(iter(state.values()))["has_conflicts"] is False


def test_non_merged_item_has_no_conflicts(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    sess, it = _merged_item(db_session, models, user["id"], {})  # empty provenance
    d = client.get(f"/review/{sess.id}/items/{it.id}").json()
    assert d["has_conflicts"] is False
    assert d["provenance"] == {}


def test_conflict_on_non_resolvable_field_is_not_flagged(client, login_as, db_session, test_app):
    # A disagreement on image_url can't be picked in the edit panel, so it must
    # not leave a permanent ⚠. Provenance is still returned (for reference).
    models = test_app["models"]
    user = login_as()
    prov = {"image_url": {"value": "a.jpg", "source": "SAP",
                          "conflicts": [{"source": "PDF", "value": "b.jpg"}]}}
    sess, it = _merged_item(db_session, models, user["id"], prov)
    d = client.get(f"/review/{sess.id}/items/{it.id}").json()
    assert d["has_conflicts"] is False
    assert "image_url" in d["provenance"]

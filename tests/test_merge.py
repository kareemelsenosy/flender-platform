"""Multi-source Step 3 — merge engine + /merge endpoint (with provenance)."""
from __future__ import annotations

from app.core.merge import merge_sources


# ── Engine ────────────────────────────────────────────────────────────────────

def test_conflict_prefers_priority_source_and_records_the_other():
    sap = {"name": "SAP", "rows": [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M",
         "barcode": "111", "material": "cotton/elastane"},
    ]}
    pdf = {"name": "PDF", "rows": [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M",
         "barcode": "111", "material": "100% cotton"},
    ]}
    out = merge_sources([sap, pdf])  # SAP first => higher priority
    assert len(out["records"]) == 1
    rec = out["records"][0]
    assert rec["values"]["material"] == "cotton/elastane"
    prov = rec["provenance"]["material"]
    assert prov["source"] == "SAP"
    assert prov["conflicts"] == [{"source": "PDF", "value": "100% cotton"}]
    assert rec["conflict_fields"] == ["material"]
    assert set(rec["sources"]) == {"SAP", "PDF"}


def test_enrichment_fills_empty_field_without_a_conflict():
    a = {"name": "A", "rows": [{"item_code": "X1", "size": "M", "barcode": "1", "wholesale_price": "45"}]}
    b = {"name": "B", "rows": [{"item_code": "X1", "size": "M", "barcode": "1", "material": "wool"}]}
    rec = merge_sources([a, b])["records"][0]
    assert rec["values"]["wholesale_price"] == "45"
    assert rec["values"]["material"] == "wool"   # filled from B
    assert rec["conflict_fields"] == []          # filling a gap is not a conflict


def test_style_level_source_enriches_every_size_of_the_style():
    # SAP carries 2 sizes but no material; the PDF carries material at style level
    # (no size of its own) — every size must inherit it.
    sap = {"name": "SAP", "rows": [
        {"item_code": "ACME-100", "color_name": "Black", "size": "S", "barcode": "1"},
        {"item_code": "ACME-100", "color_name": "Black", "size": "M", "barcode": "2"},
    ]}
    pdf = {"name": "PDF", "rows": [
        {"item_code": "ACME-100", "color_name": "Black",
         "style_name": "Panelled Hoodie", "material": "100% cotton"},
    ]}
    out = merge_sources([sap, pdf])
    assert out["summary"]["merged_lines"] == 2   # no spurious style-only line
    for rec in out["records"]:
        assert rec["values"]["material"] == "100% cotton"
        assert rec["values"]["style_name"] == "Panelled Hoodie"
        assert set(rec["sources"]) == {"SAP", "PDF"}


def test_barcode_matches_same_line_across_sources_even_if_code_differs():
    a = {"name": "A", "rows": [{"barcode": "5000", "item_code": "AA", "size": "M", "wholesale_price": "10"}]}
    b = {"name": "B", "rows": [{"barcode": "5000", "item_code": "BB", "size": "M", "material": "silk"}]}
    out = merge_sources([a, b])
    assert out["summary"]["merged_lines"] == 1   # same EAN => one line
    assert out["records"][0]["values"]["material"] == "silk"


def test_product_only_in_one_source_is_carried_through():
    a = {"name": "A", "rows": [{"item_code": "AA", "size": "M", "barcode": "1"}]}
    b = {"name": "B", "rows": [{"item_code": "ZZ", "size": "L", "barcode": "9", "style_name": "Solo"}]}
    out = merge_sources([a, b])
    codes = {r["values"].get("item_code") for r in out["records"]}
    assert codes == {"AA", "ZZ"}
    assert out["summary"]["merged_lines"] == 2


def test_summary_counts():
    # 'material' is a style-level attribute, so B's disagreement applies to the
    # whole style — both sizes carry the conflict (resolving it fixes the style).
    a = {"name": "A", "rows": [
        {"item_code": "AA", "size": "M", "barcode": "1", "material": "x"},
        {"item_code": "AA", "size": "L", "barcode": "2", "material": "x"},
    ]}
    b = {"name": "B", "rows": [
        {"item_code": "AA", "size": "M", "barcode": "1", "material": "y"},
    ]}
    s = merge_sources([a, b])["summary"]
    assert s["input_rows"] == 3
    assert s["merged_lines"] == 2
    assert s["styles"] == 1
    assert s["lines_from_multiple_sources"] == 2
    assert s["conflict_lines"] == 2


# ── Endpoint ──────────────────────────────────────────────────────────────────

def _session_with_items(db_session, models, uid, name, rows):
    sess = models.Session(user_id=uid, name=name, source_type="excel_upload",
                          status="completed", total_items=len(rows))
    db_session.add(sess)
    db_session.flush()
    for i, r in enumerate(rows):
        it = models.UniqueItem(
            session_id=sess.id, item_code=r["item_code"],
            color_code=f"{r.get('color_name','')}|{r.get('size','')}|{name}",
            color_name=r.get("color_name", ""), style_name=r.get("style_name", ""),
            barcode=r.get("barcode", ""), item_group=r.get("item_group", ""),
            wholesale_price=r.get("wholesale_price"), source_order=i,
        )
        it.sizes = [r["size"]] if r.get("size") else []
        db_session.add(it)
    db_session.commit()
    return sess


def test_merge_endpoint_creates_enriched_session_with_provenance(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    sap = _session_with_items(db_session, models, user["id"], "SAP Export", [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M", "barcode": "111", "item_group": "cotton/elastane"},
    ])
    pdf = _session_with_items(db_session, models, user["id"], "ACME PDF", [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M", "barcode": "111",
         "style_name": "Panelled Hoodie", "item_group": "100% cotton"},
    ])

    resp = client.post("/merge", json={"session_ids": [sap.id, pdf.id]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["summary"]["merged_lines"] == 1
    assert body["summary"]["lines_from_multiple_sources"] == 1

    merged = db_session.get(models.Session, body["session_id"])
    assert merged.source_type == "merged"
    items = db_session.query(models.UniqueItem).filter_by(session_id=merged.id).all()
    assert len(items) == 1
    it = items[0]
    # Enriched: description filled from the PDF.
    assert it.style_name == "Panelled Hoodie"
    # Conflict on item_group: SAP (priority) wins, PDF recorded in provenance.
    prov = it.provenance
    assert prov["item_group"]["source"] == "SAP Export"
    assert prov["item_group"]["conflicts"] == [{"source": "ACME PDF", "value": "100% cotton"}]


def test_merge_endpoint_enriches_every_size_with_style_level_image(client, login_as, db_session, test_app):
    """A style-level image from a secondary source must land on every size."""
    models = test_app["models"]
    user = login_as()
    sap = _session_with_items(db_session, models, user["id"], "SAP", [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M", "barcode": "1"},
        {"item_code": "ACME-100", "color_name": "Black", "size": "L", "barcode": "2"},
    ])
    # PDF carries the photo at style level (no size of its own).
    pdf = models.Session(user_id=user["id"], name="PDF", source_type="pdf_upload",
                         status="completed", total_items=1)
    db_session.add(pdf)
    db_session.flush()
    pit = models.UniqueItem(session_id=pdf.id, item_code="ACME-100",
                            color_code="Black||PDF", color_name="Black",
                            approved_url="https://img/hoodie.jpg")
    pit.sizes = []
    db_session.add(pit)
    db_session.commit()

    resp = client.post("/merge", json={"session_ids": [sap.id, pdf.id]})
    assert resp.status_code == 200, resp.text
    merged_id = resp.json()["session_id"]
    items = db_session.query(models.UniqueItem).filter_by(session_id=merged_id).all()
    assert len(items) == 2
    # Both sizes inherited the style-level image.
    assert all(it.approved_url == "https://img/hoodie.jpg" for it in items)
    assert {it.sizes[0] for it in items} == {"M", "L"}


def test_merge_requires_login(client, db_session, test_app, make_user):
    models = test_app["models"]
    user = make_user()
    s1 = _session_with_items(db_session, models, user["id"], "A", [{"item_code": "X", "size": "M"}])
    s2 = _session_with_items(db_session, models, user["id"], "B", [{"item_code": "X", "size": "M"}])
    resp = client.post("/merge", json={"session_ids": [s1.id, s2.id]})
    assert resp.status_code == 401


def test_merge_needs_at_least_two_sources(client, login_as, db_session, test_app):
    models = test_app["models"]
    user = login_as()
    s1 = _session_with_items(db_session, models, user["id"], "A", [{"item_code": "X", "size": "M"}])
    resp = client.post("/merge", json={"session_ids": [s1.id]})
    assert resp.status_code == 400


def test_merge_rejects_other_users_session(client, login_as, db_session, test_app, make_user):
    models = test_app["models"]
    owner = make_user(username="owner2", email="owner2@flendergroup.com")
    s1 = _session_with_items(db_session, models, owner["id"], "A", [{"item_code": "X", "size": "M"}])
    s2 = _session_with_items(db_session, models, owner["id"], "B", [{"item_code": "X", "size": "M"}])
    login_as(username="intruder2", email="intruder2@flendergroup.com")
    resp = client.post("/merge", json={"session_ids": [s1.id, s2.id]})
    assert resp.status_code == 404

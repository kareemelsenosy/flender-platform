"""Attributes and Images as packages, and delivery to the SAP folder."""
from __future__ import annotations

import os

import pytest

from app.core.collection_attributes import (
    CONFIDENT, build_attribute_package, styles_from_rows,
)
from app.core.collection_images import (
    APPROVED, MISSING, OPEN_STATUSES, REVIEW_REQUIRED, STATUS_LABELS,
    build_image_package,
)


def _row(style="A", colour="Black", **kw):
    base = {"Style Number": style, "Color": colour, "Name": "Detroit Jacket",
            "Item Group": "JACKETS", "Material": "100% Cotton", "Sex": "Men"}
    base.update(kw)
    return base


# ── Attributes ───────────────────────────────────────────────────────────────

def test_attributes_are_decided_per_style_not_per_row():
    """A jacket is a work jacket in every colour and size."""
    rows = [_row("A", "Black"), _row("A", "Navy"), _row("A", "Olive"),
            _row("B", "Black", Name="Logo Tee", **{"Item Group": "T-SHIRTS"})]
    styles = styles_from_rows(rows, brand="Carhartt WIP", season="SS27")
    assert {s["style_code"] for s in styles} == {"A", "B"}
    assert next(s for s in styles if s["style_code"] == "A")["rows"] == 3


def test_a_style_takes_its_master_group_from_the_item_group():
    styles = styles_from_rows([_row(**{"Item Group": "HOODYS"})])
    assert styles[0]["master_group"] == "SWEATS"


def test_a_later_row_fills_a_description_the_first_row_lacked():
    rows = [_row("A", "Black", Material=""),
            _row("A", "Navy", Material="Organic Cotton")]
    assert styles_from_rows(rows)[0]["material"] == "Organic Cotton"


def test_pandas_missing_values_never_become_attribute_text():
    styles = styles_from_rows([_row(Material=float("nan"))])
    assert styles[0]["material"] == ""


def test_a_confident_assignment_raises_nothing():
    styles = styles_from_rows([_row()])
    out = build_attribute_package(
        styles, lambda s: {"product_type": "WJK", "confidence": 0.95,
                           "FABRIC": None, "FIT": "Regular", "STYLE": [], "WEIGHT": None})
    assert out["exceptions"] == []
    assert out["rows"][0]["product_type"] == "WJK"
    assert out["rows"][0]["FIT"] == "Regular"


def test_a_low_confidence_assignment_asks_for_confirmation():
    styles = styles_from_rows([_row()])
    out = build_attribute_package(
        styles, lambda s: {"product_type": "WJK", "confidence": CONFIDENT - 0.2})
    assert out["exceptions"][0]["severity"] == "warning"
    assert "confidence" in out["exceptions"][0]["reason"]


def test_no_product_type_is_a_decision_not_a_blank():
    styles = styles_from_rows([_row()])
    out = build_attribute_package(styles, lambda s: {"product_type": None})
    assert out["exceptions"][0]["severity"] == "manual_review"


def test_one_failing_style_does_not_sink_the_whole_run():
    styles = styles_from_rows([_row("A"), _row("B", Name="Tee")])

    def flaky(style):
        if style["style_code"] == "A":
            raise RuntimeError("model unavailable")
        return {"product_type": "TSH", "confidence": 0.9}

    out = build_attribute_package(styles, flaky)
    assert len(out["rows"]) == 1                       # B still assigned
    assert any(e["severity"] == "critical" for e in out["exceptions"])


def test_the_style_list_is_flattened_for_a_spreadsheet_cell():
    styles = styles_from_rows([_row()])
    out = build_attribute_package(
        styles, lambda s: {"product_type": "WJK", "confidence": 0.9,
                           "STYLE": ["Workwear", "Heritage"]})
    assert out["rows"][0]["STYLE"] == "Workwear, Heritage"


# ── Images ───────────────────────────────────────────────────────────────────

class _Ref:
    def __init__(self, with_images):
        self._with = with_images

    def known_values(self, *, style="", colour="", barcode=""):
        return {"has_images": style in self._with, "item_group": f"G-{style}"}


def test_a_product_sap_already_has_images_for_is_settled():
    out = build_image_package([_row("A")], reference=_Ref({"A"}))
    assert out["rows"][0]["status"] == APPROVED
    assert out["exceptions"] == []


def test_a_product_with_nothing_anywhere_is_missing():
    out = build_image_package([_row("A")], reference=_Ref(set()))
    assert out["rows"][0]["status"] == MISSING
    # Reported as a gap to work on, not put to a reviewer as a question —
    # a collection with no images yet would otherwise open 1,500 decisions.
    assert out["exceptions"][0]["severity"] == "warning"


def test_a_product_with_a_candidate_is_a_real_decision():
    out = build_image_package([_row("A")], reference=_Ref(set()),
                              supplied_files=["A_black_1.jpg"])
    assert out["exceptions"][0]["severity"] == "manual_review"


def test_a_photo_in_the_supplier_package_needs_review_not_approval():
    out = build_image_package([_row("A")], reference=_Ref(set()),
                              supplied_files=["A_black_1.jpg"])
    assert out["rows"][0]["status"] == REVIEW_REQUIRED


def test_missing_is_not_the_only_status():
    """An image nobody will ever get differs from one the supplier owes us."""
    assert OPEN_STATUSES < set(STATUS_LABELS)
    assert "never_expected" in STATUS_LABELS
    assert "not_available" in STATUS_LABELS


def test_a_human_decision_overrides_what_was_inferred():
    out = build_image_package(
        [_row("A")], reference=_Ref(set()),
        decisions={"image_status::A|Black": "never_expected"})
    assert out["rows"][0]["status"] == "never_expected"
    assert out["exceptions"] == []                     # settled, no longer work


def test_coverage_is_reported_per_style_colour():
    rows = [_row("A", "Black"), _row("A", "Black"), _row("B", "Navy")]
    out = build_image_package(rows, reference=_Ref({"A"}))
    assert out["summary"]["products"] == 2             # deduped
    assert out["summary"]["with_images"] == 1
    assert out["summary"]["coverage_pct"] == 50.0


# ── Delivery ─────────────────────────────────────────────────────────────────

def _make_run(db, job_id, kind="temp", status="ready", path=None):
    from app.models import PackageRun
    run = db.query(PackageRun).filter(PackageRun.job_id == job_id,
                                      PackageRun.kind == kind).first()
    run.status = status
    if path:
        run.file_path = path
    db.commit()
    db.refresh(run)
    return run


@pytest.fixture
def delivered_setup(client, login_as, test_app, monkeypatch, tmp_path):
    import io
    import pandas as pd
    import app.routers.intake_routes as ir
    from app.services import packages as pkg
    monkeypatch.setattr(ir, "INTAKE_API_KEY", "k")
    monkeypatch.setenv("DELIVERY_ROOT", str(tmp_path / "dropbox"))
    login_as()
    buf = io.BytesIO()
    pd.DataFrame([{"Style Number": "A", "Color": "BLACK", "Name": "Tee",
                   "Subcategory": "JACKET", "Season": "SS27",
                   "Barcode": "1", "Size 1": "S"}]).to_excel(buf, index=False)
    jid = client.post("/api/intake/email", headers={"X-Intake-Key": "k"},
                      data={"subject": "Hiking Patrol SS27"},
                      files=[("files", ("SS27_ORDER_SHEET.xlsx", buf.getvalue()))]
                      ).json()["job_id"]
    client.post(f"/collections/{jid}/packages/temp/generate")
    return jid, tmp_path / "dropbox"


def test_an_unapproved_package_is_not_delivered(client, delivered_setup):
    jid, root = delivered_setup
    client.post(f"/collections/{jid}/packages/temp/deliver")
    assert not root.exists() or not any(root.rglob("*.xlsx"))


def test_an_approved_package_lands_in_the_sap_folder(client, delivered_setup, test_app):
    jid, root = delivered_setup
    db = test_app["database"].SessionLocal()
    _make_run(db, jid, status="approved")
    db.close()

    client.post(f"/collections/{jid}/packages/temp/deliver")
    written = list((root / "temp").glob("*.xlsx"))
    assert len(written) == 1

    db = test_app["database"].SessionLocal()
    from app.models import PackageRun
    run = db.query(PackageRun).filter(PackageRun.job_id == jid).first()
    assert run.status == "delivered"
    assert run.summary.get("delivered_to")
    db.close()


def test_delivering_twice_never_overwrites_what_sap_may_be_reading(
        client, delivered_setup, test_app):
    jid, root = delivered_setup
    from app.models import PackageRun
    from app.services import packages as pkg
    db = test_app["database"].SessionLocal()
    run = _make_run(db, jid, status="approved")
    pkg.deliver(db, run)
    run.status = "approved"
    db.commit()
    import time
    time.sleep(1.1)                       # the stamp has second resolution
    pkg.deliver(db, run)
    db.close()
    assert len(list((root / "temp").glob("*.xlsx"))) == 2

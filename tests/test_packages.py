"""Output packages: generate, decide, approve, download.

The behaviour that matters is that a reviewer answers questions, not rows.
A Carhartt run raises over a thousand base-colour exceptions across a few
dozen distinct colour names; the screen must ask once per name.
"""
from __future__ import annotations

import io

import pytest

from app.services.packages import (
    KINDS, PRODUCT_CREATION, TEMP, decision_key, group_exceptions,
)


def _exc(field, subject, sku, severity="manual_review"):
    return {"field": field, "sku": sku, "severity": severity,
            "reason": f"'{subject}' has never been classified"}


# ── Grouping ─────────────────────────────────────────────────────────────────

def test_many_rows_of_one_colour_become_one_question():
    rows = [_exc("BASE COLOR", "Pumice", f"S{i}") for i in range(900)]
    grouped = group_exceptions(rows)
    assert len(grouped) == 1
    assert grouped[0]["subject"] == "Pumice"
    assert grouped[0]["rows"] == 900


def test_distinct_subjects_stay_distinct():
    rows = [_exc("BASE COLOR", "Pumice", "A"), _exc("BASE COLOR", "Styx", "B")]
    assert {g["subject"] for g in group_exceptions(rows)} == {"Pumice", "Styx"}


def test_the_same_word_under_two_fields_is_two_questions():
    rows = [_exc("BASE COLOR", "TOP", "A"), _exc("U_ItmsGrpCod", "TOP", "B")]
    assert len(group_exceptions(rows)) == 2


def test_questions_are_ordered_critical_first_then_by_reach():
    rows = ([_exc("BASE COLOR", "Small", "A")]
            + [_exc("BASE COLOR", "Big", f"B{i}") for i in range(50)]
            + [_exc("Barcode", "Missing", "C", severity="critical")])
    order = [g["severity"] for g in group_exceptions(rows)]
    assert order[0] == "critical"
    grouped = group_exceptions(rows)
    reviews = [g for g in grouped if g["severity"] == "manual_review"]
    assert reviews[0]["subject"] == "Big"      # widest reach first


def test_one_critical_row_makes_the_whole_question_critical():
    rows = [_exc("Barcode", "X", "A"), _exc("Barcode", "X", "B", severity="critical")]
    assert group_exceptions(rows)[0]["severity"] == "critical"


def test_only_a_few_example_skus_are_carried():
    rows = [_exc("BASE COLOR", "Pumice", f"S{i}") for i in range(100)]
    assert len(group_exceptions(rows)[0]["skus"]) <= 5


def test_decision_key_is_field_and_subject():
    assert decision_key("BASE COLOR", "Pumice") == "BASE COLOR::Pumice"


# ── Through the app ──────────────────────────────────────────────────────────

def _order_sheet_bytes():
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame([
        {"Style Number": "HP0127001", "Color": "COYOTE BROWN",
         "Name": "EDGE LT SOFTSHELL JACKET", "Subcategory": "JACKET",
         "Season": "SS27", "Barcode": "111", "Size 1": "S", "Size 2": "M"},
        {"Style Number": "HP0127002", "Color": "PUMICE",
         "Name": "TRAIL PANT", "Subcategory": "JACKET",
         "Season": "SS27", "Barcode": "222", "Size 1": "S", "Size 2": "M"},
    ]).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def collection(client, login_as, test_app, monkeypatch):
    """A collection with one order sheet, created through the real intake."""
    import app.routers.intake_routes as ir
    monkeypatch.setattr(ir, "INTAKE_API_KEY", "k")
    login_as()
    r = client.post("/api/intake/email", headers={"X-Intake-Key": "k"},
                    data={"subject": "Hiking Patrol SS27"},
                    files=[("files", ("SS27_ORDER_SHEET.xlsx", _order_sheet_bytes()))])
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_generating_a_package_creates_rows_and_questions(client, collection):
    r = client.post(f"/collections/{collection}/packages/{TEMP}/generate",
                    follow_redirects=True)
    assert r.status_code == 200
    # Two order rows, two sizes each.
    assert "4" in r.text
    assert "decision" in r.text.lower()


def test_the_screen_asks_per_colour_not_per_row(client, collection):
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    page = client.get(f"/collections/{collection}/packages/{TEMP}").text
    # PUMICE is unknown and covers 2 rows, but it is one question.
    assert "Pumice" in page or "PUMICE" in page
    assert "2 rows" in page


def test_answering_a_question_removes_it(client, collection):
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    from app.database import SessionLocal
    from app.models import PackageRun
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    key = next(q["key"] for q in run.exceptions if q["field"] == "U_BaseColor")
    db.close()

    client.post(f"/collections/{collection}/packages/{TEMP}/decide",
                data={"key": key, "value": "Grey"}, follow_redirects=False)
    page = client.get(f"/collections/{collection}/packages/{TEMP}").text
    assert "answered" in page.lower()
    assert "Grey" in page


def test_an_answer_applies_to_every_row_it_covers(client, collection):
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    from app.database import SessionLocal
    from app.models import PackageRun
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    before = run.review_count
    key = next(q["key"] for q in run.exceptions if q["field"] == "U_BaseColor")
    db.close()

    client.post(f"/collections/{collection}/packages/{TEMP}/decide",
                data={"key": key, "value": "Grey"})
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    assert run.review_count < before
    db.close()


def test_undo_puts_the_question_back(client, collection):
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    from app.database import SessionLocal
    from app.models import PackageRun
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    key = next(q["key"] for q in run.exceptions if q["field"] == "U_BaseColor")
    db.close()

    client.post(f"/collections/{collection}/packages/{TEMP}/decide",
                data={"key": key, "value": "Grey"})
    client.post(f"/collections/{collection}/packages/{TEMP}/decide",
                data={"key": key, "value": ""})
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    assert key not in run.decisions
    db.close()


def test_the_generated_sheet_can_be_downloaded(client, collection):
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    r = client.get(f"/collections/{collection}/packages/{TEMP}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats")


def test_approval_is_refused_while_a_critical_error_is_open(client, collection):
    """Approval protects SAP; a critical error must block it."""
    from app.database import SessionLocal
    from app.models import PackageRun
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    run.critical_count = 1
    db.commit()
    db.close()

    client.post(f"/collections/{collection}/packages/{TEMP}/approve")
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    assert run.status != "approved"
    db.close()


def test_a_clean_package_can_be_approved(client, collection):
    from app.database import SessionLocal
    from app.models import PackageRun
    client.post(f"/collections/{collection}/packages/{TEMP}/generate")
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    run.critical_count = 0
    db.commit()
    db.close()

    client.post(f"/collections/{collection}/packages/{TEMP}/approve")
    db = SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == collection).first()
    assert run.status == "approved" and run.approved_at is not None
    db.close()


def test_packages_appear_on_the_collection_page(client, collection):
    page = client.get(f"/collections/{collection}").text
    for label in KINDS.values():
        assert label in page


def test_the_brand_sap_sheet_can_be_connected(client, collection):
    """A pasted Google Sheets URL is accepted, not just a bare id."""
    from app.database import SessionLocal
    from app.models import BrandConfig
    client.post(f"/collections/{collection}/brand-config",
                data={"sap_sheet_id": "https://docs.google.com/spreadsheets/d/ABC123/edit#gid=0",
                      "earliest_ship_date": "2027-01-15"})
    db = SessionLocal()
    cfg = db.query(BrandConfig).first()
    assert cfg.sap_sheet_id == "ABC123"
    assert cfg.earliest_ship_date == "2027-01-15"
    db.close()


def test_an_unknown_package_kind_is_refused(client, collection):
    r = client.post(f"/collections/{collection}/packages/nonsense/generate",
                    follow_redirects=False)
    assert r.status_code == 302


# ── Regressions found while wiring this up ───────────────────────────────────

def test_answering_one_colour_does_not_raise_another():
    """The vocabulary must grow with each answer, never shrink to it.

    Narrowing it to the values chosen so far made colours the rules had
    already resolved unresolvable, so every answer raised a fresh question
    and the queue never emptied.
    """
    from app.core.temp_template import build_temp_sheet
    rows = [{"Style Number": "A", "Color": "COYOTE BROWN", "Name": "Jacket",
             "Subcategory": "JACKET", "Season": "SS27", "Size 1": "S"},
            {"Style Number": "B", "Color": "PUMICE", "Name": "Pant",
             "Subcategory": "JACKET", "Season": "SS27", "Size 1": "S"}]
    groups = {"JACKET": ["JACKETS"]}

    before = build_temp_sheet(rows, brand="HP", season="SS27", group_map=groups)
    unresolved = {e["subject"] for e in before["exceptions"]
                  if e["field"] == "U_BaseColor"}
    assert unresolved == {"PUMICE"}          # COYOTE BROWN resolves by rule

    from app.core.base_color import DEFAULT_VOCAB
    vocab = sorted(set(DEFAULT_VOCAB) | {"Grey"})
    after = build_temp_sheet(rows, brand="HP", season="SS27", group_map=groups,
                             vocab=vocab, colour_lookup={"pumice": "Grey"})
    assert not [e for e in after["exceptions"] if e["field"] == "U_BaseColor"]


def test_a_question_subject_is_the_thing_decided_not_a_slice_of_prose():
    """Subjects were parsed out of the reason text and came back mangled —
    "the supplier's ship date" yielded a subject of "s ship date)"."""
    from app.core.temp_template import build_temp_sheet
    out = build_temp_sheet(
        [{"Style Number": "A", "Color": "BLACK", "Name": "Tee",
          "Subcategory": "JACKET", "Season": "SS27", "Size 1": "S"}],
        brand="HP", season="SS27", group_map={"JACKET": ["JACKETS"]})
    subjects = {e["field"]: e.get("subject") for e in out["exceptions"]}
    assert subjects.get("U_ESDate") == "this collection"
    assert "'" not in str(subjects.get("U_ESDate"))


def test_two_styles_sharing_an_unknown_size_run_are_one_question():
    from app.core.temp_template import build_temp_sheet
    rows = [{"Style Number": s, "Color": "BLACK", "Name": "Tee",
             "Subcategory": "JACKET", "Season": "SS27",
             "Size 1": "XS", "Size 2": "XXXL"} for s in ("A", "B")]
    out = build_temp_sheet(rows, brand="HP", season="SS27",
                           group_map={"JACKET": ["JACKETS"]})
    runs = {e["subject"] for e in out["exceptions"]
            if e["field"] == "U_def_size_list_code"}
    assert len(runs) == 1                    # the run, not the style


def test_flenders_own_brands_are_all_recognised():
    """Ten of the fourteen on the season tracker were invisible."""
    from app.core.intake import detect_brand
    for brand in ("Carhartt WIP", "HUF", "Gramicci", "Arte", "ACL", "Market",
                  "TwoJeys", "Thisisneverthat", "Komono", "Edwin",
                  "Hiking Patrol", "Rip n Dip", "Dime", "Butter Goods"):
        assert detect_brand(brand), f"{brand} is not registered"


def test_a_collection_with_no_detected_brand_can_still_be_named(client, collection):
    from app.database import SessionLocal
    from app.models import CollectionJob
    client.post(f"/collections/{collection}/identity",
                data={"brand": "Liberaiders", "season": "FW27"})
    db = SessionLocal()
    job = db.get(CollectionJob, collection)
    assert job.brand == "Liberaiders" and job.season == "FW27"
    db.close()

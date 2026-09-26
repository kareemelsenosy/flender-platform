"""Version comparison, B2B tracking and the status emails."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from app.core.version_diff import compare_versions, describe


def _row(style="A", colour="Black", size="M", barcode="1",
         price="50", name="Detroit Jacket"):
    return {"Style Number": style, "Color": colour, "Size": size,
            "Barcode": barcode, "Wholesale Price": price, "Name": name}


# ── Version comparison ───────────────────────────────────────────────────────

def test_a_new_style_is_added():
    diff = compare_versions([_row("A")], [_row("A"), _row("B")])
    assert diff["summary"]["added"] == 1
    assert diff["summary"]["removed"] == 0
    assert "B" in diff["added"][0]["label"]


def test_a_dropped_style_is_removed():
    diff = compare_versions([_row("A"), _row("B")], [_row("A")])
    assert diff["summary"]["removed"] == 1


def test_a_colour_is_its_own_product():
    """SAP creates an item per colour, so a new colour is a new product."""
    diff = compare_versions([_row("A", "Black")],
                            [_row("A", "Black"), _row("A", "Navy")])
    assert diff["summary"]["added"] == 1


def test_an_identical_file_reports_nothing():
    rows = [_row("A"), _row("B")]
    diff = compare_versions(rows, list(rows))
    assert diff["summary"]["changed"] == 0
    assert diff["summary"]["added"] == diff["summary"]["removed"] == 0
    assert "no changes" in describe(diff)[0]


def test_a_price_move_is_a_change_not_a_replacement():
    diff = compare_versions([_row("A", price="50")], [_row("A", price="65")])
    assert diff["summary"]["changed"] == 1
    assert diff["summary"]["added"] == diff["summary"]["removed"] == 0
    change = diff["changed"][0]["changes"][0]
    assert change["field"] == "wholesale_price"
    assert change["was"] == "50" and change["now"] == "65"


def test_a_barcode_change_is_reported():
    diff = compare_versions([_row("A", barcode="111")],
                            [_row("A", barcode="222")])
    assert any(c["field"] == "barcode" for c in diff["changed"][0]["changes"])


def test_losing_a_size_is_a_change_to_the_product_not_a_removal():
    before = [_row("A", size="S"), _row("A", size="M"), _row("A", size="L")]
    after = [_row("A", size="S"), _row("A", size="M")]
    diff = compare_versions(before, after)
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["changed"] == 1
    assert any(c["field"] == "size_range" for c in diff["changed"][0]["changes"])


def test_removals_are_described_as_needing_care():
    """Products already ordered must not simply disappear."""
    diff = compare_versions([_row("A"), _row("B")], [_row("A")])
    assert any("before deactivating" in line for line in describe(diff))


def test_quantity_alone_is_not_treated_as_a_change():
    a = _row("A"); a["Qty"] = "10"
    b = _row("A"); b["Qty"] = "40"
    assert compare_versions([a], [b])["summary"]["changed"] == 0


# ── Through the app ──────────────────────────────────────────────────────────

def _sheet(rows):
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def two_versions(client, login_as, monkeypatch):
    import app.routers.intake_routes as ir
    monkeypatch.setattr(ir, "INTAKE_API_KEY", "k")
    monkeypatch.setattr(ir, "_connect_sap_history", lambda db, job: None)
    login_as()
    first = client.post("/api/intake/email", headers={"X-Intake-Key": "k"},
                        data={"subject": "Carhartt WIP SS27"},
                        files=[("files", ("v1_ORDER_SHEET.xlsx",
                                          _sheet([_row("A"), _row("B")])))]).json()
    second = client.post("/api/intake/email", headers={"X-Intake-Key": "k"},
                         data={"subject": "Carhartt WIP SS27"},
                         files=[("files", ("v2_ORDER_SHEET.xlsx",
                                           _sheet([_row("A", price="65"),
                                                   _row("C")])))]).json()
    return first["job_id"], second["job_id"]


def test_comparing_two_versions_stores_the_report(client, two_versions, test_app):
    first, second = two_versions
    client.post(f"/collections/{second}/compare", data={"previous_id": first})
    from app.models import CollectionJob
    db = test_app["database"].SessionLocal()
    job = db.get(CollectionJob, second)
    summary = job.change_report["summary"]
    assert job.parent_job_id == first
    assert summary["added"] == 1        # C
    assert summary["removed"] == 1      # B
    assert summary["changed"] == 1      # A's price
    db.close()


def test_the_change_report_shows_on_the_collection_page(client, two_versions):
    first, second = two_versions
    client.post(f"/collections/{second}/compare", data={"previous_id": first})
    page = client.get(f"/collections/{second}").text
    assert "Changes since" in page


# ── B2B ──────────────────────────────────────────────────────────────────────

def test_b2b_starts_unpublished(client, two_versions):
    _, second = two_versions
    assert "Not uploaded" in client.get(f"/collections/{second}").text


def test_b2b_can_be_marked_live(client, two_versions, test_app):
    _, second = two_versions
    client.post(f"/collections/{second}/b2b",
                data={"uploaded": "yes", "note": "published by Joshua"})
    from app.models import CollectionJob
    db = test_app["database"].SessionLocal()
    job = db.get(CollectionJob, second)
    assert job.b2b_uploaded is True and job.b2b_uploaded_at is not None
    assert job.b2b_note == "published by Joshua"
    db.close()


def test_b2b_can_be_taken_back_down(client, two_versions, test_app):
    _, second = two_versions
    client.post(f"/collections/{second}/b2b", data={"uploaded": "yes"})
    client.post(f"/collections/{second}/b2b", data={"uploaded": "no"})
    from app.models import CollectionJob
    db = test_app["database"].SessionLocal()
    job = db.get(CollectionJob, second)
    assert job.b2b_uploaded is False and job.b2b_uploaded_at is None
    db.close()


# ── Status emails ────────────────────────────────────────────────────────────

def test_intake_email_says_what_arrived_and_what_is_missing(monkeypatch):
    from app.services import status_email
    sent = {}

    def capture(to, subject, text, html):
        sent.update(to=to, subject=subject, text=text)
        return True

    monkeypatch.setattr("app.routers.auth_routes._send_email", capture)

    class Job:
        id, label = 1, "Carhartt WIP SS27"
        report = {"totals": {"styles": 508, "colour_styles": 1537, "skus": 10778},
                  "missing": ["price_list"], "warnings": ["42 lines without a barcode"]}

    assert status_email.intake_complete("hl@flendergroup.com", Job()) is True
    assert "508" in sent["text"] and "10,778" in sent["text"]
    assert "Price list" in sent["text"]
    assert "barcode" in sent["text"]


def test_reconcile_email_names_what_sap_is_missing(monkeypatch):
    from app.services import status_email
    sent = {}
    monkeypatch.setattr("app.routers.auth_routes._send_email",
                        lambda to, s, t, h: sent.update(subject=s, text=t) or True)

    class Job:
        id, label = 2, "Hiking Patrol SS27"

    class Run:
        kind, row_count = "temp", 272
        found_count, expected_count = 58, 60
        missing_in_sap = ["HP0127001 Coyote Brown", "HP0127002 Taupe"]

    status_email.reconciled("hl@flendergroup.com", Job(), Run(), "TEMP")
    assert "2 missing in SAP" in sent["subject"]
    assert "58 of 60" in sent["text"]
    assert "HP0127001" in sent["text"]


def test_a_mail_failure_never_raises(monkeypatch):
    from app.services import status_email

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("app.routers.auth_routes._send_email", boom)

    class Job:
        id, label, report = 1, "X", {}

    assert status_email.intake_complete("a@b.com", Job()) is False


def test_no_email_address_means_no_attempt(monkeypatch):
    from app.services import status_email
    monkeypatch.setattr("app.routers.auth_routes._send_email",
                        lambda *a, **k: pytest.fail("should not have sent"))

    class Job:
        id, label, report = 1, "X", {}

    assert status_email.intake_complete("", Job()) is False

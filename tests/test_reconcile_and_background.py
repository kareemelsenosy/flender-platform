"""Background packages, SAP reconciliation, sheet discovery, delivery folder."""
from __future__ import annotations

import io
import time

import pandas as pd
import pytest

from app.services import packages as pkg


def _sheet_bytes(rows=2):
    buf = io.BytesIO()
    pd.DataFrame([{"Style Number": f"S{i}", "Color": "BLACK", "Name": "Tee",
                   "Subcategory": "JACKET", "Season": "SS27",
                   "Barcode": str(i), "Size 1": "S", "Size 2": "M"}
                  for i in range(rows)]).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def collection(client, login_as, monkeypatch, tmp_path):
    import app.routers.intake_routes as ir
    monkeypatch.setattr(ir, "INTAKE_API_KEY", "k")
    monkeypatch.setenv("DELIVERY_ROOT", str(tmp_path / "dropbox"))
    login_as()
    jid = client.post("/api/intake/email", headers={"X-Intake-Key": "k"},
                      data={"subject": "Hiking Patrol SS27"},
                      files=[("files", ("SS27_ORDER_SHEET.xlsx", _sheet_bytes()))]
                      ).json()["job_id"]
    return jid, tmp_path


# ── Background running ───────────────────────────────────────────────────────

def test_a_slow_package_returns_immediately_and_reports_progress(
        client, collection, monkeypatch):
    """Attributes call a model per style; the request must not hold open."""
    jid, _ = collection
    seen = {}

    def slow(style):
        seen[style["style_code"]] = True
        return {"product_type": "WJK", "confidence": 0.9}

    monkeypatch.setattr("app.core.attribute_engine.enrich_style", slow)
    r = client.post(f"/collections/{jid}/packages/attributes/generate",
                    follow_redirects=False)
    assert r.status_code == 302                       # returned, not blocked

    # Wait for this test's own thread, so it cannot run on into the next
    # test's database once the fixtures have wiped it.
    for _ in range(50):
        state = client.get(
            f"/collections/{jid}/packages/attributes/status").json()["state"]
        if state != "running":
            break
        time.sleep(0.1)
    assert state in ("ready", "needs_decisions")
    assert seen                                        # the model was consulted


def test_progress_reports_stage_and_percentage():
    class Run:
        status, stage = "running", "Assigning attributes (5 of 10 styles)"
        progress_done, progress_total = 5, 10
    out = pkg.progress_of(Run())
    assert out["running"] is True and out["pct"] == 50
    assert "5 of 10" in out["stage"]


def test_progress_of_an_unstarted_package_does_not_divide_by_zero():
    class Run:
        status, stage = "draft", None
        progress_done, progress_total = 0, 0
    assert pkg.progress_of(Run())["pct"] == 0


def test_a_package_already_running_is_not_started_twice(client, collection, test_app):
    jid, _ = collection
    from app.models import CollectionJob, PackageRun
    db = test_app["database"].SessionLocal()
    job = db.get(CollectionJob, jid)
    run = PackageRun(job_id=jid, kind="attributes", status="running")
    db.add(run)
    db.commit()
    again = pkg.run_package(db, job, "attributes")
    assert again.status == "running"
    db.close()


# ── Reconciliation ───────────────────────────────────────────────────────────

class _Ref:
    """A SAP feed that holds only the styles it was given."""

    def __init__(self, have):
        self.have = have
        self.records = []

    def __len__(self):
        return len(self.have)

    def find(self, *, style="", colour="", barcode=""):
        return [{"item_group": style}] if style in self.have else []

    def known_values(self, **kw):
        return {}


def _deliver(db, jid, kind="temp"):
    from app.models import PackageRun
    run = db.query(PackageRun).filter(PackageRun.job_id == jid,
                                      PackageRun.kind == kind).first()
    run.status = "delivered"
    db.commit()
    db.refresh(run)
    return run


def test_reconcile_is_refused_before_delivery(client, collection, test_app):
    jid, _ = collection
    client.post(f"/collections/{jid}/packages/temp/generate")
    from app.models import PackageRun
    db = test_app["database"].SessionLocal()
    run = db.query(PackageRun).filter(PackageRun.job_id == jid).first()
    _, problem = pkg.reconcile(db, run)
    assert "deliver" in problem
    db.close()


def test_reconcile_reports_what_sap_is_still_missing(
        client, collection, test_app, monkeypatch):
    """A file reaching the import folder is not SAP having created anything."""
    jid, _ = collection
    client.post(f"/collections/{jid}/packages/temp/generate")
    monkeypatch.setattr(pkg, "_sap_reference", lambda c: (_Ref({"S0"}), ""))

    db = test_app["database"].SessionLocal()
    run = _deliver(db, jid)
    run, problem = pkg.reconcile(db, run)
    assert problem == ""
    assert run.status == "reconciled"
    # Counted per product, not per row: two products, one of them in SAP.
    assert run.expected_count == 2
    assert run.found_count == 1
    assert any("S1" in m for m in run.missing_in_sap)
    db.close()


def test_reconcile_with_everything_present_reports_nothing_missing(
        client, collection, test_app, monkeypatch):
    jid, _ = collection
    client.post(f"/collections/{jid}/packages/temp/generate")
    monkeypatch.setattr(pkg, "_sap_reference", lambda c: (_Ref({"S0", "S1"}), ""))
    db = test_app["database"].SessionLocal()
    run, problem = pkg.reconcile(db, _deliver(db, jid))
    assert problem == "" and run.missing_in_sap == []
    assert run.found_count == run.expected_count
    db.close()


def test_reconcile_says_so_when_no_sap_sheet_is_connected(
        client, collection, test_app, monkeypatch):
    jid, _ = collection
    client.post(f"/collections/{jid}/packages/temp/generate")
    monkeypatch.setattr(pkg, "_sap_reference",
                        lambda c: (None, "no SAP sheet configured for this brand"))
    db = test_app["database"].SessionLocal()
    run, problem = pkg.reconcile(db, _deliver(db, jid))
    assert "no SAP sheet" in problem
    assert run.status == "delivered"          # unchanged, not falsely reconciled
    db.close()


# ── Delivery folder ──────────────────────────────────────────────────────────

def test_a_saved_folder_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_ROOT", str(tmp_path / "from-env"))

    class Config:
        delivery_root = str(tmp_path / "from-settings")

    assert pkg.delivery_root(Config()).name == "from-settings"
    assert pkg.delivery_root(None).name == "from-env"


def test_an_empty_setting_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_ROOT", str(tmp_path / "from-env"))

    class Config:
        delivery_root = ""

    assert pkg.delivery_root(Config()).name == "from-env"


def test_the_folder_can_be_set_from_the_collection_page(client, collection, test_app):
    jid, tmp = collection
    client.post(f"/collections/{jid}/brand-config",
                data={"sap_sheet_id": "", "earliest_ship_date": "",
                      "delivery_root": str(tmp / "dropbox-import")})
    from app.models import BrandConfig
    db = test_app["database"].SessionLocal()
    cfg = db.query(BrandConfig).first()
    assert cfg.delivery_root.endswith("dropbox-import")
    db.close()


# ── Finding the SAP sheet ────────────────────────────────────────────────────

def test_a_brandless_collection_cannot_be_searched_for():
    sheet_id, note = pkg.find_sap_sheet("")
    assert sheet_id == "" and "no brand" in note


def test_a_search_failure_is_reported_not_raised(monkeypatch):
    """Drive being unreachable must not break the collection page."""
    import googleapiclient.discovery

    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(googleapiclient.discovery, "build", boom)
    sheet_id, note = pkg.find_sap_sheet("Gramicci")
    assert sheet_id == "" and "could not be searched" in note


def test_a_background_run_survives_its_collection_being_deleted(test_app):
    """A collection can be deleted while a package is still being built.

    The thread then updates a row that no longer exists, and the failed flush
    leaves the session unusable — so even recording the failure blew up.
    """
    from app.models import CollectionJob, PackageRun, User
    db = test_app["database"].SessionLocal()
    user = User(username="bg", email="bg@flendergroup.com",
                password_hash="x", email_verified=True)
    db.add(user)
    db.commit()
    job = CollectionJob(user_id=user.id, brand="Test", season="SS27")
    db.add(job)
    db.commit()
    run = PackageRun(job_id=job.id, kind="attributes", status="running")
    db.add(run)
    db.commit()
    job_id, run_id = job.id, run.id
    db.delete(job)          # cascades the run away
    db.commit()
    db.close()

    # Must return quietly rather than raise out of the thread.
    pkg._run_in_background(job_id, "attributes")

    db = test_app["database"].SessionLocal()
    assert db.query(PackageRun).filter(PackageRun.id == run_id).first() is None
    db.close()

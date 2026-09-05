"""Supplier email intake — classification, detection, analysis, webhook."""
from __future__ import annotations

import io

import pytest

from app.core.intake import (
    CATALOG, IMAGES, ORDER_SHEET, OTHER, PRICE_LIST,
    analyse_rows, build_intake_report, classify_attachment, detect_brand,
    detect_season, format_intake_email, missing_kinds,
)


# ── Attachment classification ────────────────────────────────────────────────

@pytest.mark.parametrize("filename,expected", [
    # The shapes suppliers actually send.
    ("SS27_ORDER_SHEET.xlsx", ORDER_SHEET),
    ("Carhartt_WIP_OrderSheet_2026.xlsx", ORDER_SHEET),
    ("HUF Land Cruiser Collection OrderSheet.xlsx", ORDER_SHEET),
    ("SS27_PRICE_LIST.xlsx", PRICE_LIST),
    ("pricelist-ss27.csv", PRICE_LIST),
    ("Butter Goods RRP 2026.xlsx", PRICE_LIST),
    ("SS27_LOOKBOOK.pdf", CATALOG),
    ("brand catalogue spring.pdf", CATALOG),
    ("Images.zip", IMAGES),
    ("Product images SP27.zip", IMAGES),
    ("packshot_001.jpg", IMAGES),
    ("terms_and_conditions.docx", OTHER),
])
def test_classify_attachment(filename, expected):
    assert classify_attachment(filename) == expected


def test_unnamed_spreadsheet_defaults_to_order_sheet():
    # The overwhelmingly common case; the report shows the assumption so a
    # human can correct it.
    assert classify_attachment("SS27.xlsx") == ORDER_SHEET


def test_price_beats_order_when_both_words_appear():
    assert classify_attachment("order_price_list_ss27.xlsx") == PRICE_LIST


def test_pdf_named_as_an_order_sheet_is_not_filed_as_a_catalog():
    assert classify_attachment("SS27 order sheet.pdf") == ORDER_SHEET


def test_image_index_spreadsheet_is_not_an_order_sheet():
    assert classify_attachment("SS27 image list.xlsx") == OTHER


# ── Brand & season ───────────────────────────────────────────────────────────

def test_detect_brand_prefers_the_longest_match():
    # "carhartt" is also registered; the specific label must win.
    assert detect_brand("Carhartt WIP SS27 order") == "Carhartt WIP"


def test_detect_brand_reads_filenames_too():
    assert detect_brand("", "20260415_Butter_Goods_OrderSheet.xlsx") == "Butter Goods"


def test_detect_brand_returns_none_when_unregistered():
    assert detect_brand("Some Unknown Label FW27") is None


@pytest.mark.parametrize("text,expected", [
    ("Carhartt WIP SS27", "SS27"),
    ("FW 26 order sheet", "FW26"),
    ("Spring/Summer 2027 collection", "SS27"),
    ("HUF Holiday 26", "HO26"),
    ("Pre-Fall 2026 linesheet", "PF26"),
    ("SP27 images", "SS27"),
    ("no season here", None),
])
def test_detect_season(text, expected):
    assert detect_season(text) == expected


# ── Analysis ─────────────────────────────────────────────────────────────────

def _row(**kw):
    base = {"item_code": "I0001", "color_name": "Black", "size": "M",
            "barcode": "111", "wholesale_price": 50, "retail_price": 100}
    base.update(kw)
    return base


def test_analyse_counts_styles_colours_and_skus():
    rows = [
        _row(size="S", barcode="1"), _row(size="M", barcode="2"),
        _row(item_code="I0002", color_name="Navy", barcode="3"),
    ]
    out = analyse_rows(rows)
    assert out["skus"] == 3
    assert out["styles"] == 2          # I0001/Black and I0002/Navy
    assert out["colour_styles"] == 2   # Black, Navy


def test_analyse_counts_the_gaps_that_block_sap_creation():
    rows = [
        _row(barcode=""), _row(barcode="2", wholesale_price=None),
        _row(barcode="3", retail_price=None), _row(barcode="4", size=""),
    ]
    out = analyse_rows(rows)
    assert out["missing_barcode"] == 1
    assert out["missing_wholesale_price"] == 1
    assert out["missing_retail_price"] == 1
    assert out["missing_size"] == 1


def test_analyse_does_not_double_count_a_repeated_line():
    # PDF extraction commonly repeats a row across pages.
    rows = [_row(barcode="9"), _row(barcode="9")]
    assert analyse_rows(rows)["skus"] == 1


def test_analyse_of_nothing_is_zero_not_an_error():
    assert analyse_rows([])["skus"] == 0


# ── Report ───────────────────────────────────────────────────────────────────

def test_missing_kinds_lists_what_did_not_arrive():
    assert missing_kinds([ORDER_SHEET, PRICE_LIST]) == [CATALOG, IMAGES]


def test_report_flags_a_missing_image_package_but_still_processes():
    files = [{"filename": "o.xlsx", "kind": ORDER_SHEET},
             {"filename": "p.xlsx", "kind": PRICE_LIST},
             {"filename": "c.pdf", "kind": CATALOG}]
    report = build_intake_report("Carhartt WIP", "SS27", files,
                                 analyse_rows([_row()]))
    assert report["missing"] == [IMAGES]
    # A missing image package does not block the data work.
    assert report["ready_to_process"] is True


def test_report_is_not_ready_when_no_products_parsed():
    report = build_intake_report("Carhartt WIP", "SS27", [], analyse_rows([]))
    assert report["ready_to_process"] is False


def test_report_warns_when_brand_could_not_be_detected():
    report = build_intake_report(None, "SS27", [], analyse_rows([_row()]))
    assert any("Brand" in w for w in report["warnings"])
    assert report["ready_to_process"] is False


def test_intake_email_names_the_collection_and_the_gaps():
    files = [{"filename": "o.xlsx", "kind": ORDER_SHEET}]
    report = build_intake_report("Carhartt WIP", "SS27", files,
                                 analyse_rows([_row(barcode="")]))
    text = format_intake_email(report)
    assert "Carhartt WIP — SS27" in text
    assert "Order sheet" in text
    assert "Image package" in text          # listed as not received
    assert "without a barcode" in text


# ── Webhook ──────────────────────────────────────────────────────────────────

def _xlsx_bytes():
    """A minimal supplier order sheet the real parser can read."""
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame([
        {"Style Code": "I031234", "Color": "Black", "Size": "M",
         "Barcode": "4055123456789", "Wholesale Price": 45, "RRP": 99},
        {"Style Code": "I031234", "Color": "Black", "Size": "L",
         "Barcode": "4055123456790", "Wholesale Price": 45, "RRP": 99},
        {"Style Code": "I031299", "Color": "Navy", "Size": "M",
         "Barcode": "", "Wholesale Price": 60, "RRP": 129},
    ]).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def intake_key(monkeypatch):
    """Enable the webhook with a known key for the duration of a test."""
    import app.routers.intake_routes as ir
    monkeypatch.setattr(ir, "INTAKE_API_KEY", "test-key")
    return "test-key"


def test_intake_endpoint_is_absent_until_a_key_is_configured(client):
    # No INTAKE_API_KEY in the test environment — the route must not exist
    # rather than accept anonymous posts.
    r = client.post("/api/intake/email",
                    files=[("files", ("a.xlsx", _xlsx_bytes()))])
    assert r.status_code == 404


def test_intake_endpoint_rejects_a_wrong_key(client, intake_key, make_user):
    make_user()
    r = client.post("/api/intake/email", headers={"X-Intake-Key": "nope"},
                    files=[("files", ("a.xlsx", _xlsx_bytes()))])
    assert r.status_code == 401


def test_intake_endpoint_creates_an_analysed_collection(client, intake_key, make_user):
    make_user()
    r = client.post(
        "/api/intake/email",
        headers={"X-Intake-Key": "test-key"},
        data={"sender": "sales@carhartt-wip.com",
              "subject": "Carhartt WIP SS27 order sheet"},
        files=[("files", ("SS27_ORDER_SHEET.xlsx", _xlsx_bytes())),
               ("files", ("SS27_LOOKBOOK.pdf", b"%PDF-1.4 not a real pdf"))],
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["ok"] is True
    assert body["brand"] == "Carhartt WIP"
    assert body["season"] == "SS27"
    assert body["skus"] == 3
    assert body["styles"] == 2
    assert ORDER_SHEET in body["received"]
    assert CATALOG in body["received"]
    # No price list and no images arrived — both must be reported.
    assert set(body["missing"]) == {PRICE_LIST, IMAGES}
    assert any("barcode" in w for w in body["warnings"])


def test_intake_endpoint_needs_at_least_one_attachment(client, intake_key, make_user):
    make_user()
    r = client.post("/api/intake/email", headers={"X-Intake-Key": "test-key"},
                    data={"subject": "empty"})
    assert r.status_code == 400


def test_collection_appears_in_the_ui_after_intake(client, intake_key, login_as):
    # The mailbox files collections under the first active user, which is the
    # account created by login_as here.
    login_as()
    client.post("/api/intake/email", headers={"X-Intake-Key": "test-key"},
                data={"subject": "Carhartt WIP SS27"},
                files=[("files", ("SS27_ORDER_SHEET.xlsx", _xlsx_bytes()))])

    page = client.get("/collections")
    assert page.status_code == 200
    assert "Carhartt WIP" in page.text
    assert "SS27" in page.text


def test_correcting_a_files_kind_re_runs_the_analysis(client, intake_key, login_as):
    login_as()
    created = client.post(
        "/api/intake/email", headers={"X-Intake-Key": "test-key"},
        data={"subject": "Carhartt WIP SS27"},
        files=[("files", ("SS27_data.xlsx", _xlsx_bytes()))],
    ).json()
    job_id = created["job_id"]
    # It was assumed to be the order sheet; it is really the price list.
    assert created["received"] == [ORDER_SHEET]

    detail = client.get(f"/collections/{job_id}")
    file_id = int(detail.text.split("/files/")[1].split("/kind")[0])

    client.post(f"/collections/{job_id}/files/{file_id}/kind",
                data={"kind": PRICE_LIST}, follow_redirects=False)

    after = client.get(f"/collections/{job_id}")
    assert after.status_code == 200
    # Re-analysed against the corrected kind: still parsed, now a price list.
    assert "corrected" in after.text


# ── Multi-brand collections ──────────────────────────────────────────────────

def test_detect_brands_drops_the_shorter_overlapping_name():
    from app.core.intake import detect_brands
    # "carhartt" must not be reported alongside "carhartt wip".
    assert detect_brands("Carhartt WIP SS27") == ["Carhartt WIP"]


def test_detect_brands_finds_a_genuinely_multi_brand_file():
    from app.core.intake import detect_brands
    found = detect_brands("HOKA & ASICS ATS - FOOTWEAR OrderSheet.xlsx")
    assert set(found) == {"HOKA", "ASICS"}


def test_report_warns_when_a_collection_names_two_brands():
    report = build_intake_report("HOKA", "SS27", [], analyse_rows([_row()]),
                                 all_brands=["HOKA", "ASICS"])
    assert any("2 brands" in w for w in report["warnings"])
    assert report["brands"] == ["HOKA", "ASICS"]


def test_single_brand_collection_gets_no_multi_brand_warning():
    report = build_intake_report("Carhartt WIP", "SS27", [],
                                 analyse_rows([_row()]),
                                 all_brands=["Carhartt WIP"])
    assert not any("brands named" in w for w in report["warnings"])

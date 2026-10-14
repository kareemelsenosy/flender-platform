"""Brand rules, split collections and the fixes from the October review.

Covers the Task Specification Adjustment Sheet: per-item base colour from the
brand's colour table (statistic codes included), size runs and size codes as
editable tables, order sheet + product list combining with a missing-items
check, brand and season read from inside the file, column mapping that a
person can correct, and critical answers that actually settle.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from app.core import brand_rules as core
from app.core import field_mapping
from app.core.base_color import propose
from app.core.combine import combine
from app.core.intake import (
    PRODUCT_LIST, classify_attachment, detect_identity_from_rows, detect_season,
)


# ── Rules tables (pure) ──────────────────────────────────────────────────────

def test_seeds_carry_the_rules_flender_wrote_down():
    carhartt = core.seed_for("CARHARTT  WIP")
    assert carhartt["settings"]["uses_base_colour"] is True
    assert {r["code"] for r in carhartt["tables"]["size_runs"]} >= {"00", "03", "06", "14", "15", "32-36"}
    huf = core.seed_for("HUF")
    assert huf["settings"]["uses_base_colour"] is False
    assert core.sizes_for_code("D", huf["tables"]) == ["XS", "S", "M", "L", "XL", "XXL"]
    # The product-list code reaches the same scale.
    assert core.sizes_for_code("A", huf["tables"]) == core.sizes_for_code("D", huf["tables"])
    assert core.seed_for("Edwin")["settings"]["uses_base_colour"] is True
    assert core.seed_for("Gramicci")["settings"]["uses_base_colour"] is False


def test_colour_goes_through_the_statistic_code_to_the_base_colour():
    tables = {"colours": [{"colour": "Sky Blue", "stat_code": "12"}],
              "stat_codes": [{"stat_code": "12", "base": "BLUE"}]}
    hit = propose("sky blue", table=core.colour_table_lookup(tables))
    assert hit["value"] == "Blue" and hit["source"] == "brand_table"
    assert "12" in hit["reason"]


def test_the_brand_table_beats_the_word_rules():
    # A rule would say Blue; the brand says this one is Grey.
    tables = {"colours": [{"colour": "Steel Blue", "base": "Grey"}]}
    assert propose("Steel Blue", table=core.colour_table_lookup(tables))["value"] == "Grey"


def test_a_missing_colour_takes_a_temporary_value_only_when_nothing_better_exists():
    temp = {"ikola": {"value": "Yellow", "why": "catalogue: Ikola lemon"}}
    r = propose("Ikola", table={}, temporary=temp)
    assert r["value"] == "Yellow" and r["source"] == "temporary"
    # A name the rules can read is not overridden by a suggestion.
    assert propose("Sky Blue", table={}, temporary={"sky blue": {"value": "Red"}})["value"] == "Blue"


def test_size_run_ranges_cover_every_code_inside_them():
    tables = core.seed_for("Carhartt WIP")["tables"]
    assert core.size_run_meaning("34", tables)["meaning"].startswith("Pants length")
    assert core.size_run_meaning("06", tables)["meaning"] == "Onesize"
    assert core.size_run_meaning("99", tables) is None


def test_every_rule_reads_as_a_sentence():
    assert core.sentence("size_codes", {"order_code": "D", "list_code": "A",
                                        "meaning": "XS-XXL", "sizes": "XS, S"}) == \
        "Order-form size code D (product-list code A) means XS-XXL: XS, S."
    assert "statistic code 12" in core.sentence(
        "colours", {"colour": "Sky Blue", "stat_code": "12", "base": "Blue"})


def test_upsert_replaces_the_row_with_the_same_key():
    rows, _ = core.upsert_row([], "colours", {"colour": "Ikola", "base": "yellow"})
    rows, old = core.upsert_row(rows, "colours", {"colour": "IKOLA", "base": "Green"})
    assert len(rows) == 1 and old["base"] == "Yellow" and rows[0]["base"] == "Green"


def test_the_colour_table_imports_from_its_two_sheets(tmp_path):
    path = tmp_path / "colours.xlsx"
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame({"Colour Description": ["Sky Blue", "Ikola"],
                      "Statistic Code": ["12", "40"]}).to_excel(xw, sheet_name="Colours", index=False)
        pd.DataFrame({"Statistic Code": ["12", "40"],
                      "Base Colour": ["Blue", "Yellow"]}).to_excel(xw, sheet_name="Codes", index=False)
    parsed = core.parse_colour_workbook(str(path))
    assert [r["colour"] for r in parsed["colours"]] == ["Sky Blue", "Ikola"]
    lookup = core.colour_table_lookup(parsed)
    assert lookup["ikola"]["base"] == "Yellow"


def test_an_order_form_defines_its_own_size_scales(tmp_path):
    path = tmp_path / "huf.xlsx"
    pd.DataFrame([["SIZE SCALES", "A", "S/M", "L/XL"],
                  ["E", "XS", "S", "M", "L", "XL", "XXL"],
                  [None] * 7,
                  ["Division", "Category", "Description", "Style Number", "Color",
                   "Catalog Page", "Size", "Units", "Price", "MSRP", "Total"],
                  ["Apparel", "", "JACKET", "JK1-BLK", "BLACK", "1", "E", "0", "50", "100", "0"]]
                 ).to_excel(path, header=False, index=False)
    legend = core.read_size_legend(str(path))
    assert legend["A"] == ["S/M", "L/XL"]
    assert legend["E"] == ["XS", "S", "M", "L", "XL", "XXL"]
    rows = core.expand_scale_rows([{"Size": "E"}], {}, legend)
    assert [r["Size"] for r in rows] == ["XS", "S", "M", "L", "XL", "XXL"]


# ── Intake ───────────────────────────────────────────────────────────────────

def test_the_hs_code_file_is_a_product_list():
    assert classify_attachment("SU27 UPC HS code.xlsx") == PRODUCT_LIST
    assert classify_attachment("HUF HS list.xlsx") == PRODUCT_LIST
    assert classify_attachment("FW27 Pre EUR Importsheet.xlsx") == "order_sheet"


@pytest.mark.parametrize("text,expected", [
    ("SU27 UPC HS code.xlsx", "SU27"), ("HUF FA26", "FA26")])
def test_huf_season_letters_are_read(text, expected):
    assert detect_season(text) == expected


def test_brand_and_season_are_read_from_inside_the_file():
    found = detect_identity_from_rows([{"Brand": "CARHARTT WIP", "Collection": "FW27 D1"}] * 3)
    assert (found["brand"], found["season"]) == ("Carhartt WIP", "FW27")
    huf = detect_identity_from_rows([{"Division": "HUF", "Collection": "SU27",
                                      "Collection Name": "Summer 2027"}])
    assert (huf["brand"], huf["season"]) == ("HUF", "SU27")
    # HUF's order form says "Apparel" under Division — that is not a brand.
    assert detect_identity_from_rows([{"Division": "Apparel"}])["brand"] is None


# ── Column mapping ───────────────────────────────────────────────────────────

def test_other_brands_columns_map_onto_the_standard_fields():
    m = field_mapping.detect(["Division", "Description", "Style Number", "Color",
                              "Size", "Price", "MSRP", "HS Code", "UPC"])
    got = {f: v["header"] for f, v in m.items() if v["header"]}
    assert got["Item No."] == "Style Number"
    assert got["Item Description"] == "Description"
    assert got["Wholesale Price EUR"] == "Price"
    assert got["Retail EUR"] == "MSRP"
    assert got["Duty Position"] == "HS Code"
    assert got["Barcode"] == "UPC"
    assert "Sex" not in got                      # Division is not a gender


def test_a_brand_rule_overrides_detection_and_can_say_none():
    m = field_mapping.detect(["Style", "Article", "Colour"],
                             {"Item No.": "Article", "Color": field_mapping.NONE})
    assert m["Item No."] == {"header": "Article", "source": "brand rule"}
    assert m["Color"]["header"] == ""


# ── Combining a split collection ─────────────────────────────────────────────

def _huf_list():
    return [{"Item No.": "JK00525", "Color": "CAMO", "Size": s, "Barcode": f"88{i}",
             "Duty Position": "6201409000", "Country of Origin": "China"}
            for i, s in enumerate(["XS", "S", "M"])] + [
            {"Item No.": "ZZ1", "Color": "RED", "Size": "M", "Barcode": "9",
             "Duty Position": "1"}]


def test_a_size_scale_line_becomes_one_line_per_size_with_its_upc():
    order = [{"Item No.": "JK00525-CAMO", "Color": "CAMO", "Size": "D"},
             {"Item No.": "JK00999-BLACK", "Color": "BLACK", "Size": "D"}]
    rows, report = combine(order, _huf_list(), scale_codes={"D"})
    camo = [r for r in rows if r["Item No."] == "JK00525-CAMO"]
    assert [(r["Size"], r["Barcode"]) for r in camo] == [("XS", "880"), ("S", "881"), ("M", "882")]
    assert all(r["Duty Position"] == "6201409000" for r in camo)
    assert report["only_in_order_count"] == 1 and "JK00999-BLACK" in report["only_in_order"][0]
    assert report["only_in_list"] == ["ZZ1 · RED"]


def test_a_style_level_match_never_stamps_one_sizes_barcode_on_every_size():
    order = [{"Item No.": "JK00525", "Color": "CAMO", "Size": "XL"}]
    rows, _ = combine(order, _huf_list())
    assert "Barcode" not in rows[0] or not rows[0]["Barcode"]
    assert rows[0]["Duty Position"] == "6201409000"


def test_the_same_line_is_joined_by_barcode():
    order = [{"Item No.": "X", "Barcode": "881", "Size": "S"}]
    rows, report = combine(order, _huf_list())
    assert rows[0]["Country of Origin"] == "China"
    assert report["matched_items"] == 1


# ── Service: requests are approved by people ─────────────────────────────────

def test_a_request_is_filed_once_and_a_rejection_is_remembered(db_session):
    from app.services import brand_rules as svc
    first = svc.request_change(db_session, "Carhartt WIP", "colours",
                               {"colour": "Ikola", "base": "Yellow"}, reason="new")
    assert first is not None
    assert svc.request_change(db_session, "Carhartt WIP", "colours",
                              {"colour": "IKOLA", "base": "Yellow"}, reason="again") is None
    svc.decide_change(db_session, first.id, False)
    assert svc.request_change(db_session, "Carhartt WIP", "colours",
                              {"colour": "Ikola", "base": "Green"}, reason="x") is None


def test_approving_can_correct_the_suggestion(db_session):
    from app.services import brand_rules as svc
    req = svc.request_change(db_session, "Carhartt WIP", "colours",
                             {"colour": "Ikola", "base": "Green"}, reason="ai")
    svc.decide_change(db_session, req.id, True, override={"base": "Yellow"})
    rules = svc.rules_for(db_session, "Carhartt WIP")
    assert core.colour_table_lookup(rules["tables"])["ikola"]["base"] == "Yellow"


def test_importing_the_table_settles_waiting_requests(db_session, tmp_path):
    from app.services import brand_rules as svc
    svc.request_change(db_session, "Carhartt WIP", "colours",
                       {"colour": "Sky Blue", "base": "Blue"}, reason="rule")
    path = tmp_path / "c.xlsx"
    pd.DataFrame({"Colour": ["Sky Blue"], "Base Colour": ["Blue"]}).to_excel(path, index=False)
    svc.import_colour_table(db_session, "Carhartt WIP", str(path))
    assert svc.pending(db_session, "Carhartt WIP") == []


# ── Through the app ──────────────────────────────────────────────────────────

def _xlsx(rows) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    return buf.getvalue()


def _carhartt_rows():
    return [{"Collection": "FW27 D1", "Brand": "CARHARTT WIP", "Barcode": f"40{i}",
             "SKU": f"I1.89.{s}", "Item Group": "Jackets", "Item No.": "I1",
             "Colour Code": "8902", "Size Run": "03", "Size": s,
             "Item Description": "Detroit Jacket", "Color": colour,
             "Wholesale Price EUR": "80"}
            for i, (s, colour) in enumerate([("S", "Sky Blue"), ("M", "Sky Blue"),
                                             ("L", "Ikola")])]


def _upload(client, name, rows, **extra):
    r = client.post("/collections/upload", files=[("files", (name, _xlsx(rows)))],
                    data=extra, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_brand_is_read_from_the_file_when_the_name_has_none(client, login_as, db_session):
    login_as()
    jid = _upload(client, "FW27 Pre EUR Importsheet.xlsx", _carhartt_rows())
    from app.models import CollectionJob
    job = db_session.get(CollectionJob, jid)
    assert job.brand == "Carhartt WIP" and job.season == "FW27"
    assert not any("Brand could not" in w for w in job.report["warnings"])


def test_setting_the_brand_by_hand_clears_the_warning(client, login_as, db_session):
    login_as()
    rows = [{"Item No.": "A1", "Color": "Black", "Size": "M"}]
    jid = _upload(client, "Importsheet.xlsx", rows)
    from app.models import CollectionJob
    assert any("Brand could not" in w for w in db_session.get(CollectionJob, jid).report["warnings"])
    client.post(f"/collections/{jid}/identity", data={"brand": "Carhartt WIP", "season": "FW27"})
    db_session.expire_all()
    job = db_session.get(CollectionJob, jid)
    assert not any("Brand could not" in w for w in job.report["warnings"])


def test_carhartt_base_colours_come_from_its_table(client, login_as, db_session, monkeypatch):
    from app.models import PackageRun
    from app.services import brand_rules as svc
    import app.services.colour_suggest as cs
    login_as()
    svc.edit_row(db_session, "Carhartt WIP", "colours", {"colour": "Sky Blue", "stat_code": "12"})
    svc.edit_row(db_session, "Carhartt WIP", "stat_codes", {"stat_code": "12", "base": "Blue"})
    monkeypatch.setattr(cs, "suggest", lambda brand, names, *a, **k:
                        {n: {"value": "Yellow", "why": "suggested from the catalogue"}
                         for n in names if n == "Ikola"})
    jid = _upload(client, "FW27 Importsheet.xlsx", _carhartt_rows())
    client.post(f"/collections/{jid}/packages/product_creation/generate")
    db_session.expire_all()
    run = db_session.query(PackageRun).filter_by(job_id=jid, kind="product_creation").first()
    sheet = pd.read_excel(run.file_path, dtype=str)
    assert list(sheet["BASE COLOR"]) == ["Blue", "Blue", "Yellow"]
    # Ikola is filled temporarily and flagged, and a rule request waits.
    q = next(q for q in run.exceptions if q["field"] == "BASE COLOR")
    assert q["subject"] == "Ikola" and "temporary Yellow" in q["reason"]
    assert any(p.row["colour"] == "Ikola" for p in svc.pending(db_session, "Carhartt WIP"))
    assert not any(q["field"] == "BASE COLOR" and q["subject"] == "Sky Blue"
                   for q in run.exceptions)


def test_brands_without_base_colour_are_never_asked(client, login_as, db_session):
    from app.models import PackageRun
    login_as()
    rows = [{**r, "Brand": "GRAMICCI", "Collection": "SS27"} for r in _carhartt_rows()]
    jid = _upload(client, "Gramicci SS27 order.xlsx", rows)
    client.post(f"/collections/{jid}/packages/product_creation/generate")
    db_session.expire_all()
    run = db_session.query(PackageRun).filter_by(job_id=jid, kind="product_creation").first()
    assert not any(q["field"] == "BASE COLOR" for q in run.exceptions)


def test_a_colour_decision_is_written_into_the_brand_table(client, login_as, db_session):
    from app.models import PackageRun
    from app.services import brand_rules as svc
    login_as()
    jid = _upload(client, "FW27 Importsheet.xlsx", _carhartt_rows())
    client.post(f"/collections/{jid}/packages/product_creation/generate")
    client.post(f"/collections/{jid}/packages/product_creation/decide",
                data={"key": "BASE COLOR::Ikola", "value": "Yellow"})
    db_session.expire_all()
    rules = svc.rules_for(db_session, "Carhartt WIP")
    assert core.colour_table_lookup(rules["tables"])["ikola"]["base"] == "Yellow"


def test_answering_a_critical_missing_column_actually_settles_it(client, login_as, db_session):
    from app.models import PackageRun
    login_as()
    rows = [{k: v for k, v in r.items() if k != "Wholesale Price EUR"} for r in _carhartt_rows()]
    jid = _upload(client, "FW27 Importsheet.xlsx", rows)
    client.post(f"/collections/{jid}/packages/product_creation/generate")
    db_session.expire_all()
    run = db_session.query(PackageRun).filter_by(job_id=jid, kind="product_creation").first()
    assert run.critical_count == 1
    client.post(f"/collections/{jid}/packages/product_creation/decide",
                data={"key": "Wholesale Price EUR::Wholesale Price EUR", "value": "80"})
    db_session.expire_all()
    run = db_session.query(PackageRun).filter_by(job_id=jid, kind="product_creation").first()
    assert run.critical_count == 0
    assert set(pd.read_excel(run.file_path, dtype=str)["Wholesale Price EUR"]) == {"80"}


def test_a_product_list_added_later_is_joined_and_checked(client, login_as, db_session):
    from app.models import CollectionJob
    login_as()
    order = [{"Division": "Apparel", "Description": "JACKET", "Style Number": "JK00525-CAMO",
              "Color": "CAMO", "Size": "D", "Price": "50"}]
    jid = _upload(client, "HUF SU27 order form.xlsx", order)
    hs = [{"Division": "HUF", "Collection": "SU27", "Style": "JK00525", "Color": "CAMO",
           "Size": s, "UPC": f"88{i}", "HS Code": "6201409000"}
          for i, s in enumerate(["XS", "S"])]
    client.post(f"/collections/{jid}/files",
                files=[("files", ("SU27 UPC HS code.xlsx", _xlsx(hs)))])
    db_session.expire_all()
    job = db_session.get(CollectionJob, jid)
    assert {f.kind for f in job.files} == {"order_sheet", "product_list"}
    combined = job.report["combine"]
    assert combined["matched_items"] == 1 and combined["only_in_order_count"] == 0
    page = client.get(f"/collections/{jid}").text
    assert "Order sheet against product list" in page and "Step by step" in page


def test_saving_a_column_mapping_becomes_a_brand_rule(client, login_as, db_session):
    from app.models import CollectionJob
    from app.services import brand_rules as svc
    login_as()
    rows = [{"Brand": "GRAMICCI", "Collection": "SS27", "Article": "G1",
             "Shade": "Navy", "Size": "M"}]
    jid = _upload(client, "Gramicci SS27 order.xlsx", rows)
    job = db_session.get(CollectionJob, jid)
    fid = next(iter(job.report["mappings"]))
    client.post(f"/collections/{jid}/mapping",
                data={"file_id": fid, "col::Item No.": "Article", "col::Color": "Shade"})
    rules = svc.rules_for(db_session, "Gramicci")
    # "Article" was already found automatically, so only the colour is a new rule.
    assert core.column_overrides(rules["tables"]) == {"Color": "Shade"}
    db_session.expire_all()
    fields = db_session.get(CollectionJob, jid).report["mappings"][fid]["fields"]
    assert fields["Color"] == {"header": "Shade", "source": "brand rule"}


def test_the_brand_rules_pages_render_and_edit(client, login_as):
    login_as()
    assert "Brand Rules" in client.get("/brand-rules").text
    page = client.get("/brand-rules/carhartt wip").text
    assert "Carhartt WIP rules" in page
    assert "Pants length (inches)" in page and "Size run 06 means Onesize" in page
    client.post("/brand-rules/carhartt wip/rows",
                data={"table": "size_runs", "code": "30", "meaning": "Pants length (inches)"})
    assert "Size run 30 means Pants length (inches)." in client.get("/brand-rules/carhartt wip").text
    export = client.get("/brand-rules/carhartt wip/export")
    assert export.status_code == 200 and export.content[:2] == b"PK"


def test_a_running_package_can_be_cancelled(client, login_as, db_session):
    from app.models import PackageRun
    from app.services import packages as pkg
    login_as()
    jid = _upload(client, "FW27 Importsheet.xlsx", _carhartt_rows())
    run = PackageRun(job_id=jid, kind="attributes", status="running")
    db_session.add(run)
    db_session.commit()
    client.post(f"/collections/{jid}/packages/attributes/cancel")
    db_session.expire_all()
    assert db_session.get(PackageRun, run.id).status == "cancelling"


def test_the_attribute_run_stops_when_cancelled():
    from app.core.collection_attributes import build_attribute_package

    class Stop(Exception):
        pass

    seen = []

    def progress(done, total):
        seen.append(done)
        if done >= 2:
            raise Stop()

    styles = [{"style_code": f"S{i}", "name": "Tee"} for i in range(20)]
    with pytest.raises(Stop):
        build_attribute_package(styles, lambda s: {"product_type": "TSHIRT", "confidence": 1},
                                on_progress=progress, workers=4)
    assert max(seen) < 20


# ── Real supplier layouts (Real Test Files, October 2026) ────────────────────

def test_a_style_code_with_a_dash_is_only_split_when_the_suffix_is_the_colour():
    from app.services.packages import fill_from_collection

    class Job:
        brand = "Market"
    rows = fill_from_collection([
        {"Item No.": "MKT26Q1-SS0589", "Color": "BLACK", "Size": "S"},
        {"Item No.": "JK00520-DKBRN", "Color": "DARK BROWN", "Size": "S"}], Job())
    assert rows[0]["Item No."] == "MKT26Q1-SS0589"
    assert (rows[1]["Item No."], rows[1]["Colour Code"]) == ("JK00520", "DKBRN")


@pytest.mark.parametrize("value,columns,expected", [
    ("XS, S, M, L, XL", [], ["XS", "S", "M", "L", "XL"]),
    ("28 - 38", ["25", "26", "28", "29", "30", "34", "36", "38"], ["28", "29", "30", "34", "36", "38"]),
    ("XXS-4XL", ["XS / 28", "S / 30", "4XL / 42", "One Size"], ["XS", "S", "4XL"]),
    ("26-44", ["XS / 28", "S / 30", "4XL / 42"], ["28", "30", "42"]),
])
def test_sizes_written_as_lists_and_ranges_become_one_row_each(value, columns, expected):
    from app.core.size_expand import expand_rows
    rows, _ = expand_rows([{"Item No.": "A", "Size": value}], columns, has_size_field=True)
    assert [r["Size"] for r in rows] == expected


def test_one_size_goods_without_any_size_column_get_onesize():
    from app.core.size_expand import expand_rows
    rows, note = expand_rows([{"Item No.": "KOM-1"}], ["KOM-Code", "Name"], has_size_field=False)
    assert rows[0]["Size"] == "onesize" and note["one_size_default"] == 1


def test_a_price_without_eur_never_fills_the_eur_column():
    m = field_mapping.detect(["Style No", "WHS AED", "SRP AED", "Price"])
    rows = field_mapping.apply([{"Style No": "A", "WHS AED": "774"}], m)
    assert rows[0].get("Wholesale Price EUR") in (None, "")
    assert m["Wholesale Price EUR"]["currency"] == "AED"
    eur = field_mapping.detect(["WHSL EU"])
    assert eur["Wholesale Price EUR"]["currency"] == "EUR"


def test_lot_keys_and_header_legends_are_read():
    import pandas as pd
    from app.core.size_expand import legend_from_lot_sheet, legend_in_header
    lots = pd.DataFrame([["SOCKS Men", None, None, None], [None, "35-38", "39-42", "Sum"],
                         ["SX", "6", None, "6"], ["SY", None, "6", "6"]])
    assert legend_from_lot_sheet(lots) == {"SX": ["35-38"], "SY": ["39-42"]}
    tnt = pd.DataFrame([["No.", "STYLE CODE", "A", None, "XS", "S", "M", "TOTAL"],
                        [None, None, "B", None, None, "M/L", None, None],
                        [None, None, "C", "OS", None, None, None, None],
                        ["1", "TN1", "A", None, "3", "1", None, "4"]])
    assert legend_in_header(tnt, 0) == {"A": ["XS", "S", "M"], "B": ["M/L"], "C": ["OS"]}


@pytest.mark.parametrize("text,expected", [
    ("Q226 Order Sheet (May)", "SS26"), ("NEXT Q1 2026", "SS26"),
    ("Q3 - 2026", "FW26"), ("2026 FALL/WINTER", "FW26")])
def test_seasons_written_as_quarters_or_year_first(text, expected):
    assert detect_season(text) == expected


def test_a_short_brand_name_does_not_match_inside_another():
    from app.core.intake import detect_brand
    assert detect_brand("KOMONO.xlsx") == "Komono"


def test_a_size_with_its_own_barcode_is_never_split():
    """Carhartt sells 'S-M' and 'M-L' as sizes, each with a barcode. Reading
    them as ranges split the line and threw the barcode away."""
    from app.core.size_expand import expand_rows
    rows, _ = expand_rows([{"Item No.": "I037159", "Size": "S-M", "Barcode": "4068584602566"}],
                          [], has_size_field=True)
    assert [(r["Size"], r["Barcode"]) for r in rows] == [("S-M", "4068584602566")]


# ── Carhartt colour master, statistic codes, training mode ───────────────────

def _colour_master(path):
    """A small copy of Carhartt's colour master: one row per house colour code."""
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame({"For YPLM": ["No"] * 4, "Code": ["89", "HZ", "3UB", "00W"],
                      "Description": ["Black", "Hamilton Brown", "Shungite", "Tobacco / Gold"],
                      "Statistic Colour Code": ["001", "003", "002", None],
                      "BASE COLOR": ["Black", "Brown", "Grey", None]}
                     ).to_excel(xw, sheet_name="051026", index=False)
        pd.DataFrame({"Code": ["89"], "Description": ["Gold / Bordeaux"],
                      "Statistic Colour Code": ["9"]}).to_excel(xw, sheet_name="Sheet1", index=False)


def test_the_colour_master_is_read_by_code_through_the_statistic_code(tmp_path):
    path = tmp_path / "colours.xlsx"
    _colour_master(path)
    parsed = core.parse_colour_workbook(str(path))
    assert [r["code"] for r in parsed["colours"]] == ["89", "HZ", "3UB"]   # 00W has no stat code
    tables = {"colours": parsed["colours"],
              "stat_codes": core.seed_for("Carhartt WIP")["tables"]["stat_codes"]}
    lookup = core.colour_table_lookup(tables)
    # The order sheet's colour code is house colour + finish: 8902 -> 89.
    hit = core.resolve_from_table("Black", lookup, "8902")
    assert hit["value"] == "Black" and "statistic code 001" in hit["reason"]
    assert core.resolve_from_table("Hamilton Brown", lookup, "HZ02")["value"] == "Brown"
    assert core.resolve_from_table("Shungite", lookup, "3UB02")["value"] == "Grey"


def test_carhartt_statistic_codes_are_built_in():
    stats = {r["stat_code"]: r["base"] for r in core.seed_for("Carhartt WIP")["tables"]["stat_codes"]}
    assert stats["001"] == "Black" and stats["013"] == "Multicolour" and len(stats) == 13


def test_shungite_gets_its_base_colour_from_the_imported_table(client, login_as, db_session, tmp_path):
    from app.models import PackageRun
    from app.services import brand_rules as svc
    login_as()
    path = tmp_path / "colours.xlsx"
    _colour_master(path)
    svc.import_colour_table(db_session, "Carhartt WIP", str(path))
    rows = [{**r, "Color": "Shungite", "Colour Code": "3UB02"} for r in _carhartt_rows()]
    jid = _upload(client, "FW27 Importsheet.xlsx", rows)
    client.post(f"/collections/{jid}/packages/product_creation/generate")
    db_session.expire_all()
    run = db_session.query(PackageRun).filter_by(job_id=jid, kind="product_creation").first()
    assert set(pd.read_excel(run.file_path, dtype=str)["BASE COLOR"]) == {"Grey"}
    assert not any(q["field"] == "BASE COLOR" for q in run.exceptions)


def test_training_mode_writes_rules_instead_of_asking(db_session):
    from app.services import brand_rules as svc
    svc.request_change(db_session, "Carhartt WIP", "colours",
                       {"colour": "Smoke Green", "base": "Green"}, reason="rule")
    assert svc.set_training_mode(db_session, True) == 1          # waiting one applied
    assert svc.pending(db_session, "Carhartt WIP") == []
    svc.propose_rule(db_session, "Carhartt WIP", "colours",
                     {"colour": "Dark Navy", "base": "Blue"}, reason="navy is blue")
    lookup = core.colour_table_lookup(svc.rules_for(db_session, "Carhartt WIP")["tables"])
    assert lookup["dark navy"]["base"] == "Blue" and lookup["smoke green"]["base"] == "Green"
    assert any(h.source == "training" for h in svc.history(db_session, "Carhartt WIP"))
    # Off again: back to asking.
    svc.set_training_mode(db_session, False)
    svc.propose_rule(db_session, "Carhartt WIP", "colours",
                     {"colour": "Pumice", "base": "Grey"}, reason="x")
    assert [p.row["colour"] for p in svc.pending(db_session, "Carhartt WIP")] == ["Pumice"]


def test_the_training_switch_is_on_the_brand_rules_page(client, login_as):
    login_as()
    assert "Training mode is off" in client.get("/brand-rules").text
    client.post("/brand-rules/training", data={"mode": "on"})
    assert "Training mode is ON" in client.get("/brand-rules").text


@pytest.mark.parametrize("group,name,expected", [
    ("", "Another Zip Cardigan", "SWEATS"), ("Pants Bib Non-Denim", "Bib Overall", "PANTS"),
    ("JACKETS", "Nine Military", "JACKETS"), ("OUTER", "PERTEX Down Jacket", "JACKETS"),
    ("HEAD", "Beanie", "ACCS"), ("Ripples", "Brook Twilight", "")])
def test_a_style_gets_its_group_from_supplier_wording(group, name, expected):
    from app.core.collection_attributes import guess_master
    assert guess_master(group, name) == expected


def test_an_unknown_group_lets_the_model_choose_from_every_product_type(monkeypatch):
    """ACL has no group column: everything defaulted to accessories, so a
    cardigan was only offered bags and caps and came back unassigned."""
    import app.core.attribute_engine as eng
    seen = {}
    sweat = eng.PRODUCT_TYPES_BY_GROUP["SWEATS"][0][0]

    def fake(prompt, max_tokens=300):
        seen["prompt"] = prompt
        return ('{"product_type":"%s","confidence":0.9,"FABRIC":null,"FIT":null,'
                '"STYLE":[],"WEIGHT":null}' % sweat)
    monkeypatch.setattr(eng, "_call_ai", fake)
    out = eng.enrich_style({"style_code": "A", "name": "Zip Cardigan", "master_group": ""})
    assert out["product_type"] == sweat and out["master_group"] == "SWEATS"
    assert "BAG" in seen["prompt"] and sweat in seen["prompt"]


def test_the_house_colour_wins_over_a_look_alike_full_code():
    """Carhartt '0260' is White (house '02', finish '60'); the colour master
    also has a different house colour called '0260' (Black). FW26 SAP: White."""
    tables = {"colours": [{"code": "02", "colour": "White", "stat_code": "012"},
                          {"code": "0260", "colour": "Scotty Stripe, Black / Wax", "stat_code": "001"}],
              "stat_codes": core.seed_for("Carhartt WIP")["tables"]["stat_codes"]}
    hit = core.resolve_from_table("White", core.colour_table_lookup(tables), "0260")
    assert hit["value"] == "White"

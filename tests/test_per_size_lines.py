"""An uploaded sheet with one row per size keeps each size's barcode and qty.

The order-sheet tool searches one image per style-colour, so the rows of an
uploaded sheet are folded into one item per style-colour. Each size's own
barcode and quantity used to be lost in that fold: the export gave every size
the first size's barcode and the style's total as its stock.
"""
from __future__ import annotations

import time

import openpyxl

CSV = (
    "Item No.,Item Description,Color,Colour Code,Size,Barcode,Brand,Qty\n"
    "I026383,Chase Sweat,Black,0B9XX,M,4064958000011,Carhartt WIP,3\n"
    "I026383,Chase Sweat,Black,0B9XX,L,4064958000028,Carhartt WIP,2\n"
    "I020222,Acrylic Watch Hat,Black,89XX,One Size,4064958000035,Carhartt WIP,10\n"
)


def _mapped_session(client, test_app):
    up = client.post("/upload/file", files={"file": ("sizes.csv", CSV, "text/csv")})
    assert up.status_code == 200, up.text
    sid = up.json()["session_id"]
    form = {"map_item_code": "Item No.", "map_style_name": "Item Description",
            "map_color_name": "Color", "map_color_code": "Colour Code",
            "map_size": "Size", "map_barcode": "Barcode", "map_brand": "Brand",
            "map_qty_available": "Qty"}
    resp = client.post(f"/mapping/{sid}", data=form, follow_redirects=False)
    assert resp.status_code == 302, resp.text
    return sid


def test_each_size_keeps_its_own_barcode_and_quantity(client, login_as, test_app, db_session):
    models = test_app["models"]
    login_as()
    sid = _mapped_session(client, test_app)

    sweat = db_session.query(models.UniqueItem).filter_by(session_id=sid, item_code="I026383").one()
    assert sweat.sizes == ["M", "L"]
    assert sweat.size_details == {"M": {"barcode": "4064958000011", "qty": 3.0},
                                  "L": {"barcode": "4064958000028", "qty": 2.0}}

    for it in db_session.query(models.UniqueItem).filter_by(session_id=sid):
        it.search_status, it.review_status = "done", "approved"
    db_session.commit()

    assert client.post(f"/generate/{sid}", json={"save_images": False}).status_code == 200
    out = None
    for _ in range(40):
        poll = test_app["database"].SessionLocal()
        try:
            gf = poll.query(models.GeneratedFile).filter_by(session_id=sid).first()
            out = gf.file_path if gf else None
        finally:
            poll.close()
        if out:
            break
        time.sleep(0.25)
    assert out

    ws = openpyxl.load_workbook(out).active
    rows = list(ws.iter_rows(values_only=True))
    head = next(i for i, r in enumerate(rows) if r and "Barcode" in r)
    cols = {h: i for i, h in enumerate(rows[head]) if h}
    lines = {(r[cols["Manufacturer Code"]], r[cols["Size"]]): (str(r[cols["Barcode"]]), r[cols["Stock"]])
             for r in rows[head + 1:] if r and r[cols["Manufacturer Code"]]}
    assert lines[("I026383", "M")] == ("4064958000011", 3)
    assert lines[("I026383", "L")] == ("4064958000028", 2)
    assert lines[("I020222", "One Size")] == ("4064958000035", 10)


def test_a_barcode_typed_in_review_replaces_the_sheets(client, login_as, test_app, db_session):
    models = test_app["models"]
    login_as()
    sid = _mapped_session(client, test_app)
    sweat = db_session.query(models.UniqueItem).filter_by(session_id=sid, item_code="I026383").one()
    resp = client.post(f"/review/{sid}/items/{sweat.id}/edit", json={"barcode": "999"})
    assert resp.status_code == 200
    db_session.refresh(sweat)
    # The quantities stay per size; only the barcodes give way to the edit.
    assert sweat.size_details == {"M": {"qty": 3.0}, "L": {"qty": 2.0}}


def test_merging_an_uploaded_sheet_keeps_every_size():
    from app.models import UniqueItem
    from app.routers.merge_routes import _item_to_rows
    it = UniqueItem(item_code="I026383", color_name="Black", barcode="4064958000011",
                    qty_available=5)
    it.sizes = ["M", "L"]
    it.size_details = {"M": {"barcode": "4064958000011", "qty": 3},
                       "L": {"barcode": "4064958000028", "qty": 2}}
    rows = _item_to_rows(it)
    assert [(r["size"], r["barcode"], r["qty_available"]) for r in rows] == [
        ("M", "4064958000011", 3), ("L", "4064958000028", 2)]

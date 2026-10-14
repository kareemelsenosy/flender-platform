"""Brand Rules — the per-brand mapping tables, readable and editable by staff.

One page per brand: its base-colour table, size runs, size codes and column
mapping, each row also written out as a sentence; the requests the system has
raised to change them; and the history of every change.
"""
from __future__ import annotations

import io
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import get_current_user_id
from app.core import brand_rules as core
from app.core.base_color import DEFAULT_VOCAB
from app.core.field_mapping import FIELD_NAMES, PLAIN_NAMES
from app.database import get_db
from app.models import BrandRuleSet, CollectionJob, RuleChange, User
from app.services import brand_rules as svc
from app.templates_config import templates

router = APIRouter()


def _ruleset(db: DBSession, key: str, display: str = ""):
    rs = db.query(BrandRuleSet).filter(BrandRuleSet.brand_key == core.brand_key(key)).first()
    if rs is None:
        from app.core.intake import brand_display_name
        name = display or brand_display_name(core.brand_key(key))
        # A key arriving from a URL is lower-case; prefer the spelling a
        # collection used, so the page says "Carhartt WIP", not "carhartt wip".
        job = db.query(CollectionJob).filter(CollectionJob.brand.isnot(None)).all()
        for j in job:
            if core.brand_key(j.brand) == core.brand_key(key):
                name = j.brand
                break
        rs = svc.get_rules(db, name)
    return rs


def _back(key: str, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/brand-rules/{core.brand_key(key)}{anchor}", status_code=302)


@router.get("/brand-rules", response_class=HTMLResponse)
async def rules_index(request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)

    brands: dict[str, dict] = {}
    for rs in db.query(BrandRuleSet).all():
        v = svc.view(rs)
        brands[rs.brand_key] = {"brand": rs.brand, "key": rs.brand_key, "exists": True,
                                "settings": v["settings"],
                                "counts": {t: len(v["tables"][t]) for t in core.TABLE_ORDER}}
    names = {j.brand for j in db.query(CollectionJob).filter(
        CollectionJob.brand.isnot(None)).all() if j.brand}
    names |= {core.brand_key(k) for k in core.SEEDS}
    for name in names:
        key = core.brand_key(name)
        if key and key not in brands:
            seed = core.seed_for(name)
            brands[key] = {"brand": name if name != key else name.title(), "key": key,
                           "exists": False, "settings": seed["settings"],
                           "counts": {t: len(seed["tables"][t]) for t in core.TABLE_ORDER}}

    pending = svc.pending(db)
    per_brand: dict[str, int] = {}
    for p in pending:
        per_brand[p.brand_key] = per_brand.get(p.brand_key, 0) + 1
    for key, b in brands.items():
        b["pending"] = per_brand.get(key, 0)

    return templates.TemplateResponse(request, "brand_rules.html", {
        "user": db.get(User, uid), "training": svc.training_mode(db),
        "note": request.session.pop("rules_note", ""),
        "brands": sorted(brands.values(), key=lambda b: (-b["pending"], b["brand"].lower())),
        "pending_total": len(pending),
        "tables": core.TABLES,
    })


@router.post("/brand-rules/training")
async def rules_training(request: Request, mode: str = Form(...),
                         db: DBSession = Depends(get_db)):
    """Switch training mode on or off for the whole tool."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    applied = svc.set_training_mode(db, mode == "on", user_id=uid)
    request.session["rules_note"] = (
        f"Training mode is on — the system now writes the rules it works out "
        f"straight into the tables. {applied} waiting suggestion"
        f"{'s were' if applied != 1 else ' was'} applied." if mode == "on" else
        "Training mode is off — new rules wait for approval again.")
    target = request.headers.get("referer") or "/brand-rules"
    return RedirectResponse(target if "/brand-rules" in target else "/brand-rules",
                            status_code=302)


@router.post("/brand-rules/new")
async def rules_new(request: Request, brand: str = Form(...),
                    db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    if brand.strip():
        svc.get_rules(db, brand.strip())
        return _back(brand)
    return RedirectResponse("/brand-rules", status_code=302)


SHOW_ROWS = 200


@router.get("/brand-rules/{key}", response_class=HTMLResponse)
async def rules_detail(key: str, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    rs = _ruleset(db, key)
    if rs is None:
        return RedirectResponse("/brand-rules", status_code=302)
    v = svc.view(rs)
    users = {u.id: u.username for u in db.query(User).all()}
    jobs = {j.id: j.label for j in db.query(CollectionJob).filter(
        CollectionJob.user_id == uid).all()}
    vocab = sorted(set(DEFAULT_VOCAB) | {r.get("base") for r in v["tables"]["colours"]
                                         if r.get("base")}
                   | {r.get("base") for r in v["tables"]["stat_codes"] if r.get("base")})
    # An imported colour master holds thousands of codes; the page shows a
    # searchable slice so it stays usable. The download has everything.
    q = (request.query_params.get("q") or "").strip().lower()
    shown, totals = {}, {}
    for t, rows in v["tables"].items():
        totals[t] = len(rows)
        if q and t == "colours":
            rows = [r for r in rows if q in " ".join(str(x) for x in r.values()).lower()]
        shown[t] = rows[:SHOW_ROWS]
    return templates.TemplateResponse(request, "brand_rule_detail.html", {
        "user": db.get(User, uid), "rs": rs, "settings": v["settings"],
        "tables": shown, "totals": totals, "q": q, "show_rows": SHOW_ROWS,
        "row_key": core.row_key,
        "defs": core.TABLES, "order": core.TABLE_ORDER,
        "setting_defs": core.SETTINGS, "sentence": core.sentence,
        "pending": svc.pending(db, rs.brand), "history": svc.history(db, rs.brand),
        "users": users, "jobs": jobs, "vocab": vocab,
        "field_names": FIELD_NAMES, "plain_names": PLAIN_NAMES,
        "training": svc.training_mode(db),
        "note": request.session.pop("rules_note", ""),
    })


@router.post("/brand-rules/{key}/rows")
async def rules_save_row(key: str, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    table = str(form.get("table") or "")
    if table not in core.TABLES:
        return _back(key)
    rs = _ruleset(db, key)
    row = {c: str(form.get(c) or "") for c, _ in core.TABLES[table]["columns"]}
    original = str(form.get("original_key") or "").strip()
    try:
        new = svc.edit_row(db, rs.brand, table, row, user_id=uid,
                           reason=str(form.get("reason") or "")[:500])
        if original and original.lower() != core.row_key(table, new):
            svc.remove_row(db, rs.brand, table, original, user_id=uid)
        request.session["rules_note"] = f"Saved: {core.sentence(table, new)}"
    except ValueError as exc:
        request.session["rules_note"] = f"Not saved — {exc}"
    return _back(key, f"#{table}")


@router.post("/brand-rules/{key}/rows/delete")
async def rules_delete_row(key: str, request: Request, table: str = Form(...),
                           key_value: str = Form(...), db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    rs = _ruleset(db, key)
    if table in core.TABLES and svc.remove_row(db, rs.brand, table, key_value, user_id=uid):
        request.session["rules_note"] = f"Removed {key_value} from {core.TABLES[table]['label'].lower()}."
    return _back(key, f"#{table}")


@router.post("/brand-rules/{key}/settings")
async def rules_settings(key: str, request: Request, db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    rs = _ruleset(db, key)
    current = svc.view(rs)["settings"]
    for name in core.SETTINGS:
        value = form.get(name) == "on"
        if bool(current.get(name)) != value:
            svc.set_setting(db, rs.brand, name, value, user_id=uid)
    request.session["rules_note"] = "Settings saved — regenerate packages to apply them."
    return _back(key)


@router.post("/brand-rules/{key}/import-colours")
async def rules_import_colours(key: str, request: Request,
                               file: UploadFile = File(...),
                               db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    rs = _ruleset(db, key)
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".xlsx"
    if suffix not in (".xlsx", ".xls"):
        request.session["rules_note"] = "The colour table must be an Excel file."
        return _back(key, "#colours")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        parsed = svc.import_colour_table(db, rs.brand, path, user_id=uid,
                                         filename=file.filename or "")
        request.session["rules_note"] = (
            f"Imported {len(parsed['colours'])} colours and "
            f"{len(parsed['stat_codes'])} statistic codes ({'; '.join(parsed['notes'])}).")
    except Exception as exc:
        request.session["rules_note"] = f"Could not read the colour table ({type(exc).__name__})."
    finally:
        os.unlink(path)
    return _back(key, "#colours")


@router.post("/brand-rules/{key}/approve-suggested")
async def rules_approve_suggested(key: str, request: Request,
                                  db: DBSession = Depends(get_db)):
    """Approve every waiting request that already proposes a value.

    A new season can raise dozens of colour suggestions; reading the list
    and approving it in one go is the realistic workflow. Requests with
    nothing proposed stay for a person to fill in.
    """
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    rs = _ruleset(db, key)
    approved = 0
    for change in svc.pending(db, rs.brand):
        if svc.has_proposal(change):
            svc.decide_change(db, change.id, True, user_id=uid)
            approved += 1
    request.session["rules_note"] = (
        f"Approved {approved} suggestion{'s' if approved != 1 else ''}. "
        "Regenerate packages to use them.")
    return _back(key, "#requests")


@router.post("/brand-rules/requests/{change_id}/{verdict}")
async def rules_decide_request(change_id: int, verdict: str, request: Request,
                               db: DBSession = Depends(get_db)):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    change = db.get(RuleChange, change_id)
    if change is None:
        return RedirectResponse("/brand-rules", status_code=302)
    form = await request.form()
    override = {c: str(form.get(c)) for c, _ in core.TABLES.get(change.table_name, {}).get("columns", [])
                if form.get(c) is not None}
    approve = verdict == "approve"
    try:
        svc.decide_change(db, change_id, approve, user_id=uid, override=override)
        request.session["rules_note"] = ("Approved — the rule is now in the table. "
                                         "Regenerate packages to use it." if approve
                                         else "Rejected — it will not be suggested again.")
    except ValueError as exc:
        request.session["rules_note"] = f"Not approved — {exc}"
    target = request.query_params.get("next") or f"/brand-rules/{change.brand_key}#requests"
    if not target.startswith("/"):
        target = "/brand-rules"
    return RedirectResponse(target, status_code=302)


@router.get("/brand-rules/{key}/export")
async def rules_export(key: str, request: Request, db: DBSession = Depends(get_db)):
    """Every table as a spreadsheet, plus a sheet of the rules in words."""
    import pandas as pd

    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=302)
    rs = _ruleset(db, key)
    v = svc.view(rs)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        words = [{"Table": core.TABLES[t]["label"], "Rule": core.sentence(t, r)}
                 for t in core.TABLE_ORDER for r in v["tables"][t]]
        words.insert(0, {"Table": "Settings", "Rule": "; ".join(
            f"{core.SETTINGS[k]['label']}: {'yes' if val else 'no'}"
            for k, val in v["settings"].items() if k in core.SETTINGS)})
        pd.DataFrame(words).to_excel(xw, sheet_name="Rules in words", index=False)
        for t in core.TABLE_ORDER:
            cols = core.TABLES[t]["columns"]
            pd.DataFrame([{label: r.get(c, "") for c, label in cols} for r in v["tables"][t]],
                         columns=[label for _c, label in cols]
                         ).to_excel(xw, sheet_name=core.TABLES[t]["label"][:31], index=False)
    buf.seek(0)
    name = f"{rs.brand.replace(' ', '_')}_brand_rules.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})

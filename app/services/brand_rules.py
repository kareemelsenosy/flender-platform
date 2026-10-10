"""Storing and changing brand rules, with a record of every change.

Two ways a rule changes:

* **Staff edit it** on the Brand Rules page — applied at once, logged.
* **The system asks** — when processing meets a colour or size run the tables
  do not cover, it files a pending request. Nothing changes until a person
  approves it, and approving can correct the proposed value first.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core import brand_rules as core


def _utcnow():
    return datetime.now(timezone.utc)


def get_rules(db, brand: str, *, create: bool = True):
    """The brand's rule set, created from the seeds on first use."""
    from app.models import BrandRuleSet

    key = core.brand_key(brand)
    if not key:
        return None
    rs = db.query(BrandRuleSet).filter(BrandRuleSet.brand_key == key).first()
    if rs is None and create:
        seed = core.seed_for(brand)
        rs = BrandRuleSet(brand_key=key, brand=str(brand).strip()[:255])
        rs.settings = seed["settings"]
        rs.tables = seed["tables"]
        db.add(rs)
        db.commit()
        db.refresh(rs)
    return rs


def view(rs) -> dict:
    """Settings and tables with defaults filled, for reading and display."""
    seed = core.seed_for(rs.brand if rs else "")
    settings = {**seed["settings"], **(rs.settings if rs else {})}
    tables = {name: list((rs.tables if rs else {}).get(name, []))
              for name in core.TABLE_ORDER}
    return {"settings": settings, "tables": tables}


def rules_for(db, brand: str) -> dict:
    """What the packages read. Never fails: an unknown brand gets the seeds."""
    if not brand:
        return core.seed_for("")
    try:
        return view(get_rules(db, brand))
    except Exception:
        db.rollback()
        return core.seed_for(brand)


def _log(db, rs, table, action, row, previous, *, reason="", source="staff",
         status="applied", user_id=None, job_id=None):
    from app.models import RuleChange
    change = RuleChange(
        brand_key=rs.brand_key, table_name=table, action=action,
        row_json=json.dumps(row or {}),
        previous_json=json.dumps(previous) if previous else None,
        reason=(reason or "")[:2000], source=source, status=status,
        job_id=job_id,
        decided_at=_utcnow() if status != "pending" else None,
        decided_by_id=user_id if status != "pending" else None,
    )
    db.add(change)
    return change


def _settle_pending(db, rs, table: str) -> int:
    """Close requests the table now answers (after an import or an edit)."""
    from app.models import RuleChange

    key = core.TABLES[table]["key"]
    have = {str(r.get(key, "")).strip().lower() for r in rs.tables.get(table, [])}
    settled = 0
    for p in db.query(RuleChange).filter(
            RuleChange.brand_key == rs.brand_key, RuleChange.table_name == table,
            RuleChange.status == "pending").all():
        if str(p.row.get(key, "")).strip().lower() in have:
            p.status = "superseded"
            p.decided_at = _utcnow()
            settled += 1
    return settled


def edit_row(db, brand: str, table: str, row: dict, *, user_id=None,
             reason: str = "", source: str = "staff", job_id=None) -> dict:
    """Add or replace one row and record it. Returns the cleaned row."""
    if table not in core.TABLES:
        raise ValueError(f"unknown table {table!r}")
    rs = get_rules(db, brand)
    tables = rs.tables
    rows, old = core.upsert_row(tables.get(table, []), table, row)
    tables[table] = rows
    rs.tables = tables
    rs.updated_by_id = user_id
    new = rows[-1]
    _log(db, rs, table, "upsert", new, old, reason=reason, source=source,
         user_id=user_id, job_id=job_id)
    _settle_pending(db, rs, table)
    db.commit()
    return new


def remove_row(db, brand: str, table: str, key_value: str, *, user_id=None) -> bool:
    rs = get_rules(db, brand)
    tables = rs.tables
    rows, old = core.delete_row(tables.get(table, []), table, key_value)
    if old is None:
        return False
    tables[table] = rows
    rs.tables = tables
    rs.updated_by_id = user_id
    _log(db, rs, table, "delete", old, None, user_id=user_id)
    db.commit()
    return True


def set_setting(db, brand: str, name: str, value, *, user_id=None) -> None:
    if name not in core.SETTINGS:
        raise ValueError(f"unknown setting {name!r}")
    rs = get_rules(db, brand)
    settings = view(rs)["settings"]
    before = settings.get(name)
    settings[name] = value
    rs.settings = settings
    rs.updated_by_id = user_id
    _log(db, rs, "settings", "setting", {name: value}, {name: before},
         user_id=user_id)
    db.commit()


def import_colour_table(db, brand: str, path: str, *, user_id=None,
                        filename: str = "") -> dict:
    """Load the brand's Seasonal Colour Table into its rules."""
    parsed = core.parse_colour_workbook(path)
    rs = get_rules(db, brand)
    tables = rs.tables
    for table in ("colours", "stat_codes"):
        rows = tables.get(table, [])
        for row in parsed[table]:
            try:
                rows, _old = core.upsert_row(rows, table, row)
            except ValueError:
                continue
        tables[table] = rows
    rs.tables = tables
    rs.updated_by_id = user_id
    _log(db, rs, "colours", "import",
         {"file": filename, "colours": len(parsed["colours"]),
          "stat_codes": len(parsed["stat_codes"])}, None,
         reason="; ".join(parsed["notes"]), user_id=user_id)
    parsed["settled"] = _settle_pending(db, rs, "colours")
    db.commit()
    return parsed


# ── Requests the system raises ───────────────────────────────────────────────

def request_change(db, brand: str, table: str, row: dict, *, reason: str,
                   source: str = "system", job_id=None):
    """File a pending request — once. Returns it, or None if nothing to ask.

    Skipped when the table already holds that key, or an identical request is
    still waiting: regenerating a package must not pile up duplicates.
    """
    from app.models import RuleChange

    rs = get_rules(db, brand)
    row = core.clean_row(table, row)
    key = core.TABLES[table]["key"]
    value = row.get(key, "").lower()
    if not value:
        return None
    if any(str(r.get(key, "")).strip().lower() == value
           for r in rs.tables.get(table, [])):
        return None
    for p in db.query(RuleChange).filter(
            RuleChange.brand_key == rs.brand_key,
            RuleChange.table_name == table,
            RuleChange.status.in_(("pending", "rejected"))).all():
        if str(p.row.get(key, "")).strip().lower() == value:
            # A rejected suggestion is not raised again for the same value.
            return None
    change = _log(db, rs, table, "upsert", row, None, reason=reason,
                  source=source, status="pending", job_id=job_id)
    db.commit()
    return change


def decide_change(db, change_id: int, approve: bool, *, user_id=None,
                  override: dict | None = None):
    """Approve (optionally correcting the value) or reject one request."""
    from app.models import BrandRuleSet, RuleChange

    change = db.get(RuleChange, change_id)
    if change is None or change.status != "pending":
        return None
    change.decided_at = _utcnow()
    change.decided_by_id = user_id
    if not approve:
        change.status = "rejected"
        db.commit()
        return change

    row = {**change.row, **{k: v for k, v in (override or {}).items() if v is not None}}
    rs = db.query(BrandRuleSet).filter_by(brand_key=change.brand_key).first()
    tables = rs.tables
    rows, old = core.upsert_row(tables.get(change.table_name, []),
                                change.table_name, row)
    tables[change.table_name] = rows
    rs.tables = tables
    rs.updated_by_id = user_id
    change.row_json = json.dumps(rows[-1])
    change.previous_json = json.dumps(old) if old else None
    change.status = "approved"
    db.commit()
    return change


def has_proposal(change) -> bool:
    """Whether a request already says what the rule should be."""
    row = change.row
    needed = {"colours": "base", "stat_codes": "base", "size_runs": "meaning",
              "size_codes": "meaning", "columns": "header"}.get(change.table_name)
    return bool(needed and str(row.get(needed) or "").strip())


def pending(db, brand: str | None = None) -> list:
    from app.models import RuleChange
    q = db.query(RuleChange).filter(RuleChange.status == "pending")
    if brand:
        q = q.filter(RuleChange.brand_key == core.brand_key(brand))
    return q.order_by(RuleChange.created_at.desc()).all()


def history(db, brand: str, limit: int = 60) -> list:
    from app.models import RuleChange
    return (db.query(RuleChange)
            .filter(RuleChange.brand_key == core.brand_key(brand),
                    RuleChange.status.notin_(("pending", "superseded")))
            .order_by(RuleChange.created_at.desc()).limit(limit).all())

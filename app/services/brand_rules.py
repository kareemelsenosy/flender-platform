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
    stored = rs.tables if rs else {}
    tables = {}
    for name in core.TABLE_ORDER:
        if name not in stored:
            # A table this brand has never had (added to the tool after the
            # brand was set up) starts from the seed.
            tables[name] = [dict(r) for r in seed["tables"].get(name, [])]
            continue
        rows = [dict(r) for r in stored[name]]
        # Seed values fill cells that are still blank — a column added later
        # (the ring prefix) — and never overwrite what staff entered.
        by_key = {core.row_key(name, r): r for r in seed["tables"].get(name, [])}
        for r in rows:
            src = by_key.get(core.row_key(name, r))
            if src:
                for k, v in src.items():
                    if v and not str(r.get(k) or "").strip():
                        r[k] = v
        tables[name] = rows
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

    have = {core.row_key(table, r) for r in rs.tables.get(table, [])}
    if table == "colours":
        # A request for a colour name is also settled when the table now
        # answers that name through a code row.
        have |= {core._norm_colour(r.get("colour")) for r in rs.tables.get(table, [])}
    settled = 0
    for p in db.query(RuleChange).filter(
            RuleChange.brand_key == rs.brand_key, RuleChange.table_name == table,
            RuleChange.status == "pending").all():
        if core.row_key(table, p.row) in have or \
                (table == "colours" and core._norm_colour(p.row.get("colour")) in have):
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
    tables = view(rs)["tables"]
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
    tables = view(rs)["tables"]
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
    tables = view(rs)["tables"]
    for table in ("colours", "stat_codes"):
        tables[table] = core.upsert_many(tables.get(table, []), table, parsed[table])
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
    value = core.row_key(table, row)
    if not value:
        return None
    if any(core.row_key(table, r) == value for r in rs.tables.get(table, [])):
        return None
    for p in db.query(RuleChange).filter(
            RuleChange.brand_key == rs.brand_key,
            RuleChange.table_name == table,
            RuleChange.status.in_(("pending", "rejected"))).all():
        if core.row_key(table, p.row) == value:
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
    tables = view(rs)["tables"]
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


# ── Training mode ────────────────────────────────────────────────────────────
#
# Off (the default): the system only *asks* — every rule it works out waits as
# a request for a person. On: it learns by itself — what it works out goes
# straight into the brand's tables, marked "training" in the history so it
# can be reviewed and undone. For onboarding a brand, when there are hundreds
# of colours and size runs to learn and nobody wants to click through them.

TRAINING = "training_mode"


def training_mode(db) -> bool:
    from app.models import AppSetting
    try:
        row = db.get(AppSetting, TRAINING)
    except Exception:
        db.rollback()
        return False
    return bool(row and row.value == "on")


def set_training_mode(db, on: bool, *, user_id=None) -> int:
    """Switch training mode. Turning it on also applies every waiting
    suggestion that already says what the rule should be. Returns how many."""
    from app.models import AppSetting
    row = db.get(AppSetting, TRAINING) or AppSetting(key=TRAINING)
    row.value = "on" if on else "off"
    row.updated_by_id = user_id
    db.merge(row)
    db.commit()
    applied = 0
    if on:
        for change in pending(db):
            if has_proposal(change):
                decide_change(db, change.id, True, user_id=user_id)
                change.source = "training"
                applied += 1
        db.commit()
    return applied


def propose_rule(db, brand: str, table: str, row: dict, *, reason: str,
                 source: str = "system", job_id=None):
    """What the system calls when it works out a rule.

    In training mode a rule with a value is written straight into the table;
    otherwise it waits as a request. A suggestion with nothing proposed
    always waits — there is nothing to learn from it yet.
    """
    clean = core.clean_row(table, row)
    needed = _PROPOSAL_FIELD.get(table)
    if training_mode(db) and needed and clean.get(needed):
        rs = get_rules(db, brand)
        if any(core.row_key(table, r) == core.row_key(table, clean)
               for r in rs.tables.get(table, [])):
            return None
        return edit_row(db, brand, table, clean, source="training", job_id=job_id,
                        reason=f"Learned in training mode. {reason}")
    return request_change(db, brand, table, row, reason=reason, source=source,
                          job_id=job_id)


_PROPOSAL_FIELD = {"colours": "base", "stat_codes": "base", "size_runs": "meaning",
                   "size_codes": "meaning", "columns": "header"}


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


# ── Templates and learning ───────────────────────────────────────────────────

def save_template(db, brand: str, package: str, entries: list[dict], *,
                  user_id=None, reason: str = "", source: str = "staff") -> None:
    """Store a brand's column template for one package (None resets it)."""
    from app.core.output_templates import clean
    rs = get_rules(db, brand)
    settings = view(rs)["settings"]
    templates = dict(settings.get("templates") or {})
    if entries is None:
        templates.pop(package, None)
    else:
        templates[package] = clean(package, entries)
    settings["templates"] = templates
    rs.settings = settings
    rs.updated_by_id = user_id
    included = sum(1 for e in templates.get(package, []) if e.get("include"))
    _log(db, rs, "templates", "template",
         {"package": package,
          "columns": included if entries is not None else "standard"},
         None, reason=reason, source=source, user_id=user_id)
    db.commit()


def apply_learned(db, brand: str, learned: dict, *, filename: str = "",
                  user_id=None, job_id=None) -> list[str]:
    """Write what a finished sheet taught into the brand's rules.

    Applied directly: a finished sheet is a person's answer, not a guess.
    Returns plain sentences of what changed, for the page.
    """
    rs = get_rules(db, brand)
    tables = view(rs)["tables"]
    done: list[str] = []
    why = f"Learned from the finished sheet {filename}".strip()

    groups = learned.get("item_groups") or {}
    if groups.get("by_product_word"):
        rows = [{"supplier": val, "sap": ", ".join(g)} for val, g in groups["map"].items()]
        tables["item_groups"] = core.upsert_many(tables.get("item_groups", []),
                                                 "item_groups", rows)
        settings = view(rs)["settings"]
        settings["group_by_product_word"] = True
        rs.settings = settings
        examples = ", ".join(f"{k.title()} → {v[0]}" for k, v in list(groups["map"].items())[:4])
        done.append(f"Product group read from the product word that ends each name "
                    f"({examples}…): {groups['total']} words mapped to SAP item groups.")
    elif groups.get("column"):
        tables["columns"], _ = core.upsert_row(
            tables.get("columns", []), "columns",
            {"field": "Item Group", "header": groups["column"]})
        rows = [{"supplier": val, "sap": ", ".join(g)} for val, g in groups["map"].items()]
        tables["item_groups"] = core.upsert_many(tables.get("item_groups", []),
                                                 "item_groups", rows)
        done.append(f"Product group read from the “{groups['column']}” column: "
                    f"{groups['total']} categories mapped to SAP item groups "
                    f"({groups['clean']} map to exactly one).")
    if learned.get("size_lists"):
        tables["size_lists"] = core.upsert_many(tables.get("size_lists", []),
                                                "size_lists", learned["size_lists"])
        done.append(f"{len(learned['size_lists'])} SAP size lists with their sort indexes "
                    f"({', '.join(r['list_code'] for r in learned['size_lists'])}).")
    rs.tables = tables
    rs.updated_by_id = user_id
    _log(db, rs, "learned", "learn", {"file": filename, "lines": learned.get("lines"),
                                      "matched": learned.get("matched")},
         None, reason=why + ". " + " ".join(done), source="learned",
         user_id=user_id, job_id=job_id)
    db.commit()

    learned_settings = dict(learned.get("settings") or {})
    if learned.get("colours"):
        rs = get_rules(db, brand)
        tables = view(rs)["tables"]
        tables["colours"] = core.upsert_many(tables.get("colours", []), "colours",
                                             learned["colours"])
        rs.tables = tables
        learned_settings["uses_base_colour"] = True
        done.append(f"{len(learned['colours'])} colours with their base colour.")
    if learned_settings:
        rs = get_rules(db, brand)
        settings = view(rs)["settings"]
        settings.update(learned_settings)
        rs.settings = settings
        db.commit()
        words = []
        if "gender_map" in learned_settings:
            words.append("gender: " + ", ".join(f"{k or '(blank)'} = {v}"
                                                for k, v in learned_settings["gender_map"].items()))
        if learned_settings.get("hs_digits"):
            words.append(f"HS codes kept to {learned_settings['hs_digits']} digits")
        if learned_settings.get("style_has_colour_code"):
            words.append("the colour code is taken off the end of the style code")
        if learned_settings.get("one_size"):
            words.append("every item is one-size in SAP (the supplier's Size column is not a size)")
        if learned_settings.get("colour_slash") == "keep":
            words.append("colours keep their slash (Black/Tortoise)")
        if learned_settings.get("barcode_size") == "supplier":
            words.append("built barcodes end with the supplier's own size (OS, S/M)")
        if words:
            done.append("Also learned " + "; ".join(words) + ".")

    if learned.get("keep_upper"):
        settings = view(rs)["settings"]
        words = sorted(set(settings.get("keep_upper") or []) | set(learned["keep_upper"]))
        settings["keep_upper"] = words
        rs.settings = settings
        db.commit()
        done.append(f"Words kept in capitals in descriptions: {', '.join(learned['keep_upper'])}.")

    template, constants = learned.get("template") or ([], {})
    if template:
        save_template(db, brand, "temp", template, user_id=user_id, reason=why,
                      source="learned")
        fixed = ", ".join(f"{k} = {v}" for k, v in constants.items())
        done.append(f"TEMP columns set to the sheet's {len(template)} columns"
                    + (f"; fixed values {fixed}." if fixed else "."))
    return done


def generate_base_rules(db, brand: str, mappings: dict, legend: dict, *,
                        job_id=None, source_files: str = "") -> list[str]:
    """The first time a brand's files are read, write down what was found.

    The specification: the programme generates the base rules for a brand,
    then hands control to staff. Detected column mappings and the size-scale
    letters an order form defines become the brand's rules — visible, editable
    and in the history — instead of being worked out silently every time.
    Tables that already hold rules are left exactly as they are.
    """
    rs = get_rules(db, brand)
    tables = view(rs)["tables"]
    done: list[str] = []

    if not tables.get("columns"):
        per_field: dict[str, list[str]] = {}
        for m in (mappings or {}).values():
            for field, info in (m.get("fields") or {}).items():
                header = (info or {}).get("header")
                if header and info.get("source") in ("detected", "same name"):
                    per_field.setdefault(field, [])
                    if header not in per_field[field]:
                        per_field[field].append(header)
        rows = [{"field": f, "header": " | ".join(h)} for f, h in per_field.items()]
        if rows:
            tables["columns"] = core.upsert_many([], "columns", rows)
            done.append(f"{len(rows)} column mappings")

    if legend and not tables.get("size_codes"):
        rows = [{"order_code": code, "meaning": ", ".join(sizes[:2]) + ("…" if len(sizes) > 2 else ""),
                 "sizes": ", ".join(sizes)} for code, sizes in legend.items()]
        tables["size_codes"] = core.upsert_many([], "size_codes", rows)
        done.append(f"{len(rows)} size-scale codes from the order form's legend")

    if done:
        rs.tables = tables
        _log(db, rs, "base", "generate", {"file": source_files, "made": done}, None,
             reason="Base rules generated the first time this brand's files were read",
             source="generated", job_id=job_id)
        db.commit()
    return done

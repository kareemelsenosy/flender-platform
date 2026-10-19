"""Which columns each brand's SAP sheets carry, in what order, under what name.

Every brand starts from the standard template — the full TEMP and SAP Product
Creation layouts — and keeps only what applies to it. HUF's TEMP has 21
columns, not 31; House Colour and Finish only mean something for Carhartt and
Edwin, whose colour codes are house colour + finish. A column can be added
(with a fixed value, e.g. U_VCName = P), removed, renamed or reordered, and
the template is applied when the sheet is written, so everything upstream
keeps working on the full set.

Pure: no DB. Templates are stored in the brand's settings under
``templates`` as ``{package: [{"column", "label", "include", "value"}]}``.
"""
from __future__ import annotations

from app.core.sap_creation import SAP_COLUMNS
from app.core.temp_template import TEMP_COLUMNS

PACKAGES = {"temp": "TEMP Standard Template",
            "product_creation": "SAP Product Creation"}

# Heading Flender's staff use for a column, where it differs from the name
# the package builder uses internally.
LABELS = {"product_creation": {"Item No.": "Item Code"}}

# Only meaningful when the colour code is house colour + finish.
COLOUR_CODE_ONLY = {"House Colour", "Finish"}

# HUF's own TEMP STANDARD TEMPLATE (2026 Q4): 21 columns.
HUF_TEMP = ["U_Season", "U_NRStatus", "U_CodeBars", "U_ESDate", "U_SuppCatNum",
            "U_Gender", "U_ItmsGrpCod", "U_SCode", "U_SizeCode",
            "U_Webbeschreibung2", "U_BaseColor", "U_Material", "U_HS_Code",
            "U_COO", "U_VCName", "U_Webbeschreibung", "U_def_size_list_code",
            "U_Web_Color", "U_SortIndex", "U_webbeschreibung_gr", "U_LongDscp"]


def standard_columns(package: str) -> list[str]:
    return list(TEMP_COLUMNS if package == "temp" else SAP_COLUMNS)


def default_template(package: str, *, uses_colour_codes: bool = False,
                     brand_key: str = "") -> list[dict]:
    """The standard template, trimmed to what the brand can use."""
    labels = LABELS.get(package, {})
    if package == "temp" and brand_key == "huf":
        cols = [{"column": c, "label": c, "include": True,
                 "value": "P" if c == "U_VCName" else ""} for c in HUF_TEMP]
        cols += [{"column": c, "label": c, "include": False, "value": ""}
                 for c in TEMP_COLUMNS if c not in HUF_TEMP]
        return cols
    return [{"column": c, "label": labels.get(c, c),
             "include": uses_colour_codes or c not in COLOUR_CODE_ONLY,
             "value": ""}
            for c in standard_columns(package)]


def template_for(package: str, settings: dict, brand_key: str = "") -> list[dict]:
    """The brand's template: what staff saved, else the default.

    Standard columns that did not exist when a template was saved are added
    at the end, switched off, so a new SAP field never silently appears or
    disappears from a brand's sheets.
    """
    saved = ((settings or {}).get("templates") or {}).get(package)
    if not saved:
        return default_template(package,
                                uses_colour_codes=bool((settings or {}).get("uses_colour_codes")),
                                brand_key=brand_key)
    known = {c["column"] for c in saved}
    return list(saved) + [{"column": c, "label": c, "include": False, "value": ""}
                          for c in standard_columns(package) if c not in known]


def apply(out: dict, template: list[dict]) -> dict:
    """Reduce, reorder, rename and fill the package's columns for writing."""
    chosen = [c for c in template if c.get("include")]
    if not chosen:
        return out
    rows = []
    for r in out.get("rows", []):
        new = {}
        for c in chosen:
            label = c.get("label") or c["column"]
            value = c.get("value") or ""
            new[label] = value if value else r.get(c["column"], "")
        rows.append(new)
    return {**out, "rows": rows,
            "columns": [c.get("label") or c["column"] for c in chosen]}


def clean(package: str, entries: list[dict]) -> list[dict]:
    """Validate a template posted from the page."""
    out, seen = [], set()
    for e in entries:
        col = str(e.get("column") or "").strip()
        if not col or col in seen:
            continue
        seen.add(col)
        out.append({"column": col,
                    "label": str(e.get("label") or col).strip() or col,
                    "include": bool(e.get("include")),
                    "value": str(e.get("value") or "").strip()})
    return out

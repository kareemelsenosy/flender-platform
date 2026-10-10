"""Temporary base colours for names the brand's colour table does not cover.

The specification: when a colour is missing from the Seasonal Colour Table,
flag it and add a temporary colour based on the brand's catalogue and
palette. The catalogue is where an opaque name like "Ikola" is shown next to
words a person (or a model) can read — "Ikola — a washed lemon yellow".

One model call covers every missing name in the collection, so a season with
forty new colours costs one request, not forty. When no model is available
the names simply stay unanswered for a person to decide; nothing is invented.
"""
from __future__ import annotations

import json
import os
import re

_MAX_PAGES = 80
_MAX_SNIPPETS = 3


def catalogue_text(paths: list[str]) -> list[str]:
    """Lines of text from the catalogue files — PDF pages or spreadsheet rows."""
    lines: list[str] = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages[:_MAX_PAGES]:
                        lines.extend((page.extract_text() or "").splitlines())
            elif ext in (".xlsx", ".xls", ".csv"):
                import pandas as pd
                frames = ([pd.read_csv(path, dtype=str, header=None)] if ext == ".csv"
                          else pd.read_excel(path, sheet_name=None, dtype=str,
                                             header=None).values())
                for df in frames:
                    for row in df.fillna("").astype(str).values.tolist()[:5000]:
                        text = " | ".join(v for v in row if v.strip())
                        if text:
                            lines.append(text)
        except Exception:
            continue
    return lines


def snippets_for(names: list[str], lines: list[str]) -> dict[str, list[str]]:
    """Where each colour name is mentioned in the catalogue, a few lines each."""
    out: dict[str, list[str]] = {}
    for name in names:
        pattern = re.compile(r"\b" + re.escape(name.strip()) + r"\b", re.I)
        hits = []
        for line in lines:
            if pattern.search(line):
                hits.append(line.strip()[:200])
                if len(hits) >= _MAX_SNIPPETS:
                    break
        if hits:
            out[name] = hits
    return out


def suggest(brand: str, names: list[str], vocab: list[str],
            snippets: dict[str, list[str]] | None = None,
            palette: list[str] | None = None) -> dict[str, dict]:
    """Ask a model for a base colour per name, constrained to ``vocab``.

    Returns ``{name: {"value", "why"}}`` for the names it could answer.
    """
    names = [n for n in dict.fromkeys(names) if n and n.strip()][:120]
    if not names:
        return {}
    try:
        from app.services.ai_service import _call_ai, ai_available
    except Exception:
        return {}
    if not ai_available():
        return {}

    snippets = snippets or {}
    listing = []
    for n in names:
        seen = snippets.get(n)
        listing.append(f"- {n}" + (f"  (catalogue: {' / '.join(seen)})" if seen else ""))
    known = ""
    if palette:
        known = ("\nThe brand already classifies these colours like this "
                 f"(its palette): {'; '.join(palette[:60])}\n")
    prompt = (
        f"You classify {brand or 'fashion'} colour names into a base colour.\n"
        f"Allowed base colours: {', '.join(vocab)}.\n"
        "Use the catalogue mention when given; otherwise use what the name "
        "means as a colour (e.g. Sky Blue -> Blue). A patterned name (camo, "
        "check, stripe, print) is Multicolour. If you genuinely cannot tell, "
        "omit the name.\n" + known +
        "Return ONLY JSON: {\"Name\": {\"base\": \"Blue\", \"why\": \"short reason\"}}\n\n"
        "Colours:\n" + "\n".join(listing))
    try:
        text = _call_ai(prompt, max_tokens=2000)
    except Exception:
        return {}
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}

    allowed = {v.lower(): v for v in vocab}
    out: dict[str, dict] = {}
    for n in names:
        item = data.get(n) or data.get(n.strip())
        if not isinstance(item, dict):
            continue
        base = allowed.get(str(item.get("base") or "").strip().lower())
        if base:
            source = "catalogue" if snippets.get(n) else "colour name"
            why = str(item.get("why") or "").strip()[:160]
            out[n] = {"value": base,
                      "why": f"suggested from the {source}" + (f": {why}" if why else "")}
    return out

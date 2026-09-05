"""BASE COLOR resolution — Operations OS.

SAP stores a controlled ``BASE COLOR`` alongside the supplier's own colour name:
200 Carhartt colour names collapse to 11 base colours. A new season brings
hundreds of names that have never been classified, and every one of them blocks
the SAP creation sheet until somebody decides.

Three tiers, most reliable first:

  1. **History** — the same colour name was classified in a previous season.
     Certain, and needs no review.
  2. **Rules** — the name decomposes ("Steel Blue", "Alton Check, Cypress",
     "Obsidian / Shady Grey"). Proposed with a reason, for one-click approval.
  3. **Unknown** — an opaque name ("Pumice", "Styx", "Moor"). Goes to a human.

The rules were fitted against Carhartt FW26, where the correct answer is known
for all 200 colours, so their accuracy is measured rather than assumed.
"""
from __future__ import annotations

import re

# Base colours Flender actually uses. Learned lookups override this; it exists
# so a brand with no history still has a target vocabulary.
DEFAULT_VOCAB = ["Beige", "Black", "Blue", "Brown", "Green", "Grey",
                 "Multicolour", "Pink", "Purple", "Red", "White"]

# A patterned colour is Multicolour regardless of the colour named after it:
# "Alton Check, Cypress" is Multicolour, not Green.
PATTERN_WORDS = ("camo", "check", "stripe", "stripes", "print", "floral",
                 "paisley", "plaid", "tartan", "tie dye", "tie-dye", "graphic",
                 "houndstooth", "jacquard", "argyle", "gingham", "herringbone")

# Supplier colour words that are not themselves base colours.
SYNONYMS = {
    "navy": "Blue", "denim": "Blue", "indigo": "Blue", "teal": "Blue",
    "aqua": "Blue", "cobalt": "Blue", "azure": "Blue", "sapphire": "Blue",
    "olive": "Green", "khaki": "Green", "sage": "Green", "moss": "Green",
    "mint": "Green", "emerald": "Green", "lime": "Green",
    "charcoal": "Grey", "silver": "Grey", "ash": "Grey", "smoke": "Grey",
    "graphite": "Grey", "slate": "Grey", "gray": "Grey",
    "cream": "Beige", "sand": "Beige", "tan": "Beige", "ecru": "Beige",
    "camel": "Beige", "stone": "Beige", "oat": "Beige",
    "natural": "White", "wax": "White", "ivory": "White", "chalk": "White",
    "burgundy": "Red", "wine": "Red", "rust": "Red", "maroon": "Red",
    "scarlet": "Red", "crimson": "Red",
    "tobacco": "Brown", "chocolate": "Brown", "coffee": "Brown",
    "hamilton": "Brown", "walnut": "Brown", "hazel": "Brown",
    "lilac": "Purple", "violet": "Purple", "lavender": "Purple",
    "rose": "Pink", "blush": "Pink", "fuchsia": "Pink",
}

_MAX_DEPTH = 3


def normalise(name: str) -> str:
    """Collapse whitespace and casing differences in a colour name."""
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _canonical(value: str) -> str:
    """Title-case a base colour so 'BLACK' and 'Black' stop being two values."""
    return normalise(value).title()


def learn_lookup(pairs) -> dict[str, str]:
    """Build a colour-name -> base-colour lookup from historical rows.

    ``pairs`` is an iterable of ``(colour_name, base_colour)``. Casing is
    canonicalised on both sides, which is why a history containing both
    'BLACK' and 'Black' collapses to one entry.
    """
    out: dict[str, str] = {}
    for name, base in pairs:
        name, base = normalise(name), _canonical(base)
        if name and base:
            out.setdefault(name.lower(), base)
    return out


def audit_lookup(pairs) -> dict:
    """Report contradictions in a historical BASE COLOR set.

    Two kinds matter, and both were present in Carhartt FW26:
      * ``casing`` — the same base colour stored as 'BLACK' and 'Black'
      * ``conflicts`` — one colour name classified two different ways
      * ``pattern_exceptions`` — a patterned name given a specific colour
        while the great majority of patterned names are Multicolour
    """
    raw: dict[str, set] = {}
    seen_bases: set[str] = set()
    for name, base in pairs:
        name, base = normalise(name), normalise(base)
        if not name or not base:
            continue
        seen_bases.add(base)
        raw.setdefault(name.lower(), set()).add(_canonical(base))

    casing: dict[str, list[str]] = {}
    for b in seen_bases:
        casing.setdefault(_canonical(b), []).append(b)
    casing = {k: sorted(v) for k, v in casing.items() if len(set(v)) > 1}

    conflicts = {n: sorted(v) for n, v in raw.items() if len(v) > 1}

    pattern_exceptions = [
        (n, next(iter(v))) for n, v in raw.items()
        if _has_pattern(n) and next(iter(v)) != "Multicolour" and len(v) == 1
    ]
    return {
        "casing_duplicates": casing,
        "conflicts": conflicts,
        "pattern_exceptions": sorted(pattern_exceptions),
    }


def _has_pattern(text: str) -> bool:
    low = f" {text.lower()} "
    return any(f"{w}" in low for w in PATTERN_WORDS)


def _match_word(text: str, vocab: list[str]) -> "tuple[str | None, str]":
    """Look for a base colour or a known synonym, head noun first."""
    lowered = {v.lower(): v for v in vocab}
    words = [w.strip(",.").lower() for w in re.split(r"[\s\-/]+", text) if w.strip(",.")]
    for word in reversed(words):          # "Steel Blue" -> Blue
        if word in lowered:
            return lowered[word], f"named '{word}'"
    for word in reversed(words):
        if word in SYNONYMS and SYNONYMS[word] in vocab:
            return SYNONYMS[word], f"'{word}' is {SYNONYMS[word]}"
    return None, "unknown"


def propose(name: str, vocab: list[str] | None = None,
            lookup: dict[str, str] | None = None, _depth: int = 0) -> dict:
    """Resolve one supplier colour name to a base colour.

    Returns ``{"value", "confidence", "source", "reason"}``. ``value`` is None
    when nothing can be proposed, which is a decision for a human rather than a
    guess to be quietly written into SAP.
    """
    vocab = vocab or DEFAULT_VOCAB
    clean = normalise(name)
    if not clean:
        return {"value": None, "confidence": 0.0, "source": "none",
                "reason": "no colour given"}

    if lookup:
        hit = lookup.get(clean.lower())
        if hit:
            return {"value": hit, "confidence": 1.0, "source": "history",
                    "reason": "classified in a previous season"}

    if _depth > _MAX_DEPTH:
        return {"value": None, "confidence": 0.0, "source": "rule",
                "reason": "unknown"}

    # A pattern name outranks any colour mentioned with it.
    if _has_pattern(clean) and "Multicolour" in vocab:
        return {"value": "Multicolour", "confidence": 0.85, "source": "rule",
                "reason": "patterned colour name"}

    # "Obsidian / Shady Grey" — the first colour leads, and it decides alone.
    # Falling through to the whole string would let the second colour answer:
    # "Palisander / Black" is Brown, not Black.
    if "/" in clean:
        inner = propose(clean.split("/")[0], vocab, lookup, _depth + 1)
        if inner["value"]:
            return {**inner, "confidence": min(inner["confidence"], 0.8),
                    "source": "rule",
                    "reason": f"first of two colours; {inner['reason']}"}
        return {"value": None, "confidence": 0.0, "source": "unknown",
                "reason": f"leading colour '{clean.split('/')[0].strip()}' is unknown"}

    # "Sundling Stripes, Dark Navy" — the colour follows the comma.
    if "," in clean:
        inner = propose(clean.split(",")[-1], vocab, lookup, _depth + 1)
        if inner["value"]:
            return {**inner, "confidence": min(inner["confidence"], 0.8),
                    "source": "rule",
                    "reason": f"colour after the comma; {inner['reason']}"}

    value, reason = _match_word(clean, vocab)
    if value:
        return {"value": value, "confidence": 0.8, "source": "rule",
                "reason": reason}
    return {"value": None, "confidence": 0.0, "source": "unknown",
            "reason": "no recognisable colour word"}


def resolve_all(names, vocab=None, lookup=None) -> dict[str, dict]:
    """Propose a base colour for every distinct name. Keyed by the given name."""
    return {n: propose(n, vocab, lookup) for n in dict.fromkeys(names) if normalise(n)}

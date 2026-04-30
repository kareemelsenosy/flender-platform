"""Local folder image search — match images to SKUs by filename.

Two filename styles are supported:

  1. SKU-coded — filename contains the item_code (e.g. "BG264943-BLACK_01.jpg").
  2. Descriptive — filename built from style + color (e.g.
     "Express Side Bag Black 1.jpg"). Common when a brand ships its own image
     library separate from the order sheet's SKUs.

Each candidate is returned with a ``reason`` field that explains WHY it
matched. The reviewer UI surfaces this so a user who doesn't know what every
SKU looks like can still tell at a glance whether to trust the suggestion:

  • "exact code"            — the full item code is in the filename
  • "similar code (PREFIX)" — only the base code is present (e.g. excel says
                              H02628_27BC, file says H02628 — same family,
                              different variant)
  • "style + colour"        — filename matches both the product name and the
                              colour from the sheet
  • "style match"           — filename matches the product name but no colour
  • "colour only"           — filename matches the colour but nothing else
                              (low-confidence)
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

_PRIMARY_RE = re.compile(r"(?:^|[\s_\-.])(?:0*1|01|001|f|front|main|primary)(?:$|[\s_\-.])", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[-_ .,/\\]+")

_COLOR_WORDS = {
    "black", "white", "red", "blue", "green", "yellow", "pink", "purple",
    "orange", "brown", "grey", "gray", "navy", "beige", "cream", "tan",
    "khaki", "olive", "burgundy", "charcoal", "ivory", "gold", "silver",
    "multi", "ash", "forest", "rust", "stone", "sand", "cement", "plum",
    "teal", "rose", "mint", "coral", "wine", "indigo", "bronze", "fatigue",
    "army", "matcha", "russet", "clay", "chestnut",
}


def _tokens(text: str, min_len: int = 2) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if len(t) >= min_len]


def _base_code(item_code: str) -> str:
    """Return the leading SKU stem before the first variant separator.

    Examples:
        H02628_27BC  → H02628
        BG264943-BLACK → BG264943
        ACL-253-SC-447-001 → ACL  (mostly useless on its own — we only count
                                   it as a similar-code hint when it's at
                                   least 4 characters and contains a digit)

    Returns "" if no useful base could be extracted.
    """
    if not item_code:
        return ""
    parts = re.split(r"[-_ .]", item_code, maxsplit=1)
    head = parts[0] if parts else ""
    if len(head) >= 4 and any(c.isdigit() for c in head):
        return head.lower()
    # Try the first two segments joined (handles ACL-253 style)
    parts = re.split(r"[-_ .]", item_code)
    if len(parts) >= 2:
        joined = parts[0] + parts[1]
        if len(joined) >= 5 and any(c.isdigit() for c in joined):
            return joined.lower()
    return ""


def search_local_folder(folder_path: str, item: dict, max_results: int = 5) -> list[dict]:
    """
    Search a local folder for images matching the item.

    Returns list of {"path", "filename", "score" (0.0-1.0), "reason"} sorted
    by score descending. Candidates below 0.25 are dropped.
    """
    if not folder_path or not os.path.isdir(folder_path):
        return []

    item_code = str(item.get("item_code") or "").strip()
    color_code = str(item.get("color_code") or "").strip()
    color_name = str(item.get("color_name") or "").strip()
    style_name = str(item.get("style_name") or "").strip()
    item_group = str(item.get("item_group") or "").strip()

    if not item_code and not style_name:
        return []

    code_clean = re.sub(r"[-_ .]", "", item_code).lower()
    color_tokens = list({*_tokens(color_name, min_len=2), *_tokens(color_code, min_len=2)})
    color_token_set = set(color_tokens)
    # Drop colour words from style/group tokens so a SKU like BG264943-BLACK
    # or an item group like "BUT A BG264935 Yellow" doesn't double-count the
    # colour against unrelated filenames that just happen to share it.
    style_tokens = [t for t in _tokens(style_name, min_len=3) if t not in _COLOR_WORDS and t not in color_token_set]
    item_group_tokens = [t for t in _tokens(item_group, min_len=3) if t not in _COLOR_WORDS and t not in color_token_set]
    base_code = _base_code(item_code)

    results: list[dict] = []

    for root, _dirs, files in os.walk(folder_path):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue

            name_no_ext = os.path.splitext(filename)[0]
            name_lower = name_no_ext.lower()
            name_clean = re.sub(r"[-_ .]", "", name_lower)
            file_tokens = set(_tokens(name_lower, min_len=2))

            score, reason = _score_match(
                name_lower=name_lower,
                name_clean=name_clean,
                file_tokens=file_tokens,
                code_clean=code_clean,
                code_lower=item_code.lower(),
                base_code=base_code,
                style_tokens=style_tokens,
                color_tokens=color_tokens,
                item_group_tokens=item_group_tokens,
            )

            if score >= 0.25:
                full_path = os.path.join(root, filename)
                results.append({
                    "path": os.path.abspath(full_path),
                    "filename": filename,
                    "score": round(min(score, 1.0), 2),
                    "reason": reason,
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


def _score_match(
    *,
    name_lower: str,
    name_clean: str,
    file_tokens: set[str],
    code_clean: str,
    code_lower: str,
    base_code: str,
    style_tokens: list[str],
    color_tokens: list[str],
    item_group_tokens: list[str],
) -> tuple[float, str]:
    """Score a filename and explain why. Returns (score, reason)."""
    score = 0.0
    reasons: list[str] = []

    # ── SKU-coded filename ───────────────────────────────────────────────────
    code_evidence = False
    if code_clean and code_clean in name_clean:
        score += 0.65
        reasons.append("exact code")
        code_evidence = True
    elif code_lower and code_lower in name_lower:
        score += 0.60
        reasons.append("exact code")
        code_evidence = True
    else:
        # Token match against the SKU's non-colour parts (e.g. "BG264943"
        # without the trailing -BLACK).
        sku_tokens = [
            t for t in _TOKEN_SPLIT.split(code_lower)
            if len(t) >= 4 and t not in _COLOR_WORDS
        ]
        sku_token_full_match = False
        if sku_tokens:
            matched = sum(1 for t in sku_tokens if t in name_lower)
            if matched == len(sku_tokens):
                score += 0.55
                sku_token_full_match = True
                reasons.append("exact code")
                code_evidence = True
            elif matched >= 1:
                score += 0.20
                code_evidence = True

        # Similar-code hint: filename contains the base/family code but not
        # the variant suffix. Worth surfacing even when other evidence is
        # weak — the reviewer can decide whether the variant matters.
        if base_code and not sku_token_full_match and base_code in name_clean:
            score += 0.30
            reasons.append(f"similar code ({base_code.upper()})")
            code_evidence = True

        # Item-group tokens (e.g. "BG264943" lifted from "BUT BA BG264943 Black")
        if item_group_tokens:
            ig_matches = sum(1 for t in item_group_tokens if len(t) >= 5 and t in name_lower)
            if ig_matches:
                score += min(0.25, 0.15 * ig_matches)
                code_evidence = True

        # Fuzzy fallback when the SKU is mangled
        if score < 0.4 and code_clean:
            ratio = SequenceMatcher(None, code_clean, name_clean).ratio()
            if ratio > 0.85:
                score += 0.30
                reasons.append("fuzzy code")
                code_evidence = True

    # ── Descriptive filename: style + color ──────────────────────────────────
    style_match_ratio = 0.0
    if style_tokens:
        style_hits = sum(1 for t in style_tokens if t in file_tokens or t in name_clean)
        style_match_ratio = style_hits / len(style_tokens)
        if style_match_ratio == 1.0:
            score += 0.55
        elif style_match_ratio >= 0.75:
            score += 0.40
        elif style_match_ratio >= 0.5:
            score += 0.22
        elif style_match_ratio > 0:
            score += 0.08

    # If we matched nothing about the SKU and nothing about the style, this
    # file is just a colour collision — drop it.
    if not code_evidence and style_tokens and style_match_ratio == 0:
        return 0.0, ""

    color_hit = False
    if color_tokens:
        color_hit = any(t in file_tokens or t in name_lower for t in color_tokens)
        if color_hit:
            score += 0.25
        else:
            # Penalise when the filename advertises a recognised colour and we
            # have a recognised colour of our own — they disagree. We don't
            # penalise unrecognised colours ("Bronze Green" → "Green") since
            # those are handled by tokenisation already.
            file_colors = file_tokens & _COLOR_WORDS
            our_color_words = any(t in _COLOR_WORDS for t in color_tokens)
            # Soften the penalty when the wrong colour is likely a variant
            # of ours (e.g. excel "Bronze Green" / file "Bronze") — in that
            # case any of our tokens appears as a substring of the filename.
            if file_colors and our_color_words:
                substring_overlap = any(
                    t for t in color_tokens
                    if len(t) >= 3 and (t in name_lower or name_lower in t)
                )
                score -= 0.10 if substring_overlap else 0.20

    # ── Primary-image bonus ──────────────────────────────────────────────────
    if score > 0 and _PRIMARY_RE.search(" " + name_lower + " "):
        score += 0.06

    # ── Build reason string ──────────────────────────────────────────────────
    if not reasons:
        if style_match_ratio >= 0.5 and color_hit:
            reasons.append("style + colour")
        elif style_match_ratio >= 0.5:
            reasons.append("style match")
        elif color_hit:
            reasons.append("colour only")

    reason = ", ".join(reasons) if reasons else "weak match"
    return max(0.0, score), reason


def scan_folder_summary(folder_path: str) -> dict[str, Any]:
    """Get a summary of images in a folder for display."""
    if not folder_path or not os.path.isdir(folder_path):
        return {"exists": False, "count": 0, "path": folder_path}

    count = 0
    for _root, _dirs, files in os.walk(folder_path):
        for f in files:
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                count += 1

    return {"exists": True, "count": count, "path": folder_path}

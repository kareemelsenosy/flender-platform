"""Local folder image search — match images to SKUs by filename.

Two filename styles are supported:

  1. SKU-coded — filename contains the item_code (e.g. "BG264943-BLACK_01.jpg").
  2. Descriptive — filename built from style + color (e.g.
     "Express Side Bag Black 1.jpg"). Common when a brand ships its own image
     library separate from the order sheet's SKUs.

For style (2) we match on tokens from `style_name` + `color_name` /
`color_code`. The previous implementation only credited `style_name` with a
flat +0.15 and ignored `color_name` entirely, which left every descriptive
match below the LOW threshold.
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
    "teal", "rose", "mint", "coral", "wine", "indigo",
}


def _tokens(text: str, min_len: int = 2) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if len(t) >= min_len]


def search_local_folder(folder_path: str, item: dict, max_results: int = 5) -> list[dict]:
    """
    Search a local folder for images matching the item.

    Returns list of {"path": abs_path, "filename": name, "score": 0.0-1.0}
    sorted by score descending. Items below 0.25 are dropped.
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

            score = _score_match(
                name_lower=name_lower,
                name_clean=name_clean,
                file_tokens=file_tokens,
                code_clean=code_clean,
                code_lower=item_code.lower(),
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
    style_tokens: list[str],
    color_tokens: list[str],
    item_group_tokens: list[str],
) -> float:
    """Score a filename. Two complementary paths: SKU-based and descriptive."""
    score = 0.0

    # ── SKU-coded filename ───────────────────────────────────────────────────
    if code_clean and code_clean in name_clean:
        score += 0.65  # full SKU including color suffix
    elif code_lower and code_lower in name_lower:
        score += 0.60
    else:
        # Token match against the SKU's non-colour parts (e.g. "BG264943"
        # without the trailing -BLACK).
        sku_tokens = [
            t for t in _TOKEN_SPLIT.split(code_lower)
            if len(t) >= 4 and t not in _COLOR_WORDS
        ]
        if sku_tokens:
            matched = sum(1 for t in sku_tokens if t in name_lower)
            if matched == len(sku_tokens):
                score += 0.55
            elif matched >= 1:
                score += 0.20

        # Item-group tokens (e.g. "BG264943" lifted from "BUT BA BG264943 Black")
        if item_group_tokens:
            ig_matches = sum(1 for t in item_group_tokens if len(t) >= 5 and t in name_lower)
            if ig_matches:
                score += min(0.25, 0.15 * ig_matches)

        # Fuzzy fallback when the SKU is mangled
        if score < 0.4 and code_clean:
            ratio = SequenceMatcher(None, code_clean, name_clean).ratio()
            if ratio > 0.85:
                score += 0.30

    # ── Descriptive filename: style + color ──────────────────────────────────
    style_match_ratio = 0.0
    if style_tokens:
        style_hits = sum(1 for t in style_tokens if t in file_tokens or t in name_clean)
        style_match_ratio = style_hits / len(style_tokens)
        if style_match_ratio == 1.0:
            score += 0.55  # every style word present — strong signal
        elif style_match_ratio >= 0.75:
            score += 0.40
        elif style_match_ratio >= 0.5:
            score += 0.22
        elif style_match_ratio > 0:
            score += 0.08

    # If we matched nothing about the SKU and nothing about the style, this
    # file is just a colour collision — drop it.
    has_sku_evidence = score > 0  # set above by SKU/item-group matchers
    if not has_sku_evidence and style_tokens and style_match_ratio == 0:
        return 0.0

    if color_tokens:
        color_hit = any(t in file_tokens or t in name_lower for t in color_tokens)
        if color_hit:
            score += 0.25
        else:
            # Penalise when the filename advertises a recognised colour and we
            # have a recognised colour of our own — they disagree.
            file_colors = file_tokens & _COLOR_WORDS
            our_color_words = any(t in _COLOR_WORDS for t in color_tokens)
            if file_colors and our_color_words:
                score -= 0.20

    # ── Primary-image bonus ──────────────────────────────────────────────────
    if score > 0 and _PRIMARY_RE.search(" " + name_lower + " "):
        score += 0.06

    return max(0.0, score)


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

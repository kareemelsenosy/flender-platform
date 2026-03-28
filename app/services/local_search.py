"""Local folder image search — match images to SKUs by filename."""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


def search_local_folder(folder_path: str, item: dict, max_results: int = 5) -> list[dict]:
    """
    Search a local folder for images matching the item code.
    Returns list of {"path": abs_path, "filename": name, "score": 0.0-1.0}
    """
    if not folder_path or not os.path.isdir(folder_path):
        return []

    item_code = str(item.get("item_code") or "").strip()
    if not item_code:
        return []

    code_clean = re.sub(r"[-_ .]", "", item_code).lower()
    color_code = str(item.get("color_code") or "").strip().lower()
    style_name = str(item.get("style_name") or "").strip().lower()

    results = []

    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue

            name_no_ext = os.path.splitext(filename)[0]
            name_clean = re.sub(r"[-_ .]", "", name_no_ext).lower()

            score = _score_match(name_clean, name_no_ext.lower(), code_clean,
                                 item_code.lower(), color_code, style_name)

            if score > 0.2:
                full_path = os.path.join(root, filename)
                results.append({
                    "path": os.path.abspath(full_path),
                    "filename": filename,
                    "score": round(score, 2),
                })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


def _score_match(name_clean: str, name_lower: str, code_clean: str,
                 code_lower: str, color_code: str, style_name: str) -> float:
    """Score how well a filename matches an item."""
    score = 0.0

    # Exact item code match (cleaned)
    if code_clean and code_clean in name_clean:
        score += 0.50

    # Exact item code in filename
    elif code_lower and code_lower in name_lower:
        score += 0.45

    # Fuzzy match
    else:
        ratio = SequenceMatcher(None, code_clean, name_clean).ratio()
        if ratio > 0.7:
            score += ratio * 0.35

        # Token matching
        tokens = [t for t in re.split(r"[-_ .]+", code_lower) if len(t) >= 3]
        if tokens:
            matched = sum(1 for t in tokens if t in name_lower)
            if matched == len(tokens):
                score += 0.35
            elif matched >= 2:
                score += 0.20
            elif matched == 1:
                score += 0.10

    # Color code bonus
    if color_code and color_code in name_lower:
        score += 0.20

    # Style name bonus
    if style_name and len(style_name) > 3:
        style_clean = re.sub(r"[-_ .]", "", style_name)
        if style_clean in name_clean:
            score += 0.15

    return min(score, 1.0)


def scan_folder_summary(folder_path: str) -> dict[str, Any]:
    """Get a summary of images in a folder for display."""
    if not folder_path or not os.path.isdir(folder_path):
        return {"exists": False, "count": 0, "path": folder_path}

    count = 0
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                count += 1

    return {"exists": True, "count": count, "path": folder_path}

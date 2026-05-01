"""Helpers for safely handling uploaded filenames and paths."""
from __future__ import annotations

import os
import re
from pathlib import Path

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def normalize_uploaded_name(name: str | None, default: str = "upload") -> tuple[str, str]:
    """Return (display_name, safe_filename) for a user-supplied upload name."""
    raw = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not raw:
        raw = default

    display_name = raw[:255]
    stem, ext = os.path.splitext(display_name)
    safe_stem = _SAFE_CHARS_RE.sub("_", stem).strip(" ._") or default
    safe_ext = re.sub(r"[^A-Za-z0-9.]", "", ext.lower())[:16]
    if safe_ext and not safe_ext.startswith("."):
        safe_ext = f".{safe_ext}"
    safe_name = f"{safe_stem[:120]}{safe_ext}"
    return display_name, safe_name


def unique_path(base_dir: Path, filename: str) -> Path:
    """Return a non-colliding path inside base_dir for the given filename."""
    path = base_dir / filename
    counter = 1
    stem, ext = os.path.splitext(filename)
    while path.exists():
        path = base_dir / f"{stem}_{counter}{ext}"
        counter += 1
    return path


def normalize_folder_name(name: str | None, default: str = "folder") -> str:
    """Return a safe display folder name, using spaces instead of underscores.

    This is for SAP/B2B image folders only. File names are handled separately
    and may intentionally keep underscores, e.g. ``ITEMCODE_01.jpg``.
    """
    raw = str(name or "").strip()
    if not raw:
        raw = default
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", raw)
    safe = safe.replace("_", " ")
    safe = re.sub(r"\s+", " ", safe).strip().rstrip(".")
    return safe[:180] or default

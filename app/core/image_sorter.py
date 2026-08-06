"""Image Sorter — match unnamed product photos to SAP Item Group Codes.

The problem this solves: a brand sends a folder of product photos with useless
names (``CTM SUMMER MOCK17.png``), and SAP has a master file listing every item
group that is still missing an image. Somebody has to open each photo, work out
which product it is, and file it into a folder named after the item group code.

This module does that automatically:

1. ``parse_master_workbook`` reads the SAP export and pulls out one target per
   **Item Group Code** (the highlighted column) with its product name, colour,
   category, style code and brand.
2. ``collect_images`` gathers the photos from uploaded files, ZIPs (including
   ZIPs nested inside ZIPs) and folders, de-duplicating by content hash.
3. ``match_images`` matches photo → item group. A filename that already carries
   the style/item code is matched directly with no AI. Everything else goes
   through two vision passes: describe the photo against the master file's own
   vocabulary, then pick the item group from a scored shortlist.
4. ``build_output_tree`` / ``build_output_zip`` write one folder per item group
   code, with the main photo named ``<CODE>_1.<ext>`` and extras ``_2``, ``_3``.

The AI provider and key are the platform's existing ones (``ai_service``):
Gemini when configured, otherwise Claude.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps

from app.core.image_sources import CATALOG_DIR_NAME
from app.services.ai_service import (  # noqa: F401  (private helpers, same app)
    _call_ai_vision,
    _extract_json,
    _prepare_image_for_ai,
    ai_available,
)

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_ZIP_DEPTH = 3
MAX_IMAGE_BYTES = 40 * 1024 * 1024
SHORTLIST_SIZE = 14
CONFIDENCE_REVIEW_THRESHOLD = 0.75


class ImageSortError(Exception):
    """Raised when an input can't be used (bad master file, no images, ...)."""


# ── Master file ──────────────────────────────────────────────────────────────

@dataclass
class ItemGroup:
    """One SAP item group — the thing a photo has to be matched to."""
    code: str
    name: str = ""
    color: str = ""
    category: str = ""
    style_code: str = ""
    brand: str = ""
    gender: str = ""
    material: str = ""
    variants: int = 1          # how many SAP rows (sizes) roll up into this group

    def label(self) -> str:
        """Compact one-line description used in AI prompts."""
        bits = [self.name or self.code]
        if self.color:
            bits.append(f"colour: {self.color}")
        if self.category:
            bits.append(f"category: {self.category}")
        if self.style_code:
            bits.append(f"style: {self.style_code}")
        if self.material:
            bits.append(f"material: {self.material}")
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "color": self.color,
            "category": self.category, "style_code": self.style_code,
            "brand": self.brand, "gender": self.gender,
            "material": self.material, "variants": self.variants,
        }


# Header aliases, most-specific first. The first alias that matches a header
# (normalised to lowercase, punctuation stripped) wins the column.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "code": ["item group code", "itemgroupcode", "group code", "image folder name"],
    "name": ["web description 2", "item description long", "style name",
             "product name", "web description2", "description 2"],
    "color": ["web color", "web colour", "color name", "colour name",
              "base color", "base colour", "color", "colour"],
    "category": ["item group", "product group", "category"],
    "style_code": ["style code", "mfr catalog no", "mfr. catalog no",
                   "manufacturer catalog no", "style number", "style no"],
    "brand": ["web description", "brand", "vendor"],
    "gender": ["gender"],
    "material": ["material", "fabric", "composition"],
}


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(value or "").strip().lower()).strip()


def _is_highlighted(cell) -> bool:
    """True when a cell carries a non-default background fill.

    SAP exports mark the Item Group Code column with a highlight; it is the
    fallback signal when the header text is missing or renamed.
    """
    try:
        fill = cell.fill
        if not fill or fill.patternType in (None, "none"):
            return False
        rgb = getattr(fill.start_color, "rgb", None)
        if not isinstance(rgb, str):
            return False
        return rgb.upper() not in {"00000000", "FFFFFFFF", "FFFFFF", "000000"}
    except Exception:
        return False


def _find_header_row(ws, scan_rows: int = 12) -> int:
    """Row index of the header — the first row whose cells look like headers."""
    best_row, best_hits = 1, -1
    for r in range(1, min(scan_rows, ws.max_row) + 1):
        headers = [_norm_header(c.value) for c in ws[r]]
        hits = sum(
            1 for key, aliases in _COLUMN_ALIASES.items()
            for h in headers if h and h in aliases
        )
        if hits > best_hits:
            best_row, best_hits = r, hits
    if best_hits <= 0:
        raise ImageSortError(
            "Could not find a header row in the master file. It needs a header "
            "with at least an 'Item Group Code' column."
        )
    return best_row


def _map_columns(ws, header_row: int) -> dict[str, int]:
    """{field: 1-based column index} for the fields we can find."""
    headers = {}
    for cell in ws[header_row]:
        h = _norm_header(cell.value)
        if h and h not in headers:
            headers[h] = cell.column

    mapping: dict[str, int] = {}
    taken: set[int] = set()
    for field_name, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            col = headers.get(alias)
            if col and col not in taken:
                mapping[field_name] = col
                taken.add(col)
                break
    return mapping


def parse_master_workbook(
    path: str,
    sheet_name: str | None = None,
    code_column: str | None = None,
) -> tuple[list[ItemGroup], dict]:
    """Read a SAP master export into one :class:`ItemGroup` per item group code.

    ``code_column`` is an optional Excel column letter override for when the
    Item Group Code column can't be detected automatically.
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise ImageSortError(f"Could not open the master file: {e}") from e

    # Pick the sheet with the most rows unless the caller named one — SAP
    # exports often carry a small pivot tab alongside the real data tab.
    if sheet_name and sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = max(wb.worksheets, key=lambda s: (s.max_row or 0) * (s.max_column or 0))

    header_row = _find_header_row(ws)
    cols = _map_columns(ws, header_row)

    if code_column:
        from openpyxl.utils import column_index_from_string
        try:
            cols["code"] = column_index_from_string(code_column.strip().upper())
        except Exception as e:
            raise ImageSortError(f"'{code_column}' is not a valid column letter") from e

    if "code" not in cols:
        # Fall back to the highlighted column — that is how these files mark it.
        for cell in ws[header_row]:
            if _is_highlighted(cell) or _is_highlighted(ws.cell(header_row + 1, cell.column)):
                cols["code"] = cell.column
                break
    if "code" not in cols:
        raise ImageSortError(
            "Could not find the 'Item Group Code' column in the master file. "
            "Add that header, or pick the column manually."
        )

    def val(row: int, key: str) -> str:
        col = cols.get(key)
        if not col:
            return ""
        v = ws.cell(row, col).value
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    groups: dict[str, ItemGroup] = {}
    for r in range(header_row + 1, (ws.max_row or header_row) + 1):
        code = val(r, "code")
        if not code:
            continue
        existing = groups.get(code)
        if existing:
            existing.variants += 1
            continue
        groups[code] = ItemGroup(
            code=code,
            name=val(r, "name"),
            color=val(r, "color"),
            category=val(r, "category"),
            style_code=val(r, "style_code"),
            brand=val(r, "brand"),
            gender=val(r, "gender"),
            material=val(r, "material"),
        )

    if not groups:
        raise ImageSortError("The master file has a header but no item group rows.")

    meta = {
        "sheet": ws.title,
        "header_row": header_row,
        "columns": {k: _col_letter(v) for k, v in cols.items()},
        "rows": (ws.max_row or header_row) - header_row,
        "groups": len(groups),
    }
    return list(groups.values()), meta


def _col_letter(idx: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(idx)


# ── Image collection ─────────────────────────────────────────────────────────

@dataclass
class SourceImage:
    """One usable photo found in the inputs."""
    index: int
    path: str
    filename: str
    source: str = ""           # which upload / link it came from
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    sha1: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index, "filename": self.filename, "source": self.source,
            "width": self.width, "height": self.height,
            "size_bytes": self.size_bytes, "sha1": self.sha1,
        }


def _is_junk(name: str) -> bool:
    base = os.path.basename(name)
    return (
        base.startswith("._")
        or base == ".DS_Store"
        or "__MACOSX" in name.replace("\\", "/").split("/")
    )


def _extract_archives(root: Path, depth: int = 0) -> None:
    """Recursively unpack every ZIP found under ``root`` (in place)."""
    if depth >= MAX_ZIP_DEPTH:
        return
    archives = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"]
    if not archives:
        return
    for archive in archives:
        target = archive.with_suffix("")
        suffix = 1
        while target.exists():
            suffix += 1
            target = archive.with_name(f"{archive.stem}_{suffix}")
        try:
            with zipfile.ZipFile(archive) as zf:
                for member in zf.infolist():
                    if member.is_dir() or _is_junk(member.filename):
                        continue
                    # Guard against zip-slip: resolve and confirm containment.
                    dest = (target / member.filename).resolve()
                    if not str(dest).startswith(str(target.resolve())):
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
        except Exception as e:
            logger.warning(f"Could not unpack {archive.name}: {e}")
        finally:
            try:
                archive.unlink()
            except OSError:
                pass
    _extract_archives(root, depth + 1)


def collect_images(root: str | Path, source_label: str = "") -> list[SourceImage]:
    """Walk ``root`` (after unpacking any ZIPs) and return the usable photos.

    De-duplicates by SHA-1: the same photo delivered both loose and inside a
    nested archive should only be matched once.
    """
    root = Path(root)
    _extract_archives(root)

    seen: dict[str, SourceImage] = {}
    images: list[SourceImage] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_junk(str(path)):
            continue
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        size = path.stat().st_size
        if size == 0 or size > MAX_IMAGE_BYTES:
            continue
        try:
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                width, height = img.size
        except Exception:
            continue

        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        if digest in seen:
            continue

        # Pages pulled out of a catalogue PDF are crops of a printed layout,
        # not the brand's own packshot — the matcher ranks them lower for the
        # main slot.
        from_catalog = CATALOG_DIR_NAME in path.parts
        image = SourceImage(
            index=len(images) + 1,
            path=str(path),
            filename=path.name,
            source="catalog" if from_catalog else (source_label or path.parent.name),
            width=width, height=height, size_bytes=size, sha1=digest,
        )
        seen[digest] = image
        images.append(image)
    return images


# ── Direct (no-AI) filename matching ─────────────────────────────────────────

def _normalise_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _filename_direct_match(image: SourceImage, groups: list[ItemGroup]) -> ItemGroup | None:
    """Match on filename alone when it already carries the code.

    Brands that name their exports properly (``HIK_PS_HP0127202_Norweigian_
    Forest_01.jpg``) don't need the AI at all. Item group code is tried first
    because it is more specific than the style code, which is shared across
    colourways.
    """
    stem = _normalise_key(Path(image.filename).stem)
    if not stem:
        return None

    by_code = sorted(groups, key=lambda g: len(g.code), reverse=True)
    for group in by_code:
        key = _normalise_key(group.code)
        if key and key in stem:
            return group

    # Style code + colour, e.g. "MKT27SS-SS1256 Pink" written any which way.
    for group in groups:
        style_key = _normalise_key(group.style_code)
        color_key = _normalise_key(group.color)
        if style_key and len(style_key) >= 6 and style_key in stem:
            if not color_key or color_key in stem:
                return group
    return None


# Catalogue extracts are named "<pdf>_p012_03" by extract_pdf_images. That
# trailing number is a page/slot index, not the brand's main-image marker.
_CATALOG_EXTRACT_SUFFIX = re.compile(r"_p\d{2,4}_\d{2}$")


def _main_image_order(filename: str) -> int:
    """Trailing ``_1`` / ``-01`` in a filename is the brand's own main-image
    marker; keep that ordering when the photos arrive already named."""
    stem = Path(filename).stem
    if _CATALOG_EXTRACT_SUFFIX.search(stem):
        return 999
    m = re.search(r"[_\-\s](\d{1,3})$", stem)
    return int(m.group(1)) if m else 999


# ── Vision pass 1: describe the photo ────────────────────────────────────────

_VIEW_RANK = {
    "packshot": 0, "front": 1, "flat": 2, "three-quarter": 3, "side": 4,
    "back": 5, "on-model": 6, "detail": 7, "other": 8,
}


def _describe_prompt(categories: list[str], colors: list[str], brand: str) -> str:
    return f"""You are cataloguing a single product photo for a fashion wholesaler.

Look at the attached photo and describe the product you can actually see.
{f'The brand is: {brand}.' if brand else ''}

The catalogue this photo belongs to uses these category codes:
{", ".join(categories) if categories else "(unknown)"}

...and these colour names:
{", ".join(colors) if colors else "(unknown)"}

Return ONLY valid JSON:
{{
  "category": "the single best-fitting category code from the list above, or \\"\\" if none fits",
  "product_type": "plain-English garment type, e.g. 'short sleeve t-shirt', '5-panel cap', 'mesh basketball shorts'",
  "primary_color": "the dominant colour of the GARMENT ITSELF (not the print), using a colour name from the list above when one fits",
  "secondary_colors": ["other notable garment colours"],
  "pattern": "solid / camo / leopard / dalmatian / pinstripe / check / tie-dye / floral / other — '' if plain",
  "visible_text": ["every word or phrase legibly printed, embroidered or woven on the product, exactly as written"],
  "graphic": "one sentence describing the artwork or graphic on the product, '' if there is none",
  "view": "one of: packshot, front, flat, three-quarter, side, back, on-model, detail, other",
  "quality": 0.0
}}

Rules:
- "visible_text" is the strongest matching signal — transcribe slogans and wordmarks carefully, even small ones.
- Judge "primary_color" from the garment fabric, not from the printed artwork.
- "quality" is 0-1: how well this photo works as the MAIN catalogue image (full product, clean background, sharp, front-facing = high; crops, detail shots and styled model shots = low).
- Do not guess a category that is not in the list; return "" instead."""


def describe_image(
    image: SourceImage,
    categories: list[str],
    colors: list[str],
    brand: str = "",
) -> dict:
    """Vision pass 1 — what is in this photo, in the master file's vocabulary."""
    prepared = _prepare_image_for_ai(f"file://{image.path}", 1)
    if not prepared:
        return {}
    text = _call_ai_vision(
        _describe_prompt(categories, colors, brand), [prepared], max_tokens=900
    )
    if not text:
        return {}
    try:
        data = json.loads(_extract_json(text))
    except Exception as e:
        logger.warning(f"describe_image parse failed for {image.filename}: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "category": str(data.get("category") or "").strip(),
        "product_type": str(data.get("product_type") or "").strip(),
        "primary_color": str(data.get("primary_color") or "").strip(),
        "secondary_colors": [str(c).strip() for c in (data.get("secondary_colors") or []) if str(c).strip()],
        "pattern": str(data.get("pattern") or "").strip(),
        "visible_text": [str(t).strip() for t in (data.get("visible_text") or []) if str(t).strip()],
        "graphic": str(data.get("graphic") or "").strip(),
        "view": str(data.get("view") or "other").strip().lower(),
        "quality": _clamp01(data.get("quality")),
    }


THUMB_MAX_DIM = 420


def build_thumbnail(source_path: str | Path, dest_path: str | Path) -> bool:
    """Write a small JPEG preview of ``source_path`` for the review grid.

    The review page shows every photo at once. Serving the originals means
    hundreds of megabytes over hundreds of requests, which is slow enough that
    browsers give up and render broken images — so the grid gets these instead.
    Returns False if the source can't be read.
    """
    dest_path = Path(dest_path)
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.split()[-1] if "A" in img.getbands() else None
                background.paste(img, mask=alpha)
                img = background
            elif img.mode == "L":
                img = img.convert("RGB")
            img.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM))
            img.save(dest_path, format="JPEG", quality=80, optimize=True)
        return True
    except Exception as e:
        logger.warning(f"Thumbnail failed for {source_path}: {e}")
        return False


def build_thumbnails(
    results: list["MatchResult"],
    thumb_dir: str | Path,
    max_workers: int = 4,
) -> int:
    """Pre-build every preview so the review grid never waits on one."""
    thumb_dir = Path(thumb_dir)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    def work(result: "MatchResult") -> bool:
        dest = thumb_dir / f"{result.index}.jpg"
        if dest.exists():
            return True
        return build_thumbnail(result.path, dest)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        return sum(1 for ok in pool.map(work, results) if ok)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


# ── Shortlisting ─────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "and", "of", "with", "for", "in", "on", "to",
    "shirt", "tshirt", "t", "s", "ss", "ls",
}

# Colour families keep "Light Blue" close to "blue" without letting it collapse
# into "black".
_COLOR_FAMILY = {
    "black": "black", "jet": "black",
    "white": "white", "ecru": "white", "cream": "white", "offwhite": "white",
    "ivory": "white", "natural": "white",
    "grey": "grey", "gray": "grey", "charcoal": "grey", "silver": "grey",
    "blue": "blue", "navy": "blue", "denim": "blue", "indigo": "blue",
    "red": "red", "burgandy": "red", "burgundy": "red", "maroon": "red",
    "wine": "red", "crimson": "red",
    "pink": "pink", "rose": "pink", "fuchsia": "pink",
    "green": "green", "olive": "green", "khaki": "green", "sage": "green",
    "forest": "green", "mint": "green",
    "brown": "brown", "tan": "brown", "beige": "brown", "camel": "brown",
    "chocolate": "brown", "sand": "brown",
    "yellow": "yellow", "gold": "yellow", "mustard": "yellow",
    "orange": "orange", "rust": "orange",
    "purple": "purple", "violet": "purple", "lilac": "purple",
    "multi": "multi", "camo": "camo", "leopard": "leopard",
    "dalmation": "dalmatian", "dalmatian": "dalmatian",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _color_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for word in re.findall(r"[a-z]+", str(text or "").lower()):
        out.add(_COLOR_FAMILY.get(word, word))
    return out


def _color_score(desc: dict, group: ItemGroup) -> float:
    """0-1 on how well the photo's colours line up with the item group's."""
    target = _color_tokens(group.color)
    if not target:
        return 0.5
    seen = _color_tokens(desc.get("primary_color", ""))
    seen |= _color_tokens(desc.get("pattern", ""))
    secondary = set()
    for c in desc.get("secondary_colors") or []:
        secondary |= _color_tokens(c)
    if not seen and not secondary:
        return 0.4

    primary_hits = len(target & seen)
    all_hits = len(target & (seen | secondary))
    if primary_hits == len(target):
        return 1.0
    if all_hits == len(target):
        return 0.85
    if primary_hits:
        return 0.7
    if all_hits:
        return 0.5
    return 0.0


def _name_score(desc: dict, group: ItemGroup, idf: dict[str, float]) -> float:
    """0-1 on how well the printed text / graphic matches the product name.

    Weighted by inverse document frequency so that a rare word like
    "dalmation" counts for far more than "hoodie", which every hoodie shares.
    """
    name_tokens = _tokens(group.name)
    if not name_tokens:
        return 0.0
    seen = _tokens(" ".join(desc.get("visible_text") or []))
    seen |= _tokens(desc.get("graphic", ""))
    seen |= _tokens(desc.get("product_type", ""))
    seen |= _tokens(desc.get("pattern", ""))
    if not seen:
        return 0.0

    hit_weight = sum(idf.get(t, 1.0) for t in name_tokens & seen)
    total_weight = sum(idf.get(t, 1.0) for t in name_tokens)
    return hit_weight / total_weight if total_weight else 0.0


def _build_idf(groups: list[ItemGroup]) -> dict[str, float]:
    import math
    counts: dict[str, int] = {}
    for group in groups:
        for token in _tokens(group.name):
            counts[token] = counts.get(token, 0) + 1
    total = max(1, len(groups))
    return {t: math.log(total / c) + 0.35 for t, c in counts.items()}


def score_group(desc: dict, group: ItemGroup, idf: dict[str, float]) -> float:
    """Combined 0-10 shortlist score for one photo against one item group."""
    category = (desc.get("category") or "").strip().lower()
    if category and group.category:
        cat = 3.0 if category == group.category.strip().lower() else 0.0
    else:
        cat = 1.2
    return cat + 3.2 * _color_score(desc, group) + 3.8 * _name_score(desc, group, idf)


def shortlist(
    desc: dict,
    groups: list[ItemGroup],
    idf: dict[str, float],
    limit: int = SHORTLIST_SIZE,
) -> list[tuple[ItemGroup, float]]:
    """Top-scoring item groups for a photo, best first.

    Always keeps a couple of same-category options even when they score badly,
    so the vision pass can still recover from a wrong colour reading.
    """
    scored = sorted(
        ((g, score_group(desc, g, idf)) for g in groups),
        key=lambda pair: pair[1], reverse=True,
    )
    picked = scored[:limit]
    category = (desc.get("category") or "").strip().lower()
    if category:
        chosen = {g.code for g, _ in picked}
        same_cat = [
            (g, s) for g, s in scored
            if g.code not in chosen and g.category.strip().lower() == category
        ][:3]
        picked += same_cat
    return picked


# ── Vision pass 2: pick the item group ───────────────────────────────────────

def _match_prompt(candidates: list[tuple[ItemGroup, float]], desc: dict, brand: str) -> str:
    listed = "\n".join(
        f"{i}. [{g.code}] {g.label()}"
        for i, (g, _) in enumerate(candidates, start=1)
    )
    seen_text = ", ".join(desc.get("visible_text") or []) or "(none)"
    return f"""You are filing a product photo into the correct SAP item group for a fashion wholesaler.
{f'Brand: {brand}' if brand else ''}

A first pass read the photo as:
- product type: {desc.get('product_type') or '(unknown)'}
- garment colour: {desc.get('primary_color') or '(unknown)'}
- pattern: {desc.get('pattern') or 'solid'}
- text on the product: {seen_text}
- graphic: {desc.get('graphic') or '(none)'}

Candidate item groups:
{listed}

Look at the attached photo yourself and decide which candidate it is.

Decision rules, in priority order:
1. The product type must match. A hoodie is not a t-shirt; shorts are not pants; a cap is not a beanie.
2. Text printed on the garment usually names the style — "CALL MY LAWYER" on a tee means the "Call My Lawyer T-Shirt", not the hoodie of the same name unless the garment is a hoodie.
3. The garment's own colour must match the candidate's colour. Judge the fabric, not the artwork. Two candidates often differ ONLY by colour — get this right.
4. Patterned garments (camo, dalmatian, leopard, pinstripe) belong to the candidate whose colour name is that pattern.
5. When two candidate names would BOTH fit the printed words, the more specific
   name wins. Style names often describe how the artwork is RENDERED or what it
   DEPICTS, not just what it says — "Chrome" means metallic/liquid-chrome
   lettering, "Varsity" means collegiate block letters, "Star Arc" means an
   arched wordmark, "Bear" means a bear is drawn on it. Check the artwork for
   that feature before falling back to the candidate that merely repeats the
   slogan. Name the feature you used in "reason".
6. If no candidate is genuinely the same product, return best: 0. A wrong folder is worse than no folder.

Return ONLY valid JSON:
{{
  "best": <candidate number, or 0 if none match>,
  "confidence": <0.0-1.0>,
  "runner_up": <candidate number, or 0>,
  "reason": "<one short sentence>"
}}"""


def match_one_image(
    image: SourceImage,
    desc: dict,
    candidates: list[tuple[ItemGroup, float]],
    brand: str = "",
) -> dict:
    """Vision pass 2 — choose the item group for one photo."""
    if not candidates:
        return {"code": "", "confidence": 0.0, "reason": "No candidate item groups.", "runner_up": ""}

    prepared = _prepare_image_for_ai(f"file://{image.path}", 1)
    if not prepared:
        return {"code": "", "confidence": 0.0, "reason": "Image could not be read.", "runner_up": ""}

    text = _call_ai_vision(_match_prompt(candidates, desc, brand), [prepared], max_tokens=500)
    if not text:
        return {"code": "", "confidence": 0.0, "reason": "AI match request failed.", "runner_up": ""}
    try:
        data = json.loads(_extract_json(text))
    except Exception as e:
        logger.warning(f"match_one_image parse failed for {image.filename}: {e}")
        return {"code": "", "confidence": 0.0, "reason": "AI response was not valid JSON.", "runner_up": ""}

    def code_at(raw: Any) -> str:
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            return ""
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1][0].code
        return ""

    return {
        "code": code_at(data.get("best")),
        "runner_up": code_at(data.get("runner_up")),
        "confidence": _clamp01(data.get("confidence")),
        "reason": str(data.get("reason") or "").strip()[:300],
    }


# ── Orchestration ────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """One photo's outcome. ``code`` empty means unmatched."""
    index: int
    filename: str
    source: str
    path: str
    code: str = ""
    confidence: float = 0.0
    reason: str = ""
    method: str = "ai"          # ai | filename | manual | none
    runner_up: str = ""
    description: dict = field(default_factory=dict)
    position: int = 0           # 1 = main image inside its folder
    candidates: list[dict] = field(default_factory=list)
    flagged: str = ""           # forced-review note set by the conflict pass
    manual_rank: int = 0        # >0 when the user ordered this folder by hand

    @property
    def needs_review(self) -> bool:
        return bool(self.flagged) or not self.code or (
            self.method == "ai" and self.confidence < CONFIDENCE_REVIEW_THRESHOLD
        )

    def to_dict(self) -> dict:
        return {
            "index": self.index, "filename": self.filename, "source": self.source,
            "path": self.path, "code": self.code,
            "confidence": round(self.confidence, 3), "reason": self.reason,
            "method": self.method, "runner_up": self.runner_up,
            "description": self.description, "position": self.position,
            "candidates": self.candidates, "flagged": self.flagged,
            "manual_rank": self.manual_rank,
            "needs_review": self.needs_review,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MatchResult":
        return cls(
            index=int(data.get("index") or 0),
            filename=str(data.get("filename") or ""),
            source=str(data.get("source") or ""),
            path=str(data.get("path") or ""),
            code=str(data.get("code") or ""),
            confidence=float(data.get("confidence") or 0.0),
            reason=str(data.get("reason") or ""),
            method=str(data.get("method") or "ai"),
            runner_up=str(data.get("runner_up") or ""),
            description=data.get("description") or {},
            position=int(data.get("position") or 0),
            candidates=data.get("candidates") or [],
            flagged=str(data.get("flagged") or ""),
            manual_rank=int(data.get("manual_rank") or 0),
        )


def match_images(
    images: list[SourceImage],
    groups: list[ItemGroup],
    *,
    brand: str = "",
    max_workers: int = 4,
    use_ai: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[MatchResult]:
    """Match every photo to an item group. Filenames first, then AI vision."""
    if not images:
        return []
    if not groups:
        raise ImageSortError("The master file produced no item groups to match against.")

    categories = sorted({g.category for g in groups if g.category})
    colors = sorted({g.color for g in groups if g.color})
    idf = _build_idf(groups)
    by_code = {g.code: g for g in groups}
    ai_on = use_ai and ai_available()

    done = 0
    total = len(images)

    def report() -> None:
        nonlocal done
        done += 1
        if on_progress:
            try:
                on_progress(done, total)
            except Exception:
                pass

    def work(image: SourceImage) -> MatchResult:
        result = MatchResult(
            index=image.index, filename=image.filename,
            source=image.source, path=image.path,
        )
        try:
            direct = _filename_direct_match(image, groups)
            if direct is not None:
                result.code = direct.code
                result.confidence = 1.0
                result.method = "filename"
                result.reason = "Filename already contains the item code."
                return result

            if not ai_on:
                result.method = "none"
                result.reason = "AI is not configured — no filename match either."
                return result

            desc = describe_image(image, categories, colors, brand)
            result.description = desc
            if not desc:
                result.method = "none"
                result.reason = "The photo could not be described."
                return result

            candidates = shortlist(desc, groups, idf)
            result.candidates = [
                {"code": g.code, "name": g.name, "color": g.color, "score": round(s, 2)}
                for g, s in candidates[:8]
            ]
            picked = match_one_image(image, desc, candidates, brand)
            result.code = picked["code"] if picked["code"] in by_code else ""
            result.runner_up = picked["runner_up"]
            result.confidence = picked["confidence"]
            result.reason = picked["reason"]
            if not result.code:
                result.method = "none"
            return result
        except Exception as e:
            logger.error(f"Matching failed for {image.filename}: {e}", exc_info=True)
            result.method = "none"
            result.reason = f"Error: {e}"
            return result
        finally:
            report()

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        results = list(pool.map(work, images))

    results.sort(key=lambda r: r.index)
    flag_conflicts(results, groups)
    assign_positions(results)
    return results


def flag_conflicts(results: list[MatchResult], groups: list[ItemGroup]) -> int:
    """Force a human check on photos that a crowded folder probably stole.

    Every photo is matched on its own, which lets a folder hoard shots that
    belong to a near-identical sibling — two styles that print the same slogan,
    or the same design in two colourways. The tell is a folder holding more
    photos than its siblings while the runner-up group the model named sits
    empty. Nothing is moved (that would trade one guess for another); the extras
    are just pushed into the "to check" list, where one click fixes them.

    Returns how many photos were flagged.
    """
    filled: dict[str, list[MatchResult]] = {}
    for result in results:
        if result.code:
            filled.setdefault(result.code, []).append(result)

    known = {g.code for g in groups}
    empty = {code for code in known if code not in filled}
    if not empty:
        return 0

    flagged = 0
    for code, photos in filled.items():
        if len(photos) <= 2:
            continue
        # Keep the two most confident shots; anything below that which named an
        # empty sibling as its runner-up is the suspect.
        ranked = sorted(photos, key=lambda r: -r.confidence)
        for result in ranked[2:]:
            if result.runner_up and result.runner_up in empty and not result.flagged:
                result.flagged = (
                    f"This folder took {len(photos)} photos while "
                    f"'{result.runner_up}' got none — check which one this is."
                )
                flagged += 1
    return flagged


def assign_positions(results: list[MatchResult]) -> None:
    """Number the photos inside each item group — 1 is the main image.

    Automatic ranking: the brand's own trailing number if the file was already
    named, then how well the shot works as a main image (view type, then the
    model's quality score, then match confidence), and a photo pulled out of a
    catalogue page loses to the brand's own packshot.

    **Hand-set order wins.** This runs again after every correction, so a folder
    the user has ordered themselves must come back the way they left it —
    otherwise reassigning one photo silently resets the main image of every
    other product they had already fixed. Photos carrying a ``manual_rank`` keep
    that order; anything newly added to the folder is ranked automatically and
    placed behind them.
    """
    by_code: dict[str, list[MatchResult]] = {}
    for result in results:
        result.position = 0
        if result.code:
            by_code.setdefault(result.code, []).append(result)

    def auto_rank(r: MatchResult):
        view = str((r.description or {}).get("view") or "other").lower()
        return (
            _main_image_order(r.filename),
            1 if r.source == "catalog" else 0,
            _VIEW_RANK.get(view, _VIEW_RANK["other"]),
            -_clamp01((r.description or {}).get("quality")),
            -r.confidence,
            r.filename.lower(),
        )

    for group_results in by_code.values():
        pinned = sorted(
            (r for r in group_results if r.manual_rank > 0),
            key=lambda r: r.manual_rank,
        )
        rest = sorted((r for r in group_results if r.manual_rank <= 0), key=auto_rank)
        for position, result in enumerate(pinned + rest, start=1):
            result.position = position


# ── Output ───────────────────────────────────────────────────────────────────

_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder_name(code: str) -> str:
    """Item group code as a filesystem-safe folder name (spaces preserved)."""
    name = _UNSAFE_PATH_CHARS.sub("-", str(code or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "UNKNOWN"


def image_file_name(code: str, position: int, original: str) -> str:
    """``<CODE>_1.jpg`` — spaces become underscores, main image is always ``_1``."""
    base = safe_folder_name(code).replace(" ", "_")
    ext = Path(original).suffix.lower() or ".jpg"
    return f"{base}_{position}{ext}"


def build_output_tree(results: list[MatchResult], out_dir: str | Path) -> dict:
    """Write one folder per item group code into ``out_dir``.

    Returns a summary: folders written, images filed, images left unmatched.
    """
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filed = 0
    folders: dict[str, list[str]] = {}
    for result in sorted(results, key=lambda r: (r.code, r.position)):
        if not result.code or result.position < 1:
            continue
        if not os.path.exists(result.path):
            continue
        folder = out_dir / safe_folder_name(result.code)
        folder.mkdir(parents=True, exist_ok=True)
        name = image_file_name(result.code, result.position, result.filename)
        shutil.copyfile(result.path, folder / name)
        folders.setdefault(result.code, []).append(name)
        filed += 1

    return {
        "folders": len(folders),
        "images": filed,
        "unmatched": sum(1 for r in results if not r.code),
        "folder_map": folders,
    }


def build_output_zip(results: list[MatchResult], zip_path: str | Path, work_dir: str | Path) -> dict:
    """Build the folder tree, then zip it. Returns the tree summary."""
    work_dir = Path(work_dir)
    tree_dir = work_dir / "tree"
    summary = build_output_tree(results, tree_dir)

    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(tree_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(tree_dir))
    summary["zip_bytes"] = zip_path.stat().st_size
    return summary


def build_report_csv(results: list[MatchResult], groups: list[ItemGroup]) -> str:
    """A match report: every photo, and every item group still without one."""
    by_code = {g.code: g for g in groups}
    matched_codes = {r.code for r in results if r.code}

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Item Group Code", "Product", "Colour", "Category",
        "Output File", "Source File", "Position", "Method", "Confidence", "Reason",
    ])
    for result in sorted(results, key=lambda r: (r.code == "", r.code, r.position)):
        group = by_code.get(result.code)
        writer.writerow([
            result.code or "(unmatched)",
            group.name if group else "",
            group.color if group else "",
            group.category if group else "",
            image_file_name(result.code, result.position, result.filename) if result.code else "",
            result.filename,
            result.position or "",
            result.method,
            f"{result.confidence:.2f}",
            result.reason,
        ])
    for group in groups:
        if group.code not in matched_codes:
            writer.writerow([
                group.code, group.name, group.color, group.category,
                "", "", "", "no-image", "0.00", "No photo matched this item group.",
            ])
    return out.getvalue()


def summarise(results: list[MatchResult], groups: list[ItemGroup]) -> dict:
    """Headline counts for the UI."""
    matched = [r for r in results if r.code]
    codes = {r.code for r in matched}
    return {
        "images": len(results),
        "matched": len(matched),
        "unmatched": len(results) - len(matched),
        "review": sum(1 for r in results if r.needs_review),
        "groups_total": len(groups),
        "groups_covered": len(codes),
        "groups_missing": len(groups) - len(codes),
    }

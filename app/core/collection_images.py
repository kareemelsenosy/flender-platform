"""Image readiness for a collection — Operations OS.

The matching itself already exists in the Image Sorter, and the Dropbox
normalisation runs in SAP's own application. What the Operations OS is missing
is the part Flender actually asked for: knowing, per product, whether an image
exists, and if not, what is being done about it.

The requirements document is explicit that "Missing" on its own is not a
status. An image nobody will ever get is a different thing from one the
supplier has been asked for, and management needs to tell them apart.
"""
from __future__ import annotations

MISSING = "missing"
SEARCH_PENDING = "search_pending"
SUPPLIER_REQUESTED = "supplier_requested"
CANDIDATE_FOUND = "candidate_found"
REVIEW_REQUIRED = "review_required"
APPROVED = "approved"
NOT_AVAILABLE = "not_available"
NEVER_EXPECTED = "never_expected"

STATUS_LABELS = {
    MISSING: "Missing",
    SEARCH_PENDING: "Search pending",
    SUPPLIER_REQUESTED: "Supplier requested",
    CANDIDATE_FOUND: "Candidate found",
    REVIEW_REQUIRED: "Review required",
    APPROVED: "Approved",
    NOT_AVAILABLE: "Not available",
    NEVER_EXPECTED: "Never expected",
}

# Statuses that still represent work. The rest are settled, one way or another.
OPEN_STATUSES = {MISSING, SEARCH_PENDING, SUPPLIER_REQUESTED,
                 CANDIDATE_FOUND, REVIEW_REQUIRED}


def _first(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() and str(v).strip().lower() != "nan":
            return str(v).strip()
    return ""


def _flat(name) -> str:
    """Upper-case, with dashes, spaces and dots read as underscores: the
    order sheet's JK00532-TAN and the photo's JK00532_TAN are the same."""
    import re as _re
    return "_" + _re.sub(r"[\s\-./]+", "_", str(name or "").upper()) + "_"


def matching_photos(style: str, code: str, colour: str, flat_names) -> list[int]:
    """Indexes of the photos whose name carries this product.

    Style + colour code first (HUF: ..._JK00532_TAN_01.jpg), then style +
    colour name, then the style alone when there is no colour to go by.
    """
    s = _flat(style).strip("_")
    if not s:
        return []
    for colour_part in (code, colour):
        c = _flat(colour_part).strip("_")
        if c:
            key = f"_{s}_{c}_"
            hits = [i for i, n in enumerate(flat_names) if key in n]
            if hits:
                return hits
    if not code and not colour:
        return [i for i, n in enumerate(flat_names) if f"_{s}_" in n]
    return []


def build_image_package(rows, *, reference=None, supplied_files=None,
                        decisions=None) -> dict:
    """Per style-colour: does an image exist, and if not, what is its status?

    An image is counted as present when SAP already holds one for the product,
    or when the supplier's image package contains a file naming it. Anything
    else is open work, and its status is whatever a human last set.
    """
    supplied = [_flat(n) for n in (supplied_files or [])]
    decisions = decisions or {}
    out_rows, exceptions = [], []
    seen: set[str] = set()

    for row in rows:
        style = _first(row, "Item No.", "Style Number", "Style Code", "item_code")
        colour = _first(row, "Color", "Colour", "color_name")
        if not style:
            continue
        key = f"{style}|{colour}".lower()
        if key in seen:
            continue
        seen.add(key)

        known = {}
        if reference is not None:
            known = reference.known_values(style=style, colour=colour) or {}

        in_sap = bool(known.get("has_images"))
        code = _first(row, "Colour Code", "Color Code")
        photos = matching_photos(style, code, colour, supplied)
        in_drop = bool(photos)

        if in_sap:
            status, source = APPROVED, "already in SAP"
        elif in_drop:
            status, source = REVIEW_REQUIRED, f"{len(photos)} photo(s) from the brand"
        else:
            status, source = MISSING, ""

        # A human decision always wins over what was inferred.
        status = decisions.get(f"image_status::{style}|{colour}", status)

        out_rows.append({
            "style": style, "colour": colour,
            "item_group": known.get("item_group", ""),
            "status": status, "status_label": STATUS_LABELS.get(status, status),
            "source": source,
        })

        if status in OPEN_STATUSES:
            # A product with no image anywhere is work for the image search,
            # not a question for a reviewer — it is reported, not asked. Only
            # a product with something to choose between is a decision.
            exceptions.append({
                "sku": f"{style} {colour}".strip(), "field": "image_status",
                "subject": f"{style}|{colour}",
                "severity": "warning" if status == MISSING else "manual_review",
                "reason": f"{STATUS_LABELS.get(status, status)}"
                          + (f" — {source}" if source else " — no image found"),
                "suggestion": "Set what is happening with this image",
                "options": [STATUS_LABELS[s] for s in STATUS_LABELS],
            })

    counts: dict[str, int] = {}
    for r in out_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = len(out_rows)
    settled = sum(v for k, v in counts.items() if k not in OPEN_STATUSES)

    return {
        "columns": ["style", "colour", "item_group", "status", "status_label", "source"],
        "rows": out_rows,
        "exceptions": exceptions,
        "summary": {"products": total, "with_images": settled,
                    "coverage_pct": round(settled / total * 100, 1) if total else 0.0,
                    "by_status": counts},
    }

"""Multi-source Step 1 — product identity keys, grouping, dedupe."""
from __future__ import annotations

from app.core.product_identity import (
    style_key, line_key, group_by_style, dedupe_lines, count_styles,
)


def test_barcode_wins_for_line_key():
    a = {"barcode": "4001234500011", "item_code": "X", "size": "M"}
    b = {"barcode": "4001234500011", "item_code": "OTHER", "size": "L"}
    # Same EAN => same sellable line regardless of other columns.
    assert line_key(a) == line_key(b) == "ean:4001234500011"


def test_line_key_falls_back_to_code_plus_colour_plus_size():
    row = {"item_code": "ACME-100", "color_name": "Black", "size": "M"}
    assert line_key(row) == "code:acme100|black|sz:m"
    # No barcode, different size => different line.
    other = {"item_code": "ACME-100", "color_name": "Black", "size": "L"}
    assert line_key(row) != line_key(other)


def test_style_key_ignores_size_and_barcode():
    m = {"item_code": "ACME-100", "color_name": "Black", "size": "M", "barcode": "111"}
    l = {"item_code": "ACME-100", "color_name": "Black", "size": "L", "barcode": "222"}
    # Same style+colour, different size/EAN => same style master record.
    assert style_key(m) == style_key(l) == "code:acme100|black"


def test_style_key_name_fallback_when_no_code():
    row = {"brand": "ACME", "style_name": "Panelled Hoodie", "color_name": "Black"}
    assert style_key(row) == "nm:acme|panelledhoodie|black"


def test_code_normalisation_ignores_separators_and_case():
    assert style_key({"item_code": "ACME-100"}) == style_key({"item_code": "acme 100"})
    assert style_key({"item_code": "ACME_100"}) == style_key({"item_code": "acme100"})


def test_group_by_style_and_count():
    rows = [
        {"item_code": "ACME-100", "color_name": "Black", "size": "S"},
        {"item_code": "ACME-100", "color_name": "Black", "size": "M"},
        {"item_code": "ACME-100", "color_name": "Black", "size": "L"},
        {"item_code": "ACME-220", "color_name": "Olive", "size": "30"},
        {"item_code": "ACME-220", "color_name": "Olive", "size": "32"},
    ]
    groups = group_by_style(rows)
    assert set(len(v) for v in groups.values()) == {3, 2}
    assert count_styles(rows) == 2


def test_dedupe_lines_drops_exact_duplicate_lines_keeps_distinct_sizes():
    rows = [
        {"item_code": "ACME-100", "color_name": "Black", "size": "M"},
        {"item_code": "ACME-100", "color_name": "Black", "size": "M"},  # dup
        {"item_code": "ACME-100", "color_name": "Black", "size": "L"},  # distinct
    ]
    deduped, removed = dedupe_lines(rows)
    assert removed == 1
    assert len(deduped) == 2


def test_dedupe_keeps_unidentifiable_rows():
    rows = [{"note": "no id here"}, {"note": "also none"}]
    deduped, removed = dedupe_lines(rows)
    assert removed == 0
    assert len(deduped) == 2


def test_singleton_group_for_unidentifiable_rows():
    # Rows with no derivable key must never be merged together.
    rows = [{"x": 1}, {"y": 2}]
    assert len(group_by_style(rows)) == 2

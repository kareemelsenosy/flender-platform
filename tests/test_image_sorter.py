"""Image Sorter — master-file parsing, image collection, naming and output.

The AI vision passes are stubbed; what's tested here is everything around them:
the parts that must be right regardless of what the model says.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import openpyxl
import pytest
from PIL import Image

from app.core.image_sorter import (
    ImageSortError,
    ItemGroup,
    MatchResult,
    _build_idf,
    _filename_direct_match,
    assign_positions,
    build_output_tree,
    build_output_zip,
    build_report_csv,
    collect_images,
    flag_conflicts,
    image_file_name,
    parse_master_workbook,
    safe_folder_name,
    score_group,
    shortlist,
    summarise,
)
from app.core.image_sources import (
    SourceFetchError,
    _dropbox_download_url,
    detect_source,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_master(path: Path, highlight_code: bool = False) -> None:
    """A cut-down SAP export: two sizes of one item group + two more groups."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Code", "Web Description", "Web Description 2", "Size Description",
               "Item Group Code", "Web Color", "Item Group", "Style Code", "Gender"])
    ws.append(["CTM T SS1 Black S", "CHINATOWN MARKET", "Call Me T-Shirt", "S",
               "CTM T SS1 Black", "Black", "T-SHIRTS", "MKT-SS1", "Men"])
    ws.append(["CTM T SS1 Black M", "CHINATOWN MARKET", "Call Me T-Shirt", "M",
               "CTM T SS1 Black", "Black", "T-SHIRTS", "MKT-SS1", "Men"])
    ws.append(["CTM T SS1 White S", "CHINATOWN MARKET", "Call Me T-Shirt", "S",
               "CTM T SS1 White", "White", "T-SHIRTS", "MKT-SS1", "Men"])
    ws.append(["CTM H HD9 Pink S", "CHINATOWN MARKET", "Have A Nice Day Hoodie", "S",
               "CTM H HD9 Pink", "Pink", "HOODYS", "MKT-HD9", "Men"])
    if highlight_code:
        from openpyxl.styles import PatternFill
        yellow = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")
        for row in range(1, 5):
            ws.cell(row, 5).fill = yellow
    wb.save(path)


def _make_image(path: Path, color=(200, 30, 30), size=(64, 64)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


@pytest.fixture
def groups():
    return [
        ItemGroup(code="CTM T SS1 Black", name="Call Me T-Shirt", color="Black",
                  category="T-SHIRTS", style_code="MKT-SS1", brand="CHINATOWN MARKET"),
        ItemGroup(code="CTM T SS1 White", name="Call Me T-Shirt", color="White",
                  category="T-SHIRTS", style_code="MKT-SS1", brand="CHINATOWN MARKET"),
        ItemGroup(code="CTM H HD9 Pink", name="Have A Nice Day Hoodie", color="Pink",
                  category="HOODYS", style_code="MKT-HD9", brand="CHINATOWN MARKET"),
    ]


# ── Master file ──────────────────────────────────────────────────────────────

def test_parse_master_rolls_sizes_up_into_item_groups(tmp_path):
    path = tmp_path / "master.xlsx"
    _make_master(path)
    parsed, meta = parse_master_workbook(str(path))

    assert {g.code for g in parsed} == {"CTM T SS1 Black", "CTM T SS1 White", "CTM H HD9 Pink"}
    by_code = {g.code: g for g in parsed}
    # Two size rows collapse into one group, and the count is kept.
    assert by_code["CTM T SS1 Black"].variants == 2
    assert by_code["CTM T SS1 Black"].name == "Call Me T-Shirt"
    assert by_code["CTM T SS1 Black"].color == "Black"
    assert by_code["CTM T SS1 Black"].category == "T-SHIRTS"
    assert by_code["CTM T SS1 Black"].brand == "CHINATOWN MARKET"
    assert meta["columns"]["code"] == "E"


def test_parse_master_accepts_a_column_override(tmp_path):
    path = tmp_path / "master.xlsx"
    _make_master(path)
    parsed, meta = parse_master_workbook(str(path), code_column="a")
    assert meta["columns"]["code"] == "A"
    # Column A is the size-level Code, so every row is its own "group".
    assert len(parsed) == 4


def test_parse_master_falls_back_to_the_highlighted_column(tmp_path):
    """SAP marks the Item Group Code column with a fill. When the header text
    doesn't match, that highlight is what identifies the column."""
    path = tmp_path / "master.xlsx"
    _make_master(path, highlight_code=True)
    wb = openpyxl.load_workbook(path)
    wb.active.cell(1, 5).value = "Grouping"     # rename the header away
    wb.save(path)

    parsed, meta = parse_master_workbook(str(path))
    assert meta["columns"]["code"] == "E"
    assert len(parsed) == 3


def test_parse_master_rejects_a_file_with_no_usable_header(tmp_path):
    path = tmp_path / "junk.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["alpha", "beta", "gamma"])
    wb.active.append(["1", "2", "3"])
    wb.save(path)
    with pytest.raises(ImageSortError):
        parse_master_workbook(str(path))


# ── Image collection ─────────────────────────────────────────────────────────

def test_collect_images_unpacks_nested_zips_and_skips_mac_junk(tmp_path):
    inner_dir = tmp_path / "build" / "inner"
    _make_image(inner_dir / "photo_a.png")
    _make_image(inner_dir / "photo_b.png", color=(10, 10, 200))
    inner_zip = tmp_path / "build" / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.write(inner_dir / "photo_a.png", "photo_a.png")
        zf.write(inner_dir / "photo_b.png", "photo_b.png")

    source = tmp_path / "source"
    source.mkdir()
    outer = source / "drop.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner_zip, "nested/inner.zip")
        zf.writestr("__MACOSX/._photo_a.png", "junk")
        zf.writestr(".DS_Store", "junk")

    images = collect_images(source)
    assert {i.filename for i in images} == {"photo_a.png", "photo_b.png"}


def test_collect_images_dedupes_identical_photos(tmp_path):
    source = tmp_path / "source"
    _make_image(source / "one.png")
    _make_image(source / "copies" / "one_again.png")   # byte-identical
    _make_image(source / "different.png", color=(0, 200, 0))

    images = collect_images(source)
    assert len(images) == 2


def test_collect_images_ignores_non_images(tmp_path):
    source = tmp_path / "source"
    _make_image(source / "real.png")
    (source / "notes.txt").write_text("hello")
    (source / "broken.png").write_bytes(b"not actually a png")

    images = collect_images(source)
    assert [i.filename for i in images] == ["real.png"]


# ── Filename fast path ───────────────────────────────────────────────────────

def test_filename_containing_the_item_code_matches_without_ai(groups, tmp_path):
    from app.core.image_sorter import SourceImage
    image = SourceImage(index=1, path=str(tmp_path / "x.jpg"),
                        filename="CTM_T_SS1_Black_1.jpg")
    matched = _filename_direct_match(image, groups)
    assert matched is not None and matched.code == "CTM T SS1 Black"


def test_filename_with_style_code_needs_the_colour_to_disambiguate(groups, tmp_path):
    from app.core.image_sorter import SourceImage
    # Style code alone is shared by Black and White — the colour decides.
    image = SourceImage(index=1, path=str(tmp_path / "x.jpg"),
                        filename="MKT-HD9 Pink front.jpg")
    matched = _filename_direct_match(image, groups)
    assert matched is not None and matched.code == "CTM H HD9 Pink"


def test_meaningless_filename_has_no_direct_match(groups, tmp_path):
    from app.core.image_sorter import SourceImage
    image = SourceImage(index=1, path=str(tmp_path / "x.jpg"),
                        filename="CTM SUMMER MOCK17.png")
    assert _filename_direct_match(image, groups) is None


# ── Scoring / shortlist ──────────────────────────────────────────────────────

def test_score_prefers_the_right_colourway_of_the_same_style(groups):
    idf = _build_idf(groups)
    desc = {
        "category": "T-SHIRTS", "product_type": "short sleeve t-shirt",
        "primary_color": "White", "secondary_colors": [], "pattern": "",
        "visible_text": ["CALL ME"], "graphic": "", "view": "front", "quality": 0.9,
    }
    white = score_group(desc, groups[1], idf)
    black = score_group(desc, groups[0], idf)
    hoodie = score_group(desc, groups[2], idf)
    assert white > black > hoodie


def test_shortlist_keeps_same_category_options_even_when_colour_is_off(groups):
    idf = _build_idf(groups)
    desc = {
        "category": "T-SHIRTS", "product_type": "t-shirt", "primary_color": "Purple",
        "secondary_colors": [], "pattern": "", "visible_text": [], "graphic": "",
        "view": "front", "quality": 0.5,
    }
    picked = [g.code for g, _ in shortlist(desc, groups, idf, limit=1)]
    assert "CTM T SS1 Black" in picked and "CTM T SS1 White" in picked


# ── Positions / naming ───────────────────────────────────────────────────────

def _result(index, code, filename, view="front", quality=0.5, confidence=0.9, path=""):
    return MatchResult(index=index, filename=filename, source="", path=path, code=code,
                       confidence=confidence, method="ai",
                       description={"view": view, "quality": quality})


def test_main_image_is_the_best_packshot_not_the_detail_crop():
    results = [
        _result(1, "CTM T SS1 Black", "a.jpg", view="detail", quality=0.2),
        _result(2, "CTM T SS1 Black", "b.jpg", view="packshot", quality=0.95),
        _result(3, "CTM T SS1 Black", "c.jpg", view="on-model", quality=0.6),
    ]
    assign_positions(results)
    assert {r.filename: r.position for r in results} == {"b.jpg": 1, "c.jpg": 2, "a.jpg": 3}


def test_a_filename_that_already_says_1_keeps_the_main_slot():
    results = [
        _result(1, "CTM T SS1 Black", "SHOT_2.jpg", view="packshot", quality=0.99),
        _result(2, "CTM T SS1 Black", "SHOT_1.jpg", view="detail", quality=0.1),
    ]
    assign_positions(results)
    assert {r.filename: r.position for r in results} == {"SHOT_1.jpg": 1, "SHOT_2.jpg": 2}


def test_unmatched_photos_get_no_position():
    results = [_result(1, "", "orphan.jpg")]
    assign_positions(results)
    assert results[0].position == 0


def test_output_names_put_the_main_image_at_underscore_one():
    assert image_file_name("CTM T SS1 Black", 1, "whatever.PNG") == "CTM_T_SS1_Black_1.png"
    assert image_file_name("CTM T SS1 Black", 3, "x.jpeg") == "CTM_T_SS1_Black_3.jpeg"


def test_folder_names_are_filesystem_safe_but_keep_spaces():
    assert safe_folder_name("CTM T SS1 Black") == "CTM T SS1 Black"
    assert safe_folder_name("CTM/T:SS1*Black") == "CTM-T-SS1-Black"
    assert safe_folder_name("  ") == "UNKNOWN"


# ── Output tree ──────────────────────────────────────────────────────────────

def test_build_output_tree_writes_one_folder_per_item_group(tmp_path):
    source = tmp_path / "src"
    _make_image(source / "a.png")
    _make_image(source / "b.png", color=(0, 0, 255))
    _make_image(source / "c.png", color=(0, 255, 0))

    results = [
        _result(1, "CTM T SS1 Black", "a.png", path=str(source / "a.png")),
        _result(2, "CTM T SS1 Black", "b.png", path=str(source / "b.png"), quality=0.9),
        _result(3, "", "c.png", path=str(source / "c.png")),
    ]
    assign_positions(results)
    out = tmp_path / "tree"
    summary = build_output_tree(results, out)

    assert summary == {
        "folders": 1, "images": 2, "unmatched": 1,
        "folder_map": {"CTM T SS1 Black": ["CTM_T_SS1_Black_1.png", "CTM_T_SS1_Black_2.png"]},
    }
    folder = out / "CTM T SS1 Black"
    assert sorted(p.name for p in folder.iterdir()) == [
        "CTM_T_SS1_Black_1.png", "CTM_T_SS1_Black_2.png",
    ]
    # Only that one folder exists, and the unmatched photo is nowhere in the tree.
    assert [p.name for p in out.iterdir()] == ["CTM T SS1 Black"]
    assert list(out.rglob("c.png")) == []


def test_build_output_zip_contains_the_folder_tree(tmp_path):
    source = tmp_path / "src"
    _make_image(source / "a.png")
    results = [_result(1, "CTM T SS1 Black", "a.png", path=str(source / "a.png"))]
    assign_positions(results)

    zip_path = tmp_path / "out.zip"
    build_output_zip(results, zip_path, tmp_path / "work")
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["CTM T SS1 Black/CTM_T_SS1_Black_1.png"]


def test_report_lists_matched_photos_and_item_groups_with_none(groups):
    results = [_result(1, "CTM T SS1 Black", "a.png")]
    assign_positions(results)
    csv_text = build_report_csv(results, groups)

    assert "CTM_T_SS1_Black_1.png" in csv_text
    # The two groups that got nothing must still appear, marked as such.
    assert csv_text.count("no-image") == 2
    assert "CTM H HD9 Pink" in csv_text


def test_summarise_counts_coverage(groups):
    results = [
        _result(1, "CTM T SS1 Black", "a.png"),
        _result(2, "CTM T SS1 Black", "b.png"),
        _result(3, "", "c.png"),
    ]
    assign_positions(results)
    assert summarise(results, groups) == {
        "images": 3, "matched": 2, "unmatched": 1, "review": 1,
        "groups_total": 3, "groups_covered": 1, "groups_missing": 2,
    }


# ── Review flags ─────────────────────────────────────────────────────────────

def test_low_confidence_ai_matches_are_flagged_for_review():
    assert _result(1, "CTM T SS1 Black", "a.png", confidence=0.4).needs_review
    assert not _result(2, "CTM T SS1 Black", "b.png", confidence=0.95).needs_review


def test_hand_set_matches_are_never_flagged():
    r = _result(1, "CTM T SS1 Black", "a.png", confidence=0.1)
    r.method = "manual"
    assert not r.needs_review


def test_crowded_folder_is_flagged_when_a_sibling_got_nothing(groups):
    """Two styles that print the same slogan let one folder hoard the other's
    photos. The tell is a full folder next to an empty runner-up."""
    results = [
        _result(i, "CTM T SS1 Black", f"{i}.png", confidence=c)
        for i, c in enumerate([0.95, 0.9, 0.8, 0.78], start=1)
    ]
    for r in results[2:]:
        r.runner_up = "CTM T SS1 White"

    assert flag_conflicts(results, groups) == 2
    assert [r.needs_review for r in results] == [False, False, True, True]
    assert "CTM T SS1 White" in results[2].flagged


def test_no_conflict_flag_when_every_group_already_has_a_photo(groups):
    results = [
        _result(1, "CTM T SS1 Black", "a.png"),
        _result(2, "CTM T SS1 Black", "b.png"),
        _result(3, "CTM T SS1 Black", "c.png"),
        _result(4, "CTM T SS1 White", "d.png"),
        _result(5, "CTM H HD9 Pink", "e.png"),
    ]
    results[2].runner_up = "CTM T SS1 White"
    assert flag_conflicts(results, groups) == 0


# ── Link handling ────────────────────────────────────────────────────────────

def test_detect_source_classifies_the_links_brands_send():
    assert detect_source("https://www.dropbox.com/scl/fo/abc?rlkey=x&dl=0") == "dropbox"
    assert detect_source("https://drive.google.com/file/d/15Ru5/view") == "gdrive"
    assert detect_source("https://docs.google.com/spreadsheets/d/1o9R5/edit") == "gsheet"
    assert detect_source("https://www.brandboom.com/app/a/B7D4EB1138C") == "brandboom"
    assert detect_source("https://cdn.example.com/pack.zip") == "direct"
    assert detect_source("not a url") == ""


def test_dropbox_links_are_rewritten_to_download_directly():
    url = _dropbox_download_url(
        "https://www.dropbox.com/scl/fo/abc/DEF?rlkey=k1&st=abc&e=1&dl=0"
    )
    assert "dl=1" in url and "dl=0" not in url
    assert "st=" not in url and "e=1" not in url
    assert "rlkey=k1" in url


def test_a_sheets_link_is_rejected_as_an_image_source(tmp_path):
    # Imported together: conftest reloads app.* per session, so the class this
    # module bound at import time is not the one the reloaded function raises.
    from app.core.image_sources import SourceFetchError as Err, fetch_image_source
    with pytest.raises(Err, match="master file"):
        fetch_image_source("https://docs.google.com/spreadsheets/d/abc/edit", tmp_path)

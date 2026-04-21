from __future__ import annotations

from app.core.searcher import ImageSearcher, SearchHit, item_sort_key


def test_cache_identity_normalizes_size_suffixed_footwear_codes():
    searcher = ImageSearcher()

    item_a = {
        "item_code": "3WE30133563-W-6",
        "style_name": "Cloudvista 2 W",
        "color_name": "Pelican / Ghost / Yellow",
        "brand": "ON",
        "item_group": "Footwear",
    }
    item_b = {
        "item_code": "3WE30133563-W-8.5",
        "style_name": "Cloudvista 2 W",
        "color_name": "Pelican / Ghost / Yellow",
        "brand": "ON",
        "item_group": "Footwear",
    }

    assert searcher.cache_identity(item_a) == searcher.cache_identity(item_b)
    assert searcher.build_manual_search_query(item_a).endswith("3WE30133563-W")


def test_search_prefers_official_footwear_hit_over_bike_or_drink(monkeypatch):
    searcher = ImageSearcher({"brand_site_urls": {"on": ["on.com"]}})

    official = SearchHit(
        url="https://cdn.on.com/images/cloudvista-2-pelican-ghost.jpg",
        page_url="https://www.on.com/en-us/products/cloudvista-2/3WE30133563/pelican-ghost",
        title="Women's running shoes On Cloudvista 2 Pelican / Ghost (3WE30133563-W)",
        description="Trail running shoe",
    )
    bike = SearchHit(
        url="https://example.com/images/ghost-bike.jpg",
        page_url="https://example.com/ghost-mountain-bike",
        title="Ghost mountain bike",
        description="Blue and yellow MTB bicycle",
    )
    drink = SearchHit(
        url="https://example.com/images/mountain-dew-can.jpg",
        page_url="https://example.com/mountain-dew-can",
        title="Mountain Dew can",
        description="Limited edition yellow can",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [official] if domain == "on.com" else [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [bike, drink])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, scores = searcher.search({
        "item_code": "3WE30133563-W-9",
        "style_name": "Cloudvista 2 W",
        "color_name": "Pelican / Ghost / Yellow",
        "brand": "ON",
        "item_group": "Footwear",
    })

    assert candidates[0] == official.url
    assert scores[official.url] > scores[bike.url]
    assert scores[official.url] > scores[drink.url]


def test_search_penalizes_wrong_garment_type_even_when_code_matches(monkeypatch):
    searcher = ImageSearcher()

    wrong_tshirt = SearchHit(
        url="https://example.com/casablanca/ws25-tr-280-02-tee.jpg",
        page_url="https://example.com/casablanca/graffiti-t-shirt",
        title="Casablanca Casa Blanca Graffiti T-Shirt BLUE WS25-TR-280-02",
        description="Graphic tee",
    )
    correct_shorts = SearchHit(
        url="https://example.com/casablanca/ws25-tr-280-02-shorts.jpg",
        page_url="https://example.com/casablanca/graffiti-skater-shorts",
        title="Casablanca Casa Blanca Graffiti Skater Shorts BLUE WS25-TR-280-02",
        description="Men's shorts",
    )

    monkeypatch.setattr(searcher, "_bing_search", lambda query: [wrong_tshirt, correct_shorts])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, scores = searcher.search({
        "item_code": "WS25-TR-280-02",
        "style_name": "Casa Blanca Graffiti Skater Shorts",
        "color_name": "Blue",
        "brand": "Casablanca",
        "item_group": "Shorts",
    })

    assert candidates[0] == correct_shorts.url
    assert scores[correct_shorts.url] > scores[wrong_tshirt.url]


def test_search_collapses_same_image_resize_variants(monkeypatch):
    searcher = ImageSearcher({"brand_site_urls": {"on": ["on.com"]}})

    hit_a = SearchHit(
        url="https://images.ctfassets.net/example/cloudvista.png?w=4000&h=4000&fm=jpg",
        page_url="https://www.on.com/en-us/products/cloudvista-2/3WE30133563/pelican-ghost",
        title="Women's running shoes On Cloudvista 2 Pelican / Ghost (3WE30133563-W)",
        description="Trail running shoe",
    )
    hit_b = SearchHit(
        url="https://images.ctfassets.net/example/cloudvista.png?w=1600&h=1600&fm=jpg",
        page_url="https://www.on.com/en-us/products/cloudvista-2/3WE30133563/pelican-ghost",
        title="Women's running shoes On Cloudvista 2 Pelican / Ghost (3WE30133563-W)",
        description="Trail running shoe",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [hit_a, hit_b] if domain == "on.com" else [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, _scores = searcher.search({
        "item_code": "3WE30133563-W-9",
        "style_name": "Cloudvista 2 W",
        "color_name": "Pelican / Ghost / Yellow",
        "brand": "ON",
        "item_group": "Footwear",
    })

    assert len(candidates) == 1


def test_search_prefers_exact_google_style_query_results(monkeypatch):
    searcher = ImageSearcher()

    exact = SearchHit(
        url="https://buttergoods.com/images/wharfie-beanie-bone.jpg",
        page_url="https://buttergoods.com/products/wharfie-beanie-bone",
        title="Butter Goods Wharfie Beanie Bone BG243810-BONE",
        description="Official product image",
    )
    generic = SearchHit(
        url="https://example.com/images/wharfie-beanie-black.jpg",
        page_url="https://example.com/products/wharfie-beanie-black",
        title="Butter Goods Wharfie Beanie Black",
        description="Marketplace image",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_google_images_scrape",
        lambda query: [exact] if query.startswith("Butter Goods Wharfie Beanie") else [generic],
    )

    candidates, scores = searcher.search({
        "item_code": "BG243810-BONE",
        "style_name": "Wharfie Beanie",
        "color_name": "bone",
        "brand": "Butter Goods",
        "item_group": "Beanie",
    })

    assert candidates[0] == exact.url
    assert scores[exact.url] > scores[generic.url]


def test_search_prefers_quoted_phrase_hits_for_full_manual_query(monkeypatch):
    searcher = ImageSearcher()

    exact = SearchHit(
        url="https://buttergoods.com/images/wharfie-beanie-bone-packshot.jpg",
        page_url="https://buttergoods.com/products/wharfie-beanie-bone",
        title="Butter Goods Wharfie Beanie Bone BG243810-BONE",
        description="Official product image",
    )
    generic = SearchHit(
        url="https://example.com/images/beanie.jpg",
        page_url="https://example.com/products/beanie",
        title="Butter Goods Beanie",
        description="Generic listing",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [exact] if query.startswith("\"Butter Goods") else [generic])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, scores = searcher.search({
        "item_code": "BG243810-BONE",
        "style_name": "Wharfie Beanie",
        "color_name": "bone",
        "brand": "Butter Goods",
        "item_group": "Beanie",
    })

    assert candidates[0] == exact.url
    assert scores[exact.url] > scores[generic.url]


def test_item_sort_key_keeps_same_style_and_base_code_grouped_by_size():
    rows = [
        {
            "brand": "adidas",
            "style_name": "Wmns Gazelle Indoor",
            "item_code": "HQ8718-8.5",
            "item_group": "Footwear",
            "color_name": "Red",
            "color_code": "",
            "size": "8.5",
        },
        {
            "brand": "adidas",
            "style_name": "Wmns Gazelle Indoor",
            "item_code": "HQ8718-6.5",
            "item_group": "Footwear",
            "color_name": "Red",
            "color_code": "",
            "size": "6.5",
        },
        {
            "brand": "adidas",
            "style_name": "Wmns Gazelle Indoor",
            "item_code": "HQ8718-7.5",
            "item_group": "Footwear",
            "color_name": "Red",
            "color_code": "",
            "size": "7.5",
        },
    ]

    ordered = sorted(
        rows,
        key=lambda row: item_sort_key(
            brand=row["brand"],
            style_name=row["style_name"],
            item_code=row["item_code"],
            item_group=row["item_group"],
            color_name=row["color_name"],
            color_code=row["color_code"],
            size=row["size"],
        ),
    )

    assert [row["size"] for row in ordered] == ["6.5", "7.5", "8.5"]


def test_brand_playbook_matches_american_rag_variants():
    searcher = ImageSearcher()

    urls = searcher.matching_brand_site_urls("American Rag Cie")

    assert "americanrag.ae" in urls


def test_assess_match_confidence_auto_approves_strong_official_match():
    searcher = ImageSearcher({"brand_site_urls": {"on": ["on.com"]}})
    item = {
        "item_code": "3WE30133563-W-9",
        "style_name": "Cloudvista 2 W",
        "color_name": "Pelican / Ghost / Yellow",
        "brand": "ON",
        "item_group": "Footwear",
    }
    urls = [
        "https://www.on.com/en-us/products/cloudvista-2/3WE30133563/pelican-ghost",
        "https://example.com/random-shoe",
    ]
    scores = {
        urls[0]: 0.89,
        urls[1]: 0.54,
    }

    decision = searcher.assess_match_confidence(urls, scores, item)

    assert decision["label"] == "high"
    assert decision["auto_approve"] is True
    assert decision["suggested_url"] == urls[0]


def test_assess_match_confidence_sends_ambiguous_results_to_review():
    searcher = ImageSearcher()
    item = {
        "item_code": "BG243810-BONE",
        "style_name": "Wharfie Beanie",
        "color_name": "bone",
        "brand": "Butter Goods",
        "item_group": "Beanie",
    }
    urls = [
        "https://example.com/products/beanie-bone",
        "https://marketplace.test/generic-butter-goods-beanie",
    ]
    scores = {
        urls[0]: 0.66,
        urls[1]: 0.61,
    }

    decision = searcher.assess_match_confidence(urls, scores, item)

    assert decision["label"] in {"medium", "low"}
    assert decision["auto_approve"] is False

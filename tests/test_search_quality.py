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
    assert bike.url not in scores or scores[official.url] > scores[bike.url]
    assert drink.url not in scores or scores[official.url] > scores[drink.url]


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
    assert wrong_tshirt.url not in scores or scores[correct_shorts.url] > scores[wrong_tshirt.url]


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
    assert generic.url not in scores or scores[exact.url] > scores[generic.url]


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
    assert generic.url not in scores or scores[exact.url] > scores[generic.url]


def test_search_filters_obvious_wrong_color_variants_in_strict_mode(monkeypatch):
    searcher = ImageSearcher()

    exact = SearchHit(
        url="https://aurelien.com/products/yacht-loafers-chocolate.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers YLWCHT-3800",
        description="Chocolate suede loafers",
    )
    wrong_black = SearchHit(
        url="https://aurelien.com/products/yacht-loafers-black.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-black",
        title="Aurélien Lady Black Yacht Loafers YLWCHT-3800",
        description="Black suede loafers",
    )
    wrong_beige = SearchHit(
        url="https://aurelien.com/products/yacht-loafers-beige.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-beige",
        title="Aurélien Lady Beige Yacht Loafers YLWCHT-3800",
        description="Beige suede loafers",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [exact, wrong_black, wrong_beige])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates == [exact.url]


def test_search_prefers_clean_packshot_over_detail_or_outsole_views(monkeypatch):
    searcher = ImageSearcher({"brand_site_urls": {"aurelien": ["aurelien.com"]}})

    packshot = SearchHit(
        url="https://aurelien.com/images/lady-chocolate-yacht-loafers-packshot.jpg",
        page_url="https://aurelien.com/products/lady-chocolate-yacht-loafers",
        title="Aurélien Lady Chocolate Yacht Loafers packshot",
        description="Official product image",
    )
    detail = SearchHit(
        url="https://aurelien.com/images/lady-chocolate-yacht-loafers-detail-closeup.jpg",
        page_url="https://aurelien.com/products/lady-chocolate-yacht-loafers",
        title="Aurélien Lady Chocolate Yacht Loafers detail closeup",
        description="Detail view",
    )
    outsole = SearchHit(
        url="https://aurelien.com/images/lady-chocolate-yacht-loafers-outsole.jpg",
        page_url="https://aurelien.com/products/lady-chocolate-yacht-loafers",
        title="Aurélien Lady Chocolate Yacht Loafers outsole",
        description="Bottom sole view",
    )
    on_foot = SearchHit(
        url="https://aurelien.com/images/lady-chocolate-yacht-loafers-on-foot.jpg",
        page_url="https://aurelien.com/products/lady-chocolate-yacht-loafers",
        title="Aurélien Lady Chocolate Yacht Loafers on-foot",
        description="Lifestyle styling image",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [packshot, detail, outsole, on_foot])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates[0] == packshot.url
    assert detail.url not in candidates
    assert outsole.url not in candidates
    assert on_foot.url not in candidates


def test_strict_search_caps_candidates_to_few_high_quality_options(monkeypatch):
    searcher = ImageSearcher()

    hits = [
        SearchHit(
            url=f"https://aurelien.com/images/yacht-loafers-chocolate-{idx}.jpg",
            page_url="https://aurelien.com/products/yacht-loafers-chocolate",
            title=f"Aurélien Lady Chocolate Yacht Loafers option {idx}",
            description="Chocolate loafers",
        )
        for idx in range(1, 7)
    ]

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: hits)
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert len(candidates) <= 3


def test_strict_search_prefers_google_exact_pool_before_broad_matches(monkeypatch):
    searcher = ImageSearcher()

    google_exact = SearchHit(
        url="https://aurelien.com/images/yacht-loafers-chocolate-packshot.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers YLWCHT-3800",
        description="Official packshot",
    )
    broad_wrong = SearchHit(
        url="https://example.com/images/yacht-loafers-brown.jpg",
        page_url="https://example.com/products/yacht-loafers-brown",
        title="Aurélien Lady Brown Yacht Loafers",
        description="Marketplace listing",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [broad_wrong])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_google_images_scrape",
        lambda query: [google_exact] if query.startswith("\"Aurélien Lady Chocolate Yacht Loafers") else [broad_wrong],
    )
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates[0] == google_exact.url
    assert broad_wrong.url not in candidates


def test_strict_search_uses_google_or_bing_exact_pool_before_broad_sources(monkeypatch):
    searcher = ImageSearcher()

    google_exact = SearchHit(
        url="https://aurelien.com/images/yacht-loafers-chocolate-clean.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers YLWCHT-3800",
        description="Official clean packshot",
    )
    bing_exact = SearchHit(
        url="https://aurelien.com/images/yacht-loafers-chocolate-bing-clean.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers YLWCHT-3800",
        description="Official clean Bing packshot",
    )
    broad_good = SearchHit(
        url="https://example.com/images/yacht-loafers-chocolate-marketplace.jpg",
        page_url="https://example.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers",
        description="Marketplace listing",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_bing_search",
        lambda query: [bing_exact] if query.startswith("\"Aurélien Lady Chocolate Yacht Loafers") else [broad_good],
    )
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_google_images_scrape",
        lambda query: [google_exact] if query.startswith("\"Aurélien Lady Chocolate Yacht Loafers") else [broad_good],
    )
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [broad_good])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [broad_good])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates
    assert candidates[0] == google_exact.url
    assert broad_good.url not in candidates


def test_strict_search_falls_back_to_bing_exact_pool_when_google_exact_missing(monkeypatch):
    searcher = ImageSearcher()

    bing_exact = SearchHit(
        url="https://aurelien.com/images/yacht-loafers-chocolate-bing-clean.jpg",
        page_url="https://aurelien.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers YLWCHT-3800",
        description="Official clean Bing packshot",
    )
    broad_good = SearchHit(
        url="https://example.com/images/yacht-loafers-chocolate-marketplace.jpg",
        page_url="https://example.com/products/yacht-loafers-chocolate",
        title="Aurélien Lady Chocolate Yacht Loafers",
        description="Marketplace listing",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_bing_search",
        lambda query: [bing_exact] if query.startswith("\"Aurélien Lady Chocolate Yacht Loafers") else [broad_good],
    )
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [broad_good])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [broad_good])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [broad_good])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates
    assert candidates[0] == bing_exact.url
    assert broad_good.url not in candidates


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

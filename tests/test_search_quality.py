from __future__ import annotations

from app.core.searcher import ImageSearcher, SearchHit, item_sort_key, normalize_related_item_code


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

    # Only return hits for the broad query; exact/phrase queries return nothing so
    # hits stay in source 'bing' only, keeping the category penalty visible.
    monkeypatch.setattr(
        searcher,
        "_bing_search",
        lambda query: [wrong_tshirt, correct_shorts] if not query.startswith('"') else [],
    )
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

    assert len(candidates) <= 5


def test_related_item_code_normalizes_near_duplicate_variant_suffixes():
    related_a = normalize_related_item_code(
        "CITYLOAFER2LGRY-4300",
        item_group="Footwear",
        style_name="City Loafer M",
    )
    related_b = normalize_related_item_code(
        "CITYLOAFER2LGRY-4200",
        item_group="Footwear",
        style_name="City Loafer M",
    )

    assert related_a == "CITYLOAFER2LGRY"
    assert related_a == related_b


def test_cache_identity_uses_related_family_code_for_near_duplicate_variants():
    searcher = ImageSearcher()

    item_a = {
        "item_code": "CITYLOAFER2LGRY-4300",
        "style_name": "City Loafer M",
        "color_name": "LIGHT GREY",
        "brand": "Aurélien",
        "item_group": "Footwear",
    }
    item_b = {
        "item_code": "CITYLOAFER2LGRY-4200",
        "style_name": "City Loafer M",
        "color_name": "LIGHT GREY",
        "brand": "Aurélien",
        "item_group": "Footwear",
    }

    assert searcher.cache_identity(item_a) == searcher.cache_identity(item_b)


def test_search_prefers_related_family_code_hit_for_near_duplicate_variant_skus(monkeypatch):
    searcher = ImageSearcher({"brand_site_urls": {"aurélien": ["aurelien.com"]}})

    correct_family_hit = SearchHit(
        url="https://aurelien.com/images/cityloafer2lgry-packshot.jpg",
        page_url="https://aurelien.com/products/city-loafer-light-grey",
        title="Aurélien City Loafer M Light Grey CITYLOAFER2LGRY",
        description="Official clean packshot",
    )
    wrong_color_hit = SearchHit(
        url="https://aurelien.com/images/cityloafer2brn-packshot.jpg",
        page_url="https://aurelien.com/products/city-loafer-brown",
        title="Aurélien City Loafer M Chocolate CITYLOAFER2BRN",
        description="Official brown packshot",
    )

    monkeypatch.setattr(searcher, "_bing_site_search", lambda domain, query: [])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_search", lambda query: [])
    monkeypatch.setattr(
        searcher,
        "_google_images_scrape",
        lambda query: [correct_family_hit] if "CITYLOAFER2LGRY" in query else [wrong_color_hit],
    )
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [wrong_color_hit])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, _scores = searcher.search({
        "item_code": "CITYLOAFER2LGRY-4300",
        "style_name": "City Loafer M",
        "color_name": "LIGHT GREY",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })

    assert candidates
    assert candidates[0] == correct_family_hit.url
    assert wrong_color_hit.url not in candidates


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
    # Google exact/phrase is tier 0 because it should mirror manual Google first.
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


def test_brand_playbook_matches_aurelien_official_domain():
    searcher = ImageSearcher()

    urls = searcher.matching_brand_site_urls("Aurélien")

    assert "aurelien-online.com" in urls


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


# ── Cross-engine consensus + first-visible ranking ──────────────────────────

def _stub_all_search_methods(searcher, monkeypatch):
    """Reset every search source to an empty list. Tests then override just
    the specific methods they care about."""
    for name in (
        "_bing_search", "_bing_raw", "_bing_site_search",
        "_google_search", "_google_images_scrape",
        "_duckduckgo_search", "_yahoo_images_scrape",
    ):
        if name == "_bing_site_search":
            monkeypatch.setattr(searcher, name, lambda domain, query: [])
        else:
            monkeypatch.setattr(searcher, name, lambda query=None: [])


def test_search_prefers_url_that_appears_on_both_google_and_bing(monkeypatch):
    """A consensus hit (top-5 on Google AND top-5 on Bing) should beat a URL
    that only shows up on one engine, even when the one-engine URL has
    equally strong keyword hits."""
    searcher = ImageSearcher()

    consensus = SearchHit(
        url="https://brand.example/images/wharfie-beanie-bone.jpg",
        page_url="https://brand.example/products/wharfie-beanie-bone",
        title="Butter Goods Wharfie Beanie BG243810-BONE bone",
        description="Wool beanie",
    )
    bing_only = SearchHit(
        url="https://otherstore.example/images/wharfie-beanie-bone-alt.jpg",
        page_url="https://otherstore.example/wharfie-beanie-bone",
        title="Butter Goods Wharfie Beanie BG243810-BONE bone",
        description="Wool beanie",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [consensus, bing_only])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [consensus])

    candidates, scores = searcher.search({
        "item_code": "BG243810-BONE",
        "style_name": "Wharfie Beanie",
        "color_name": "bone",
        "brand": "Butter Goods",
        "item_group": "Beanie",
    })

    assert candidates[0] == consensus.url
    # Scores may both hit the 1.0 ceiling; what matters is consensus ranks first
    assert scores[consensus.url] >= scores.get(bing_only.url, 0.0)


def test_search_rewards_top_serp_position_over_deep_ranked(monkeypatch):
    """A URL at position 0 on Google and Bing should outrank a URL that's only
    20th on Bing, all else equal."""
    searcher = ImageSearcher()

    front_page = SearchHit(
        url="https://brand.example/hero.jpg",
        page_url="https://brand.example/hero",
        title="Butter Goods Wharfie Beanie BG243810-BONE bone",
        description="Wool beanie",
    )
    deep_rank_filler = [
        SearchHit(
            url=f"https://filler.example/img-{i}.jpg",
            page_url="https://filler.example",
            title="random",
            description="",
        )
        for i in range(20)
    ]
    buried = SearchHit(
        url="https://buried.example/hero.jpg",
        page_url="https://buried.example/hero",
        title="Butter Goods Wharfie Beanie BG243810-BONE bone",
        description="Wool beanie",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [front_page, *deep_rank_filler, buried])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [front_page])

    candidates, scores = searcher.search({
        "item_code": "BG243810-BONE",
        "style_name": "Wharfie Beanie",
        "color_name": "bone",
        "brand": "Butter Goods",
        "item_group": "Beanie",
    })

    assert candidates[0] == front_page.url
    # Scores may both hit the 1.0 ceiling; what matters is front_page ranks first
    if buried.url in scores:
        assert scores[front_page.url] >= scores[buried.url]


def test_color_equivalence_accepts_brown_page_for_chocolate_item(monkeypatch):
    """When the item's color is 'chocolate', a vendor page described as
    'brown' should not be filtered out as a wrong-color variant — these are
    sibling shades in the same equivalence class."""
    searcher = ImageSearcher()

    hit = SearchHit(
        url="https://brand.example/images/loafer-brown.jpg",
        page_url="https://brand.example/products/yacht-loafer-brown",
        title="Aurélien Lady Yacht Loafer YLWCHT-3800 brown",
        description="Leather loafer",
    )
    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [hit])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [hit])

    candidates, _ = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Yacht Loafer",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })

    assert hit.url in candidates


def test_color_equivalence_still_rejects_unrelated_colors():
    """Equivalence classes should broaden within a family but NOT blur across
    families — a 'black' page is still wrong for a 'chocolate' item."""
    searcher = ImageSearcher()
    wrong_black = {
        "url": "https://brand.example/images/loafer-black.jpg",
        "page_url": "https://brand.example/products/yacht-loafer-black",
        "title": "Aurélien Yacht Loafer BLACK",
        "description": "",
    }
    right_brown = {
        "url": "https://brand.example/images/loafer-brown.jpg",
        "page_url": "https://brand.example/products/yacht-loafer-brown",
        "title": "Aurélien Yacht Loafer brown",
        "description": "",
    }
    ctx = searcher._build_item_context({
        "item_code": "YLWCHT-3800",
        "style_name": "Yacht Loafer",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })
    assert searcher._is_obvious_wrong_color_hit(wrong_black, ctx) is True
    assert searcher._is_obvious_wrong_color_hit(right_brown, ctx) is False


def test_dominant_color_cluster_picks_item_color_when_it_dominates(monkeypatch):
    """When most top results contain 'brown' (≈ 'chocolate'), the dominant
    cluster includes the item's color family and the search returns only
    those matching hits, dropping the black outlier."""
    searcher = ImageSearcher()

    chocolate_a = SearchHit(
        url="https://brand.example/loafer-chocolate-1.jpg",
        page_url="https://brand.example/loafer-chocolate",
        title="Aurélien Yacht Loafer YLWCHT-3800 chocolate",
        description="Chocolate leather loafer",
    )
    brown_b = SearchHit(
        url="https://brand.example/loafer-brown-2.jpg",
        page_url="https://brand.example/loafer-brown",
        title="Aurélien Yacht Loafer YLWCHT-3800 brown",
        description="Brown leather loafer",
    )
    mocha_c = SearchHit(
        url="https://brand.example/loafer-mocha-3.jpg",
        page_url="https://brand.example/loafer-mocha",
        title="Aurélien Yacht Loafer YLWCHT-3800 mocha",
        description="Mocha leather loafer",
    )
    black_outlier = SearchHit(
        url="https://brand.example/loafer-black-4.jpg",
        page_url="https://brand.example/loafer-black",
        title="Aurélien Yacht Loafer YLWCHT-3800 black",
        description="Black leather loafer",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(
        searcher, "_bing_search",
        lambda query: [chocolate_a, brown_b, mocha_c, black_outlier],
    )
    monkeypatch.setattr(
        searcher, "_google_images_scrape",
        lambda query: [chocolate_a, brown_b, mocha_c],
    )

    candidates, scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Yacht Loafer",
        "color_name": "Chocolate",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })

    # Chocolate/brown/mocha are all in the same equivalence class — should survive
    assert chocolate_a.url in candidates or brown_b.url in candidates
    # Black is a completely different color family — should be filtered out
    assert black_outlier.url not in candidates


def test_compound_color_rejects_light_beige_for_light_grey_item():
    """The 'LIGHT GREY' vs 'LIGHT BEIGE' bug: old tokenisation put 'light'
    into the acceptable set, so any 'light *' page matched. The compound
    tokeniser fixes this."""
    searcher = ImageSearcher()
    wrong_light_beige = {
        "url": "https://aurelien.com/images/light-beige-yacht-loafer.jpg",
        "page_url": "https://aurelien.com/products/light-beige-yacht-loafer",
        "title": "Aurélien Light Beige Yacht Loafer",
        "description": "",
    }
    right_light_grey = {
        "url": "https://aurelien.com/images/light-grey-yacht-loafer.jpg",
        "page_url": "https://aurelien.com/products/light-grey-yacht-loafer",
        "title": "Aurélien Light Grey Yacht Loafer",
        "description": "",
    }
    ctx = searcher._build_item_context({
        "item_code": "CITYLOAFER2LGRY-4200",
        "style_name": "City Loafer",
        "color_name": "LIGHT GREY",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })
    assert ctx["color_tokens"] == ["lightgrey"]
    assert searcher._is_obvious_wrong_color_hit(wrong_light_beige, ctx) is True
    assert searcher._is_obvious_wrong_color_hit(right_light_grey, ctx) is False


def test_strict_search_prefers_exact_color_over_broad_color_family(monkeypatch):
    """LIGHT GREY should not be displaced by same-family-but-not-exact colors
    like taupe/stone when a clean exact LIGHT GREY hit exists."""
    searcher = ImageSearcher()

    exact_light_grey = SearchHit(
        url="https://aurelien-online.com/images/city-loafer-light-grey-01.jpg",
        page_url="https://aurelien-online.com/products/light-grey-city-loafer",
        title="Aurélien Light Grey City Loafer CITYLOAFER2LGRY",
        description="Clean packshot",
    )
    broad_taupe = SearchHit(
        url="https://aurelien-online.com/images/city-loafer-taupe-01.jpg",
        page_url="https://aurelien-online.com/products/taupe-city-loafer",
        title="Aurélien Taupe City Loafer CITYLOAFER2LGRY",
        description="Clean packshot",
    )
    broad_stone = SearchHit(
        url="https://aurelien-online.com/images/city-loafer-stone-01.jpg",
        page_url="https://aurelien-online.com/products/stone-city-loafer",
        title="Aurélien Stone City Loafer CITYLOAFER2LGRY",
        description="Clean packshot",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [exact_light_grey, broad_taupe, broad_stone])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [broad_taupe, broad_stone])

    candidates, _scores = searcher.search({
        "item_code": "CITYLOAFER2LGRY-4300",
        "style_name": "City Loafer M",
        "color_name": "LIGHT GREY",
        "brand": "Aurélien",
        "item_group": "Footwear",
    })

    assert candidates
    assert candidates[0] == exact_light_grey.url
    assert broad_taupe.url not in candidates
    assert broad_stone.url not in candidates


def test_strict_search_prefers_distinctive_style_token_over_same_color_sibling(monkeypatch):
    """Official same-color images for another model should not beat the item
    whose distinctive style/model word appears in the query."""
    searcher = ImageSearcher()

    correct_yacht = SearchHit(
        url="https://aurelien-online.com/cdn/shop/products/aurelien-yacht-loafers-chocolate-01.jpg",
        page_url="https://aurelien-online.com/products/yacht-loafers-chocolate",
        title="Aurélien Yacht Loafers Chocolate",
        description="Clean packshot",
    )
    wrong_voyager = SearchHit(
        url="https://aurelien-online.com/cdn/shop/products/aurelien-voyager-loafer-chocolate-01.jpg",
        page_url="https://aurelien-online.com/products/voyager-loafer-chocolate",
        title="Aurélien Voyager Loafer Chocolate",
        description="Clean packshot",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [wrong_voyager, correct_yacht])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [wrong_voyager, correct_yacht])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "CHOCOLATE",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates
    assert candidates[0] == correct_yacht.url
    assert wrong_voyager.url not in candidates


def test_strict_search_rejects_url_that_names_wrong_product_family(monkeypatch):
    searcher = ImageSearcher()

    correct_loafer = SearchHit(
        url="https://aurelien-online.com/cdn/shop/products/aurelien-yacht-loafers-chocolate-01.jpg",
        page_url="https://aurelien-online.com/products/yacht-loafers-chocolate",
        title="Aurélien Yacht Loafers Chocolate",
        description="Clean packshot",
    )
    mislabeled_belt_image = SearchHit(
        url="https://aurelien-online.com/cdn/shop/products/aurelien-chocolate-suede-belt-01.jpg",
        page_url="https://aurelien-online.com/products/yacht-loafers-chocolate",
        title="Aurélien Yacht Loafers Chocolate",
        description="Search metadata says loafer, URL is actually a belt image",
    )

    _stub_all_search_methods(searcher, monkeypatch)
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [mislabeled_belt_image, correct_loafer])
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [mislabeled_belt_image, correct_loafer])

    candidates, _scores = searcher.search({
        "item_code": "YLWCHT-3800",
        "style_name": "Lady Chocolate Yacht Loafers",
        "color_name": "CHOCOLATE",
        "brand": "Aurélien",
        "item_group": "Loafers",
    })

    assert candidates
    assert candidates[0] == correct_loafer.url
    assert mislabeled_belt_image.url not in candidates


def test_color_equivalence_covers_obscure_colors():
    """Expanded equivalence classes — emerald should match green items,
    burgundy should match red items, etc."""
    searcher = ImageSearcher()
    ctx = searcher._build_item_context({
        "item_code": "X-1",
        "style_name": "Tee",
        "color_name": "Green",
        "brand": "Acme",
        "item_group": "T-Shirt",
    })
    emerald_hit = {"url": "https://x/emerald.jpg", "page_url": "", "title": "Emerald tee", "description": ""}
    sage_hit = {"url": "https://x/sage.jpg", "page_url": "", "title": "Sage tee", "description": ""}
    red_hit = {"url": "https://x/red.jpg", "page_url": "", "title": "Red tee", "description": ""}
    assert searcher._is_obvious_wrong_color_hit(emerald_hit, ctx) is False
    assert searcher._is_obvious_wrong_color_hit(sage_hit, ctx) is False
    assert searcher._is_obvious_wrong_color_hit(red_hit, ctx) is True


def test_url_lifestyle_path_marks_as_variant():
    """URL path patterns like /lookbook/ or /editorial/ mark the hit as
    a lifestyle shot even when the scraped title/description is generic."""
    searcher = ImageSearcher()
    lookbook = {
        "url": "https://brand.example/lookbook/fw26/model-wearing-loafer.jpg",
        "page_url": "https://brand.example/lookbook/fw26",
        "title": "Aurélien Lady Chocolate Yacht Loafer",
        "description": "",
    }
    packshot = {
        "url": "https://brand.example/products/loafer-packshot.jpg",
        "page_url": "https://brand.example/products/loafer",
        "title": "Aurélien Lady Chocolate Yacht Loafer",
        "description": "",
    }
    assert searcher._strict_hit_looks_like_variant(lookbook) is True
    assert searcher._strict_hit_looks_like_variant(packshot) is False


def test_high_numbered_carousel_image_marked_as_variant():
    """Images named with high numeric suffixes like -05.jpg are usually
    secondary carousel shots, not the primary packshot."""
    searcher = ImageSearcher()
    secondary = {
        "url": "https://brand.example/products/loafer-05.jpg",
        "page_url": "https://brand.example/products/loafer",
        "title": "Loafer",
        "description": "",
    }
    primary = {
        "url": "https://brand.example/products/loafer-01.jpg",
        "page_url": "https://brand.example/products/loafer",
        "title": "Loafer",
        "description": "",
    }
    assert searcher._strict_hit_looks_like_variant(secondary) is True
    assert searcher._strict_hit_looks_like_variant(primary) is False


def test_shopify_resized_carousel_index_marked_as_variant():
    searcher = ImageSearcher()
    secondary = {
        "url": "https://cdn.shopify.com/products/Aurelien_Yacht_Loafers_chocolate6_600x.jpg?v=1",
        "page_url": "",
        "title": "Aurélien Yacht Loafers Chocolate",
        "description": "",
    }
    primary = {
        "url": "https://cdn.shopify.com/products/Aurelien_Yacht_Loafers_chocolate1_600x.jpg?v=1",
        "page_url": "",
        "title": "Aurélien Yacht Loafers Chocolate",
        "description": "",
    }
    assert searcher._strict_hit_looks_like_variant(secondary) is True
    assert searcher._strict_hit_looks_like_variant(primary) is False

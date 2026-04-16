from __future__ import annotations

from app.core.searcher import ImageSearcher, SearchHit, split_and_normalize_domains


def test_split_and_normalize_domains_handles_commas_newlines_and_schemes():
    values = split_and_normalize_domains([
        "https://americanrag.ae, www.on.com/products",
        " stoneisland.com \n goldengoose.com ",
    ])

    assert values == [
        "americanrag.ae",
        "on.com",
        "stoneisland.com",
        "goldengoose.com",
    ]


def test_brand_config_matching_handles_suffix_variants_without_searching_other_brands(monkeypatch):
    searcher = ImageSearcher({
        "brand_site_urls": {
            "american rag": ["americanrag.ae"],
            "on": ["on.com"],
        }
    })

    called_domains: list[str] = []
    official = SearchHit(
        url="https://americanrag.ae/cdn/images/jacket-black.jpg",
        page_url="https://americanrag.ae/products/jacket-black",
        title="American Rag Cie black jacket",
        description="Official product image",
    )
    generic = SearchHit(
        url="https://example.com/jacket.jpg",
        page_url="https://example.com/jacket",
        title="Generic jacket",
        description="Marketplace listing",
    )

    monkeypatch.setattr(
        searcher,
        "_bing_site_search",
        lambda domain, query: called_domains.append(domain) or ([official] if domain == "americanrag.ae" else []),
    )
    monkeypatch.setattr(searcher, "_bing_search", lambda query: [generic])
    monkeypatch.setattr(searcher, "_bing_raw", lambda query: [])
    monkeypatch.setattr(searcher, "_google_images_scrape", lambda query: [])
    monkeypatch.setattr(searcher, "_duckduckgo_search", lambda query: [])
    monkeypatch.setattr(searcher, "_yahoo_images_scrape", lambda query: [])

    candidates, scores = searcher.search({
        "item_code": "AR-001",
        "style_name": "Black Jacket",
        "color_name": "Black",
        "brand": "American Rag Cie",
        "item_group": "Jacket",
    })

    assert "americanrag.ae" in called_domains
    assert "on.com" not in called_domains
    assert candidates[0] == official.url
    assert scores[official.url] > scores[generic.url]


def test_review_research_uses_session_priority_domains_and_brand_defaults(
    client,
    login_as,
    db_session,
    test_app,
    monkeypatch,
):
    user = login_as()
    models = test_app["models"]

    session = models.Session(
        user_id=user["id"],
        name="Jobber Stock.xlsx",
        source_type="excel_upload",
        status="reviewing",
        total_items=1,
    )
    session.config = {
        "extra_brand_urls": ["https://americanrag.ae"],
        "search_notes": "Use the official American Rag domain first.",
    }
    db_session.add(session)
    db_session.flush()

    item = models.UniqueItem(
        session_id=session.id,
        item_code="AR-001",
        color_code="BLK",
        color_name="Black",
        brand="American Rag Cie",
        style_name="Black Jacket",
        item_group="Jacket",
        review_status="approved",
    )
    config = models.BrandSearchConfig(
        user_id=user["id"],
        brand_name="American Rag",
        search_notes="Official product images live on americanrag.ae",
    )
    config.site_urls = ["americanrag.ae"]
    db_session.add_all([item, config])
    db_session.commit()
    db_session.refresh(item)

    captured: dict[str, list[str]] = {}

    def fake_search(self, item_dict, ai_queries=None):
        captured["extra_site_urls"] = list(self.extra_site_urls)
        captured["matched_brand_urls"] = self.matching_brand_site_urls(item_dict.get("brand", ""))
        return (["https://americanrag.ae/cdn/images/jacket-black.jpg"], {
            "https://americanrag.ae/cdn/images/jacket-black.jpg": 0.93,
        })

    monkeypatch.setattr(test_app["review_routes"], "ai_available", lambda: False)
    monkeypatch.setattr(test_app["review_routes"].ImageSearcher, "search", fake_search)

    resp = client.post(
        f"/review/{session.id}/re-search",
        json={"id": item.id, "instructions": ""},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert captured["extra_site_urls"] == ["americanrag.ae"]
    assert captured["matched_brand_urls"] == ["americanrag.ae"]
    assert data["approved_url"] == "https://americanrag.ae/cdn/images/jacket-black.jpg"


def test_search_page_prefills_matching_settings_defaults(client, login_as, db_session, test_app):
    user = login_as()
    models = test_app["models"]

    session = models.Session(
        user_id=user["id"],
        name="Jobber Stock.xlsx",
        source_type="excel_upload",
        status="mapping",
        total_items=1,
    )
    db_session.add(session)
    db_session.flush()

    item = models.UniqueItem(
        session_id=session.id,
        item_code="AR-001",
        brand="American Rag Cie",
        style_name="Black Jacket",
        color_name="Black",
        item_group="Jacket",
    )
    config = models.BrandSearchConfig(
        user_id=user["id"],
        brand_name="American Rag",
        search_notes="Search the official American Rag domain first.",
    )
    config.site_urls = ["americanrag.ae"]
    db_session.add_all([item, config])
    db_session.commit()

    resp = client.get(f"/search/{session.id}")

    assert resp.status_code == 200
    assert "americanrag.ae" in resp.text
    assert "Search the official American Rag domain first." in resp.text

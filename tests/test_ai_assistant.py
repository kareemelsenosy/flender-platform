from __future__ import annotations

from app.services import ai_service


def _create_session_with_item(test_app, db_session, user_id: int, *, brand: str = "American Rag") -> tuple[object, object]:
    models = test_app["models"]

    session = models.Session(
        user_id=user_id,
        name="Jobber Stock.xlsx",
        source_type="excel_upload",
        source_ref="Jobber Stock.xlsx",
        status="reviewing",
        total_items=1,
        searched_items=1,
    )
    session.config = {
        "search_mode": "web",
        "extra_brand_urls": ["https://americanrag.ae", "americanrag.com"],
        "search_notes": "Prefer official product pages and exact color matches.",
    }
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    item = models.UniqueItem(
        session_id=session.id,
        item_code="AR-123-BLK",
        brand=brand,
        style_name="Oversized Tee",
        color_name="Black",
        item_group="T-Shirt",
        barcode="1234567890",
        review_status="approved",
        auto_selected=True,
        approved_url="https://cdn.example.com/tee-black.jpg",
    )
    item.candidates = [
        "https://cdn.example.com/tee-black.jpg",
        "https://cdn.example.com/tee-white.jpg",
    ]
    item.scores = {
        "https://cdn.example.com/tee-black.jpg": 0.92,
        "https://cdn.example.com/tee-white.jpg": 0.18,
    }
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return session, item


def test_ai_assistant_requires_auth(client):
    response = client.post("/api/ai-assistant/chat", json={"message": "help"})
    assert response.status_code == 401


def test_ai_assistant_uses_owned_session_and_item_context(client, login_as, test_app, db_session, monkeypatch):
    user = login_as(username="owner", email="owner@example.com")
    models = test_app["models"]

    brand_cfg = models.BrandSearchConfig(user_id=user["id"], brand_name="American Rag")
    brand_cfg.site_urls = ["americanrag.ae", "americanrag.com"]
    brand_cfg.search_notes = "Search americanrag.ae first for UAE stock."
    db_session.add(brand_cfg)
    db_session.commit()

    session, item = _create_session_with_item(test_app, db_session, user["id"])

    captured: dict = {}

    def fake_chat(message, context):
        captured["message"] = message
        captured["context"] = context
        return {
            "reply": "Use americanrag.ae first and keep black in every query.",
            "suggestions": ["Re-run search with official UAE domain first"],
            "search_instructions": "Search americanrag.ae first with exact SKU and black color.",
            "priority_domains": ["americanrag.ae"],
        }

    monkeypatch.setattr(test_app["api_routes"], "ai_assistant_chat", fake_chat)

    response = client.post("/api/ai-assistant/chat", json={
        "message": "Improve this search",
        "page_path": f"/review/{session.id}",
        "session_id": session.id,
        "item_id": item.id,
        "page_context": {
            "can_apply_step3_suggestions": True,
            "helper_context": {
                "session_id": session.id,
                "item_id": item.id,
                "current_item": {"item_code": item.item_code},
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["priority_domains"] == ["americanrag.ae"]
    assert payload["search_instructions"].startswith("Search americanrag.ae first")

    context = captured["context"]
    assert captured["message"] == "Improve this search"
    assert context["assistant_capabilities"]["can_apply_step3_suggestions"] is True
    assert context["session"]["id"] == session.id
    assert context["session"]["search_config"]["priority_domains"] == ["americanrag.ae", "americanrag.com"]
    assert context["session"]["matched_brand_domains"]["American Rag"] == ["americanrag.ae", "americanrag.com"]
    assert context["item"]["id"] == item.id
    assert context["item"]["item_code"] == "AR-123-BLK"
    assert context["item"]["top_candidates"][0] == "https://cdn.example.com/tee-black.jpg"


def test_ai_assistant_does_not_leak_foreign_session(client, login_as, make_user, test_app, db_session, monkeypatch):
    owner = make_user(username="owner2", email="owner2@example.com")
    intruder = login_as(username="intruder", email="intruder@example.com")
    session, item = _create_session_with_item(test_app, db_session, owner["id"], brand="ON")

    captured: dict = {}

    def fake_chat(message, context):
        captured["context"] = context
        return {
            "reply": "No owned session context was available.",
            "suggestions": [],
            "search_instructions": "",
            "priority_domains": [],
        }

    monkeypatch.setattr(test_app["api_routes"], "ai_assistant_chat", fake_chat)

    response = client.post("/api/ai-assistant/chat", json={
        "message": "Tell me about this session",
        "page_path": f"/review/{session.id}",
        "session_id": session.id,
        "item_id": item.id,
        "page_context": {"helper_context": {"session_id": session.id, "item_id": item.id}},
    })

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["context"].get("session") is None
    assert captured["context"].get("item") is None


def test_ai_assistant_reports_provider_failure_when_configured(monkeypatch):
    monkeypatch.setattr(ai_service, "CLAUDE_API_KEY", "configured-key")
    monkeypatch.setattr(ai_service, "GEMINI_API_KEY", "")
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1400: None)
    ai_service._set_ai_last_error("claude", "text", "401 invalid x-api-key")

    try:
        result = ai_service.ai_assistant_chat("Help me", {"page_path": "/review/1"})
    finally:
        ai_service._clear_ai_last_error()

    assert "AI is configured" in result["reply"]
    assert "CLAUDE text" in result["reply"]
    assert "[redacted-key]" not in result["reply"]


def test_ai_status_endpoint_returns_runtime_status(client, login_as, test_app, monkeypatch):
    login_as(username="status-user", email="status@example.com")
    monkeypatch.setattr(
        test_app["api_routes"],
        "ai_runtime_status",
        lambda: {
            "configured": True,
            "providers": ["claude"],
            "last_error": {"provider": "claude", "operation": "text", "message": "timeout", "at": 123.0},
            "last_success_at": None,
        },
    )

    response = client.get("/api/ai-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["configured"] is True
    assert payload["providers"] == ["claude"]
    assert payload["last_error"]["message"] == "timeout"

from __future__ import annotations

import importlib


def test_ai_mapping_falls_back_to_heuristics_when_provider_is_unavailable(
    client,
    login_as,
    test_app,
    monkeypatch,
):
    login_as()

    csv_bytes = (
        b"Manufacturer Code,Web Description 2,Color,Size,Brand Name,WHS Price,RRP Price,FreeStock,Gender\n"
        b"SKU-001,Runner,Red,42,FLENDER,10,20,5,Men\n"
    )
    upload_resp = client.post(
        "/upload/file",
        files={"file": ("catalog.csv", csv_bytes, "text/csv")},
    )
    assert upload_resp.status_code == 200
    session_id = upload_resp.json()["session_id"]

    ai_service = importlib.import_module("app.services.ai_service")
    monkeypatch.setattr(ai_service, "ai_available", lambda: True)
    monkeypatch.setattr(ai_service, "ai_map_columns", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        ai_service,
        "ai_last_error_summary",
        lambda: "CLAUDE text: credit balance is too low",
    )

    response = client.post(f"/mapping/{session_id}/ai-suggest")
    assert response.status_code == 200
    data = response.json()
    assert data["fallback"] is True
    assert "smart header matching" in data["notes"]
    assert data["mappings"]["item_code"]["header"] == "Manufacturer Code"
    assert data["mappings"]["style_name"]["header"] == "Web Description 2"
    assert data["mappings"]["brand"]["header"] == "Brand Name"


def test_ai_mapping_falls_back_when_no_ai_key_is_configured(
    client,
    login_as,
    test_app,
    monkeypatch,
):
    login_as()

    csv_bytes = (
        b"Manufacturer Code,Web Description 2,Color,Size,Brand Name\n"
        b"SKU-001,Runner,Red,42,FLENDER\n"
    )
    upload_resp = client.post(
        "/upload/file",
        files={"file": ("catalog.csv", csv_bytes, "text/csv")},
    )
    assert upload_resp.status_code == 200
    session_id = upload_resp.json()["session_id"]

    ai_service = importlib.import_module("app.services.ai_service")
    monkeypatch.setattr(ai_service, "ai_available", lambda: False)

    response = client.post(f"/mapping/{session_id}/ai-suggest")
    assert response.status_code == 200
    data = response.json()
    assert data["fallback"] is True
    assert "No AI provider key configured" in data["notes"]
    assert data["mappings"]["item_code"]["header"] == "Manufacturer Code"

from __future__ import annotations

from app.services import ai_service


def test_ai_rank_urls_uses_vision_when_scores_are_close(monkeypatch):
    urls = [
        "https://cdn.example.com/shorts-tee.jpg",
        "https://cdn.example.com/shorts-correct.jpg",
        "https://cdn.example.com/shorts-wrong.jpg",
        "https://cdn.example.com/shorts-fourth.jpg",
    ]

    monkeypatch.setattr(ai_service, "_prepare_images_for_ai", lambda candidates: [
        {"index": 1, "url": candidates[0], "mime_type": "image/jpeg", "data": b"1"},
        {"index": 2, "url": candidates[1], "mime_type": "image/jpeg", "data": b"2"},
        {"index": 3, "url": candidates[2], "mime_type": "image/jpeg", "data": b"3"},
    ])
    monkeypatch.setattr(
        ai_service,
        "_call_ai_vision",
        lambda prompt, images, max_tokens=1024: '{"ranked":[2,1],"discarded":[3],"notes":"candidate 2 is the right shorts"}',
    )
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1024: None)

    ranked, discarded = ai_service.ai_rank_urls(
        urls,
        {
            "item_code": "WS25-TR-280-02",
            "style_name": "Casa Blanca Graffiti Skater Shorts",
            "color_name": "Blue",
            "item_group": "Shorts",
        },
        "Casablanca",
        scores={
            urls[0]: 0.58,
            urls[1]: 0.54,
            urls[2]: 0.51,
            urls[3]: 0.49,
        },
    )

    assert ranked == [urls[1], urls[0], urls[3]]
    assert discarded == {urls[2]}


def test_ai_rank_urls_skips_vision_when_metadata_already_has_clear_winner(monkeypatch):
    urls = [
        "https://cdn.example.com/official-shoe.jpg",
        "https://cdn.example.com/generic-shoe.jpg",
    ]
    vision_called = {"value": False}

    def fake_vision(prompt, images, max_tokens=1024):
        vision_called["value"] = True
        return '{"ranked":[2,1]}'

    monkeypatch.setattr(ai_service, "_call_ai_vision", fake_vision)
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1024: "[2,1]")

    ranked, discarded = ai_service.ai_rank_urls(
        urls,
        {
            "item_code": "3WE30133563-W",
            "style_name": "Cloudvista 2 W",
            "color_name": "Pelican / Ghost / Yellow",
            "item_group": "Footwear",
        },
        "ON",
        scores={
            urls[0]: 0.99,
            urls[1]: 0.36,
        },
    )

    assert vision_called["value"] is False
    assert ranked == [urls[1], urls[0]]
    assert discarded == set()


def test_ai_rank_urls_can_force_vision_for_manual_research(monkeypatch):
    urls = [
        "https://cdn.example.com/cloudsurfer-black-1.jpg",
        "https://cdn.example.com/cloudsurfer-black-2.jpg",
    ]

    monkeypatch.setattr(ai_service, "_prepare_images_for_ai", lambda candidates: [
        {"index": 1, "url": candidates[0], "mime_type": "image/jpeg", "data": b"1"},
        {"index": 2, "url": candidates[1], "mime_type": "image/jpeg", "data": b"2"},
    ])
    monkeypatch.setattr(
        ai_service,
        "_call_ai_vision",
        lambda prompt, images, max_tokens=1024: '{"ranked":[2,1],"discarded":[],"notes":"candidate 2 is cleaner"}',
    )
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1024: None)

    ranked, discarded = ai_service.ai_rank_urls(
        urls,
        {
            "item_code": "3WE30543714-W-8",
            "style_name": "Cloudsurfer Next W",
            "color_name": "Black",
            "item_group": "Footwear",
        },
        "ON",
        scores={
            urls[0]: 0.95,
            urls[1]: 0.41,
        },
        prefer_vision=True,
    )

    assert ranked == [urls[1], urls[0]]
    assert discarded == set()


def test_ai_rank_urls_uses_vision_for_detail_or_lifestyle_ambiguity(monkeypatch):
    urls = [
        "https://aurelien.com/images/yacht-loafers-detail-closeup.jpg",
        "https://aurelien.com/images/yacht-loafers-packshot.jpg",
        "https://aurelien.com/images/yacht-loafers-on-foot.jpg",
    ]
    vision_called = {"value": False}

    monkeypatch.setattr(ai_service, "_prepare_images_for_ai", lambda candidates: [
        {"index": 1, "url": candidates[0], "mime_type": "image/jpeg", "data": b"1"},
        {"index": 2, "url": candidates[1], "mime_type": "image/jpeg", "data": b"2"},
        {"index": 3, "url": candidates[2], "mime_type": "image/jpeg", "data": b"3"},
    ])

    def fake_vision(prompt, images, max_tokens=1024):
        vision_called["value"] = True
        return '{"ranked":[2,1],"discarded":[3],"notes":"candidate 2 is the clean packshot"}'

    monkeypatch.setattr(ai_service, "_call_ai_vision", fake_vision)
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1024: None)

    ranked, discarded = ai_service.ai_rank_urls(
        urls,
        {
            "item_code": "YLWCHT-3800",
            "style_name": "Lady Chocolate Yacht Loafers",
            "color_name": "Chocolate",
            "item_group": "Loafers",
        },
        "Aurélien",
        scores={
            urls[0]: 0.94,
            urls[1]: 0.83,
            urls[2]: 0.76,
        },
    )

    assert vision_called["value"] is True
    assert ranked == [urls[1], urls[0]]
    assert discarded == {urls[2]}


def test_ai_rank_urls_drops_unwanted_footwear_presentations_after_vision(monkeypatch):
    urls = [
        "https://aurelien.com/images/city-loafer-packshot.jpg",
        "https://aurelien.com/images/city-loafer-detail-closeup.jpg",
        "https://aurelien.com/images/city-loafer-outsole.jpg",
        "https://aurelien.com/images/city-loafer-on-foot.jpg",
    ]

    monkeypatch.setattr(ai_service, "_prepare_images_for_ai", lambda candidates: [
        {"index": 1, "url": candidates[0], "mime_type": "image/jpeg", "data": b"1"},
        {"index": 2, "url": candidates[1], "mime_type": "image/jpeg", "data": b"2"},
        {"index": 3, "url": candidates[2], "mime_type": "image/jpeg", "data": b"3"},
        {"index": 4, "url": candidates[3], "mime_type": "image/jpeg", "data": b"4"},
    ])
    monkeypatch.setattr(
        ai_service,
        "_call_ai_vision",
        lambda prompt, images, max_tokens=1024: '{"ranked":[1],"discarded":[2,3,4],"notes":"candidate 1 is the correct clean packshot"}',
    )
    monkeypatch.setattr(ai_service, "_call_ai", lambda prompt, max_tokens=1024: None)

    ranked, discarded = ai_service.ai_rank_urls(
        urls,
        {
            "item_code": "CITYLOAFER2LGRY-4300",
            "style_name": "City Loafer M",
            "color_name": "LIGHT GREY",
            "item_group": "Footwear",
        },
        "Aurélien",
        scores={
            urls[0]: 0.78,
            urls[1]: 0.75,
            urls[2]: 0.72,
            urls[3]: 0.7,
        },
        prefer_vision=True,
    )

    assert ranked == [urls[0]]
    assert discarded == {urls[1], urls[2], urls[3]}

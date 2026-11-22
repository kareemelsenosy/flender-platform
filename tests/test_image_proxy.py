"""The review page's image proxy fetches only public images."""
import importlib

import pytest


@pytest.fixture
def review_routes(test_app):
    return importlib.import_module("app.routers.review_routes")


class _Resp:
    def __init__(self, status, headers, content=b""):
        self.status_code, self.headers, self.content = status, headers, content

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)


def test_a_redirect_to_an_internal_address_is_refused(review_routes, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(302, {"location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(review_routes._UPSTREAM_HTTP, "get", fake_get)
    monkeypatch.setattr(review_routes, "_host_is_internal",
                        lambda url: "169.254" in url)
    status, _h, body = review_routes._fetch_remote_image("https://cdn.example.com/a.jpg")
    assert status == 403 and body == b""
    assert calls == ["https://cdn.example.com/a.jpg"]


def test_only_images_are_passed_on(client, login_as, review_routes, monkeypatch):
    login_as()
    monkeypatch.setattr(review_routes, "_host_is_internal", lambda url: False)
    for ct, body, ok in (("application/json", b'{"secret": 1}', False),
                         ("image/svg+xml", b"<svg onload=alert(1)>", False),
                         ("text/html", b"<html>", False),
                         ("image/jpeg", b"\xff\xd8\xff\xe0data", True)):
        review_routes._image_cache.clear()
        review_routes._fail_cache.clear()
        monkeypatch.setattr(review_routes, "_fetch_remote_image",
                            lambda url, ct=ct, body=body: (200, {"content-type": ct}, body))
        resp = client.get("/api/image/proxy", params={"url": f"https://x.example/{ct}"})
        assert (resp.status_code == 200) is ok, ct

"""Authenticated image search for Carhartt-WIP B2B catalog.

The Carhartt-WIP B2B store at https://b2b.carhartt-wip.com/reorder_en/ is a
Magento-style site that requires a login before product pages or search results
are accessible. This module logs in once per process (lazy + cached) and exposes
a single function: ``find_images_for_sku(sku)`` returning candidate image URLs.

Wired into the broader search pipeline in ``app/core/searcher.py`` — for items
whose brand is Carhartt-WIP, we *always* try the B2B search first and fold the
results into the candidate pool with a strong score boost (they're guaranteed
catalog packshots).

Credentials come from environment variables so they never live in the repo:

    CARHARTT_B2B_USER   — login email
    CARHARTT_B2B_PASS   — login password

If either is missing, the module no-ops and returns ``[]`` so the rest of the
search pipeline carries on normally.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_BASE = "https://b2b.carhartt-wip.com"
_LOCALE_PATH = "/reorder_en"
_LOGIN_URL = f"{_BASE}{_LOCALE_PATH}/customer/account/login/"
_LOGIN_POST_URL = f"{_BASE}{_LOCALE_PATH}/customer/account/loginPost/"
_SEARCH_URL = f"{_BASE}{_LOCALE_PATH}/catalogsearch/result/"
_ACCOUNT_URL = f"{_BASE}{_LOCALE_PATH}/customer/account/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_FORM_KEY_RE = re.compile(
    r'name="form_key"[^>]*value="([^"]+)"', re.IGNORECASE
)

# Magento sometimes flips the image URL to a placeholder when an image is being
# lazy-loaded; the real URL is in data-src/data-original.
_IMG_ATTRS = ("src", "data-src", "data-original", "data-lazy", "data-srcset")


def _is_image_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower().split("?", 1)[0]
    return u.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


def _is_carhartt_product_image(url: str) -> bool:
    """Filter out icons, logos, and UI assets from the catalog HTML."""
    if not _is_image_url(url):
        return False
    u = url.lower()
    # Magento product media path
    if "/catalog/product/" in u:
        return True
    # CDN-style product paths
    if "carhartt" in u and "/media/" in u and "logo" not in u:
        return True
    return False


class _ImgCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []
        self.seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        for attr_name, attr_value in attrs:
            if attr_value and attr_name.lower() in _IMG_ATTRS:
                # data-srcset may contain "url 1x, url2 2x" — take the first.
                value = attr_value.split(",", 1)[0].strip().split(" ", 1)[0]
                if value and value not in self.seen:
                    self.seen.add(value)
                    self.images.append(value)


class CarharttB2BClient:
    """Thread-safe lazy-login client. Falls back to no-op if creds missing."""

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self._lock = threading.Lock()
        self._login_attempted = False
        self._login_ok = False

    def _have_credentials(self) -> bool:
        return bool(os.getenv("CARHARTT_B2B_USER") and os.getenv("CARHARTT_B2B_PASS"))

    def _ensure_logged_in(self) -> bool:
        if self._login_ok:
            return True
        if not self._have_credentials():
            return False
        with self._lock:
            if self._login_ok:
                return True
            if self._login_attempted and not self._login_ok:
                # Don't hammer the site after a failed attempt — reset on next
                # process restart only.
                return False
            self._login_attempted = True

            session = requests.Session()
            session.headers.update({
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })

            try:
                # 1. Fetch the login form to seed cookies and extract form_key.
                resp = session.get(_LOGIN_URL, timeout=15, allow_redirects=True)
                if resp.status_code != 200:
                    logger.warning("Carhartt B2B login page returned %s", resp.status_code)
                    return False

                form_key_match = _FORM_KEY_RE.search(resp.text)
                form_key = form_key_match.group(1) if form_key_match else ""

                # 2. POST credentials. Magento expects login[username] / login[password].
                payload = {
                    "form_key": form_key,
                    "login[username]": os.environ["CARHARTT_B2B_USER"],
                    "login[password]": os.environ["CARHARTT_B2B_PASS"],
                    "send": "",
                }
                login_resp = session.post(
                    _LOGIN_POST_URL,
                    data=payload,
                    timeout=20,
                    allow_redirects=True,
                    headers={"Referer": _LOGIN_URL},
                )

                # 3. Verify by hitting the account page — it's only reachable
                #    when authenticated; an unauthenticated request bounces
                #    back to the login form.
                account = session.get(_ACCOUNT_URL, timeout=15, allow_redirects=False)
                if account.status_code == 200 and "logout" in account.text.lower():
                    self._session = session
                    self._login_ok = True
                    logger.info("Carhartt B2B: login successful")
                    return True
                logger.warning(
                    "Carhartt B2B: login verification failed "
                    "(login_status=%s, account_status=%s)",
                    login_resp.status_code, account.status_code,
                )
                return False
            except requests.RequestException as exc:
                logger.warning("Carhartt B2B login error: %s", exc)
                return False

    def search_by_sku(self, sku: str) -> list[str]:
        """Return product image URLs for an SKU. Empty list on any failure."""
        if not sku or not self._ensure_logged_in() or self._session is None:
            return []
        sku = sku.strip()
        try:
            resp = self._session.get(
                _SEARCH_URL,
                params={"q": sku},
                timeout=15,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning("Carhartt B2B search error for %r: %s", sku, exc)
            return []
        if resp.status_code != 200:
            return []

        collector = _ImgCollector()
        try:
            collector.feed(resp.text)
        except Exception:
            return []

        # Resolve relative URLs and filter to product images.
        results: list[str] = []
        for raw in collector.images:
            absolute = urljoin(resp.url, raw)
            if not _is_carhartt_product_image(absolute):
                continue
            # Magento often serves a small thumbnail and a high-res via
            # different `/cache/<hash>/` paths. Prefer non-cache variants but
            # keep both — the downstream image proxy will pick the best.
            if absolute not in results:
                results.append(absolute)

        if results:
            logger.info(
                "Carhartt B2B: found %d image(s) for SKU %r", len(results), sku
            )
        return results


# Module singleton — created lazily, login happens on first search call.
_client = CarharttB2BClient()


def is_enabled() -> bool:
    """True when both credentials are configured (regardless of login state)."""
    return bool(os.getenv("CARHARTT_B2B_USER") and os.getenv("CARHARTT_B2B_PASS"))


def find_images_for_sku(sku: str) -> list[str]:
    """Public entrypoint used by the search pipeline."""
    if not is_enabled():
        return []
    return _client.search_by_sku(sku)


_CARHARTT_BRAND_TOKENS = {
    "carhartt wip", "carhartt-wip", "carhartt", "carharttwip", "carhartt_wip",
}


def is_carhartt_brand(brand: str | None) -> bool:
    if not brand:
        return False
    b = re.sub(r"\s+", " ", brand.strip().lower())
    return b in _CARHARTT_BRAND_TOKENS or b.startswith("carhartt")

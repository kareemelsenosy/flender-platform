"""
Core searcher — Brand-agnostic image search with confidence scoring.
Refactored from images-finder/searcher.py into a class (no globals).
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup


def _make_http_session() -> requests.Session:
    """Shared session with connection pooling for all search requests."""
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=40, pool_maxsize=80)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_HTTP = _make_http_session()

MAX_CANDIDATES = 5
REQUEST_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
}

BRAND_DOMAINS: dict[str, str] = {
    "patagonia": "patagonia.com",
    "stone island": "stoneisland.com",
    "golden goose": "goldengoose.com",
    "the north face": "thenorthface.com",
    "north face": "thenorthface.com",
    "arc'teryx": "arcteryx.com",
    "arcteryx": "arcteryx.com",
    "moncler": "moncler.com",
    "canada goose": "canadagoose.com",
    "dsquared2": "dsquared2.com",
    "palm angels": "palmangels.com",
    "ami paris": "amiparis.com",
    "off-white": "off---white.com",
    "burberry": "burberry.com",
    "balenciaga": "balenciaga.com",
    "gucci": "gucci.com",
    "prada": "prada.com",
    "valentino": "valentino.com",
    "versace": "versace.com",
    "tommy hilfiger": "tommy.com",
    "ralph lauren": "ralphlauren.com",
    "boss": "hugoboss.com",
    "hugo boss": "hugoboss.com",
    "lacoste": "lacoste.com",
    "nike": "nike.com",
    "adidas": "adidas.com",
    "new balance": "newbalance.com",
    "converse": "converse.com",
    "vans": "vans.com",
    "timberland": "timberland.com",
    "levi's": "levi.com",
    "levis": "levi.com",
    "calvin klein": "calvinklein.com",
    "diesel": "diesel.com",
    "acne studios": "acnestudios.com",
    "kenzo": "kenzo.com",
    "givenchy": "givenchy.com",
    "alexander mcqueen": "alexandermcqueen.com",
    "bottega veneta": "bottegaveneta.com",
    "saint laurent": "ysl.com",
    "fendi": "fendi.com",
    "herno": "herno.com",
    "woolrich": "woolrich.com",
    "jason markk": "jasonmarkk.com",
    "huf": "hufworldwide.com",
    "another cotton lab": "anothercottonlab.com",
    "thisisneverthat": "thisisneverthat.com",
}

_CDN_PATTERNS = [
    "demandware", "cloudfront.net", "akamaized.net", "fastly.net",
    "imgix.net", "cloudinary.com", "shopify.com", "scene7.com",
]
_PRODUCT_PATHS = ["/product/", "/products/", "/p/", "/item/", "/images/product", "/catalog/product"]


class ImageSearcher:
    """Search for product images across multiple sources."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.max_candidates = int(self.config.get("max_candidates", MAX_CANDIDATES))
        self.google_api_key = self.config.get("google_api_key", "")
        self.google_cse_id = self.config.get("google_cse_id", "")
        self.brand_site_urls: dict[str, list[str]] = self.config.get("brand_site_urls", {})

    def search(self, item: dict, ai_queries: list[str] | None = None) -> tuple[list[str], dict[str, float]]:
        """
        Search for product images — all sources fire in parallel (no sequential waits).
        Returns (candidates, scores).
        """
        item_code  = str(item.get("item_code")  or "").strip()
        color_code = str(item.get("color_code") or "").strip() or None
        color_name = str(item.get("color_name") or "").strip() or None
        style_name = str(item.get("style_name") or "").strip() or None
        brand      = str(item.get("brand")      or "").strip()

        source_results: dict[str, list[str]] = {}
        lock = threading.Lock()

        # Resolve brand site domain once (before spawning threads)
        site_urls = self.brand_site_urls.get(brand.lower(), [])
        if not site_urls:
            domain = BRAND_DOMAINS.get(brand.lower().strip())
            if domain:
                site_urls = [domain]

        # Build task list — all run simultaneously
        tasks: dict[str, Any] = {}

        if ai_queries:
            for i, query in enumerate(ai_queries[:3]):
                tasks[f"ai_{i}"] = lambda q=query: self._bing_raw(q)

        if site_urls:
            tasks["brand_site"] = lambda: self._bing_site_search(
                site_urls[0], item_code, color_name, style_name
            )

        if self.google_api_key and self.google_cse_id:
            tasks["google"] = lambda: self._google_search(
                brand, item_code, color_name, style_name
            )

        tasks["bing"] = lambda: self._bing_search(brand, item_code, color_name, style_name)
        tasks["ddg"]  = lambda: self._duckduckgo_search(brand, item_code, color_name, style_name)

        # Fire all sources at the same time
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {pool.submit(fn): key for key, fn in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    found = future.result()
                    if found:
                        with lock:
                            source_results[key] = found
                except Exception:
                    pass

        all_urls = self._dedupe([u for v in source_results.values() for u in v])
        url_sources = {
            url: sum(1 for v in source_results.values() if url in v)
            for url in all_urls
        }
        raw_scores = {
            url: self._score_url(url, item_code, color_code, brand, i, url_sources[url])
            for i, url in enumerate(all_urls)
        }
        candidates = sorted(all_urls, key=lambda u: raw_scores[u], reverse=True)[:self.max_candidates]
        scores = {u: round(raw_scores[u], 2) for u in candidates}
        return candidates, scores

    def _score_url(self, url: str, item_code: str, color_code: str | None,
                   brand: str, position: int = 0, source_count: int = 1) -> float:
        score = 0.0
        lower = url.lower()
        code_clean = re.sub(r"[-_ ]", "", item_code).lower()
        url_clean = re.sub(r"[-_ ]", "", lower)

        if item_code.lower() in lower or (len(code_clean) > 3 and code_clean in url_clean):
            score += 0.40
        else:
            tokens = [t for t in re.split(r"[-_ ]+", item_code.lower()) if len(t) >= 4]
            if tokens:
                matched = sum(1 for t in tokens if t in url_clean)
                if matched == len(tokens):
                    score += 0.35
                elif matched >= 2:
                    score += 0.20
                elif matched == 1:
                    score += 0.08

        if color_code and color_code.lower() in lower:
            score += 0.20

        domain = BRAND_DOMAINS.get(brand.lower().strip())
        if domain and domain in lower:
            score += 0.25
        else:
            brand_slug = re.sub(r"[^\w]", "", brand).lower()
            if brand_slug and len(brand_slug) > 3 and brand_slug in lower:
                score += 0.10

        if any(p in lower for p in _CDN_PATTERNS) or any(p in lower for p in _PRODUCT_PATHS):
            score += 0.10

        score += max(0.0, 0.06 - (position + 1) * 0.01)

        if source_count >= 2:
            score += 0.30

        return min(score, 1.0)

    def _bing_site_search(self, domain: str, item_code: str,
                          color_name: str | None, style_name: str | None) -> list[str]:
        """Search Bing limited to a specific domain."""
        parts = [f"site:{domain}", item_code]
        if style_name:
            parts.append(style_name)
        if color_name:
            parts.append(color_name)
        query = " ".join(p for p in parts if p)
        return self._bing_raw(query)

    def _bing_search(self, brand: str, item_code: str,
                     color_name: str | None, style_name: str | None) -> list[str]:
        parts = [brand, item_code]
        if style_name:
            parts.append(style_name)
        if color_name:
            parts.append(color_name)
        query = " ".join(p for p in parts if p)
        return self._bing_raw(query)

    def _bing_raw(self, query: str) -> list[str]:
        bing_headers = {
            "User-Agent": _HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bing.com/",
        }
        url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&form=HDRSC2&first=1"
        resp = self._get(url, headers=bing_headers)
        if resp is None:
            return []

        urls: list[str] = []
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all("a", class_="iusc"):
            m = tag.get("m", "")
            if not m:
                continue
            try:
                obj = json.loads(m)
                if "murl" in obj:
                    urls.append(obj["murl"])
            except Exception:
                pass

        imgurl_matches = re.findall(r'imgurl=(https?://[^&"\']+)', resp.text)
        urls.extend([urllib.parse.unquote(u) for u in imgurl_matches])
        return self._dedupe(urls)

    def _google_search(self, brand: str, item_code: str,
                       color_name: str | None, style_name: str | None) -> list[str]:
        if not self.google_api_key or not self.google_cse_id:
            return []
        parts = [brand, item_code]
        if color_name:
            parts.append(color_name)
        if style_name:
            parts.append(style_name)
        query = " ".join(p for p in parts if p)

        resp = self._get("https://www.googleapis.com/customsearch/v1", params={
            "key": self.google_api_key, "cx": self.google_cse_id, "q": query,
            "searchType": "image", "num": self.max_candidates,
            "imgType": "photo", "safe": "active",
        })
        if resp is None:
            return []
        try:
            data = resp.json()
            return [it["link"] for it in data.get("items", []) if "link" in it]
        except Exception:
            return []

    def _duckduckgo_search(self, brand: str, item_code: str,
                           color_name: str | None, style_name: str | None) -> list[str]:
        parts = [brand, item_code]
        if style_name:
            parts.append(style_name)
        if color_name:
            parts.append(color_name)
        query = " ".join(p for p in parts if p)

        headers = {"User-Agent": _HEADERS["User-Agent"], "Accept-Language": "en-US,en;q=0.9"}
        try:
            resp = _HTTP.get("https://duckduckgo.com/",
                             params={"q": query, "iax": "images", "ia": "images"},
                             headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception:
            return []

        vqd = re.search(r"vqd=([\d-]+)", resp.text)
        if not vqd:
            return re.findall(r'"image"\s*:\s*"(https://[^"]+?)"', resp.text)[:self.max_candidates]

        try:
            img_resp = _HTTP.get("https://duckduckgo.com/i.js",
                                 params={"q": query, "o": "json", "vqd": vqd.group(1), "f": ",,,", "p": "1"},
                                 headers={**headers, "Referer": "https://duckduckgo.com/"},
                                 timeout=REQUEST_TIMEOUT)
            data = img_resp.json()
            return [r["image"] for r in data.get("results", [])[:self.max_candidates] if "image" in r]
        except Exception:
            return []

    def _get(self, url: str, params: dict | None = None,
             headers: dict | None = None, retries: int = 2) -> requests.Response | None:
        h = headers or _HEADERS
        for attempt in range(retries + 1):
            try:
                resp = _HTTP.get(url, params=params, headers=h,
                                 timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if resp.status_code == 200:
                    return resp
                # back-off only on rate-limit; don't block other threads with sleep
                if resp.status_code == 429 and attempt < retries:
                    import time; time.sleep(1.5 ** attempt)
            except requests.RequestException:
                pass
        return None

    @staticmethod
    def _dedupe(urls: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

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
    adapter = HTTPAdapter(
        pool_connections=100,
        pool_maxsize=200,
        max_retries=2,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_HTTP = _make_http_session()

MAX_CANDIDATES = 10
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
    # Sportswear / Footwear
    "hoka": "hoka.com",
    "hoka one one": "hoka.com",
    "asics": "asics.com",
    "puma": "puma.com",
    "reebok": "reebok.com",
    "saucony": "saucony.com",
    "on": "on.com",
    "on running": "on.com",
    "salomon": "salomon.com",
    "columbia": "columbia.com",
    "under armour": "underarmour.com",
    "fila": "fila.com",
    "mizuno": "mizuno.com",
    "brooks": "brooksrunning.com",
    # Streetwear
    "carhartt wip": "carhartt-wip.com",
    "carhartt": "carhartt-wip.com",
    "karl kani": "karlkani.com",
    "stussy": "stussy.com",
    "stüssy": "stussy.com",
    "supreme": "supremenewyork.com",
    "bape": "bape.com",
    "a bathing ape": "bape.com",
    "obey": "obeyclothing.com",
    "dickies": "dickies.com",
    # Casual / Fashion
    "tommy jeans": "tommy.com",
    "polo ralph lauren": "ralphlauren.com",
    "gant": "gant.com",
    "napapijri": "napapijri.com",
    "cp company": "cpcompany.com",
    "c.p. company": "cpcompany.com",
    "barbour": "barbour.com",
    "fred perry": "fredperry.com",
    "superdry": "superdry.com",
    "jack wolfskin": "jack-wolfskin.com",
    "mammut": "mammut.com",
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
        # Extra priority domains that apply to every item in this session
        self.extra_site_urls: list[str] = self.config.get("extra_site_urls", [])

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

        # Extra priority domains (from step-3 form) — searched first, before brand domains
        for ei, extra_domain in enumerate(self.extra_site_urls[:3]):
            d = extra_domain.strip().lstrip("https://").lstrip("http://").rstrip("/")
            if d:
                tasks[f"extra_{ei}"] = lambda dom=d: self._bing_site_search(
                    dom, item_code, color_name, style_name
                )

        if site_urls:
            tasks["brand_site"] = lambda: self._bing_site_search(
                site_urls[0], item_code, color_name, style_name
            )
            # Also try a second brand-site search focusing on style/product name
            if style_name and style_name.lower() != item_code.lower():
                tasks["brand_site_style"] = lambda: self._bing_site_search(
                    site_urls[0], style_name, color_name, None
                )

        if self.google_api_key and self.google_cse_id:
            tasks["google"] = lambda: self._google_search(
                brand, item_code, color_name, style_name
            )

        tasks["bing"] = lambda: self._bing_search(brand, item_code, color_name, style_name)
        tasks["bing_code_only"] = lambda: self._bing_raw(f"{item_code} {brand} product image") if item_code else []
        tasks["google_scrape"] = lambda: self._google_images_scrape(brand, item_code, color_name, style_name)
        tasks["ddg"]  = lambda: self._duckduckgo_search(brand, item_code, color_name, style_name)
        tasks["yahoo"] = lambda: self._yahoo_images_scrape(brand, item_code, color_name, style_name)

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

        # Color name matching — boost if URL contains color words, penalize obvious mismatches
        if color_name:
            color_words = [w.lower() for w in re.split(r"[\s+\-/]", color_name) if len(w) >= 3]
            color_clean = re.sub(r"[\s+\-/]", "", color_name.lower())
            if color_clean and color_clean in url_clean:
                score += 0.15
            elif any(w in url_clean for w in color_words):
                score += 0.08

        # Boost for user-specified priority domains (highest priority)
        for extra_d in self.extra_site_urls:
            clean_d = extra_d.strip().lstrip("https://").lstrip("http://").rstrip("/")
            if clean_d and clean_d in lower:
                score += 0.45
                break

        domain = BRAND_DOMAINS.get(brand.lower().strip())
        if domain and domain in lower:
            score += 0.35  # strong boost for official brand domain
        else:
            brand_slug = re.sub(r"[^\w]", "", brand).lower()
            if brand_slug and len(brand_slug) > 3 and brand_slug in lower:
                score += 0.15
            # Penalize URLs from wrong brand domains
            for other_brand, other_domain in BRAND_DOMAINS.items():
                if other_brand != brand.lower().strip() and other_domain in lower:
                    score -= 0.30  # penalize images from wrong brand's site
                    break

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

        import html as _html
        urls: list[str] = []
        raw = resp.text
        # Decode HTML entities — Bing encodes JSON as &quot;murl&quot;:&quot;https://...&quot;
        decoded = _html.unescape(raw)

        soup = BeautifulSoup(raw, "html.parser")
        # Method 1: iusc anchor tags with JSON metadata
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

        # Method 2: murl from decoded JSON blobs (handles &quot; encoding)
        for text in (raw, decoded):
            murl_matches = re.findall(r'"murl"\s*:\s*"(https?://[^"\\]+)"', text)
            urls.extend(murl_matches)

        # Method 3: imgurl= in raw HTML
        imgurl_matches = re.findall(r'imgurl=(https?://[^&"\'\\s]+)', decoded)
        urls.extend([urllib.parse.unquote(u) for u in imgurl_matches])

        # Method 4: contentUrl / mediaUrl patterns
        for pat in [r'"contentUrl"\s*:\s*"(https?://[^"\\]+)"',
                    r'"mediaUrl"\s*:\s*"(https?://[^"\\]+)"']:
            urls.extend(re.findall(pat, decoded))

        # Method 5: any https URL ending in image extension
        if len(urls) < 3:
            img_urls = re.findall(r'https?://[^\s"\'<>&]{15,}\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>&]*)?', decoded)
            urls.extend([u for u in img_urls if "bing.com" not in u and "microsoft.com" not in u])

        # Method 6: turl (thumbnail) as last resort
        if len(urls) < 3:
            turl_matches = re.findall(r'"turl"\s*:\s*"(https?://[^"\\]+)"', decoded)
            urls.extend(turl_matches)

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

    def _google_images_scrape(self, brand: str, item_code: str,
                              color_name: str | None, style_name: str | None) -> list[str]:
        """Scrape Google Images search results (no API key needed)."""
        parts = [brand, item_code]
        if style_name:
            parts.append(style_name)
        if color_name:
            parts.append(color_name)
        query = " ".join(p for p in parts if p)

        google_headers = {
            "User-Agent": _HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&tbm=isch&hl=en"
        resp = self._get(url, headers=google_headers)
        if resp is None:
            return []

        urls: list[str] = []
        # Google embeds image URLs in various JSON-like structures in the page
        # Pattern 1: ["url","https://..."] pairs
        img_matches = re.findall(r'\["(https?://[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)",[0-9]+,[0-9]+\]', resp.text)
        urls.extend(img_matches)

        # Pattern 2: ou:"url" (original URL in metadata)
        ou_matches = re.findall(r'"ou"\s*:\s*"(https?://[^"]+)"', resp.text)
        urls.extend(ou_matches)

        # Pattern 3: data-src attributes
        soup = BeautifulSoup(resp.text, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("data-iurl") or ""
            if src.startswith("http") and not "gstatic.com" in src:
                urls.append(src)

        # Filter out Google/tracking URLs
        filtered = [u for u in urls if "google.com" not in u and "gstatic.com" not in u
                    and "googleapis.com" not in u and len(u) > 20]

        # If few results, try a tighter code-only query as fallback
        if len(filtered) < 3 and item_code:
            fallback_query = f"{item_code} {brand}"
            fallback_url = f"https://www.google.com/search?q={urllib.parse.quote(fallback_query)}&tbm=isch&hl=en"
            resp2 = self._get(fallback_url, headers=google_headers)
            if resp2:
                ou2 = re.findall(r'"ou"\s*:\s*"(https?://[^"]+)"', resp2.text)
                extra = [u for u in ou2 if "google.com" not in u and "gstatic.com" not in u and len(u) > 20]
                filtered = self._dedupe(filtered + extra)

        return self._dedupe(filtered)[:self.max_candidates]

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

    def _yahoo_images_scrape(self, brand: str, item_code: str,
                             color_name: str | None, style_name: str | None) -> list[str]:
        """Scrape Yahoo Image Search results (no API key needed)."""
        parts = [brand, item_code]
        if style_name:
            parts.append(style_name)
        if color_name:
            parts.append(color_name)
        query = " ".join(p for p in parts if p)

        headers = {
            "User-Agent": _HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        url = f"https://images.search.yahoo.com/search/images?p={urllib.parse.quote(query)}&fr=yfp-t&fr2=sb-top-images.search.yahoo.com&tab=organic&ri=0"
        resp = self._get(url, headers=headers)
        if resp is None:
            return []

        urls: list[str] = []
        # Yahoo embeds image URLs in data-src and mosrc attributes
        soup = BeautifulSoup(resp.text, "html.parser")
        for img in soup.find_all("img"):
            for attr in ("data-src", "src", "data-original"):
                src = img.get(attr, "")
                if src.startswith("http") and "yimg.com" not in src and "yahoo.com" not in src:
                    urls.append(src)
                    break

        # Also extract from JSON-like blobs
        raw_urls = re.findall(r'"imgurl"\s*:\s*"(https?://[^"]+)"', resp.text)
        urls.extend([urllib.parse.unquote(u) for u in raw_urls])

        raw_urls2 = re.findall(r'src="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', resp.text)
        for u in raw_urls2:
            if "yimg.com" not in u and "yahoo.com" not in u:
                urls.append(u)

        return self._dedupe(urls)[:self.max_candidates]

    def _get(self, url: str, params: dict | None = None,
             headers: dict | None = None, retries: int = 2) -> requests.Response | None:
        h = headers or _HEADERS
        for attempt in range(retries + 1):
            try:
                resp = _HTTP.get(url, params=params, headers=h,
                                 timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if resp.status_code == 200:
                    return resp
                # Retry on rate-limit or server errors
                if resp.status_code in (429, 502, 503) and attempt < retries:
                    import time; time.sleep(1.5 ** (attempt + 1))
                    continue
            except requests.RequestException:
                if attempt < retries:
                    import time; time.sleep(1.0)
                    continue
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

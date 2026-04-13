"""
Core searcher — Brand-agnostic image search with confidence scoring.
Refactored from images-finder/searcher.py into a class (no globals).
"""
from __future__ import annotations

from dataclasses import dataclass
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

_STOP_TOKENS = {
    "and", "the", "with", "for", "from", "mens", "men", "women", "womens",
    "woman", "man", "kid", "kids", "junior", "jr", "adult", "unisex", "new",
    "wmns", "gs", "ps", "td", "eu", "uk", "us",
}
_COLOR_WORDS = {
    "black", "white", "blue", "navy", "green", "red", "pink", "purple", "orange",
    "yellow", "grey", "gray", "beige", "brown", "tan", "khaki", "olive", "cream",
    "silver", "gold", "multi", "multicolor", "violet", "indigo", "aqua", "turquoise",
    "pelican", "ghost", "salt", "lakers", "natural", "oxide", "wax", "dog",
}
_CATEGORY_FAMILY_TERMS: dict[str, set[str]] = {
    "footwear": {
        "footwear", "shoe", "shoes", "sneaker", "sneakers", "trainer", "trainers",
        "running", "runner", "runners", "boot", "boots", "sandals", "sandals",
        "slipper", "slippers", "loafer", "loafers", "clog", "clogs",
    },
    "shorts": {"short", "shorts", "bermuda", "bermudas", "swimshort", "swimshorts"},
    "tshirt": {"tshirt", "tshirts", "tee", "tees", "t-shirt", "t-shirts", "jersey"},
    "shirt": {"shirt", "shirts", "overshirt", "overshirts", "polo", "polos", "blouse", "top", "tops"},
    "hoodie": {"hoodie", "hoodies", "sweatshirt", "sweatshirts", "crewneck", "pullover", "pullovers"},
    "pants": {
        "pant", "pants", "trouser", "trousers", "jean", "jeans", "denim",
        "legging", "leggings", "cargo", "cargos", "chino", "chinos",
    },
    "jacket": {
        "jacket", "jackets", "coat", "coats", "parka", "parkas", "vest", "vests",
        "gilet", "gilets", "blazer", "blazers", "windbreaker", "anorak",
    },
    "bag": {
        "bag", "bags", "backpack", "backpacks", "tote", "totes", "satchel", "satchels",
        "pouch", "pouches", "wallet", "wallets", "keyholder", "keyholders",
        "keychain", "keychains", "shoulderbag", "crossbody",
    },
    "hat": {"hat", "hats", "cap", "caps", "beanie", "beanies", "bucket", "buckethat", "buckethats"},
    "dress": {"dress", "dresses", "gown", "gowns"},
    "skirt": {"skirt", "skirts"},
    "accessory": {"bracelet", "bracelets", "necklace", "necklaces", "ring", "rings", "belt", "belts", "scarf", "scarves"},
}
_NEGATIVE_HINT_TERMS: dict[str, set[str]] = {
    "footwear": {"bike", "bikes", "bicycle", "bicycles", "mtb", "mountainbike", "drink", "beverage", "can", "cans"},
    "shorts": {"tshirt", "tshirts", "tee", "tees", "hoodie", "hoodies", "shirt", "shirts", "jacket", "jackets"},
    "tshirt": {"shorts", "bermuda", "bermudas", "pants", "trousers", "shoes", "sneakers"},
}
_LOW_QUALITY_TERMS = {
    "logo", "logos", "banner", "banners", "icon", "icons", "avatar", "avatars",
    "wallpaper", "wallpapers", "illustration", "illustrations", "vector", "vectors",
    "mockup", "mockups", "template", "templates", "clipart", "poster", "posters",
}
_TRANSIENT_QUERY_PARAMS = {
    "w", "h", "width", "height", "q", "quality", "fit", "fm", "fl", "f",
    "auto", "dpr", "ixlib", "imwidth", "crop", "rect", "bg",
}
_SIZE_SUFFIX_RE = re.compile(r"(?i)^(.+?-[WMUKBGT])-\d{1,2}(?:\.\d+)?$")
_TRAILING_SIZE_TOKEN_RE = re.compile(r"(?i)^(.+?)-(?:eu|us|uk)?\d{1,2}(?:\.\d+)?$")


@dataclass
class SearchHit:
    url: str
    page_url: str = ""
    title: str = ""
    description: str = ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _tokenize(value: str) -> list[str]:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return [token for token in text.split() if token and token not in _STOP_TOKENS]


def _unique_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    kept = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() not in _TRANSIENT_QUERY_PARAMS:
            kept.append((key, value))
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        urllib.parse.urlencode(sorted(kept)),
        "",
    ))


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

    def _normalize_item_code(self, item_code: str, item_group: str | None, style_name: str | None) -> str:
        code = str(item_code or "").strip()
        if not code:
            return ""
        text = " ".join(filter(None, [item_group, style_name])).lower()
        footwear_hint = any(term in text for term in _CATEGORY_FAMILY_TERMS["footwear"])

        m = _SIZE_SUFFIX_RE.match(code)
        if m:
            return m.group(1)
        if footwear_hint:
            m = _TRAILING_SIZE_TOKEN_RE.match(code)
            if m:
                return m.group(1)
        return code

    def _normalized_color_identity(self, color_code: str | None, color_name: str | None) -> str:
        if color_name:
            return _slug(color_name)
        code = str(color_code or "").strip()
        if "|" in code:
            code = code.split("|", 1)[0]
        return _slug(code)

    def _infer_category_family(self, item_group: str | None, style_name: str | None) -> tuple[str | None, set[str]]:
        tokens = set(_tokenize(" ".join(filter(None, [item_group, style_name]))))
        family_scores: dict[str, int] = {}
        matched_terms: dict[str, set[str]] = {}
        for family, terms in _CATEGORY_FAMILY_TERMS.items():
            hits = {token for token in tokens if token in terms}
            if hits:
                family_scores[family] = len(hits)
                matched_terms[family] = hits
        if not family_scores:
            return None, set()
        family = max(family_scores, key=family_scores.get)
        return family, matched_terms.get(family, set())

    def _build_item_context(self, item: dict) -> dict[str, Any]:
        item_code = str(item.get("item_code") or "").strip()
        color_code = str(item.get("color_code") or "").strip() or None
        color_name = str(item.get("color_name") or "").strip() or None
        style_name = str(item.get("style_name") or "").strip() or None
        brand = str(item.get("brand") or "").strip()
        barcode = str(item.get("barcode") or "").strip() or None
        item_group = str(item.get("item_group") or "").strip() or None
        base_item_code = self._normalize_item_code(item_code, item_group, style_name)
        family, family_terms = self._infer_category_family(item_group, style_name)
        style_tokens = [
            token for token in _tokenize(style_name or "")
            if token not in family_terms and token not in _COLOR_WORDS and len(token) >= 3
        ]
        color_tokens = [token for token in _tokenize(color_name or "") if len(token) >= 3]
        return {
            "item_code": item_code,
            "base_item_code": base_item_code or item_code,
            "color_code": color_code,
            "color_name": color_name,
            "style_name": style_name,
            "brand": brand,
            "barcode": barcode,
            "item_group": item_group,
            "category_family": family,
            "category_terms": family_terms,
            "style_tokens": _unique_preserve(style_tokens),
            "color_tokens": _unique_preserve(color_tokens),
            "normalized_color_identity": self._normalized_color_identity(color_code, color_name),
        }

    def cache_identity(self, item: dict) -> tuple[str, str, str]:
        ctx = self._build_item_context(item)
        return (
            ctx["base_item_code"] or ctx["item_code"],
            ctx["normalized_color_identity"],
            (ctx["brand"] or "").lower().strip(),
        )

    def build_manual_search_query(self, item: dict) -> str:
        ctx = self._build_item_context(item)
        parts = [
            ctx["brand"],
            ctx["style_name"],
            ctx["item_group"],
            ctx["color_name"],
            ctx["base_item_code"],
        ]
        return " ".join(part for part in parts if part).strip()

    def _build_text_query(self, ctx: dict[str, Any], include_category: bool = True, prefer_base_code: bool = True) -> str:
        item_code = ctx["base_item_code"] if prefer_base_code else ctx["item_code"]
        parts = [ctx["brand"], item_code, ctx["style_name"], ctx["color_name"]]
        if include_category:
            parts.append(ctx["item_group"])
        return " ".join(part for part in parts if part).strip()

    def _build_code_query(self, ctx: dict[str, Any]) -> str:
        parts = [ctx["brand"], ctx["base_item_code"], ctx["item_group"], "product image"]
        return " ".join(part for part in parts if part).strip()

    def _coerce_hits(self, raw_hits: list[Any]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for raw in raw_hits or []:
            if isinstance(raw, SearchHit):
                hit = raw
            elif isinstance(raw, dict):
                url = str(raw.get("url") or "").strip()
                if not url:
                    continue
                hit = SearchHit(
                    url=url,
                    page_url=str(raw.get("page_url") or "").strip(),
                    title=str(raw.get("title") or "").strip(),
                    description=str(raw.get("description") or "").strip(),
                )
            else:
                url = str(raw or "").strip()
                if not url:
                    continue
                hit = SearchHit(url=url)
            hits.append(hit)
        return hits

    def _aggregate_hits(self, source_results: dict[str, list[SearchHit]]) -> list[dict[str, Any]]:
        combined: dict[str, dict[str, Any]] = {}
        for source_name, hits in source_results.items():
            for index, hit in enumerate(hits):
                key = _canonical_url(hit.url)
                entry = combined.setdefault(key, {
                    "url": hit.url,
                    "page_url": "",
                    "title": "",
                    "description": "",
                    "source_names": set(),
                    "positions": [],
                })
                if (
                    ("mm.bing.net" in entry["url"].lower() or "th.bing.com" in entry["url"].lower())
                    and "bing.net" not in hit.url.lower()
                ):
                    entry["url"] = hit.url
                entry["source_names"].add(source_name)
                entry["positions"].append(index)
                if hit.page_url and not entry["page_url"]:
                    entry["page_url"] = hit.page_url
                if hit.title and not entry["title"]:
                    entry["title"] = hit.title
                if hit.description and not entry["description"]:
                    entry["description"] = hit.description
        return list(combined.values())

    def search(self, item: dict, ai_queries: list[str] | None = None) -> tuple[list[str], dict[str, float]]:
        """
        Search for product images — all sources fire in parallel (no sequential waits).
        Returns (candidates, scores).
        """
        ctx = self._build_item_context(item)
        item_code = ctx["item_code"]
        brand = ctx["brand"]

        source_results: dict[str, list[SearchHit]] = {}
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
                    dom, self._build_text_query(ctx)
                )

        if site_urls:
            tasks["brand_site"] = lambda: self._bing_site_search(
                site_urls[0], self._build_text_query(ctx)
            )
            # Also try a second brand-site search focusing on style/product name
            if ctx["style_name"] and ctx["style_name"].lower() != item_code.lower():
                tasks["brand_site_style"] = lambda: self._bing_site_search(
                    site_urls[0], self._build_text_query(ctx, prefer_base_code=False)
                )

        if self.google_api_key and self.google_cse_id:
            tasks["google"] = lambda: self._google_search(self._build_text_query(ctx))

        tasks["bing"] = lambda: self._bing_search(self._build_text_query(ctx))
        tasks["bing_code_only"] = lambda: self._bing_raw(self._build_code_query(ctx)) if item_code else []
        tasks["google_scrape"] = lambda: self._google_images_scrape(self._build_text_query(ctx))
        tasks["ddg"] = lambda: self._duckduckgo_search(self._build_text_query(ctx))
        tasks["yahoo"] = lambda: self._yahoo_images_scrape(self._build_text_query(ctx))

        # Fire all sources at the same time
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {pool.submit(fn): key for key, fn in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    found = self._coerce_hits(future.result())
                    if found:
                        with lock:
                            source_results[key] = found
                except Exception:
                    pass

        combined_hits = self._aggregate_hits(source_results)
        raw_scores = {}
        for hit in combined_hits:
            raw_scores[hit["url"]] = self._score_hit(hit, ctx)

        candidates = sorted(
            [hit["url"] for hit in combined_hits],
            key=lambda url: raw_scores[url],
            reverse=True,
        )[:self.max_candidates]
        scores = {u: round(raw_scores[u], 2) for u in candidates}
        return candidates, scores

    def _score_hit(self, hit: dict[str, Any], ctx: dict[str, Any]) -> float:
        url = hit["url"]
        page_url = hit.get("page_url") or ""
        title = hit.get("title") or ""
        description = hit.get("description") or ""
        source_names = hit.get("source_names") or set()
        positions = hit.get("positions") or []

        score = 0.0
        lower = " ".join([url, page_url, title, description]).lower()
        normalized_text = _slug(lower)
        text_tokens = set(_tokenize(lower))

        full_code = ctx["item_code"]
        base_code = ctx["base_item_code"]
        full_code_slug = _slug(full_code)
        base_code_slug = _slug(base_code)
        if full_code_slug and full_code_slug in normalized_text:
            score += 0.75
        elif base_code_slug and base_code_slug in normalized_text:
            score += 0.65
        else:
            code_tokens = [token for token in _tokenize(base_code) if len(token) >= 3]
            if code_tokens:
                matches = sum(1 for token in code_tokens if token in text_tokens or token in normalized_text)
                if matches == len(code_tokens):
                    score += 0.45
                elif matches >= max(2, len(code_tokens) - 1):
                    score += 0.28
                elif matches >= 1:
                    score += 0.12

        barcode = ctx.get("barcode") or ""
        barcode_slug = _slug(barcode)
        if barcode_slug and barcode_slug in normalized_text:
            score += 0.6

        style_tokens = ctx.get("style_tokens") or []
        if style_tokens:
            style_matches = sum(1 for token in style_tokens if token in text_tokens or token in normalized_text)
            score += min(0.35, style_matches * 0.08)
            if style_matches == 0 and base_code_slug not in normalized_text:
                score -= 0.08

        color_code = ctx.get("color_code") or ""
        color_code_slug = _slug(color_code)
        if color_code_slug and color_code_slug in normalized_text:
            score += 0.18

        color_tokens = ctx.get("color_tokens") or []
        if color_tokens:
            color_matches = {token for token in color_tokens if token in text_tokens or token in normalized_text}
            if color_matches:
                score += min(0.18, len(color_matches) * 0.06)
            other_colors = {token for token in text_tokens if token in _COLOR_WORDS} - set(color_tokens)
            if other_colors and not color_matches:
                score -= 0.1

        # Boost for user-specified priority domains (highest priority)
        for extra_d in self.extra_site_urls:
            clean_d = extra_d.strip().lstrip("https://").lstrip("http://").rstrip("/")
            if clean_d and (clean_d in url.lower() or clean_d in page_url.lower()):
                score += 0.55
                break

        domain = BRAND_DOMAINS.get((ctx.get("brand") or "").lower().strip())
        if domain and (domain in url.lower() or domain in page_url.lower()):
            score += 0.45
        else:
            brand_slug = _slug(ctx.get("brand") or "")
            if brand_slug and len(brand_slug) > 3 and brand_slug in normalized_text:
                score += 0.15
            # Penalize URLs from wrong brand domains
            for other_brand, other_domain in BRAND_DOMAINS.items():
                if other_brand != (ctx.get("brand") or "").lower().strip() and (
                    other_domain in url.lower() or other_domain in page_url.lower()
                ):
                    score -= 0.35
                    break

        if any(p in lower for p in _CDN_PATTERNS) or any(p in lower for p in _PRODUCT_PATHS):
            score += 0.10
        if "mm.bing.net" in url.lower() or "th.bing.com" in url.lower():
            score -= 0.12

        category_family = ctx.get("category_family")
        if category_family:
            family_terms = _CATEGORY_FAMILY_TERMS.get(category_family, set())
            family_hits = {token for token in text_tokens if token in family_terms}
            if family_hits:
                score += 0.35
            conflicting_hits = set()
            for other_family, other_terms in _CATEGORY_FAMILY_TERMS.items():
                if other_family == category_family:
                    continue
                matched = {token for token in text_tokens if token in other_terms}
                if matched:
                    conflicting_hits |= matched
            if conflicting_hits and not family_hits:
                score -= 0.45
            negative_terms = _NEGATIVE_HINT_TERMS.get(category_family, set())
            if any(token in text_tokens for token in negative_terms):
                score -= 0.4

        if any(token in text_tokens for token in _LOW_QUALITY_TERMS):
            score -= 0.25

        if "brand_site" in source_names:
            score += 0.35
        if "brand_site_style" in source_names:
            score += 0.2
        if any(name.startswith("extra_") for name in source_names):
            score += 0.45

        best_position = min(positions) if positions else 99
        score += max(0.0, 0.06 - (best_position + 1) * 0.01)

        source_count = len(source_names)
        if source_count >= 2:
            score += min(0.25, (source_count - 1) * 0.10)

        return min(max(score, 0.0), 1.0)

    def _bing_site_search(self, domain: str, query: str) -> list[SearchHit]:
        """Search Bing limited to a specific domain."""
        query = " ".join(p for p in [f"site:{domain}", query] if p)
        return self._bing_raw(query)

    def _bing_search(self, query: str) -> list[SearchHit]:
        return self._bing_raw(query)

    def _bing_raw(self, query: str) -> list[SearchHit]:
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
        raw = resp.text
        # Decode HTML entities — Bing encodes JSON as &quot;murl&quot;:&quot;https://...&quot;
        decoded = _html.unescape(raw)

        soup = BeautifulSoup(raw, "html.parser")

        hits: list[SearchHit] = []
        seen: set[str] = set()

        # Method 1: iusc anchor tags with JSON metadata — best source for paired murl+turl
        for tag in soup.find_all("a", class_="iusc"):
            m = tag.get("m", "")
            if not m:
                continue
            try:
                obj = json.loads(m)
                title = str(obj.get("t") or obj.get("title") or "").strip()
                page_url = str(obj.get("purl") or obj.get("surl") or obj.get("ru") or "").strip()
                description = str(obj.get("desc") or obj.get("caption") or "").strip()
                for key in ("murl", "turl"):
                    value = str(obj.get(key) or "").strip()
                    if value and value not in seen:
                        seen.add(value)
                        hits.append(SearchHit(
                            url=value,
                            page_url=page_url,
                            title=title,
                            description=description,
                        ))
            except Exception:
                pass

        # Method 2: murl/turl from decoded JSON blobs (handles &quot; encoding)
        for text in (raw, decoded):
            for value in re.findall(r'"murl"\s*:\s*"(https?://[^"\\]+)"', text):
                if value and value not in seen:
                    seen.add(value)
                    hits.append(SearchHit(url=value))
            for value in re.findall(r'"turl"\s*:\s*"(https?://[^"\\]+)"', text):
                if value and value not in seen:
                    seen.add(value)
                    hits.append(SearchHit(url=value))

        # Method 3: imgurl= in raw HTML
        imgurl_matches = re.findall(r'imgurl=(https?://[^&"\'\\s]+)', decoded)
        for value in [urllib.parse.unquote(u) for u in imgurl_matches]:
            if value and value not in seen:
                seen.add(value)
                hits.append(SearchHit(url=value))

        # Method 4: contentUrl / mediaUrl patterns
        for pat in [r'"contentUrl"\s*:\s*"(https?://[^"\\]+)"',
                    r'"mediaUrl"\s*:\s*"(https?://[^"\\]+)"']:
            for value in re.findall(pat, decoded):
                if value and value not in seen:
                    seen.add(value)
                    hits.append(SearchHit(url=value))

        # Method 5: any https URL ending in image extension
        if len(hits) < 3:
            img_urls = re.findall(r'https?://[^\s"\'<>&]{15,}\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>&]*)?', decoded)
            for value in img_urls:
                if "bing.com" in value or "microsoft.com" in value:
                    continue
                if value not in seen:
                    seen.add(value)
                    hits.append(SearchHit(url=value))

        return hits

    def _google_search(self, query: str) -> list[SearchHit]:
        if not self.google_api_key or not self.google_cse_id:
            return []
        resp = self._get("https://www.googleapis.com/customsearch/v1", params={
            "key": self.google_api_key, "cx": self.google_cse_id, "q": query,
            "searchType": "image", "num": self.max_candidates,
            "imgType": "photo", "safe": "active",
        })
        if resp is None:
            return []
        try:
            data = resp.json()
            hits: list[SearchHit] = []
            for item in data.get("items", []):
                url = str(item.get("link") or "").strip()
                if not url:
                    continue
                hits.append(SearchHit(
                    url=url,
                    page_url=str(item.get("image", {}).get("contextLink") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    description=str(item.get("snippet") or "").strip(),
                ))
            return hits
        except Exception:
            return []

    def _google_images_scrape(self, query: str) -> list[SearchHit]:
        """Scrape Google Images search results (no API key needed)."""
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

        # If few results, retry once with the same text query to pick up alternate embeds
        if len(filtered) < 3:
            fallback_query = query
            fallback_url = f"https://www.google.com/search?q={urllib.parse.quote(fallback_query)}&tbm=isch&hl=en"
            resp2 = self._get(fallback_url, headers=google_headers)
            if resp2:
                ou2 = re.findall(r'"ou"\s*:\s*"(https?://[^"]+)"', resp2.text)
                extra = [u for u in ou2 if "google.com" not in u and "gstatic.com" not in u and len(u) > 20]
                filtered = self._dedupe(filtered + extra)

        return [SearchHit(url=u) for u in self._dedupe(filtered)[:self.max_candidates]]

    def _duckduckgo_search(self, query: str) -> list[SearchHit]:

        headers = {"User-Agent": _HEADERS["User-Agent"], "Accept-Language": "en-US,en;q=0.9"}
        try:
            resp = _HTTP.get("https://duckduckgo.com/",
                             params={"q": query, "iax": "images", "ia": "images"},
                             headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception:
            return []

        vqd = re.search(r"vqd=([\d-]+)", resp.text)
        if not vqd:
            fallback = re.findall(r'"image"\s*:\s*"(https://[^"]+?)"', resp.text)[:self.max_candidates]
            return [SearchHit(url=u) for u in fallback]

        try:
            img_resp = _HTTP.get("https://duckduckgo.com/i.js",
                                 params={"q": query, "o": "json", "vqd": vqd.group(1), "f": ",,,", "p": "1"},
                                 headers={**headers, "Referer": "https://duckduckgo.com/"},
                                 timeout=REQUEST_TIMEOUT)
            data = img_resp.json()
            hits: list[SearchHit] = []
            for result in data.get("results", [])[:self.max_candidates]:
                url = str(result.get("image") or "").strip()
                if not url:
                    continue
                hits.append(SearchHit(
                    url=url,
                    page_url=str(result.get("url") or "").strip(),
                    title=str(result.get("title") or "").strip(),
                    description=str(result.get("source") or "").strip(),
                ))
            return hits
        except Exception:
            return []

    def _yahoo_images_scrape(self, query: str) -> list[SearchHit]:
        """Scrape Yahoo Image Search results (no API key needed)."""
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

        return [SearchHit(url=u) for u in self._dedupe(urls)[:self.max_candidates]]

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

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
import unicodedata
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
STRICT_MAX_CANDIDATES = 5
REQUEST_TIMEOUT = 15
SEARCH_CACHE_VERSION = 3

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

BRAND_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "american rag": {
        "preferred_domains": ["americanrag.ae"],
        "blocked_domains": ["amazon.", "ebay.", "aliexpress.", "temu."],
        "strict_query": True,
    },
    "adidas": {
        "preferred_domains": ["adidas.com", "assets.adidas.com"],
        "strict_query": True,
    },
    "on": {
        "preferred_domains": ["on.com"],
        "blocked_terms": ["bike", "bicycle", "mtb", "drink", "beverage", "dew", "can", "cans"],
        "strict_query": True,
    },
    "butter goods": {
        "preferred_domains": ["buttergoods.com"],
        "strict_query": True,
    },
    "casablanca": {
        "preferred_domains": ["casablancaparis.com"],
        "strict_query": True,
    },
    "carhartt wip": {
        "preferred_domains": ["carhartt-wip.com"],
        "strict_query": True,
    },
    "golden goose": {
        "preferred_domains": ["goldengoose.com"],
        "strict_query": True,
    },
    "stone island": {
        "preferred_domains": ["stoneisland.com"],
        "strict_query": True,
    },
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
    "chocolate",
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
_PREFERRED_PRODUCT_SHOT_TERMS = {
    "packshot", "product", "pair", "pairs", "profile", "side", "lateral",
    "front", "catalog", "studio",
}
_DETAIL_SHOT_TERMS = {
    "detail", "details", "closeup", "zoom", "macro", "crop", "cropped", "texture",
}
_OUTSOLE_SHOT_TERMS = {
    "sole", "outsole", "bottom", "underside", "heel",
}
_LIFESTYLE_SHOT_TERMS = {
    "lifestyle", "editorial", "lookbook", "campaign", "worn", "model",
}
_STRICT_PREVIEW_SHOT_TERMS = {
    "closeup", "close-up", "detail", "macro", "texture", "sole", "outsole",
    "bottom", "underside", "heel", "on-foot", "worn", "model",
    "editorial", "campaign", "lookbook", "lifestyle",
}
_GENERIC_BRAND_TOKENS = {
    "co", "company", "cie", "inc", "ltd", "llc", "group", "official", "brand",
    "spa", "srl", "the",
}
_TRANSIENT_QUERY_PARAMS = {
    "w", "h", "width", "height", "q", "quality", "fit", "fm", "fl", "f",
    "auto", "dpr", "ixlib", "imwidth", "crop", "rect", "bg",
}
_SIZE_SUFFIX_RE = re.compile(r"(?i)^(.+?-[WMUKBGT])-\d{1,2}(?:\.\d+)?$")
_TRAILING_SIZE_TOKEN_RE = re.compile(r"(?i)^(.+?)-(?:eu|us|uk)?\d{1,2}(?:\.\d+)?$")
_SIZE_NUMERIC_RE = re.compile(r"(\d+(?:\.\d+)?)")
_VARIANT_FAMILY_CODE_RE = re.compile(r"(?i)^(.+?)-(\d{4})$")


def normalize_base_item_code(item_code: str | None, item_group: str | None = None, style_name: str | None = None) -> str:
    code = str(item_code or "").strip()
    if not code:
        return ""
    text = " ".join(filter(None, [item_group, style_name])).lower()
    footwear_hint = any(term in text for term in _CATEGORY_FAMILY_TERMS.get("footwear", set()))
    m = _SIZE_SUFFIX_RE.match(code)
    if m:
        return m.group(1)
    if footwear_hint:
        m = _TRAILING_SIZE_TOKEN_RE.match(code)
        if m:
            return m.group(1)
    return code


def normalize_related_item_code(item_code: str | None, item_group: str | None = None, style_name: str | None = None) -> str:
    code = normalize_base_item_code(item_code, item_group, style_name)
    if not code:
        return ""
    m = _VARIANT_FAMILY_CODE_RE.match(code)
    if not m:
        return code
    family_code = m.group(1).strip()
    if re.search(r"[a-zA-Z]", family_code) and re.search(r"\d", family_code):
        return family_code
    return code


def item_sort_key(
    *,
    brand: str | None,
    style_name: str | None,
    item_code: str | None,
    item_group: str | None,
    color_name: str | None,
    color_code: str | None,
    size: str | None = None,
) -> tuple:
    """Sort key mirroring the sample xlsx layout:
    Brand -> Style Name -> Base Item Code (size suffix stripped) -> Color -> Size -> Raw code.
    Keeps identical styles adjacent and their colors/sizes in a stable order.
    """
    base_code = normalize_base_item_code(item_code, item_group, style_name)
    color_label = (color_name or color_code or "").strip()
    size_str = str(size or "").strip()
    size_match = _SIZE_NUMERIC_RE.search(size_str)
    size_num = float(size_match.group(1)) if size_match else float("inf")
    return (
        (brand or "").strip().lower(),
        (style_name or item_group or "").strip().lower(),
        base_code.lower(),
        color_label.lower(),
        size_num,
        size_str.lower(),
        (item_code or "").strip().lower(),
    )


@dataclass
class SearchHit:
    url: str
    page_url: str = ""
    title: str = ""
    description: str = ""


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", normalized.lower())


def _join_distinct_parts(parts: list[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        key = _slug(text)
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return " ".join(out).strip()


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


def normalize_search_domain(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parsed = urllib.parse.urlsplit(text)
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")


def split_and_normalize_domains(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    out: list[str] = []
    for raw in raw_values:
        for chunk in re.split(r"[\n,;]+", str(raw or "")):
            domain = normalize_search_domain(chunk)
            if domain:
                out.append(domain)
    return _unique_preserve(out)


class ImageSearcher:
    """Search for product images across multiple sources."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.max_candidates = int(self.config.get("max_candidates", MAX_CANDIDATES))
        self.google_api_key = self.config.get("google_api_key", "")
        self.google_cse_id = self.config.get("google_cse_id", "")
        raw_brand_site_urls = self.config.get("brand_site_urls", {}) or {}
        self.brand_site_urls: dict[str, list[str]] = {}
        for brand_name, urls in raw_brand_site_urls.items():
            clean_urls = split_and_normalize_domains(urls)
            if clean_urls:
                self.brand_site_urls[str(brand_name or "").strip().lower()] = clean_urls
        # Extra priority domains that apply to every item in this session
        self.extra_site_urls: list[str] = split_and_normalize_domains(
            self.config.get("extra_site_urls", [])
        )
        self.strict_match_mode = bool(self.config.get("strict_match_mode", True))

    def _brand_identity_keys(self, brand: str) -> set[str]:
        text = str(brand or "").strip().lower()
        if not text:
            return set()
        tokenized = re.sub(r"[^a-z0-9]+", " ", text).split()
        filtered = [token for token in tokenized if token not in _GENERIC_BRAND_TOKENS]
        keys = {
            text,
            _slug(text),
            _slug(" ".join(filtered)),
        }
        return {key for key in keys if key and len(key) >= 2}

    def _domain_identity_keys(self, urls: list[str]) -> set[str]:
        keys: set[str] = set()
        for url in urls or []:
            host = normalize_search_domain(url)
            if not host:
                continue
            pieces = [piece for piece in host.split(".") if piece and piece != "www"]
            if pieces:
                keys.add(_slug(pieces[0]))
            keys.add(_slug(host))
        return {key for key in keys if key and len(key) >= 2}

    def _config_matches_brand(self, brand: str, config_brand: str, config_urls: list[str]) -> bool:
        brand_keys = self._brand_identity_keys(brand)
        config_keys = self._brand_identity_keys(config_brand)
        if not brand_keys or not config_keys:
            return False

        if brand_keys & config_keys:
            return True

        for a in brand_keys:
            for b in config_keys:
                if min(len(a), len(b)) >= 5 and (a in b or b in a):
                    return True

        domain_keys = self._domain_identity_keys(config_urls)
        for brand_key in brand_keys:
            for domain_key in domain_keys:
                if min(len(brand_key), len(domain_key)) >= 5 and (
                    brand_key in domain_key or domain_key in brand_key
                ):
                    return True

        return False

    def _matching_brand_playbook(self, brand: str) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "preferred_domains": [],
            "blocked_domains": [],
            "blocked_terms": [],
            "strict_query": self.strict_match_mode,
        }
        brand_keys = self._brand_identity_keys(brand)
        if not brand_keys:
            return merged

        for playbook_brand, playbook in BRAND_PLAYBOOKS.items():
            playbook_keys = self._brand_identity_keys(playbook_brand)
            if not playbook_keys:
                continue
            matched = bool(brand_keys & playbook_keys)
            if not matched:
                for a in brand_keys:
                    for b in playbook_keys:
                        if min(len(a), len(b)) >= 4 and (a in b or b in a):
                            matched = True
                            break
                    if matched:
                        break
            if not matched:
                continue
            merged["preferred_domains"].extend(split_and_normalize_domains(playbook.get("preferred_domains", [])))
            merged["blocked_domains"].extend(split_and_normalize_domains(playbook.get("blocked_domains", [])))
            merged["blocked_terms"].extend([str(term).strip().lower() for term in playbook.get("blocked_terms", []) if str(term).strip()])
            merged["strict_query"] = bool(playbook.get("strict_query", merged["strict_query"]))
        merged["preferred_domains"] = _unique_preserve(merged["preferred_domains"])
        merged["blocked_domains"] = _unique_preserve(merged["blocked_domains"])
        merged["blocked_terms"] = _unique_preserve(merged["blocked_terms"])
        return merged

    def matching_brand_configs(self, brand: str) -> list[tuple[str, list[str]]]:
        matches: list[tuple[str, list[str]]] = []
        for config_brand, config_urls in self.brand_site_urls.items():
            if self._config_matches_brand(brand, config_brand, config_urls):
                matches.append((config_brand, config_urls))
        return matches

    def matching_brand_site_urls(self, brand: str) -> list[str]:
        urls: list[str] = []
        for _config_brand, config_urls in self.matching_brand_configs(brand):
            urls.extend(config_urls)
        urls.extend(self._matching_brand_playbook(brand).get("preferred_domains", []))
        domain = BRAND_DOMAINS.get((brand or "").lower().strip())
        if domain:
            urls.append(domain)
        return _unique_preserve(urls)

    def _normalize_item_code(self, item_code: str, item_group: str | None, style_name: str | None) -> str:
        return normalize_base_item_code(item_code, item_group, style_name)

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
        related_item_code = normalize_related_item_code(item_code, item_group, style_name)
        family, family_terms = self._infer_category_family(item_group, style_name)
        playbook = self._matching_brand_playbook(brand)
        style_tokens = [
            token for token in _tokenize(style_name or "")
            if token not in family_terms and token not in _COLOR_WORDS and len(token) >= 3
        ]
        color_tokens = [token for token in _tokenize(color_name or "") if len(token) >= 3]
        exact_query = _join_distinct_parts([brand, style_name, item_group or "", color_name or "", base_item_code or item_code or barcode or ""])
        exact_query_tokens = [token for token in _tokenize(exact_query) if len(token) >= 3]
        return {
            "item_code": item_code,
            "base_item_code": base_item_code or item_code,
            "related_item_code": related_item_code or base_item_code or item_code,
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
            "preferred_domains": _unique_preserve(self.extra_site_urls + self.matching_brand_site_urls(brand)),
            "blocked_domains": playbook.get("blocked_domains", []),
            "blocked_terms": set(playbook.get("blocked_terms", [])),
            "strict_query": bool(playbook.get("strict_query", self.strict_match_mode)),
            "exact_query": exact_query,
            "exact_query_tokens": _unique_preserve(exact_query_tokens),
        }

    def cache_identity(self, item: dict) -> tuple[str, str, str]:
        ctx = self._build_item_context(item)
        return (
            ctx.get("related_item_code") or ctx["base_item_code"] or ctx["item_code"],
            ctx["normalized_color_identity"],
            (ctx["brand"] or "").lower().strip(),
        )

    def should_force_ai_primary(self, item: dict) -> bool:
        ctx = self._build_item_context(item)
        family = ctx.get("category_family")
        return bool(ctx.get("strict_query")) or family in {"footwear", "bag", "hat"}

    def build_manual_search_query(self, item: dict) -> str:
        ctx = self._build_item_context(item)
        return _join_distinct_parts([
            ctx["brand"],
            ctx["style_name"],
            ctx["item_group"],
            ctx["color_name"],
            ctx["base_item_code"],
        ])

    def _build_exact_query(self, ctx: dict[str, Any]) -> str:
        parts = [
            ctx["brand"],
            ctx["style_name"],
            ctx["item_group"],
            ctx["color_name"],
            ctx["base_item_code"],
        ]
        if not ctx["style_name"] and ctx["barcode"]:
            parts.append(ctx["barcode"])
        return _join_distinct_parts(parts)

    def _build_phrase_query(self, ctx: dict[str, Any]) -> str:
        exact = self._build_exact_query(ctx)
        if not exact:
            return ""
        return f"\"{exact}\""

    def _build_text_query(self, ctx: dict[str, Any], include_category: bool = True, prefer_base_code: bool = True) -> str:
        item_code = (
            ctx.get("related_item_code") or ctx["base_item_code"]
            if prefer_base_code
            else ctx["item_code"]
        )
        parts = [ctx["brand"], item_code, ctx["style_name"], ctx["color_name"]]
        if include_category:
            parts.append(ctx["item_group"])
        return _join_distinct_parts(parts)

    def _build_code_query(self, ctx: dict[str, Any]) -> str:
        parts = [ctx["brand"], ctx.get("related_item_code") or ctx["base_item_code"], ctx["item_group"], "product image"]
        return _join_distinct_parts(parts)

    def _is_obvious_wrong_color_hit(self, hit: dict[str, Any], ctx: dict[str, Any]) -> bool:
        color_tokens = ctx.get("color_tokens") or []
        if not color_tokens:
            return False
        text = " ".join([
            str(hit.get("url") or ""),
            str(hit.get("page_url") or ""),
            str(hit.get("title") or ""),
            str(hit.get("description") or ""),
        ]).lower()
        normalized = _slug(text)
        text_tokens = set(_tokenize(text))
        color_matches = {token for token in color_tokens if token in text_tokens or token in normalized}
        if color_matches:
            return False
        other_colors = {token for token in text_tokens if token in _COLOR_WORDS} - set(color_tokens)
        return bool(other_colors)

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

    def _strict_hit_priority_pool(self, hit: dict[str, Any]) -> int:
        source_names = set(hit.get("source_names") or set())
        if {"google_exact", "google_phrase", "google_scrape_exact", "google_scrape_phrase"} & source_names:
            return 0
        if {"bing_exact", "bing_phrase"} & source_names:
            return 1
        if any(
            (name.startswith("brand_site") or name.startswith("extra_")) and ("exact" in name or "phrase" in name)
            for name in source_names
        ):
            return 2
        if {"google", "google_scrape"} & source_names:
            return 3
        if {"bing", "ddg", "yahoo"} & source_names:
            return 4
        return 5

    def _strict_hit_looks_like_variant(self, hit: dict[str, Any]) -> bool:
        text = " ".join([
            str(hit.get("url") or ""),
            str(hit.get("page_url") or ""),
            str(hit.get("title") or ""),
            str(hit.get("description") or ""),
        ]).lower()
        text_tokens = set(_tokenize(text))
        return any(term in text_tokens for term in _STRICT_PREVIEW_SHOT_TERMS) or "on-foot" in text or "on foot" in text

    def _strict_candidate_pool(
        self,
        hits: list[dict[str, Any]],
        ctx: dict[str, Any],
        raw_scores: dict[str, float],
    ) -> list[dict[str, Any]]:
        if not hits:
            return hits

        filtered_hits = list(hits)
        strict_color_hits = [
            hit for hit in filtered_hits
            if not self._is_obvious_wrong_color_hit(hit, ctx)
        ]
        if strict_color_hits:
            filtered_hits = strict_color_hits

        for tier in range(5):
            pool = [hit for hit in filtered_hits if self._strict_hit_priority_pool(hit) == tier]
            if not pool:
                continue
            ordered_pool = sorted(pool, key=lambda hit: raw_scores.get(hit["url"], 0.0), reverse=True)
            full_code_slug = _slug(ctx.get("item_code") or "")
            base_code_slug = _slug(ctx.get("base_item_code") or "")
            related_code_slug = _slug(ctx.get("related_item_code") or "")
            if full_code_slug or base_code_slug:
                code_hits = []
                for hit in ordered_pool:
                    text = " ".join([
                        str(hit.get("url") or ""),
                        str(hit.get("page_url") or ""),
                        str(hit.get("title") or ""),
                        str(hit.get("description") or ""),
                    ]).lower()
                    normalized = _slug(text)
                    if (
                        (full_code_slug and full_code_slug in normalized)
                        or (base_code_slug and base_code_slug in normalized)
                        or (related_code_slug and related_code_slug in normalized)
                    ):
                        code_hits.append(hit)
                if code_hits:
                    ordered_pool = code_hits
                elif tier in (0, 1):
                    continue

            top_pool_score = raw_scores.get(ordered_pool[0]["url"], 0.0)
            if top_pool_score < 0.72:
                continue

            clean_pool = [hit for hit in ordered_pool if not self._strict_hit_looks_like_variant(hit)]
            if clean_pool:
                top_clean_score = raw_scores.get(clean_pool[0]["url"], 0.0)
                if top_clean_score >= 0.55:
                    return [
                        hit for hit in clean_pool
                        if raw_scores.get(hit["url"], 0.0) >= max(0.55, top_clean_score - 0.16)
                    ]
            return [
                hit for hit in ordered_pool
                if raw_scores.get(hit["url"], 0.0) >= max(0.68, top_pool_score - 0.16)
            ]

        global_clean_hits = [hit for hit in filtered_hits if not self._strict_hit_looks_like_variant(hit)]
        if global_clean_hits:
            ordered_clean_hits = sorted(global_clean_hits, key=lambda hit: raw_scores.get(hit["url"], 0.0), reverse=True)
            top_clean_score = raw_scores.get(ordered_clean_hits[0]["url"], 0.0)
            return [
                hit for hit in ordered_clean_hits
                if raw_scores.get(hit["url"], 0.0) >= max(0.55, top_clean_score - 0.16)
            ]
        return filtered_hits

    def search(self, item: dict, ai_queries: list[str] | None = None) -> tuple[list[str], dict[str, float]]:
        """
        Search for product images — all sources fire in parallel (no sequential waits).
        Returns (candidates, scores).
        """
        ctx = self._build_item_context(item)
        item_code = ctx["item_code"]
        brand = ctx["brand"]
        broad_query = self._build_text_query(ctx)
        exact_query = self._build_exact_query(ctx)
        phrase_query = self._build_phrase_query(ctx)

        source_results: dict[str, list[SearchHit]] = {}
        lock = threading.Lock()

        # Resolve brand site domain once (before spawning threads)
        site_urls = self.matching_brand_site_urls(brand)
        if not site_urls:
            domain = BRAND_DOMAINS.get(brand.lower().strip())
            if domain:
                site_urls = [normalize_search_domain(domain)]

        # Build task list — all run simultaneously
        tasks: dict[str, Any] = {}

        if ai_queries:
            for i, query in enumerate(ai_queries[:3]):
                tasks[f"ai_{i}"] = lambda q=query: self._bing_raw(q)

        # Extra priority domains (from step-3 form) — searched first, before brand domains
        for ei, extra_domain in enumerate(self.extra_site_urls[:3]):
            d = normalize_search_domain(extra_domain)
            if d:
                tasks[f"extra_{ei}"] = lambda dom=d: self._bing_site_search(
                    dom, broad_query
                )
                if exact_query and exact_query != broad_query:
                    tasks[f"extra_{ei}_exact"] = lambda dom=d, q=exact_query: self._bing_site_search(dom, q)
                if phrase_query:
                    tasks[f"extra_{ei}_phrase"] = lambda dom=d, q=phrase_query: self._bing_site_search(dom, q)

        if site_urls:
            seen_domains = set(self.extra_site_urls)
            for site_idx, site_url in enumerate(site_urls[:2]):
                if not site_url or site_url in seen_domains:
                    continue
                seen_domains.add(site_url)
                tasks[f"brand_site_{site_idx}"] = lambda dom=site_url: self._bing_site_search(
                    dom, broad_query
                )
                if exact_query and exact_query != broad_query:
                    tasks[f"brand_site_{site_idx}_exact"] = lambda dom=site_url, q=exact_query: self._bing_site_search(
                        dom, q
                    )
                if phrase_query:
                    tasks[f"brand_site_{site_idx}_phrase"] = lambda dom=site_url, q=phrase_query: self._bing_site_search(
                        dom, q
                    )
                if ctx["style_name"] and ctx["style_name"].lower() != item_code.lower():
                    tasks[f"brand_site_{site_idx}_style"] = lambda dom=site_url: self._bing_site_search(
                        dom, self._build_text_query(ctx, prefer_base_code=False)
                    )

        if self.google_api_key and self.google_cse_id:
            tasks["google"] = lambda q=broad_query: self._google_search(q)
            if exact_query and exact_query != broad_query:
                tasks["google_exact"] = lambda q=exact_query: self._google_search(q)
            if phrase_query:
                tasks["google_phrase"] = lambda q=phrase_query: self._google_search(q)

        tasks["bing"] = lambda q=broad_query: self._bing_search(q)
        if exact_query and exact_query != broad_query:
            tasks["bing_exact"] = lambda q=exact_query: self._bing_search(q)
        if phrase_query:
            tasks["bing_phrase"] = lambda q=phrase_query: self._bing_search(q)
        tasks["bing_code_only"] = lambda: self._bing_raw(self._build_code_query(ctx)) if item_code else []
        tasks["google_scrape"] = lambda q=broad_query: self._google_images_scrape(q)
        if exact_query and exact_query != broad_query:
            tasks["google_scrape_exact"] = lambda q=exact_query: self._google_images_scrape(q)
        if phrase_query:
            tasks["google_scrape_phrase"] = lambda q=phrase_query: self._google_images_scrape(q)
        tasks["ddg"] = lambda q=broad_query: self._duckduckgo_search(q)
        tasks["yahoo"] = lambda q=broad_query: self._yahoo_images_scrape(q)

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
        if ctx.get("strict_query"):
            combined_hits = self._strict_candidate_pool(combined_hits, ctx, raw_scores)
            raw_scores = {hit["url"]: raw_scores[hit["url"]] for hit in combined_hits}

        ranked_urls = sorted(
            [hit["url"] for hit in combined_hits],
            key=lambda url: raw_scores[url],
            reverse=True,
        )
        max_candidates = STRICT_MAX_CANDIDATES if ctx.get("strict_query") else self.max_candidates
        if ctx.get("strict_query") and ranked_urls:
            top_score = raw_scores[ranked_urls[0]]
            keep_threshold = max(0.68, top_score - 0.14)
            filtered_ranked_urls = [url for url in ranked_urls if raw_scores[url] >= keep_threshold]
            ranked_urls = filtered_ranked_urls or ranked_urls
        candidates = ranked_urls[:max_candidates]
        scores = {u: round(raw_scores[u], 2) for u in candidates}
        return candidates, scores

    def assess_match_confidence(
        self,
        urls: list[str],
        scores: dict[str, float] | None,
        item: dict,
        *,
        prefer_first: bool = False,
    ) -> dict[str, Any]:
        ctx = self._build_item_context(item)
        if not urls:
            return {
                "score": 0.0,
                "label": "missing",
                "auto_approve": False,
                "reason": "No candidates found.",
                "suggested_url": "",
            }

        ranked = list(urls)
        if not prefer_first:
            ranked = sorted(urls, key=lambda url: float((scores or {}).get(url, 0.0) or 0.0), reverse=True)
        top_url = ranked[0]
        top_score = float((scores or {}).get(top_url, 0.0) or 0.0)
        second_score = float((scores or {}).get(ranked[1], 0.0) or 0.0) if len(ranked) > 1 else 0.0
        gap = max(0.0, top_score - second_score)

        lower = top_url.lower()
        normalized = _slug(lower)
        text_tokens = set(_tokenize(lower))
        preferred_domain = any(domain and domain in lower for domain in ctx.get("preferred_domains", []))
        blocked_domain = any(domain and domain in lower for domain in ctx.get("blocked_domains", []))
        blocked_term = any(term in text_tokens for term in ctx.get("blocked_terms", set()))
        local_file = top_url.startswith("file://")

        full_code_slug = _slug(ctx.get("item_code") or "")
        base_code_slug = _slug(ctx.get("base_item_code") or "")
        code_match = bool(
            (full_code_slug and full_code_slug in normalized) or
            (base_code_slug and base_code_slug in normalized)
        )

        color_tokens = ctx.get("color_tokens") or []
        color_match = any(token in text_tokens or token in normalized for token in color_tokens)
        wrong_colors = bool(({token for token in text_tokens if token in _COLOR_WORDS} - set(color_tokens)) and not color_match)

        category_family = ctx.get("category_family")
        category_match = True
        if category_family:
            family_terms = _CATEGORY_FAMILY_TERMS.get(category_family, set())
            family_hits = {token for token in text_tokens if token in family_terms}
            category_match = bool(family_hits) or not family_terms

        exact_tokens = ctx.get("exact_query_tokens") or []
        exact_matches = sum(1 for token in exact_tokens if token in text_tokens or token in normalized)
        exact_ratio = (exact_matches / len(exact_tokens)) if exact_tokens else 0.0

        confidence = top_score
        if preferred_domain:
            confidence += 0.08
        if code_match:
            confidence += 0.06
        if color_match:
            confidence += 0.05
        if category_match:
            confidence += 0.04
        confidence += min(0.12, gap * 0.6)
        if exact_ratio >= 0.75:
            confidence += 0.08
        elif ctx.get("strict_query") and exact_ratio < 0.5:
            confidence -= 0.1
        if wrong_colors:
            confidence -= 0.18
        if blocked_domain or blocked_term:
            confidence -= 0.18
        if local_file and top_score >= 0.85:
            confidence += 0.08
        confidence = min(max(confidence, 0.0), 1.0)

        reasons: list[str] = []
        if preferred_domain:
            reasons.append("preferred domain")
        if code_match:
            reasons.append("code match")
        if color_match:
            reasons.append("color match")
        if category_match:
            reasons.append("category match")
        if gap >= 0.12:
            reasons.append("clear lead")
        if wrong_colors:
            reasons.append("possible wrong color")
        if blocked_domain or blocked_term:
            reasons.append("suspicious source")
        if exact_ratio >= 0.75:
            reasons.append("full-query match")

        high_confidence = (
            top_score >= (0.88 if ctx.get("strict_query") else 0.82)
            and gap >= (0.16 if ctx.get("strict_query") else 0.12)
            and not wrong_colors
            and not blocked_domain
            and not blocked_term
            and (category_match or preferred_domain or code_match)
            and (preferred_domain or code_match or exact_ratio >= (0.8 if ctx.get("strict_query") else 0.72) or local_file)
        )
        medium_confidence = (
            top_score >= (0.68 if ctx.get("strict_query") else 0.6)
            and not blocked_domain
            and not blocked_term
            and not wrong_colors
            and (gap >= (0.08 if ctx.get("strict_query") else 0.05) or preferred_domain or code_match or exact_ratio >= (0.65 if ctx.get("strict_query") else 0.58))
        )

        if high_confidence:
            label = "high"
        elif medium_confidence:
            label = "medium"
        else:
            label = "low"

        return {
            "score": round(confidence, 2),
            "label": label,
            "auto_approve": label == "high",
            "reason": ", ".join(reasons) if reasons else "Weak evidence.",
            "suggested_url": top_url,
        }

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
        related_code = ctx.get("related_item_code") or ""
        full_code_slug = _slug(full_code)
        base_code_slug = _slug(base_code)
        related_code_slug = _slug(related_code)
        if full_code_slug and full_code_slug in normalized_text:
            score += 0.75
        elif base_code_slug and base_code_slug in normalized_text:
            score += 0.65
        elif related_code_slug and related_code_slug in normalized_text:
            score += 0.5
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

        exact_tokens = ctx.get("exact_query_tokens") or []
        exact_matches = sum(1 for token in exact_tokens if token in text_tokens or token in normalized_text)
        exact_ratio = (exact_matches / len(exact_tokens)) if exact_tokens else 0.0
        related_code_present = bool(related_code_slug and related_code_slug in normalized_text)
        if exact_ratio >= 0.8:
            score += 0.18
        elif exact_ratio >= 0.6:
            score += 0.08

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
                score -= 0.18 if ctx.get("strict_query") else 0.1
            elif other_colors and color_matches:
                score -= min(0.08, len(other_colors) * 0.03)
            if style_tokens:
                style_matches = sum(1 for token in style_tokens if token in text_tokens or token in normalized_text)
                if style_matches >= max(1, min(2, len(style_tokens))) and color_matches:
                    score += 0.12

        # Boost for user-specified priority domains (highest priority)
        for extra_d in self.extra_site_urls:
            clean_d = normalize_search_domain(extra_d)
            if clean_d and (clean_d in url.lower() or clean_d in page_url.lower()):
                score += 0.55
                break

        matched_brand_domain = ""
        for brand_d in self.matching_brand_site_urls(ctx.get("brand") or ""):
            clean_d = normalize_search_domain(brand_d)
            if clean_d and (clean_d in url.lower() or clean_d in page_url.lower()):
                matched_brand_domain = clean_d
                score += 0.40
                break

        domain = BRAND_DOMAINS.get((ctx.get("brand") or "").lower().strip())
        if domain and (domain in url.lower() or domain in page_url.lower()):
            score += 0.25 if matched_brand_domain == normalize_search_domain(domain) else 0.45
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
        if any(term in text_tokens for term in _PREFERRED_PRODUCT_SHOT_TERMS):
            score += 0.12
        if any(term in text_tokens for term in _DETAIL_SHOT_TERMS) or "close-up" in lower:
            score -= 0.22 if ctx.get("strict_query") else 0.14
        if any(term in text_tokens for term in _OUTSOLE_SHOT_TERMS):
            score -= 0.28 if ctx.get("strict_query") else 0.18
        if any(term in text_tokens for term in _LIFESTYLE_SHOT_TERMS) or "on-foot" in lower or "on foot" in lower:
            score -= 0.24 if ctx.get("strict_query") else 0.16

        category_family = ctx.get("category_family")
        if category_family:
            family_terms = _CATEGORY_FAMILY_TERMS.get(category_family, set())
            family_hits = {token for token in text_tokens if token in family_terms}
            if family_hits:
                score += 0.42
            conflicting_hits = set()
            for other_family, other_terms in _CATEGORY_FAMILY_TERMS.items():
                if other_family == category_family:
                    continue
                matched = {token for token in text_tokens if token in other_terms}
                if matched:
                    conflicting_hits |= matched
            if conflicting_hits and not family_hits:
                score -= 0.9 if ctx.get("strict_query") else 0.45
            negative_terms = _NEGATIVE_HINT_TERMS.get(category_family, set())
            if any(token in text_tokens for token in negative_terms):
                score -= 0.55 if ctx.get("strict_query") else 0.4

        if any(token in text_tokens for token in _LOW_QUALITY_TERMS):
            score -= 0.25

        if any(name.startswith("brand_site") and "style" not in name for name in source_names):
            score += 0.35
        if any(name.startswith("brand_site") and "exact" in name for name in source_names):
            score += 0.14
        if any(name.startswith("brand_site") and "phrase" in name for name in source_names):
            score += 0.2
        if any(name.startswith("brand_site") and "style" in name for name in source_names):
            score += 0.2
        if any(name.startswith("extra_") for name in source_names):
            score += 0.45
        if any(name.startswith("extra_") and "exact" in name for name in source_names):
            score += 0.16
        if any(name.startswith("extra_") and "phrase" in name for name in source_names):
            score += 0.22
        if "google_exact" in source_names or "google_scrape_exact" in source_names:
            score += 0.34
        if "google_phrase" in source_names or "google_scrape_phrase" in source_names:
            score += 0.38
        if "bing_exact" in source_names:
            score += 0.26
        if "bing_phrase" in source_names:
            score += 0.3
        if ctx.get("strict_query") and (
            {"google_exact", "google_phrase", "google_scrape_exact", "google_scrape_phrase", "bing_exact", "bing_phrase"} & set(source_names)
        ):
            if exact_ratio < 0.45 and not related_code_present:
                score -= 0.24
            if not (
                (full_code_slug and full_code_slug in normalized_text)
                or (base_code_slug and base_code_slug in normalized_text)
                or related_code_present
            ):
                score -= 0.12

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

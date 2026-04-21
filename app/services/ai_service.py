"""
AI service — Column mapping and search optimization.
Uses Google Gemini (free) by default, falls back to Claude if set.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from PIL import Image, ImageOps

from app.config import CLAUDE_API_KEY, GEMINI_API_KEY

logger = logging.getLogger(__name__)

_AI_IMAGE_MAX_CANDIDATES = 6
_AI_IMAGE_MAX_DIM = 768
_AI_IMAGE_FETCH_TIMEOUT = 8
_SEARCH_PERFECTION_RULES = """Non-negotiable match rules:
- The visible product type must be exact. Shorts are not t-shirts. Shoes are not sandals, bikes, drinks, or accessories.
- The visible color must match exactly or be an obvious close synonym. If the candidate is clearly a different color, reject it.
- If item codes differ, do NOT treat the same image as valid unless they share the same normalized base code and only vary by size.
- When the full exact query already identifies the product, keep that full phrasing as the first search query instead of shortening it too early.
- Prefer official product pages and clean packshots over marketplaces, logos, banners, thumbnails, or lifestyle/editorial images.
"""


def _join_distinct_prompt_parts(parts: list[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(text)
    return " ".join(out).strip()


def _make_http_session() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=32,
        pool_maxsize=64,
        max_retries=1,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_AI_HTTP = _make_http_session()


def _call_gemini(prompt: str, max_tokens: int = 1024) -> str | None:
    """Call Google Gemini API (free tier)."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.1,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None


def _call_claude(prompt: str, max_tokens: int = 1024) -> str | None:
    """Call Anthropic Claude API (paid)."""
    if not CLAUDE_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None


def _call_ai(prompt: str, max_tokens: int = 1024) -> str | None:
    """Try Gemini first (free), then Claude."""
    if GEMINI_API_KEY:
        result = _call_gemini(prompt, max_tokens)
        if result:
            return result
    if CLAUDE_API_KEY:
        result = _call_claude(prompt, max_tokens)
        if result:
            return result
    return None


def _call_gemini_vision(prompt: str, images: list[dict[str, Any]], max_tokens: int = 1024) -> str | None:
    if not GEMINI_API_KEY or not images:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        parts: list[Any] = [types.Part.from_text(text=prompt)]
        for image in images:
            parts.append(types.Part.from_text(text=f"Candidate {image['index']}: {image['url']}"))
            parts.append(types.Part.from_bytes(
                data=image["data"],
                mime_type=image["mime_type"],
            ))
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=parts,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.1,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini vision API error: {e}")
        return None


def _call_claude_vision(prompt: str, images: list[dict[str, Any]], max_tokens: int = 1024) -> str | None:
    if not CLAUDE_API_KEY or not images:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            content.append({"type": "text", "text": f"Candidate {image['index']}: {image['url']}"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["mime_type"],
                    "data": base64.b64encode(image["data"]).decode("ascii"),
                },
            })
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        text_blocks = [
            block.text.strip()
            for block in message.content
            if getattr(block, "type", "") == "text" and getattr(block, "text", "").strip()
        ]
        return "\n".join(text_blocks).strip() if text_blocks else None
    except Exception as e:
        logger.error(f"Claude vision API error: {e}")
        return None


def _call_ai_vision(prompt: str, images: list[dict[str, Any]], max_tokens: int = 1024) -> str | None:
    if GEMINI_API_KEY:
        result = _call_gemini_vision(prompt, images, max_tokens)
        if result:
            return result
    if CLAUDE_API_KEY:
        result = _call_claude_vision(prompt, images, max_tokens)
        if result:
            return result
    return None


def _load_image_for_ai(url: str) -> bytes | None:
    text = str(url or "").strip()
    if not text:
        return None
    try:
        if text.startswith("file://"):
            from pathlib import Path
            return Path(text[7:]).read_bytes()

        if not text.startswith(("http://", "https://")):
            return None
        resp = _AI_HTTP.get(
            text,
            headers={"User-Agent": "Mozilla/5.0 FLENDER/1.0"},
            timeout=_AI_IMAGE_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        content_type = str(resp.headers.get("content-type") or "").lower()
        if content_type and "image" not in content_type:
            return None
        return resp.content
    except Exception:
        return None


def _prepare_image_for_ai(url: str, index: int) -> dict[str, Any] | None:
    raw = _load_image_for_ai(url)
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.split()[-1] if "A" in img.getbands() else None
                bg.paste(img, mask=alpha)
                img = bg
            elif img.mode == "L":
                img = img.convert("RGB")

            img.thumbnail((_AI_IMAGE_MAX_DIM, _AI_IMAGE_MAX_DIM))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=84, optimize=True)
            return {
                "index": index,
                "url": url,
                "mime_type": "image/jpeg",
                "data": out.getvalue(),
            }
    except Exception:
        return None


def _prepare_images_for_ai(urls: list[str]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(urls) or 1)) as pool:
        futures = {
            pool.submit(_prepare_image_for_ai, url, idx): idx
            for idx, url in enumerate(urls, start=1)
        }
        for future in as_completed(futures):
            image = future.result()
            if image:
                prepared.append(image)
    prepared.sort(key=lambda image: image["index"])
    return prepared


def _extract_json(text: str) -> str:
    """Extract JSON from markdown code blocks."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return text


def compose_search_instructions(
    *,
    manual_instructions: str = "",
    session_notes: str = "",
    brand_notes: list[str] | None = None,
    priority_domains: list[str] | None = None,
) -> str:
    """Build one instruction block for AI search prompts."""
    blocks: list[str] = []
    clean_domains = [str(domain or "").strip() for domain in (priority_domains or []) if str(domain or "").strip()]
    if clean_domains:
        bullets = "\n".join(f"- {domain}" for domain in clean_domains)
        blocks.append(
            "Priority domains (search these first and prefer official product pages on them):\n"
            f"{bullets}"
        )
    if session_notes.strip():
        blocks.append(session_notes.strip())
    clean_brand_notes = [note.strip() for note in (brand_notes or []) if str(note or "").strip()]
    if clean_brand_notes:
        blocks.append("Brand-specific search notes:\n" + "\n\n".join(clean_brand_notes))
    if manual_instructions.strip():
        blocks.append("Current item instructions:\n" + manual_instructions.strip())
    return "\n\n".join(blocks).strip()


def ai_map_columns(headers: list[str], sample_rows: list[dict], standard_fields: list[str]) -> dict[str, Any]:
    """
    Use AI to suggest column mappings from file headers to standard fields.
    Returns {standard_field: {"header": matched_header, "confidence": 0.0-1.0}}
    """
    sample_text = ""
    for i, row in enumerate(sample_rows[:3]):
        cleaned = {k: v for k, v in row.items() if k != "_raw" and v is not None}
        sample_text += f"Row {i+1}: {json.dumps(cleaned, default=str)}\n"

    prompt = f"""You are a data mapping assistant for a fashion/apparel order management system.

Given these Excel column headers and sample data, map each header to the correct standard field.

Excel Headers: {json.dumps(headers)}

Sample Data:
{sample_text}

Standard Fields to map to: {json.dumps(standard_fields)}

Field descriptions:
- item_code: Product SKU, style number, manufacturer code, article number
- style_name: Product name, description, web description
- color_name: Color name (e.g. "Navy Blue", "Black")
- color_code: Color code/ID (e.g. "V0020", "001")
- size: Size values (S, M, L, XL, 38, 39, etc.)
- brand: Brand name (e.g. "Stone Island", "Golden Goose")
- wholesale_price: Wholesale/cost price (WHS, dealer price, net price, WHSL IN EUR)
- retail_price: Retail/RRP price (recommended retail, MSRP)
- qty_available: Available quantity, stock, freestock, QTY
- gender: Gender (Men, Women, Unisex, Kids)
- barcode: EAN, UPC, GTIN barcode
- item_group: Product group/category

Return ONLY valid JSON in this exact format:
{{
  "mappings": {{
    "standard_field_name": {{"header": "original_header_name", "confidence": 0.95}},
    ...
  }},
  "unmapped_headers": ["headers that don't match any standard field"],
  "notes": "brief explanation of any uncertain mappings"
}}

Rules:
- Each header maps to ONE standard field only
- Each standard field has ONE header only
- Confidence 0.9+ for obvious matches, 0.5-0.9 for likely, below 0.5 skip it
- Omit fields with no matching header"""

    text = _call_ai(prompt, max_tokens=2048)
    if not text:
        return {}

    try:
        return json.loads(_extract_json(text))
    except Exception as e:
        logger.error(f"AI mapping JSON parse failed: {e}\nResponse: {text[:200]}")
        return {}


def ai_optimize_search_query(
    item: dict,
    brand: str,
    failed_queries: list[str] | None = None,
    search_instructions: str = "",
) -> list[str]:
    """
    Use AI to generate optimized search queries.
    search_instructions: free-text hints from user (e.g. URL patterns, naming conventions).
    Returns list of 2-3 search queries.
    """
    item_code = item.get("item_code", "")
    style_name = item.get("style_name", "")
    color_name = item.get("color_name", "")
    barcode = item.get("barcode", "")
    item_group = item.get("item_group", "")

    instructions_block = ""
    if search_instructions.strip():
        instructions_block = f"\nSpecial instructions from user:\n{search_instructions.strip()}\n"

    exact_query = _join_distinct_prompt_parts([brand, style_name, item_group, color_name, item_code or barcode])

    prompt = f"""Generate 2-3 optimized image search queries to find a product photo for this fashion item.
The image MUST match the exact color specified. Color accuracy is critical.
{_SEARCH_PERFECTION_RULES}

Brand: {brand}
SKU/Code: {item_code}
Product Name: {style_name}
Color: {color_name}
Barcode: {barcode}
Category: {item_group}
Preferred full exact query: {exact_query}
{f'Previous failed queries: {json.dumps(failed_queries)}' if failed_queries else ''}{instructions_block}

Rules:
- Query 1 should be the full exact query or a quoted version of it whenever it is strong enough
- ALWAYS include the color name in at least 2 of the queries
- Include the brand name in every query
- Use the SKU/code in at least one query
- If the category is known, include it to avoid wrong product types
- Do NOT repeat words already present in the style name (example: avoid "Wharfie Beanie Beanie")
- Try different formats: full exact query, "brand code color", "brand product-name color", "brand barcode"

Return ONLY a JSON array of search query strings:
["query 1", "query 2", "query 3"]"""

    text = _call_ai(prompt, max_tokens=1024)
    if not text:
        return []

    try:
        return json.loads(_extract_json(text))
    except Exception:
        return []


def ai_build_search_queries(item: dict, brand: str, search_instructions: str = "") -> list[str]:
    """
    Use AI to build the initial search queries for an item, using user instructions.
    Used BEFORE any search attempt when instructions are provided.
    Returns list of 2-3 search queries.
    """
    if not search_instructions.strip():
        return []

    item_code = item.get("item_code", "")
    style_name = item.get("style_name", "")
    color_name = item.get("color_name", "")
    barcode = item.get("barcode", "")
    item_group = item.get("item_group", "")

    exact_query = _join_distinct_prompt_parts([brand, style_name, item_group, color_name, item_code or barcode])

    prompt = f"""You are helping search for product images for a fashion B2B platform.
Build the best search queries for this product based on the user's instructions.
The image MUST match the exact color specified. Color accuracy is critical.
{_SEARCH_PERFECTION_RULES}

Brand: {brand}
SKU/Code: {item_code}
Product Name: {style_name}
Color: {color_name}
Barcode: {barcode}
Category: {item_group}
Preferred full exact query: {exact_query}

User instructions:
{search_instructions.strip()}

Rules:
- Query 1 should preserve the full exact query or a quoted version of it whenever possible
- ALWAYS include the color "{color_name}" in at least 2 of the queries
- Include the brand name in every query
- Follow user instructions about URLs, naming patterns, and search strategies
- If instructions mention a specific website, include "site:domain.com" in one query
- Do NOT repeat words already present in the style name (example: avoid "Wharfie Beanie Beanie")

Return ONLY a JSON array of 2-3 search query strings:
["query 1", "query 2", "query 3"]"""

    text = _call_ai(prompt, max_tokens=1024)
    if not text:
        return []

    try:
        return json.loads(_extract_json(text))
    except Exception:
        return []


def _should_use_vision_ranking(
    urls: list[str],
    scores: dict[str, float] | None = None,
    *,
    prefer_vision: bool = False,
) -> bool:
    if len(urls) < 2:
        return False
    if prefer_vision:
        return True
    if not scores:
        return len(urls) <= _AI_IMAGE_MAX_CANDIDATES

    top_scores = [float(scores.get(url, 0.0) or 0.0) for url in urls[:3]]
    top_1 = top_scores[0] if top_scores else 0.0
    top_2 = top_scores[1] if len(top_scores) > 1 else 0.0
    if top_1 >= 0.86 and (top_1 - top_2) >= 0.18:
        return False
    return True


def _ai_rank_urls_with_vision(
    urls: list[str],
    item: dict,
    brand: str,
    *,
    scores: dict[str, float] | None = None,
    prefer_vision: bool = False,
) -> list[str] | None:
    if not _should_use_vision_ranking(urls, scores, prefer_vision=prefer_vision):
        return None

    inspect_urls = list(urls[:_AI_IMAGE_MAX_CANDIDATES])
    prepared = _prepare_images_for_ai(inspect_urls)
    if len(prepared) < 2:
        return None

    item_code = item.get("item_code", "")
    style_name = item.get("style_name", "")
    color_name = item.get("color_name", "")
    barcode = item.get("barcode", "")
    item_group = item.get("item_group", "")
    listed = "\n".join(f"{image['index']}. {image['url']}" for image in prepared)
    prompt = f"""You are visually checking candidate product images for a fashion B2B platform.
{_SEARCH_PERFECTION_RULES}

Product to match:
- Brand: {brand}
- SKU/Code: {item_code}
- Style: {style_name}
- Color: {color_name}
- Barcode: {barcode}
- Category: {item_group}

Attached below are candidate images in order. Their source URLs are:
{listed}

Judge the ACTUAL visible product in each image, not only the URL text.

Ranking rules:
1. Exact visible product type must match. Shorts are not t-shirts. Shoes are not bikes or drinks.
2. Exact visible color match is critical. Wrong color should be rejected.
3. Prefer clean product photos and official packshots when multiple candidates are otherwise similar.
4. If multiple candidates show the same product, rank the clearest/best-framed product photo first.
5. If a candidate looks like the same photo reused for a clearly different item family, discard it.

Return ONLY valid JSON in this format:
{{
  "ranked": [2, 1],
  "discarded": [3],
  "notes": "short reason"
}}

Only use candidate numbers from the attached images."""

    text = _call_ai_vision(prompt, prepared, max_tokens=900)
    if not text:
        return None

    try:
        data = json.loads(_extract_json(text))
        ranked_indices = [int(idx) for idx in (data.get("ranked") or [])]
        discarded_indices = {int(idx) for idx in (data.get("discarded") or [])}
    except Exception as e:
        logger.error(f"AI vision ranking parse failed: {e}")
        return None

    url_by_index = {image["index"]: image["url"] for image in prepared}
    ranked_urls: list[str] = []
    seen: set[str] = set()
    for idx in ranked_indices:
        url = url_by_index.get(idx)
        if url and url not in seen:
            seen.add(url)
            ranked_urls.append(url)

    middle_urls = [
        image["url"]
        for image in prepared
        if image["index"] not in discarded_indices and image["url"] not in seen
    ]
    trailing_urls = [
        image["url"]
        for image in prepared
        if image["index"] in discarded_indices and image["url"] not in seen
    ]
    remainder = [url for url in urls if url not in ranked_urls and url not in middle_urls and url not in trailing_urls]
    final = ranked_urls + middle_urls + remainder + trailing_urls
    return final if final else None


def _ai_rank_urls_text_only(urls: list[str], item: dict, brand: str) -> list[str]:
    """
    Use AI to re-rank image URLs by likelihood of being the correct product image.
    Filters out logos, banners, and thumbnails. Returns re-ordered list.
    """
    if not urls:
        return urls

    item_code = item.get("item_code", "")
    style_name = item.get("style_name", "")
    color_name = item.get("color_name", "")
    barcode = item.get("barcode", "")
    item_group = item.get("item_group", "")

    # Number the URLs so AI can reference them
    numbered = "\n".join(f"{i+1}. {u}" for i, u in enumerate(urls))

    prompt = f"""You are a product image quality judge for a fashion B2B platform.
{_SEARCH_PERFECTION_RULES}

Given these image URLs found by web search, rank them from BEST to WORST for this product.

Product:
- Brand: {brand}
- SKU/Code: {item_code}
- Style: {style_name}
- Color: {color_name}
- Barcode: {barcode}
- Category: {item_group}

Image URLs:
{numbered}

Ranking criteria (most important first):
1. COLOR MATCH IS CRITICAL — The color is "{color_name}". URLs containing this color name or a matching color code should rank highest. URLs showing a DIFFERENT color (e.g., "black" when we need "white") must be EXCLUDED entirely.
2. URL contains the exact item code/SKU → strongest signal
3. URLs matching the full exact query wording most closely should rank highest.
4. URL is from the brand's official domain or a known fashion CDN
5. URL path suggests a product image (/product/, /products/, /p/, /catalog/, /item/, scene7, cloudfront, akamaized, shopify, cloudinary)
6. URL does NOT look like a logo, banner, thumbnail, or avatar
7. High-resolution image path preferred (not /thumb/, /small/, /icon/, /logo/)
8. Category/type mismatch is a major negative. Example: shorts must not rank above t-shirts, footwear must not rank below bikes or drinks.

Return ONLY a JSON array of the URL numbers in order from best to worst:
[3, 1, 5, 2, 4]

IMPORTANT:
- EXCLUDE any URL that clearly shows the WRONG color (different from "{color_name}")
- EXCLUDE logos, banners, irrelevant images, or wrong product types entirely
- If one result matches the full exact query clearly better than the others, put it first
- Only include numbers for URLs that are likely the correct product in the correct color"""

    text = _call_ai(prompt, max_tokens=1024)
    if not text:
        return urls

    try:
        ranking = json.loads(_extract_json(text))
        reordered = []
        seen = set()
        for idx in ranking:
            i = int(idx) - 1
            if 0 <= i < len(urls) and urls[i] not in seen:
                reordered.append(urls[i])
                seen.add(urls[i])
        # Append any URLs not ranked by AI at the end
        for u in urls:
            if u not in seen:
                reordered.append(u)
        return reordered
    except Exception as e:
        logger.error(f"AI rank URLs parse failed: {e}")
        return urls


def ai_rank_urls(
    urls: list[str],
    item: dict,
    brand: str,
    *,
    scores: dict[str, float] | None = None,
    prefer_vision: bool = False,
) -> list[str]:
    """AI re-rank candidates, using vision first when the results are ambiguous."""
    if not urls:
        return urls

    vision_ranked = _ai_rank_urls_with_vision(
        urls,
        item,
        brand,
        scores=scores,
        prefer_vision=prefer_vision,
    )
    if vision_ranked:
        return vision_ranked

    return _ai_rank_urls_text_only(urls, item, brand)


def ai_assistant_chat(message: str, context: dict[str, Any]) -> dict[str, Any]:
    """Context-aware FLENDER assistant for search, review, sheets, and export."""
    prompt = f"""You are FLENDER AI, an in-product assistant for an order-sheet and product-image workflow.

The user is asking from inside the website. Your job is to help with:
- image search quality
- exact color/category/product matching
- priority brand domains
- Google Sheets import behavior
- review/grouping workflow
- export/ordersheet issues

Context:
{json.dumps(context, ensure_ascii=False, default=str, indent=2)}

User message:
{message.strip()}

Important rules:
- Search quality must follow these rules:
{_SEARCH_PERFECTION_RULES}
- Be concrete and operational. Give actions the user can take in this website.
- If the request is about image search, prioritize exact SKU, exact color, exact product family, official brand domains, and grouping when appropriate.
- If the request is about review, mention whether applying one image to a whole product/color group is a good idea.
- If the request is about export or Google Sheets, explain what fields/columns matter.
- Be honest: do NOT claim you can see pixels in images unless the context explicitly includes visual inspection output. If you only have metadata/session context, say so briefly and still help.
- Prefer short, high-signal answers.

Return ONLY valid JSON in this format:
{{
  "reply": "main assistant reply",
  "suggestions": ["short actionable suggestion 1", "short actionable suggestion 2"],
  "search_instructions": "optional search-instructions text to apply in Step 3 if relevant, else empty string",
  "priority_domains": ["optional domain.com", "optional domain2.com"]
}}"""

    text = _call_ai(prompt, max_tokens=1400)
    if not text:
        return {
            "reply": "AI is not available right now. I can still help once the AI provider is configured again.",
            "suggestions": [],
            "search_instructions": "",
            "priority_domains": [],
        }

    try:
        data = json.loads(_extract_json(text))
        return {
            "reply": str(data.get("reply") or "").strip() or "I analyzed the context, but I do not have a strong recommendation yet.",
            "suggestions": [str(s).strip() for s in (data.get("suggestions") or []) if str(s).strip()][:4],
            "search_instructions": str(data.get("search_instructions") or "").strip(),
            "priority_domains": [str(d).strip() for d in (data.get("priority_domains") or []) if str(d).strip()][:5],
        }
    except Exception as e:
        logger.error(f"AI assistant JSON parse failed: {e}")
        return {
            "reply": text.strip(),
            "suggestions": [],
            "search_instructions": "",
            "priority_domains": [],
        }


def ai_available() -> bool:
    """Check if any AI service is configured."""
    return bool(GEMINI_API_KEY or CLAUDE_API_KEY)


_CONTEXT_PROMPT = (
    "You are helping an image-search tool find the correct product photos for a wholesale order sheet. "
    "The user has uploaded a reference file that describes the items we're searching for.\n\n"
    "Write a short, high-signal brief (4-8 bullet points, <= 120 words total) that the image searcher can use.\n"
    "Focus on:\n"
    "- What category of product these are (e.g., men's basketball sneakers, wool beanies, leather handbags)\n"
    "- Gender/age target if present\n"
    "- Brands/style families mentioned\n"
    "- Distinguishing visual features (colorways, materials, logos, silhouettes)\n"
    "- Any disambiguation hints (e.g., 'not the slim-fit variant', 'current season only')\n\n"
    "Do NOT invent details that aren't in the source. Write plain bullet points with a leading '- '. "
    "Skip headings and introductions."
)


def ai_describe_context_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str | None:
    """Use vision AI to describe a product-context image the user uploaded."""
    if not ai_available() or not image_bytes:
        return None
    payload = [{
        "index": 1,
        "url": "user-uploaded context image",
        "data": image_bytes,
        "mime_type": mime_type or "image/jpeg",
    }]
    return _call_ai_vision(_CONTEXT_PROMPT, payload, max_tokens=600)


def ai_describe_context_text(text: str, filename: str = "") -> str | None:
    """Summarize a text-based context file (txt/csv/xlsx-extracted) for the image searcher."""
    if not ai_available() or not (text or "").strip():
        return None
    snippet = text.strip()
    if len(snippet) > 8000:
        snippet = snippet[:8000] + "\n[... truncated ...]"
    prompt = (
        _CONTEXT_PROMPT
        + f"\n\nSource file: {filename or 'uploaded document'}\n---\n{snippet}\n---"
    )
    return _call_ai(prompt, max_tokens=600)

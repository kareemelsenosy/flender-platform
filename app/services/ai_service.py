"""
AI service — Column mapping and search optimization.
Uses Google Gemini (free) by default, falls back to Claude if set.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import CLAUDE_API_KEY, GEMINI_API_KEY

logger = logging.getLogger(__name__)


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

    prompt = f"""Generate 2-3 optimized image search queries to find a product photo for this fashion item.
The image MUST match the exact color specified. Color accuracy is critical.

Brand: {brand}
SKU/Code: {item_code}
Product Name: {style_name}
Color: {color_name}
Barcode: {barcode}
Category: {item_group}
{f'Previous failed queries: {json.dumps(failed_queries)}' if failed_queries else ''}{instructions_block}

Rules:
- ALWAYS include the color name in at least 2 of the queries
- Include the brand name in every query
- Use the SKU/code in at least one query
- If the category is known, include it to avoid wrong product types
- Try different formats: "brand code color", "brand product-name color", "brand barcode"

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

    prompt = f"""You are helping search for product images for a fashion B2B platform.
Build the best search queries for this product based on the user's instructions.
The image MUST match the exact color specified. Color accuracy is critical.

Brand: {brand}
SKU/Code: {item_code}
Product Name: {style_name}
Color: {color_name}
Barcode: {barcode}
Category: {item_group}

User instructions:
{search_instructions.strip()}

Rules:
- ALWAYS include the color "{color_name}" in at least 2 of the queries
- Include the brand name in every query
- Follow user instructions about URLs, naming patterns, and search strategies
- If instructions mention a specific website, include "site:domain.com" in one query

Return ONLY a JSON array of 2-3 search query strings:
["query 1", "query 2", "query 3"]"""

    text = _call_ai(prompt, max_tokens=1024)
    if not text:
        return []

    try:
        return json.loads(_extract_json(text))
    except Exception:
        return []


def ai_rank_urls(urls: list[str], item: dict, brand: str) -> list[str]:
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
3. URL is from the brand's official domain or a known fashion CDN
4. URL path suggests a product image (/product/, /products/, /p/, /catalog/, /item/, scene7, cloudfront, akamaized, shopify, cloudinary)
5. URL does NOT look like a logo, banner, thumbnail, or avatar
6. High-resolution image path preferred (not /thumb/, /small/, /icon/, /logo/)
7. Category/type mismatch is a major negative. Example: shorts must not rank above t-shirts, footwear must not rank below bikes or drinks.

Return ONLY a JSON array of the URL numbers in order from best to worst:
[3, 1, 5, 2, 4]

IMPORTANT:
- EXCLUDE any URL that clearly shows the WRONG color (different from "{color_name}")
- EXCLUDE logos, banners, irrelevant images, or wrong product types entirely
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


def ai_available() -> bool:
    """Check if any AI service is configured."""
    return bool(GEMINI_API_KEY or CLAUDE_API_KEY)
